"""Unit tests for `resolution/decision_revision.py`: the pure direction
rule, subject-overlap scoring, candidate generation, and the judge
(decision-revision-detector, Phase A, Slices 3 and 4 / S3-S4).

Slices 3's tests are pure -- no `LLMBackend` double is needed. Slice 4's
judge tests use a module-local `_ScriptedLLM`/`_RaisingLLM` double
(byte-identical shape to `test_decision_subject.py`'s) -- zero network,
zero real Ollama process.
"""

import itertools
import json
import math
import string
from collections.abc import Iterable, Sequence
from datetime import date
from difflib import SequenceMatcher

import pytest

from openkos.llm.base import Message
from openkos.llm.ollama import OllamaUnavailable
from openkos.model.relations import RESOLUTION_RELATION_TYPES
from openkos.resolution import decision_revision, similarity
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


def _mutually_dissimilar_tokens(count: int) -> list[str]:
    """`count` tokens, each a double-letter-run pair (e.g. `"aaabbb"`),
    generated so every two returned tokens have a `SequenceMatcher.ratio()`
    strictly below `similarity.SIMILARITY_THRESHOLD` against EVERY token
    already returned -- i.e. two "isolated" fixture Decisions built from
    two different tokens never accidentally score a `subject_overlap`
    match. Generated (deterministically, via `itertools.permutations` over
    `string.ascii_lowercase`) rather than hardcoded, so a large fixture
    stays legible and auditable instead of being an opaque literal list."""
    tokens: list[str] = []
    for first, second in itertools.permutations(string.ascii_lowercase, 2):
        candidate = first * 3 + second * 3
        if all(
            SequenceMatcher(None, candidate, existing).ratio()
            < similarity.SIMILARITY_THRESHOLD
            for existing in tokens
        ):
            tokens.append(candidate)
        if len(tokens) >= count:
            break
    assert len(tokens) == count
    return tokens


def test_plan_revision_candidates_excludes_shared_source_pairs() -> None:
    shared_a = _decision("decisions/shared-a", "billing tool", sources=["sources/one"])
    shared_b = _decision("decisions/shared-b", "billing tool", sources=["sources/one"])
    disjoint = _decision("decisions/disjoint", "billing tool", sources=["sources/two"])

    plan = decision_revision.plan_revision_candidates([shared_a, shared_b, disjoint])

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

    plan = decision_revision.plan_revision_candidates([a, b])

    assert plan.candidates == ()
    assert plan.total == 0


def test_plan_revision_candidates_drops_pairs_below_the_overlap_threshold() -> None:
    at_threshold_a = _decision("decisions/at-a", "alpha bravo", sources=["sources/1"])
    at_threshold_b = _decision("decisions/at-b", "alpha zulu", sources=["sources/2"])
    kept_plan = decision_revision.plan_revision_candidates(
        [at_threshold_a, at_threshold_b]
    )
    assert len(kept_plan.candidates) == 1
    assert kept_plan.candidates[0].score == pytest.approx(0.5)

    below_a = _decision("decisions/below-a", "alpha bravo", sources=["sources/3"])
    below_b = _decision("decisions/below-b", "charlie delta", sources=["sources/4"])
    dropped_plan = decision_revision.plan_revision_candidates([below_a, below_b])
    assert dropped_plan.candidates == ()


def test_plan_revision_candidates_counts_decisions_without_a_subject() -> None:
    no_subject = _decision("decisions/no-subject", None, sources=["sources/1"])
    with_subject_a = _decision(
        "decisions/with-a", "billing tool", sources=["sources/2"]
    )
    with_subject_b = _decision(
        "decisions/with-b", "billing tool", sources=["sources/3"]
    )

    plan = decision_revision.plan_revision_candidates(
        [no_subject, with_subject_a, with_subject_b]
    )

    assert plan.without_subject == 1
    pair_concept_ids = {cid for pair in plan.candidates for cid in pair.pair_ids}
    assert "decisions/no-subject" not in pair_concept_ids


def test_plan_revision_candidates_top_k_is_a_union() -> None:
    hub = _decision("decisions/hub", "billing tool", sources=["sources/hub"])
    groups = [
        ("p1", ["quartz", "jungle", "marble", "violet"]),
        ("p2", ["cactus", "dragon", "falcon", "gizmo"]),
        ("p3", ["harbor", "igloo", "jockey", "kernel"]),
        ("p4", ["lentil", "mirror", "nickel", "oyster"]),
        ("p5", ["pepper", "quiver", "raptor", "salmon"]),
        ("p6", ["tundra", "umpire", "velvet", "walrus"]),
        ("p7", ["xenon", "yeoman", "anchor", "bishop"]),
    ]
    # Every partner's subject is "billing tool" plus 4 tokens unique to
    # that partner. Against the hub (2 tokens), the smaller set is the
    # hub's own 2 tokens, both found in every partner -> score 1.0 for
    # every hub/partner pair, a tie. Against each other, the smaller set
    # is 6 tokens with only 2 (billing, tool) ever matching -> 2/6 = 0.333,
    # below `SUBJECT_OVERLAP_THRESHOLD`, so partners never pair with each
    # other -- the fixture isolates the hub's fan-out from any partner's
    # OWN unrelated candidates.
    partners = [
        _decision(
            f"decisions/{name}",
            "billing tool " + " ".join(words),
            sources=[f"sources/{name}"],
        )
        for name, words in groups
    ]

    plan = decision_revision.plan_revision_candidates([hub, *partners])

    hub_pairs = {
        candidate.pair_ids
        for candidate in plan.candidates
        if "decisions/hub" in candidate.pair_ids
    }
    expected = {
        tuple(sorted(("decisions/hub", f"decisions/{name}"))) for name, _ in groups
    }
    # All 7 hub/partner pairs tie at score 1.0. The hub's OWN top-5 (by
    # partner id) keeps only p1-p5; p6 and p7 survive ONLY because each
    # independently ranks the hub inside its own top-5 (it has no other
    # eligible partner) -- proving top-k is a UNION across both sides, not
    # an intersection.
    assert hub_pairs == expected


def test_plan_revision_candidates_ordering() -> None:
    tied_a = _decision("decisions/tied-a", "billing tool", sources=["sources/1"])
    tied_b = _decision("decisions/tied-b", "billing tool", sources=["sources/2"])
    tied_c = _decision("decisions/tied-c", "billing tool", sources=["sources/3"])
    lower_a = _decision("decisions/lower-a", "alpha bravo", sources=["sources/4"])
    lower_b = _decision("decisions/lower-b", "alpha zulu", sources=["sources/5"])

    plan = decision_revision.plan_revision_candidates(
        [tied_a, tied_b, tied_c, lower_a, lower_b], top_k=10, cap=10
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
    tokens = _mutually_dissimilar_tokens(205)
    decisions = [
        _decision(
            f"decisions/pair{i:03d}{side}",
            token,
            sources=[f"sources/pair{i:03d}{side}"],
        )
        for i, token in enumerate(tokens)
        for side in ("a", "b")
    ]

    plan = decision_revision.plan_revision_candidates(decisions)

    assert plan.total == 205
    assert len(plan.candidates) == 200
    assert (
        decision_revision.revision_truncation_notice(plan)
        == "200 of 205 candidate pair(s) shown (cap reached); dropped: 5"
    )

    under_cap_plan = decision_revision.plan_revision_candidates(decisions[:20])
    assert decision_revision.revision_truncation_notice(under_cap_plan) is None


def test_plan_revision_candidates_exclusions_applied_before_the_cap() -> None:
    tokens = _mutually_dissimilar_tokens(206)
    decisions = [
        _decision(
            f"decisions/pair{i:03d}{side}",
            token,
            sources=[f"sources/pair{i:03d}{side}"],
        )
        for i, token in enumerate(tokens[:205])
        for side in ("a", "b")
    ]
    resolved_token = tokens[205]
    resolved_a = _decision(
        "decisions/resolved-a",
        resolved_token,
        sources=["sources/resolved-a"],
        resolved_with=["decisions/resolved-b"],
    )
    resolved_b = _decision(
        "decisions/resolved-b", resolved_token, sources=["sources/resolved-b"]
    )
    decisions.extend([resolved_a, resolved_b])

    plan = decision_revision.plan_revision_candidates(decisions)

    # If the resolved pair were dropped AFTER the cap (post-cap filtering)
    # rather than excluded before it, `total` would read 206 (it would have
    # been counted) even though `candidates` excludes it -- this pins that
    # both `total` and `candidates` reflect the SAME pre-exclusion set.
    assert plan.total == 205
    assert len(plan.candidates) == 200
    pair_concept_ids = {cid for pair in plan.candidates for cid in pair.pair_ids}
    assert "decisions/resolved-a" not in pair_concept_ids
    assert "decisions/resolved-b" not in pair_concept_ids


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
