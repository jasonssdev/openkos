"""Consistency warnings every MCP tool composes into its result (design
Decision 11): whether a torn (in-flight) write was observed, and -- only
when a caller declares it depends on a particular derived store -- which
stores are stale. This module never raises: a failing consistency check
must not be what breaks the tool call it advises (mirrors
`application.status.stale_index_names`'s own "never raises" contract).

`mcp/gate.py` is the only caller that turns a `Consistency` into wire-shape
`warnings`/`not_run` entries (design Decision 3): this module reports raw
facts and raw `read_outcome.NotRun` entries, and decides nothing about what
crosses the disclosure boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from openkos import config, read_outcome
from openkos.application import status as application_status
from openkos.bundle import ledger as bundle_ledger


@dataclass(frozen=True)
class Consistency:
    """One tool call's consistency snapshot.

    `in_flight_writes` is `None` when the torn-write scan itself could not
    run (an exception, folded into `not_run`) -- never `0` for a check that
    silently failed. `stale_stores` is always `()` when `stale_reads` was
    empty, and never raises even when it was not."""

    in_flight_writes: int | None
    stale_stores: tuple[str, ...]
    not_run: tuple[read_outcome.NotRun, ...]


def read_consistency(
    layout: config.WorkspaceLayout, *, stale_reads: tuple[str, ...] = ()
) -> Consistency:
    """Read the in-flight-write count and (when `stale_reads` names at
    least one store) the stale-store list for `layout`.

    `bundle_ledger.scan_torn_writes` reads and parses every pending marker
    and CAN raise; that failure becomes `in_flight_writes=None` plus a
    `NotRun("in_flight_write", ...)` rather than propagating (design
    Decision 11). `application_status.stale_index_names` already never
    raises on its own (it degrades to `()`), so no `NotRun` is ever produced
    for staleness -- `stale_index` can appear only as a warning, never as a
    `not_run` entry.
    """
    not_run: list[read_outcome.NotRun] = []
    in_flight_writes: int | None
    try:
        in_flight_writes = len(bundle_ledger.scan_torn_writes(layout.bundle_dir))
    except Exception as exc:  # noqa: BLE001 -- a marker read/parse failure, never propagated
        in_flight_writes = None
        not_run.append(read_outcome.NotRun(label="in_flight_write", reason=str(exc)))

    stale_stores = application_status.stale_index_names(layout, reads=stale_reads)

    return Consistency(
        in_flight_writes=in_flight_writes,
        stale_stores=stale_stores,
        not_run=tuple(not_run),
    )
