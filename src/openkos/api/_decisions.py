"""Listing open decisions.

`list_open_decisions` returns items that carry the stored proposal payload, an
unversioned mapping that can derive from confidential concepts and holds the
adjudicator's confidence. None of that crosses the facade: the typed details
below are built from the reader functions that live beside the payload's
writer, and a payload they cannot read yields a decision with no detail, still
listed (ADR-0053: list, never hide).
"""

from dataclasses import dataclass
from typing import Literal

from openkos.api._workspace import Workspace
from openkos.application import curate_decisions
from openkos.application.curate_decisions import Action, WithheldAction

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
