"""`openkos.api` and undoing a merge (`unmerge-api` change, ADR-0064): the
merge history is read and an unmerge is previewed through the public facade
only, over merges made through the decision surface itself.

Every test runs in a real git-backed workspace whose identity is pinned.
"""

from pathlib import Path

import pytest

from openkos.api import (
    Action,
    Applied,
    DecisionPreview,
    GroupSelection,
    MergeRecord,
    NotUnmergeable,
    UnmergePreview,
    UnmergeStep,
    UnmergeVersion,
    Workspace,
    apply_decision,
    merge_history,
    preview_decision,
    preview_unmerge,
)
from openkos.application import curate_decisions
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _pin_identity
from tests.unit.application.curation_support import make_workspace
from tests.unit.cli.commit_phase_support import write_doc

A, B, C = (f"concepts/{n}" for n in "abc")


def _doc(root: Path, concept_id: str) -> None:
    write_doc(
        root,
        concept_id,
        {"type": "Concept", "title": f"Title {concept_id}"},
        f"Body of {concept_id}.\n",
    )


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, members: tuple[str, ...]
) -> Path:
    _pin_identity(monkeypatch, tmp_path)
    root = make_workspace(tmp_path, monkeypatch)
    for concept_id in members:
        _doc(root, concept_id)
    vcs_git.commit_paths(root, ["bundle"], "seed concepts")
    support.enqueue_identity(root, members, verdict="same", fresh=True)
    support.forbid_model_clients(monkeypatch, "an unmerge")
    return root


def _apply(
    root: Path, members: tuple[str, ...], selection: GroupSelection | None = None
) -> Applied:
    key = curate_decisions.identity_decision_key(members)
    action = Action.MERGE if selection is None else Action.MERGE_GROUP
    preview = preview_decision(Workspace.open(root), key, action, selection=selection)
    assert isinstance(preview, DecisionPreview), preview
    applied = apply_decision(Workspace.open(root), preview.version, action)
    assert isinstance(applied, Applied), applied
    return applied


@pytest.fixture
def pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`a` absorbed `b` through a pairwise decision."""
    root = _workspace(tmp_path, monkeypatch, (A, B))
    _apply(root, (A, B))
    return root


@pytest.fixture
def group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`a` absorbed `b`, then `c`, through one group decision."""
    root = _workspace(tmp_path, monkeypatch, (A, B, C))
    applied = _apply(
        root, (A, B, C), GroupSelection(survivor_id=A, member_ids=(A, B, C))
    )
    assert applied.merged == ((A, B), (A, C))
    return root


# -- history ------------------------------------------------------------------ #


def test_history_lists_a_groups_merges_newest_first(group: Path) -> None:
    before = support.snapshot(group)

    history = merge_history(Workspace.open(group), A)

    assert [(r.survivor_id, r.absorbed_id, r.absorbed_title) for r in history] == [
        (A, C, f"Title {C}"),
        (A, B, f"Title {B}"),
    ]
    assert all(type(r) is MergeRecord for r in history)
    assert all(r.merged_at for r in history)
    assert support.snapshot(group) == before


def test_history_of_a_concept_never_merged_is_empty(pair: Path) -> None:
    assert merge_history(Workspace.open(pair), "concepts/none") == ()


# -- preview ------------------------------------------------------------------ #


def test_a_pairwise_merge_previews_as_one_step(pair: Path) -> None:
    before = support.snapshot(pair)

    preview = preview_unmerge(Workspace.open(pair), A, B)

    assert isinstance(preview, UnmergePreview)
    assert (preview.survivor_id, preview.absorbed_id) == (A, B)
    [step] = preview.steps
    assert isinstance(step, UnmergeStep)
    assert (step.survivor_id, step.absorbed_id, step.absorbed_title) == (
        A,
        B,
        f"Title {B}",
    )
    assert step.files_rewritten == 0
    assert not step.survivor_edits_unverifiable
    assert not step.restores_catalog_snapshot
    assert not preview.survivor_edits_discarded
    assert isinstance(preview.version, UnmergeVersion)
    assert (preview.version.survivor_id, preview.version.absorbed_id) == (A, B)
    assert preview.version.discard_survivor_edits is False
    assert len(preview.version.content_digest) == 64
    assert support.snapshot(pair) == before


def test_a_group_previews_every_step_newest_first(group: Path) -> None:
    preview = preview_unmerge(Workspace.open(group), A, B)

    assert isinstance(preview, UnmergePreview)
    assert [s.absorbed_id for s in preview.steps] == [C, B]


def test_an_absorbed_survivor_names_its_absorber(group: Path) -> None:
    assert preview_unmerge(Workspace.open(group), B, C) == NotUnmergeable(
        survivor_id=B, absorbed_id=C, reason="survivor_missing", absorbed_into=A
    )


def test_an_id_never_merged_in_is_not_merged(pair: Path) -> None:
    _doc(pair, C)

    assert preview_unmerge(Workspace.open(pair), A, C) == NotUnmergeable(
        survivor_id=A, absorbed_id=C, reason="not_merged"
    )


def test_an_edited_survivor_needs_the_opt_in(pair: Path) -> None:
    path = pair / "bundle" / f"{A}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", "utf-8")

    refused = preview_unmerge(Workspace.open(pair), A, B)
    allowed = preview_unmerge(Workspace.open(pair), A, B, discard_survivor_edits=True)

    assert refused == NotUnmergeable(
        survivor_id=A, absorbed_id=B, reason="survivor_edited"
    )
    assert isinstance(allowed, UnmergePreview)
    assert allowed.survivor_edits_discarded
    assert allowed.version.discard_survivor_edits is True
