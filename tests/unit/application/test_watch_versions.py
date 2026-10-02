"""A watched file edited after import becomes a new Source version (#1212,
#1224, ADR-0041).

The watch imports the new bytes as a new raw copy and Source, then enqueues the
`supersedes` relation as a `relation_type` pending-work row instead of writing
it: writing it deprecates the earlier Source, which ADR-0037 reserves for a
human-facing path. Every test drives the real watch job over a real workspace.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from openkos import config
from openkos.application import watch
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import make_workspace
from tests.unit.application.test_watch import _Env
from tests.unit.cli.conftest import pinned_git_identity as pinned_git_identity


@pytest.fixture
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> _Env:
    root = make_workspace(tmp_path, monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return _Env(root, inbox)


def _rows(env: _Env) -> list[pq.PendingItem]:
    path = config.WorkspaceLayout(env.root).findings_db_path
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        return pq.all_items(conn)
    finally:
        conn.close()


def _edit(env: _Env, path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    env.job()
    env.settle()
    env.job()


def _import(env: _Env, name: str = "a.md") -> Path:
    path = env.drop(name)
    env.job()
    env.settle()
    env.job()
    return path


def _source_text(env: _Env, concept: str) -> str:
    return (env.root / "bundle" / "sources" / f"{concept}.md").read_text(
        encoding="utf-8"
    )


def test_an_edit_keeps_both_versions_and_their_sources(env: _Env) -> None:
    path = _import(env)

    _edit(env, path, "An edited note.\n")

    assert env.raw() == ["a-2.md", "a.md"]
    assert "a-2.md" in _source_text(env, "a-2")
    assert "a.md" in _source_text(env, "a")


def test_the_earlier_source_is_not_deprecated_by_the_unattended_run(env: _Env) -> None:
    path = _import(env)
    before = _source_text(env, "a")

    _edit(env, path, "An edited note.\n")

    assert _source_text(env, "a") == before
    assert "supersedes" not in _source_text(env, "a-2")


def test_the_edit_is_queued_as_a_supersedes_relation_for_a_person(env: _Env) -> None:
    path = _import(env)

    _edit(env, path, "An edited note.\n")

    (row,) = _rows(env)
    assert (row.kind, row.status, row.producer) == (
        "relation_type",
        "pending",
        "source-supersession/1",
    )
    assert row.targets == ("sources/a-2", "sources/a")
    payload = json.loads(row.payload)
    assert payload["suggested_type"] == "supersedes"
    assert (payload["effective_source_id"], payload["effective_target_id"]) == (
        "sources/a-2",
        "sources/a",
    )


def test_a_second_edit_supersedes_the_first_edit_not_the_original(env: _Env) -> None:
    path = _import(env)
    _edit(env, path, "First edit.\n")

    _edit(env, path, "Second edit, a little longer.\n")

    assert env.raw() == ["a-2.md", "a-3.md", "a.md"]
    assert sorted(r.targets for r in _rows(env)) == [
        ("sources/a-2", "sources/a"),
        ("sources/a-3", "sources/a-2"),
    ]


def test_an_unchanged_save_after_an_edit_imports_nothing_more(env: _Env) -> None:
    path = _import(env)
    _edit(env, path, "An edited note.\n")
    calls = env.model.calls

    path.write_text("An edited note.\n", encoding="utf-8")  # same bytes, new stat
    env.job()
    env.settle()
    env.job()

    assert env.raw() == ["a-2.md", "a.md"]
    assert env.model.calls == calls
    assert len(_rows(env)) == 1


def test_restoring_the_first_bytes_imports_no_new_version(env: _Env) -> None:
    path = _import(env)
    first = path.read_text(encoding="utf-8")
    _edit(env, path, "An edited note.\n")
    calls = env.model.calls

    _edit(env, path, first)

    assert env.raw() == ["a-2.md", "a.md"]
    assert env.model.calls == calls


def test_an_edit_that_never_fits_the_budget_is_still_refused_into_the_queue(
    env: _Env,
) -> None:
    path = _import(env)
    path.write_text("An edited note.\n", encoding="utf-8")
    env.job(max_calls_per_pass=1)
    env.settle()

    env.job(max_calls_per_pass=1)

    assert env.raw() == ["a.md"]
    (row,) = _rows(env)
    assert row.kind == "watch_refusal"
    assert env.observation("a.md").outcome == watch.EXCEEDS_BUDGET


def test_relate_resolves_the_row_and_deprecates_the_earlier_source(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from openkos.cli.main import app

    path = _import(env)
    _edit(env, path, "An edited note.\n")
    monkeypatch.chdir(env.root)

    result = CliRunner().invoke(
        app, ["relate", "sources/a-2", "supersedes", "sources/a", "--auto"]
    )

    assert result.exit_code == 0, result.output
    assert "status: deprecated" in _source_text(env, "a")
    (row,) = _rows(env)
    assert (row.status, row.targets) == ("applied", ("sources/a-2", "sources/a"))
