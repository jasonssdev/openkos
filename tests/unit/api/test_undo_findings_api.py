"""`openkos.api` and undoing a settled finding (`undo-findings` change,
ADR-0067, ADR-0074): each undo reverses a choice applied through
`preview_finding` and `apply_decision`, through the public facade only, in a
real git-backed workspace whose identity is pinned. Lists and previews take no
lock, build no client and write nothing."""

import dataclasses
import shutil
from pathlib import Path

import pytest

from openkos import lock
from openkos.api import (
    Action,
    Applied,
    ContradictionReopened,
    ContradictionReopenPreview,
    ContradictionReopenVersion,
    DeclinedContradiction,
    FindingPreview,
    NotUndoable,
    ReconciledPair,
    UndoStale,
    Unreconciled,
    UnreconcilePreview,
    UnreconcileVersion,
    Workspace,
    WorkspaceBusyError,
    apply_decision,
    apply_reopen_contradiction,
    apply_unreconcile,
    declined_contradictions,
    preview_finding,
    preview_reopen_contradiction,
    preview_unreconcile,
    reconciled_pairs,
)
from openkos.model import okf
from tests.unit.api import support
from tests.unit.api.test_decisions_apply import _git, _head_files
from tests.unit.api.test_findings_api import (
    CONTRADICTION_KEY,
    PAIR,
    _contradiction,
    root,
)

__all__ = ["root"]  # the fixture, re-exported for pytest

A, B = PAIR


def _settle(root: Path, action: Action) -> None:
    _contradiction(root)
    workspace = Workspace.open(root)
    preview = preview_finding(workspace, CONTRADICTION_KEY, action)
    assert isinstance(preview, FindingPreview), preview
    assert isinstance(apply_decision(workspace, preview.version, action), Applied)


@pytest.fixture
def declined(root: Path) -> Path:
    """`a` and `b`'s contradiction declined through a decision."""
    _settle(root, Action.DECLINE)
    return root


def _reopen_preview(root: Path) -> ContradictionReopenPreview:
    preview = preview_reopen_contradiction(Workspace.open(root), B, A)
    assert isinstance(preview, ContradictionReopenPreview), preview
    return preview


# -- a declined contradiction ---------------------------------------------------- #


def test_a_declined_contradiction_is_listed_and_previewed_without_the_lock(
    declined: Path,
) -> None:
    before = support.snapshot(declined)

    with lock.workspace_lock(declined):
        listed = declined_contradictions(Workspace.open(declined))
        preview = _reopen_preview(declined)

    assert [
        (type(r), r.first_id, r.second_id, r.merged_absorbed_id) for r in listed
    ] == [(DeclinedContradiction, A, B, None)]
    assert listed[0].decided_at
    assert (preview.first_id, preview.second_id, preview.merged_absorbed_id) == (
        A,
        B,
        None,
    )
    assert type(preview.version) is ContradictionReopenVersion
    assert (preview.version.first_id, preview.version.second_id) == (A, B)
    assert support.snapshot(declined) == before


def test_a_declined_contradiction_is_reopened_in_one_commit(declined: Path) -> None:
    preview = _reopen_preview(declined)
    head = _git(declined, "rev-parse", "HEAD")

    reopened = apply_reopen_contradiction(Workspace.open(declined), preview.version)

    assert isinstance(reopened, ContradictionReopened)
    assert (reopened.first_id, reopened.second_id) == (A, B)
    assert reopened.merged_absorbed_id is None
    assert reopened.commit_sha is not None
    assert _git(declined, "rev-parse", "HEAD~1") == head
    assert _git(declined, "log", "-1", "--format=%s") == (
        f"openkos: reopen contradiction {A} <-> {B}"
    )
    [path] = _head_files(declined)
    assert path.startswith("bundle/.state/decisions/")
    assert declined_contradictions(Workspace.open(declined)) == ()


def test_a_contradiction_never_declined_is_not_undoable(root: Path) -> None:
    assert preview_reopen_contradiction(Workspace.open(root), A, B) == NotUndoable(
        "not_declined"
    )


def test_an_altered_reopen_version_is_stale_and_writes_nothing(
    declined: Path,
) -> None:
    preview = _reopen_preview(declined)
    altered = dataclasses.replace(preview.version, content_digest="0" * 64)
    before = support.snapshot(declined)

    assert apply_reopen_contradiction(Workspace.open(declined), altered) == UndoStale()
    assert support.snapshot(declined) == before


def test_a_replayed_reopen_version_is_not_undoable(declined: Path) -> None:
    preview = _reopen_preview(declined)
    workspace = Workspace.open(declined)
    assert isinstance(
        apply_reopen_contradiction(workspace, preview.version), ContradictionReopened
    )

    assert apply_reopen_contradiction(workspace, preview.version) == NotUndoable(
        "not_declined"
    )


def test_a_held_lock_makes_the_reopen_busy(declined: Path) -> None:
    preview = _reopen_preview(declined)
    before = support.snapshot(declined)

    with lock.workspace_lock(declined), pytest.raises(WorkspaceBusyError):
        apply_reopen_contradiction(Workspace.open(declined), preview.version)

    assert support.snapshot(declined) == before


def test_a_commit_that_cannot_be_made_is_warned_once(declined: Path) -> None:
    preview = _reopen_preview(declined)
    shutil.rmtree(declined / ".git")
    warnings: list[str] = []

    reopened = apply_reopen_contradiction(
        Workspace.open(declined), preview.version, on_warning=warnings.append
    )

    assert isinstance(reopened, ContradictionReopened)
    assert reopened.commit_sha is None
    assert len(warnings) == 1
    assert declined_contradictions(Workspace.open(declined)) == ()


# -- a reconcile ------------------------------------------------------------------ #


def _front(root: Path, concept_id: str) -> dict[str, object]:
    path = root / "bundle" / f"{concept_id}.md"
    return dict(okf.load_frontmatter(path.read_text(encoding="utf-8"))[0])


def _body(root: Path, concept_id: str) -> str:
    path = root / "bundle" / f"{concept_id}.md"
    return okf.load_frontmatter(path.read_text(encoding="utf-8"))[1]


def _unreconcile_preview(root: Path) -> UnreconcilePreview:
    preview = preview_unreconcile(Workspace.open(root), B, A)
    assert isinstance(preview, UnreconcilePreview), preview
    return preview


def test_a_reconciled_pair_is_listed_and_previewed_without_the_lock(
    root: Path,
) -> None:
    _settle(root, Action.SUPERSEDE)
    before = support.snapshot(root)

    with lock.workspace_lock(root):
        listed = reconciled_pairs(Workspace.open(root))
        preview = _unreconcile_preview(root)

    assert listed == (ReconciledPair(A, B, "supersedes", A, B),)
    assert (preview.first_id, preview.second_id) == (A, B)
    assert (preview.relation_type, preview.holder_id, preview.target_id) == (
        "supersedes",
        A,
        B,
    )
    assert preview.withdraws_deprecation is True
    assert preview.pages_changed == (A, B)
    assert type(preview.version) is UnreconcileVersion
    assert (preview.version.first_id, preview.version.second_id) == (A, B)
    assert support.snapshot(root) == before


@pytest.mark.parametrize(
    "action", [Action.RECONCILE, Action.SUPERSEDE, Action.SUPERSEDE_REVERSED]
)
def test_a_settled_contradiction_is_undone_in_one_commit(
    root: Path, action: Action
) -> None:
    pages = {cid: (_front(root, cid), _body(root, cid)) for cid in PAIR}
    _settle(root, action)
    preview = _unreconcile_preview(root)
    head = _git(root, "rev-parse", "HEAD")

    undone = apply_unreconcile(Workspace.open(root), preview.version)

    assert isinstance(undone, Unreconciled)
    assert (undone.first_id, undone.second_id) == (A, B)
    assert undone.relation_type == preview.relation_type
    assert undone.commit_sha is not None
    assert undone.committed_confidential is False
    assert _git(root, "rev-parse", "HEAD~1") == head
    assert _git(root, "log", "-1", "--format=%s") == (
        f"openkos: unreconcile {A} <-> {B}"
    )
    assert _head_files(root) == [f"bundle/{A}.md", f"bundle/{B}.md", "bundle/log.md"]
    for cid in PAIR:
        front, body = pages[cid]
        assert _body(root, cid) == body
        assert {k: v for k, v in _front(root, cid).items() if k != "status"} == front
        assert _front(root, cid).get("status") in (None, "stable")
    assert reconciled_pairs(Workspace.open(root)) == ()


def test_a_pair_never_reconciled_is_not_undoable(root: Path) -> None:
    assert preview_unreconcile(Workspace.open(root), A, B) == NotUndoable(
        "not_reconciled"
    )


def test_an_altered_unreconcile_version_is_stale_and_writes_nothing(
    root: Path,
) -> None:
    _settle(root, Action.RECONCILE)
    preview = _unreconcile_preview(root)
    altered = dataclasses.replace(preview.version, content_digest="0" * 64)
    before = support.snapshot(root)

    assert apply_unreconcile(Workspace.open(root), altered) == UndoStale()
    assert support.snapshot(root) == before


def test_a_confidential_page_reports_its_unreconcile_commit(root: Path) -> None:
    _settle(root, Action.RECONCILE)
    page = root / "bundle" / f"{A}.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "---\n", "---\nsensitivity: confidential\n", 1
        ),
        "utf-8",
    )
    _git(root, "commit", "-qam", "mark confidential")

    undone = apply_unreconcile(Workspace.open(root), _unreconcile_preview(root).version)

    assert isinstance(undone, Unreconciled)
    assert undone.committed_confidential is True


def test_an_unreconcile_on_a_held_lock_raises_busy(root: Path) -> None:
    _settle(root, Action.RECONCILE)
    preview = _unreconcile_preview(root)
    before = support.snapshot(root)

    with lock.workspace_lock(root), pytest.raises(WorkspaceBusyError):
        apply_unreconcile(Workspace.open(root), preview.version)

    assert support.snapshot(root) == before
