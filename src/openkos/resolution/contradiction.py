"""Read-only, config-free LLM contradiction-detection precision layer over
graph typed edges (MVP-2 slice 3, freshness-lint-v1 S3).

Mirrors `resolution/edge_typing.py` one layer over: `find_contradictions`
OWNS the `openkos.graph` read internally -- opens `sqlite_graph.build_graph`,
derives candidate pairs from TYPED edges only (`relation_type is not None`),
EXCLUDING `derived_from`-typed edges (#135: a derivation/provenance link is
never a contradiction candidate, whether graph-projection-synthesized or
hand-authored), and judges each remaining already-related concept pair via
an injected `LLMBackend`
into a `CONTRADICTS`/`CONSISTENT`/`UNCERTAIN` verdict with confidence,
rationale, and cited conflicting claims. Verdicts are advisory, for human
review only -- this module never writes, merges, or reconciles.

Config-free leaf (mirrors `adjudication.py`, `edge_typing.py`,
`extraction/concept.py`, and `retrieval/answer.py`): this module never
imports `openkos.config`; the caller supplies an `LLMBackend`, never an
`OllamaClient` constructed here. Importing the `OllamaError` TYPE from
`openkos.llm.ollama` keeps that discipline intact: `ollama.py` is itself a
config-free stdlib leaf, and the error family is the failure contract every
`LLMBackend` caller already speaks.

An `OllamaError`-family exception raised by `llm.chat` mid-loop STOPS the
loop but never discards paid-for work (issue #441): each judged candidate
cost one real LLM call, so `find_contradictions` returns a
`ContradictionBatch` carrying every completed `ContradictionVerdict` (input
order, one per judged candidate) plus the failure that stopped the loop and
the 1-based index of the candidate whose chat raised. A complete run
returns `failure=None`. Only PARSING and VALIDATION failures degrade a
single pair's verdict -- those never stop the loop, and no judged pair is
ever skipped or dropped: the batch holds exactly one `ContradictionVerdict`
per JUDGED candidate (after the `_MAX_PAIRS` cap), in the same
deterministic order.

Layering: this module is DERIVED, not canonical -- it MAY import
`openkos.graph` (derived -> derived, allowed). The live, tested constraint is
narrower than an earlier version of this docstring claimed: the canonical
layer (`openkos.model`, `openkos.bundle`, `openkos.state`) MUST NOT import
`openkos.graph` (`tests/unit/graph/test_base.py::test_canonical_layer_does_not_import_graph`),
and `graph/` MUST NOT register a CLI verb
(`tests/unit/graph/test_analysis.py::test_cli_main_registers_no_graph_command`).
`cli/main.py` importing `openkos.graph` is NOT a violation and is established
practice (`query`, `reindex`, and -- since graph-projection-reuse -- the shared
per-invocation `build_graph` this module's optional `store` parameter accepts).

status-aware-retrieval (MVP-3 gap #8 · S1, Phase 3): unless the caller
passes `include_deprecated=True`, `find_contradictions` computes the shared
`openkos.lifecycle.deprecated_concept_ids(bundle_dir)` predicate ONCE per
call and drops any candidate pair touching a deprecated/superseded concept
id (either side) BEFORE judging -- a superseded concept never appears in a
candidate pair (spec: Superseded concept absent from contradiction
candidates). `include_deprecated=True` skips the predicate walk entirely (no
`_iter_docs` pass), restoring today's status-blind behavior byte-for-byte
(design R1's zero-cost escape path).

Post-review HIGH correction: deprecation filtering happens INSIDE
`_candidate_pairs`, BEFORE the `_MAX_PAIRS` cap slice, not after it. The
original implementation filtered the already-capped `pairs` list, which let
deprecated-touching pairs consume cap slots ahead of live pairs sorting
past `_MAX_PAIRS` -- starving those live pairs of judgment even when the
200-pair budget had unused capacity once deprecation filtering was applied.
`total_pair_count` now reflects `_candidate_pairs`'s deduped,
deprecation-filtered ("live") count BEFORE the cap, so the existing
`total_pair_count > len(verdicts)` cap-reached signal fires only when live
pairs genuinely exceed the cap.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from openkos import lifecycle, sensitivity
from openkos.graph.base import GraphStore
from openkos.graph.sqlite_graph import CandidateSource, build_graph
from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.llm.ollama import OllamaError
from openkos.model import okf

_MAX_PAIRS = 200
"""Hard cap on the number of deduped candidate pairs judged in one
`find_contradictions` run. Typed-edge pairs are sparse; ~2-5s/`llm.chat`
locally means 200 bounds a run to roughly 10-15 minutes while exceeding
realistic typed-edge counts. Truncation to this cap is NEVER silent -- the
caller receives the full deduped total alongside the (possibly capped)
verdict list, and the CLI verb reports "N of M pairs shown (cap reached)"
when the two differ (spec: Cap truncation is reported)."""

_MERGED_BODY_FLOOR = 50
"""Slots within `_MAX_PAIRS` that merged-body candidates cannot be crowded
out of (issue #444).

Typed-edge candidates used to fill the shared budget unconditionally first,
so any corpus whose live typed-edge pairs alone reached `_MAX_PAIRS` judged
ZERO merged-body candidates -- and the corpora that exhaust the typed-edge
budget are the busiest, longest-lived ones, which are also the ones most
likely to have accumulated merges. #409's detection went silent in exactly
the situation it was built for.

This is a MINIMUM, not a quota: merged-body candidates take every slot typed
edges leave unused, and only `min(len(merged_specs), _MERGED_BODY_FLOOR)` is
ever withheld from typed edges, so a corpus with two merges gives up two
typed-edge slots rather than fifty. 50 is a quarter of the budget, chosen
against measurement rather than taste: real end-to-end runs produced 8-16
typed-edge pairs (see #379's cost measurement), so the remaining 150 slots
stay far above anything a real corpus has reached, while the merged-body
count is bounded by the bundle's total merge-ledger entries."""

_CONFIDENCE_DISPLAY_THRESHOLD = 0.7
"""Precision-first display threshold: the default CLI report shows only
`CONTRADICTS` verdicts with confidence at or above this value, matching the
project's existing >=0.7 autonomous-confidence bar. `--all` bypasses this
filter entirely. Kept private -- callers use the public
`is_high_confidence_contradiction` helper instead of importing this
constant directly (no cross-import of an underscore symbol across module
boundaries)."""

_MALFORMED_REPLY_RATIONALE = (
    "malformed reply: could not parse a valid verdict JSON object"
)
"""Stable rationale for a reply that fails fail-closed parsing (mirrors
`adjudication.py`'s `_MALFORMED_REPLY_RATIONALE`)."""

_SYSTEM_PROMPT = (
    "You are a contradiction-detection adjudicator in a local-first "
    "knowledge engine. Given two RELATED concepts and the relation linking "
    "them, decide whether their content CONTRADICTS, is CONSISTENT, or the "
    "answer is UNCERTAIN. Assert contradicts ONLY when you can cite "
    "specific conflicting claims from both concepts; otherwise use "
    "consistent or uncertain.\n\n"
    "Return ONLY a JSON object, with NO prose, NO markdown, and NO code "
    "fences around it, matching exactly this shape:\n"
    '{"verdict": "contradicts"|"consistent"|"uncertain", '
    '"confidence": <0.0-1.0>, "rationale": "...", '
    '"conflicting_claims": ["...", ...]}'
)
"""Stable system half of the 2-message prompt (mirrors
`adjudication._SYSTEM_PROMPT`/`edge_typing._SYSTEM_PROMPT`): the closed
3-value verdict vocabulary, the citation requirement for `contradicts`, and
the JSON-only instruction baked into system text; the `user` message
carries both concept `[id — title]` headers, both full bodies, and the
`relation_type` linking them (design's LLM Prompt Contract)."""


class Verdict(Enum):
    """Contradiction-detection outcome for one candidate pair."""

    CONTRADICTS = "contradicts"
    """The pair's content is judged to contain specific, cited conflicting
    claims."""
    CONSISTENT = "consistent"
    """The pair's content is judged to be consistent, not contradictory."""
    UNCERTAIN = "uncertain"
    """The model could not confidently decide, the reply/content was
    insufficient, OR a `CONTRADICTS` verdict lacked cited claims
    (fail-closed degrade -- citation-gated precision)."""


@dataclass(frozen=True)
class ContradictionVerdict:
    """One candidate pair's contradiction-detection result. Ephemeral --
    never a persisted OKF type or `bundle`/`state` file."""

    pair_ids: tuple[str, str]
    """The pair's two concept ids, sorted (`tuple(sorted(pair))`)."""
    verdict: Verdict
    """`CONTRADICTS`, `CONSISTENT`, or `UNCERTAIN`."""
    confidence: float
    """Clamped to `[0.0, 1.0]`."""
    rationale: str
    """Free-text explanation; may be blank for a well-formed reply that
    omitted one, but is never blank on the malformed-reply degrade path."""
    conflicting_claims: tuple[str, ...]
    """Claims cited from the pair's content supporting a `CONTRADICTS`
    verdict. Empty for `CONSISTENT`/`UNCERTAIN`; a `CONTRADICTS` reply with
    empty/missing claims is coerced to `UNCERTAIN` instead (citation gate)."""
    merged_absorbed_id: str | None = None
    """`None` for a typed-edge candidate. For an intra-document (merged-body)
    candidate (surface-merged-body-contradictions, issue #409), the absorbed
    concept id this verdict compares against `pair_ids`' survivor -- i.e.
    the `MergeLedgerEntry.absorbed_id` this candidate was built from.

    WARNING: this field is the SOLE discriminator between a typed-edge
    candidate and a merged-body candidate. Branch on `merged_absorbed_id is
    not None` -- NEVER fall back to `pair_ids` equality.

    `pair_ids[0] == pair_ids[1]` is NOT a safe stand-in even though, since
    #411, a merged-body candidate is the only thing that produces it: that
    is a property of one filter line in `_candidate_pairs`
    (`edge.source_id != edge.target_id`), not of this dataclass. `_pair_key`
    still has no self-pair guard, and `okf.merge_relations` still leaves a
    pre-existing survivor-side self-loop untouched, so a `(x, x)` typed-edge
    pair remains one relaxed filter away from being reachable again. A
    consumer keyed on pair shape would silently start conflating
    self-loop-derived and merged-content verdicts on the day that happens;
    a consumer keyed on this field never can."""


@dataclass(frozen=True)
class ContradictionBatch:
    """Outcome of one `find_contradictions` run: every completed verdict
    plus, when the loop was cut short, the failure that stopped it (issue
    #441). Ephemeral, like `ContradictionVerdict` -- never a persisted OKF
    type or `bundle`/`state` file.

    Partials ride the RETURN, not an exception payload, on purpose (mirrors
    `adjudication.AdjudicationBatch`/`edge_typing.EdgeSuggestionBatch`): an
    exception-carried partial forces every caller into a try/except that
    must remember to salvage the results off the exception, and the one
    caller that forgets reintroduces exactly the work-discarding bug this
    type exists to fix. A return value cannot be silently dropped by an
    unhandled raise."""

    results: list[ContradictionVerdict]
    """Every completed verdict, in input order -- each one was fully paid
    for (its `llm.chat` call succeeded) before the loop stopped."""
    failure: OllamaError | None = None
    """The `OllamaError`-family exception that stopped the loop, or `None`
    for a complete run."""
    failed_index: int | None = None
    """1-based index of the candidate whose `llm.chat` raised `failure`;
    `None` when the run completed. The failed candidate produced no verdict
    and no `on_progress` call, and no later candidate was ever prompted."""


def _pair_key(source_id: str, target_id: str) -> tuple[str, str]:
    """Canonical unordered-pair key: `source_id`/`target_id` sorted --
    equivalent dedup semantics to `frozenset({source_id, target_id})`
    (spec), and directly usable as the deterministic ordering key
    (`tuple(sorted(pair))`)."""
    first, second = sorted((source_id, target_id))
    return first, second


def _candidate_pairs(
    store: GraphStore,
    deprecated: frozenset[str] = frozenset(),
    *,
    cap: int | None = _MAX_PAIRS,
) -> tuple[list[tuple[str, str]], int]:
    """Derive candidate pairs from `store`'s TYPED edges only
    (`relation_type is not None`), EXCLUDING any edge whose `relation_type
    == "derived_from"` (#135, provenance-mirror-typed-projection): a
    `derived_from` relationship is a derivation/provenance link, never a
    contradiction candidate -- regardless of whether it was synthesized by
    graph-projection provenance-mirror typing or hand-authored in
    `relations:` frontmatter, since this function has no signal to
    distinguish the two and a derivation is never a contradiction candidate
    either way. A typed SELF-LOOP (`source_id == target_id`) is excluded on
    the same line and for the same kind of reason (issue #411): a document
    cannot be a contradiction candidate against itself, and judging one loads
    the same body twice to spend a call on a question with no answer.

    Self-loops are filtered here rather than assumed impossible upstream
    because they are genuinely reachable: the `Relation` codec accepts them
    (only the interactive `relate` verb refuses to CREATE one), and
    `okf.merge_relations` deliberately leaves a pre-existing one untouched
    -- it drops only a self-loop a merge itself would newly synthesize while
    retargeting `absorbed_id -> survivor_id`.

    The drop is silent in `total_count` by construction, since it happens
    before that count is taken. This is deliberate: `total_count` feeds the
    "N of M pairs shown (cap reached)" truncation notice, whose job is to
    say that judgeable work was cut off. A self-pair was never judgeable
    work, so counting it there would make the notice claim a truncation that
    did not happen. Surfacing the malformed relation to the operator belongs
    to `lint`, which has no self-loop check today.

    The remaining typed edges are deduped by
    `frozenset({source_id, target_id})` so symmetric, duplicate, and
    multi-edge pairs collapse to exactly one candidate (spec: Symmetric and
    multi-edge pairs judged once).

    `deprecated` (status-aware-retrieval Phase 3, post-review correction) is
    applied to the deduped, sorted set BEFORE the `_MAX_PAIRS` cap slice --
    any pair where either id is in `deprecated` is dropped first, so the cap
    only ever consumes LIVE pairs. Filtering AFTER the cap starves live
    pairs that sort past `_MAX_PAIRS`: if the alphabetically-first cap
    slots happen to be dominated by deprecated-touching pairs, a post-cap
    filter would drop them from an already-truncated list and never give a
    later, live pair a chance to be judged, even though the pair budget was
    not actually exhausted by live pairs. The default empty frozenset makes
    this a total no-op, preserving byte-identical behavior for callers that
    never pass a deprecated set.

    `cap` (surface-merged-body-contradictions, Requirement 3) defaults to
    `_MAX_PAIRS`, preserving this function's own historical truncation
    contract for a direct caller. `find_contradictions` passes `cap=None` to
    get the FULL live list uncapped, so it can merge typed-edge candidates
    with intra-document (merged-body) candidates into ONE ordered list
    before applying a SINGLE `_MAX_PAIRS` slice at that higher level --
    never two independent caps that could drift apart.

    Returns `(pairs, total_count)`: `pairs` is the deduped, deprecation-
    filtered set sorted by `tuple(sorted(pair))`, truncated to `cap` (or
    returned in full when `cap is None`); `total_count` is the FULL deduped,
    deprecation-filtered count BEFORE truncation, so the caller can detect
    and report a cap-reached truncation (spec: Cap truncation is reported)
    -- truncation is never silent, and the cap-reached signal now only
    fires when LIVE pairs genuinely exceed the cap."""
    typed_edges = [
        edge
        for edge in store.edges()
        if edge.relation_type is not None
        and edge.relation_type != "derived_from"
        and edge.source_id != edge.target_id
    ]
    pair_keys = {_pair_key(edge.source_id, edge.target_id) for edge in typed_edges}
    ordered = sorted(pair_keys)
    live = [
        pair
        for pair in ordered
        if pair[0] not in deprecated and pair[1] not in deprecated
    ]
    if cap is None:
        return live, len(live)
    return live[:cap], len(live)


def _pair_relation_types(store: GraphStore) -> dict[tuple[str, str], str]:
    """Map each deduped pair key to the relation `type` shown to the LLM as
    "the relation_type linking them" (design's LLM Prompt Contract); has no
    bearing on dedup/ordering/cap, which `_candidate_pairs` owns exclusively.

    4R-review FIX (readability/reliability): prefers the FIRST GENUINE
    (non-`derived_from`) typed edge encountered for a pair, in
    `store.edges()`'s own deterministic order, over a `derived_from` edge
    for the SAME pair. Without this, a pair carrying BOTH a genuine typed
    edge (e.g. `related_to`) AND a provenance-mirror-synthesized
    `derived_from` edge to the same target would have `derived_from` picked
    whenever it sorts alphabetically before the genuine type (`store.edges()`
    orders `NULL`, then `relation_type` ascending) -- mislabeling the actual
    assertable relation that made the pair a contradiction candidate in the
    first place. A `derived_from` edge is only ever recorded as a LAST
    resort, so a pair reaching this function (already guaranteed by
    `_candidate_pairs`'s guard to carry at least one non-`derived_from` typed
    edge) always prefers that genuine type; `derived_from` is kept as a
    degrade-safe fallback only, never surfaced when a genuine type exists."""
    mapping: dict[tuple[str, str], str] = {}
    for edge in store.edges():
        if edge.relation_type is None:
            continue
        key = _pair_key(edge.source_id, edge.target_id)
        if key not in mapping or (
            mapping[key] == "derived_from" and edge.relation_type != "derived_from"
        ):
            mapping[key] = edge.relation_type
    return mapping


def _load_doc(
    bundle_dir: Path,
    concept_id: str,
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> tuple[str, str]:
    """Guarded single-doc re-read (module-local copy of
    `edge_typing._load_doc` -- no cross-import of its `_`-prefixed symbols,
    design D4): returns `(title, body)` for `concept_id`'s document under
    `bundle_dir`. An unreadable or unparseable document -- including a
    dangling edge endpoint with no document at all -- degrades to
    `(concept_id, "")` rather than raising or skipping the pair; the caller
    always gets something to prompt with.

    sensitivity-fail-closed-filter (directory-walk-observability follow-up,
    defense-in-depth): after re-reading this doc's OWN frontmatter, also
    independently re-checks it via `sensitivity.should_block` --
    walk-independent, so a doc the `sensitive_concept_ids` walk silently
    missed (an unlistable subtree, `okf.py`'s documented `_walk_errors`
    case) is still degraded to `(concept_id, "")` here, never entering the
    `llm.chat` payload. `include_confidential=True` skips this re-check
    identically to how it skips the upstream `_candidate_pairs` filter,
    mirroring `retrieval/answer.py`'s `_assemble_context` (answer.py:
    211-214).

    Correction batch (post-4R-review readability FIX 1): the re-check now
    calls the centralized `sensitivity.should_block(metadata,
    include_confidential=...)` predicate instead of inlining `not
    include_confidential and sensitivity.blocks_llm_send(...)` directly --
    behavior-preserving; see `sensitivity.py`'s module docstring for the
    5-way duplication this replaces.

    `local_exemption` (issue #240) is threaded here for the same reason
    `include_confidential` is: this walk-independent re-check is the LAST
    gate before the prompt, so if the upstream filter admitted a concept
    under a verified-local backend and this one still degraded it to
    `(concept_id, "")`, the exemption would be cosmetic. Defaults to
    `False`, fail-closed."""
    try:
        text = okf.concept_path_for(concept_id, bundle_dir).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return concept_id, ""
    try:
        metadata, body = okf.load_frontmatter(text)
    except Exception:  # broad: any parse failure degrades this doc, never raises
        return concept_id, ""
    if sensitivity.should_block(
        metadata,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    ):
        return concept_id, ""
    title = str(metadata.get("title") or "") or concept_id
    return title, body


@dataclass(frozen=True)
class _CandidateSpec:
    """One judgeable candidate BEFORE judgment, unifying a typed-edge
    candidate and an intra-document (merged-body) candidate into the SAME
    ordered list (surface-merged-body-contradictions, Requirement 3: one
    cap, one list) -- so both kinds share the single `_MAX_PAIRS` slice in
    `find_contradictions`, never two independently maintained caps.

    `merge_entry is None` marks a typed-edge candidate (`pair_ids` are two
    distinct, related concept ids; `relation_type` is the edge label shown
    to the LLM). `merge_entry is not None` marks an intra-document
    candidate: `pair_ids == (survivor_id, survivor_id)` (for readability
    only -- see `ContradictionVerdict.merged_absorbed_id`'s warning),
    `relation_type` is always `None`, and `merge_entry.absorbed_id` is the
    concept this candidate's `merged_absorbed_id` will carry."""

    pair_ids: tuple[str, str]
    relation_type: str | None
    merge_entry: okf.MergeLedgerEntry | None = None


def _merged_body_candidates(
    bundle_dir: Path, deprecated: frozenset[str]
) -> list[_CandidateSpec]:
    """Build one intra-document candidate per `MergeLedgerEntry` on every
    survivor document under `bundle_dir` (surface-merged-body-contradictions
    #409): each candidate pairs `entry.survivor_before`'s body against
    `entry.absorbed_snapshot`'s body, both recovered deterministically from
    the survivor's OWN ledger -- no second node, no LLM at merge time, no
    re-ingest.

    Linear, not quadratic: `merged_from` is a flat LIFO list -- `plan_merge`
    always appends `[*existing_entries, entry]` -- so a survivor's current
    ledger already lists every historical entry with no recursion. Exactly
    `len(merged_from)` candidates per survivor: never the fully-stacked
    current body against every fragment, and never all-pairs-of-fragments.

    `deprecated` mirrors `_candidate_pairs`'s own treatment: a survivor id
    in `deprecated` contributes zero merged-body candidates, exactly like it
    never appears in a typed-edge candidate pair. `sensitivity` is
    deliberately NOT filtered here (unlike `_candidate_pairs`'s `excluded`
    set, which also folds in confidential ids): a survivor's CURRENT
    on-disk sensitivity is only ONE of the three values the ledger-aware
    gate (`sensitivity.merged_content_blocked`) ranks fail-closed per entry
    at JUDGE time (`_load_ledger_bodies`) -- pre-filtering here on the
    current value alone would be the exact insufficient check Correction 1
    replaces.

    A survivor whose `merged_from` frontmatter is corrupt (non-list, or an
    entry that fails to decode) contributes zero candidates for THAT
    survivor rather than raising -- this is a doc-level building step, not
    a judged pair; a sibling survivor with a valid ledger is unaffected."""
    candidates: list[_CandidateSpec] = []
    for scan in okf._iter_docs(bundle_dir):
        if scan.read_error is not None or scan.parse_error is not None:
            continue
        survivor_id = okf.concept_id_for(scan.path, bundle_dir)
        if survivor_id in deprecated:
            continue
        metadata = scan.metadata or {}
        try:
            entries = okf.decode_merged_from(metadata)
        except Exception:  # noqa: S112 -- broad: a corrupt ledger degrades this doc only
            continue
        for entry in entries:
            candidates.append(
                _CandidateSpec(
                    pair_ids=(survivor_id, survivor_id),
                    relation_type=None,
                    merge_entry=entry,
                )
            )
    return candidates


_MERGED_CONTENT_HEADING_PREFIX = okf.MERGED_CONTENT_HEADING_PREFIX
"""The heading `okf.build_merged_document` writes above an absorbed body.

No longer a duplicated literal (#445): `okf` owns the string it writes, and
this module aliases it to FIND that heading when cutting a ledger snapshot
back down to the survivor's own content. The alias is kept so this module's
own references stay local and its intent stays documented at the point of
use. `tests/unit/cli/test_merge_core.py::
test_build_merged_document_body_layout_is_pinned` pins the rendered layout
against its own literal, so a change to the heading fails there loudly
rather than silently degrading this split."""


def _own_body_before_merge(before_body: str) -> str:
    """Cut `entry.survivor_before`'s body down to the survivor's OWN content
    at that merge point, dropping every previously-absorbed section.

    `build_merged_document` APPENDS rather than overwrites, so the ledger's
    `survivor_before` for entry *k* already embeds the *k-1* bodies absorbed
    before it. Sending that whole accumulated body would be wrong twice over:

    - **Cost.** Candidate COUNT is linear (one per ledger entry), but the
      bytes per candidate would grow with every prior merge, so one
      survivor's candidates would together cost O(m^2) in the number of
      merges -- the quadratic-cost shape `_MAX_PAIRS` and issues #382/#422
      exist to prevent. Found by the 4R review of this change; the
      exploration's own linearity analysis covered candidate count only.
    - **Correctness.** Those earlier absorbed bodies are ALREADY judged as
      their own candidates. Leaving them in invites a verdict about a
      disagreement that belongs to a different candidate, reported against
      this one.

    Truncating at the FIRST heading is what makes this exact: entries are
    appended LIFO, so everything from the first `## Merged content (` onward
    belongs to strictly earlier entries. A body with no such heading (the
    first merge on a survivor) is returned unchanged."""
    cut = before_body.find(_MERGED_CONTENT_HEADING_PREFIX)
    if cut == -1:
        return before_body
    return before_body[:cut]


def _load_ledger_bodies(
    bundle_dir: Path,
    survivor_id: str,
    entry: okf.MergeLedgerEntry,
    *,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Ledger-body sibling of `_load_doc` (Requirement 4): returns
    `((before_title, before_body), (absorbed_title, absorbed_body))` for one
    intra-document candidate, pairing `entry.survivor_before`'s body against
    `entry.absorbed_snapshot`'s body -- both IN-MEMORY ledger strings,
    never a second path read.

    Re-reads `survivor_id`'s CURRENT on-disk document -- the same
    walk-independent re-read `_load_doc` performs for its own concept id --
    ONLY to learn the survivor's current `sensitivity`, one of the three
    values `sensitivity.merged_content_blocked` (Requirement 1) ranks
    fail-closed alongside `entry.sensitivity_before`/`entry.sensitivity_after`.
    An unreadable/unparseable current survivor document degrades BOTH bodies
    to `(id, "")`, the same shape `_load_doc` uses for its own degrade path
    -- fail-closed, since a survivor whose current sensitivity cannot be
    read must never be assumed safe to send.

    When `sensitivity.merged_content_blocked` blocks, both bodies degrade to
    `(id, "")` identically. Only once the gate clears are
    `entry.survivor_before`/`entry.absorbed_snapshot` parsed via
    `okf.load_frontmatter`; a parse failure on either ledger string
    independently degrades ONLY that side to `("", "")`, mirroring
    `_load_doc`'s broad-except contract.

    The parsed `survivor_before` body is then cut to the survivor's own
    content by `_own_body_before_merge` -- see that function for why sending
    the accumulated body would be both quadratic in cost and wrong about
    which candidate a disagreement belongs to.

    `include_confidential`/`local_exemption` are threaded straight through
    to `sensitivity.merged_content_blocked`, identically to how `_load_doc`
    threads them to `sensitivity.should_block`."""
    try:
        current_text = okf.concept_path_for(survivor_id, bundle_dir).read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeDecodeError):
        return (survivor_id, ""), (entry.absorbed_id, "")
    try:
        current_metadata, _ = okf.load_frontmatter(current_text)
    except Exception:  # broad: any parse failure degrades this candidate
        return (survivor_id, ""), (entry.absorbed_id, "")

    if sensitivity.merged_content_blocked(
        current_metadata.get("sensitivity"),
        entry,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    ):
        return (survivor_id, ""), (entry.absorbed_id, "")

    try:
        before_metadata, before_body = okf.load_frontmatter(entry.survivor_before)
    except Exception:  # broad: any parse failure degrades this side only
        before_metadata, before_body = {}, ""
    before_body = _own_body_before_merge(before_body)
    before_title = str(before_metadata.get("title") or "") or survivor_id

    try:
        absorbed_metadata, absorbed_body = okf.load_frontmatter(entry.absorbed_snapshot)
    except Exception:  # broad: any parse failure degrades this side only
        absorbed_metadata, absorbed_body = {}, ""
    absorbed_title = str(absorbed_metadata.get("title") or "") or entry.absorbed_id

    return (before_title, before_body), (absorbed_title, absorbed_body)


def _build_merge_messages(
    survivor_id: str,
    absorbed_id: str,
    before_doc: tuple[str, str],
    absorbed_doc: tuple[str, str],
) -> list[Message]:
    """Assemble the 2-message prompt for one intra-document (merged-body)
    candidate: the survivor's body immediately BEFORE it absorbed
    `absorbed_id` against the absorbed document's own snapshot body --
    mirrors `_build_messages`'s shape, but frames both halves as the SAME
    concept's own history rather than two distinct, related concepts."""
    before_title, before_body = before_doc
    absorbed_title, absorbed_body = absorbed_doc
    user_content = (
        f"MERGE: {survivor_id} absorbed {absorbed_id}\n\n"
        f"[{survivor_id} before merge — {before_title}]\n{before_body}\n\n"
        f"[{absorbed_id} — {absorbed_title}]\n{absorbed_body}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _build_messages(
    pair: tuple[str, str],
    src_doc: tuple[str, str],
    tgt_doc: tuple[str, str],
    relation_type: str | None,
) -> list[Message]:
    """Assemble the 2-message prompt (mirrors
    `edge_typing._build_messages`/`adjudication._build_messages`): system
    rubric + a user turn listing the relation linking the pair, and each
    concept's `[id — title]` header plus full body."""
    source_id, target_id = pair
    src_title, src_body = src_doc
    tgt_title, tgt_body = tgt_doc
    user_content = (
        f"RELATION: {source_id} --{relation_type}--> {target_id}\n\n"
        f"[{source_id} — {src_title}]\n{src_body}\n\n"
        f"[{target_id} — {tgt_title}]\n{tgt_body}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _map_verdict(raw_verdict: object) -> Verdict | None:
    """Case-insensitive mapping of the reply's `verdict` field to `Verdict`.
    `None` (not `Verdict.UNCERTAIN`) signals "unrecognized" so the caller can
    still keep the reply's parsed `confidence`/`rationale` (spec: unknown
    verdict -> UNCERTAIN, confidence kept)."""
    if not isinstance(raw_verdict, str):
        return None
    try:
        return Verdict(raw_verdict.strip().lower())
    except ValueError:
        return None


def _coerce_confidence(raw_confidence: object) -> float:
    """Coerce the reply's `confidence` field to a float clamped to `[0.0,
    1.0]`; a non-numeric value (including `bool`, which is technically an
    `int` subclass but never a valid confidence) fails closed to `0.0`. A
    non-finite numeric value (`NaN`, `+Infinity`, `-Infinity`) also fails
    closed to `0.0`: NaN comparisons are always `False`, so the naive
    `max(0.0, min(1.0, nan))` would otherwise silently clamp to `1.0`, the
    opposite of fail-closed (module-local copy of
    `adjudication._coerce_confidence`, design D4)."""
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, int | float):
        return 0.0
    value = float(raw_confidence)
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _coerce_claims(raw_claims: object) -> tuple[str, ...]:
    """Coerce the reply's `conflicting_claims` field to a tuple of strings:
    a non-list value coerces to an empty tuple; non-string/blank entries
    within a list are dropped rather than surfaced verbatim."""
    if not isinstance(raw_claims, list):
        return ()
    return tuple(
        claim for claim in raw_claims if isinstance(claim, str) and claim.strip()
    )


def _parse_reply(raw: object) -> tuple[Verdict, float, str, tuple[str, ...]]:
    """Fail-closed parse + validate of one pair's LLM reply: never raises.
    An unparseable or non-object reply degrades to `(UNCERTAIN, 0.0,
    _MALFORMED_REPLY_RATIONALE, ())`. Otherwise `verdict` is matched
    case-insensitively (unrecognized -> `UNCERTAIN`, keeping the parsed
    `confidence`/`rationale`); `confidence` is coerced and clamped to
    `[0.0, 1.0]`; `rationale` is used as-is if it is a string, else `""`;
    `conflicting_claims` is coerced to a tuple of strings. Citation gate: a
    `CONTRADICTS` verdict with empty/missing `conflicting_claims` is
    coerced to `UNCERTAIN` (spec: Citation-Gated Precision) -- this gate
    does NOT apply to `CONSISTENT`/`UNCERTAIN`."""
    data = parsing.extract_json_object(raw)
    if data is None:
        return Verdict.UNCERTAIN, 0.0, _MALFORMED_REPLY_RATIONALE, ()

    confidence = _coerce_confidence(data.get("confidence"))
    rationale_raw = data.get("rationale", "")
    rationale = rationale_raw if isinstance(rationale_raw, str) else ""
    conflicting_claims = _coerce_claims(data.get("conflicting_claims"))

    verdict = _map_verdict(data.get("verdict"))
    if verdict is None:
        return Verdict.UNCERTAIN, confidence, rationale, conflicting_claims

    if verdict is Verdict.CONTRADICTS and not conflicting_claims:
        return Verdict.UNCERTAIN, confidence, rationale, conflicting_claims

    return verdict, confidence, rationale, conflicting_claims


def is_high_confidence_contradiction(verdict: ContradictionVerdict) -> bool:
    """`True` iff `verdict.verdict is Verdict.CONTRADICTS` AND its
    confidence is at or above `_CONFIDENCE_DISPLAY_THRESHOLD`. The stable
    public entry point callers (the `contradictions` CLI verb) use instead
    of importing the private threshold constant directly."""
    return (
        verdict.verdict is Verdict.CONTRADICTS
        and verdict.confidence >= _CONFIDENCE_DISPLAY_THRESHOLD
    )


def _pairs_and_types(
    store: GraphStore, excluded: frozenset[str], *, cap: int | None = _MAX_PAIRS
) -> tuple[list[tuple[str, str]], int, dict[tuple[str, str], str]]:
    """Return `(pairs, total_count, relation_types)` over an already-open
    `store` (`find_contradictions`'s two-branch shape, graph-projection-
    reuse design §3): a one-line extraction holding today's
    `_candidate_pairs`/`_pair_relation_types` pair, so both the
    caller-supplied and self-built branches compute candidates
    identically. `cap` is threaded straight to `_candidate_pairs`
    (surface-merged-body-contradictions, Requirement 3): `find_contradictions`
    passes `cap=None` to get the full live list uncapped, so it can be
    merged with intra-document candidates before a single, higher-level
    `_MAX_PAIRS` slice."""
    pairs, total_count = _candidate_pairs(store, excluded, cap=cap)
    relation_types = _pair_relation_types(store)
    return pairs, total_count, relation_types


@dataclass(frozen=True)
class CandidatePlan:
    """The full, already-capped candidate list one `find_contradictions` run
    will judge, plus the pre-cap totals behind it (issue #446).

    Exists so the number `curate`'s Contradictions cost gate STATES and the
    number `find_contradictions` SPENDS come from one computation. They were
    two: the gate counted typed-edge pairs while the run judged typed-edge
    plus merged-body candidates, so the gate understated the spend by the
    merge-ledger count and the operator consented to less than was paid."""

    specs: tuple[_CandidateSpec, ...]
    """The candidates to judge, in order, already sliced to `_MAX_PAIRS`.
    Exactly one `llm.chat` call is made per element."""
    edge_total: int
    """Deduped, self-loop-free, deprecation/sensitivity-filtered typed-edge
    pair count BEFORE the cap."""
    merged_total: int
    """Merged-body candidate count BEFORE the cap -- one per
    `MergeLedgerEntry` on every non-deprecated survivor."""

    @property
    def total_count(self) -> int:
        """Combined pre-cap total, the `total_pair_count` a caller compares
        against `len(specs)` to detect truncation."""
        return self.edge_total + self.merged_total

    @property
    def llm_calls(self) -> int:
        """Exactly how many `llm.chat` calls judging this plan costs."""
        return len(self.specs)

    @property
    def merged_judged(self) -> int:
        """Merged-body candidates that survived the cap."""
        return sum(1 for spec in self.specs if spec.merge_entry is not None)

    @property
    def edge_judged(self) -> int:
        """Typed-edge candidates that survived the cap."""
        return len(self.specs) - self.merged_judged


def contradiction_truncation_notice(plan: CandidatePlan) -> str | None:
    """The "N of M ... (cap reached)" line a caller renders when the shared
    `_MAX_PAIRS` budget truncated this plan, naming WHICH KIND was dropped
    and by how much -- `None` when nothing was truncated (issue #444).

    Mirrors `candidate_group_truncation_notice`/`candidate_truncation_notice`:
    the resolution layer owns the wording, the CLI only echoes it, so every
    verb reporting this truncation says the same thing.

    Naming the kind is the part of #444 that holds regardless of the ordering
    policy. The old notice reported one combined number, so an operator whose
    corpus was over the cap could not tell whether the candidates that went
    unjudged were typed-edge pairs or the merged bodies -- the same
    report-what-was-dropped gap #381, #404 and #409 were each filed about."""
    dropped_edges = plan.edge_total - plan.edge_judged
    dropped_merged = plan.merged_total - plan.merged_judged
    if dropped_edges <= 0 and dropped_merged <= 0:
        return None
    dropped: list[str] = []
    if dropped_edges > 0:
        dropped.append(f"{dropped_edges} typed-edge")
    if dropped_merged > 0:
        dropped.append(f"{dropped_merged} merged-body")
    return (
        f"{plan.llm_calls} of {plan.total_count} candidate(s) shown "
        f"(cap reached); dropped: {', '.join(dropped)}"
    )


def plan_candidates(
    bundle_dir: Path,
    *,
    store: GraphStore | None = None,
    candidates: CandidateSource | None = None,
    include_deprecated: bool = False,
    include_confidential: bool = False,
    local_exemption: bool = False,
) -> CandidatePlan:
    """Build the candidate list `find_contradictions` will judge, WITHOUT
    judging it -- no `llm.chat` call, so a caller can price a run before
    committing to it (issue #446).

    This is the ONE place typed-edge and merged-body candidates are combined
    and sliced to `_MAX_PAIRS` (#409 Requirement 3: one cap, one list, one
    `total_count`). `find_contradictions` calls it rather than inlining the
    combination, and `curate`'s `_contradictions_probe` calls it to state the
    stage's cost -- so the stated and spent numbers are the same number by
    construction rather than by two implementations agreeing.

    THE SLICING ORDER IS LOAD-BEARING, not incidental (issue #444). Merged-body
    candidates hold a reserved floor of `_MERGED_BODY_FLOOR` slots that typed
    edges cannot crowd them out of, because typed edges filling the budget
    first meant a busy corpus judged NONE of them. The floor is a minimum, not
    a quota -- merged-body candidates take every slot typed edges leave unused,
    and only `min(len(merged_specs), _MERGED_BODY_FLOOR)` is ever withheld. See
    `_MERGED_BODY_FLOOR` for why 50, and do not reorder the slice below without
    re-reading it: reverting to a plain `edge_specs + merged_specs` truncation
    silently restores the bug.

    `store`/`candidates` mirror `find_contradictions`' two-branch shape: when
    `store` is supplied it is reused and never closed (ownership stays with
    the caller, graph-projection-reuse #196); otherwise a graph is built and
    closed here.

    Deprecation is computed ONCE and used twice, exactly as before: unioned
    with the sensitivity set for typed-edge exclusion, and passed alone to
    `_merged_body_candidates`, whose per-entry sensitivity gate is the
    ledger-aware `sensitivity.merged_content_blocked` applied at judge time
    rather than the survivor's current on-disk value."""
    deprecated: frozenset[str] = frozenset()
    if not include_deprecated:
        deprecated = lifecycle.deprecated_concept_ids(bundle_dir)
    confidential = sensitivity.sensitive_concept_ids(
        bundle_dir,
        include_confidential=include_confidential,
        local_exemption=local_exemption,
    )
    excluded = deprecated | confidential

    if store is not None:
        edge_pairs, edge_total, relation_types = _pairs_and_types(
            store, excluded, cap=None
        )
    else:
        with build_graph(bundle_dir, candidates=candidates) as owned:
            edge_pairs, edge_total, relation_types = _pairs_and_types(
                owned, excluded, cap=None
            )

    edge_specs = [
        _CandidateSpec(pair_ids=pair, relation_type=relation_types.get(pair))
        for pair in edge_pairs
    ]
    merged_specs = _merged_body_candidates(bundle_dir, deprecated)

    # Issue #444: reserve the floor BEFORE typed edges take the budget, then
    # let merged-body candidates spill into whatever typed edges left unused.
    # `reserved` is `min(...)`, never the flat floor, so a corpus with two
    # merges gives up two typed-edge slots rather than fifty.
    reserved = min(len(merged_specs), _MERGED_BODY_FLOOR)
    kept_edges = edge_specs[: _MAX_PAIRS - reserved]
    kept_merged = merged_specs[: _MAX_PAIRS - len(kept_edges)]

    return CandidatePlan(
        specs=tuple(kept_edges + kept_merged),
        edge_total=edge_total,
        merged_total=len(merged_specs),
    )


def find_contradictions(
    bundle_dir: Path,
    *,
    llm: LLMBackend,
    include_deprecated: bool = False,
    include_confidential: bool = False,
    local_exemption: bool = False,
    candidates: CandidateSource | None = None,
    store: GraphStore | None = None,
    plan: CandidatePlan | None = None,
    on_progress: Callable[[int, int, ContradictionVerdict], None] | None = None,
) -> tuple[ContradictionBatch, int]:
    """Orchestrate the whole read-only contradiction-detection flow: open
    `build_graph` over `bundle_dir` internally, derive candidate pairs
    (`_candidate_pairs`: typed edges only, deduped, sorted, capped at
    `_MAX_PAIRS`), then judge each pair with one `llm.chat` call.

    `store` (graph-projection-reuse, issue #196): when supplied, this
    function reuses that already-open store instead of building its own,
    and never closes it -- ownership stays with whoever opened it. When
    supplied, `candidates` is silently unused (the caller already consumed
    it building that store).

    Returns `(batch, total_pair_count)`: `batch.results` has exactly one
    `ContradictionVerdict` per JUDGED candidate pair (after dropping, unless
    `include_deprecated`, any pair touching a deprecated concept -- BEFORE
    the `_MAX_PAIRS` cap, status-aware-retrieval Phase 3 -- and after the
    cap itself), in the same deterministic order; `total_pair_count` is the
    combined deduped, deprecation-filtered candidate count BEFORE the cap
    (post-review correction: filtering now happens upstream of the cap
    inside `_candidate_pairs`, so this count reflects LIVE pairs only), so
    the caller can detect a cap-reached truncation via `total_pair_count >
    len(batch.results)` on a COMPLETE run (spec: Cap truncation is
    reported) -- that signal now only fires when live pairs genuinely
    exceed the cap, never as a side effect of deprecation filtering alone,
    and `total_pair_count` keeps this pre-cap meaning on a partial run too,
    where the shortfall is the failure's, not the cap's.

    Unless `include_deprecated=True`, the shared
    `lifecycle.deprecated_concept_ids(bundle_dir)` predicate is computed
    ONCE and passed into `_candidate_pairs`, which drops any pair where
    either id is deprecated/superseded BEFORE slicing to `_MAX_PAIRS` -- a
    superseded concept never appears in a candidate pair (spec), and a live
    pair sorting past the first `_MAX_PAIRS` entries is never starved by
    deprecated-touching pairs consuming cap slots ahead of it (post-review
    HIGH correction; see `_candidate_pairs`'s docstring for the full
    rationale). `include_deprecated=True` skips the predicate walk entirely
    and passes an empty set, restoring today's status-blind behavior
    byte-for-byte.

    sensitivity-fail-closed-filter (S3a): unless `include_confidential=True`,
    the shared `sensitivity.sensitive_concept_ids(bundle_dir)` predicate is
    likewise computed ONCE and unioned with the deprecated set BEFORE it is
    passed into `_candidate_pairs` -- a confidential concept is excluded from
    a candidate pair exactly like a deprecated one, and
    `include_confidential=True` independently skips its own predicate walk at
    zero added cost.

    A run with zero candidate pairs (e.g. no typed edges at all, or every
    typed-edge pair touches a deprecated/confidential concept) returns
    `(ContradictionBatch(results=[]), total)` WITHOUT calling `llm.chat` --
    there is nothing to judge.

    An `OllamaError`-family exception raised by `llm.chat` stops the loop
    and comes back IN the batch (`failure` set, `failed_index` naming the
    1-based candidate whose chat raised) rather than propagating (issue
    #441): propagation made the caller pay for every judged call and then
    discard all of the completed verdicts with the raise -- #422's
    `OllamaGenerationCapped` made that edge fast and frequent. Only the
    `llm.chat` call sits inside the guard; reply-parsing/validation
    failures still degrade that one pair's verdict, an unreadable endpoint
    doc still degrades to `(concept_id, "")` for that one candidate, and a
    raise from the caller's own `on_progress` still propagates untouched.
    A complete run returns `failure=None, failed_index=None`.

    `on_progress` (issue #190, mirroring `suggest_edge_types`'s #134
    contract), if given, is called once per judged pair in the
    deterministic pair order, AFTER that pair's `ContradictionVerdict` is
    built, with `(index, total, verdict)` where `index` is 1-based and
    `total` is the number of pairs planned for judgment (post-cap, i.e.
    `len(plan.specs)` -- NOT the pre-cap `total_pair_count` this function
    returns) -- a hook for a CLI to render a per-pair progress line during
    an otherwise opaque, minutes-long run. The candidate whose chat RAISED
    does not count -- it produced no verdict, so there is nothing to report
    progress on (#441). It never affects the returned batch; an exception
    it raises propagates to the caller (it is the caller's own callback).

    `local_exemption` (issue #240) is the second escape hatch defined by
    `sensitivity.should_block`: the caller asserting that the `llm.chat`
    backend this run will actually reach is verifiably this machine, so a
    `confidential` concept is not leaving anywhere and the gate has nothing
    to protect. It is threaded, never re-derived -- the disjunction with
    `include_confidential` lives ONLY in `sensitivity.py` (see its module
    docstring). Defaults to `False`: a caller that cannot prove locality
    gets today's blanket blocking, so forgetting the parameter can only ever
    be MORE restrictive.

    surface-merged-body-contradictions (issue #409, detection half):
    intra-document (merged-body) candidates -- one per `MergeLedgerEntry` on
    every survivor in `bundle_dir` (`_merged_body_candidates`) -- are merged
    into the SAME ordered candidate list as the typed-edge pairs above,
    BEFORE the single `_MAX_PAIRS` slice (Requirement 3: one cap, one list;
    `total_pair_count` reflects the combined pre-cap total). Each such
    candidate's `ContradictionVerdict.merged_absorbed_id` is set to its
    `MergeLedgerEntry.absorbed_id` -- the SOLE discriminator from a
    typed-edge candidate (see that field's docstring warning). Its two
    bodies are gated per entry by the ledger-aware
    `sensitivity.merged_content_blocked` (Requirement 1), never by the
    survivor's current on-disk sensitivity alone -- see `_load_ledger_bodies`.

    The candidate list itself is built by `plan_candidates` (issue #446), not
    inline here: `curate`'s Contradictions cost gate has to state the number
    of calls this function will make BEFORE it runs, and the only way those
    two numbers cannot drift is for both to come from the same code.

    `plan` (issue #444) lets a caller that ALREADY built one pass it in
    rather than have it rebuilt. Both CLI verbs need the plan themselves, to
    render `contradiction_truncation_notice`'s per-kind line -- passing it
    here means the notice describes the very list that was judged, and saves
    the second bundle walk that recomputing it would cost. Omitted, this
    builds its own exactly as before."""
    if plan is None:
        plan = plan_candidates(
            bundle_dir,
            store=store,
            candidates=candidates,
            include_deprecated=include_deprecated,
            include_confidential=include_confidential,
            local_exemption=local_exemption,
        )
    total_count = plan.total_count
    specs = list(plan.specs)

    verdicts: list[ContradictionVerdict] = []
    judged_total = len(specs)
    for index, spec in enumerate(specs, start=1):
        if spec.merge_entry is not None:
            survivor_id, _ = spec.pair_ids
            src_doc, tgt_doc = _load_ledger_bodies(
                bundle_dir,
                survivor_id,
                spec.merge_entry,
                include_confidential=include_confidential,
                local_exemption=local_exemption,
            )
            messages = _build_merge_messages(
                survivor_id, spec.merge_entry.absorbed_id, src_doc, tgt_doc
            )
            merged_absorbed_id = spec.merge_entry.absorbed_id
        else:
            source_id, target_id = spec.pair_ids
            src_doc = _load_doc(
                bundle_dir,
                source_id,
                include_confidential=include_confidential,
                local_exemption=local_exemption,
            )
            tgt_doc = _load_doc(
                bundle_dir,
                target_id,
                include_confidential=include_confidential,
                local_exemption=local_exemption,
            )
            messages = _build_messages(
                spec.pair_ids, src_doc, tgt_doc, spec.relation_type
            )
            merged_absorbed_id = None
        # Guard ONLY the chat call (#441): a transport/model failure must
        # not discard the completed verdicts, while parse/validate/progress
        # failures keep their own existing contracts untouched.
        try:
            reply = llm.chat(messages)
        except OllamaError as exc:
            return (
                ContradictionBatch(results=verdicts, failure=exc, failed_index=index),
                total_count,
            )
        verdict, confidence, rationale, conflicting_claims = _parse_reply(reply)
        pair_verdict = ContradictionVerdict(
            pair_ids=spec.pair_ids,
            verdict=verdict,
            confidence=confidence,
            rationale=rationale,
            conflicting_claims=conflicting_claims,
            merged_absorbed_id=merged_absorbed_id,
        )
        verdicts.append(pair_verdict)
        if on_progress is not None:
            on_progress(index, judged_total, pair_verdict)
    return ContradictionBatch(results=verdicts), total_count
