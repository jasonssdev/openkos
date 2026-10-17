"""Listing open decisions.

`list_open_decisions` returns items that carry the stored proposal payload, an
unversioned mapping that can derive from confidential concepts and holds the
adjudicator's confidence. None of that crosses the facade: the typed details
below are built from the reader functions that live beside the payload's
writer, and a payload they cannot read yields a decision with no detail, still
listed (ADR-0053: list, never hide).
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from openkos.api import _wiring
from openkos.api._workspace import Workspace
from openkos.application import curate_decisions
from openkos.application.curate_decisions import (
    Action,
    Applied,
    DecisionVersion,
    GroupSelection,
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
class ContradictionDetail:
    """A contradiction: two pages that say incompatible things. A merged-body
    one (`merged_absorbed_id` set) was found inside a combined page, between
    the page and the one it absorbed."""

    first_id: str
    second_id: str
    merged_absorbed_id: str | None
    rationale: str
    conflicting_claims: tuple[str, ...]


@dataclass(frozen=True)
class RevisionDetail:
    """A revision: a later Decision that reverses or refines an earlier one.
    `later_id` is `None` when the dates do not order the pair; it never comes
    from the model."""

    first_id: str
    second_id: str
    change: Literal["reverses", "refines"]
    later_id: str | None
    rationale: str
    first_quote: str
    second_quote: str
    first_date: str | None
    second_date: str | None


@dataclass(frozen=True)
class RefusedFileDetail:
    """A file the inbox watch did not add: `file_name` is its path relative to
    the inbox. `changed_after_import`: a file of that name was already added
    with different contents. `exceeds_pass_budget`: it is larger than one
    unattended pass may add."""

    file_name: str
    reason: Literal["changed_after_import", "exceeds_pass_budget"]


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
    finding: ContradictionDetail | RevisionDetail | RefusedFileDetail | None = None
    """The detail of a contradiction, a revision or a refused file; `None`
    for the other kinds, and for a payload that cannot be read."""


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


def _finding(
    item: curate_decisions.DecisionItem,
) -> ContradictionDetail | RevisionDetail | RefusedFileDetail | None:
    if item.kind == "contradiction":
        contradiction = curate_decisions.contradiction_fields(item.payload)
        return None if contradiction is None else ContradictionDetail(*contradiction)
    if item.kind == "revision":
        revision = curate_decisions.revision_fields(item.payload)
        return None if revision is None else RevisionDetail(*revision)
    if item.kind == "watch_refusal":
        refused = curate_decisions.refused_file_fields(item.payload)
        return None if refused is None else RefusedFileDetail(*refused)
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
            finding=_finding(item),
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
class MergeStep:
    """One pairwise merge of a plan: `survivor_id` absorbs `absorbed_id`."""

    survivor_id: str
    absorbed_id: str
    absorbed_type: str | None
    type_outcome: Literal["same_type", "recorded", "discarded", "unknown"]
    """What the step does with the absorbed type: nothing to do (same as the
    survivor's), recorded as the survivor's `type_alternative`, discarded
    because the survivor already records one, or unknown because a type cannot
    be read."""
    files_rewritten: int


@dataclass(frozen=True)
class MergePlan:
    """Merging concepts into a survivor: one pairwise merge, or, for
    `merge_group`, one per marked member."""

    survivor_id: str
    absorbed_id: str
    """The first absorbed member; `steps` lists them all."""
    survivor_criterion: str
    """Why this survivor, or `"chosen"` when the person named it."""
    files_rewritten: int
    cross_source_same_pair: bool
    cross_type_concern: str | None
    kind: Literal["merge"] = "merge"
    steps: tuple[MergeStep, ...] = ()
    """The pairwise merges, in the order they are written."""
    kept_distinct_ids: tuple[str, ...] = ()
    """The members of a group that were not marked: left as they are and
    recorded as different from the survivor."""
    survivor_type: str | None = None


@dataclass(frozen=True)
class RelatePlan:
    """Writing one typed relation, as it would be written."""

    source_id: str
    target_id: str
    relation_type: str
    kind: Literal["relate"] = "relate"


@dataclass(frozen=True)
class TierPlan:
    """Setting one concept type's volatility tier."""

    type_name: str
    tier: str
    kind: Literal["set_tier"] = "set_tier"
    configured_tier: str | None = None
    """The type's setting before this change, `None` when it had none and the
    engine's default applied: what `preview_tier_change` restores."""


@dataclass(frozen=True)
class KeepDistinctPlan:
    """Recording that these concepts are different things."""

    member_ids: tuple[str, ...]
    kind: Literal["keep_distinct"] = "keep_distinct"


@dataclass(frozen=True)
class DecisionPreview:
    """What an action would do, and the version to apply it against."""

    decision_key: str
    action: Action
    plan: MergePlan | RelatePlan | TierPlan | KeepDistinctPlan
    version: DecisionVersion


ApplyOutcome = Applied | NotApplied | Stale


@dataclass(frozen=True)
class ReconcilePlan:
    """Recording how two pages relate, as `reconcile` writes it: both stand
    (`reconciled_with`, no holder), one replaces the other (`supersedes`, the
    target listed as deprecated), or one refines the other (`revises`, both
    stay current)."""

    first_id: str
    second_id: str
    relation_type: Literal["reconciled_with", "supersedes", "revises"]
    holder_id: str | None
    target_id: str | None
    deprecates_id: str | None
    pages_changed: tuple[str, ...]
    """The pages whose file changes: an edge added or a note appended."""
    kind: Literal["reconcile"] = "reconcile"


@dataclass(frozen=True)
class DeclinePlan:
    """Recording that a contradiction was looked at and left as it is."""

    first_id: str
    second_id: str
    merged_absorbed_id: str | None
    kind: Literal["decline"] = "decline"


@dataclass(frozen=True)
class FindingPreview:
    """What a contradiction or revision action would do, and the version
    `apply_decision` applies it against."""

    decision_key: str
    action: Action
    plan: ReconcilePlan | DeclinePlan
    version: DecisionVersion


def _plan_of(
    body: curate_decisions.DecisionPreviewBody,
) -> MergePlan | RelatePlan | TierPlan | KeepDistinctPlan:
    if isinstance(body, curate_decisions.MergeGroupDecisionPreview):
        marked = {f"{member}.md" for member in body.selection.member_ids}
        rewritten = {
            rel for step in body.steps for rel in step.prepared.touched_files
        } - marked
        steps = tuple(
            MergeStep(
                survivor_id=step.prepared.survivor_canonical,
                absorbed_id=step.prepared.absorbed_canonical,
                absorbed_type=step.absorbed_type,
                type_outcome=step.type_outcome,
                files_rewritten=len(step.prepared.touched_files),
            )
            for step in body.steps
        )
        return MergePlan(
            survivor_id=body.survivor_id,
            absorbed_id=steps[0].absorbed_id,
            survivor_criterion="chosen",
            files_rewritten=len(rewritten),
            cross_source_same_pair=body.cross_source_same_pair,
            cross_type_concern=body.cross_type_concern,
            steps=steps,
            kept_distinct_ids=body.kept_distinct_ids,
            survivor_type=body.survivor_type,
        )
    if isinstance(body, curate_decisions.MergeDecisionPreview):
        return MergePlan(
            survivor_id=body.prepared.survivor_canonical,
            absorbed_id=body.prepared.absorbed_canonical,
            survivor_criterion=body.survivor_criterion,
            files_rewritten=len(body.prepared.touched_files),
            cross_source_same_pair=body.cross_source_same_pair,
            cross_type_concern=body.cross_type_concern,
            steps=(
                MergeStep(
                    survivor_id=body.prepared.survivor_canonical,
                    absorbed_id=body.prepared.absorbed_canonical,
                    absorbed_type=body.absorbed_type,
                    type_outcome=body.type_outcome,
                    files_rewritten=len(body.prepared.touched_files),
                ),
            ),
            survivor_type=body.survivor_type,
        )
    if isinstance(body, curate_decisions.RelateDecisionPreview):
        return RelatePlan(body.source_id, body.target_id, body.relation_type)
    if isinstance(body, curate_decisions.TierDecisionPreview):
        return TierPlan(body.type_name, body.tier, configured_tier=body.configured_tier)
    return KeepDistinctPlan(body.member_ids)


def preview_decision(
    workspace: Workspace,
    decision_key: str,
    action: Action,
    *,
    selection: GroupSelection | None = None,
) -> DecisionPreview | NotApplied | Stale:
    """What `action` would do to the open decision `decision_key`, with the
    version to apply it against.

    Writes nothing, takes no lock, builds no client and makes no model call. A
    decision that is no longer open is the returned `Stale`; an action it does
    not offer, and skip (which has no plan), are the returned `NotApplied`.

    `selection` names the members the person marked the same and the one that
    stays; it is required by `Action.MERGE_GROUP` and by no other action. A
    missing or invalid one is the returned `NotApplied("invalid_selection")`.
    The returned version carries it, so `apply_decision` takes no selection."""
    result = curate_decisions.preview_decision(
        workspace.root,
        workspace._layout,
        decision_key,
        action,
        policy=_POLICY,
        selection=selection,
    )
    if isinstance(result, NotApplied | Stale):
        return result
    return DecisionPreview(
        decision_key=decision_key,
        action=action,
        plan=_plan_of(result.preview),
        version=result.version,
    )


def _finding_plan(
    body: curate_decisions.FindingPreviewBody,
) -> ReconcilePlan | DeclinePlan:
    if isinstance(body, curate_decisions.DeclineDecisionPreview):
        return DeclinePlan(*body.pair_ids, body.merged_absorbed_id)
    return ReconcilePlan(
        first_id=body.first_id,
        second_id=body.second_id,
        relation_type=body.relation_type,
        holder_id=body.holder_id,
        target_id=body.target_id,
        deprecates_id=body.deprecates_id,
        pages_changed=body.pages_changed,
    )


def preview_finding(
    workspace: Workspace, decision_key: str, action: Action
) -> FindingPreview | NotApplied | Stale:
    """What a contradiction or revision `action` would do to the open decision
    `decision_key`, with the version `apply_decision` applies it against.

    Writes nothing, takes no lock, builds no client and makes no model call.
    The decision kinds' own actions are previewed by `preview_decision`; given
    one, this returns `NotApplied("wrong_preview")`, as `preview_decision` does
    given a finding action. A pair already reconciled another way is
    `NotApplied("already_reconciled")`; the other refusals are
    `preview_decision`'s."""
    result = curate_decisions.preview_finding(
        workspace.root, workspace._layout, decision_key, action
    )
    if isinstance(result, NotApplied | Stale):
        return result
    return FindingPreview(
        decision_key=decision_key,
        action=action,
        plan=_finding_plan(result.preview),
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

    `version` may come from `preview_decision` or from `preview_finding`. A
    decision that changed since the preview is the returned `Stale`, and one
    that is gone or does not take the action the returned `NotApplied`; none of
    these writes anything. Otherwise the decision's own files are written and
    committed to the workspace's git repository, only the paths it wrote. A
    commit that cannot be made still leaves the apply successful with
    `commit_sha=None`, reported once through `on_warning`.
    `committed_confidential` is true when the commit holds a concept explicitly
    marked confidential, so an app can show the notice the CLI prints; the API
    itself prints nothing.

    The workspace lock is held only inside the commit section, waiting at most
    `wait_seconds` (0 to 3600); beyond it `WorkspaceBusyError` is raised and
    nothing is written. Raises the `WriteRefused` family when a check fails
    after the preview. Merged bodies are not reconciled with a model, and the
    derived indexes are not refreshed."""
    wait = _wiring.validated_wait(wait_seconds)
    notes = _wiring.CommitNotes()
    result = curate_decisions.apply_decision(
        workspace.root,
        workspace._layout,
        version,
        action,
        policy=_POLICY,
        ports=_wiring.decision_ports(
            workspace, wait_seconds=wait, on_warning=on_warning, notes=notes
        ),
        observer=curate_decisions.DecisionObserver(),
        surface=_SURFACE,
    )
    if isinstance(result, Applied) and notes.confidential:
        return replace(result, committed_confidential=True)
    return result
