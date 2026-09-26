"""Direction, candidate generation, and the judge for the decision-revision
detector (decision-revision-detector, Phase A, Slices 3 and 4 / S3-S4): a
pure, config-free leaf building on `resolution/decision_subject.py`'s
subject pass.

Slice 4 (the judge, added after Slice 3's direction/candidate functions
below) is the only part of this module that calls an `LLMBackend`:
`build_judge_messages`, `parse_judge_reply`, `judge_pairs`, and the verdict
types. It reuses `pair_direction` (Slice 3, this module) to choose
presentation order and, via `RevisionVerdict.direction`, as the ONLY
authority a served finding's direction is ever recomputed from -- never a
stored holder, and never a reply field (design.md Decision 6, ADR-0025).

Config-free leaf (mirrors `decision_subject.py`, `resolution/contradiction.py`):
every function here is pure over already-resolved inputs. `pair_direction`
takes plain `DecisionDate` values; `plan_revision_candidates` takes plain
`DecisionInput` values. Neither reads `bundle/provenance` nor
`openkos.model.relations` directly -- the service (Phase B, S6) resolves
event dates and builds each Decision's `resolved_with` frozenset from
frontmatter before calling in, exactly as this module's docstrings and
design.md Decision 4 require.

`pair_direction` is the single authority on temporal direction (design.md
Decision 3, ADR-0025): direction comes ONLY from each side's resolved
`DecisionDate`, never from a model reply. Slice 4's judge (a later task
list) reads this same function to choose presentation order, and
`RevisionVerdict.direction` there delegates to it rather than storing a
holder -- so there is exactly one authority on direction, and no reply
field can ever disagree with it.

`subject_overlap`/`plan_revision_candidates` implement design.md Decision 4:
lexical subject blocking through `resolution.similarity.tokenize`/
`.MATCH_FUNCTION_WORDS`'s PUBLIC names only (no import of the private
`similarity._content_tokens`), per-Decision top-k union ranking (the
`graph/proximity.py` pair-nomination shape), and a single global cap with
a truncation notice -- the same "one cap, one list, exclusions before the
count" shape as `contradiction.py`'s `_candidate_pairs`/
`contradiction_truncation_notice`.
"""

import hashlib
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from enum import Enum
from typing import Final, Literal, cast

from openkos.llm import parsing
from openkos.llm.base import LLMBackend, Message
from openkos.llm.ollama import OllamaError
from openkos.resolution import similarity
from openkos.resolution.decision_subject import quoted_verbatim
from openkos.resolution.normalize import normalize_key

DateState = Literal["dated", "missing", "multiple", "none-reached"]
"""A Decision's resolved event-date state (design.md Decision 3 /
decision-revision-detection's "Decision Event Date Resolution" requirement).
Resolved by the service (Phase B, S6) via `bundle.provenance` +
`okf.read_event_date`; this leaf only ever consumes the result."""

DirectionReason = Literal["dated", "missing", "multiple", "none-reached", "equal"]
"""Why `pair_direction` returned the `holder`/`earlier` it did: one of the
four `DateState` values (the first non-dated side's own state), or
`"equal"` when both sides resolved the SAME single date."""


@dataclass(frozen=True)
class DecisionDate:
    """One Decision's resolved event date. `value` holds the single agreed
    date when `state == "dated"`; it is `None` for every other state."""

    value: date | None
    state: DateState


@dataclass(frozen=True)
class Direction:
    """The result of `pair_direction`. `holder` names the LATER Decision
    -- never stored anywhere, always recomputed on demand (ADR-0025) --
    and `earlier` the other side; both are `None` when direction is
    unknown. `reason` explains why, and is always populated."""

    holder: str | None
    earlier: str | None
    reason: DirectionReason


def pair_direction(
    id_a: str, date_a: DecisionDate, id_b: str, date_b: DecisionDate
) -> Direction:
    """The pure direction rule (design.md Decision 3's table), checked in
    id order: `id_a`/`date_a` are checked FIRST, so when both sides are
    non-dated with DIFFERENT states, the reported reason is always
    `date_a`'s state, never `date_b`'s. Every caller in this module passes
    the sorted pair ids here (`id_a < id_b`), so this "first side" is
    always the deterministic, sorted-first id.

    - `date_a.state != "dated"`: direction unknown, `reason = date_a.state`.
    - `date_a.state == "dated"` but `date_b.state != "dated"`: direction
      unknown, `reason = date_b.state`.
    - Both dated, equal `value`: direction unknown, `reason = "equal"` --
      an equal pair of dates cannot order a pair, so it is treated like a
      missing date rather than guessed (design.md Decision 3, taken
      decision).
    - Both dated, distinct `value`: `holder` is whichever id resolved the
      LATER date, `earlier` is the other, `reason = "dated"`.

    No field of a judge reply is ever read here (design.md Decision 6,
    ADR-0025): this function's only inputs are two ids and two
    `DecisionDate` values, so no model output can ever set direction."""
    if date_a.state != "dated":
        return Direction(holder=None, earlier=None, reason=date_a.state)
    if date_b.state != "dated":
        return Direction(holder=None, earlier=None, reason=date_b.state)
    # Both sides are `dated`, so `.value` is guaranteed non-`None` by this
    # module's own construction contract (`DecisionDate`'s docstring); the
    # `state` Literal and `value` Optional are independent fields to
    # `mypy`, so `cast` -- not `assert` (banned outside tests by this
    # repo's Ruff config, S101) -- narrows the type with no new branch.
    value_a = cast(date, date_a.value)
    value_b = cast(date, date_b.value)
    if value_a == value_b:
        return Direction(holder=None, earlier=None, reason="equal")
    if value_a < value_b:
        return Direction(holder=id_b, earlier=id_a, reason="dated")
    return Direction(holder=id_a, earlier=id_b, reason="dated")


def _overlap_tokens(subject: str) -> tuple[str, ...]:
    """`subject`'s comparison tokens for `subject_overlap`: normalize via
    `normalize.normalize_key`, tokenize via `similarity.tokenize` (which
    already drops tokens shorter than `similarity.MIN_TOKEN_LENGTH`), then
    drop `similarity.MATCH_FUNCTION_WORDS` members -- UNLESS doing so
    would leave fewer than two tokens of a multi-word subject, in which
    case the original tokens are kept instead.

    This retention guard mirrors `similarity._content_tokens`'s own rule,
    for the SAME reason (#555, referenced by design.md Decision 4):
    dropping function words outright can reduce a multi-word subject to
    ONE token, and a manufactured single-token set would then
    subset-contain into every other subject sharing that one generic
    token. Reimplemented here through `similarity`'s PUBLIC names only
    (`tokenize`, `MATCH_FUNCTION_WORDS`) -- there is deliberately no
    import of the private `similarity._content_tokens`."""
    tokens = similarity.tokenize(normalize_key(subject))
    content = tuple(
        token for token in tokens if token not in similarity.MATCH_FUNCTION_WORDS
    )
    if len(tokens) >= 2 and len(content) < 2:
        return tokens
    return content


def subject_overlap(subject_a: str, subject_b: str) -> float:
    """A graded lexical overlap score in `[0.0, 1.0]` between two Decision
    subjects (design.md Decision 4): the fraction of the SMALLER token set
    (`_overlap_tokens`) that has an equivalent token
    (`SequenceMatcher.ratio() >= similarity.SIMILARITY_THRESHOLD`)
    somewhere in the LARGER set. Either subject tokenizing to nothing
    (including an empty subject) gives `0.0`.

    Graded, not all-or-nothing like `similarity.near_match_score`: top-k
    candidate ranking (`plan_revision_candidates`) needs a score to RANK
    by, and an all-or-nothing containment rule would reject a pair that
    differs in only one of several tokens even though the rest match
    exactly."""
    tokens_a = _overlap_tokens(subject_a)
    tokens_b = _overlap_tokens(subject_b)
    if not tokens_a or not tokens_b:
        return 0.0
    smaller, larger = (
        (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
    )
    matched = sum(
        1
        for small_token in smaller
        if any(
            SequenceMatcher(None, small_token, large_token).ratio()
            >= similarity.SIMILARITY_THRESHOLD
            for large_token in larger
        )
    )
    return matched / len(smaller)


SUBJECT_OVERLAP_THRESHOLD: Final[float] = 0.5
"""UNMEASURED: placeholder until sub-change 3's harness (design.md
Decision 4). Chosen for RECALL, not precision -- the judge (a later task
list) is the precision layer, so this leaf's job is to not exclude a real
pair, not to avoid proposing a false one."""

TOP_K: Final[int] = 5
"""UNMEASURED: placeholder until sub-change 3's harness. Mirrors
`graph/proximity.py`'s per-node candidate budget (`graph/proximity.py:67`)."""

MAX_PAIRS: Final[int] = 200
"""UNMEASURED: placeholder until sub-change 3's harness. Mirrors
`resolution.contradiction._MAX_PAIRS` (`contradiction.py:94`)."""


@dataclass(frozen=True)
class DecisionInput:
    """One Decision as `plan_revision_candidates` sees it: already
    subject-derived, already resolved to its reached Source ids, already
    joined to its resolution-relation targets. The service (Phase B, S6)
    builds these from frontmatter and the subject cache; this leaf never
    reads `bundle/` or `openkos.model.relations` itself."""

    concept_id: str
    subject: str | None
    """`None` when the subject pass failed, was malformed, or never ran
    for this Decision -- excluded from pairing, counted in
    `RevisionCandidatePlan.without_subject`."""
    source_ids: frozenset[str]
    resolved_with: frozenset[str]
    """Targets of this Decision's own `RESOLUTION_RELATION_TYPES` edges
    (`supersedes`/`reconciled_with`/`revises`), already filtered by the
    caller. A pair is excluded whenever EITHER side names the other here,
    regardless of which of the three types it was -- this function reads
    only membership, never `openkos.model.relations` itself."""


@dataclass(frozen=True)
class RevisionCandidate:
    """One surviving candidate pair: sorted `pair_ids` and its
    `subject_overlap` score."""

    pair_ids: tuple[str, str]
    score: float


@dataclass(frozen=True)
class RevisionCandidatePlan:
    """The result of one `plan_revision_candidates` run.

    `candidates` is already capped and ordered by `(-score, pair_ids)`.
    `total` is the PRE-CAP count of pairs that survived every eligibility
    exclusion -- disjoint sources, no shared resolution edge, and the
    subject-overlap threshold -- AND the per-Decision top-k union.
    Exclusions and the count both come strictly BEFORE the cap
    (design.md Decision 4: "exclusions come before the count, which come
    before the cap"), so a pair excluded at any earlier step never
    inflates `total` and never consumes a slot `candidates` could
    otherwise have kept for a pair that survived every exclusion.
    `without_subject` is a separate count, taken before pairing even
    starts."""

    candidates: tuple[RevisionCandidate, ...]
    total: int
    without_subject: int


def plan_revision_candidates(
    decisions: Sequence[DecisionInput],
    *,
    top_k: int = TOP_K,
    cap: int = MAX_PAIRS,
) -> RevisionCandidatePlan:
    """The candidate-generation algorithm (design.md Decision 4), in
    order:

    1. Keep only Decisions with a subject; count the rest in
       `without_subject`.
    2. For every unordered pair (in sorted `concept_id` order, so the
       stored pair key is always `(smaller_id, larger_id)`): drop it if
       the two `source_ids` sets intersect (one meeting is not a change
       over time); drop it if EITHER side names the other in
       `resolved_with` (an `or` between the two directions -- a resolved
       pair is resolved regardless of which side recorded the edge); drop
       it if `subject_overlap` falls strictly below
       `SUBJECT_OVERLAP_THRESHOLD`.
    3. Per-Decision top-k: each surviving Decision ranks its OWN eligible
       partners by `(-score, partner_id)` and keeps only the first
       `top_k`. A pair survives if EITHER side kept it -- a UNION across
       both sides (`graph/proximity.py`'s pair-nomination shape), never an
       intersection: a low-degree Decision that independently ranks a
       high-degree hub inside its own top-k still yields that pair, even
       when the hub itself ranked that partner outside its own top-k.
    4. Order every surviving pair by `(-score, pair_ids)`. `total` is this
       ordered list's length -- every exclusion above has already run, so
       nothing counted here was dropped later. `candidates` is its first
       `cap` entries.

    This ordering -- exclusions, then the count, then the cap -- is never
    reordered: a pair dropped at step 2 never reaches `total`, and the
    cap in step 4 never drops a pair the union in step 3 already decided
    to keep in favor of one that never survived step 2 at all."""
    eligible_decisions = [
        decision for decision in decisions if decision.subject is not None
    ]
    without_subject = len(decisions) - len(eligible_decisions)
    ordered_decisions = sorted(
        eligible_decisions, key=lambda decision: decision.concept_id
    )

    score_by_pair: dict[tuple[str, str], float] = {}
    for index, decision_a in enumerate(ordered_decisions):
        for decision_b in ordered_decisions[index + 1 :]:
            if decision_a.source_ids & decision_b.source_ids:
                continue
            if (
                decision_b.concept_id in decision_a.resolved_with
                or decision_a.concept_id in decision_b.resolved_with
            ):
                continue
            # Both sides come from `ordered_decisions`, already filtered to
            # `subject is not None`; `cast` (not `assert`, S101) narrows
            # the type with no new branch.
            subject_a = cast(str, decision_a.subject)
            subject_b = cast(str, decision_b.subject)
            score = subject_overlap(subject_a, subject_b)
            if score < SUBJECT_OVERLAP_THRESHOLD:
                continue
            score_by_pair[(decision_a.concept_id, decision_b.concept_id)] = score

    partners: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for (id_a, id_b), score in score_by_pair.items():
        partners[id_a].append((score, id_b))
        partners[id_b].append((score, id_a))

    kept_pairs: set[tuple[str, str]] = set()
    for decision_id, ranked in partners.items():
        ranked.sort(key=lambda item: (-item[0], item[1]))
        for _, partner_id in ranked[:top_k]:
            pair = (
                (decision_id, partner_id)
                if decision_id < partner_id
                else (partner_id, decision_id)
            )
            kept_pairs.add(pair)

    ordered_candidates = sorted(
        (
            RevisionCandidate(pair_ids=pair, score=score_by_pair[pair])
            for pair in kept_pairs
        ),
        key=lambda candidate: (-candidate.score, candidate.pair_ids),
    )
    return RevisionCandidatePlan(
        candidates=tuple(ordered_candidates[:cap]),
        total=len(ordered_candidates),
        without_subject=without_subject,
    )


def revision_truncation_notice(plan: RevisionCandidatePlan) -> str | None:
    """The "N of M candidate pair(s) shown (cap reached); dropped: K" line
    (design.md Decision 4), mirroring
    `contradiction.contradiction_truncation_notice`'s wording -- `None`
    when the cap did not truncate this plan (`plan.total <=
    len(plan.candidates)`)."""
    dropped = plan.total - len(plan.candidates)
    if dropped <= 0:
        return None
    return (
        f"{len(plan.candidates)} of {plan.total} candidate pair(s) shown "
        f"(cap reached); dropped: {dropped}"
    )


# ---------------------------------------------------------------------------
# The judge (Slice 4 / S4, design.md Decision 6)
# ---------------------------------------------------------------------------


class RevisionVerdictValue(Enum):
    """The judge's fixed four-value vocabulary (design.md Decision 6).
    There is deliberately no degrade member: a malformed reply produces a
    `RevisionVerdict` with `malformed=True` and `verdict=UNRELATED`
    instead, so this enum stays exactly the four values the spec's "Judge
    Verdict Vocabulary And Reply Shape" requirement fixes."""

    REVERSES = "reverses"
    REFINES = "refines"
    REAFFIRMS = "reaffirms"
    UNRELATED = "unrelated"


@dataclass(frozen=True)
class JudgeSide:
    """One Decision as the judge sees it: its own `title`/`body` plus the
    `DecisionDate` the service (Phase B, S6) already resolved. Two of these
    make one candidate pair's input to `build_judge_messages`/`judge_pairs`
    -- this leaf never resolves a date itself."""

    concept_id: str
    title: str
    body: str
    date: DecisionDate


def _presentation_order(
    a: JudgeSide, b: JudgeSide
) -> tuple[JudgeSide, JudgeSide, Direction]:
    """The presentation order `build_judge_messages` and `judge_pairs`
    both need, plus the `Direction` that decided it: sort `a`/`b` by
    `concept_id` first (every `pair_direction` caller in this module passes
    sorted ids), then resolve direction over the sorted pair. A KNOWN
    direction reorders to earlier-first; an UNKNOWN direction keeps the
    sorted-id order as is (design.md Decision 6). Returning `Direction`
    alongside the order lets a caller building a `RevisionVerdict` align
    quotes/dates back onto sorted `pair_ids` without recomputing
    `pair_direction` a second time."""
    id_a, id_b = (a, b) if a.concept_id < b.concept_id else (b, a)
    direction = pair_direction(id_a.concept_id, id_a.date, id_b.concept_id, id_b.date)
    if direction.holder is None:
        return id_a, id_b, direction
    earlier = id_a if id_a.concept_id == direction.earlier else id_b
    later = id_b if earlier is id_a else id_a
    return earlier, later, direction


def _format_resolved_date(decision_date: DecisionDate) -> str:
    """ISO-format a `DecisionDate` known to be `dated` (only called from a
    branch already guarded on `direction.holder is not None`, which
    `pair_direction` only returns when BOTH sides are `dated`). `cast`, not
    `assert` (S101, banned outside `tests/`), narrows the type with no new
    branch -- the same pattern `pair_direction` itself uses above."""
    return cast(date, decision_date.value).isoformat()


_JUDGE_SYSTEM_PROMPT: Final = """You are comparing two Decisions recorded in a knowledge base, from different meetings.

Decide how the second Decision relates to the first Decision's choice on the same subject:
- reverses: the second overturns or replaces the first's choice.
- refines: the second keeps the first's choice but narrows, extends, or conditions it.
- reaffirms: the second restates the same choice.
- unrelated: the two concern a different subject, or one has no bearing on the other.

When the order between the two Decisions is not established, apply these same four definitions symmetrically -- judge whether one overturns, refines, reaffirms, or has no bearing on the other, without assuming which one came first.

Quote one sentence copied VERBATIM from each Decision's own body that supports your verdict.

Reply with JSON only, no other text:
{"verdict": "reverses"|"refines"|"reaffirms"|"unrelated", "confidence": 0.0-1.0, "rationale": "...", "quote_first": "...", "quote_second": "..."}

Never add a field naming which Decision is earlier or later -- that is determined elsewhere, from recorded dates, never from this reply. Never paraphrase; each quote must be copied character-for-character from its own Decision's body."""
"""The judge's system prompt (design.md Decision 6). UNMEASURED: placeholder
wording until sub-change 3's harness measures it. Deliberately defines no
field for direction or ordering -- `parse_judge_reply` reads only the five
schema keys this prompt asks for, so no reply can ever set direction
(ADR-0025)."""

JUDGE_PROMPT_VERSION: Final[str] = hashlib.sha256(
    _JUDGE_SYSTEM_PROMPT.encode()
).hexdigest()[:16]
"""Derived, never hand-bumped, from `_JUDGE_SYSTEM_PROMPT` itself -- so a
future prompt edit cannot forget to invalidate the revision-findings cache
(Phase B, S5) that keys on this constant, mirroring
`decision_subject.SUBJECT_PROMPT_VERSION`."""


def build_judge_messages(a: JudgeSide, b: JudgeSide) -> list[Message]:
    """The two-turn `[system, user]` message list sent to the judge's
    `LLMBackend.chat` (design.md Decision 6). Presentation order comes
    from `_presentation_order`, never from the order `a`/`b` are passed in:

    - Known direction: the earlier Decision is shown first, under the
      header `"EARLIER DECISION (<date>) [<id> — <title>]"`, then the
      later Decision under `"LATER DECISION (<date>) [...]"`.
    - Unknown direction: both sides in sorted `concept_id` order, under
      `"DECISION 1 [...]"` / `"DECISION 2 [...]"`, preceded by the line
      `"ORDER UNKNOWN: the dates of these decisions do not establish which
      came first."`
    """
    first, second, direction = _presentation_order(a, b)
    if direction.holder is not None:
        preamble = ""
        header_first = (
            f"EARLIER DECISION ({_format_resolved_date(first.date)}) "
            f"[{first.concept_id} — {first.title}]"
        )
        header_second = (
            f"LATER DECISION ({_format_resolved_date(second.date)}) "
            f"[{second.concept_id} — {second.title}]"
        )
    else:
        preamble = (
            "ORDER UNKNOWN: the dates of these decisions do not establish "
            "which came first.\n\n"
        )
        header_first = f"DECISION 1 [{first.concept_id} — {first.title}]"
        header_second = f"DECISION 2 [{second.concept_id} — {second.title}]"
    user_content = (
        f"{preamble}{header_first}\n{first.body}\n\n{header_second}\n{second.body}"
    )
    return [
        {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _coerce_confidence(raw_confidence: object) -> float:
    """Module-local copy of `contradiction._coerce_confidence` (design.md
    Decision 6, so `resolution.decision_revision` never cross-imports a
    private name from `resolution.contradiction`): coerces `confidence` to
    a float clamped to `[0.0, 1.0]`. A non-numeric value (including `bool`,
    technically an `int` subclass but never a valid confidence) fails
    closed to `0.0`. A non-finite value (`NaN`, `+Infinity`, `-Infinity`)
    also fails closed to `0.0`: NaN comparisons are always `False`, so the
    naive `max(0.0, min(1.0, nan))` would otherwise silently clamp to
    `1.0`, the opposite of fail-closed."""
    if isinstance(raw_confidence, bool) or not isinstance(raw_confidence, int | float):
        return 0.0
    value = float(raw_confidence)
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _map_revision_verdict(raw_verdict: object) -> RevisionVerdictValue | None:
    """Case-insensitive mapping of the reply's `verdict` field to
    `RevisionVerdictValue`. `None` (not `UNRELATED`) signals "unrecognized"
    so the caller can still keep the reply's parsed `confidence`/
    `rationale` -- the `contradiction._map_verdict` precedent (design.md
    Decision 6: unknown verdict -> UNRELATED, confidence kept)."""
    if not isinstance(raw_verdict, str):
        return None
    try:
        return RevisionVerdictValue(raw_verdict.strip().lower())
    except ValueError:
        return None


_MALFORMED_REPLY_RATIONALE: Final = (
    "malformed reply: could not parse a valid verdict JSON object"
)
"""Stable rationale for a judge reply that fails fail-closed parsing --
mirrors `adjudication._MALFORMED_REPLY_RATIONALE`'s wording, distinguishing
this degrade from an ordinary low-confidence `UNRELATED` verdict."""


@dataclass(frozen=True)
class ParsedJudgement:
    """The direct result of `parse_judge_reply`: the judge reply's five
    schema fields, with `quote_first`/`quote_second` verified against
    whichever `first_body`/`second_body` the CALLER passed in -- in
    PRESENTATION order, never remapped to sorted `pair_ids` order here.
    `judge_pairs` (the only production caller) is the one that maps these
    back onto sorted `pair_ids` order when it builds the final
    `RevisionVerdict`, using the same presentation order it chose via
    `_presentation_order`."""

    verdict: RevisionVerdictValue
    confidence: float
    rationale: str
    quote_first: str | None
    quote_second: str | None


def parse_judge_reply(
    raw: object, first_body: str, second_body: str
) -> ParsedJudgement | None:
    """Fail-closed parse of one judge reply (design.md Decision 6).

    Returns `None` (malformed -- never persisted, degrades that pair only)
    when `raw` is not a JSON object. Otherwise reads EXACTLY the five
    schema keys the prompt asks for -- `verdict`, `confidence`, `rationale`,
    `quote_first`, `quote_second` -- and nothing else: an extra key
    claiming an order or direction (e.g. `"later": "..."`) is never read,
    so it is structurally impossible for a reply to set direction
    (ADR-0025; direction comes only from `pair_direction` over resolved
    dates).

    - `verdict`: an unrecognized string maps to `UNRELATED` while keeping
      the coerced `confidence` (`_map_revision_verdict`'s contract).
    - `confidence`: coerced and clamped via `_coerce_confidence`.
    - `rationale`: used as-is if a string, else `""`.
    - `quote_first`/`quote_second`: kept only if verbatim
      (`quoted_verbatim`, imported from `decision_subject`) in
      `first_body`/`second_body` RESPECTIVELY -- the exact presentation
      order the caller supplied, never sorted-id order. A quote that fails
      verification on its own side becomes `None` on that side only; the
      other side's quote is unaffected."""
    data = parsing.extract_json_object(raw)
    if data is None:
        return None

    confidence = _coerce_confidence(data.get("confidence"))
    raw_rationale = data.get("rationale")
    rationale = raw_rationale if isinstance(raw_rationale, str) else ""

    verdict = _map_revision_verdict(data.get("verdict"))
    if verdict is None:
        verdict = RevisionVerdictValue.UNRELATED

    raw_quote_first = data.get("quote_first")
    quote_first = (
        raw_quote_first
        if isinstance(raw_quote_first, str)
        and quoted_verbatim(raw_quote_first, first_body)
        else None
    )
    raw_quote_second = data.get("quote_second")
    quote_second = (
        raw_quote_second
        if isinstance(raw_quote_second, str)
        and quoted_verbatim(raw_quote_second, second_body)
        else None
    )

    return ParsedJudgement(
        verdict=verdict,
        confidence=confidence,
        rationale=rationale,
        quote_first=quote_first,
        quote_second=quote_second,
    )


@dataclass(frozen=True)
class RevisionVerdict:
    """One judged candidate pair's result. `quotes` and `dates` are always
    ALIGNED WITH `pair_ids` (sorted `concept_id` order) -- never
    presentation order -- so a caller reading `pair_ids[0]` always finds
    that same Decision's own quote/date at `quotes[0]`/`dates[0]`.

    `direction` is a `@property`, never a stored field: it is recomputed
    on demand by `pair_direction` from `dates`/`pair_ids` alone, so no
    reply field -- and no drift between a stored holder and the dates it
    was computed from -- can ever exist (design.md Decision 2/6, ADR-0025).

    A malformed reply is represented as `malformed=True`,
    `verdict=UNRELATED`, `confidence=0.0`,
    `rationale=_MALFORMED_REPLY_RATIONALE`, `quotes=(None, None)` -- shown
    only under `--all` (Phase B, S7/S8) and never persisted, so it is
    re-judged next run."""

    pair_ids: tuple[str, str]
    verdict: RevisionVerdictValue
    confidence: float
    rationale: str
    quotes: tuple[str | None, str | None]
    dates: tuple[DecisionDate, DecisionDate]
    malformed: bool = False

    @property
    def direction(self) -> Direction:
        return pair_direction(
            self.pair_ids[0], self.dates[0], self.pair_ids[1], self.dates[1]
        )


@dataclass(frozen=True)
class RevisionBatch:
    """The result of one `judge_pairs` run -- the same partial-batch
    contract as `decision_subject.SubjectBatch`/`find_contradictions`
    (issue #441): `results` holds every completed `RevisionVerdict` in
    request order; a raised `OllamaError` mid-loop stops the batch and is
    carried in `failure`/`failed_index` (1-based) rather than propagating,
    so every already-paid-for result survives. A complete run returns
    `failure=None, failed_index=None`."""

    results: list[RevisionVerdict] = field(default_factory=list)
    failure: OllamaError | None = None
    failed_index: int | None = None


def _sorted_pair(
    a: JudgeSide, b: JudgeSide
) -> tuple[tuple[str, str], tuple[DecisionDate, DecisionDate]]:
    """`(pair_ids, dates)`, both in sorted `concept_id` order -- the
    alignment `RevisionVerdict.pair_ids`/`.dates` always keep."""
    if a.concept_id < b.concept_id:
        return (a.concept_id, b.concept_id), (a.date, b.date)
    return (b.concept_id, a.concept_id), (b.date, a.date)


def _align_quotes_to_pair_ids(
    pair_ids: tuple[str, str],
    presented_first_id: str,
    quote_first: str | None,
    quote_second: str | None,
) -> tuple[str | None, str | None]:
    """Map `parse_judge_reply`'s presentation-order quotes
    (`quote_first`/`quote_second`) onto sorted `pair_ids` order. When the
    Decision shown FIRST is also the sorted-first id, the mapping is
    identity; otherwise the two quotes swap -- exactly the case Slice 4's
    own test proves with an earlier Decision whose id sorts alphabetically
    AFTER the later Decision's id."""
    if presented_first_id == pair_ids[0]:
        return quote_first, quote_second
    return quote_second, quote_first


_ACTIONABLE_CONFIDENCE: Final[float] = 0.7
"""UNMEASURED: placeholder until sub-change 3's harness (design.md
Decision 6). Mirrors `contradiction._CONFIDENCE_DISPLAY_THRESHOLD`."""

_ACTIONABLE_VERDICT_VALUES: Final = frozenset(
    {RevisionVerdictValue.REVERSES.value, RevisionVerdictValue.REFINES.value}
)
_REPORTABLE_VERDICT_VALUES: Final = _ACTIONABLE_VERDICT_VALUES | frozenset(
    {RevisionVerdictValue.REAFFIRMS.value}
)


def is_actionable_revision(
    verdict: str, confidence: float, quote_0: str | None, quote_1: str | None
) -> bool:
    """The citation gate, applied as an actionability rule rather than a
    coercion (design.md Decision 6): `True` iff `verdict` is `"reverses"`
    or `"refines"`, `confidence >= _ACTIONABLE_CONFIDENCE`, and BOTH quotes
    were verified (neither is `None`). `REAFFIRMS` is never actionable, no
    matter the confidence or quotes -- see `is_reportable_revision` for the
    weaker rule it satisfies instead."""
    return (
        verdict in _ACTIONABLE_VERDICT_VALUES
        and confidence >= _ACTIONABLE_CONFIDENCE
        and quote_0 is not None
        and quote_1 is not None
    )


def is_reportable_revision(
    verdict: str, confidence: float, quote_0: str | None, quote_1: str | None
) -> bool:
    """The report's default-display rule (design.md Decision 6/8): `True`
    iff `verdict` is `"reverses"`, `"refines"`, or `"reaffirms"`,
    `confidence >= _ACTIONABLE_CONFIDENCE`, and BOTH quotes were verified.
    `UNRELATED` is never reportable, regardless of confidence or quotes.
    Every actionable verdict is also reportable (the two verdict sets
    differ by exactly `REAFFIRMS`), but a reportable `REAFFIRMS` is never
    actionable -- reaffirming writes no relation (Phase B, S7/S8)."""
    return (
        verdict in _REPORTABLE_VERDICT_VALUES
        and confidence >= _ACTIONABLE_CONFIDENCE
        and quote_0 is not None
        and quote_1 is not None
    )


RELATION_FOR_VERDICT: Final[dict[RevisionVerdictValue, str]] = {
    RevisionVerdictValue.REVERSES: "supersedes",
    RevisionVerdictValue.REFINES: "revises",
}
"""The two verdicts `reconcile --from-findings` (Phase B, S8/S9) may write
a relation for. Deliberately keyed by `RevisionVerdictValue`, not by the
`str` vocabulary `is_actionable_revision` reads -- this map's job is to
choose which relation TYPE a write uses, not to gate whether one happens
at all. `REAFFIRMS`/`UNRELATED` are absent by construction: neither ever
causes a bundle write (design.md Decision 6). A test ties this map's
values to `RESOLUTION_RELATION_TYPES - {"reconciled_with"}`, so a fourth
resolution type added to one side but not mapped here fails loudly."""


def judge_pairs(
    pairs: Sequence[tuple[JudgeSide, JudgeSide]],
    *,
    llm: LLMBackend,
    on_progress: Callable[[int, int, RevisionVerdict], None] | None = None,
) -> RevisionBatch:
    """Judge every `pairs` entry, one `llm.chat` call each, in order
    (design.md Decision 6) -- the same loop shape as
    `decision_subject.derive_subjects`/`find_contradictions`.

    Only `llm.chat` sits inside the `OllamaError` guard: a raised error
    stops the loop and returns the completed prefix (`failure`/
    `failed_index` set), never discarding results already paid for. A
    reply that fails to parse (`parse_judge_reply` returns `None`)
    degrades that pair to a `malformed=True` `RevisionVerdict` and the
    loop continues -- it never raises and never stops the batch
    (decision-revision-detection scenario "One malformed reply degrades
    without aborting the batch").

    `on_progress`, if given, is called once per completed pair (`index`
    1-based, `total = len(pairs)`, the completed `RevisionVerdict`),
    mirroring `find_contradictions`'s `on_progress` contract. The pair
    whose `chat` raised does not count -- it produced no result."""
    results: list[RevisionVerdict] = []
    total = len(pairs)
    for index, (a, b) in enumerate(pairs, start=1):
        messages = build_judge_messages(a, b)
        try:
            reply = llm.chat(messages)
        except OllamaError as exc:
            return RevisionBatch(results=results, failure=exc, failed_index=index)

        pair_ids, dates = _sorted_pair(a, b)
        first, second, _direction = _presentation_order(a, b)
        parsed = parse_judge_reply(reply, first.body, second.body)
        if parsed is None:
            verdict = RevisionVerdict(
                pair_ids=pair_ids,
                verdict=RevisionVerdictValue.UNRELATED,
                confidence=0.0,
                rationale=_MALFORMED_REPLY_RATIONALE,
                quotes=(None, None),
                dates=dates,
                malformed=True,
            )
        else:
            quotes = _align_quotes_to_pair_ids(
                pair_ids, first.concept_id, parsed.quote_first, parsed.quote_second
            )
            verdict = RevisionVerdict(
                pair_ids=pair_ids,
                verdict=parsed.verdict,
                confidence=parsed.confidence,
                rationale=parsed.rationale,
                quotes=quotes,
                dates=dates,
            )
        results.append(verdict)
        if on_progress is not None:
            on_progress(index, total, verdict)
    return RevisionBatch(results=results)
