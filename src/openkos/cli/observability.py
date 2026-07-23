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
"""

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
