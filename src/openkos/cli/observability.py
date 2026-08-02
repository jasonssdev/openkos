"""CLI-layer observability helpers for the sensitivity-aware fail-closed
filter (sensitivity-fail-closed-filter, MVP-3 gap #8 · S3 --
directory-walk-observability follow-up).

`sensitivity.py` stays a pure, no-I/O leaf (its own module invariant): this
module owns the CLI-visible STDERR signal that the directory walk backing
the fail-closed confidential-content filter was itself incomplete
(`okf._walk_errors`) -- a directory-scan error can make part of a bundle
unreadable, so the filter could not inspect every document and some
confidential material may not have been excluded.

Mirrors the existing `state/reindex.py:285` + `cli/main.py`'s
`report.prune_skipped` self-explaining-warning precedent, generalized to
one shared helper reused by all five sensitivity-filter verbs (`query`,
`contradictions`, `adjudicate`, `suggest-relations`, `suggest-volatility`)
instead of duplicating the STDERR message at five call sites.

Issue #190 widens this module's charter from "walk-incompleteness signal"
to "CLI-layer STDERR signals" generally: `progress_callback` builds the
TTY-gated per-item progress hook the library `on_progress` seams
(`adjudicate_candidates`, `suggest_volatility`, `find_contradictions`,
`state.reindex.reindex`) accept, and `stage_notice` is its single-call
sibling for verbs whose long wait is ONE LLM call (`ingest`, `query`).
Both are gated on `sys.stderr.isatty()`: a piped or redirected run stays
byte-clean, which is the entire NO_COLOR/non-TTY discipline here since no
color is ever emitted.
"""

import sys
from collections.abc import Callable
from pathlib import Path

import typer

from openkos.model import okf

_INCOMPLETE_WALK_WARNING = (
    "openkos: bundle scan was incomplete -- a directory-scan error made "
    "part of the bundle unreadable, so the confidential-content filter "
    "could not inspect every document and some confidential material may "
    "not have been excluded. Fix the directory permissions and re-run, or "
    "pass --include-confidential to bypass the filter deliberately."
)
"""Self-explaining STDERR message (mirrors `state/reindex.py`'s
`prune_skipped` notice style): names the condition, its consequence, and
both remediation paths, rather than a bare "walk incomplete" line."""


def warn_if_walk_incomplete(
    bundle_dir: Path, *, mode: str = "warn", include_confidential: bool = False
) -> None:
    """Warn to STDERR when the directory walk backing the sensitivity
    fail-closed filter over `bundle_dir` is provably incomplete
    (`okf._walk_errors` reports at least one unlistable subdirectory).

    Deliberately skipped when `include_confidential` is `True` -- the
    filter is then off entirely, so an incomplete walk has no bearing on
    what gets sent. `mode="warn"` (the only mode this slice implements)
    emits the self-explaining STDERR line and always returns normally: it
    NEVER raises and NEVER changes the caller's exit code, this helper is
    signal-only (spec: Incomplete walk warns and still exits 0).

    `mode="refuse"` raises `NotImplementedError`: a stable seam for a
    future cloud-egress mode that REFUSES instead of warning on this
    condition, explicitly out of scope for this change (spec). The
    signature is already shaped for that future mode so its slice needs no
    re-threading -- only filling this branch in and flipping call sites to
    `mode="refuse"`.
    """
    if include_confidential:
        return
    if mode == "refuse":
        raise NotImplementedError(
            "mode='refuse' is a stable seam for a future cloud-egress mode; "
            "not implemented in this slice"
        )
    if mode == "warn" and bool(okf._walk_errors(bundle_dir)):
        typer.echo(_INCOMPLETE_WALK_WARNING, err=True)


def progress_callback(
    verb: str, noun: str
) -> Callable[[int, int, object], None] | None:
    """Build the TTY-gated per-item progress hook a CLI verb passes into a
    library `on_progress` seam (issue #190).

    Returns `None` when stderr is NOT a TTY -- the verb then passes no hook
    at all, so a piped or redirected run stays byte-clean with zero
    per-item overhead. On a TTY, returns a callback matching every library
    `on_progress` contract (`(index, total, result)`, index 1-based) that
    prints `openkos <verb>: <noun> <index>/<total>...` to STDERR per item;
    the just-built result object is accepted but never rendered, so ONE
    generic factory serves every seam regardless of its result type.
    STDOUT is never touched -- it belongs to the verb's report.
    """
    if not sys.stderr.isatty():
        return None

    def _callback(index: int, total: int, _result: object) -> None:
        typer.echo(f"openkos {verb}: {noun} {index}/{total}...", err=True)

    return _callback


def stage_notice(verb: str, message: str) -> None:
    """Print one TTY-gated `openkos <verb>: <message>` stage line to STDERR
    -- `progress_callback`'s single-call sibling for verbs whose long wait
    is ONE LLM call rather than a per-item loop (issue #190: `ingest`
    before extraction, `query` before its answer call).

    Silent when stderr is NOT a TTY, for the same clean-piping reason;
    signal-only otherwise -- it never raises and never changes the
    caller's exit code.
    """
    if not sys.stderr.isatty():
        return
    typer.echo(f"openkos {verb}: {message}", err=True)
