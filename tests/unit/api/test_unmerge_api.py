"""`openkos.api` and undoing a merge (`unmerge-api` change, ADR-0064): the
merge history is read and an unmerge is previewed through the public facade
only, over merges made through the decision surface itself.

Every test runs in a real git-backed workspace whose identity is pinned.
"""

import dataclasses
import shutil
import threading
from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    Action,
    Applied,
    DecisionPreview,
    GroupSelection,
    MergeRecord,
    NotUnmergeable,
    Refused,
    Unmerged,
    UnmergeInterrupted,
    UnmergePreview,
    UnmergeStale,
    UnmergeStep,
    UnmergeVersion,
    Workspace,
    WorkspaceBusyError,
    WriteFailed,
    apply_decision,
    apply_unmerge,
    merge_history,
    preview_decision,
    preview_unmerge,
)
from openkos.application import curate_decisions, unmerge_service
from openkos.vcs import git as vcs_git
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _pin_identity
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


# -- apply -------------------------------------------------------------------- #


def _version(root: Path, survivor: str, absorbed: str, **kw: bool) -> UnmergeVersion:
    preview = preview_unmerge(Workspace.open(root), survivor, absorbed, **kw)
    assert isinstance(preview, UnmergePreview), preview
    return preview.version


def _commits(root: Path) -> list[str]:
    return _git(root, "log", "--format=%s").splitlines()


def test_a_pairwise_merge_is_split_again_to_its_bytes_before_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, (A, B))
    before_merge = {
        k: v for k, v in support.snapshot(root).items() if k.startswith("bundle/")
    }
    _apply(root, (A, B))
    version = _version(root, A, B)

    outcome = apply_unmerge(Workspace.open(root), version)

    assert isinstance(outcome, Unmerged)
    assert outcome.survivor_id == A
    assert outcome.restored_ids == (B,)
    [sha] = outcome.commit_shas
    assert sha is not None
    assert _git(root, "rev-parse", "--short", "HEAD") == sha
    assert _commits(root)[0] == f"openkos: unmerge {B}"
    assert outcome.committed_confidential is False
    after = {k: v for k, v in support.snapshot(root).items() if k.startswith("bundle/")}
    changed = {
        k
        for k in before_merge.keys() | after.keys()
        if before_merge.get(k) != after.get(k)
    }
    assert changed == {"bundle/log.md"}


def test_a_group_is_split_again_one_commit_per_step(group: Path) -> None:
    version = _version(group, A, B)

    outcome = apply_unmerge(Workspace.open(group), version)

    assert isinstance(outcome, Unmerged)
    assert outcome.restored_ids == (C, B)
    assert len(outcome.commit_shas) == 2
    assert _commits(group)[:2] == [f"openkos: unmerge {B}", f"openkos: unmerge {C}"]
    assert (group / "bundle" / f"{B}.md").is_file()
    assert (group / "bundle" / f"{C}.md").is_file()
    assert merge_history(Workspace.open(group), A) == ()


def test_a_bundle_changed_since_the_preview_is_stale(pair: Path) -> None:
    version = _version(pair, A, B)
    _doc(pair, C)
    before = support.snapshot(pair)

    outcome = apply_unmerge(Workspace.open(pair), version)

    assert outcome == UnmergeStale(survivor_id=A, absorbed_id=B)
    assert outcome.reason == "content_changed"
    assert support.snapshot(pair) == before


def test_a_flipped_echoed_flag_is_stale(pair: Path) -> None:
    version = _version(pair, A, B)
    before = support.snapshot(pair)

    outcome = apply_unmerge(
        Workspace.open(pair), dataclasses.replace(version, discard_survivor_edits=True)
    )

    assert isinstance(outcome, UnmergeStale)
    assert support.snapshot(pair) == before


def test_a_replayed_version_is_not_merged(pair: Path) -> None:
    version = _version(pair, A, B)
    assert isinstance(apply_unmerge(Workspace.open(pair), version), Unmerged)

    assert apply_unmerge(Workspace.open(pair), version) == NotUnmergeable(
        survivor_id=A, absorbed_id=B, reason="not_merged"
    )


def test_an_edited_survivor_is_split_when_the_preview_opted_in(pair: Path) -> None:
    path = pair / "bundle" / f"{A}.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nEdited.\n", "utf-8")
    version = _version(pair, A, B, discard_survivor_edits=True)

    outcome = apply_unmerge(Workspace.open(pair), version)

    assert isinstance(outcome, Unmerged)
    assert "Edited." not in path.read_text(encoding="utf-8")


def test_a_held_lock_raises_busy_and_writes_nothing(pair: Path) -> None:
    version = _version(pair, A, B)
    before = support.snapshot(pair)

    with lock.workspace_lock(pair), pytest.raises(WorkspaceBusyError):
        apply_unmerge(Workspace.open(pair), version, wait_seconds=0)

    assert support.snapshot(pair) == before


@pytest.mark.parametrize("wait", [-1.0, float("nan")])
def test_a_wait_outside_the_range_is_refused_before_anything_runs(
    pair: Path, wait: float
) -> None:
    version = _version(pair, A, B)
    before = support.snapshot(pair)

    with pytest.raises(ValueError, match="wait_seconds"):
        apply_unmerge(Workspace.open(pair), version, wait_seconds=wait)

    assert support.snapshot(pair) == before


def test_a_non_repository_still_splits_and_warns_once(pair: Path) -> None:
    version = _version(pair, A, B)
    shutil.rmtree(pair / ".git")
    warnings: list[str] = []

    outcome = apply_unmerge(Workspace.open(pair), version, on_warning=warnings.append)

    assert isinstance(outcome, Unmerged)
    assert outcome.commit_shas == (None,)
    assert warnings == [
        "the unmerge was applied but not committed: the workspace is not "
        "inside a git repository"
    ]


def test_a_commit_restoring_a_confidential_concept_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_identity(monkeypatch, tmp_path)
    root = make_workspace(tmp_path, monkeypatch)
    _doc(root, A)
    write_doc(
        root,
        B,
        {"type": "Concept", "title": f"Title {B}", "sensitivity": "confidential"},
        f"Body of {B}.\n",
    )
    vcs_git.commit_paths(root, ["bundle"], "seed concepts")
    support.enqueue_identity(root, (A, B), verdict="same", fresh=True)
    _apply(root, (A, B))

    outcome = apply_unmerge(Workspace.open(root), _version(root, A, B))

    assert isinstance(outcome, Unmerged)
    assert outcome.committed_confidential is True


def test_an_interrupted_unwind_is_caught_as_a_write_failure(
    group: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _version(group, A, B)
    real = unmerge_service._unmerge_step
    calls: list[str] = []

    def second_fails(
        root: Path, request: object, absorbed: str, *args: object, **kwargs: object
    ) -> None:
        calls.append(absorbed)
        if len(calls) == 2:
            raise Refused("openkos unmerge: refused.")
        real(root, request, absorbed, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(unmerge_service, "_unmerge_step", second_fails)

    with pytest.raises(WriteFailed) as caught:
        apply_unmerge(Workspace.open(group), version)

    assert isinstance(caught.value, UnmergeInterrupted)
    assert caught.value.restored_ids == (C,)
    assert caught.value.failed_absorbed_id == B


def test_a_lock_released_within_the_wait_lets_the_unmerge_through(
    pair: Path,
) -> None:
    version = _version(pair, A, B)
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with lock.workspace_lock(pair):
            holding.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    holding.wait(5)
    threading.Timer(0.3, release.set).start()
    try:
        outcome = apply_unmerge(Workspace.open(pair), version, wait_seconds=10)
    finally:
        release.set()
        holder.join(5)

    assert isinstance(outcome, Unmerged)
