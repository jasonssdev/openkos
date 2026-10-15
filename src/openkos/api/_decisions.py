"""Listing open decisions.

`list_open_decisions` returns items that carry the stored proposal payload, an
unversioned mapping that can derive from confidential concepts and holds the
adjudicator's confidence. None of that crosses the facade: the typed details
below are built from the reader functions that live beside the payload's
writer, and a payload they cannot read yields a decision with no detail, still
listed (ADR-0053: list, never hide).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from openkos.api import _wiring
from openkos.api._workspace import Workspace
from openkos.application import curate_decisions
from openkos.application.curate_decisions import (
    Action,
    Applied,
    DecisionVersion,
    NotApplied,
    Stale,
    WithheldAction,
)

DecisionKind = curate_decisions.DecisionKind

Evaluation = Literal[
    "judged_same",
    "judged_different",
    "judged_uncertain",
    "pending_evaluation",
    "not_applicable",
]


@dataclass(frozen=True)
class IdentityDetail:
    """An identity decision: a group that may be one entity."""

    member_ids: tuple[str, ...]
    okf_type: str
    tier: str
    verdict: Literal["same", "different", "uncertain"] | None
    rationale: str | None


@dataclass(frozen=True)
class RelationDetail:
    """A relation-type decision: a suggested type for one untyped edge."""

    source_id: str
    target_id: str
    effective_source_id: str
    effective_target_id: str
    suggested_type: str
    rationale: str


@dataclass(frozen=True)
class TierDetail:
    """A volatility decision: a suggested tier for one concept type."""

    type_name: str
    current_default: str
    suggested_tier: str
    rationale: str


@dataclass(frozen=True)
class Decision:
    """One open decision, as data a client can render."""

    decision_key: str
    kind: DecisionKind
    targets: tuple[str, ...]
    fresh: bool | None
    evaluation: Evaluation
    actions: tuple[Action, ...]
    withheld: tuple[WithheldAction, ...]
    resolves_via: str
    detail: IdentityDetail | RelationDetail | TierDetail | None


def _detail(
    item: curate_decisions.DecisionItem,
) -> IdentityDetail | RelationDetail | TierDetail | None:
    if item.kind == "identity":
        identity = curate_decisions.identity_detail_fields(item.payload, item.targets)
        if identity is None:
            return None
        members, okf_type, tier, verdict, rationale = identity
        return IdentityDetail(
            member_ids=members,
            okf_type=okf_type,
            tier=tier,
            verdict=verdict,  # type: ignore[arg-type]
            rationale=rationale,
        )
    if item.kind == "relation_type":
        relation = curate_decisions.relation_fields(item.payload)
        if relation is None:
            return None
        return RelationDetail(*relation)
    if item.kind == "volatility":
        tier_fields = curate_decisions.tier_detail_fields(item.payload)
        if tier_fields is None:
            return None
        return TierDetail(*tier_fields)
    return None


def list_decisions(workspace: Workspace) -> tuple[Decision, ...]:
    """Every open decision. Takes no lock, builds no client, makes no model
    call and writes nothing; an absent queue lists nothing."""
    return tuple(
        Decision(
            decision_key=item.decision_key,
            kind=item.kind,
            targets=item.targets,
            fresh=item.fresh,
            evaluation=item.evaluation,
            actions=item.actions,
            withheld=item.withheld,
            resolves_via=item.resolves_via,
            detail=_detail(item),
        )
        for item in curate_decisions.list_open_decisions(workspace._layout)
    )


# -- preview and apply ---------------------------------------------------------- #

_SURFACE = "api"
"""The verb the services name in a drift refusal and a log line."""

_POLICY = curate_decisions.DecisionPolicy(no_reconcile=True)
"""Preview and apply never reconcile merged bodies with a model: the same policy
on both, so the plan a caller reviewed is the plan that is committed."""


@dataclass(frozen=True)
class MergePlan:
    """Merging two concepts: the survivor absorbs the other."""

    survivor_id: str
    absorbed_id: str
    survivor_criterion: str
    files_rewritten: int
    cross_source_same_pair: bool
    cross_type_concern: str | None


@dataclass(frozen=True)
class RelatePlan:
    """Writing one typed relation, as it would be written."""

    source_id: str
    target_id: str
    relation_type: str


@dataclass(frozen=True)
class TierPlan:
    """Setting one concept type's volatility tier."""

    type_name: str
    tier: str


@dataclass(frozen=True)
class KeepDistinctPlan:
    """Recording that these concepts are different things."""

    member_ids: tuple[str, ...]


@dataclass(frozen=True)
class DecisionPreview:
    """What an action would do, and the version to apply it against."""

    decision_key: str
    action: Action
    plan: MergePlan | RelatePlan | TierPlan | KeepDistinctPlan
    version: DecisionVersion


ApplyOutcome = Applied | NotApplied | Stale


def _plan_of(
    body: curate_decisions.DecisionPreviewBody,
) -> MergePlan | RelatePlan | TierPlan | KeepDistinctPlan:
    if isinstance(body, curate_decisions.MergeDecisionPreview):
        return MergePlan(
            survivor_id=body.prepared.survivor_canonical,
            absorbed_id=body.prepared.absorbed_canonical,
            survivor_criterion=body.survivor_criterion,
            files_rewritten=len(body.prepared.touched_files),
            cross_source_same_pair=body.cross_source_same_pair,
            cross_type_concern=body.cross_type_concern,
        )
    if isinstance(body, curate_decisions.RelateDecisionPreview):
        return RelatePlan(body.source_id, body.target_id, body.relation_type)
    if isinstance(body, curate_decisions.TierDecisionPreview):
        return TierPlan(body.type_name, body.tier)
    return KeepDistinctPlan(body.member_ids)


def preview_decision(
    workspace: Workspace, decision_key: str, action: Action
) -> DecisionPreview | NotApplied | Stale:
    """What `action` would do to the open decision `decision_key`, with the
    version to apply it against.

    Writes nothing, takes no lock, builds no client and makes no model call. A
    decision that is no longer open is the returned `Stale`; an action it does
    not offer, and skip (which has no plan), are the returned `NotApplied`."""
    result = curate_decisions.preview_decision(
        workspace.root, workspace._layout, decision_key, action, policy=_POLICY
    )
    if isinstance(result, NotApplied | Stale):
        return result
    return DecisionPreview(
        decision_key=decision_key,
        action=action,
        plan=_plan_of(result.preview),
        version=result.version,
    )


def apply_decision(
    workspace: Workspace,
    version: DecisionVersion,
    action: Action,
    *,
    wait_seconds: float = 0.0,
    on_warning: Callable[[str], None] | None = None,
) -> ApplyOutcome:
    """Apply `action` to the one decision `version` was issued for.

    A decision that changed since the preview is the returned `Stale`, and one
    that is gone or does not take the action the returned `NotApplied`; none of
    these writes anything. Otherwise the decision's own files are written and
    committed to the workspace's git repository, only the paths it wrote. A
    commit that cannot be made still leaves the apply successful with
    `commit_sha=None`, reported once through `on_warning`.

    The workspace lock is held only inside the commit section, waiting at most
    `wait_seconds` (0 to 3600); beyond it `WorkspaceBusyError` is raised and
    nothing is written. Raises the `WriteRefused` family when a check fails
    after the preview. Merged bodies are not reconciled with a model, and the
    derived indexes are not refreshed."""
    wait = _wiring.validated_wait(wait_seconds)
    return curate_decisions.apply_decision(
        workspace.root,
        workspace._layout,
        version,
        action,
        policy=_POLICY,
        ports=_wiring.decision_ports(
            workspace, wait_seconds=wait, on_warning=on_warning
        ),
        observer=curate_decisions.DecisionObserver(),
        surface=_SURFACE,
    )
