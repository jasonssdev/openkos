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
4. a duplicate group is present, correctly labelled, and merely ambiguous;
5. a non-NFC on-disk name (#491) is none of those -- the bundle WORKS, since
   `okf.concept_path_for` resolves an NFC id against a decomposed file. It
   is hygiene, and hygiene outranks nothing.

Absence outranks ambiguity BECAUSE the ambiguity cannot be judged correctly
over an incomplete set -- adjudicating duplicates before the missing
documents are in is work that may have to be redone.

Cost order corroborates this ranking (tier 1 is also the cheapest, tiers 4
and 5 the most expensive) but DOES NOT DRIVE IT, and the individual tier
docstrings below mention only cost because that is what their own
implementation has to honour. Do not infer from them that the sequence is
cost-derived and reorder on cost grounds: a cheaper check that answers a
less blocking question still belongs lower.

`status` (`cli/main.py:5209-5353`) is never touched, imported for its
control flow, or refactored (design D2): every signal this module reads
comes from a function `status`/`lint` already ship
(`vector_store_is_empty`, `lint_check.collect_docs` + its checks,
`find_exact_title_groups`, `lint_check.scan_non_nfc_entries`), so no walk
logic is duplicated and the two
verbs can drift in framing without drifting in truth. ONE signal is sourced
outside that set: `walk_incomplete` reads `okf._walk_errors` directly (#486),
because neither `status` nor `lint` exposes the unlistable-directory signal
as a function this module could call -- `status` folds it into its own
rendered section. The principle it serves is the same one, though: the walk
logic still lives in `okf`, and this module only reads its result.

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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from openkos import config
from openkos import lint as lint_check
from openkos.bundle import decisions as bundle_decisions
from openkos.model import okf
from openkos.resolution import CandidateGroup, find_exact_title_groups
from openkos.resolution.contradiction import is_high_confidence_finding
from openkos.state import derived, findings
from openkos.state.derived import stale_derived_stores
from openkos.state.vectorstore import content_hash, vector_store_is_empty

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


def fts_index_present(path: Path) -> bool:
    """Whether the on-disk FTS index exists at `path` (#553). Absent-only,
    mirroring `doctor`'s workspace presence checks -- staleness belongs to
    `stale_derived_stores`, which deliberately never reports absence.

    A module-level public seam (not an inline `Path.exists()` in the
    signal property) for the same reason `vector_store_is_empty` is one:
    this module's tests patch PUBLIC attributes only, never private
    internals (module docstring), and tests targeting lower tiers need a
    sanctioned way to hold this tier open."""
    return path.exists()


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
    """The exact, runnable command string -- verbatim, never re-derived.

    ONE tier is exempt, and only one (#486): the rank-0 bootstrap rung
    returns `openkos ingest <path>` with a literal placeholder. Its own
    docstring holds the reasoning; this sentence exists so the exception is
    discoverable from the contract it bends. Every other tier's command is
    runnable as printed. If a SECOND templated tier ever appears, replace
    this exception with a structured `placeholder: bool` rather than growing
    the list -- one exception is worth a sentence, two are worth a field."""
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
        self._walk_incomplete: bool | None = None
        self._non_nfc_entries: list[lint_check.NonNfcEntry] | None = None
        self._open_contradictions: tuple[findings.PersistedFinding, ...] | None = None

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
    def fts_index_present(self) -> bool:
        """0 walks: one `Path.exists()` check on `.openkos/fts.db` (#553),
        via the module-level public seam `fts_index_present`."""
        return fts_index_present(self._layout.fts_db_path)

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
    def walk_incomplete(self) -> bool:
        """1 walk (`okf._walk_errors`), memoized, and read by the rank-0
        bootstrap tier ALONE -- after that tier's two zero-walk gates have
        already decided the bundle looks empty (#486).

        Deliberately not folded into `docs`: `lint_check.collect_docs`
        reports the documents it could not READ, never the directories it
        could not LIST, so an unlistable subtree leaves `docs` empty with no
        skip notice at all. That is the exact gap that let a populated
        bundle read as a fresh one.

        This is a SECOND full traversal, not a free one, and the honest
        bound is where it is paid rather than how big it is: an empty `docs`
        means zero ELIGIBLE documents, never a small tree -- reserved files,
        non-document assets and deep nesting are all still walked. What the
        cost contract preserves is that no other tier's budget moves, and
        that this one is reached only on a run already headed for the
        bootstrap recommendation.

        Unwrapped, like the helper's two other callers: `okf._walk_errors`
        hands `os.walk` an `onerror` collector, so a directory-scan failure
        becomes an entry in its result rather than an exception. There is no
        `OSError` here left to catch, and swallowing anything else would
        report "the walk was complete" -- the precise claim this property
        exists to stop the bootstrap tier from making."""
        if self._walk_incomplete is None:
            self._walk_incomplete = bool(okf._walk_errors(self._layout.bundle_dir))
        return self._walk_incomplete

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
            # #797: a group the human ruled distinct must not keep routing
            # `next` back to `curate`. Before this, declining the merge left
            # the workspace in the state that recommends `curate`, whose
            # Identity stage re-offers the same merge -- a loop whose only
            # exit was performing the merge the human had refused.
            self._exact_title_groups = [
                group
                for group in find_exact_title_groups(self._layout.bundle_dir)
                if not _is_group_kept_distinct(self._layout, group.member_ids)
            ]
        return self._exact_title_groups

    @property
    def non_nfc_entries(self) -> list[lint_check.NonNfcEntry]:
        """1 walk (`lint_check.scan_non_nfc_entries`), memoized, read by the
        LAST tier alone (#491).

        A names-only traversal: `scan_non_nfc_entries` pulls
        `bundle_dir.rglob("*")` and reads NAMES, never opening a file
        (`lint.py`'s own docstring makes that the reason it is not a
        violation of the no-fifth-walk guard). So it is cheaper per entry
        than `docs`, which reads and parses every one.

        It also cannot ride on `docs`: `collect_docs` surfaces only
        readable, parseable `.md` documents, while a decomposed name can
        sit on a directory or a non-`.md` file, neither of which that walk
        would ever report.

        The cost argument is placement, not caching -- the same one
        `walk_incomplete` makes for its own extra traversal. This property
        is reached only when every ranked tier above came up empty, so no
        other tier's budget moves and a bundle with real work pending never
        pays for it. That is why #491's proposed persistent
        "last known non-NFC count" cache is not needed: the walk this
        module has to avoid is the one on a busy bundle, and ranking last
        avoids it outright rather than making it cheaper."""
        if self._non_nfc_entries is None:
            self._non_nfc_entries = list(
                lint_check.scan_non_nfc_entries(self._layout.bundle_dir)
            )
        return self._non_nfc_entries

    @property
    def open_contradictions(self) -> tuple[findings.PersistedFinding, ...]:
        """Every persisted finding that is a **high-confidence
        contradiction** (`is_high_confidence_finding` -- CONTRADICTS at or
        above the shared display threshold, #639), **open** (no `declined`
        decision) **and** not stale (design Decision 6), read only by the
        LAST tier -- mirrors `non_nfc_entries`'s own
        reached-only-when-everything-above-is-clean placement.

        The verdict filter exists because curate persists EVERY judged
        verdict, `consistent` included (provenance, not pending work) --
        without it this property ranked pairs already judged consistent as
        "open contradictions" and nothing could clear them (#639). It is
        the same predicate `contradictions`, `reconcile --from-findings`,
        and `cli.main._contradiction_finding_counts` apply, so `next` and
        `status` can never disagree about what is outstanding.

        `.openkos/findings.db`'s pure-derivation contract (`config.
        WorkspaceLayout.findings_db_path`'s own docstring: "this property
        never creates anything on disk by itself") is honoured here by
        checking `path.exists()` BEFORE `derived.open_derived_connection`,
        which would otherwise lazily create an empty file on a workspace
        that has never run `curate`'s Contradictions stage -- the same
        guard `vector_store_is_empty` uses for `vectors_db_path`.

        The `declined` join happens per finding, by recomputing
        `bundle_decisions.decision_key_for` from the finding's own
        `pair_ids`/`merged_absorbed_id` (design Decision 7's read-time
        join -- no stored cross-pointer either store owns)."""
        if self._open_contradictions is None:
            findings_db_path = self._layout.findings_db_path
            if not findings_db_path.exists():
                self._open_contradictions = ()
                return self._open_contradictions
            conn = derived.open_derived_connection(findings_db_path)
            try:
                persisted = findings.open_findings(
                    conn,
                    current_digest=_current_finding_digest(self._layout.bundle_dir),
                )
            finally:
                conn.close()
            self._open_contradictions = tuple(
                finding
                for finding in persisted
                if is_high_confidence_finding(finding.verdict, finding.confidence)
                and not finding.stale
                and not _is_contradiction_declined(self._layout, finding)
            )
        return self._open_contradictions


def _current_finding_digest(bundle_dir: Path) -> Callable[[str], str | None]:
    """A `state.findings.open_findings`-compatible `current_digest`
    callback: reads `input_ref` as a concept id and content-hashes its
    CURRENT bytes (design Decision 2). Local to this module rather than
    imported from `cli.main`'s own `_current_finding_digest`: `cli.main`
    imports `next_action` (it calls `next_action.next_action`), so the
    reverse import would be circular. An unreadable or non-file
    `input_ref` -- a merged-body candidate's synthetic ledger-snapshot
    label, or a concept removed since the finding was recorded -- answers
    `None`, "cannot currently determine", never evidence of drift
    (`state.findings._is_stale`'s own contract)."""

    def _digest(input_ref: str) -> str | None:
        try:
            raw = okf.concept_path_for(input_ref, bundle_dir).read_bytes()
        except OSError:
            return None
        return content_hash(raw)

    return _digest


def _is_group_kept_distinct(
    layout: config.WorkspaceLayout, member_ids: Sequence[str]
) -> bool:
    """`True` iff a human has ruled this exact member set distinct and has
    not reopened it (#797). Local to this module for the same
    circular-import reason as `_is_contradiction_declined` -- `cli.main`
    imports this module, so the reverse would be circular, and the two
    copies must stay behaviourally identical."""
    members = tuple(sorted(member_ids))
    key = bundle_decisions.identity_decision_key_for(members)
    for record in bundle_decisions.read_identity_decisions(
        members[0], layout.bundle_dir
    ):
        if record.decision_key == key:
            return record.state == "declined"
    return False


def _is_contradiction_declined(
    layout: config.WorkspaceLayout, finding: "findings.PersistedFinding"
) -> bool:
    """`True` iff a decision record for `finding`'s own
    `pair_ids`/`merged_absorbed_id` exists and its `state` is `declined`
    (pending-work spec: "Declined Findings Are Hidden By Default"). Local
    to this module for the same circular-import reason as
    `_current_finding_digest`."""
    key = bundle_decisions.decision_key_for(
        finding.pair_ids, finding.merged_absorbed_id
    )
    for record in bundle_decisions.read_decisions(
        finding.pair_ids[0], layout.bundle_dir
    ):
        if record.decision_key == key:
            return record.state == "declined"
    return False


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
    if signals.walk_incomplete:
        # "Empty" is now a claim this run cannot make (#486): the walk that
        # produced zero documents provably could not list part of the
        # bundle, so a populated subtree may be sitting right there. Recommend
        # the verb that NAMES the unreadable directory rather than a first
        # ingest that would be advice for a different bundle.
        return NextAction(
            command="openkos status",
            reason=(
                "This bundle looks empty, but at least one directory could "
                "not be read -- check which one before ingesting anything."
            ),
        )
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


def _tier_missing_fts_index(signals: _BundleSignals) -> NextAction | None:
    """Rank 1b (#553): missing on-disk FTS index. Ranked directly BELOW the
    missing-vector-index tier (same command, and when BOTH are missing the
    user is told about the vector index, whose absence also blocks candidate
    edges) and ABOVE staleness (an absent index blocks the lexical channel
    outright; a stale one merely degrades it).

    Absence-only, zero walks: one `Path.exists()` check, the same
    absent-only posture `doctor`'s workspace checks take. Staleness stays
    `_tier_stale_derived_indexes`' job. This tier exists because
    `stale_derived_stores` DELIBERATELY does not report absence (a store
    that was never built is not "stale"), which left #553's exact shape --
    vectors populated by ingest's embed, `fts.db` never built -- invisible
    to every tier, so `next` recommended curation over a bundle answering
    every query dense-only."""
    if signals.fts_index_present:
        return None
    return NextAction(
        command="openkos reindex",
        reason=(
            "Lexical (full-text) retrieval is unavailable -- the FTS index is missing."
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


def _tier_unjudged_extraction(signals: _BundleSignals) -> NextAction | None:
    """Rank between unextracted source and below-source sensitivity:
    derived objects stored WITHOUT judge selection (#772's quarantine
    tokens, read via `lint_check.check_unjudged`) are knowledge that
    passed no quality gate yet feeds retrieval, adjudication, and the
    graph -- and the repair is computed, one-command, and self-clearing
    (issue #868). Under the module's ordering principle that puts it below
    a FAILED extraction (absence outranks unvetted presence: tier 5's
    documents are still missing) and above a mislabelled sensitivity or a
    pending duplicate group, which are label problems over content that
    did pass its gates.

    Everything else is `_tier_unextracted_source`'s contract verbatim, on
    purpose: the detail comes from the same `_ingest_retry_hint` remedy
    (`lint.py`, shared by `check_unextracted` and `check_unjudged` so the
    two debts cannot drift on how they spell it), so the #274 corroboration
    against `doc.resource`, trap 2's bare-command declination, and #276's
    named declinations all apply unchanged. The recommended command became
    RUNNABLE with #865 -- `ingest raw/<name>` now resolves to the owning
    Source's re-ingest instead of duplicating it -- which is why this tier
    lands after that fix rather than with the token (#868: "the tier
    should land with, or after, the remedy fix")."""
    docs_by_identity = {doc.identity: doc for doc in signals.docs}
    for finding in lint_check.check_unjudged(signals.docs):
        doc = docs_by_identity.get(finding.concept_id)
        if doc is None or not doc.resource:
            signals.record_declination(
                f"{finding.concept_id}: derived objects lack judge "
                "selection, but the document records no resource to "
                "re-ingest"
            )
            continue
        command = _command_from_detail(
            finding.detail, _INGEST_VERB, takes_argument=True
        )
        if command != f"{_INGEST_VERB} {doc.resource}":
            signals.record_declination(
                f"{finding.concept_id}: derived objects lack judge "
                "selection, but its resource is not a runnable argument -- "
                "rename the file and re-ingest"
            )
            continue
        return NextAction(
            command=command,
            reason=f"{finding.concept_id}: {finding.detail}",
        )
    return None


def _tier_below_source_sensitivity(signals: _BundleSignals) -> NextAction | None:
    """Rank 3: below-source-sensitivity descendant. Ranked above tier 4 --
    its single-document sibling (#693), where this tier's sweep repairs a
    whole closure at once -- and above tier 5 because a mislabelled
    sensitivity is UNSAFE while a duplicate group is only ambiguous, and
    below tier 2 because the document is at least present (module
    docstring). Note that tiers 2, 3 and 4 all cost the same single shared
    walk, so cost cannot separate them at all -- this group is the clearest
    evidence the order is not cost-derived.

    Trap 1 (`multi-source-uncovered`'s detail sentence): filters on
    `finding.kind` BEFORE ever extracting a command, so a
    `multi-source-uncovered` finding never reaches this tier's extraction at
    all -- it belongs to tier 4, which reads a structured `remediation`
    rather than prose. Since #693 that detail no longer spells `openkos
    backfill-sensitivity` as a runnable span either, so the negated command
    is now unreachable from both directions rather than one.

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


def _tier_multi_source_uncovered(signals: _BundleSignals) -> NextAction | None:
    """Rank 4 (#693): a `multi-source-uncovered` document, resolvable with
    `openkos set-sensitivity`.

    `status` has listed this finding under *Needs attention* since 0.2.5
    while `next`, run seconds later against the same unchanged bundle,
    reported nothing to do. Both verbs answer "what should I do?", so they
    were not offering two views of one truth -- they were contradicting each
    other. This tier is what makes them agree.

    Ranked immediately BELOW tier 3 and above tier 5 (duplicate groups).
    Both sensitivity tiers are already paid for by the same memoized
    `signals.docs` walk, so cost cannot order them: `below-source-sensitivity`
    wins because `backfill-sensitivity` repairs a whole closure in one sweep
    while `set-sensitivity` repairs exactly one document. Recommending the
    single-document fix while a sweep is pending would send the operator the
    long way round. It stays above tier 5 for the same reason tier 3 does --
    a mislabelled sensitivity is UNSAFE, a duplicate group is only ambiguous.

    Reads `finding.remediation`, NEVER the detail prose. Tier 2 has to
    corroborate its extracted command against the document's own `resource`
    (#274) precisely because the detail interpolates document-controlled
    values, and this kind's detail interpolates three of them -- a raw
    `sensitivity` string and every cited id and level. `remediation` is
    computed by `lint` from the document's identity and the engine's own
    high-water-mark, so there is no prose for a document to forge, and the
    guard tier 2 needs does not have to be reinvented here.

    An id that cannot be spelled as a bare argument yields an empty
    `remediation`, and this tier DECLINES it out loud rather than dropping it
    (#276): the mislabelling is real whether or not a one-line command exists
    for it.

    Like tier 2, it returns on the FIRST finding it can recommend, so an
    unspellable finding sorted after that one is never reached and never
    declared. That is deliberate and not a gap: `next` recommends one action
    and has never claimed to enumerate everything it did not reach -- the
    standing `_STATUS_POINTER` contract (D4) is what covers the remainder,
    and `status` lists every such finding. Declining silently would be the
    defect; not walking past a hit is the design."""
    for finding in lint_check.check_below_source_sensitivity(signals.docs):
        if finding.kind != "multi-source-uncovered":
            continue
        if not finding.remediation:
            signals.record_declination(
                f"{finding.concept_id}: sensitivity sits below its cited "
                "concepts, but its id is not a runnable argument -- rename "
                "it, or run `openkos set-sensitivity` by hand"
            )
            continue
        return NextAction(
            command=finding.remediation,
            reason=f"{finding.concept_id}: {finding.detail}",
        )
    return None


def _tier_duplicate_groups(signals: _BundleSignals) -> NextAction | None:
    """Rank 5: pending exact-title duplicate group. Ranked below every
    sensitivity tier because it is
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
    findings `next` never walked far enough to see (D5).

    Recall bound (issue #593): this tier counts the identical-title family
    ONLY. `resolution/candidates.py` produces two further tiers -- acronym
    and near-match -- whose pairwise scoring was measured at 5.3s for 400
    docs / 24.8s for 1000, a cost `next` must not pay (the module ordering
    already calls this the most expensive check at TWO walks). A
    near-match-only backlog therefore never fires this tier; that is
    covered by D4's standing contract (`_NO_ACTION_LINE` means no tier
    fired, never that the bundle is clean) plus `status`'s #593 disclosure
    line naming `openkos duplicates` as the full scan."""
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


def _tier_non_nfc_names(signals: _BundleSignals) -> NextAction | None:
    """Rank 6: on-disk names that are not NFC (issue #491).

    Ranked LAST, below even the duplicate-groups tier, and the position is
    the whole cost argument. A decomposed filename blocks nothing, is not
    missing, and is not unsafe -- `okf.concept_path_for` already resolves
    an NFC id against a decomposed file, so the bundle WORKS. It is
    hygiene: the spelling on disk disagrees with the canonical id, which
    every other verb uses. By the module's ordering principle -- what
    blocks, then what is missing, then what is unsafe, then what is merely
    ambiguous -- hygiene outranks nothing.

    Ranking it last is also what makes its extra traversal defensible
    without any persistent cache, exactly as `walk_incomplete` argues for
    its own: the walk is reached only on a run where every ranked tier
    above found nothing, so no other tier's budget moves and a bundle with
    real work pending never pays it.

    The command is `openkos normalize-names` (#474 part 2), which
    remediates precisely what `lint`'s `non-nfc-name` finding reports, and
    reads from the SAME `scan_non_nfc_entries` -- so the recommendation
    and the verb can never disagree about what is offending.

    Reports only the count of the finding that fired (D5)."""
    entries = signals.non_nfc_entries
    if not entries:
        return None
    count = len(entries)
    return NextAction(
        command="openkos normalize-names",
        reason=(
            f"{count} on-disk name{_plural(count)} "
            f"{_agree(count, 'is', 'are')} not NFC, so the spelling on disk "
            "disagrees with the canonical id. Review them first with "
            "`openkos lint`."
        ),
    )


def _tier_open_contradictions(signals: _BundleSignals) -> NextAction | None:
    """Rank 7, LAST (pending-work design, Decision 6): a persisted
    contradiction finding that is open, non-stale, and non-declined.

    Ranked after everything else for the same reason `_tier_non_nfc_names`
    is: acting on a contradiction judges what is already present and
    correctly labelled, so it cannot outrank a tier that reports content
    missing, unsafe, or merely ambiguous -- and it is reached only once
    every tier above it has declined, so `open_contradictions`'s own walk
    (an `.openkos/findings.db` read plus one decision-sidecar read per
    finding) is never paid on a bundle with real work pending higher up.

    THE HONESTY GUARD IS THE POINT OF THIS TIER, NOT A SIDE CONDITION
    (design Decision 6): `open_contradictions` already filters to
    high-confidence CONTRADICTS ∧ open ∧ not stale ∧ not declined, so a
    finding that is consistent, low-confidence, stale, or declined simply
    never appears in the tuple this reads -- there is no separate check to
    get wrong here, and this docstring exists so a future edit widening
    that filter is recognised as breaking the guarantee it protects rather
    than as a harmless tweak. The verdict condition is part of that guard,
    not an optimisation (#639): curate persists every judged verdict, and
    dropping the condition makes this tier tell an operator to review pairs
    already judged consistent -- a recommendation nothing can clear. When every persisted finding is stale or
    declined, this tier returns `None` exactly like every other tier that
    finds nothing to recommend -- the module's `None`-action contract
    (`next_action`'s own docstring, `:616-621`) and `_NO_ACTION_LINE`
    (`:77-80`) are untouched, and `_STATUS_POINTER` (`:71-75`) is still
    appended by `render_lines` on every path. A `None` action here means
    only "no ranked tier fired", never "no contradictions exist"."""
    findings_ = signals.open_contradictions
    if not findings_:
        return None
    finding = findings_[0]
    source_id, target_id = finding.pair_ids
    return NextAction(
        command="openkos contradictions",
        reason=(
            # Fixed wording, not `finding.verdict`: after the #639 filter
            # the verdict here is always `contradicts`, and interpolating
            # it printed the raw enum value ("an open contradicts
            # finding").
            f"{source_id} <-> {target_id}: an open contradiction finding "
            f"is pending review (confidence: {finding.confidence:.2f})."
        ),
    )


Tier = Callable[[_BundleSignals], NextAction | None]

_TIERS: tuple[Tier, ...] = (
    _tier_bootstrap_empty_bundle,
    _tier_missing_vector_index,
    _tier_missing_fts_index,
    _tier_stale_derived_indexes,
    _tier_unextracted_source,
    _tier_unjudged_extraction,
    _tier_below_source_sensitivity,
    _tier_multi_source_uncovered,
    _tier_duplicate_groups,
    _tier_non_nfc_names,
    _tier_open_contradictions,
)
"""D1 order: ingest-first (empty bundle, #386), reindex (missing vectors),
reindex (missing FTS, #553), reindex (stale, #381), ingest (failed
extraction), ingest (judge debt, #868), backfill-sensitivity,
set-sensitivity (#693), curate, normalize-names,
contradictions (durable-pending-work, Decision 6). A higher-ranked tier's finding always
wins; a lower-ranked tier is never even evaluated once a higher one fires
(first-hit short-circuit) -- which is also what keeps the three reindex
tiers from ever all firing: a missing index short-circuits before the
stale check's bundle walk is ever paid."""


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
