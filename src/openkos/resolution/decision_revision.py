"""Direction and candidate generation for the decision-revision detector
(decision-revision-detector, Phase A, Slice 3 / S3): a pure, config-free
leaf building on `resolution/decision_subject.py`'s subject pass.

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

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Final, Literal, cast

from openkos.resolution import similarity
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
