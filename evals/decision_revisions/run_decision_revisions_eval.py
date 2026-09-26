"""Measures the decision-revision-detector's Phase A leaves (#1014 piece
(a), sub-change 3) BEFORE any Phase B plumbing is built -- the owner's
measure-first order: Phase B starts only after the owner reads these
numbers.

Drives the REAL production leaves through their own public API --
`derive_subjects`, `plan_revision_candidates`, `judge_pairs` -- never a
reimplementation of any of them: `resolution/decision_subject.py`'s
subject pass, then `resolution/decision_revision.py`'s candidate
generation and judge. What is measured here is what ships.

Stage order, every run: subject pass (once) -> candidate generation
(once) -> the judge, `--runs` times, over BOTH (a) the real candidate set
AND (b) every labelled pair directly, so judge quality stays measurable
even for a pair the candidate stage never offers. The report labels every
number `(a)` or `(b)`.

## What it reports

1. **Subject pass**: how often a subject is produced, how often a
   verbatim evidence quote survives, and how often a labelled pair's two
   subjects clear `SUBJECT_OVERLAP_THRESHOLD`.
2. **Candidate-stage recall**: of the adjudicated true pairs
   (REVERSES/REFINES/REAFFIRMS), how many `plan_revision_candidates`
   proposes at all, and which ones it never offers.
3. **Judge**: a confusion matrix over REVERSES/REFINES/REAFFIRMS/
   UNRELATED; precision/recall of REVERSES and of REFINES; the
   REVERSES<->REFINES confusion specifically; actionable rate under the
   `_ACTIONABLE_CONFIDENCE` (0.7) threshold.
4. **Direction accuracy**: against the fixture's own dates -- deterministic,
   so this should read 100%; a lower number is a bug, not a model
   property. Also reports how many pairs have no direction, and why.
5. **Stability**: each pair's modal-verdict share across `--runs`
   judgements.

Every rate is reported as `k of n`, never a bare percentage (a filtered
probe hides its complement).

## Fixture

`revision_fixtures.load_fixture()` -- currently T1's tiny SYNTHETIC
placeholder (NOT AMI, and not meant to measure anything about the
production judge); T2 replaces its contents with a hand-written,
owner-adjudicated fixture. See this harness's `README.md`.

Usage:

    uv run python evals/decision_revisions/run_decision_revisions_eval.py --self-test
    uv run python evals/decision_revisions/run_decision_revisions_eval.py --runs 15
    uv run python evals/decision_revisions/run_decision_revisions_eval.py \\
        --runs 15 --model qwen3:8b --temperature 0.0 --seed 7
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# APPENDED, not inserted at zero -- see `contradictions/run_contradictions_eval.py`'s
# own comment: this keeps this harness's OWN `revision_fixtures` from being
# shadowed by a same-named module anywhere else under `evals/`.
sys.path.append(str(REPO_ROOT / "evals"))

from harness_report import arm_identity_line  # noqa: E402
from revision_fixtures import (  # noqa: E402
    Fixture,
    LabelledPair,
    RevisionExpectation,
    load_fixture,
    resolve_decision_date,
)

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.llm.base import LLMBackend, Message  # noqa: E402
from openkos.resolution.decision_revision import (  # noqa: E402
    JUDGE_PROMPT_VERSION,
    RELATION_FOR_VERDICT,
    SUBJECT_OVERLAP_THRESHOLD,
    DecisionDate,
    DecisionInput,
    JudgeSide,
    RevisionCandidatePlan,
    RevisionVerdict,
    is_actionable_revision,
    judge_pairs,
    pair_direction,
    plan_revision_candidates,
    revision_truncation_notice,
    subject_overlap,
)
from openkos.resolution.decision_subject import (  # noqa: E402
    SUBJECT_PROMPT_VERSION,
    DecisionSubject,
    SubjectRequest,
    derive_subjects,
)

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 15
"""The repo's own measured floor for judged-pair stability (#1014's own
task doc, citing the 5-run-arm-variance memory) -- 5 runs could not tell
two MODELS apart on a comparable judge, so this harness never defaults
below it."""

_TRUE_VERDICTS: Final[frozenset[str]] = frozenset({"REVERSES", "REFINES", "REAFFIRMS"})
"""The fixture's "adjudicated true pair" classes candidate recall is
scored against -- `UNRELATED` is deliberately excluded: the candidate
stage is not expected to find it, and a labelled UNRELATED pair the
candidate stage DOES propose is not a miss."""

_WIRE_FOR_EXPECTED: Final[dict[str, str]] = {
    "REVERSES": "reverses",
    "REFINES": "refines",
    "REAFFIRMS": "reaffirms",
    "UNRELATED": "unrelated",
}
"""Maps the fixture's own upper-case label vocabulary to the judge's wire
verdict values (`RevisionVerdictValue.*.value`) precision/recall are
scored against."""


def _pair_key(a: str, b: str) -> tuple[str, str]:
    """The sorted `pair_ids` key every scoring function below reads by --
    mirrors `contradictions/run_contradictions_eval.py`'s own `_pair_key`."""
    return (a, b) if a <= b else (b, a)


# ---------------------------------------------------------------------------
# A model-free `LLMBackend` for `--self-test` (zero network, zero Ollama).
# ---------------------------------------------------------------------------


@dataclass
class ScriptedBackend:
    """Returns `replies[calls]` in order, one reply per `.chat()` call,
    across EVERY stage of one `run_pipeline` invocation -- subject pass,
    then every judge call, in the exact order the pipeline issues them.
    Exhausting the script is a fixture bug, never a silent empty reply."""

    replies: list[str]
    calls: int = field(default=0)

    def chat(self, messages: Sequence[Message]) -> str:
        if self.calls >= len(self.replies):
            raise AssertionError(
                f"ScriptedBackend exhausted after {self.calls} call(s); the "
                "self-test's canned reply list is shorter than the number "
                "of chat() calls the pipeline actually made"
            )
        del messages  # scripted by call order, never by message content
        reply = self.replies[self.calls]
        self.calls += 1
        return reply


# ---------------------------------------------------------------------------
# The pipeline: subject pass -> candidates -> judge over (a) and (b).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgeRow:
    """One judged `(labelled pair, run)` observation -- the shared unit
    every judge-stage scoring function below reads."""

    pair_ids: tuple[str, str]
    expected: RevisionExpectation
    observed: str
    confidence: float
    quote_0: str | None
    quote_1: str | None
    malformed: bool


def judge_rows(
    pairs: Sequence[LabelledPair], runs: Sequence[Sequence[RevisionVerdict]]
) -> list[JudgeRow]:
    """Flatten `runs` (one `judge_pairs` batch's `.results` per run) into
    one `JudgeRow` per `(pair, run)`, matched back to its `LabelledPair`
    by sorted `pair_ids`."""
    by_key = {_pair_key(*p.decision_ids): p for p in pairs}
    rows: list[JudgeRow] = []
    for run in runs:
        for verdict in run:
            labelled = by_key[verdict.pair_ids]
            rows.append(
                JudgeRow(
                    pair_ids=verdict.pair_ids,
                    expected=labelled.expected_verdict,
                    observed=verdict.verdict.value,
                    confidence=verdict.confidence,
                    quote_0=verdict.quotes[0],
                    quote_1=verdict.quotes[1],
                    malformed=verdict.malformed,
                )
            )
    return rows


@dataclass(frozen=True)
class PipelineResult:
    """One full `run_pipeline` run's raw output -- everything every
    scoring function below is computed from."""

    subject_results: list[tuple[str, DecisionSubject | None]]
    subjects_by_id: dict[str, str]
    plan: RevisionCandidatePlan
    candidate_pair_ids: set[tuple[str, str]]
    rows_a: list[JudgeRow]
    """Judge outcomes over (a) the real candidate set -- only pairs
    `plan_revision_candidates` actually proposed."""
    rows_b: list[JudgeRow]
    """Judge outcomes over (b) every labelled pair directly, whether or
    not the candidate stage ever proposed it."""
    dates_by_id: dict[str, DecisionDate]


def run_pipeline(fixture: Fixture, llm: LLMBackend, *, runs: int) -> PipelineResult:
    """Run the full measured pipeline once -- subject pass and candidate
    generation exactly once, then the judge `runs` times over (a) and (b)
    -- entirely through the production leaves' own public API. `llm` is
    one shared backend for every stage: the caller decides whether that
    is a `ScriptedBackend` (`--self-test`) or a real `OllamaClient`."""
    sources_by_id = {s.source_id: s for s in fixture.sources}
    decisions_by_id = {d.concept_id: d for d in fixture.decisions}
    dates_by_id = {
        concept_id: resolve_decision_date(decision, sources_by_id)
        for concept_id, decision in decisions_by_id.items()
    }

    ordered_ids = sorted(decisions_by_id)
    requests = [
        SubjectRequest(
            concept_id=concept_id,
            title=decisions_by_id[concept_id].title,
            body=decisions_by_id[concept_id].body,
        )
        for concept_id in ordered_ids
    ]
    subject_batch = derive_subjects(requests, llm=llm)
    subjects_by_id = {
        concept_id: subject.subject
        for concept_id, subject in subject_batch.results
        if subject is not None
    }

    decision_inputs = [
        DecisionInput(
            concept_id=concept_id,
            subject=subjects_by_id.get(concept_id),
            source_ids=frozenset(decisions_by_id[concept_id].source_ids),
            resolved_with=frozenset(),
        )
        for concept_id in ordered_ids
    ]
    plan = plan_revision_candidates(decision_inputs)
    candidate_pair_ids = {candidate.pair_ids for candidate in plan.candidates}

    def judge_side(concept_id: str) -> JudgeSide:
        decision = decisions_by_id[concept_id]
        return JudgeSide(
            concept_id=concept_id,
            title=decision.title,
            body=decision.body,
            date=dates_by_id[concept_id],
        )

    candidate_pairs = [
        (judge_side(a), judge_side(b))
        for a, b in sorted(candidate.pair_ids for candidate in plan.candidates)
    ]
    labelled_pairs_sorted = sorted(_pair_key(*p.decision_ids) for p in fixture.pairs)
    labelled_judge_pairs = [
        (judge_side(a), judge_side(b)) for a, b in labelled_pairs_sorted
    ]

    runs_a: list[list[RevisionVerdict]] = []
    runs_b: list[list[RevisionVerdict]] = []
    for _ in range(runs):
        runs_a.append(judge_pairs(candidate_pairs, llm=llm).results)
        runs_b.append(judge_pairs(labelled_judge_pairs, llm=llm).results)

    return PipelineResult(
        subject_results=subject_batch.results,
        subjects_by_id=subjects_by_id,
        plan=plan,
        candidate_pair_ids=candidate_pair_ids,
        rows_a=judge_rows(fixture.pairs, runs_a),
        rows_b=judge_rows(fixture.pairs, runs_b),
        dates_by_id=dates_by_id,
    )


# ---------------------------------------------------------------------------
# Pure scoring functions -- importable, no LLM, no I/O.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectPassStats:
    produced: tuple[int, int]
    """`(k, n)`: `n` is every subject-pass request made; `k` is how many
    produced a `DecisionSubject` rather than degrading to `None`."""
    evidence_kept: tuple[int, int]
    """`(k, n)`: `n` is `produced`'s `k` (evidence cannot survive without a
    subject); `k` is how many of those also kept a verbatim `evidence`
    quote."""


def subject_pass_stats(
    results: Sequence[tuple[str, DecisionSubject | None]],
) -> SubjectPassStats:
    total = len(results)
    produced = sum(1 for _concept_id, subject in results if subject is not None)
    with_evidence = sum(
        1
        for _concept_id, subject in results
        if subject is not None and subject.evidence is not None
    )
    return SubjectPassStats(
        produced=(produced, total), evidence_kept=(with_evidence, produced)
    )


@dataclass(frozen=True)
class PairOverlapStats:
    cleared: tuple[int, int]
    """`(k, n)`: of labelled pairs where BOTH sides produced a subject,
    how many clear `SUBJECT_OVERLAP_THRESHOLD`."""
    excluded_missing_subject: int
    """Labelled pairs where at least one side never produced a subject --
    counted separately, never folded into `cleared`'s denominator (a pair
    excluded here was never eligible to clear anything)."""


def pair_overlap_stats(
    subjects_by_id: dict[str, str], pairs: Sequence[LabelledPair], threshold: float
) -> PairOverlapStats:
    eligible = 0
    cleared = 0
    missing = 0
    for labelled in pairs:
        id_a, id_b = labelled.decision_ids
        subject_a = subjects_by_id.get(id_a)
        subject_b = subjects_by_id.get(id_b)
        if subject_a is None or subject_b is None:
            missing += 1
            continue
        eligible += 1
        if subject_overlap(subject_a, subject_b) >= threshold:
            cleared += 1
    return PairOverlapStats(
        cleared=(cleared, eligible), excluded_missing_subject=missing
    )


@dataclass(frozen=True)
class CandidateRecall:
    hit: tuple[int, int]
    """`(k, n)`: of the adjudicated true pairs (REVERSES/REFINES/
    REAFFIRMS), how many `plan_revision_candidates` proposed at all."""
    missed: tuple[tuple[str, str], ...]
    """Every true pair's sorted `pair_ids` the candidate stage never
    offered -- never summarized away: "a judge can only be as good as the
    candidates it is shown" (#1014's own task doc)."""


def candidate_recall(
    candidate_pair_ids: set[tuple[str, str]], pairs: Sequence[LabelledPair]
) -> CandidateRecall:
    true_pair_ids = [
        _pair_key(*labelled.decision_ids)
        for labelled in pairs
        if labelled.expected_verdict in _TRUE_VERDICTS
    ]
    missed = tuple(
        sorted(
            pair_ids for pair_ids in true_pair_ids if pair_ids not in candidate_pair_ids
        )
    )
    hit = len(true_pair_ids) - len(missed)
    return CandidateRecall(hit=(hit, len(true_pair_ids)), missed=missed)


def confusion_matrix(rows: Sequence[JudgeRow]) -> Counter[tuple[str, str]]:
    """`(expected, observed)` -> count, over every judged row."""
    return Counter((row.expected, row.observed) for row in rows)


@dataclass(frozen=True)
class PrecisionRecall:
    precision: tuple[int, int]
    recall: tuple[int, int]


def precision_recall(rows: Sequence[JudgeRow], expected_label: str) -> PrecisionRecall:
    """Precision/recall of one fixture verdict label (`"REVERSES"` or
    `"REFINES"`), both as `(k, n)` -- never a bare float, per this
    harness's `n of TOTAL` rule.

    `wire = _WIRE_FOR_EXPECTED[expected_label]`. `TP` = observed `wire` AND
    expected `expected_label`; `FP` = observed `wire` AND a DIFFERENT
    expected label; `FN` = a DIFFERENT observed verdict AND expected
    `expected_label`. `precision = TP / (TP + FP)`, `recall = TP / (TP +
    FN)` -- `(0, 0)` on an empty denominator, never a `ZeroDivisionError`."""
    wire = _WIRE_FOR_EXPECTED[expected_label]
    true_positive = sum(
        1 for row in rows if row.observed == wire and row.expected == expected_label
    )
    false_positive = sum(
        1 for row in rows if row.observed == wire and row.expected != expected_label
    )
    false_negative = sum(
        1 for row in rows if row.observed != wire and row.expected == expected_label
    )
    return PrecisionRecall(
        precision=(true_positive, true_positive + false_positive),
        recall=(true_positive, true_positive + false_negative),
    )


@dataclass(frozen=True)
class ReversesRefinesConfusion:
    reverses_as_refines: tuple[int, int]
    """`(k, n)`: of rows LABELLED `REVERSES`, how many the judge called
    `refines`."""
    refines_as_reverses: tuple[int, int]
    """`(k, n)`: of rows LABELLED `REFINES`, how many the judge called
    `reverses`."""


def reverses_refines_confusion(rows: Sequence[JudgeRow]) -> ReversesRefinesConfusion:
    reverses_total = sum(1 for row in rows if row.expected == "REVERSES")
    reverses_confused = sum(
        1 for row in rows if row.expected == "REVERSES" and row.observed == "refines"
    )
    refines_total = sum(1 for row in rows if row.expected == "REFINES")
    refines_confused = sum(
        1 for row in rows if row.expected == "REFINES" and row.observed == "reverses"
    )
    return ReversesRefinesConfusion(
        reverses_as_refines=(reverses_confused, reverses_total),
        refines_as_reverses=(refines_confused, refines_total),
    )


def actionable_rate(rows: Sequence[JudgeRow]) -> tuple[int, int]:
    """`(k, n)`: of every judged row, how many satisfy
    `is_actionable_revision` -- the production citation gate
    (`decision_revision.is_actionable_revision`), never reimplemented."""
    hits = sum(
        1
        for row in rows
        if is_actionable_revision(
            row.observed, row.confidence, row.quote_0, row.quote_1
        )
    )
    return hits, len(rows)


def pair_stability(rows: Sequence[JudgeRow]) -> dict[tuple[str, str], float]:
    """Modal-verdict share per pair across its judged runs -- needs no
    labels, mirrors `contradictions/run_contradictions_eval.py`'s own
    `score_pair` stability arithmetic."""
    by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        by_pair[row.pair_ids].append(row.observed)
    stability: dict[tuple[str, str], float] = {}
    for pair_ids, verdicts in by_pair.items():
        _modal, modal_count = Counter(verdicts).most_common(1)[0]
        stability[pair_ids] = modal_count / len(verdicts)
    return stability


@dataclass(frozen=True)
class DirectionAccuracy:
    correct: tuple[int, int]
    """`(k, n)`: `n` is every labelled pair; `k` is how many `pair_direction`
    -- over the fixture's OWN resolved dates, never a judge reply
    (ADR-0025) -- agrees with the fixture's `expected_later_id`. This is
    deterministic: a number below `n` is a bug in this harness or in
    `pair_direction`, never a model property."""
    no_direction_reasons: Counter[str]
    """Why `pair_direction` returned no holder, keyed by its own `reason`
    field, for every pair where it did not -- regardless of whether that
    absence was itself the FIXTURE's expectation."""


def direction_accuracy(
    pairs: Sequence[LabelledPair], dates_by_id: dict[str, DecisionDate]
) -> DirectionAccuracy:
    correct = 0
    no_direction: Counter[str] = Counter()
    for labelled in pairs:
        id_a, id_b = _pair_key(*labelled.decision_ids)
        direction = pair_direction(id_a, dates_by_id[id_a], id_b, dates_by_id[id_b])
        if direction.holder == labelled.expected_later_id:
            correct += 1
        if direction.holder is None:
            no_direction[direction.reason] += 1
    return DirectionAccuracy(
        correct=(correct, len(pairs)), no_direction_reasons=no_direction
    )


# ---------------------------------------------------------------------------
# `--self-test`: proves the wiring AND the scoring math, zero network.
# ---------------------------------------------------------------------------


def _subject_reply(subject: str, value: str, evidence: str) -> str:
    return json.dumps({"subject": subject, "value": value, "evidence": evidence})


def _judge_reply(
    verdict: str, confidence: float, quote_first: str, quote_second: str
) -> str:
    return json.dumps(
        {
            "verdict": verdict,
            "confidence": confidence,
            "rationale": "scripted",
            "quote_first": quote_first,
            "quote_second": quote_second,
        }
    )


_MALFORMED_JUDGE_REPLY: Final = "the model got confused and did not reply in JSON"
_MALFORMED_SUBJECT_REPLY: Final = "not a JSON object at all, just prose."


def _self_test() -> int:
    """Runs the REAL pipeline (`run_pipeline`) over `revision_fixtures`'s
    tiny synthetic set through a `ScriptedBackend`, then asserts exact
    numbers from every scoring function above. Zero network calls."""
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        if actual != expected:
            failures.append(f"{label}: expected {expected!r}, got {actual!r}")

    fixture = load_fixture()
    bodies = {decision.concept_id: decision.body for decision in fixture.decisions}

    # Subject-pass script, one entry per Decision, in sorted-concept-id
    # order (the same order `run_pipeline` builds its requests in).
    # `decisions/orphan-note` is scripted malformed on purpose: it pairs
    # with nothing, so its degrade can only show up in `subject_pass_stats`.
    # `decisions/office-seating` gets a subject and a value but a NON-
    # verbatim evidence quote, on purpose: `evidence_kept` must count it as
    # produced-but-not-kept, never as a full failure.
    subject_script: dict[str, tuple[str, str, str] | None] = {
        "decisions/billing-tool-v1": (
            "billing tool",
            "Stripe",
            bodies["decisions/billing-tool-v1"],
        ),
        "decisions/billing-tool-v2": (
            "billing tool",
            "Braintree",
            bodies["decisions/billing-tool-v2"],
        ),
        "decisions/office-seating": (
            "office",
            "",
            "The seats will be rearranged soon.",
        ),
        "decisions/office-snacks": (
            "office",
            "Fresh Bites",
            bodies["decisions/office-snacks"],
        ),
        "decisions/oncall-rotation-v1": (
            "pager rotation",
            "weekly",
            bodies["decisions/oncall-rotation-v1"],
        ),
        "decisions/oncall-rotation-v2": (
            "response timing",
            "15 minutes",
            bodies["decisions/oncall-rotation-v2"],
        ),
        "decisions/orphan-note": None,
        "decisions/parking-policy-v1": (
            "parking policy",
            "Lot A",
            bodies["decisions/parking-policy-v1"],
        ),
        "decisions/parking-policy-v2": (
            "parking policy",
            "weekends",
            bodies["decisions/parking-policy-v2"],
        ),
        "decisions/release-cadence-v1": (
            "release cadence",
            "two weeks",
            bodies["decisions/release-cadence-v1"],
        ),
        "decisions/release-cadence-v2": (
            "release cadence",
            "freeze exception",
            bodies["decisions/release-cadence-v2"],
        ),
        "decisions/standup-time-v1": (
            "standup schedule",
            "9:30am",
            bodies["decisions/standup-time-v1"],
        ),
        "decisions/standup-time-v2": (
            "standup schedule",
            "9:30am",
            bodies["decisions/standup-time-v2"],
        ),
    }
    ordered_ids = sorted(subject_script)
    check(
        "subject script covers exactly the fixture's own decisions",
        ordered_ids,
        sorted(bodies),
    )
    subject_replies = [
        _MALFORMED_SUBJECT_REPLY
        if subject_script[concept_id] is None
        else _subject_reply(*subject_script[concept_id])  # type: ignore[misc]
        for concept_id in ordered_ids
    ]

    # Judge script: first=early/v1 side's body, second=late/v2 side's body
    # -- `_presentation_order` always shows the earlier-dated side first
    # (or, direction unknown, the alphabetically-first id), and every pair
    # in this fixture happens to agree on which side that is; verified
    # directly against `pair_direction` in the assertions below rather
    # than assumed.
    pair_bodies: dict[str, tuple[str, str]] = {
        "billing": (
            bodies["decisions/billing-tool-v1"],
            bodies["decisions/billing-tool-v2"],
        ),
        "office": (
            bodies["decisions/office-snacks"],
            bodies["decisions/office-seating"],
        ),
        "oncall": (
            bodies["decisions/oncall-rotation-v1"],
            bodies["decisions/oncall-rotation-v2"],
        ),
        "parking": (
            bodies["decisions/parking-policy-v1"],
            bodies["decisions/parking-policy-v2"],
        ),
        "release": (
            bodies["decisions/release-cadence-v1"],
            bodies["decisions/release-cadence-v2"],
        ),
        "standup": (
            bodies["decisions/standup-time-v1"],
            bodies["decisions/standup-time-v2"],
        ),
    }
    # One (verdict, confidence) per run, `None` for the one deliberately
    # malformed judge reply (deliverable: "one malformed reply"). `billing`
    # confuses REVERSES with REFINES on run index 1 (deliverable: the
    # REVERSES<->REFINES confusion this harness must name specifically).
    judge_script: dict[str, list[tuple[str, float] | None]] = {
        "billing": [("reverses", 0.90), ("refines", 0.75), ("reverses", 0.90)],
        "release": [("refines", 0.85), ("refines", 0.85), ("refines", 0.85)],
        "standup": [("reaffirms", 0.80), ("reaffirms", 0.80), ("reaffirms", 0.80)],
        "office": [("unrelated", 0.50), ("unrelated", 0.50), ("unrelated", 0.50)],
        "parking": [("refines", 0.75), None, ("refines", 0.75)],
        "oncall": [("refines", 0.80), ("refines", 0.80), ("refines", 0.80)],
    }

    def judge_reply(pair_key: str, run_index: int) -> str:
        entry = judge_script[pair_key][run_index]
        if entry is None:
            return _MALFORMED_JUDGE_REPLY
        verdict, confidence = entry
        first_body, second_body = pair_bodies[pair_key]
        quote_first = "" if verdict == "unrelated" else first_body
        quote_second = "" if verdict == "unrelated" else second_body
        return _judge_reply(verdict, confidence, quote_first, quote_second)

    stage_a_order = ["billing", "office", "parking", "release", "standup"]
    stage_b_order = ["billing", "office", "oncall", "parking", "release", "standup"]
    self_test_runs = 3

    judge_replies: list[str] = []
    for run_index in range(self_test_runs):
        judge_replies += [judge_reply(key, run_index) for key in stage_a_order]
        judge_replies += [judge_reply(key, run_index) for key in stage_b_order]

    backend = ScriptedBackend(replies=subject_replies + judge_replies)
    result = run_pipeline(fixture, backend, runs=self_test_runs)

    # --- wiring: candidate generation over the SCRIPTED subjects ----------
    check(
        "without_subject excludes exactly the orphan note",
        result.plan.without_subject,
        1,
    )
    check("no candidate truncation on this tiny fixture", result.plan.total, 5)
    check(
        "candidate set is exactly the 5 lexically-overlapping pairs",
        result.candidate_pair_ids,
        {
            _pair_key("decisions/billing-tool-v1", "decisions/billing-tool-v2"),
            _pair_key("decisions/office-seating", "decisions/office-snacks"),
            _pair_key("decisions/parking-policy-v1", "decisions/parking-policy-v2"),
            _pair_key("decisions/release-cadence-v1", "decisions/release-cadence-v2"),
            _pair_key("decisions/standup-time-v1", "decisions/standup-time-v2"),
        },
    )
    check(
        "no truncation notice on an untruncated plan",
        revision_truncation_notice(result.plan),
        None,
    )
    check(
        "stage (a) row count is candidates x runs",
        len(result.rows_a),
        5 * self_test_runs,
    )
    check(
        "stage (b) row count is labelled pairs x runs",
        len(result.rows_b),
        6 * self_test_runs,
    )

    # --- subject-pass stats -------------------------------------------------
    subject_stats = subject_pass_stats(result.subject_results)
    check(
        "subject produced: 12 of 13 (orphan degrades)", subject_stats.produced, (12, 13)
    )
    check(
        "evidence kept: 11 of 12 produced (office-seating's quote is not verbatim)",
        subject_stats.evidence_kept,
        (11, 12),
    )

    # --- pair-overlap stats (item 1's third number) -------------------------
    overlap_stats = pair_overlap_stats(
        result.subjects_by_id, fixture.pairs, SUBJECT_OVERLAP_THRESHOLD
    )
    check(
        "5 of 6 labelled pairs clear the overlap threshold (paraphrase pair does not)",
        overlap_stats.cleared,
        (5, 6),
    )
    check(
        "no labelled pair is excluded for a missing subject",
        overlap_stats.excluded_missing_subject,
        0,
    )

    # --- candidate-stage recall (item 2) -------------------------------------
    recall = candidate_recall(result.candidate_pair_ids, fixture.pairs)
    check("candidate recall: 4 of 5 true pairs proposed", recall.hit, (4, 5))
    check(
        "the one missed candidate is the paraphrased on-call pair",
        recall.missed,
        (_pair_key("decisions/oncall-rotation-v1", "decisions/oncall-rotation-v2"),),
    )

    # --- direction accuracy (item 4) -- deterministic, must be 100% --------
    direction = direction_accuracy(fixture.pairs, result.dates_by_id)
    check("direction accuracy is 6 of 6 (deterministic)", direction.correct, (6, 6))
    check(
        "exactly one pair has no direction, because its second side is undated",
        direction.no_direction_reasons,
        Counter({"missing": 1}),
    )

    # --- judge stage (b): confusion matrix, precision/recall, actionable ---
    matrix = confusion_matrix(result.rows_b)
    check(
        "confusion matrix over stage (b)'s 18 rows",
        matrix,
        Counter(
            {
                ("REVERSES", "reverses"): 2,
                ("REVERSES", "refines"): 1,
                ("REFINES", "refines"): 8,
                ("REFINES", "unrelated"): 1,
                ("REAFFIRMS", "reaffirms"): 3,
                ("UNRELATED", "unrelated"): 3,
            }
        ),
    )
    check(
        "REVERSES precision 2 of 2, recall 2 of 3",
        precision_recall(result.rows_b, "REVERSES"),
        PrecisionRecall(precision=(2, 2), recall=(2, 3)),
    )
    check(
        "REFINES precision 8 of 9, recall 8 of 9",
        precision_recall(result.rows_b, "REFINES"),
        PrecisionRecall(precision=(8, 9), recall=(8, 9)),
    )
    check(
        "REVERSES<->REFINES confusion: 1 of 3 REVERSES rows read as REFINES, 0 of 9 the other way",
        reverses_refines_confusion(result.rows_b),
        ReversesRefinesConfusion(
            reverses_as_refines=(1, 3), refines_as_reverses=(0, 9)
        ),
    )
    check(
        "actionable rate, stage (b): 11 of 18", actionable_rate(result.rows_b), (11, 18)
    )
    check(
        "actionable rate, stage (a): 8 of 15", actionable_rate(result.rows_a), (8, 15)
    )

    stability = pair_stability(result.rows_b)
    check(
        "billing pair stability 2/3 (one run confused with REFINES)",
        stability[_pair_key("decisions/billing-tool-v1", "decisions/billing-tool-v2")],
        2 / 3,
    )
    check(
        "parking pair stability 2/3 (one malformed run)",
        stability[
            _pair_key("decisions/parking-policy-v1", "decisions/parking-policy-v2")
        ],
        2 / 3,
    )
    check(
        "release pair stability 1.0",
        stability[
            _pair_key("decisions/release-cadence-v1", "decisions/release-cadence-v2")
        ],
        1.0,
    )

    # --- the one malformed judge reply degrades, never raises --------------
    malformed_rows = [row for row in result.rows_b if row.malformed]
    check("exactly one malformed judge row in stage (b)", len(malformed_rows), 1)
    if malformed_rows:
        check(
            "a malformed row degrades to UNRELATED",
            malformed_rows[0].observed,
            "unrelated",
        )
        check(
            "a malformed row's confidence fails closed to 0.0",
            malformed_rows[0].confidence,
            0.0,
        )
        check(
            "a malformed row keeps no quotes",
            (malformed_rows[0].quote_0, malformed_rows[0].quote_1),
            (None, None),
        )

    for line in failures:
        print(f"FAIL {line}")
    print(f"\nself-test: {'FAILED' if failures else 'passed'}")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Live run: writes results/<slug>.md and runs-<slug>.json.
# ---------------------------------------------------------------------------


def _rows_to_json(rows: Sequence[JudgeRow]) -> list[dict[str, object]]:
    return [
        {
            "pair_ids": list(row.pair_ids),
            "expected": row.expected,
            "observed": row.observed,
            "confidence": row.confidence,
            "quote_0": row.quote_0,
            "quote_1": row.quote_1,
            "malformed": row.malformed,
        }
        for row in rows
    ]


def _rate_line(label: str, value: tuple[int, int]) -> str:
    k, n = value
    pct = k / n if n else 0.0
    return f"| {label} | {k} of {n} ({pct:.2f}) |"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="pin options.temperature (default: the model's Modelfile value)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="pin options.seed (default: unpinned)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the model-free wiring+scoring self-test and exit.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    from openkos.llm.ollama import OllamaClient

    fixture = load_fixture()
    client = OllamaClient(
        model=args.model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        temperature=args.temperature,
        seed=args.seed,
    )

    print(
        f"model {args.model}, {args.runs} run(s), "
        f"{len(fixture.decisions)} decisions, {len(fixture.pairs)} labelled pairs\n"
    )
    result = run_pipeline(fixture, client, runs=args.runs)

    subject_stats = subject_pass_stats(result.subject_results)
    overlap_stats = pair_overlap_stats(
        result.subjects_by_id, fixture.pairs, SUBJECT_OVERLAP_THRESHOLD
    )
    recall = candidate_recall(result.candidate_pair_ids, fixture.pairs)
    direction = direction_accuracy(fixture.pairs, result.dates_by_id)
    matrix_b = confusion_matrix(result.rows_b)
    reverses_pr = precision_recall(result.rows_b, "REVERSES")
    refines_pr = precision_recall(result.rows_b, "REFINES")
    confusion = reverses_refines_confusion(result.rows_b)
    actionable_a = actionable_rate(result.rows_a)
    actionable_b = actionable_rate(result.rows_b)
    stability_b = pair_stability(result.rows_b)
    mean_stability_b = statistics.fmean(stability_b.values()) if stability_b else 0.0
    truncation = revision_truncation_notice(result.plan)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    slug = f"{stamp}-{args.model.replace(':', '-')}"

    (results_dir / f"runs-{slug}.json").write_text(
        json.dumps(
            {
                "generated_at": stamp,
                "model": args.model,
                "runs": args.runs,
                "temperature": args.temperature,
                "seed": args.seed,
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "subject_prompt_version": SUBJECT_PROMPT_VERSION,
                "judge_prompt_version": JUDGE_PROMPT_VERSION,
                "subject_overlap_threshold": SUBJECT_OVERLAP_THRESHOLD,
                "without_subject": result.plan.without_subject,
                "candidate_total": result.plan.total,
                "candidates": [[*c.pair_ids, c.score] for c in result.plan.candidates],
                "rows_a": _rows_to_json(result.rows_a),
                "rows_b": _rows_to_json(result.rows_b),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    extra = [f"model `{args.model}`", f"**{args.runs} runs**"]
    if args.temperature is not None:
        extra.append(f"temperature `{args.temperature}`")
    if args.seed is not None:
        extra.append(f"seed `{args.seed}`")

    lines = [
        "# decision-revision-detector eval (#1014 piece (a), sub-change 3)",
        "",
        f"_Generated: {stamp}_",
        "",
        arm_identity_line(
            max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
            context_window=DEFAULT_CONTEXT_WINDOW,
            extra=extra,
        ),
        "",
        "Fixture: `evals/decision_revisions/revision_fixtures.py` -- NOT AMI. "
        "Labels are owner-adjudicated (T3), never scored before settlement.",
        "",
        "## Subject pass",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("subject produced", subject_stats.produced),
        _rate_line("evidence kept (of produced)", subject_stats.evidence_kept),
        _rate_line(
            f"pair overlap >= {SUBJECT_OVERLAP_THRESHOLD} (of pairs with both subjects)",
            overlap_stats.cleared,
        ),
        f"| pairs excluded for a missing subject | {overlap_stats.excluded_missing_subject} |",
        "",
        "## Candidate-stage recall",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("true pairs proposed as a candidate", recall.hit),
        f"| missed true pairs | {', '.join(' <-> '.join(p) for p in recall.missed) or '(none)'} |",
    ]
    if truncation is not None:
        lines.append(f"| truncation | {truncation} |")
    lines += [
        "",
        "## Direction accuracy (deterministic -- a lower number is a bug)",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("direction agrees with the fixture's own dates", direction.correct),
        f"| no-direction reasons | {dict(direction.no_direction_reasons)} |",
        "",
        "## Judge -- stage (b), every labelled pair directly",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("REVERSES precision", reverses_pr.precision),
        _rate_line("REVERSES recall", reverses_pr.recall),
        _rate_line("REFINES precision", refines_pr.precision),
        _rate_line("REFINES recall", refines_pr.recall),
        _rate_line("REVERSES judged REFINES", confusion.reverses_as_refines),
        _rate_line("REFINES judged REVERSES", confusion.refines_as_reverses),
        _rate_line("actionable rate (b)", actionable_b),
        f"| mean pair stability (b) | {mean_stability_b:.2f} |",
        "",
        "Confusion matrix (expected, observed) -> count:",
        "",
        "```",
        *(
            f"{expected:10s} -> {observed:10s}: {count}"
            for (expected, observed), count in sorted(matrix_b.items())
        ),
        "```",
        "",
        "## Judge -- stage (a), the real candidate set only",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("actionable rate (a)", actionable_a),
        "",
        "## Relation this would write, per verdict (informational, S8/S9)",
        "",
        "| verdict | relation |",
        "| --- | --- |",
        *(
            f"| {verdict.value} | {relation} |"
            for verdict, relation in RELATION_FOR_VERDICT.items()
        ),
    ]

    report = "\n".join(lines) + "\n"
    (results_dir / f"decision-revisions-{slug}.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
