"""`openkos.api` and undoing a review choice (`review-undo` change, ADR-0067):
each undo reverses a choice applied through the decision surface itself, through
the public facade only, in a real git-backed workspace whose identity is
pinned. Previews and lists take no lock, build no client and write nothing."""

import dataclasses
import shutil
from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    Action,
    Applied,
    DecisionPreview,
    KeepDistinctPlan,
    KeptDistinct,
    NotUndoable,
    Reopened,
    ReopenPreview,
    ReopenVersion,
    UndoStale,
    Workspace,
    WorkspaceBusyError,
    apply_decision,
    apply_reopen,
    kept_distinct,
    list_decisions,
    preview_decision,
    preview_reopen,
    refresh_decision_queue,
)
from openkos.application import curate_decisions
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files, _pin_identity
from tests.unit.api.test_unmerge_api import A, B, C, _workspace
from tests.unit.application.curation_support import make_workspace


def _keep_separate(root: Path, members: tuple[str, ...]) -> None:
    key = curate_decisions.identity_decision_key(members)
    preview = preview_decision(Workspace.open(root), key, Action.KEEP_DISTINCT)
    assert isinstance(preview, DecisionPreview), preview
    assert isinstance(preview.plan, KeepDistinctPlan)
    applied = apply_decision(
        Workspace.open(root), preview.version, Action.KEEP_DISTINCT
    )
    assert isinstance(applied, Applied), applied


@pytest.fixture
def kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`a` and `b` kept separate through a decision."""
    root = _workspace(tmp_path, monkeypatch, (A, B))
    _keep_separate(root, (A, B))
    return root


def _preview(root: Path, members: tuple[str, ...]) -> ReopenPreview:
    preview = preview_reopen(Workspace.open(root), members)
    assert isinstance(preview, ReopenPreview), preview
    return preview


# -- keep separate ------------------------------------------------------------- #


def test_a_kept_pair_is_listed_and_previewed_without_the_lock(kept: Path) -> None:
    before = support.snapshot(kept)

    with lock.workspace_lock(kept):
        listed = kept_distinct(Workspace.open(kept))
        preview = _preview(kept, (B, A))

    assert [(type(r), r.member_ids) for r in listed] == [(KeptDistinct, (A, B))]
    assert listed[0].decided_at
    assert preview.member_ids == (A, B)
    assert type(preview.version) is ReopenVersion
    assert preview.version.member_ids == (A, B)
    assert support.snapshot(kept) == before


def test_a_kept_pair_is_reopened_in_one_commit(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    head = _git(kept, "rev-parse", "HEAD")

    reopened = apply_reopen(Workspace.open(kept), preview.version)

    assert isinstance(reopened, Reopened)
    assert reopened.member_ids == (A, B)
    assert reopened.commit_sha is not None
    assert _git(kept, "rev-parse", "HEAD~1") == head
    assert _git(kept, "log", "-1", "--format=%s") == f"openkos: reopen identity {A}/{B}"
    assert [path.split("/")[1] for path in _head_files(kept)] == [".state"]
    assert kept_distinct(Workspace.open(kept)) == ()


def test_a_pair_never_kept_separate_is_not_undoable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, (A, B))

    assert preview_reopen(Workspace.open(root), (A, B)) == NotUndoable(
        "not_kept_distinct"
    )


def test_an_altered_version_is_stale_and_writes_nothing(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    altered = dataclasses.replace(preview.version, content_digest="0" * 64)
    before = support.snapshot(kept)

    assert apply_reopen(Workspace.open(kept), altered) == UndoStale()
    assert support.snapshot(kept) == before


def test_a_replayed_version_is_not_undoable(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    assert isinstance(apply_reopen(Workspace.open(kept), preview.version), Reopened)

    assert apply_reopen(Workspace.open(kept), preview.version) == NotUndoable(
        "not_kept_distinct"
    )


def test_a_held_lock_raises_busy_and_writes_nothing(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    before = support.snapshot(kept)

    with lock.workspace_lock(kept), pytest.raises(WorkspaceBusyError):
        apply_reopen(Workspace.open(kept), preview.version)

    assert support.snapshot(kept) == before


def test_a_wait_outside_the_range_is_refused_before_anything_runs(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    before = support.snapshot(kept)

    with pytest.raises(ValueError, match="wait_seconds"):
        apply_reopen(Workspace.open(kept), preview.version, wait_seconds=-1)

    assert support.snapshot(kept) == before


def test_a_commit_that_cannot_be_made_is_warned_once(kept: Path) -> None:
    preview = _preview(kept, (A, B))
    shutil.rmtree(kept / ".git")
    warnings: list[str] = []

    reopened = apply_reopen(
        Workspace.open(kept), preview.version, on_warning=warnings.append
    )

    assert isinstance(reopened, Reopened)
    assert reopened.commit_sha is None
    assert len(warnings) == 1
    assert warnings[0].startswith("the undo was applied but not committed")


def test_a_reopened_pair_is_offered_again_by_a_queue_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin_identity(monkeypatch, tmp_path)
    root = make_workspace(tmp_path, monkeypatch)
    support.write_identity_groups(root)
    support.forbid_model_clients(monkeypatch, "an undo")
    refresh_decision_queue(Workspace.open(root))
    pair = ("concepts/g0-a", "concepts/g0-b")
    key = curate_decisions.identity_decision_key(pair)
    _keep_separate(root, pair)
    assert key not in {d.decision_key for d in list_decisions(Workspace.open(root))}

    apply_reopen(Workspace.open(root), _preview(root, pair).version)
    refresh_decision_queue(Workspace.open(root))

    assert key in {d.decision_key for d in list_decisions(Workspace.open(root))}


def test_a_group_member_set_is_reopened_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path, monkeypatch, (A, B, C))
    _keep_separate(root, (A, B, C))

    assert preview_reopen(Workspace.open(root), (A, B)) == NotUndoable(
        "not_kept_distinct"
    )
    preview = _preview(root, (C, B, A))
    assert isinstance(apply_reopen(Workspace.open(root), preview.version), Reopened)
    assert kept_distinct(Workspace.open(root)) == ()
