"""Unit tests for `resolution/decision_revision.py`: the pure direction
rule, subject-overlap scoring, candidate generation, and the judge
(decision-revision-detector, Phase A, Slices 3 and 4 / S3-S4).

Slices 3's tests are pure -- no `LLMBackend` double is needed. Slice 4's
judge tests use a module-local `_ScriptedLLM`/`_RaisingLLM` double
(byte-identical shape to `test_decision_subject.py`'s) -- zero network,
zero real Ollama process.
"""

import inspect
import json
import math
from collections.abc import Iterable, Sequence
from datetime import date

import pytest

from openkos import event_dates
from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.model.relations import RESOLUTION_RELATION_TYPES
from openkos.resolution import decision_revision
from openkos.resolution.decision_revision import (
    DateState,
    DecisionDate,
    DecisionInput,
)

# ---------------------------------------------------------------------------
# pair_direction
# ---------------------------------------------------------------------------

_STATES: tuple[DateState, ...] = ("dated", "missing", "multiple", "none-reached")
_DATE_EARLY = date(2026, 1, 1)
_DATE_LATE = date(2026, 3, 1)


def _date(state: DateState, value: date | None = None) -> DecisionDate:
    if state == "dated":
        assert value is not None
        return DecisionDate(value=value, state=state)
    return DecisionDate(value=None, state=state)


def test_date_state_is_the_event_dates_alias() -> None:
    """`decision_revision.DateState` is an explicit alias of
    `event_dates.DateState` (design.md Decision 1) -- a source-level alias,
    not a second, separate `Literal` definition.

    `typing.Literal` caches by value (`_tp_cache`), so an `is` comparison
    ALONE cannot distinguish an alias from an independently re-typed local
    `Literal` carrying the same 4 states -- two separately constructed
    `Literal["dated", "missing", "multiple", "none-reached"]` objects are
    already `is`-identical on this interpreter, confirmed directly:
    `Literal["dated", "missing", "multiple", "none-reached"] is Literal[
    "dated", "missing", "multiple", "none-reached"]` is `True`. So this
    test ALSO inspects `decision_revision`'s own source for the alias
    assignment, which DOES distinguish the two. Kills a future
    re-introduction of a local `Literal` definition in place of the
    alias."""
    assert decision_revision.DateState is event_dates.DateState

    source = inspect.getsource(decision_revision)
    assert "DateState = event_dates.DateState" in source
    assert "DateState = Literal[" not in source


def _build_direction_table() -> list[
    tuple[DecisionDate, DecisionDate, str | None, str | None, str]
]:
    """Every combination of `DateState` on each side (16 cells), minus the
    dated/dated cell (handled by the three explicit cases appended below),
    plus dated-vs-dated equal, `a < b`, and `a > b` -- design.md Decision
    3's table, exhaustively. `holder`/`earlier` name `"a"`/`"b"` (the
    fixed, sorted pair ids every test in this table uses) or `None`."""
    table: list[tuple[DecisionDate, DecisionDate, str | None, str | None, str]] = []
    for state_a in _STATES:
        for state_b in _STATES:
            if state_a == "dated" and state_b == "dated":
                continue
            date_a = _date(state_a, _DATE_EARLY if state_a == "dated" else None)
            date_b = _date(state_b, _DATE_EARLY if state_b == "dated" else None)
            # Side `a` is checked FIRST: whenever it is not dated, the
            # reason is `a`'s own state regardless of what `b` is -- this
            # is the "first non-dated side reported in id order" case
            # (e.g. `a` missing, `b` multiple -> reason is "missing").
            reason = state_a if state_a != "dated" else state_b
            table.append((date_a, date_b, None, None, reason))
    table.append(
        (_date("dated", _DATE_EARLY), _date("dated", _DATE_EARLY), None, None, "equal")
    )
    table.append(
        (_date("dated", _DATE_EARLY), _date("dated", _DATE_LATE), "b", "a", "dated")
    )
    table.append(
        (_date("dated", _DATE_LATE), _date("dated", _DATE_EARLY), "a", "b", "dated")
    )
    return table


@pytest.mark.parametrize(
    ("date_a", "date_b", "expected_holder", "expected_earlier", "expected_reason"),
    _build_direction_table(),
)
def test_pair_direction_full_table(
    date_a: DecisionDate,
    date_b: DecisionDate,
    expected_holder: str | None,
    expected_earlier: str | None,
    expected_reason: str,
) -> None:
    result = decision_revision.pair_direction("a", date_a, "b", date_b)
    assert result.holder == expected_holder
    assert result.earlier == expected_earlier
    assert result.reason == expected_reason


# ---------------------------------------------------------------------------
# subject_overlap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject_a", "subject_b", "expected"),
    [
        ("billing tool", "billing tool", 1.0),
        ("billing tool", "release cadence", 0.0),
        ("billing database", "database for billing", 1.0),
        ("postgres", "postgres database migration", 1.0),
        ("", "billing tool", 0.0),
        ("billing tool", "", 0.0),
    ],
)
def test_subject_overlap(subject_a: str, subject_b: str, expected: float) -> None:
    assert decision_revision.subject_overlap(subject_a, subject_b) == pytest.approx(
        expected
    )


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("vector_a", "vector_b", "expected"),
    [
        ((1.0, 0.0), (1.0, 0.0), 1.0),
        ((1.0, 0.0), (0.0, 1.0), 0.0),
        ((1.0, 0.0), (-1.0, 0.0), -1.0),
        # Not pre-normalized: a longer vector pointing the same direction
        # still scores 1.0 -- the function must normalize itself.
        ((2.0, 0.0), (5.0, 0.0), 1.0),
        ((3.0, 4.0), (3.0, 4.0), 1.0),
    ],
)
def test_cosine_similarity(
    vector_a: tuple[float, ...], vector_b: tuple[float, ...], expected: float
) -> None:
    assert decision_revision.cosine_similarity(vector_a, vector_b) == pytest.approx(
        expected
    )


def test_cosine_similarity_zero_vector_fails_closed_to_zero() -> None:
    assert decision_revision.cosine_similarity((0.0, 0.0), (1.0, 0.0)) == 0.0
    assert decision_revision.cosine_similarity((1.0, 0.0), (0.0, 0.0)) == 0.0
    assert decision_revision.cosine_similarity((0.0, 0.0), (0.0, 0.0)) == 0.0


def test_cosine_similarity_mismatched_length_fails_closed_to_zero() -> None:
    assert decision_revision.cosine_similarity((1.0, 0.0), (1.0, 0.0, 0.0)) == 0.0


# ---------------------------------------------------------------------------
# plan_revision_candidates
# ---------------------------------------------------------------------------


def _decision(
    concept_id: str,
    subject: str | None,
    *,
    sources: Iterable[str] = (),
    resolved_with: Iterable[str] = (),
) -> DecisionInput:
    return DecisionInput(
        concept_id=concept_id,
        subject=subject,
        source_ids=frozenset(sources),
        resolved_with=frozenset(resolved_with),
    )


def _unit_vector(index: int, dims: int) -> list[float]:
    """An orthonormal basis vector: `1.0` at `index`, else `0.0`. Two
    DIFFERENT indices have `cosine_similarity` exactly `0.0`; the SAME
    index used for two Decisions gives exactly `1.0`."""
    vector = [0.0] * dims
    vector[index] = 1.0
    return vector


def _vector_at_cosine(
    base_index: int, other_index: int, cosine: float, dims: int
) -> list[float]:
    """A unit vector whose `cosine_similarity` against
    `_unit_vector(base_index, dims)` is exactly `cosine`: it combines the
    `base_index` and `other_index` basis dimensions as
    `cosine * e_base + sin(theta) * e_other`, which is itself a unit vector
    (`cosine**2 + sin(theta)**2 == 1`) and stays exactly orthogonal to
    every other family's basis dimension."""
    vector = [0.0] * dims
    vector[base_index] = cosine
    vector[other_index] = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    return vector


def _mutually_orthogonal_pairs(
    count: int,
) -> list[tuple[str, list[float]]]:
    """`count` one-hot vectors, one per index -- every two returned vectors
    have `cosine_similarity` exactly `0.0` against each other, so `count`
    isolated pair fixtures never accidentally score a cross-pair match
    (mirrors the retired `_mutually_dissimilar_tokens`'s lexical-isolation
    role, now geometrically exact instead of approximated)."""
    return [(f"decisions/pair{i:03d}", _unit_vector(i, count)) for i in range(count)]


def test_plan_revision_candidates_excludes_shared_source_pairs() -> None:
    shared_a = _decision("decisions/shared-a", "billing tool", sources=["sources/one"])
    shared_b = _decision("decisions/shared-b", "billing tool", sources=["sources/one"])
    disjoint = _decision("decisions/disjoint", "billing tool", sources=["sources/two"])
    # All three share the SAME vector (cosine 1.0 every pair) -- proves the
    # source exclusion applies even at the highest possible similarity.
    vectors = {
        "decisions/shared-a": [1.0, 0.0],
        "decisions/shared-b": [1.0, 0.0],
        "decisions/disjoint": [1.0, 0.0],
    }

    plan = decision_revision.plan_revision_candidates(
        [shared_a, shared_b, disjoint], vectors
    )

    pairs = {candidate.pair_ids for candidate in plan.candidates}
    assert ("decisions/shared-a", "decisions/shared-b") not in pairs
    assert ("decisions/disjoint", "decisions/shared-a") in pairs
    assert ("decisions/disjoint", "decisions/shared-b") in pairs


@pytest.mark.parametrize("relation_type", sorted(RESOLUTION_RELATION_TYPES))
@pytest.mark.parametrize("direction", ["a_names_b", "b_names_a"])
def test_plan_revision_candidates_excludes_resolved_pairs(
    relation_type: str, direction: str
) -> None:
    # `relation_type` documents which of the three RESOLUTION_RELATION_TYPES
    # this exclusion covers (decision-revision-detection scenario "A
    # resolved pair is excluded before the count"); the pure function
    # itself takes `resolved_with` as an already-type-filtered frozenset,
    # so the exclusion rule is identical for every member.
    del relation_type
    if direction == "a_names_b":
        a = _decision(
            "decisions/a",
            "billing tool",
            sources=["sources/a"],
            resolved_with=["decisions/b"],
        )
        b = _decision("decisions/b", "billing tool", sources=["sources/b"])
    else:
        a = _decision("decisions/a", "billing tool", sources=["sources/a"])
        b = _decision(
            "decisions/b",
            "billing tool",
            sources=["sources/b"],
            resolved_with=["decisions/a"],
        )
    # Identical vectors -- cosine 1.0 -- so only the resolution exclusion
    # can be why this pair is dropped.
    vectors = {"decisions/a": [1.0, 0.0], "decisions/b": [1.0, 0.0]}

    plan = decision_revision.plan_revision_candidates([a, b], vectors)

    assert plan.candidates == ()
    assert plan.total == 0


def test_plan_revision_candidates_paraphrase_with_no_lexical_overlap_is_a_candidate() -> (
    None
):
    """A pair whose SUBJECTS share no lexical token at all is still a
    candidate when their embeddings are close -- the whole point of
    sub-change 3: `subject_overlap("billing tool", "payment processor")`
    is `0.0` (no shared token), but candidate blocking no longer reads
    `subject` at all."""
    paraphrase_a = _decision(
        "decisions/paraphrase-a", "billing tool", sources=["sources/1"]
    )
    paraphrase_b = _decision(
        "decisions/paraphrase-b", "payment processor", sources=["sources/2"]
    )
    assert decision_revision.subject_overlap("billing tool", "payment processor") == 0.0
    vectors = {
        "decisions/paraphrase-a": [1.0, 0.0],
        "decisions/paraphrase-b": [0.9, math.sqrt(1 - 0.9**2)],
    }

    plan = decision_revision.plan_revision_candidates(
        [paraphrase_a, paraphrase_b], vectors
    )

    assert len(plan.candidates) == 1
    only_candidate = plan.candidates[0]
    assert only_candidate.pair_ids == (
        "decisions/paraphrase-a",
        "decisions/paraphrase-b",
    )
    assert only_candidate.score == pytest.approx(0.9)


def test_plan_revision_candidates_at_threshold_is_kept_below_threshold_is_dropped() -> (
    None
):
    threshold = decision_revision.EMBEDDING_SIMILARITY_THRESHOLD
    at_a = _decision("decisions/at-a", "alpha", sources=["sources/1"])
    at_b = _decision("decisions/at-b", "alpha", sources=["sources/2"])
    at_vectors = {
        "decisions/at-a": _unit_vector(0, 2),
        "decisions/at-b": _vector_at_cosine(0, 1, threshold, 2),
    }
    kept_plan = decision_revision.plan_revision_candidates([at_a, at_b], at_vectors)
    assert len(kept_plan.candidates) == 1
    assert kept_plan.candidates[0].score == pytest.approx(threshold)

    below_a = _decision("decisions/below-a", "alpha", sources=["sources/3"])
    below_b = _decision("decisions/below-b", "alpha", sources=["sources/4"])
    below_vectors = {
        "decisions/below-a": _unit_vector(0, 2),
        "decisions/below-b": _vector_at_cosine(0, 1, threshold - 0.01, 2),
    }
    dropped_plan = decision_revision.plan_revision_candidates(
        [below_a, below_b], below_vectors
    )
    assert dropped_plan.candidates == ()


def test_plan_revision_candidates_counts_decisions_without_a_vector() -> None:
    no_vector = _decision("decisions/no-vector", "billing tool", sources=["sources/1"])
    with_vector_a = _decision("decisions/with-a", "billing tool", sources=["sources/2"])
    with_vector_b = _decision("decisions/with-b", "billing tool", sources=["sources/3"])
    # `decisions/no-vector` is deliberately ABSENT from `vectors` -- a
    # missing entry, not a present-but-empty one.
    vectors = {
        "decisions/with-a": [1.0, 0.0],
        "decisions/with-b": [1.0, 0.0],
    }

    plan = decision_revision.plan_revision_candidates(
        [no_vector, with_vector_a, with_vector_b], vectors
    )

    assert plan.without_vector == 1
    pair_concept_ids = {cid for pair in plan.candidates for cid in pair.pair_ids}
    assert "decisions/no-vector" not in pair_concept_ids


def test_plan_revision_candidates_a_zero_vector_does_not_crash_and_is_no_candidate() -> (
    None
):
    """A Decision whose vector is present but degenerate (all-zero) is
    NOT counted as `without_vector` (it has an entry), but
    `cosine_similarity` fails closed to `0.0` against it, so it forms no
    candidate and, critically, raises nothing."""
    zero = _decision("decisions/zero", "billing tool", sources=["sources/1"])
    normal = _decision("decisions/normal", "billing tool", sources=["sources/2"])
    vectors = {
        "decisions/zero": [0.0, 0.0],
        "decisions/normal": [1.0, 0.0],
    }

    plan = decision_revision.plan_revision_candidates([zero, normal], vectors)

    assert plan.without_vector == 0
    assert plan.candidates == ()


def test_plan_revision_candidates_top_k_is_a_union() -> None:
    hub = _decision("decisions/hub", "billing tool", sources=["sources/hub"])
    names = [f"p{i}" for i in range(1, 8)]
    partners = [
        _decision(f"decisions/{name}", "billing tool", sources=[f"sources/{name}"])
        for name in names
    ]
    dims = 1 + len(names)
    # Every hub/partner cosine ties at 0.8 (>= 0.65, a candidate); every
    # partner/partner cosine is 0.8**2 = 0.64 (< 0.65, dropped) -- the
    # geometric equivalent of the retired lexical fixture's "hub ties with
    # everyone, partners don't pair with each other" shape (see
    # `_vector_at_cosine`'s docstring for why the cross term vanishes).
    vectors = {"decisions/hub": _unit_vector(0, dims)}
    for index, name in enumerate(names, start=1):
        vectors[f"decisions/{name}"] = _vector_at_cosine(0, index, 0.8, dims)

    plan = decision_revision.plan_revision_candidates([hub, *partners], vectors)

    hub_pairs = {
        candidate.pair_ids
        for candidate in plan.candidates
        if "decisions/hub" in candidate.pair_ids
    }
    expected = {tuple(sorted(("decisions/hub", f"decisions/{name}"))) for name in names}
    partner_pairs = {
        candidate.pair_ids
        for candidate in plan.candidates
        if "decisions/hub" not in candidate.pair_ids
    }
    # All 7 hub/partner pairs tie at score 0.8. The hub's OWN top-5 (by
    # partner id) keeps only p1-p5; p6 and p7 survive ONLY because each
    # independently ranks the hub inside its own top-5 (it has no other
    # eligible partner) -- proving top-k is a UNION across both sides, not
    # an intersection. No partner/partner pair survives (0.64 < 0.65).
    assert hub_pairs == expected
    assert partner_pairs == set()


def test_plan_revision_candidates_ordering() -> None:
    tied_a = _decision("decisions/tied-a", "billing tool", sources=["sources/1"])
    tied_b = _decision("decisions/tied-b", "billing tool", sources=["sources/2"])
    tied_c = _decision("decisions/tied-c", "billing tool", sources=["sources/3"])
    lower_a = _decision("decisions/lower-a", "alpha", sources=["sources/4"])
    lower_b = _decision("decisions/lower-b", "alpha", sources=["sources/5"])
    # tied-a/b/c share one vector (cosine 1.0 every combo); lower-a/b sit
    # at cosine 0.7 -- above the 0.65 threshold, so still a candidate, but
    # strictly below the tied trio's 1.0.
    vectors = {
        "decisions/tied-a": _unit_vector(0, 3),
        "decisions/tied-b": _unit_vector(0, 3),
        "decisions/tied-c": _unit_vector(0, 3),
        "decisions/lower-a": _unit_vector(1, 3),
        "decisions/lower-b": _vector_at_cosine(1, 2, 0.7, 3),
    }

    plan = decision_revision.plan_revision_candidates(
        [tied_a, tied_b, tied_c, lower_a, lower_b], vectors, top_k=10, cap=10
    )

    scores = [candidate.score for candidate in plan.candidates]
    assert scores == sorted(scores, reverse=True)
    tied_pairs = [
        candidate.pair_ids
        for candidate in plan.candidates
        if candidate.score == pytest.approx(1.0)
    ]
    assert tied_pairs == sorted(tied_pairs)
    assert plan.candidates[-1].pair_ids == ("decisions/lower-a", "decisions/lower-b")


def test_plan_revision_candidates_cap_and_truncation_notice() -> None:
    pairs = _mutually_orthogonal_pairs(205)
    decisions = [
        _decision(f"{concept_id}{side}", token_id, sources=[f"{concept_id}{side}"])
        for concept_id, _vector in pairs
        for token_id, side in ((concept_id, "a"), (concept_id, "b"))
    ]
    vectors = {
        f"{concept_id}{side}": vector
        for concept_id, vector in pairs
        for side in ("a", "b")
    }

    plan = decision_revision.plan_revision_candidates(decisions, vectors)

    assert plan.total == 205
    assert len(plan.candidates) == 200
    assert (
        decision_revision.revision_truncation_notice(plan)
        == "200 of 205 candidate pair(s) shown (cap reached); dropped: 5"
    )

    under_cap_decisions = decisions[:40]
    under_cap_plan = decision_revision.plan_revision_candidates(
        under_cap_decisions, vectors
    )
    assert decision_revision.revision_truncation_notice(under_cap_plan) is None


def test_plan_revision_candidates_exclusions_applied_before_the_cap() -> None:
    pairs = _mutually_orthogonal_pairs(206)
    decisions = [
        _decision(f"{concept_id}{side}", token_id, sources=[f"{concept_id}{side}"])
        for concept_id, _vector in pairs[:205]
        for token_id, side in ((concept_id, "a"), (concept_id, "b"))
    ]
    vectors = {
        f"{concept_id}{side}": vector
        for concept_id, vector in pairs[:205]
        for side in ("a", "b")
    }
    resolved_concept_id, resolved_vector = pairs[205]
    resolved_a = _decision(
        f"{resolved_concept_id}a",
        resolved_concept_id,
        sources=[f"{resolved_concept_id}a"],
        resolved_with=[f"{resolved_concept_id}b"],
    )
    resolved_b = _decision(
        f"{resolved_concept_id}b",
        resolved_concept_id,
        sources=[f"{resolved_concept_id}b"],
    )
    decisions.extend([resolved_a, resolved_b])
    vectors[f"{resolved_concept_id}a"] = resolved_vector
    vectors[f"{resolved_concept_id}b"] = resolved_vector

    plan = decision_revision.plan_revision_candidates(decisions, vectors)

    # If the resolved pair were dropped AFTER the cap (post-cap filtering)
    # rather than excluded before it, `total` would read 206 (it would have
    # been counted) even though `candidates` excludes it -- this pins that
    # both `total` and `candidates` reflect the SAME pre-exclusion set.
    assert plan.total == 205
    assert len(plan.candidates) == 200
    pair_concept_ids = {cid for pair in plan.candidates for cid in pair.pair_ids}
    assert f"{resolved_concept_id}a" not in pair_concept_ids
    assert f"{resolved_concept_id}b" not in pair_concept_ids


# ---------------------------------------------------------------------------
# The judge (Slice 4 / S4)
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """A structural `LLMBackend`: returns queued replies in call order,
    recording every call's messages. Byte-identical shape to
    `test_decision_subject.py`'s double."""

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        return self._replies.pop(0)


class _RaisingLLM:
    """A structural `LLMBackend`: raises `error` on its `error_at`-th
    (1-based) call, otherwise returns the next queued reply."""

    def __init__(
        self,
        replies: Sequence[str],
        *,
        error: BaseException,
        error_at: int,
    ) -> None:
        self._replies = list(replies)
        self.error = error
        self.error_at = error_at
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if len(self.calls) == self.error_at:
            raise self.error
        return self._replies.pop(0)


def _judge_side(
    concept_id: str,
    title: str,
    body: str,
    *,
    date_value: date | None = None,
    date_state: DateState = "missing",
) -> decision_revision.JudgeSide:
    return decision_revision.JudgeSide(
        concept_id=concept_id,
        title=title,
        body=body,
        date=DecisionDate(value=date_value, state=date_state),
    )


# ---------------------------------------------------------------------------
# build_judge_messages
# ---------------------------------------------------------------------------


def test_build_judge_messages_known_direction_orders_earlier_first() -> None:
    earlier = _judge_side(
        "decisions/early",
        "Use Postgres",
        "We chose Postgres for billing.",
        date_value=date(2026, 1, 1),
        date_state="dated",
    )
    later = _judge_side(
        "decisions/late",
        "Use SQLite",
        "Billing moves to SQLite; Postgres is dropped.",
        date_value=date(2026, 3, 1),
        date_state="dated",
    )

    # Argument order is deliberately reversed from chronological/id order,
    # to prove presentation order comes from `pair_direction`, never from
    # the order `a`/`b` are passed in.
    messages = decision_revision.build_judge_messages(later, earlier)

    assert messages[0] == {
        "role": "system",
        "content": decision_revision._JUDGE_SYSTEM_PROMPT,
    }
    user_content = messages[1]["content"]
    assert (
        "EARLIER DECISION (2026-01-01) [decisions/early — Use Postgres]" in user_content
    )
    assert "LATER DECISION (2026-03-01) [decisions/late — Use SQLite]" in user_content
    assert user_content.index("EARLIER DECISION") < user_content.index("LATER DECISION")
    assert user_content.index(earlier.body) < user_content.index(later.body)


def test_build_judge_messages_unknown_direction_orders_by_id() -> None:
    first_by_id = _judge_side("decisions/aaa", "Aaa", "Aaa body sentence.")
    second_by_id = _judge_side("decisions/bbb", "Bbb", "Bbb body sentence.")

    # Argument order reversed from id order, same reason as above.
    messages = decision_revision.build_judge_messages(second_by_id, first_by_id)

    user_content = messages[1]["content"]
    assert user_content.startswith(
        "ORDER UNKNOWN: the dates of these decisions do not establish which came first."
    )
    assert "DECISION 1 [decisions/aaa — Aaa]" in user_content
    assert "DECISION 2 [decisions/bbb — Bbb]" in user_content
    assert user_content.index("DECISION 1") < user_content.index("DECISION 2")
    assert user_content.index(first_by_id.body) < user_content.index(second_by_id.body)


# ---------------------------------------------------------------------------
# parse_judge_reply
# ---------------------------------------------------------------------------


def test_parse_judge_reply_direction_is_structurally_unreadable() -> None:
    """design.md's stated success criterion for Decision 6: an extra
    `"later"` field naming the WRONG (earlier-dated) Decision, plus quotes
    that could be read as implying the same wrong order, never changes the
    `RevisionVerdict.direction` a caller later builds -- direction is a
    `@property` computed from the stored `dates`, and the parser reads
    only the five schema keys."""
    earlier_date = DecisionDate(value=date(2026, 1, 1), state="dated")
    later_date = DecisionDate(value=date(2026, 3, 1), state="dated")
    pair_ids = ("decisions/early", "decisions/late")
    first_body = "We chose Postgres for billing."
    second_body = "Billing moves to SQLite; Postgres is dropped."

    raw = json.dumps(
        {
            "verdict": "reverses",
            "confidence": 0.9,
            "rationale": "Billing tool changed.",
            "quote_first": second_body,
            "quote_second": first_body,
            "later": "decisions/early",  # extra field claiming the WRONG order
        }
    )

    parsed = decision_revision.parse_judge_reply(raw, first_body, second_body)
    assert parsed is not None

    verdict = decision_revision.RevisionVerdict(
        pair_ids=pair_ids,
        verdict=parsed.verdict,
        confidence=parsed.confidence,
        rationale=parsed.rationale,
        quotes=(parsed.quote_first, parsed.quote_second),
        dates=(earlier_date, later_date),
    )

    assert verdict.direction.holder == "decisions/late"
    assert verdict.direction.earlier == "decisions/early"


def test_parse_judge_reply_unknown_verdict_maps_to_unrelated_keeping_confidence() -> (
    None
):
    raw = json.dumps({"verdict": "supersedes", "confidence": 0.6})
    parsed = decision_revision.parse_judge_reply(raw, "body one.", "body two.")
    assert parsed is not None
    assert parsed.verdict is decision_revision.RevisionVerdictValue.UNRELATED
    assert parsed.confidence == pytest.approx(0.6)


def _parse_with_confidence(confidence_value: object, *, omit: bool = False) -> float:
    payload: dict[str, object] = {"verdict": "unrelated"}
    if not omit:
        payload["confidence"] = confidence_value
    parsed = decision_revision.parse_judge_reply(json.dumps(payload), "b1", "b2")
    assert parsed is not None
    return parsed.confidence


def test_parse_judge_reply_confidence_coercion() -> None:
    assert _parse_with_confidence(math.nan) == 0.0
    assert _parse_with_confidence(True) == 0.0
    assert _parse_with_confidence("0.9") == 0.0
    assert _parse_with_confidence(None, omit=True) == 0.0
    assert _parse_with_confidence(0.85) == pytest.approx(0.85)
    assert _parse_with_confidence(1.5) == 1.0
    assert _parse_with_confidence(-0.5) == 0.0


def test_parse_judge_reply_rationale_non_string_becomes_empty() -> None:
    raw = json.dumps({"verdict": "unrelated", "rationale": 42})
    parsed = decision_revision.parse_judge_reply(raw, "b1", "b2")
    assert parsed is not None
    assert parsed.rationale == ""


def test_parse_judge_reply_quotes_verified_and_mapped_by_presentation_order() -> None:
    # `earlier`'s id sorts alphabetically AFTER `later`'s id, so
    # presentation order (earlier-first) and sorted-id order disagree.
    earlier_body = "Priya owns the schema migration plan."
    later_body = "The schema migration plan moves to a new owner."
    assert "decisions/zzz-earlier" > "decisions/aaa-later"

    raw = json.dumps(
        {
            "verdict": "refines",
            "confidence": 0.9,
            "quote_first": earlier_body,
            "quote_second": later_body,
        }
    )

    parsed = decision_revision.parse_judge_reply(raw, earlier_body, later_body)
    assert parsed is not None
    assert parsed.quote_first == earlier_body
    assert parsed.quote_second == later_body

    mismatched_raw = json.dumps(
        {
            "verdict": "refines",
            "confidence": 0.9,
            "quote_first": "A wholly different sentence.",
            "quote_second": later_body,
        }
    )
    mismatched = decision_revision.parse_judge_reply(
        mismatched_raw, earlier_body, later_body
    )
    assert mismatched is not None
    # A quote that fails verification against its OWN side's body becomes
    # `None` on that side only.
    assert mismatched.quote_first is None
    assert mismatched.quote_second == later_body


# ---------------------------------------------------------------------------
# is_actionable_revision / is_reportable_revision / RELATION_FOR_VERDICT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", list(decision_revision.RevisionVerdictValue))
@pytest.mark.parametrize("confidence", [0.69, 0.70])
@pytest.mark.parametrize(
    ("quote_0", "quote_1"),
    [
        ("earlier quote", "later quote"),
        (None, "later quote"),
        (None, None),
    ],
)
def test_is_actionable_revision_truth_table(
    verdict: decision_revision.RevisionVerdictValue,
    confidence: float,
    quote_0: str | None,
    quote_1: str | None,
) -> None:
    result = decision_revision.is_actionable_revision(
        verdict.value, confidence, quote_0, quote_1
    )
    expected = (
        verdict
        in (
            decision_revision.RevisionVerdictValue.REVERSES,
            decision_revision.RevisionVerdictValue.REFINES,
        )
        and confidence >= 0.70
        and quote_0 is not None
        and quote_1 is not None
    )
    assert result is expected


def test_is_reportable_revision_includes_reaffirms_but_not_unrelated() -> None:
    reaffirms = decision_revision.RevisionVerdictValue.REAFFIRMS.value
    unrelated = decision_revision.RevisionVerdictValue.UNRELATED.value

    assert (
        decision_revision.is_reportable_revision(
            reaffirms, 0.70, "earlier quote", "later quote"
        )
        is True
    )
    assert (
        decision_revision.is_actionable_revision(
            reaffirms, 0.70, "earlier quote", "later quote"
        )
        is False
    )
    assert (
        decision_revision.is_reportable_revision(
            unrelated, 1.0, "earlier quote", "later quote"
        )
        is False
    )


def test_relation_for_verdict_matches_resolution_relation_types() -> None:
    assert set(
        decision_revision.RELATION_FOR_VERDICT.values()
    ) == RESOLUTION_RELATION_TYPES - {"reconciled_with"}


# ---------------------------------------------------------------------------
# RevisionVerdict.is_untyped_change / relation_for (#1014 Plan 2: an
# undirected REVERSES/REFINES is an untyped CHANGE -- measured, the judge
# never answers REFINES without an established order (0 of 30 undirected
# pairs), so the engine must not infer a relation type from an undirected
# verdict; the person picks both the later side and the type in reconcile.
# ---------------------------------------------------------------------------


def _revision_verdict(
    verdict_value: decision_revision.RevisionVerdictValue, *, directed: bool
) -> decision_revision.RevisionVerdict:
    """One `RevisionVerdict` over a fixed pair, `directed` choosing whether
    `.direction.holder` resolves (both sides dated, distinct values) or not
    (both sides `missing`) -- the only two inputs `is_untyped_change`/
    `relation_for` read besides `verdict` itself."""
    pair_ids = ("decisions/a", "decisions/b")
    if directed:
        dates = (
            DecisionDate(value=date(2026, 1, 1), state="dated"),
            DecisionDate(value=date(2026, 3, 1), state="dated"),
        )
    else:
        dates = (
            DecisionDate(value=None, state="missing"),
            DecisionDate(value=None, state="missing"),
        )
    return decision_revision.RevisionVerdict(
        pair_ids=pair_ids,
        verdict=verdict_value,
        confidence=0.9,
        rationale="",
        quotes=("earlier quote", "later quote"),
        dates=dates,
    )


@pytest.mark.parametrize("directed", [True, False])
@pytest.mark.parametrize("verdict_value", list(decision_revision.RevisionVerdictValue))
def test_is_untyped_change_and_relation_for_truth_table(
    verdict_value: decision_revision.RevisionVerdictValue, directed: bool
) -> None:
    """Directed REVERSES -> `supersedes`, directed REFINES -> `revises`
    (`RELATION_FOR_VERDICT` unchanged); undirected REVERSES and undirected
    REFINES -> `is_untyped_change=True`, `relation_for(...) is None`;
    REAFFIRMS/UNRELATED are never untyped-changed and never get a relation,
    directed or not -- `RELATION_FOR_VERDICT` has no entry for either."""
    verdict = _revision_verdict(verdict_value, directed=directed)

    expected_untyped = not directed and verdict_value in (
        decision_revision.RevisionVerdictValue.REVERSES,
        decision_revision.RevisionVerdictValue.REFINES,
    )
    assert verdict.is_untyped_change is expected_untyped

    relation = decision_revision.relation_for(verdict)
    if expected_untyped:
        assert relation is None
    else:
        assert relation == decision_revision.RELATION_FOR_VERDICT.get(verdict_value)


def test_relation_for_never_types_an_undirected_reverses_or_refines() -> None:
    """Named explicitly (not just as one parametrized cell): the two cases
    #1014 Plan 2 exists for -- an undirected REVERSES and an undirected
    REFINES both fail to type, even though `RELATION_FOR_VERDICT` itself
    maps both verdicts when directed."""
    undirected_reverses = _revision_verdict(
        decision_revision.RevisionVerdictValue.REVERSES, directed=False
    )
    undirected_refines = _revision_verdict(
        decision_revision.RevisionVerdictValue.REFINES, directed=False
    )
    assert decision_revision.relation_for(undirected_reverses) is None
    assert decision_revision.relation_for(undirected_refines) is None
    assert (
        decision_revision.RELATION_FOR_VERDICT[
            decision_revision.RevisionVerdictValue.REVERSES
        ]
        == "supersedes"
    )
    assert (
        decision_revision.RELATION_FOR_VERDICT[
            decision_revision.RevisionVerdictValue.REFINES
        ]
        == "revises"
    )


def test_is_actionable_revision_unaffected_by_undirected_change() -> None:
    """`is_actionable_revision`/`is_reportable_revision` keep their current
    contracts (#1014 Plan 2): an undirected REVERSES/REFINES with
    confidence and both quotes verified is still actionable -- the human
    types it in `reconcile`'s combined prompt, so the engine must still
    surface it, only without a pre-picked relation type."""
    assert (
        decision_revision.is_actionable_revision(
            decision_revision.RevisionVerdictValue.REVERSES.value,
            0.9,
            "earlier quote",
            "later quote",
        )
        is True
    )


# ---------------------------------------------------------------------------
# judge_pairs
# ---------------------------------------------------------------------------


def test_judge_pairs_partial_batch_on_llm_failure() -> None:
    pairs = [
        (
            _judge_side(f"decisions/a{i}", f"A{i}", f"Body a{i}."),
            _judge_side(f"decisions/b{i}", f"B{i}", f"Body b{i}."),
        )
        for i in range(3)
    ]
    well_formed_reply = json.dumps({"verdict": "unrelated", "confidence": 0.1})
    error = OllamaUnavailable("backend down")
    llm = _RaisingLLM([well_formed_reply], error=error, error_at=2)

    batch = decision_revision.judge_pairs(pairs, llm=llm)

    assert len(batch.results) == 1
    assert batch.failure is error
    assert batch.failed_index == 2
    assert len(llm.calls) == 2


def test_judge_pairs_malformed_reply_degrades_without_aborting() -> None:
    malformed_pair = (
        _judge_side("decisions/malformed-a", "Malformed A", "Body malformed a."),
        _judge_side("decisions/malformed-b", "Malformed B", "Body malformed b."),
    )
    well_formed_pair = (
        _judge_side("decisions/well-a", "Well A", "Body well a."),
        _judge_side("decisions/well-b", "Well B", "Body well b."),
    )
    malformed_reply = "not json"
    well_formed_reply = json.dumps({"verdict": "refines", "confidence": 0.8})
    llm = _ScriptedLLM([malformed_reply, well_formed_reply])

    batch = decision_revision.judge_pairs([malformed_pair, well_formed_pair], llm=llm)

    assert batch.failure is None
    assert len(batch.results) == 2
    malformed_result, well_formed_result = batch.results
    assert malformed_result.malformed is True
    assert malformed_result.verdict is decision_revision.RevisionVerdictValue.UNRELATED
    assert malformed_result.confidence == 0.0
    assert malformed_result.rationale == decision_revision._MALFORMED_REPLY_RATIONALE
    assert malformed_result.quotes == (None, None)
    assert well_formed_result.malformed is False
    assert well_formed_result.verdict is decision_revision.RevisionVerdictValue.REFINES


def test_judge_pairs_guards_only_the_chat_call() -> None:
    """The `OllamaError` guard wraps ONLY `llm.chat` (design.md, mirroring
    `find_contradictions`'s #441 contract, and `decision_subject
    .derive_subjects`'s own guard test): an `OllamaError` raised by the
    caller's own `on_progress` -- code that runs AFTER the guarded call --
    must still propagate untouched, never be caught and folded into
    `RevisionBatch.failure`."""
    pair = (
        _judge_side("decisions/only-a", "Only A", "Body only a."),
        _judge_side("decisions/only-b", "Only B", "Body only b."),
    )
    llm = _ScriptedLLM([json.dumps({"verdict": "unrelated", "confidence": 0.1})])
    progress_error = OllamaUnavailable("on_progress exploded")

    def _raising_progress(
        index: int, total: int, verdict: decision_revision.RevisionVerdict
    ) -> None:
        raise progress_error

    with pytest.raises(OllamaUnavailable):
        decision_revision.judge_pairs([pair], llm=llm, on_progress=_raising_progress)


def test_judge_prompt_separates_a_narrowed_choice_from_an_overturned_one() -> None:
    """The harness (#1014) found a choice kept in force but narrowed by an
    exception or limit read as REVERSES. Pin the two definitions that
    separate them, so a later prompt edit cannot drop either rule silently.
    `unrelated` is deliberately left as it was: a clause there cost
    undirected change recall in the A/B."""
    lines = {
        line.split(":", 1)[0].removeprefix("- "): line
        for line in decision_revision._JUDGE_SYSTEM_PROMPT.splitlines()
        if line.startswith("- ")
    }

    assert "no longer applies" in lines["reverses"]
    assert "an exception, a limit, or an extra option" in lines["refines"]
    assert "otherwise continues" in lines["refines"]
    assert lines["unrelated"] == (
        "- unrelated: the two concern a different subject, or one has no bearing "
        "on the other."
    )
