"""Direct unit tests for `openkos.application.consistency` (mcp-read-surface
slice 5, design Decision 11): the consistency-warnings service every MCP
tool's result composes through `mcp/gate.py`. Mirrors `test_list_service.py`'s
posture -- no MCP server, no CLI, exercised directly against a real
(tmp-path) workspace.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openkos import config, read_outcome
from openkos.application import consistency
from openkos.bundle import ledger as bundle_ledger


def _workspace(tmp_path: Path) -> config.WorkspaceLayout:
    config.write_config(tmp_path)
    layout = config.WorkspaceLayout(tmp_path)
    layout.bundle_dir.mkdir(parents=True, exist_ok=True)
    return layout


def test_read_consistency_in_flight_and_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending merge-ledger marker gives `in_flight_writes == 1`; a marker
    whose scan raises gives `in_flight_writes is None` and a
    `NotRun("in_flight_write", str(exc))`; `stale_reads=("fts",)` with a
    stale FTS store lists `"fts"` in `stale_stores`; `stale_reads=()` (the
    default) never touches staleness at all; the staleness check itself
    never raises and never produces a `NotRun` (it degrades to `()`)."""
    layout = _workspace(tmp_path)

    # No pending marker at all: a clean workspace reports 0, no NotRun.
    clean = consistency.read_consistency(layout)
    assert clean.in_flight_writes == 0
    assert clean.not_run == ()
    assert clean.stale_stores == ()

    # A pending marker: in_flight_writes counts it.
    monkeypatch.setattr(
        bundle_ledger,
        "scan_torn_writes",
        lambda bundle_dir: [(bundle_dir / "whatever.pending", "roll-forward")],
    )
    one_pending = consistency.read_consistency(layout)
    assert one_pending.in_flight_writes == 1
    assert one_pending.not_run == ()

    # The scan itself raises: in_flight_writes degrades to None plus a
    # NotRun labelled "in_flight_write", never letting the exception escape.
    def _raising_scan(bundle_dir: Path) -> list[object]:
        raise OSError("cannot read ledger marker at sources/zq-canary-src-7f3a")

    monkeypatch.setattr(bundle_ledger, "scan_torn_writes", _raising_scan)
    raised = consistency.read_consistency(layout)
    assert raised.in_flight_writes is None
    assert len(raised.not_run) == 1
    entry = raised.not_run[0]
    assert isinstance(entry, read_outcome.NotRun)
    assert entry.label == "in_flight_write"
    assert "ledger marker" in entry.reason

    # Restore the real (unpatched) scan for the staleness assertions below.
    monkeypatch.undo()

    # stale_reads=() (the default): staleness is never even touched.
    default_reads = consistency.read_consistency(layout)
    assert default_reads.stale_stores == ()

    # stale_reads=("fts",) with a stale FTS store lists "fts".
    from openkos.application import status as application_status

    monkeypatch.setattr(
        application_status,
        "stale_derived_stores",
        lambda bundle_dir, stores, expected_schema=None: ("fts",),
    )
    stale = consistency.read_consistency(layout, stale_reads=("fts",))
    assert stale.stale_stores == ("fts",)
    assert stale.not_run == ()

    # The staleness check itself never raises and never produces a NotRun.
    def _raising_stale(
        bundle_dir: Path, stores: object, expected_schema: object = None
    ) -> tuple[str, ...]:
        raise RuntimeError("boom")

    monkeypatch.setattr(application_status, "stale_derived_stores", _raising_stale)
    degraded = consistency.read_consistency(layout, stale_reads=("fts",))
    assert degraded.stale_stores == ()
    assert degraded.not_run == ()
