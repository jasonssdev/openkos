"""Unit tests for `openkos.application.revisions_report` (#1014 piece (a),
Phase B re-plan, Slice P7a): the pure `revisions` report renderer over
`revisions.RevisionPlan`/`revisions.RevisionOutcome`-shaped inputs
(design.md Decision 8, as revised by Decision B4).

No CLI, no bundle, no LLM: every fixture is built directly from the
service-layer dataclasses (`RevisionPlan`, `RevisionOutcome`,
`VectorCoverage`, `decision_revision.RevisionCandidate(Plan)`,
`decision_revision.RevisionVerdict`, `revision_findings.RevisionFinding`),
mirroring `test_decision_revision.py`'s and `test_revisions_service.py`'s
own posture of exercising pure functions over hand-built values."""

from datetime import date

import pytest

from openkos.application import revisions, revisions_report
from openkos.resolution import decision_revision
from openkos.resolution.decision_revision import DecisionDate, RevisionVerdictValue
from openkos.state import revision_findings


def _candidate_plan(
    pairs: list[tuple[str, str]],
) -> decision_revision.RevisionCandidatePlan:
    candidates = tuple(
        decision_revision.RevisionCandidate(pair_ids=pair, score=0.9) for pair in pairs
    )
    return decision_revision.RevisionCandidatePlan(
        candidates=candidates, total=len(candidates), without_vector=0
    )


def _plan(
    pairs: list[tuple[str, str]],
    *,
    served: tuple[revision_findings.RevisionFinding, ...] = (),
) -> revisions.RevisionPlan:
    """A minimal `RevisionPlan` over the given candidate pairs -- `coverage`
    is a fixed `store="ok"`, no missing/stale ids, since the counts-line
    tests exercise `_counts_line` directly rather than through a full
    `RevisionPlan`."""
    candidate_plan = _candidate_plan(pairs)
    coverage = revisions.VectorCoverage(
        store="ok", vectors={}, missing=frozenset(), stale=frozenset()
    )
    return revisions.RevisionPlan(
        coverage=coverage,
        candidate_plan=candidate_plan,
        served=served,
        to_judge=candidate_plan.candidates,
    )


def _verdict(
    id_a: str,
    id_b: str,
    verdict_value: RevisionVerdictValue,
    *,
    confidence: float,
    quotes: tuple[str | None, str | None],
    malformed: bool = False,
    date_a: DecisionDate | None = None,
    date_b: DecisionDate | None = None,
) -> decision_revision.RevisionVerdict:
    """One `RevisionVerdict` over sorted `(id_a, id_b)`, dated distinctly by
    default (`id_a` earlier) so direction resolves KNOWN unless the caller
    overrides `date_a`/`date_b`."""
    return decision_revision.RevisionVerdict(
        pair_ids=(id_a, id_b),
        verdict=verdict_value,
        confidence=confidence,
        rationale="stub rationale",
        quotes=quotes,
        dates=(
            date_a
            if date_a is not None
            else DecisionDate(value=date(2026, 1, 1), state="dated"),
            date_b
            if date_b is not None
            else DecisionDate(value=date(2026, 2, 1), state="dated"),
        ),
        malformed=malformed,
    )


def test_counts_line_omits_zero_valued_clauses() -> None:
    """Each of `missing`/`stale`/`excluded` alone renders only its own
    clause; all three non-zero renders all three joined by `"; "`; all
    three zero omits the WHOLE line (`None`)."""
    assert revisions_report._counts_line(missing=3, stale=0, excluded=0) == (
        "3 Decision(s) without an embedding. Run 'openkos reindex' to include them."
    )
    assert revisions_report._counts_line(missing=0, stale=2, excluded=0) == (
        "2 changed since the last reindex. Run 'openkos reindex' to include them."
    )
    assert revisions_report._counts_line(missing=0, stale=0, excluded=5) == (
        "5 excluded (unreadable relations)."
    )
    assert revisions_report._counts_line(missing=1, stale=2, excluded=3) == (
        "1 Decision(s) without an embedding; 2 changed since the last reindex; "
        "3 excluded (unreadable relations). Run 'openkos reindex' to include them."
    )
    assert revisions_report._counts_line(missing=0, stale=0, excluded=0) is None


def test_remedy_clause_only_when_missing_or_stale_is_nonzero() -> None:
    """The `"Run 'openkos reindex' to include them."` clause appears only
    when `missing + stale > 0` -- a run with only `excluded > 0` prints the
    counts line WITHOUT the remedy clause. Kills a remedy printed with
    nothing to remedy."""
    excluded_only = revisions_report._counts_line(missing=0, stale=0, excluded=4)
    assert excluded_only == "4 excluded (unreadable relations)."
    assert "reindex" not in excluded_only

    missing_only = revisions_report._counts_line(missing=1, stale=0, excluded=0)
    assert missing_only is not None
    assert "Run 'openkos reindex' to include them." in missing_only

    stale_only = revisions_report._counts_line(missing=0, stale=1, excluded=0)
    assert stale_only is not None
    assert "Run 'openkos reindex' to include them." in stale_only


def test_groups_by_earlier_decision_for_known_direction_with_verdict_ordering() -> None:
    """Findings sharing the same earlier Decision (`decisions/alpha`) group
    under its id; within that group the order is REVERSES, REFINES,
    REAFFIRMS, (UNRELATED only under `--all`), then confidence descending
    within a verdict."""
    reverses_high = _verdict(
        "decisions/alpha",
        "decisions/delta",
        RevisionVerdictValue.REVERSES,
        confidence=0.95,
        quotes=("Alpha quote.", "Delta quote."),
        date_b=DecisionDate(value=date(2026, 2, 15), state="dated"),
    )
    reverses_low = _verdict(
        "decisions/alpha",
        "decisions/beta",
        RevisionVerdictValue.REVERSES,
        confidence=0.75,
        quotes=("Alpha quote.", "Beta quote."),
    )
    refines = _verdict(
        "decisions/alpha",
        "decisions/epsilon",
        RevisionVerdictValue.REFINES,
        confidence=0.99,
        quotes=("Alpha quote.", "Epsilon quote."),
        date_b=DecisionDate(value=date(2026, 2, 20), state="dated"),
    )
    reaffirms = _verdict(
        "decisions/alpha",
        "decisions/zeta",
        RevisionVerdictValue.REAFFIRMS,
        confidence=0.99,
        quotes=("Alpha quote.", "Zeta quote."),
        date_b=DecisionDate(value=date(2026, 2, 25), state="dated"),
    )
    unrelated = _verdict(
        "decisions/alpha",
        "decisions/eta",
        RevisionVerdictValue.UNRELATED,
        confidence=0.99,
        quotes=("Alpha quote.", "Eta quote."),
        date_b=DecisionDate(value=date(2026, 2, 28), state="dated"),
    )
    pairs = [
        v.pair_ids for v in (reverses_high, reverses_low, refines, reaffirms, unrelated)
    ]
    plan = _plan(pairs)
    outcome = revisions.RevisionOutcome(
        results=(reverses_high, reverses_low, refines, reaffirms, unrelated)
    )

    default_report = revisions_report.revisions_report(plan, outcome, excluded=0)
    p_reverses_high = default_report.index("reversed by decisions/delta")
    p_reverses_low = default_report.index("reversed by decisions/beta")
    p_refines = default_report.index("refined by decisions/epsilon")
    p_reaffirms = default_report.index("reaffirmed by decisions/zeta")
    assert p_reverses_high < p_reverses_low < p_refines < p_reaffirms
    assert "decisions/eta" not in default_report

    all_report = revisions_report.revisions_report(
        plan, outcome, excluded=0, show_all=True
    )
    p_reaffirms_all = all_report.index("reaffirmed by decisions/zeta")
    p_unrelated_all = all_report.index("unrelated to decisions/eta")
    assert p_reaffirms_all < p_unrelated_all


@pytest.mark.parametrize(
    ("date_a", "date_b", "wording"),
    [
        (
            DecisionDate(value=None, state="missing"),
            DecisionDate(value=date(2026, 1, 1), state="dated"),
            "no event_date",
        ),
        (
            DecisionDate(value=None, state="multiple"),
            DecisionDate(value=date(2026, 1, 1), state="dated"),
            "2+ distinct event_dates",
        ),
        (
            DecisionDate(value=None, state="none-reached"),
            DecisionDate(value=date(2026, 1, 1), state="dated"),
            "no Source reached",
        ),
        (
            DecisionDate(value=date(2026, 1, 1), state="dated"),
            DecisionDate(value=date(2026, 1, 1), state="dated"),
            "same event_date",
        ),
    ],
)
def test_groups_by_pair_id_0_for_unknown_direction_with_state_wording(
    date_a: DecisionDate, date_b: DecisionDate, wording: str
) -> None:
    """An unknown-direction finding groups under `pair_id_0`
    (`decisions/mango`) and renders `[direction unknown: <id>: <state>]`
    with all four exact phrasings."""
    pair_ids = ("decisions/mango", "decisions/nectarine")
    verdict = decision_revision.RevisionVerdict(
        pair_ids=pair_ids,
        verdict=RevisionVerdictValue.REFINES,
        confidence=0.9,
        rationale="r",
        quotes=("Mango quote.", "Nectarine quote."),
        dates=(date_a, date_b),
    )
    plan = _plan([pair_ids])
    outcome = revisions.RevisionOutcome(results=(verdict,))

    report = revisions_report.revisions_report(plan, outcome, excluded=0)

    assert "decisions/mango" in report.splitlines()
    assert f"[direction unknown: decisions/nectarine: {wording}]" in report


def test_reaffirms_line_renders_under_the_reaffirmed_decisions_group() -> None:
    """A persisted `REAFFIRMS` finding between Decisions `alpha` and `beta`,
    where `beta` reaffirms `alpha` on a known date, renders `"reaffirmed by
    beta on <date>"` under `alpha`'s group -- and re-running the renderer
    over the SAME finding served (zero fresh judging) still renders it."""
    pair_ids = ("decisions/alpha", "decisions/beta")
    verdict = _verdict(
        *pair_ids,
        RevisionVerdictValue.REAFFIRMS,
        confidence=0.9,
        quotes=("Alpha quote.", "Beta quote."),
        date_b=DecisionDate(value=date(2026, 4, 1), state="dated"),
    )
    plan = _plan([pair_ids])
    outcome = revisions.RevisionOutcome(results=(verdict,))

    report = revisions_report.revisions_report(plan, outcome, excluded=0)

    assert "decisions/alpha" in report.splitlines()
    assert "reaffirmed by decisions/beta on 2026-04-01" in report

    finding = revision_findings.RevisionFinding(
        pair_ids=pair_ids,
        verdict="reaffirms",
        confidence=0.9,
        rationale="stub rationale",
        quotes=("Alpha quote.", "Beta quote."),
        dates=("2026-01-01", "2026-04-01"),
        date_states=("dated", "dated"),
        include_confidential=False,
        prompt_version="stub-version",
        input_digests=(),
    )
    served_plan = _plan([pair_ids], served=(finding,))
    served_outcome = revisions.RevisionOutcome(results=())

    served_report = revisions_report.revisions_report(
        served_plan, served_outcome, excluded=0
    )

    assert "reaffirmed by decisions/beta on 2026-04-01" in served_report


def test_unverified_quote_renders_placeholder_and_not_actionable_tag() -> None:
    """A REVERSES/REFINES finding with one unverified quote renders `"(no
    verbatim quote from <id>)"` for that side, is tagged `[not actionable:
    unquoted]`, and STILL appears in the default (non-`--all`) view."""
    pair_ids = ("decisions/alpha", "decisions/beta")
    verdict = _verdict(
        *pair_ids,
        RevisionVerdictValue.REVERSES,
        confidence=0.9,
        quotes=("Alpha quote.", None),
        date_b=DecisionDate(value=date(2026, 4, 1), state="dated"),
    )
    plan = _plan([pair_ids])
    outcome = revisions.RevisionOutcome(results=(verdict,))

    report = revisions_report.revisions_report(
        plan, outcome, excluded=0, show_all=False
    )

    assert "(no verbatim quote from decisions/beta)" in report
    assert "[not actionable: unquoted]" in report


def test_default_filter_versus_all_flag() -> None:
    """The default view shows `is_reportable_revision` results plus any
    unquoted REVERSES/REFINES; `--all` additionally shows UNRELATED,
    low-confidence, and malformed results."""
    reportable = _verdict(
        "decisions/alpha",
        "decisions/beta",
        RevisionVerdictValue.REAFFIRMS,
        confidence=0.9,
        quotes=("Q1.", "Q2."),
    )
    low_confidence = _verdict(
        "decisions/alpha",
        "decisions/gamma",
        RevisionVerdictValue.REFINES,
        confidence=0.2,
        quotes=("Q1.", "Q2."),
        date_b=DecisionDate(value=date(2026, 3, 1), state="dated"),
    )
    unrelated = _verdict(
        "decisions/alpha",
        "decisions/delta",
        RevisionVerdictValue.UNRELATED,
        confidence=0.9,
        quotes=("Q1.", "Q2."),
        date_b=DecisionDate(value=date(2026, 3, 5), state="dated"),
    )
    malformed = _verdict(
        "decisions/alpha",
        "decisions/epsilon",
        RevisionVerdictValue.UNRELATED,
        confidence=0.0,
        quotes=(None, None),
        malformed=True,
        date_b=DecisionDate(value=date(2026, 3, 10), state="dated"),
    )
    pairs = [v.pair_ids for v in (reportable, low_confidence, unrelated, malformed)]
    plan = _plan(pairs)
    outcome = revisions.RevisionOutcome(
        results=(reportable, low_confidence, unrelated, malformed)
    )

    default_report = revisions_report.revisions_report(
        plan, outcome, excluded=0, show_all=False
    )
    assert "reaffirmed by decisions/beta" in default_report
    assert "decisions/gamma" not in default_report
    assert "decisions/delta" not in default_report
    assert "decisions/epsilon" not in default_report

    all_report = revisions_report.revisions_report(
        plan, outcome, excluded=0, show_all=True
    )
    assert "reaffirmed by decisions/beta" in all_report
    assert "refined by decisions/gamma" in all_report
    assert "unrelated to decisions/delta" in all_report
    assert "decisions/epsilon" in all_report


def test_empty_result_messages() -> None:
    """Zero candidate PAIRS at all yields the no-candidates message (Decision
    B4's revised wording); zero findings judged or served, with at least one
    candidate, yields `"No decision revisions found."`."""
    empty_outcome = revisions.RevisionOutcome(results=())

    no_candidates_plan = _plan([])
    no_candidates_report = revisions_report.revisions_report(
        no_candidates_plan, empty_outcome, excluded=0
    )
    assert no_candidates_report.strip() == (
        "No candidate Decision pairs found (need two Decisions from "
        "different Sources with similar embeddings)."
    )

    with_candidates_plan = _plan([("decisions/a", "decisions/b")])
    no_findings_report = revisions_report.revisions_report(
        with_candidates_plan, empty_outcome, excluded=0
    )
    assert no_findings_report.strip() == "No decision revisions found."
