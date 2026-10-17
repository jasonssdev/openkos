"""Unit tests for the group half of `openkos.application.curate_decisions`
(the `identity-groups-decision` change, ADR-0063): the group selection, the
group preview and the version that binds the selection.

Every test drives the real function over a real workspace; nothing here mocks
the function under test.
"""

import dataclasses
from pathlib import Path

import pytest

from openkos import config
from openkos.application import curate_decisions
from openkos.application.curate_decisions import (
    Action,
    DecisionVersion,
    GroupSelection,
    MergeGroupDecisionPreview,
    NotApplied,
)
from openkos.resolution.adjudication import Verdict
from tests.unit.application.group_support import (
    A,
    B,
    C,
    D,
    group_of,
    make_workspace,
    selection_of,
    snapshot,
    write_concept,
)

_A, _B, _C, _D = A, B, C, D
_write = write_concept
_workspace = make_workspace
_group = group_of
_selection = selection_of


def _preview(
    root: Path,
    layout: config.WorkspaceLayout,
    selection: GroupSelection,
    *,
    members: tuple[str, ...] = (_A, _B, _C),
    verdict: Verdict | None = Verdict.SAME,
) -> MergeGroupDecisionPreview | NotApplied:
    return curate_decisions.preview_merge_group(
        root, layout, _group(*members), verdict=verdict, selection=selection
    )


def _must(
    root: Path,
    layout: config.WorkspaceLayout,
    selection: GroupSelection,
    **kwargs: object,
) -> MergeGroupDecisionPreview:
    preview = _preview(root, layout, selection, **kwargs)  # type: ignore[arg-type]
    assert isinstance(preview, MergeGroupDecisionPreview), preview
    return preview


_snapshot = snapshot


# -- 1.1.1 the selection ---------------------------------------------------- #

_INVALID = NotApplied(Action.MERGE_GROUP, "invalid_selection")


def test_the_group_action_follows_the_decision_actions_it_joined() -> None:
    """Added after `set_tier`; the finding actions that came later follow it, so
    every earlier wire name keeps its position."""
    actions = list(Action)
    assert actions[actions.index(Action.SET_TIER) + 1] is Action.MERGE_GROUP
    assert Action.MERGE_GROUP.value == "merge_group"


@pytest.mark.parametrize(
    "selection",
    [
        None,
        _selection(_C, _A, _B),  # survivor not among the marked members
        _selection(_A, _A, "concepts/zzz"),  # a member outside the group
        _selection(_A, _A),  # one marked member
        _selection(_A, _A, _A),  # one distinct marked member, repeated
        _selection("concepts/zzz", "concepts/zzz", _A),  # survivor outside the group
        _selection(_A),  # nothing marked
    ],
)
def test_an_invalid_selection_is_the_returned_not_applied_result(
    selection: GroupSelection | None,
) -> None:
    result = curate_decisions.validate_selection((_A, _B, _C), selection)

    assert result == _INVALID


def test_a_valid_selection_comes_back_deduplicated_and_ascending() -> None:
    result = curate_decisions.validate_selection(
        (_A, _B, _C), _selection(_A, _C, _A, _C)
    )

    assert result == GroupSelection(survivor_id=_A, member_ids=(_A, _C))


# -- 1.1.2 the preview ------------------------------------------------------ #


def test_all_three_marked_previews_two_steps_in_ascending_absorbed_order(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path)
    before = _snapshot(root)

    preview = _must(root, layout, _selection(_B, _C, _A, _B))

    assert preview.survivor_id == _B
    assert [s.prepared.survivor_canonical for s in preview.steps] == [_B, _B]
    assert [s.prepared.absorbed_canonical for s in preview.steps] == [_A, _C]
    assert preview.kept_distinct_ids == ()
    assert preview.as_proposed is True
    assert preview.selection == GroupSelection(_B, (_A, _B, _C))
    assert _snapshot(root) == before


def test_no_step_names_an_unmarked_member(tmp_path: Path) -> None:
    root, layout = _workspace(tmp_path, {_A: "Concept", _B: "Concept", _C: "Concept"})

    preview = _must(root, layout, _selection(_A, _A, _B))

    names = {
        name
        for step in preview.steps
        for name in (step.prepared.survivor_canonical, step.prepared.absorbed_canonical)
    }
    assert names == {_A, _B}
    assert preview.kept_distinct_ids == (_C,)
    assert preview.as_proposed is False


# -- 1.1.3 verdict and member presence --------------------------------------- #


@pytest.mark.parametrize("verdict", [None, Verdict.DIFFERENT, Verdict.UNCERTAIN])
def test_a_verdict_other_than_same_is_not_mergeable(
    tmp_path: Path, verdict: Verdict | None
) -> None:
    root, layout = _workspace(tmp_path)

    result = _preview(root, layout, _selection(_A, _A, _B), verdict=verdict)

    assert result == NotApplied(Action.MERGE_GROUP, "not_mergeable")


def test_a_member_that_does_not_resolve_is_member_missing(tmp_path: Path) -> None:
    root, layout = _workspace(tmp_path)
    (layout.bundle_dir / f"{_C}.md").unlink()

    result = _preview(root, layout, _selection(_A, _A, _C))

    assert result == NotApplied(Action.MERGE_GROUP, "member_missing")


# -- 1.1.4 type outcomes ----------------------------------------------------- #

_THREE_TYPES = {_A: "Person", _B: "Organization", _C: "Place"}


def test_a_survivor_without_a_runner_up_records_one_type_and_discards_the_next(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path, _THREE_TYPES)

    preview = _must(root, layout, _selection(_A, _A, _B, _C))

    assert preview.survivor_type == "Person"
    assert [(s.absorbed_type, s.type_outcome) for s in preview.steps] == [
        ("Organization", "recorded"),
        ("Place", "discarded"),
    ]
    assert preview.cross_type_concern is not None
    assert "Person" in preview.cross_type_concern


def test_a_survivor_that_already_records_one_discards_both(tmp_path: Path) -> None:
    root, layout = _workspace(tmp_path, _THREE_TYPES)
    _write(layout, _A, "Person", extra={"type_alternative": "Thing"})

    preview = _must(root, layout, _selection(_A, _A, _B, _C))

    assert [s.type_outcome for s in preview.steps] == ["discarded", "discarded"]


def test_the_same_type_is_reported_as_same_type(tmp_path: Path) -> None:
    root, layout = _workspace(tmp_path)

    preview = _must(root, layout, _selection(_A, _A, _B, _C))

    assert [s.type_outcome for s in preview.steps] == ["same_type", "same_type"]
    assert preview.cross_type_concern is None


def test_an_unreadable_type_is_unknown(tmp_path: Path) -> None:
    root, layout = _workspace(tmp_path)
    (layout.bundle_dir / f"{_B}.md").write_text(
        "---\ntitle: no type here\n---\n\nbody\n", encoding="utf-8"
    )

    preview = _must(root, layout, _selection(_A, _A, _B))

    assert [(s.absorbed_type, s.type_outcome) for s in preview.steps] == [
        (None, "unknown")
    ]


@pytest.mark.parametrize("survivor", [_A, _B, _C])
def test_the_previewed_outcome_matches_the_pairwise_plan_of_the_first_step(
    tmp_path: Path, survivor: str
) -> None:
    """The first step's outcome is exactly what the unchanged merge plan says."""
    root, layout = _workspace(tmp_path, _THREE_TYPES)

    preview = _must(root, layout, _selection(survivor, _A, _B, _C))

    first = preview.steps[0]
    discarded = first.prepared.discarded_absorbed_type
    assert (first.type_outcome == "discarded") is (discarded is not None)


# -- 1.1.5 the concern covers the marked members only ------------------------ #


def test_an_unmarked_member_of_another_type_does_not_raise_the_concern(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(
        tmp_path, {_A: "Person", _B: "Person", _C: "Organization"}
    )

    unmarked = _must(root, layout, _selection(_A, _A, _B))
    marked = _must(root, layout, _selection(_A, _A, _C))

    assert unmarked.cross_type_concern is None
    assert marked.cross_type_concern is not None


# -- 1.1.6 / 1.1.7 the version ---------------------------------------------- #


def _version(
    root: Path, layout: config.WorkspaceLayout, selection: GroupSelection
) -> DecisionVersion:
    return curate_decisions.version_of(
        _must(root, layout, selection), layout=layout, payload_digest="p"
    )


def test_a_group_version_carries_the_canonical_selection(tmp_path: Path) -> None:
    root, layout = _workspace(tmp_path)

    version = _version(root, layout, _selection(_A, _C, _A, _C))

    assert version.selection == GroupSelection(_A, (_A, _C))


def test_reordering_or_repeating_marked_members_keeps_digest_and_token(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path)

    one = _version(root, layout, _selection(_A, _C, _A, _A))
    other = _version(root, layout, _selection(_A, _A, _C))

    assert one.content_digest == other.content_digest
    assert one.token() == other.token()


def test_a_different_selection_changes_the_digest_and_the_token(
    tmp_path: Path,
) -> None:
    root, layout = _workspace(tmp_path)

    two = _version(root, layout, _selection(_A, _A, _B))
    three = _version(root, layout, _selection(_A, _A, _B, _C))
    other_survivor = _version(root, layout, _selection(_B, _A, _B))

    assert (
        len({two.content_digest, three.content_digest, other_survivor.content_digest})
        == 3
    )
    assert len({two.token(), three.token(), other_survivor.token()}) == 3


def test_editing_an_unmarked_member_changes_the_digest(tmp_path: Path) -> None:
    root, layout = _workspace(tmp_path)
    before = _version(root, layout, _selection(_A, _A, _B))

    path = layout.bundle_dir / f"{_C}.md"
    path.write_text(path.read_text("utf-8") + "\nEdited.\n", encoding="utf-8")

    assert _version(root, layout, _selection(_A, _A, _B)).content_digest != (
        before.content_digest
    )


def test_the_keep_lines_alone_bind_an_unmarked_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every step's own lines already hash each bundle document the scan read,
    so the `keep` lines are a second guard. Take the first away to prove the
    second holds by itself: an unmarked member's bytes and its absence both
    move the digest."""
    monkeypatch.setattr(curate_decisions, "_merge_content_lines", lambda *_a: [])
    root, layout = _workspace(tmp_path)
    before = _version(root, layout, _selection(_A, _A, _B))

    path = layout.bundle_dir / f"{_C}.md"
    path.write_text(path.read_text("utf-8") + "\nEdited.\n", encoding="utf-8")
    edited = _version(root, layout, _selection(_A, _A, _B))
    path.unlink()
    forgotten = _version(root, layout, _selection(_A, _A, _B))

    assert (
        len({before.content_digest, edited.content_digest, forgotten.content_digest})
        == 3
    )


def test_editing_a_marked_member_changes_the_digest(tmp_path: Path) -> None:
    root, layout = _workspace(tmp_path)
    before = _version(root, layout, _selection(_A, _A, _B))

    path = layout.bundle_dir / f"{_B}.md"
    path.write_text(path.read_text("utf-8") + "\nEdited.\n", encoding="utf-8")

    assert _version(root, layout, _selection(_A, _A, _B)).content_digest != (
        before.content_digest
    )


def test_selection_is_the_last_defaulted_field_of_the_version() -> None:
    fields = dataclasses.fields(DecisionVersion)

    assert fields[-1].name == "selection"
    assert fields[-1].default is None


def test_a_version_without_a_selection_keeps_its_token_byte_for_byte() -> None:
    """Pinned before the change: the formula for every existing token."""
    assert DecisionVersion("identity:abc", "p1", "c1").token() == (
        "846afa6b747861dffa5ee7ecb66751fbb86c62c1a52fb87353b11d0098ae3e71"
    )
    assert DecisionVersion("identity:abc", None, "c1").token() == (
        "f10fbb5b1d091ae15c52213ac58d3941a70ba6ad4450c60a4fa4e216e2d4c8f3"
    )


def test_a_selection_changes_the_token_of_an_otherwise_equal_version() -> None:
    plain = DecisionVersion("identity:abc", "p1", "c1")
    selected = dataclasses.replace(plain, selection=GroupSelection(_A, (_A, _B)))
    other = dataclasses.replace(plain, selection=GroupSelection(_B, (_A, _B)))

    assert len({plain.token(), selected.token(), other.token()}) == 3
