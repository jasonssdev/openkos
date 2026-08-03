"""CLI-layer observability helpers for the sensitivity-aware fail-closed
filter (sensitivity-fail-closed-filter, MVP-3 gap #8 · S3 --
directory-walk-observability follow-up).

`sensitivity.py` stays a pure, no-I/O leaf (its own module invariant): this
module owns the CLI-visible STDERR signal that the directory walk backing
the fail-closed confidential-content filter was itself incomplete
(`okf._walk_errors`) -- a directory-scan error can make part of a bundle
unreadable, so the filter could not inspect every document and some
confidential material may not have been excluded.

The confidential-content filter can also be off entirely with NO walk
error involved: `--include-confidential` disables it explicitly, and the
confidential local exemption (issue #240) disables it implicitly whenever
`sensitivity.sensitive_concept_ids` short-circuits to `frozenset()` before
`okf._iter_docs` is ever touched, because the resolved client/workspace
pair already proved the filter's whole reason for existing (stopping
confidential material from leaving the machine) does not apply this run.
Both hatches share the same consequence FOR THE FILTER: no filter ran, so
"some confidential material may not have been excluded" would be a claim
about something that never happened, and `warn_if_walk_incomplete` must be
told about both so it can hold that message back.

Issue #356 is the correction to how far that reasoning reached. Until it,
this module treated an incomplete walk as ONLY a filter concern and
returned before walking at all under either hatch, on the argument that
the walk's result would be discarded anyway. That argument was false. The
same unlistable subdirectory truncates the whole INPUT SET, not just the
filter's view of it: `okf._iter_docs` walks with `rglob`, which silently
swallows `scandir()` failures, and `graph.sqlite_graph.build_graph` ->
`_populate_graph_tables` -> `_iter_docs`, so the graph projection quietly
loses nodes, edges, candidate edges, and contradictions -- and the command
still exits 0 over a bundle it never fully read. On the SHIPPED DEFAULT
path (local backend, exemption active) that produced no signal anywhere.
So this module now carries TWO independent messages over ONE walk: a
general "your inputs were incomplete" advisory that no hatch suppresses,
and the original filter-scoped one that both hatches still suppress. They
are kept separate rather than merged into one conditionally-worded line so
that each states one truth on its own and neither lies when the other does
not apply. The zero-cost bypass is genuinely given up -- one walk per
invocation is now paid on every path -- which is the accepted price of the
signal; what is not accepted is paying for it twice, hence the single
`okf._walk_errors` call feeding both messages.

`okf.survey_bundle` already reports the same condition as one
`unreadable directory` finding per failure, which is how `status` surfaces
it (under `Needs attention:`) and how `doctor` fails its bundle-readable
check. #356 changed WHO ELSE reports it, not how it is detected: those
verbs keep their finding and deliberately gain no second advisory, since a
duplicate line would say nothing their findings do not already say. Note
that `lint` does NOT surface it, contrary to what #356 assumed -- it reads
through `lint.collect_docs` -> `okf._iter_docs`, the walk that drops the
subtree silently -- which is why the advisory below points at `status`.

Mirrors the existing `state/reindex.py:285` + `cli/main.py`'s
`report.prune_skipped` self-explaining-warning precedent, generalized to
one shared helper reused by all six sensitivity-filter call sites (`query`,
`contradictions`, `adjudicate`, `suggest-relations`, `suggest-volatility`,
and `curate`, which resolves its own exemption once for the whole run --
design D8 -- and calls this helper exactly once rather than per stage)
instead of duplicating the STDERR message at six call sites.

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

_INCOMPLETE_INPUTS_WARNING = (
    "openkos: bundle scan was incomplete -- a directory-scan error made "
    "part of the bundle unreadable, so this command's inputs are "
    "incomplete and its result may be missing content. Fix the directory "
    "permissions and re-run, or run 'openkos status' to see which "
    "directories could not be read."
)
"""The general completeness advisory (#356), emitted on EVERY incomplete
walk with no hatch able to suppress it.

Deliberately says nothing about sensitivity: it is true whatever the
confidential filter did, because what shrank is the document set every
downstream pass reads -- concepts, edges, candidate pairs, contradictions.
It is worded as "may be missing content" rather than a count, because the
contents of a subtree that could not be listed are by definition unknown
(the same reason `okf.survey_bundle` never folds a walk error into its
`sources`/`concepts` totals).

It opens with the same `bundle scan was incomplete` phrase as the
filter-scoped message below, on purpose: when both print, they are one
cause reported at two scopes, and a reader should see that immediately.
The consequence for tests is that the shared prefix no longer identifies
either message -- assert on `this command's inputs are incomplete` for
this one, `confidential-content filter` for the other.

`openkos status` is the pointer rather than a repetition of the paths,
because `okf.survey_bundle` already names EVERY unreadable directory as a
finding; this line stays one line no matter how many directories failed.
It is `status` specifically, and NOT `lint`, even though both are read
verbs and issue #356 assumed both surfaced it: `status` folds
`survey.findings` into its `Needs attention:` section verbatim, while
`lint` reads through `lint.collect_docs` -> `okf._iter_docs`, which is the
very walk that swallows the error -- pointing at `lint` would send the
user to a command that reports nothing over the exact bundle this line
just warned about."""

_INCOMPLETE_WALK_WARNING = (
    "openkos: bundle scan was incomplete -- a directory-scan error made "
    "part of the bundle unreadable, so the confidential-content filter "
    "could not inspect every document and some confidential material may "
    "not have been excluded. Fix the directory permissions and re-run, or "
    "pass --include-confidential to bypass the filter deliberately."
)
"""Self-explaining STDERR message (mirrors `state/reindex.py`'s
`prune_skipped` notice style): names the condition, its consequence, and
both remediation paths, rather than a bare "walk incomplete" line.

Unchanged by #356, in wording and in suppression: it is a claim about the
FILTER, so it may only print when a filter actually ran -- neither
`--include-confidential` nor the confidential local exemption is set."""


def warn_if_walk_incomplete(
    bundle_dir: Path,
    *,
    mode: str = "warn",
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> None:
    """Warn to STDERR when the directory walk over `bundle_dir` is provably
    incomplete (`okf._walk_errors` reports at least one unlistable
    subdirectory), with up to TWO independent messages over ONE walk.

    `_INCOMPLETE_INPUTS_WARNING` is emitted on EVERY incomplete walk (#356).
    Neither hatch suppresses it, because it is not about the sensitivity
    filter: `okf._iter_docs` is the walk behind the whole document set, and
    `graph.sqlite_graph.build_graph` builds its projection from it, so an
    unlistable subtree silently costs the run nodes, edges, candidate edges
    and contradictions whatever the filter did. Before #356 that loss was
    reported by nobody on the shipped default path -- the command printed a
    smaller result and exited 0.

    `_INCOMPLETE_WALK_WARNING` is emitted ADDITIONALLY, and only when
    NEITHER `include_confidential` nor `local_exemption` is set. That
    message claims the confidential-content filter could not inspect every
    document, and either hatch takes the filter off entirely, so with one
    set the claim would be about a filter that never ran:
    `include_confidential` is the explicit, user-requested bypass;
    `local_exemption` (issue #240) is the implicit one, already resolved by
    the caller from the verified-local client and the workspace's
    `confidential_local_exemption` key before this helper is ever called.
    Suppressing THAT message under a hatch is still correct; suppressing
    the walk itself was not, and no longer happens.

    `okf._walk_errors` therefore runs exactly ONCE per call, and its single
    result feeds both messages. #356 accepts the cost of that walk on every
    path (the pre-#240 zero-cost bypass is deliberately given up: a result
    that IS actionable cannot be skipped as unused), but not the cost of
    walking twice, which is what two separately-guarded emissions would
    have meant for all six call sites.

    `mode="warn"` (the only mode this slice implements) emits and always
    returns normally: it NEVER raises and NEVER changes the caller's exit
    code, this helper is signal-only (spec: Incomplete walk warns and still
    exits 0). Any unrecognized mode stays a silent no-op and pays for no
    walk, exactly as before.

    `mode="refuse"` raises `NotImplementedError`: a stable seam for a
    future cloud-egress mode that REFUSES instead of warning on this
    condition, explicitly out of scope for this change (spec). The
    signature is already shaped for that future mode so its slice needs no
    re-threading -- only filling this branch in and flipping call sites to
    `mode="refuse"`. The mode check now runs FIRST, before the walk and
    before either message, so the seam can never half-run (no scan paid
    for, no line printed by a call that then raises). One consequence is
    intended: a hatch no longer pre-empts the seam, as it did when the
    hatch return sat above it. That pre-emption was an artifact of the old
    early return rather than a decision, and #356 established that this
    condition -- the bundle could not be fully read -- is independent of
    whether the confidential filter ran at all.
    """
    if mode == "refuse":
        raise NotImplementedError(
            "mode='refuse' is a stable seam for a future cloud-egress mode; "
            "not implemented in this slice"
        )
    if mode != "warn":
        return
    if not okf._walk_errors(bundle_dir):
        return
    typer.echo(_INCOMPLETE_INPUTS_WARNING, err=True)
    if not (include_confidential or local_exemption):
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
