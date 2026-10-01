"""The commit phase of `suggest-relations --apply` (#1137, ADR-0036; tasks.md
1.6, second group).

The cost prompt, the edge-typing model calls and the per-item prompts run
without the workspace lock. Each accepted relation's prepare, drift check,
write and auto-commit, and the persist of the suggestions into `findings.db`,
are commit phases.
"""

import contextlib
from pathlib import Path

import pytest

from openkos import config as config_module
from openkos.application import lifecycle as application_lifecycle
from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch
from openkos.state import derived
from openkos.state import edge_suggestions as edge_suggestions_store
from tests.unit.cli.commit_phase_support import (
    hold_the_lock_from,
    init_workspace,
    lock_is_free,
    runner,
    simulate_tty,
    wrap,
    write_doc,
)
from tests.unit.cli.conftest import commit_pending_fixture_docs
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.vcs.conftest import isolate_git_identity


@pytest.fixture(autouse=True)
def _git_identity(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_git_identity(
        monkeypatch,
        tmp_path_factory.mktemp("git-identity-config"),
        name="Isolated Tester",
        email="tester@example.invalid",
    )


def _seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, on_type: object = None
) -> None:
    write_doc(tmp_path, "concepts/a", {"type": "Concept", "title": "Alpha"})
    write_doc(tmp_path, "concepts/b", {"type": "Concept", "title": "Beta"})
    commit_pending_fixture_docs()
    edge = Edge(source_id="concepts/a", target_id="concepts/b")
    monkeypatch.setattr(
        "openkos.cli.main.candidate_edges", lambda bundle_dir, **kwargs: [edge]
    )

    def _type(edges: list[Edge], **kwargs: object) -> EdgeSuggestionBatch:
        if callable(on_type):
            on_type()
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

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _type)


def _stored(
    tmp_path: Path,
) -> tuple[edge_suggestions_store.PersistedEdgeSuggestion, ...]:
    layout = config_module.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        return edge_suggestions_store.open_edge_suggestions(conn)
    finally:
        conn.close()


def test_costs_typing_and_prompts_do_not_hold_the_lock_but_the_writes_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    simulate_tty(monkeypatch)
    at_cost: list[bool] = []
    at_typing: list[bool] = []
    at_prompt: list[bool] = []
    at_persist: list[bool] = []
    at_write: list[bool] = []
    _seed(
        tmp_path, monkeypatch, on_type=lambda: at_typing.append(lock_is_free(tmp_path))
    )

    def _confirm(*args: object, **kwargs: object) -> bool:
        at_cost.append(lock_is_free(tmp_path))
        return True

    def _answer_yes(*args: object, **kwargs: object) -> str:
        at_prompt.append(lock_is_free(tmp_path))
        return "y"

    monkeypatch.setattr("typer.confirm", _confirm)
    monkeypatch.setattr("typer.prompt", _answer_yes)
    wrap(
        monkeypatch,
        edge_suggestions_store,
        "record_edge_suggestions",
        before=lambda: at_persist.append(lock_is_free(tmp_path)),
    )
    wrap(
        monkeypatch,
        application_lifecycle,
        "relate_core",
        before=lambda: at_write.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["suggest-relations", "--apply"])

    assert result.exit_code == 0, result.stderr
    assert at_cost == [True]
    assert at_typing == [True]
    assert at_prompt == [True]
    assert at_persist == [False]
    assert at_write == [False]
    assert "type: references" in (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )


def test_apply_refuses_with_exit_3_when_the_lock_is_busy_at_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    _seed(tmp_path, monkeypatch)
    before = _snapshot(tmp_path / "bundle")
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        take = hold_the_lock_from(stack, tmp_path, acquired)

        def _answer_yes_and_let_another_process_in(
            *args: object, **kwargs: object
        ) -> str:
            take()
            return "y"

        monkeypatch.setattr("typer.prompt", _answer_yes_and_let_another_process_in)

        result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"])

    assert acquired == [True]
    assert result.exit_code == 3
    assert "refusing to run" in result.stderr
    assert _snapshot(tmp_path / "bundle") == before


@pytest.mark.parametrize("edited_while_typing", [False, True])
def test_a_suggestion_is_persisted_only_for_the_content_it_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edited_while_typing: bool
) -> None:
    """The row's digests used to be read at persist time. With the typing call
    lock-free, an endpoint edited meanwhile would be stored under its NEW
    digest and served later as a fresh suggestion about content nobody typed."""
    init_workspace(tmp_path, monkeypatch)

    def _edit() -> None:
        if edited_while_typing:
            write_doc(
                tmp_path,
                "concepts/a",
                {"type": "Concept", "title": "Alpha"},
                "Rewritten while typing.\n",
            )

    _seed(tmp_path, monkeypatch, on_type=_edit)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0, result.stderr
    stored = (
        _stored(tmp_path) if (tmp_path / ".openkos" / "findings.db").exists() else ()
    )
    if edited_while_typing:
        assert stored == ()
    else:
        assert [(row.source_id, row.target_id) for row in stored] == [
            ("concepts/a", "concepts/b")
        ]
