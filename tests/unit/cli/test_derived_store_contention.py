"""Derived-store lock contention is mapped to one refusal, in one place.

`reindex` used to be the only verb that translated a SQLite "database is
locked" into a retry message; every other writer of `.openkos/` let the raw
`OperationalError` escape as a traceback. `_guard_workspace_lock` now owns the
mapping for every locked verb, so these tests drive it with a REAL second
connection holding the write lock past a tiny `busy_timeout` rather than a
hand-built exception.
"""

import sqlite3
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from openkos.cli.main import _guard_workspace_lock, app
from openkos.state import derived, findings
from tests.unit.conftest import make_non_lock_operational_error

runner = CliRunner()


def _probe_app(body: object) -> typer.Typer:
    probe = typer.Typer()

    @probe.command("probe")
    @_guard_workspace_lock("probe")
    def _probe() -> None:
        body()  # type: ignore[operator]

    return probe


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def test_a_findings_write_blocked_past_busy_timeout_is_a_refusal_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second connection holds `BEGIN IMMEDIATE` on findings.db; the verb's
    findings write waits out `busy_timeout` and must exit 1 with the shared
    retry wording, naming the verb the user ran."""
    _init(tmp_path, monkeypatch)
    db = tmp_path / ".openkos" / "findings.db"
    derived.open_derived_connection(db).close()
    monkeypatch.setattr(derived, "_BUSY_TIMEOUT_MS", 1)
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")

    def _write() -> None:
        conn = derived.open_derived_connection(db)
        try:
            findings.record_findings(
                conn,
                [
                    findings.Finding(
                        pair_ids=("a", "b"),
                        merged_absorbed_id=None,
                        verdict="consistent",
                        confidence=0.5,
                        rationale="r",
                        input_digests=(),
                    )
                ],
            )
        finally:
            conn.close()

    try:
        result = runner.invoke(_probe_app(_write), [])
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos probe: failed -- another OpenKOS process is "
    )
    assert "try again" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_non_lock_operational_error_is_not_mislabelled_as_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only SQLITE_BUSY/SQLITE_LOCKED map to the refusal; any other
    operational failure keeps propagating, carried unchanged."""
    _init(tmp_path, monkeypatch)
    sentinel = make_non_lock_operational_error()

    def _boom() -> None:
        raise sentinel

    result = runner.invoke(_probe_app(_boom), [])

    assert result.exception is sentinel
    assert "another process" not in result.stderr
