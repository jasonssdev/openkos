"""`openkos next`'s tier engine: the single ranked, short-circuiting answer to
"which one command should I run next" over the current bundle.

This module owns the whole decision: an ordered tuple of tier callables
(`_TIERS`, D1 priority order) plus one lazily-memoized signal holder
(`_BundleSignals`). `cli/main.py` gains only the workspace gate, one call
into `next_action()`, and an echo loop over `render_lines()` -- no ranking
logic lives there.

WHY THE TIERS ARE IN THIS ORDER (issue #277) -- read before reordering
`_TIERS`. The order is *what blocks other work, then what is missing, then
what is unsafe, then what is merely ambiguous*:

0. a bundle with zero eligible documents has nothing to index, judge, or
   label at all (#386) -- every other recommendation presupposes content,
   so the first ingest outranks them all;
1. a missing vector index blocks dense retrieval and candidate edges, so
   every later judgment is made over a starved corpus;
2. an unextracted source is knowledge absent from the bundle entirely;
3. a descendant below its Source's sensitivity is present but mislabelled;
4. a duplicate group is present, correctly labelled, and merely ambiguous.

Absence outranks ambiguity BECAUSE the ambiguity cannot be judged correctly
over an incomplete set -- adjudicating duplicates before the missing
documents are in is work that may have to be redone.

Cost order corroborates this ranking (tier 1 is also the cheapest, tier 4
also the most expensive) but DOES NOT DRIVE IT, and the individual tier
docstrings below mention only cost because that is what their own
implementation has to honour. Do not infer from them that the sequence is
cost-derived and reorder on cost grounds: a cheaper check that answers a
less blocking question still belongs lower.

`status` (`cli/main.py:5209-5353`) is never touched, imported for its
control flow, or refactored (design D2): every signal this module reads
comes from a function `status`/`lint` already ship
(`vector_store_is_empty`, `lint_check.collect_docs` + its checks,
`find_exact_title_groups`), so no walk logic is duplicated and the two
verbs can drift in framing without drifting in truth.

Cost contract, enforced STRUCTURALLY, not by discipline: `_BundleSignals` is
the only object holding a `Path`. A tier callable receives only a
`_BundleSignals` instance, never a directory -- the same pinned-signature
guard `lint.py` already documents for `check_unextracted(docs)` -- so a tier
that never receives a directory is incapable of opening a walk on its own.
Each property memoizes its own walk, so reading `signals.docs` from both
tier 2 and tier 3 in the same run still calls `lint_check.collect_docs`
exactly once.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from openkos import config
from openkos import lint as lint_check
from openkos.resolution import CandidateGroup, find_exact_title_groups
from openkos.state.derived import stale_derived_stores
from openkos.state.vectorstore import vector_store_is_empty

_STATUS_POINTER = "For everything else, run `openkos status`."
"""The honesty guard (D4): appended after every branch of `render_lines`,
so the pointer to the full report is present whether or not a tier fired --
`next`'s short-circuit means it never proves a commandless finding's
absence, so it never claims the bundle is clean."""

_NO_ACTION_LINE = "No ranked action found in this bundle."
"""Printed only when every tier declines. Deliberately silent about
whether OTHER, commandless findings exist -- `next` never walked far enough
to know, and `_STATUS_POINTER` is where that answer lives."""

_INGEST_VERB = "openkos ingest"
_BACKFILL_VERB = "openkos backfill-sensitivity"
"""The verbs tiers 2 and 3 will accept out of a finding's detail prose. A
tier recognises its own command rather than trusting the detail's first
backtick span, because that prose interpolates document-controlled values
that may carry backticks themselves."""


def _plural(count: int) -> str:
    return "" if count == 1 else "s"


def _agree(count: int, singular: str, plural: str) -> str:
    """Pick the form that agrees with `count`, for words `_plural` cannot
    inflect by appending an `s`.

    A line carrying both a count and a verb has to inflect BOTH from the
    same number. Inflecting only the noun is what produced "1 candidate
    group with identical titles are pending review" -- the count was right,
    the noun was right, and the sentence still read as a typo. `status`
    avoids the trap by ending its own duplicate-group line with a command
    rather than a verb (`main.py:5323-5324`), so it never needed this."""
    return singular if count == 1 else plural


@dataclass(frozen=True)
class NextAction:
    """One ranked recommendation: a runnable command and a one-line reason."""

    command: str
    """The exact, runnable command string -- verbatim, never re-derived."""
    reason: str
    """A single line explaining why this command was recommended."""


@dataclass(frozen=True)
class NextResult:
    """Everything one `next` run learned: the ranked action, if any, plus
    the notices for documents the run could not read (#275).

    The two travel together because they are answers to the same question.
    `collect_docs` EXCLUDES an unreadable or unparseable document from
    `docs` and reports it only as a skip notice (`lint.py:147-166`), so a
    result carrying the action alone describes a document set it already
    knows is incomplete -- and "no ranked action" over a damaged bundle
    then reads as a clean scan, which is precisely what those notices exist
    to prevent."""

    action: NextAction | None
    declinations: tuple[str, ...] = ()
    """Real findings this run saw and deliberately refused to recommend,
    because no runnable command could be derived from them (#276). Declining
    is correct -- a command that does not run is worse than none -- but
    declining silently is not: the finding is genuine and would otherwise
    leave no trace on any output at all."""
    skip_notices: tuple[str, ...] = ()
    """Only what this run ACTUALLY observed, never what a further walk
    might have found. A run whose first tier fires without paying the docs
    walk collected no notices and says nothing -- surfacing them must not
    buy them with the walk the cost contract forbids (D3)."""


class _BundleSignals:
    """Lazily-memoized signals over one bundle, each paying its own walk
    exactly once no matter how many tiers read it (design: "lazy memoized
    signals, not self-contained tier closures")."""

    def __init__(self, layout: config.WorkspaceLayout) -> None:
        self._layout = layout
        self._docs: list[lint_check.LintDoc] | None = None
        self._skip_notices: tuple[str, ...] = ()
        self._declinations: list[str] = []
        self._exact_title_groups: list[CandidateGroup] | None = None
        self._stale_indexes: tuple[str, ...] | None = None

    def record_declination(self, notice: str) -> None:
        """Note a real finding this run deliberately refused to turn into a
        recommendation (#276). Kept on the signals object rather than
        threaded through the tier return type so `Tier`'s pinned signature
        -- the structural guard that a tier receives only signals, never a
        directory -- stays exactly as it is."""
        self._declinations.append(notice)

    @property
    def observed_declinations(self) -> tuple[str, ...]:
        """Findings seen and declined, in evaluation order. Like
        `observed_skip_notices`, this reports only what an already-paid
        walk produced and never triggers one."""
        return tuple(self._declinations)

    @property
    def vector_store_empty(self) -> bool:
        """0 walks: a single `vector_meta` row-count check."""
        return vector_store_is_empty(self._layout.vectors_db_path)

    @property
    def stale_indexes(self) -> tuple[str, ...]:
        """1 walk (`bundle_manifest_hash`), memoized, and skipped entirely
        when neither manifest-gated store is on disk -- read only by tier 2,
        so tier 1's zero-walk cost contract is untouched. Never raises: an
        advisory that breaks `next` would be worse than the staleness it
        reports."""
        if self._stale_indexes is None:
            try:
                self._stale_indexes = stale_derived_stores(
                    self._layout.bundle_dir,
                    (
                        ("fts", self._layout.fts_db_path),
                        ("graph", self._layout.graph_db_path),
                    ),
                )
            except Exception:  # broad: an advisory never breaks its command
                self._stale_indexes = ()
        return self._stale_indexes

    @property
    def docs(self) -> list[lint_check.LintDoc]:
        """1 walk (`lint_check.collect_docs`), memoized so tiers 2 and 3
        sharing this property never trigger a second call. The same call's
        skip notices are retained rather than discarded (#275): they name
        the documents this list is missing, so dropping them would leave a
        partial `docs` indistinguishable from a complete one."""
        if self._docs is None:
            docs, skip_notices = lint_check.collect_docs(self._layout.bundle_dir)
            self._docs = docs
            self._skip_notices = tuple(skip_notices)
        return self._docs

    @property
    def observed_skip_notices(self) -> tuple[str, ...]:
        """The notices this run actually paid for -- empty until `docs` has
        been read at least once. Reading this property NEVER triggers the
        walk itself: tier 1's zero-walk cost contract is structural, and a
        report of what was observed must not become a reason to observe
        more."""
        return self._skip_notices

    @property
    def exact_title_groups(self) -> list[CandidateGroup]:
        """2 further walks (`find_exact_title_groups`'s own
        `_iter_eligible` plus `lifecycle.deprecated_concept_ids`), read only
        by tier 4 -- the last and most expensive tier."""
        if self._exact_title_groups is None:
            self._exact_title_groups = list(
                find_exact_title_groups(self._layout.bundle_dir)
            )
        return self._exact_title_groups


_SAFE_ARGUMENT = re.compile(r"\A(?!-)[\w./-]+\Z")
"""A command argument `next` is willing to print. Deliberately excludes a
leading `-`: an argument position is for a resource path, and a value that
reads as an option would turn a recommendation into a different command
than the one the finding names."""


def _command_from_detail(detail: str, verb: str, *, takes_argument: bool) -> str | None:
    """Extract the command a finding's own detail text already spells, exactly
    as spelled -- no re-derivation from `doc.resource` or a hardcoded string
    (design: "tiers 2 and 3 read the command out of the finding they already
    carry").

    The detail is prose, and it interpolates values the bundle's own documents
    control: a Source's `resource` path, and a document's raw `sensitivity`
    frontmatter string, which is stored verbatim and is not restricted to the
    three known levels. Either can carry backticks of its own, and in the
    below-source-sensitivity detail the sensitivity value is interpolated
    BEFORE the command. So the leftmost backtick span is not necessarily the
    command -- taking it would let a document dictate the line printed after
    `Run:`.

    Therefore: scan every backtick span and accept only one that actually is
    the expected `openkos <verb>`. A verb that takes no argument is matched
    exactly -- otherwise a crafted value could append an option and change
    what the recommendation does, which is the same defect one step removed.
    A verb that does take one requires it to be a plain path. Anything else
    declines, and the caller moves on."""
    for match in re.finditer(r"`([^`]+)`", detail):
        command = match.group(1)
        if command == verb:
            return command
        if not takes_argument or not command.startswith(f"{verb} "):
            continue
        argument = command[len(verb) + 1 :]
        if _SAFE_ARGUMENT.fullmatch(argument):
            return command
    return None


def _tier_bootstrap_empty_bundle(signals: _BundleSignals) -> NextAction | None:
    """Rank 0 (#386): a bundle with zero eligible documents needs its FIRST
    ingest, not a reindex -- there is nothing to index, so recommending
    `openkos reindex` over an empty bundle is a runnable command that does
    nothing. This rung gates `_tier_missing_vector_index`: it checks the
    same zero-walk `vector_store_empty` signal first and declines outright
    when the index is populated, so the docs walk it needs (the SAME
    memoized `signals.docs` walk tiers 2/3 share -- never a new one) is
    paid only on runs that would otherwise recommend the meaningless
    reindex. That walk's skip notices then surface through the ordinary
    #275 path: a bundle whose only documents are unreadable is empty of
    ELIGIBLE documents, and the recommendation names the damage beside
    itself.

    The command carries a `<path>` placeholder rather than declining the
    way tier 2 does for a bare `openkos ingest`: tier 2's finding names a
    document whose own `resource` should have supplied the argument, while
    here no document exists anywhere -- only the user knows what to ingest
    first, and the placeholder says exactly where their answer goes."""
    if not signals.vector_store_empty:
        return None
    if signals.docs:
        return None
    return NextAction(
        command="openkos ingest <path>",
        reason=(
            "This bundle has no documents yet -- ingest your first source "
            "to give it something to index."
        ),
    )


def _tier_missing_vector_index(signals: _BundleSignals) -> NextAction | None:
    """Rank 1: missing or empty vector index. Ranked first among the
    content-presupposing tiers because it BLOCKS every later judgment, not
    because it is cheap; that it is also the cheapest check -- no walk of
    its own, though the rank-0 bootstrap gate has already paid the shared
    docs walk on every path that reaches this recommendation (#386) -- is
    corroboration (see the module docstring's ordering principle before
    reordering `_TIERS`)."""
    if not signals.vector_store_empty:
        return None
    return NextAction(
        command="openkos reindex",
        reason=(
            "Dense retrieval and candidate edges are unavailable -- the "
            "vector index is missing or empty."
        ),
    )


def _tier_stale_derived_indexes(signals: _BundleSignals) -> NextAction | None:
    """Rank 2: `fts.db`/`graph.db` describing an older bundle than the one on
    disk (#381). Ranked directly BELOW the missing-vector-index tier because
    both recommend the same command and only the reason can differ: an absent
    index blocks retrieval outright, a stale one merely degrades it, and the
    user should be told the worse of the two. Ranked ABOVE the content tiers
    because judging documents through indexes that predate them is work that
    may have to be redone.

    Only `reindex` and `purge` ever write these two stores; `relate`,
    `reconcile`, `merge`, `curate` and `ingest` all leave them behind. Until
    #381 the sole symptom was a quietly worse answer, which is precisely the
    kind of finding `next` exists to surface.

    Absence is deliberately not staleness (see `stale_derived_stores`): a
    freshly `init`ed workspace has no derived store at all, so this tier
    cannot become a second source of the empty-workspace `reindex`
    recommendation #386 already reports.
    """
    stale = signals.stale_indexes
    if not stale:
        return None
    return NextAction(
        command="openkos reindex",
        reason=(
            f"Retrieval is answering from indexes older than the bundle "
            f"({', '.join(stale)})."
        ),
    )


def _tier_unextracted_source(signals: _BundleSignals) -> NextAction | None:
    """Rank 2: unextracted source (`extraction_status: failed`). Ranked
    above tier 3 because this is knowledge ABSENT from the bundle, and
    absence outranks mislabelling: judging a label over a set still missing
    documents is work that may have to be redone (module docstring).

    Trap 2 (`check_unextracted`'s empty-`resource` fallback, `lint.py:632`):
    accepts only a command that carries an argument -- a bare `openkos
    ingest` with nothing after it is not runnable, so a finding whose
    extracted command has no argument is skipped rather than recommended,
    and evaluation continues to the next matching finding or the next
    tier. A resource path that is not a plain path is declined by
    `_command_from_detail` for the same reason: it would not be runnable as
    printed.

    Issue #274: reading the command out of the detail is necessary but not
    sufficient. `lint.py:630` interpolates `doc.resource` verbatim INSIDE
    the retry hint's backtick span, so a `resource` carrying a backtick of
    its own closes that span early and leaves a well-formed `openkos ingest
    <plain path>` behind -- a real command naming a DIFFERENT file than the
    finding is about. Counting backticks cannot detect this (two of them
    leave the total even and truncate just the same), so the extracted
    command is corroborated against the document's own `resource`: it is
    printed only when it is exactly the verb plus that value. The detail
    still supplies the command; the document decides whether the span the
    prose yielded was the whole of it.

    Issue #276: every one of those declinations is a REAL failed extraction,
    and dropping it silently leaves the user no trace of it at all. Each is
    therefore recorded, naming the document and which of the two repairs it
    needs -- record the missing path, or rename a file whose name cannot be
    spelled as an argument. The declination names the DOCUMENT and never
    echoes the raw `resource` back: reprinting the value tier 2 just refused
    to trust would reintroduce #274's defect one line lower."""
    docs_by_identity = {doc.identity: doc for doc in signals.docs}
    for finding in lint_check.check_unextracted(signals.docs):
        doc = docs_by_identity.get(finding.concept_id)
        if doc is None or not doc.resource:
            # bare `openkos ingest`, no argument -- trap 2
            signals.record_declination(
                f"{finding.concept_id}: extraction failed, but the document "
                "records no resource to re-ingest"
            )
            continue
        command = _command_from_detail(
            finding.detail, _INGEST_VERB, takes_argument=True
        )
        if command != f"{_INGEST_VERB} {doc.resource}":
            signals.record_declination(
                f"{finding.concept_id}: extraction failed, but its resource "
                "is not a runnable argument -- rename the file and re-ingest"
            )
            continue
        return NextAction(
            command=command,
            reason=f"{finding.concept_id}: {finding.detail}",
        )
    return None


def _tier_below_source_sensitivity(signals: _BundleSignals) -> NextAction | None:
    """Rank 3: below-source-sensitivity descendant. Ranked above tier 4
    because a mislabelled sensitivity is UNSAFE while a duplicate group is
    only ambiguous, and below tier 2 because the document is at least
    present (module docstring). Note that tiers 2 and 3 cost the same single
    shared walk, so cost cannot separate them at all -- this pair is the
    clearest evidence the order is not cost-derived.

    Trap 1 (`multi-source-uncovered`'s negating detail sentence,
    `lint.py:766-768`): filters on `finding.kind` BEFORE ever extracting a
    command, so a bundle carrying only a `multi-source-uncovered` finding
    never surfaces the `openkos backfill-sensitivity` command that
    finding's detail names only to rule out.

    This detail interpolates the document's raw `sensitivity` string before
    the command, so the command is matched by verb rather than by position --
    see `_command_from_detail`."""
    for finding in lint_check.check_below_source_sensitivity(signals.docs):
        if finding.kind != "below-source-sensitivity":
            continue
        command = _command_from_detail(
            finding.detail, _BACKFILL_VERB, takes_argument=False
        )
        if command is None:
            continue
        return NextAction(
            command=command,
            reason=f"{finding.concept_id}: {finding.detail}",
        )
    return None


def _tier_duplicate_groups(signals: _BundleSignals) -> NextAction | None:
    """Rank 4: pending exact-title duplicate group. Ranked last because it is
    merely AMBIGUOUS -- everything it concerns is present and correctly
    labelled, and the ambiguity cannot be judged well over an incomplete set
    anyway (module docstring's ordering principle). That it is also the most
    expensive check (two further walks), and the only tier requiring human
    judgment before any write, corroborates the position without setting it.

    The command is `openkos curate` -- the consolidated loop (#266) that
    RESOLVES pending groups, with `merge` as the manual path -- not the
    read-only `openkos duplicates` display, which a recommendation must not
    present as the action itself (#386). The reason still points at
    `duplicates` as the way to review the groups before curating.

    Reports only the count of the finding that fired, never a count of
    findings `next` never walked far enough to see (D5)."""
    groups = signals.exact_title_groups
    if not groups:
        return None
    count = len(groups)
    return NextAction(
        command="openkos curate",
        reason=(
            f"{count} candidate group{_plural(count)} with identical "
            f"titles {_agree(count, 'is', 'are')} pending review. "
            "Review them first with `openkos duplicates`."
        ),
    )


Tier = Callable[[_BundleSignals], NextAction | None]

_TIERS: tuple[Tier, ...] = (
    _tier_bootstrap_empty_bundle,
    _tier_missing_vector_index,
    _tier_stale_derived_indexes,
    _tier_unextracted_source,
    _tier_below_source_sensitivity,
    _tier_duplicate_groups,
)
"""D1 order: ingest-first (empty bundle, #386), reindex (missing), reindex
(stale, #381), ingest, backfill-sensitivity, curate. A higher-ranked tier's
finding always wins; a lower-ranked tier is never even evaluated once a
higher one fires (first-hit short-circuit) -- which is also what keeps the
two reindex tiers from ever both firing: a missing index short-circuits
before the stale check's bundle walk is ever paid."""


def next_action(layout: config.WorkspaceLayout) -> NextResult:
    """Evaluate `_TIERS` in order, first hit wins. A `None` `action` means
    no ranked tier produced a finding -- not that the bundle is clean (D4).
    The result also carries whatever skip notices the run happened to
    observe, which is none at all when a tier fired before the docs walk
    was ever paid."""
    signals = _BundleSignals(layout)
    action: NextAction | None = None
    for tier in _TIERS:
        action = tier(signals)
        if action is not None:
            break
    return NextResult(
        action=action,
        declinations=signals.observed_declinations,
        skip_notices=signals.observed_skip_notices,
    )


def _declination_lines(declinations: tuple[str, ...]) -> list[str]:
    """Name every finding the run refused to act on. Deliberately carries no
    count: `No Count of Unseen Findings` bans a numeral standing in for
    findings `next` never enumerated, and these are enumerated in full --
    but a header counting them would add nothing the list does not already
    say, so there is no reason to sail close to that line."""
    if not declinations:
        return []
    return [
        "Seen but not recommended -- no runnable command could be derived:",
        *(f"  {declination}" for declination in declinations),
    ]


def _skip_notice_lines(notices: tuple[str, ...]) -> list[str]:
    """Name every skipped document, not just how many there were: each one
    is a separate file to go and look at, so a bare count would report the
    damage while hiding where it is."""
    if not notices:
        return []
    count = len(notices)
    header = (
        f"{count} document{_plural(count)} could not be read and "
        f"{_agree(count, 'was', 'were')} skipped:"
    )
    return [header, *(f"  {notice}" for notice in notices)]


def render_lines(result: NextResult) -> list[str]:
    """Render `result` as human-readable lines. The skip notices and
    `_STATUS_POINTER` are both appended at this one site, after both
    branches, so the honesty guard (D4) is emitted on every path by
    construction -- a damaged bundle is named whether or not a tier fired,
    because an action derived from a knowingly incomplete document set is
    exactly as much in need of the caveat as no action at all."""
    lines: list[str] = []
    if result.action is None:
        lines.append(_NO_ACTION_LINE)
    else:
        lines.append(f"Run: {result.action.command}")
        lines.append(result.action.reason)
    lines.extend(_declination_lines(result.declinations))
    lines.extend(_skip_notice_lines(result.skip_notices))
    lines.append(_STATUS_POINTER)
    return lines
