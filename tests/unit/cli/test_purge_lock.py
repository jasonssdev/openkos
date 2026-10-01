"""`purge` holds the workspace lock for its whole run (#1137, ADR-0036; tasks.md
1.8; workspace-lock: "`purge` Holds The Lock For Its Whole Run").

`purge` is the one locked verb that is NOT split into a compute phase and a
commit phase: it rewrites history and removes derived stores, so nothing may
interleave with any step of it. These tests assert that, rather than leaving it
implied by the absence of a split:

- it is classified as a whole-verb lock, not a commit-phase verb;
- a concurrent commit phase (the section every split verb enters to write) is
  refused for as long as the purge runs, from its first read to its point of no
  return;
- a findings writer whose compute straddled a purge does not write into the
  purged state when its own commit phase arrives.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config as config_module
from openkos import lock
from openkos.application import lifecycle as application_lifecycle
from openkos.application import lock_wait
from openkos.cli.main import _READ_ONLY_COMMANDS, _SELF_LOCKING_COMMANDS, app
from openkos.graph.base import Edge
from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch
from openkos.state import derived
from openkos.state import edge_suggestions as edge_suggestions_store
from openkos.vcs import git as vcs_git
from tests.unit.cli.commit_phase_support import init_workspace, write_doc
from tests.unit.cli.conftest import commit_pending_fixture_docs
from tests.unit.vcs.conftest import TmpGitRepo, isolate_git_identity, tmp_git_repo

__all__ = ["tmp_git_repo"]

runner = CliRunner()


def _purge_callback() -> object:
    for info in app.registered_commands:
        if info.callback is not None and info.callback.__name__ == "purge":
            return info.callback
    raise AssertionError("purge is not registered")


def test_purge_is_a_whole_verb_lock_not_a_commit_phase_verb() -> None:
    callback = _purge_callback()
    attributes = vars(callback)

    assert attributes["__openkos_locked_command__"] == "purge"
    assert attributes["__openkos_commit_phase__"] is False
    assert "purge" not in _READ_ONLY_COMMANDS
    assert "purge" not in _SELF_LOCKING_COMMANDS


def _concurrent_commit_phase_is_refused(root: Path) -> bool:
    """Whether the section every split verb enters to write refuses right now.

    This is exactly what another process's commit phase takes
    (`lock_wait.locked_commit_section`), with no wait, so a refusal is
    immediate and a grant would have written."""
    try:
        with lock_wait.locked_commit_section(root, wait_seconds=0)():
            return False
    except lock.WorkspaceBusyError:
        return True


def test_no_commit_phase_interleaves_with_a_running_purge(
    tmp_git_repo: TmpGitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_git_repo.root
    monkeypatch.chdir(root)
    refused_at: dict[str, bool] = {}

    real_resolve = application_lifecycle.resolve_concept_path
    real_expunge = vcs_git.expunge_paths

    def _first_read(*args: object, **kwargs: object) -> object:
        refused_at["first read"] = _concurrent_commit_phase_is_refused(root)
        return real_resolve(*args, **kwargs)  # type: ignore[arg-type]

    def _point_of_no_return(*args: object, **kwargs: object) -> None:
        refused_at["history rewrite"] = _concurrent_commit_phase_is_refused(root)
        real_expunge(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(application_lifecycle, "resolve_concept_path", _first_read)
    monkeypatch.setattr(vcs_git, "expunge_paths", _point_of_no_return)

    result = runner.invoke(
        app,
        [
            "purge",
            tmp_git_repo.source_id,
            "--confirm-phrase",
            f"purge {tmp_git_repo.source_id}",
        ],
    )

    assert result.exit_code == 0, result.output
    assert refused_at == {"first read": True, "history rewrite": True}
    # The lock is the purge's alone: once it exited, a commit phase is granted.
    assert _concurrent_commit_phase_is_refused(root) is False


@pytest.fixture
def _git_identity(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_git_identity(
        monkeypatch,
        tmp_path_factory.mktemp("git-identity-config"),
        name="Isolated Tester",
        email="tester@example.invalid",
    )


def _stored_suggestions(
    root: Path,
) -> tuple[edge_suggestions_store.PersistedEdgeSuggestion, ...]:
    findings_db = config_module.WorkspaceLayout(root).findings_db_path
    if not findings_db.exists():
        return ()
    conn = derived.open_derived_connection(findings_db)
    try:
        return edge_suggestions_store.open_edge_suggestions(conn)
    finally:
        conn.close()


@pytest.mark.usefixtures("_git_identity")
def test_a_findings_writer_does_not_write_into_state_a_purge_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suggest-relations` computes (the edge-typing model call) with no lock, so
    a `purge` of one endpoint can run in the middle of it. The writer's commit
    phase then finds that endpoint gone: it must persist nothing about it, and
    not resurrect the `findings.db` the purge deleted."""
    init_workspace(tmp_path, monkeypatch)
    write_doc(tmp_path, "concepts/a", {"type": "Concept", "title": "Alpha"})
    write_doc(tmp_path, "concepts/b", {"type": "Concept", "title": "Beta"})
    commit_pending_fixture_docs()
    edge = Edge(source_id="concepts/a", target_id="concepts/b")
    monkeypatch.setattr(
        "openkos.cli.main.candidate_edges", lambda bundle_dir, **kwargs: [edge]
    )
    purged: list[int] = []

    def _type_while_a_purge_runs(
        edges: list[Edge], **kwargs: object
    ) -> EdgeSuggestionBatch:
        outcome = CliRunner().invoke(
            app, ["purge", "concepts/a", "--confirm-phrase", "purge concepts/a"]
        )
        purged.append(outcome.exit_code)
        assert outcome.exit_code == 0, outcome.output
        return EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=edge,
                    suggested_type="references",
                    rationale="stub rationale",
                    corrected_edge=None,
                )
            ]
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _type_while_a_purge_runs)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert purged == [0], "the purge did not run during the compute phase"
    assert not (tmp_path / "bundle" / "concepts" / "a.md").exists()
    assert result.exit_code == 0, result.stderr
    assert _stored_suggestions(tmp_path) == ()
