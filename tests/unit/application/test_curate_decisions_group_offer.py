"""Unit tests for offering and dispatching `merge_group`
(`identity-groups-decision`, ADR-0063): which identity items list it, which
withhold it and why, and how `preview_decision` / `apply_decision` route it.

Every test lists and applies over a real workspace and a populated queue row.
"""

import dataclasses
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from openkos import config
from openkos.application import curate_decisions, curate_queue
from openkos.application.curate_decisions import (
    Action,
    Applied,
    BulkEligibility,
    DecisionItem,
    DecisionObserver,
    DecisionPorts,
    DecisionPreview,
    MergeGroupDecisionPreview,
    NotApplied,
    Stale,
    WithheldAction,
    item_consent,
)
from openkos.state import pending_queue as pq
from tests.unit.application.group_support import (
    A,
    B,
    C,
    D,
    enqueue,
    identity_row,
    make_workspace,
    selection_of,
    snapshot,
)

_OFFERED = (Action.MERGE_GROUP, Action.KEEP_DISTINCT, Action.SKIP)
_PAIR_OFFERED = (Action.MERGE, Action.KEEP_DISTINCT, Action.SKIP)
_CROSS = {A: "Person", B: "Organization"}
_THREE = {A: "Concept", B: "Concept", C: "Concept"}


def _seed(
    tmp_path: Path,
    types: dict[str, str],
    *,
    verdict: str | None = "same",
) -> tuple[Path, config.WorkspaceLayout, pq.Proposal]:
    root, layout = make_workspace(tmp_path, types)
    proposal = identity_row(layout, tuple(types), verdict=verdict)
    enqueue(layout, proposal)
    return root, layout, proposal


def _item(layout: config.WorkspaceLayout) -> DecisionItem:
    [item] = curate_decisions.list_open_decisions(layout)
    return item


class _Ports:
    def __init__(self) -> None:
        self.commits: list[tuple[tuple[str, ...], str]] = []

    def autocommit(self, root: Path, paths: Sequence[str], message: str) -> str:
        self.commits.append((tuple(paths), message))
        return "abc1234"

    def ports(self) -> DecisionPorts:
        return DecisionPorts(autocommit=self.autocommit)


# -- 4.1.1 judged SAME ------------------------------------------------------- #


def test_a_judged_same_group_of_three_offers_merge_group_and_withholds_merge(
    tmp_path: Path,
) -> None:
    _root, layout, _proposal = _seed(tmp_path, _THREE)

    item = _item(layout)

    assert item.evaluation == "judged_same"
    assert item.actions == _OFFERED
    assert item.withheld == (WithheldAction(Action.MERGE, "more_than_two_members"),)


def test_a_judged_same_cross_type_pair_offers_merge_group_and_withholds_merge(
    tmp_path: Path,
) -> None:
    _root, layout, _proposal = _seed(tmp_path, _CROSS)

    item = _item(layout)

    assert item.actions == _OFFERED
    assert item.withheld == (WithheldAction(Action.MERGE, "cross_type"),)


def test_a_judged_same_same_type_pair_is_unchanged(tmp_path: Path) -> None:
    _root, layout, _proposal = _seed(tmp_path, {A: "Concept", B: "Concept"})

    item = _item(layout)

    assert item.actions == _PAIR_OFFERED
    assert item.withheld == ()
    assert Action.MERGE_GROUP not in item.actions


# -- 4.1.2 not judged SAME --------------------------------------------------- #


@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        (None, "pending_evaluation"),
        ("different", "judged_different"),
        ("uncertain", "judged_uncertain"),
    ],
)
def test_an_unjudged_or_not_same_group_withholds_both_merges(
    tmp_path: Path, verdict: str | None, reason: str
) -> None:
    _root, layout, _proposal = _seed(tmp_path, _THREE, verdict=verdict)

    item = _item(layout)

    assert item.actions == (Action.KEEP_DISTINCT, Action.SKIP)
    assert item.withheld == (
        WithheldAction(Action.MERGE, reason),  # type: ignore[arg-type]
        WithheldAction(Action.MERGE_GROUP, reason),  # type: ignore[arg-type]
    )


def test_a_pending_cross_type_pair_withholds_both_merges(tmp_path: Path) -> None:
    _root, layout, _proposal = _seed(tmp_path, _CROSS, verdict=None)

    item = _item(layout)

    assert item.withheld == (
        WithheldAction(Action.MERGE, "pending_evaluation"),
        WithheldAction(Action.MERGE_GROUP, "pending_evaluation"),
    )


def test_a_pending_same_type_pair_withholds_only_merge(tmp_path: Path) -> None:
    _root, layout, _proposal = _seed(
        tmp_path, {A: "Concept", B: "Concept"}, verdict=None
    )

    item = _item(layout)

    assert item.withheld == (WithheldAction(Action.MERGE, "pending_evaluation"),)


# -- 4.1.3 dispatch ---------------------------------------------------------- #


def _preview(
    root: Path,
    layout: config.WorkspaceLayout,
    key: str,
    action: Action,
    **kwargs: object,
) -> DecisionPreview | NotApplied | Stale:
    return curate_decisions.preview_decision(
        root,
        layout,
        key,
        action,
        **kwargs,  # type: ignore[arg-type]
    )


def test_a_withheld_merge_group_is_not_mergeable_for_preview_and_apply(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _seed(tmp_path, _THREE, verdict=None)
    selection = selection_of(A, A, B)
    before = snapshot(root)

    previewed = _preview(
        root, layout, proposal.decision_key, Action.MERGE_GROUP, selection=selection
    )
    version = curate_decisions.DecisionVersion(
        proposal.decision_key, "x", "y", selection=selection
    )
    applied = curate_decisions.apply_decision(
        root,
        layout,
        version,
        Action.MERGE_GROUP,
        ports=_Ports().ports(),
        observer=DecisionObserver(),
        surface="api",
    )

    assert previewed == NotApplied(Action.MERGE_GROUP, "not_mergeable")
    assert applied == NotApplied(Action.MERGE_GROUP, "not_mergeable")
    assert snapshot(root) == before


def test_merge_group_on_a_same_type_pair_is_not_offered(tmp_path: Path) -> None:
    root, layout, proposal = _seed(tmp_path, {A: "Concept", B: "Concept"})

    result = _preview(
        root,
        layout,
        proposal.decision_key,
        Action.MERGE_GROUP,
        selection=selection_of(A, A, B),
    )

    assert result == NotApplied(Action.MERGE_GROUP, "not_offered")


@pytest.mark.parametrize(
    "selection",
    [None, selection_of(C, A, B), selection_of(A, A), selection_of(A, A, D)],
)
def test_a_missing_or_invalid_selection_is_invalid_selection(
    tmp_path: Path, selection: object
) -> None:
    root, layout, proposal = _seed(tmp_path, _THREE)
    before = snapshot(root)

    result = _preview(
        root,
        layout,
        proposal.decision_key,
        Action.MERGE_GROUP,
        selection=selection,
    )

    assert result == NotApplied(Action.MERGE_GROUP, "invalid_selection")
    assert snapshot(root) == before


def test_a_selection_supplied_with_merge_is_invalid(tmp_path: Path) -> None:
    root, layout, proposal = _seed(tmp_path, {A: "Concept", B: "Concept"})

    result = _preview(
        root,
        layout,
        proposal.decision_key,
        Action.MERGE,
        selection=selection_of(A, A, B),
    )

    assert result == NotApplied(Action.MERGE, "invalid_selection")


def test_a_selection_supplied_with_keep_distinct_is_invalid(tmp_path: Path) -> None:
    root, layout, proposal = _seed(tmp_path, _THREE)

    result = _preview(
        root,
        layout,
        proposal.decision_key,
        Action.KEEP_DISTINCT,
        selection=selection_of(A, A, B),
    )

    assert result == NotApplied(Action.KEEP_DISTINCT, "invalid_selection")


def test_a_token_for_another_action_that_carries_a_selection_is_invalid(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _seed(tmp_path, {A: "Concept", B: "Concept"})
    version = curate_decisions.DecisionVersion(
        proposal.decision_key, "x", "y", selection=selection_of(A, A, B)
    )

    result = curate_decisions.apply_decision(
        root,
        layout,
        version,
        Action.MERGE,
        ports=_Ports().ports(),
        observer=DecisionObserver(),
        surface="api",
    )

    assert result == NotApplied(Action.MERGE, "invalid_selection")


def test_a_valid_selection_previews_and_applies_end_to_end(tmp_path: Path) -> None:
    root, layout, proposal = _seed(tmp_path, _THREE)

    previewed = _preview(
        root,
        layout,
        proposal.decision_key,
        Action.MERGE_GROUP,
        selection=selection_of(B, C, A, B, A),
    )
    assert isinstance(previewed, DecisionPreview)
    assert isinstance(previewed.preview, MergeGroupDecisionPreview)
    assert previewed.version.selection == selection_of(B, A, B, C)
    ports = _Ports()

    applied = curate_decisions.apply_decision(
        root,
        layout,
        previewed.version,
        Action.MERGE_GROUP,
        ports=ports.ports(),
        observer=DecisionObserver(),
        surface="api",
    )

    assert applied == Applied(
        proposal.decision_key,
        Action.MERGE_GROUP,
        "abc1234",
        merged=((B, A), (B, C)),
    )
    assert curate_queue.open_row_by_key(layout, proposal.decision_key) is None
    assert len(ports.commits) == 1


def test_an_altered_selection_in_the_version_is_stale_through_apply(
    tmp_path: Path,
) -> None:
    root, layout, proposal = _seed(tmp_path, _THREE)
    previewed = _preview(
        root,
        layout,
        proposal.decision_key,
        Action.MERGE_GROUP,
        selection=selection_of(A, A, B),
    )
    assert isinstance(previewed, DecisionPreview)
    altered = dataclasses.replace(previewed.version, selection=selection_of(A, A, B, C))
    before = snapshot(root)

    result = curate_decisions.apply_decision(
        root,
        layout,
        altered,
        Action.MERGE_GROUP,
        ports=_Ports().ports(),
        observer=DecisionObserver(),
        surface="api",
    )

    assert result == Stale(proposal.decision_key, "content_changed")
    assert snapshot(root) == before


# -- 4.1.4 bulk -------------------------------------------------------------- #


def test_identity_stays_outside_bulk_whatever_it_offers() -> None:
    assert curate_decisions.BULK_ELIGIBILITY["identity"] is BulkEligibility.NEVER
    answers: list[Callable[[], bool]] = [lambda: True, lambda: False]
    for interactive in answers:
        assert (
            item_consent(
                "identity", accepted=True, asymmetric=False, interactive=interactive
            )
            == "ask"
        )
