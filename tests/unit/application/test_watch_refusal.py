"""`watch_refusal` queue rows (MVP 4 unit 7.3, issue #1142, ADR-0038).

A watched file whose bytes changed after import is refused into the pending-work
queue once, never re-imported and never an error on every save. Every test drives
the real watch job over a real workspace and reads the rows back from
`findings.db`, asserting the carried digest, status and text rather than a count.
"""

import contextlib
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from openkos import config
from openkos.application import budget, pending_queue_report, watch
from openkos.application import ingest_service as svc
from openkos.state import pending_queue as pq
from tests.unit.application.curation_support import make_workspace
from tests.unit.application.test_watch import _Env
from tests.unit.cli.conftest import pinned_git_identity as pinned_git_identity

_EDITED = "A different save.\n"
_EDITED_AGAIN = "Yet another save, longer than before.\n"


@pytest.fixture
def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pinned_git_identity: None
) -> _Env:
    root = make_workspace(tmp_path, monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    return _Env(root, inbox)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows(env: _Env, kind: str = "watch_refusal") -> list[pq.PendingItem]:
    conn = sqlite3.connect(config.WorkspaceLayout(env.root).findings_db_path)
    try:
        return [i for i in pq.all_items(conn) if i.kind == kind]
    finally:
        conn.close()


def _input_digests(env: _Env, item: pq.PendingItem) -> list[tuple[str, str]]:
    conn = sqlite3.connect(config.WorkspaceLayout(env.root).findings_db_path)
    try:
        return [
            (r[0], r[1])
            for r in conn.execute(
                "SELECT input_ref, digest FROM pending_item_input_digests"
                " WHERE item_id = ? ORDER BY ordinal",
                (item.id,),
            )
        ]
    finally:
        conn.close()


def _import_then_edit(env: _Env, name: str = "a.md", text: str = _EDITED) -> Path:
    """Import `name`, then save different bytes over it and let that settle."""
    path = env.drop(name)
    env.job()
    env.settle()
    env.job()
    assert env.raw() == [name]
    path.write_text(text, encoding="utf-8")
    env.job()  # the new stat starts its clock
    env.settle()
    return path


# --- one row per source ----------------------------------------------------------


def test_repeated_saves_produce_one_row_and_no_ingest(env: _Env) -> None:
    path = _import_then_edit(env)
    calls = env.model.calls

    env.job()
    env.settle()
    assert env.job() is None
    path.write_text(_EDITED, encoding="utf-8")  # a re-save with the same bytes
    env.job()
    env.settle()
    env.job()

    rows = _rows(env)
    assert [(r.status, r.targets) for r in rows] == [("pending", ("sources/a",))]
    assert _input_digests(env, rows[0]) == [("a.md", _sha(_EDITED))]
    assert env.model.calls == calls  # no ingest, so no model call
    assert (env.root / "raw" / "a.md").read_text(encoding="utf-8") != _EDITED


def test_the_row_text_names_the_inbox_path_the_reason_and_the_remedy(
    env: _Env,
) -> None:
    _import_then_edit(env)
    env.job()

    (row,) = _rows(env)

    payload = json.loads(row.payload)
    assert payload["reason"] == "source changed after import"
    assert payload["inbox_path"] == "a.md"
    assert "rename the file in the inbox" in payload["remedy"]
    assert "under a different name" in payload["remedy"]
    assert row.producer == "watch/1"


def test_a_further_edit_retires_the_row_as_stale_and_opens_a_new_one(
    env: _Env,
) -> None:
    path = _import_then_edit(env)
    env.job()
    path.write_text(_EDITED_AGAIN, encoding="utf-8")
    env.job()
    env.settle()

    env.job()

    old, new = sorted(_rows(env), key=lambda r: r.id)
    assert (old.status, old.resolution) == ("stale", "stale")
    assert new.status == "pending"
    assert _input_digests(env, old) == [("a.md", _sha(_EDITED))]
    assert _input_digests(env, new) == [("a.md", _sha(_EDITED_AGAIN))]


# --- retiring a row -------------------------------------------------------------


def test_removing_the_file_from_the_inbox_retires_the_row_as_stale(env: _Env) -> None:
    path = _import_then_edit(env)
    env.job()
    path.unlink()

    env.job()  # the removal is first seen: a rename could still follow
    env.time.advance(1)
    env.job()  # still inside the quiet window
    assert [r.status for r in _rows(env)] == ["pending"]
    env.settle()
    env.job()

    assert [(r.status, r.resolution) for r in _rows(env)] == [("stale", "stale")]


def test_restoring_the_imported_bytes_retires_the_row_as_stale(env: _Env) -> None:
    path = _import_then_edit(env)
    env.job()
    imported = (env.root / "raw" / "a.md").read_text(encoding="utf-8")
    path.write_text(imported, encoding="utf-8")
    env.job()
    env.settle()

    env.job()

    assert [(r.status, r.resolution) for r in _rows(env)] == [("stale", "stale")]
    assert env.raw() == ["a.md"]  # the raw copy is untouched


def test_a_file_that_reappears_unchanged_keeps_its_row(env: _Env) -> None:
    path = _import_then_edit(env)
    env.job()
    saved = path.read_bytes()
    stat = path.stat()
    path.unlink()
    env.job()
    path.write_bytes(saved)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    env.job()
    assert env.observation("a.md").outcome == watch.REFUSED  # restored, not forgotten
    env.settle()
    env.job()

    assert [r.status for r in _rows(env)] == ["pending"]


# --- applied when a raw copy with the refused bytes lands ---------------------------


def test_renaming_in_the_inbox_imports_and_applies_the_row(env: _Env) -> None:
    path = _import_then_edit(env)
    env.job()
    path.rename(env.inbox / "a-v2.md")
    env.job()
    env.settle()

    result = env.job()

    assert result is not None
    assert env.raw() == ["a-v2.md", "a.md"]
    assert (env.root / "raw" / "a-v2.md").read_text(encoding="utf-8") == _EDITED
    (row,) = _rows(env)
    assert (row.status, row.resolution) == ("applied", "as_proposed")


def test_an_ingest_by_hand_of_the_refused_bytes_applies_the_row(
    env: _Env, tmp_path: Path
) -> None:
    _import_then_edit(env)
    env.job()
    byhand = tmp_path / "byhand.md"
    byhand.write_text(_EDITED, encoding="utf-8")

    svc.ingest_source(
        env.root,
        byhand,
        svc.IngestPolicy(skip_confirmation=True),
        ports=env.ingest_ports(contextlib.nullcontext, _unbudgeted(env)),
        confirm=None,
    )

    (row,) = _rows(env)
    assert (row.status, row.resolution) == ("applied", "as_proposed")


def _unbudgeted(env: _Env) -> budget.BudgetedRun:
    return budget.start_budgeted_run(
        config.WorkspaceLayout(env.root), env.cfg(), env.time.now
    )


# --- an over-budget source ------------------------------------------------------------


def test_a_source_that_never_fits_the_budget_gets_a_row_with_that_reason(
    env: _Env,
) -> None:
    env.drop("big.md")
    env.job(max_calls_per_pass=1)
    env.settle()

    env.job(max_calls_per_pass=1)

    (row,) = _rows(env)
    assert row.status == "pending"
    assert json.loads(row.payload)["reason"] == "exceeds per-pass budget"
    assert json.loads(row.payload)["inbox_path"] == "big.md"
    assert row.targets == ("sources/big",)
    assert env.observation("big.md").outcome == watch.EXCEEDS_BUDGET


def test_an_over_budget_row_goes_stale_when_the_file_is_removed(env: _Env) -> None:
    path = env.drop("big.md")
    env.job(max_calls_per_pass=1)
    env.settle()
    env.job(max_calls_per_pass=1)
    path.unlink()

    env.job(max_calls_per_pass=1)
    env.settle()
    env.job(max_calls_per_pass=1)

    assert [(r.status, r.resolution) for r in _rows(env)] == [("stale", "stale")]


def test_an_over_budget_file_that_later_fits_retires_its_row(env: _Env) -> None:
    path = env.drop("big.md")
    env.job(max_calls_per_pass=1)
    env.settle()
    env.job(max_calls_per_pass=1)
    path.write_text("Smaller now, and edited.\n", encoding="utf-8")
    env.job()
    env.settle()

    env.job()  # the default budget admits it

    assert env.raw() == ["big.md"]
    assert [(r.status, r.resolution) for r in _rows(env)] == [("stale", "stale")]


# --- what the person sees ---------------------------------------------------------------


def test_pending_lists_the_row_with_its_resolving_command(env: _Env) -> None:
    _import_then_edit(env)
    env.job()

    report = pending_queue_report.read_report(config.WorkspaceLayout(env.root))
    lines = pending_queue_report.render_lines(report)

    assert "watch_refusal (1)" in lines
    assert "  - sources/a [pending]" in lines
    # No `unattended.inbox` in this workspace's config, so the path stays as the
    # watch recorded it (relative to the inbox) and is never a placeholder.
    assert "    resolve: openkos ingest a.md" in lines


def test_a_watch_with_no_refusal_creates_no_queue(env: _Env) -> None:
    env.drop("a.md")
    env.job()
    env.settle()
    env.job()

    assert not config.WorkspaceLayout(env.root).findings_db_path.exists()


def test_restoring_the_imported_bytes_costs_no_model_call(env: _Env) -> None:
    path = _import_then_edit(env)
    env.job()  # the refusal row is open
    imported = (env.root / "raw" / "a.md").read_text(encoding="utf-8")
    path.write_text(imported, encoding="utf-8")
    env.job()
    env.settle()
    assert env.model.calls > 0  # the first import did call the model
    calls, commits = env.model.calls, len(env.commits)

    result = env.job()

    assert env.model.calls == calls  # converged: no extraction on the restore
    assert len(env.commits) == commits  # and nothing written or committed
    assert result is not None
    assert (result.chat_calls, result.units_done) == (0, 1)
    assert [(r.status, r.resolution) for r in _rows(env)] == [("stale", "stale")]
