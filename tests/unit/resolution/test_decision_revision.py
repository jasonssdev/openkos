"""Unit tests for `resolution/decision_revision.py`: the pure direction
rule, subject-overlap scoring, and candidate generation
(decision-revision-detector, Phase A, Slice 3 / S3).

All tests are pure -- no `LLMBackend` double is needed for this slice; the
judge (Slice 4) is a separate task list.
"""

import itertools
import string
from collections.abc import Iterable
from datetime import date
from difflib import SequenceMatcher

import pytest

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
