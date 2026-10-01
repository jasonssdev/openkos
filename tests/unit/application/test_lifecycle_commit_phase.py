"""The commit phase of `merge` and `unmerge` (ADR-0036, issue #1137).

Both services compute their plan and ask their question with NO workspace
lock, then enter the `commit_section` port only for the drift check, the write
burst and the auto-commit. These tests drive the phases directly: a confirm
callback or a reconciliation port is the window between compute and commit, so
mutating the bundle from inside it is exactly a concurrent writer landing
there.

Real locks are used (`lock_wait.locked_commit_section`), not a stand-in: the
property under test is that the lock is NOT held while a prompt or a model call
runs, and that it IS held for the commit.
"""

import contextlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest

from openkos import lock
from openkos.application import lock_wait
from openkos.application import merge_service as merge_svc
from openkos.application import unmerge_service as unmerge_svc
from openkos.application.lifecycle import PreparedMerge
from openkos.application.write_gate import ConfirmationAnswer, DriftDetected
from tests.unit.application.curation_support import (
    CommitRecorder,
    make_workspace,
    tree,
    write_concept,
)

_SURVIVOR = "concepts/survivor"
_ABSORBED = "concepts/absorbed"
_BYSTANDER = "concepts/bystander"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_workspace(tmp_path, monkeypatch)
    write_concept(root, _SURVIVOR, title="Survivor", body="Survivor body.")
    write_concept(root, _ABSORBED, title="Absorbed", body="Absorbed body.")
    write_concept(root, _BYSTANDER, title="Bystander", body="Unrelated body.")
    return root


def _lock_is_free(root: Path) -> bool:
    """True iff the workspace lock can be taken right now (and is released)."""
    try:
        with lock.workspace_lock(root):
            return True
    except lock.WorkspaceBusyError:
        return False


class _Spy:
    """A real locking `CommitSection` that records when it is entered and left,
    plus a record of every effect sequenced around it."""

    def __init__(self, root: Path) -> None:
        self.events: list[str] = []
        self._inner = lock_wait.locked_commit_section(root, wait_seconds=0)

    @contextlib.contextmanager
    def section(self) -> Iterator[None]:
        with self._inner():
            self.events.append("enter")
            yield
            self.events.append("exit")


def _merge_ports(
    root: Path,
    spy: _Spy,
    commits: CommitRecorder,
    *,
    reconcile: Callable[[], None] | None = None,
    after_commit: Callable[[], None] | None = None,
    check_unlocked: bool = True,
) -> merge_svc.MergePorts:
    def _reconcile(
        r: Path, prepared: PreparedMerge, policy: merge_svc.MergePolicy
    ) -> PreparedMerge:
        spy.events.append("reconcile")
        if check_unlocked:
            assert _lock_is_free(root), "the reconciliation model call holds the lock"
        if reconcile is not None:
            reconcile()
        return prepared

    def _autocommit(r: Path, paths: Sequence[str], message: str) -> str | None:
        spy.events.append("autocommit")
        return commits(r, paths, message)

    def _after() -> None:
        spy.events.append("after_commit")
        if after_commit is not None:
            after_commit()

    return merge_svc.MergePorts(
        autocommit=_autocommit,
        has_reset_point=lambda r: True,
        apply_reconciliation=_reconcile,
        commit_section=spy.section,
        after_commit=_after,
    )


def _merge(
    root: Path,
    ports: merge_svc.MergePorts,
    *,
    confirm: Callable[[str], ConfirmationAnswer] | None = None,
    auto: bool = True,
) -> merge_svc.MergeOutcome:
    return merge_svc.merge_concepts(
        root,
        _SURVIVOR,
        _ABSORBED,
        merge_svc.MergePolicy(auto=auto),
        ports=ports,
        confirm=confirm,
    )


# --- merge: the prompt does not hold the lock ------------------------------


def test_merge_prompt_and_reconciliation_run_outside_the_lock(
    workspace: Path,
) -> None:
    spy = _Spy(workspace)

    def _ask(prompt: str) -> ConfirmationAnswer:
        spy.events.append("confirm")
        assert _lock_is_free(workspace), "the confirmation prompt holds the lock"
        return "proceed"

    _merge(
        workspace,
        _merge_ports(workspace, spy, CommitRecorder()),
        confirm=_ask,
        auto=False,
    )

    # The question and the model call come first, then ONE commit section that
    # wraps the write, the auto-commit and the derived refresh.
    assert spy.events == [
        "confirm",
        "reconcile",
        "enter",
        "autocommit",
        "after_commit",
        "exit",
    ]
    assert _lock_is_free(workspace), "the lock is released when the commit ends"


def test_merge_commit_holds_the_lock_while_it_writes(workspace: Path) -> None:
    spy = _Spy(workspace)
    seen: list[bool] = []

    def _during_autocommit() -> None:
        seen.append(_lock_is_free(workspace))

    ports = _merge_ports(
        workspace, spy, CommitRecorder(), after_commit=_during_autocommit
    )

    _merge(workspace, ports)

    assert seen == [False], "the derived refresh must run under the commit lock"


def test_merge_with_a_busy_lock_refuses_after_compute_and_writes_nothing(
    workspace: Path,
) -> None:
    spy = _Spy(workspace)
    before = tree(workspace)

    with lock.workspace_lock(workspace), pytest.raises(lock.WorkspaceBusyError):
        _merge(
            workspace,
            _merge_ports(workspace, spy, CommitRecorder(), check_unlocked=False),
        )

    # Compute ran (the model call may have spent), the commit never did.
    assert "reconcile" in spy.events
    assert "autocommit" not in spy.events
    assert tree(workspace) == before


# --- merge: read-dependency sentinels --------------------------------------


def test_merge_refuses_when_a_bystander_starts_linking_the_absorbed_concept(
    workspace: Path,
) -> None:
    """The scan that decided "no inbound reference" read the bystander. A link
    to the absorbed concept that lands before the commit would be left
    dangling, so the commit refuses (exit 3 at the CLI) and writes nothing."""
    bystander = workspace / "bundle" / f"{_BYSTANDER}.md"
    survivor = workspace / "bundle" / f"{_SURVIVOR}.md"
    absorbed = workspace / "bundle" / f"{_ABSORBED}.md"
    survivor_before = survivor.read_bytes()
    spy = _Spy(workspace)
    commits = CommitRecorder()

    def _bystander_gains_a_link() -> None:
        bystander.write_text(
            bystander.read_text(encoding="utf-8")
            + f"\nSee [absorbed](/{_ABSORBED}.md).\n",
            encoding="utf-8",
        )

    ports = _merge_ports(workspace, spy, commits, reconcile=_bystander_gains_a_link)

    with pytest.raises(DriftDetected) as caught:
        _merge(workspace, ports)

    assert "1 read dependency(ies) changed on disk" in caught.value.message
    assert f"{_BYSTANDER}.md" in caught.value.message
    assert absorbed.exists(), "the absorbed concept is never deleted"
    assert survivor.read_bytes() == survivor_before
    assert commits.calls == []


def test_merge_refuses_when_a_new_document_appears_before_the_commit(
    workspace: Path,
) -> None:
    spy = _Spy(workspace)
    commits = CommitRecorder()

    def _new_document() -> None:
        # Only the file, no catalog entry: the catalog drift guard would
        # otherwise refuse first and hide the read-dependency guard.
        (workspace / "bundle" / "concepts" / "latecomer.md").write_text(
            "---\ntype: Concept\ntitle: Latecomer\n---\n\nBody.\n", encoding="utf-8"
        )

    ports = _merge_ports(workspace, spy, commits, reconcile=_new_document)

    with pytest.raises(DriftDetected) as caught:
        _merge(workspace, ports)

    assert "1 new document(s) appeared: concepts/latecomer.md" in caught.value.message
    assert (workspace / "bundle" / f"{_ABSORBED}.md").exists()
    assert commits.calls == []


def test_merge_never_computes_the_high_water_mark_from_a_stale_absorbed(
    workspace: Path,
) -> None:
    """Sentinel: the merged survivor's sensitivity is the high-water mark of
    its two inputs. Raising the absorbed concept to confidential between the
    phases must not let a plan built from the stale `private` value land."""
    absorbed = workspace / "bundle" / f"{_ABSORBED}.md"
    survivor = workspace / "bundle" / f"{_SURVIVOR}.md"
    survivor_before = survivor.read_bytes()
    spy = _Spy(workspace)

    def _raise_absorbed() -> None:
        text = absorbed.read_text(encoding="utf-8")
        absorbed.write_text(
            text.replace("type: Concept", "type: Concept\nsensitivity: confidential"),
            encoding="utf-8",
        )

    ports = _merge_ports(workspace, spy, CommitRecorder(), reconcile=_raise_absorbed)

    with pytest.raises(DriftDetected):
        _merge(workspace, ports)

    assert "sensitivity: confidential" in absorbed.read_text(encoding="utf-8")
    assert survivor.read_bytes() == survivor_before
    assert "confidential" not in survivor.read_text(encoding="utf-8")


# --- unmerge ----------------------------------------------------------------


def _merged_workspace(workspace: Path) -> None:
    """Perform a real merge so `unmerge` has a ledger entry to reverse."""
    spy = _Spy(workspace)
    _merge(workspace, _merge_ports(workspace, spy, CommitRecorder()))
    assert not (workspace / "bundle" / f"{_ABSORBED}.md").exists()


def _unmerge_ports(
    spy: _Spy,
    commits: CommitRecorder,
    *,
    after_commit: Callable[[], None] | None = None,
) -> unmerge_svc.UnmergePorts:
    def _autocommit(r: Path, paths: Sequence[str], message: str) -> str | None:
        spy.events.append("autocommit")
        return commits(r, paths, message)

    def _after() -> None:
        spy.events.append("after_commit")
        if after_commit is not None:
            after_commit()

    return unmerge_svc.UnmergePorts(
        autocommit=_autocommit, commit_section=spy.section, after_commit=_after
    )


def test_unmerge_prompt_runs_outside_the_lock(workspace: Path) -> None:
    _merged_workspace(workspace)
    spy = _Spy(workspace)

    def _ask(prompt: str) -> ConfirmationAnswer:
        spy.events.append("confirm")
        assert _lock_is_free(workspace), "the confirmation prompt holds the lock"
        return "proceed"

    unmerge_svc.unmerge_concept(
        workspace,
        _SURVIVOR,
        _ABSORBED,
        unmerge_svc.UnmergePolicy(auto=False),
        ports=_unmerge_ports(spy, CommitRecorder()),
        confirm=_ask,
    )

    assert spy.events == ["confirm", "enter", "autocommit", "after_commit", "exit"]
    assert (workspace / "bundle" / f"{_ABSORBED}.md").exists()
    assert _lock_is_free(workspace)


def test_unmerge_refuses_when_a_file_appears_at_the_restore_path(
    workspace: Path,
) -> None:
    """The restore writes the absorbed concept at its original path; a file
    that appears there between the phases would be overwritten in full."""
    _merged_workspace(workspace)
    spy = _Spy(workspace)
    squatter = workspace / "bundle" / f"{_ABSORBED}.md"

    def _squat(prompt: str) -> ConfirmationAnswer:
        squatter.write_text(
            "---\ntype: Concept\ntitle: Squatter\n---\n\nNew work.\n",
            encoding="utf-8",
        )
        return "proceed"

    commits = CommitRecorder()
    with pytest.raises(DriftDetected) as caught:
        unmerge_svc.unmerge_concept(
            workspace,
            _SURVIVOR,
            _ABSORBED,
            unmerge_svc.UnmergePolicy(auto=False),
            ports=_unmerge_ports(spy, commits),
            confirm=_squat,
        )

    assert f"{_ABSORBED}.md" in caught.value.message
    assert "New work." in squatter.read_text(encoding="utf-8")
    assert commits.calls == []


def test_unmerge_refuses_when_a_bystander_changes_between_the_phases(
    workspace: Path,
) -> None:
    _merged_workspace(workspace)
    spy = _Spy(workspace)
    bystander = workspace / "bundle" / f"{_BYSTANDER}.md"

    def _edit(prompt: str) -> ConfirmationAnswer:
        bystander.write_text(
            bystander.read_text(encoding="utf-8")
            + "\nrelations:\n- target: x\n  type: supersedes\n",
            encoding="utf-8",
        )
        return "proceed"

    commits = CommitRecorder()
    with pytest.raises(DriftDetected) as caught:
        unmerge_svc.unmerge_concept(
            workspace,
            _SURVIVOR,
            _ABSORBED,
            unmerge_svc.UnmergePolicy(auto=False),
            ports=_unmerge_ports(spy, commits),
            confirm=_edit,
        )

    assert f"{_BYSTANDER}.md" in caught.value.message
    assert not (workspace / "bundle" / f"{_ABSORBED}.md").exists()
    assert commits.calls == []


def test_unmerge_with_a_busy_lock_refuses_and_writes_nothing(workspace: Path) -> None:
    _merged_workspace(workspace)
    spy = _Spy(workspace)
    before = tree(workspace)

    with lock.workspace_lock(workspace), pytest.raises(lock.WorkspaceBusyError):
        unmerge_svc.unmerge_concept(
            workspace,
            _SURVIVOR,
            _ABSORBED,
            unmerge_svc.UnmergePolicy(auto=True),
            ports=_unmerge_ports(spy, CommitRecorder()),
        )

    assert "autocommit" not in spy.events
    assert tree(workspace) == before
