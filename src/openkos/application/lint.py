"""The `lint` command's read core (issue #995, PR 4 of MVP 3's
"prerequisite zero" -- the read verbs need an application service before an
MCP adapter can be a thin layer instead of a second implementation).

This module ORCHESTRATES: it wires `config.read_config`, the `index.md`
read, `lint.resolve_windows`, `lint.collect_docs`, and all thirteen
`lint.check_*` calls into one read, and returns the result as the SAME
`lint.LintReport` that module already declares. `openkos/lint.py` IMPLEMENTS
the checks themselves (`LintDoc`, `LintFinding`, `LintReport`, every
`check_*`/`scan_*` function) as a pure module with no knowledge of a caller,
a workspace, or a CLI -- this module is the one adapter-facing seam that
turns "the pieces `lint.py` exposes" into "the one read `lint` needs". A
reader who wonders which of the two `lint` modules owns a given piece of
behaviour: if it is a rule about what counts as a finding, it lives in
`openkos/lint.py`; if it is about WHEN/HOW OFTEN a rule runs against a real
workspace, it lives here.

Unlike `status` (`application/status.py`, issue #995 PR 3), `lint` has no
partial-output property to preserve, and therefore no need for two service
calls. `status`'s pre-extraction CLI body was compute-and-print
INTERLEAVED: the workspace header, `Bundle contents:`, and `Recent
activity:` sections printed BEFORE later, unguarded reads ran, so a damaged
workspace still left the operator with useful output ahead of a traceback --
which is why `status` was split into `read_status_overview` (cheap,
guarded, called and rendered FIRST) and `build_status_report` (everything
else, called and rendered SECOND). `lint`'s pre-extraction CLI body was the
opposite shape: EVERY read and check (`config.read_config`, the `index.md`
read, `lint.collect_docs`, `lint.resolve_windows`, and all thirteen
`check_*`/`scan_*` calls) ran to completion before the first `typer.echo`
-- verified by reading the pre-extraction `cli/main.py::lint` body top to
bottom: its first `typer.echo` was the one printing the workspace header,
and every read/check call preceded it, with no interleaved `typer.echo` in
between. Hoisting every read into one function therefore loses nothing a
reader could observe on a failure path, unlike `status`'s case -- so one
function, `build_lint_report`, is the whole service.

THE ERROR CONTRACT, stated once and authoritatively, because this is what
a headless or MCP adapter author comes to this module docstring for:

`build_lint_report` raises `LintInputUnavailable` when any of its three
INPUT reads fails -- `config.read_config`, the `bundle/index.md` read, or
`lint.collect_docs`. **It subclasses `Exception`, NOT `OSError`**, so a
caller who writes `except (OSError, ValueError)` around this call catches
nothing. Catch `LintInputUnavailable` by name.

Everything after those three reads propagates UNCAUGHT, exactly as it did
inline in the pre-extraction body. That is deliberate; see below.

The pre-extraction body wrapped those same three reads in one
`try/except (OSError, ValueError)`, translating a failure into
`openkos lint: failed while reading the workspace -- {exc}.` and exit 1.
The typed error carries that same boundary across the layer: the
distinction that used to live in a `try` SCOPE is data now, which is what
lets an adapter branch on it instead of parsing a line of text.

Guarding the whole call instead was tried and reverted (issue #995 PR 4,
review findings R4-lint-guard-widening-masks-non-read-errors /
R2-lint-service-docstring-contradicts-its-own-code-and-test). Two claims
justified it and both are false. `check_*` calls do NOT all operate on the
already-read `docs` list: `check_non_nfc_names`,
`check_state_dir_contains_no_markdown` and `check_dot_dir_markdown` each
take `layout.bundle_dir` and walk the filesystem themselves, so they can
raise `OSError`. And the failures they raise are not read failures at all
in every case -- a malformed `(as of YYYY-MM-DD)` body stamp reaching
`date.fromisoformat` inside `check_stale_stamps` raises `ValueError` from
in-memory logic, which "failed while reading the workspace" would name
wrongly. A refactor is the wrong place to change which errors become
messages; that is a separate decision with its own evidence.

A GAP THIS CONTRACT MAKES VISIBLE, deliberately left open (review finding
R4-lint-report-is-all-or-nothing-on-a-late-walk-failure): the report is
all-or-nothing on a late failure. The three tree-walking checks run LAST
and have no containment, so one unreadable directory under the bundle
discards the twelve check results already computed in the same call and
the operator gets a raw traceback with no diagnostic line.

That is PRE-EXISTING -- verified against `git show
main:src/openkos/cli/main.py` before this extraction, where the same three
checks ran last and unguarded in the command body. The extraction neither
introduced nor fixed it. Closing it means deciding what a partial
`LintReport` should mean and how the CLI should render one, which is a
product decision with its own evidence, not something a refactor gets to
make silently."""

from __future__ import annotations

from datetime import UTC, datetime

from openkos import config
from openkos import lint as lint_check


class LintInputUnavailable(Exception):
    """The workspace inputs `lint` needs could not be read: `openkos.yaml`,
    `bundle/index.md`, or the `collect_docs` walk.

    Exactly the failure the pre-extraction command body caught in its own
    `try/except (OSError, ValueError)` around those same three reads, and
    nothing else. A failure from any later check is NOT this and
    propagates uncaught, as it did before the extraction."""


def build_lint_report(layout: config.WorkspaceLayout) -> lint_check.LintReport:
    """Gather every read and check `lint` needs, WITHOUT rendering a single
    line (see module docstring for why this is ONE call rather than two).

    Performs, in the same order as the pre-extraction command body:
    `config.read_config`, the `bundle/index.md` read, `lint.collect_docs`
    (the ONE bundle walk that feeds `check_stale_stamps`, `check_orphans`,
    and the eight checks sharing `docs` below -- design D3's no-fifth-walk
    guard), `lint.resolve_windows`, then all thirteen `check_*`/`scan_*`
    calls. `today` is computed ONCE via `datetime.now(UTC).date()` and
    injected into `check_stale_stamps` -- the clock is never read inside
    `lint.py` itself (see that module's own contract), so this is the one
    place it gets read, exactly as it was in the pre-extraction CLI body.

    `check_below_source_sensitivity` returns one list carrying both
    `"below-source-sensitivity"` and `"multi-source-uncovered"` findings;
    `LintReport` declares them as two separate fields, so this function
    splits by `finding.kind`, exactly as the pre-extraction command body
    did inline -- a data-shaping step feeding the returned dataclass, not
    rendering, so it belongs here rather than in the CLI adapter.

    The three INPUT reads -- `config.read_config`, the `index.md` read and
    `collect_docs` -- raise `LintInputUnavailable`, which is the exact
    failure the pre-extraction body caught. Every check after them
    propagates uncaught, also exactly as before; see the module docstring
    for why widening that scope was tried and reverted."""
    try:
        cfg = config.read_config(layout.root)
        index_text = (layout.bundle_dir / "index.md").read_text(encoding="utf-8")
        docs, skip_notices = lint_check.collect_docs(layout.bundle_dir)
    except (OSError, ValueError) as exc:
        raise LintInputUnavailable(str(exc)) from exc

    windows, window_notices = lint_check.resolve_windows(cfg)
    today = datetime.now(UTC).date()
    stale = lint_check.check_stale_stamps(docs, today=today, windows=windows)
    orphans = lint_check.check_orphans(docs, index_text=index_text)
    dangling = lint_check.check_dangling_targets(docs)
    unextracted = lint_check.check_unextracted(docs)
    # #772: reuses this SAME `docs` list -- the quarantine's read half.
    unjudged = lint_check.check_unjudged(docs)
    # #801: and again -- the read half of the quoted-evidence disclosure.
    unevidenced = lint_check.check_unevidenced(docs)
    # #843: and again -- the read half of the staging-loss disclosure.
    staging_dropped = lint_check.check_staging_dropped(docs)
    # #231 (PR2): reuses this SAME `docs` list -- no new bundle walk
    # (design D3's no-fifth-walk guard).
    sensitivity_findings = lint_check.check_below_source_sensitivity(docs)
    below_source = [
        finding
        for finding in sensitivity_findings
        if finding.kind == "below-source-sensitivity"
    ]
    multi_source_uncovered = [
        finding
        for finding in sensitivity_findings
        if finding.kind == "multi-source-uncovered"
    ]
    # issue #257: reuses this SAME `docs` list again -- no new bundle walk
    # (the structural no-fifth-walk guard holds).
    dangling_provenance = lint_check.check_dangling_provenance(docs)
    # issue #421: and again -- pure, deterministic, no LLM, no clock.
    unbacked_provenance = lint_check.check_unbacked_provenance(docs)
    # issue #474: a names-only walk, never the docs list -- collect_docs
    # cannot see a decomposed directory, non-`.md` file, or unreadable doc.
    non_nfc = lint_check.check_non_nfc_names(layout.bundle_dir)
    # task 3.6: a names-only walk over `bundle/.state/` alone, never the
    # `docs` list -- `collect_docs`/`_iter_docs` never descends there.
    state_dir_markdown = lint_check.check_state_dir_contains_no_markdown(
        layout.bundle_dir
    )
    # issue #984: another names-only walk over the bundle, never the `docs`
    # list -- `collect_docs`/`_iter_docs` never descends into a
    # dot-directory at all (the same structural exclusion this check is the
    # safety net for). `.state/` keeps its own, more specific finding above.
    dot_dir_markdown = lint_check.check_dot_dir_markdown(layout.bundle_dir)
    notices = window_notices + skip_notices

    return lint_check.LintReport(
        stale=stale,
        orphans=orphans,
        dangling=dangling,
        unextracted=unextracted,
        unjudged=unjudged,
        unevidenced=unevidenced,
        staging_dropped=staging_dropped,
        below_source=below_source,
        multi_source_uncovered=multi_source_uncovered,
        dangling_provenance=dangling_provenance,
        unbacked_provenance=unbacked_provenance,
        non_nfc=non_nfc,
        state_dir_markdown=state_dir_markdown,
        dot_dir_markdown=dot_dir_markdown,
        notices=notices,
    )
