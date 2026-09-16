"""The `status` command's read core (issue #995, PR 3 of MVP 3's
"prerequisite zero" -- the read verbs need an application service before an
MCP adapter can be a thin layer instead of a second implementation).

`status`'s pre-extraction body in `cli/main.py` was compute-and-print
INTERLEAVED, not compute-then-render: each read fed a `typer.echo` call a
few lines later, sometimes with presentation logic (label widths, `_plural`,
the literal command names `openkos duplicates`/`openkos curate`/
`openkos reindex`) folded into the same block. That interleaving was not
incidental: the workspace header, `Bundle contents:`, and `Recent
activity:` sections printed BEFORE the unguarded reads below ran, so a
damaged workspace (a corrupt bundle doc, an unreadable `graph.db`) still
left the operator with three useful sections ahead of a traceback. The
first version of this extraction (issue #995, PR 3) hoisted EVERY read
into one function ahead of the CLI's first `typer.echo`, which silently
erased that partial-output behaviour -- exactly backwards for the one
command whose job is describing a broken workspace. Review findings
R3-partial-output-on-read-failure and R4-status-no-partial-output caught
it; this module now splits the reads into two calls that preserve the
original interleaving at the one boundary that matters:

- `read_status_overview` performs ONLY the cheap, already-guarded reads --
  `okf.survey_bundle` (never raises: a malformed doc becomes a
  `BundleSurvey` finding, not an exception) and the `log.md` read, which
  already degrades through `except (OSError, ValueError)` (D5). The CLI
  adapter calls this FIRST and renders the header, `Bundle contents:`, and
  `Recent activity:` from its result before doing anything else.
- `build_status_report` performs every OTHER read `status` needs --
  `lint.collect_docs` plus its eight checks over one shared `docs` list,
  the exact-title candidate-group count, the persisted-contradiction
  counts, the vector-index/derived-index staleness checks, and the graph
  projection -- none of which are guarded. The CLI adapter calls this
  SECOND, only after the sections above have already reached stdout, and
  renders `Needs attention:` and the trailing notices from its result.

Both results carry RAW facts, never rendered strings or derived display
values -- the CLI adapter keeps every `typer.echo`, `_bundle_content_lines`,
`_plural`, and the `needs_attention` string construction (same split as
`application/query.py`'s D1/D2). An `untyped` edge count is not stored on
`StatusReport` because it is trivially `total - typed` over `edge_summary`,
which the adapter can compute itself exactly as the pre-extraction code did
inline -- storing a value one subtraction away from another stored value
would just be a second, driftable copy of it.

The three-way `recent_entries` state -- `None` (log unreadable or
unparseable), `()` (log readable but genuinely empty), or a populated
tuple -- is preserved exactly: `None` and `()` render as DIFFERENT lines in
the CLI ("Recent activity unavailable ..." vs "No activity recorded yet."),
so collapsing the distinction here would break that branch silently rather
than loudly.

`stale_index_names` and `contradiction_finding_counts` are promoted here
verbatim from `cli.main._stale_index_names` and
`cli.main._contradiction_finding_counts` (public names now: no leading
underscore, per `tests/unit/application/test_layering.py::
test_application_private_names_are_never_consumed_across_the_boundary`,
issue #974 -- every module outside `application/` is forbidden from
reaching a private name on an `application/*` module, and both `status`'s
own service and `cli.main`'s `query` command are legitimate callers across
that boundary). `stale_index_names`' own docstring already explained why it
was shared rather than forked: `query` and `status` must agree on what
"stale" means, and on the wording of the names they print. That sharing
previously lived awkwardly in `cli/main.py` -- an adapter module hosting a
predicate two DIFFERENT call sites in two different commands both needed --
which this promotion resolves for good, alongside deduplicating it into
`tests/unit/application/test_layering.py::
test_shared_read_predicates_are_never_forked`'s guarded set.
"""

from __future__ import annotations

from dataclasses import dataclass

from openkos import config
from openkos import lint as lint_check
from openkos.application import pending as application_pending
from openkos.bundle import log as bundle_log
from openkos.graph.sqlite_graph import build_graph
from openkos.graph.summary import asserted_relations_exist, graph_edge_summary
from openkos.model import okf
from openkos.resolution import find_exact_title_groups
from openkos.resolution.contradiction import is_high_confidence_finding
from openkos.state.derived import stale_derived_stores
from openkos.state.vectorstore import vector_store_is_empty

RECENT_ACTIVITY_LIMIT = 5
"""How many `log.md` bullets `read_status_overview` surfaces, newest-first --
moved verbatim from `cli.main.RECENT_ACTIVITY_LIMIT`: the display limit is
policy the READ owns (`bundle.log.read_recent_entries`'s own docstring:
"the display limit itself is `cli/main.py`'s concern" predates this
extraction, which moves that concern here alongside the read it gates)."""


@dataclass(frozen=True)
class StatusOverview:
    """The cheap, already-guarded reads `status` needs before it prints its
    first line -- deliberately kept separate from `StatusReport`; see the
    module docstring for why that split and its calling order are
    load-bearing, not incidental. Frozen for the same reason as
    `StatusReport`: a `list` field on a frozen dataclass is still mutable
    in place."""

    survey: okf.BundleSurvey
    """Source/concept counts and §9 conformance findings from ONE
    `okf.survey_bundle` walk -- unrelated to the eight lint checks
    `build_status_report` runs, which walk `lint.collect_docs` separately
    (#141/#187: lint findings are knowledge-health vocabulary, never OKF
    conformance, so `survey_bundle` never computes them). `survey_bundle`
    itself never raises: a missing/unparseable frontmatter or unreadable
    file becomes a finding on the returned survey, not an exception -- what
    makes it safe to read here, ahead of the CLI's first `typer.echo`."""

    recent_entries: tuple[bundle_log.LogEntry, ...] | None
    """The most recent `RECENT_ACTIVITY_LIMIT` `log.md` bullets,
    newest-first. `None` means the log could not be read or parsed
    (`except (OSError, ValueError)`, D5's lenient degrade) -- a DISTINCT
    state from `()` (a readable log with no dated sections yet), which the
    CLI adapter renders with different wording; collapsing the two would be
    a silent behaviour change, not a refactor."""


@dataclass(frozen=True)
class StatusReport:
    """Every OTHER raw fact `status` reads -- everything `read_status_overview`
    does not already cover -- gathered by `build_status_report`, called only
    AFTER `StatusOverview` is rendered; see the module docstring for why
    that calling order is the whole point. Every collection field is a
    `tuple`, never a `list`: the dataclass is frozen, and a `list` field on
    a frozen dataclass is still mutable in place, which would defeat the
    point of freezing it."""

    dangling: tuple[lint_check.LintFinding, ...]
    """Dangling-reference findings (#141)."""

    unextracted: tuple[lint_check.LintFinding, ...]
    """Unextracted-source findings (#187)."""

    unjudged: tuple[lint_check.LintFinding, ...]
    """Unjudged-extraction findings -- the quarantine's read half (#772)."""

    unevidenced: tuple[lint_check.LintFinding, ...]
    """Quoted-evidence disclosure findings (#801)."""

    staging_dropped: tuple[lint_check.LintFinding, ...]
    """Staging-loss disclosure findings (#843)."""

    sensitivity_findings: tuple[lint_check.LintFinding, ...]
    """Below-source-sensitivity findings (#231)."""

    dangling_provenance: tuple[lint_check.LintFinding, ...]
    """Dangling-provenance findings (#257)."""

    unbacked_provenance: tuple[lint_check.LintFinding, ...]
    """Unbacked engine-owned `derived_from` findings (#421)."""

    has_eligible_docs: bool
    """`bool(docs)` over the SAME `lint.collect_docs` list the eight checks
    above share -- gates the missing-vector-index needs-attention line
    (#386: reindexing a bundle with nothing to index is meaningless)."""

    exact_title_group_count: int
    """Count of exact-title candidate groups (#186, `find_exact_title_groups`,
    #216) NOT already ruled distinct by a human (#797,
    `application_pending.is_group_kept_distinct`) -- the suppression is
    computed here, not left for the adapter, because it changes the COUNT
    itself, not just how the count is displayed."""

    open_contradictions: int
    """Persisted, non-declined, non-stale high-confidence contradiction
    findings -- see `contradiction_finding_counts` (#598)."""

    stale_contradictions: int
    """The stale half of the same split; see `contradiction_finding_counts`
    (#598)."""

    vectors_missing: bool
    """Whether `vectors.db` is absent or empty (#183 state 3)."""

    stale_indexes: tuple[str, ...]
    """Manifest-gated derived stores (`fts`, `graph`) whose contents predate
    the bundle (#381); see `stale_index_names`."""

    edge_summary: tuple[int, int] | None
    """`(total, typed)` concept-to-concept edge counts from the graph
    projection, or `None` exactly when `vectors_missing` is `True` -- the
    graph projection is never opened in that case, mirroring the
    pre-extraction `if not vectors_missing:` gate verbatim (#387)."""

    asserted_relations: bool | None
    """Whether the graph projection holds any human-asserted typed relation
    regardless of endpoint family (#912) -- consulted only when
    `edge_summary` is `(0, ...)`, so the empty-graph notice is not denied by
    relations asserted only on Source documents.

    `None` means NOT COMPUTED, and is the value in every other state: the
    projection is opened only when `vectors_missing` is `False`, and
    `asserted_relations_exist` is called only inside the zero-edge branch,
    so a plain `False` here would conflate "the projection holds no
    asserted relation" with "nobody asked" (review finding
    R2-asserted-relations-false-is-ambiguous). The sole consumer reads it
    behind that same zero-edge guard, so this is a truthfulness fix, not a
    behaviour change -- `not None` and `not False` agree."""


def stale_index_names(
    layout: config.WorkspaceLayout, *, reads: tuple[str, ...]
) -> tuple[str, ...]:
    """The manifest-gated derived stores whose contents predate the bundle,
    named for a user-facing advisory (#381) -- shared by `query` and
    `status` (and mirrored by `next`'s `_BundleSignals.stale_indexes`) so
    all three agree on what "stale" means and on the wording of the names
    they print.

    `reads` (#436) declares which of the checked stores THIS caller's
    answer actually depends on, and the advisory names only that
    intersection. `query` stopped reading `graph.db` in #434, so graph
    staleness cannot degrade its answer and warning about it there was a
    true statement about the workspace attached to the wrong claim
    ("this answer may be degraded"). `status` and `next` describe the
    workspace itself, so they keep declaring both stores. Keeping the one
    shared function -- with the caller's declaration as a parameter rather
    than a fork -- is the point: what "stale" means still lives in exactly
    one place. A name in `reads` outside the checked set is ignored.

    Only `fts.db` and `graph.db` are checkable, because only those two are
    gated by a whole-bundle `manifest_hash`. `vectors.db` is maintained
    per-document (`reindex` compares each doc's own `content_hash`), so an
    edited bundle leaves it PARTIALLY current rather than wholesale stale --
    which is exactly the asymmetry #381's evidence recorded, where dense
    retrieval still returned 9 hits while FTS and graph returned 0.

    Never raises: a failing advisory must not be what breaks the command it
    advises, so any error degrades to "nothing to report" rather than
    propagating. The cost is one bundle walk (~4ms over 29 docs), and
    `stale_derived_stores` skips even that when no declared store is on
    disk.

    Promoted verbatim from `cli.main._stale_index_names` (issue #995, PR 3):
    the docstring above is unchanged in substance from the pre-promotion
    version, because the reasoning it records did not change -- only the
    module, and the name (public, no leading underscore, so both `status`'s
    own service and `cli.main`'s `query` command can call it across the
    `application/` boundary)."""
    known = (("fts", layout.fts_db_path), ("graph", layout.graph_db_path))
    stores = tuple((name, path) for name, path in known if name in reads)
    if not stores:
        return ()
    try:
        return stale_derived_stores(layout.bundle_dir, stores)
    except Exception:  # broad: an advisory never breaks its own command
        return ()


def contradiction_finding_counts(layout: config.WorkspaceLayout) -> tuple[int, int]:
    """`(open, stale)` counts over the persisted, NON-declined findings --
    the two numbers `status` reports (#598).

    Declined findings are dropped outright, not counted into either total
    (pending-work spec: "Declined Findings Are Hidden By Default", which
    names `status`); `contradictions --declined` is the one view that shows
    them. The open count uses the SAME high-confidence-CONTRADICTS ∧ open ∧
    not stale ∧ not declined predicate `next_action.open_contradictions`
    ranks on, so the two commands can never disagree about what is
    outstanding. The verdict filter (#639) runs BEFORE the stale/open
    split: curate persists EVERY judged verdict, `consistent` included, and
    a consistent finding is neither open work nor stale work -- counting it
    told operators they had contradictions nothing could clear. It is the
    shared `is_high_confidence_finding` predicate, never a local threshold,
    so this count can never drift from what `contradictions` shows and
    `reconcile --from-findings` offers. Stale is split off rather than
    folded in or dropped: `next` excludes it by design, so `status` staying
    silent would erase it from the operator's view entirely ("A stale
    finding remains visible as stale").

    Promoted verbatim from `cli.main._contradiction_finding_counts` (issue
    #995, PR 3), public name only -- only `status` calls this, so unlike
    `stale_index_names` there is no second adapter call site to repoint."""
    open_count = 0
    stale_count = 0
    for finding in application_pending.persisted_findings(layout):
        if not is_high_confidence_finding(finding.verdict, finding.confidence):
            continue
        if application_pending.is_contradiction_declined(
            layout, finding.pair_ids, finding.merged_absorbed_id
        ):
            continue
        if finding.stale:
            stale_count += 1
        else:
            open_count += 1
    return open_count, stale_count


def read_status_overview(layout: config.WorkspaceLayout) -> StatusOverview:
    """Gather ONLY the cheap, already-guarded reads `status` needs before it
    can render anything, WITHOUT rendering a single line itself. This is
    deliberately the FIRST call the CLI adapter makes, ahead of
    `build_status_report`; see the module docstring for why that split and
    calling order are load-bearing, not incidental (review findings
    R3-partial-output-on-read-failure / R4-status-no-partial-output).

    This function's own guarantee is narrower than "never raises": the
    `log.md` read is the ONE read explicitly guarded here (`except (OSError,
    ValueError)` -> `recent_entries = None`, D5's lenient degrade). The
    `okf.survey_bundle` call above it is NOT guarded by this function --
    there is no `try` around it. What makes that call safe in practice is
    `survey_bundle`'s OWN contract (a malformed or unreadable document
    degrades to a finding on the returned survey rather than raising; see
    its docstring), not anything this function does. If that contract were
    ever violated, the exception would propagate straight out of this FIRST
    call, uncaught here and uncaught by `status()`'s own body -- before the
    workspace header, `Bundle contents:`, or `Recent activity:` reach
    stdout. An operator would see a raw traceback and no output at all: the
    one partial-output failure mode this split does not protect against."""
    survey = okf.survey_bundle(layout.bundle_dir)

    try:
        log_text = (layout.bundle_dir / "log.md").read_text(encoding="utf-8")
        recent_entries: tuple[bundle_log.LogEntry, ...] | None = tuple(
            bundle_log.read_recent_entries(log_text, RECENT_ACTIVITY_LIMIT)
        )
    except (OSError, ValueError):
        recent_entries = None

    return StatusOverview(survey=survey, recent_entries=recent_entries)


def build_status_report(layout: config.WorkspaceLayout) -> StatusReport:
    """Gather every OTHER fact `status` reports -- everything
    `read_status_overview` does not already cover -- performing each read
    the pre-extraction command body performed, in the same order, with the
    same degrade behaviour, but WITHOUT rendering a single line. Deliberately
    the SECOND call the CLI adapter makes; see the module docstring for why.

    Every read below is UNGUARDED (`lint.collect_docs` and its eight
    checks, the exact-title/contradiction counts, `vector_store_is_empty`,
    `build_graph`): this function never raises beyond what these reads
    themselves would raise uncaught in the pre-extraction body -- exactly
    as fallible, and exactly as unguarded, as they were inline in
    `cli.main.status` before this extraction."""
    # #141: dangling-reference findings are knowledge-health (lint)
    # vocabulary, not OKF conformance -- `survey_bundle` never computes
    # them, so this reads `lint`'s own `collect_docs` + the checks below
    # directly. #187/#772/#801/#843/#231/#257/#421: every check after the
    # first reuses this SAME in-memory `docs` list -- no second, third, ...
    # `collect_docs()` call, no new walk (status spec: "No new bundle walk
    # is introduced").
    docs, _skip_notices = lint_check.collect_docs(layout.bundle_dir)
    dangling = tuple(lint_check.check_dangling_targets(docs))
    unextracted = tuple(lint_check.check_unextracted(docs))
    unjudged = tuple(lint_check.check_unjudged(docs))
    unevidenced = tuple(lint_check.check_unevidenced(docs))
    staging_dropped = tuple(lint_check.check_staging_dropped(docs))
    sensitivity_findings = tuple(lint_check.check_below_source_sensitivity(docs))
    dangling_provenance = tuple(lint_check.check_dangling_provenance(docs))
    unbacked_provenance = tuple(lint_check.check_unbacked_provenance(docs))

    # #186/#216: exact-title candidate groups only (never near-match) --
    # #797: a group a human already ruled distinct is filtered out HERE,
    # not inside `find_exact_title_groups` (deliberately uncapped and
    # shared), so the suppression stays this caller's own choice, exactly
    # as it was inline.
    exact_title_group_count = sum(
        1
        for group in find_exact_title_groups(layout.bundle_dir)
        if not application_pending.is_group_kept_distinct(layout, group.member_ids)
    )

    # #598: the persisted contradiction verdicts `curate` already paid an
    # LLM to compute -- a SQLite read of `.openkos/findings.db`, NOT a
    # sixth bundle walk.
    open_contradictions, stale_contradictions = contradiction_finding_counts(layout)

    # #183 (Slice 0): a missing/empty `vectors.db` gates both the
    # needs-attention line (adapter-side) and whether the graph projection
    # below is opened at all.
    vectors_missing = vector_store_is_empty(layout.vectors_db_path)

    # #381: declares BOTH manifest-gated stores (#436) -- `status`
    # describes the workspace, not one answer, unlike `query`, which reads
    # only `fts`.
    stale_indexes = stale_index_names(layout, reads=("fts", "graph"))

    # #387/#912/#195: the graph projection is built at most once, and only
    # when `vectors.db` is non-empty -- `edge_summary` stays `None`
    # otherwise, mirroring the pre-extraction `if not vectors_missing:`
    # gate exactly.
    edge_summary: tuple[int, int] | None = None
    asserted_relations: bool | None = None
    if not vectors_missing:
        with build_graph(layout.bundle_dir) as store:
            edge_summary = graph_edge_summary(layout.bundle_dir, store=store)
            # Consulted only by the zero-count notice, so the second
            # edges() pass is paid only when the concept-to-concept count
            # is already zero -- the one case where the edge set is small.
            if edge_summary[0] == 0:
                asserted_relations = asserted_relations_exist(store)

    return StatusReport(
        dangling=dangling,
        unextracted=unextracted,
        unjudged=unjudged,
        unevidenced=unevidenced,
        staging_dropped=staging_dropped,
        sensitivity_findings=sensitivity_findings,
        dangling_provenance=dangling_provenance,
        unbacked_provenance=unbacked_provenance,
        has_eligible_docs=bool(docs),
        exact_title_group_count=exact_title_group_count,
        open_contradictions=open_contradictions,
        stale_contradictions=stale_contradictions,
        vectors_missing=vectors_missing,
        stale_indexes=stale_indexes,
        edge_summary=edge_summary,
        asserted_relations=asserted_relations,
    )
