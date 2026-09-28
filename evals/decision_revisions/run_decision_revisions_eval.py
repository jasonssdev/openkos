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

A live run measures `revision_fixture_library.load_library_fixture()` --
hand-written community-library meeting notes (NOT AMI), labelled by
construction with every doubtful call flagged `contested` for the owner.
`--self-test` keeps running over `revision_fixtures.load_fixture()`, T1's
tiny SYNTHETIC placeholder, whose exact numbers it pins. `--print-fixture`
renders the real fixture as a markdown adjudication sheet. See this
harness's `README.md`.

Usage:

    uv run python evals/decision_revisions/run_decision_revisions_eval.py --self-test
    uv run python evals/decision_revisions/run_decision_revisions_eval.py --print-fixture
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
from typing import Final, cast

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# APPENDED, not inserted at zero -- see `contradictions/run_contradictions_eval.py`'s
# own comment: this keeps this harness's OWN `revision_fixtures` from being
# shadowed by a same-named module anywhere else under `evals/`.
sys.path.append(str(REPO_ROOT / "evals"))

from harness_report import arm_identity_line  # noqa: E402
from revision_fixture_library import load_library_fixture  # noqa: E402
from revision_fixtures import (  # noqa: E402
    Fixture,
    JudgeSplit,
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
    RevisionVerdictValue,
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
    expected: RevisionExpectation | None
    """`None` for a stage (a) candidate pair the fixture never labelled --
    the candidate stage may propose any pair. It still counts toward
    `actionable_rate` (production would act on it), and no label-reading
    metric can match it, since every `expected == <label>` test is false."""
    split: JudgeSplit | None
    """The matched `LabelledPair.split`, or `None` for an unlabelled
    stage (a) candidate pair -- it belongs to neither named split, but
    still counts in `rows_for_split(rows, None)` ("all"), since a real
    production candidate is not itself part of this measurement's
    original-vs-confirmation question."""
    directed: bool | None
    """`True` when the matched `LabelledPair.expected_later_id is not
    None` (a known direction), `False` when it is `None` (undirected --
    undated/equal/multi-date), and `None` for an unlabelled stage (a)
    candidate row, mirroring `split`'s own three-state shape (#1014 Plan 2,
    task T7). This is read from the FIXTURE's own `expected_later_id`, not
    recomputed from a model reply or from `RevisionVerdict.direction`:
    `fixture_integrity` already pins `expected_later_id ==
    pair_direction(...).holder` over the fixture's own dates, so the two
    are equivalent, and reading the fixture keeps this scoring module
    independent of any particular judged batch's own verdicts."""
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
    by sorted `pair_ids`. A pair with no label keeps its row with
    `expected=None` rather than raising: stage (a) judges whatever the
    candidate stage proposed, labelled or not."""
    by_key = {_pair_key(*p.decision_ids): p for p in pairs}
    rows: list[JudgeRow] = []
    for run in runs:
        for verdict in run:
            labelled = by_key.get(verdict.pair_ids)
            rows.append(
                JudgeRow(
                    pair_ids=verdict.pair_ids,
                    expected=None if labelled is None else labelled.expected_verdict,
                    split=None if labelled is None else labelled.split,
                    directed=None
                    if labelled is None
                    else labelled.expected_later_id is not None,
                    observed=verdict.verdict.value,
                    confidence=verdict.confidence,
                    quote_0=verdict.quotes[0],
                    quote_1=verdict.quotes[1],
                    malformed=verdict.malformed,
                )
            )
    return rows


def rows_from_stored_json(
    entries: Sequence[dict[str, object]], pairs: Sequence[LabelledPair]
) -> list[JudgeRow]:
    """Reconstruct `JudgeRow`s from a stored `runs-*.json`'s `rows_a`/
    `rows_b` list -- zero model calls, for `--rescore` (#1014 Plan 2, task
    T7). Re-matches each entry's `pair_ids` against the CURRENT fixture's
    `LabelledPair`s by sorted key, exactly as `judge_rows` matches a live
    `judge_pairs` batch -- so `expected`, `split` and `directed` are always
    freshly DERIVED from `pairs` here, never trusted from the stored JSON.
    This is deliberate, not just convenient: an older stored run's JSON may
    carry no `split` key at all (it predates task T1), and never carried a
    `directed` key (it predates this task); only `observed`/`confidence`/
    `quote_0`/`quote_1`/`malformed` -- what the model actually produced --
    are read from the stored entry."""
    by_key = {_pair_key(*p.decision_ids): p for p in pairs}
    rows: list[JudgeRow] = []
    for entry in entries:
        raw_pair_ids = cast("list[str]", entry["pair_ids"])
        pair_ids = _pair_key(raw_pair_ids[0], raw_pair_ids[1])
        labelled = by_key.get(pair_ids)
        rows.append(
            JudgeRow(
                pair_ids=pair_ids,
                expected=None if labelled is None else labelled.expected_verdict,
                split=None if labelled is None else labelled.split,
                directed=None
                if labelled is None
                else labelled.expected_later_id is not None,
                observed=str(entry["observed"]),
                confidence=float(entry["confidence"]),  # type: ignore[arg-type]
                quote_0=cast("str | None", entry.get("quote_0")),
                quote_1=cast("str | None", entry.get("quote_1")),
                malformed=bool(entry["malformed"]),
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


def rows_for_split(
    rows: Sequence[JudgeRow], split: JudgeSplit | None
) -> list[JudgeRow]:
    """Every `JudgeRow` belonging to `split` -- `split=None` means "all",
    unfiltered, and is the ONLY way to get a pooled number: naming
    `"original"` or `"confirmation"` never silently includes the other,
    and never includes an unlabelled stage (a) candidate row
    (`JudgeRow.split is None`) either. Every judge-stage scoring function
    in this module reads a plain `Sequence[JudgeRow]`, so this is the one
    place split filtering happens -- callers filter first, then call the
    existing pure functions unchanged."""
    if split is None:
        return list(rows)
    return [row for row in rows if row.split == split]


def pairs_for_split(
    pairs: Sequence[LabelledPair], split: JudgeSplit | None
) -> list[LabelledPair]:
    """`rows_for_split`'s counterpart over `LabelledPair`s directly, for
    the fixture-level metrics (`candidate_recall`, `direction_accuracy`)
    that read the fixture's own pairs rather than judged rows."""
    if split is None:
        return list(pairs)
    return [pair for pair in pairs if pair.split == split]


def directed_rows(rows: Sequence[JudgeRow]) -> list[JudgeRow]:
    """Every `JudgeRow` whose matched pair has a KNOWN direction
    (`JudgeRow.directed is True`) -- an unlabelled row (`directed is None`)
    and an undirected row (`directed is False`) are both excluded (#1014
    Plan 2, task T7). This is the "directed pairs keep the four-way
    scoring" half of the split: every existing judge-stage scoring
    function (`confusion_matrix`, `precision_recall`,
    `reverses_refines_confusion`, `actionable_rate`) reads a plain
    `Sequence[JudgeRow]` unchanged, so filtering happens here, once,
    exactly as `rows_for_split` already does for the original/confirmation
    axis -- directedness and split are independent axes over the same
    rows."""
    return [row for row in rows if row.directed is True]


def undirected_rows(rows: Sequence[JudgeRow]) -> list[JudgeRow]:
    """`directed_rows`'s complement over LABELLED rows: `JudgeRow.directed
    is False` -- an unlabelled row (`directed is None`) is excluded here
    too, so `directed_rows(rows) + undirected_rows(rows)` never equals
    `rows` when `rows` includes stage (a) candidate pairs the fixture never
    labelled (#1014 Plan 2, task T7)."""
    return [row for row in rows if row.directed is False]


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
    """`(expected, observed)` -> count, over every LABELLED judged row;
    a row with `expected=None` has no cell to land in."""
    return Counter(
        (row.expected, row.observed) for row in rows if row.expected is not None
    )


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


_CHANGE_WIRE_VALUES: Final[frozenset[str]] = frozenset(
    {RevisionVerdictValue.REVERSES.value, RevisionVerdictValue.REFINES.value}
)
"""The judge's wire verdicts that collapse to CHANGE under the undirected
scoring rule (#1014 Plan 2, task T7) -- the SAME two values
`decision_revision._ACTIONABLE_VERDICT_VALUES` names, read fresh here via
the public `RevisionVerdictValue` enum rather than importing that private
set, so this harness never silently drifts from production's own
REVERSES/REFINES vocabulary."""

_CHANGE_EXPECTED_LABELS: Final[frozenset[str]] = frozenset({"REVERSES", "REFINES"})
"""The fixture's own label vocabulary members that collapse to CHANGE --
`_WIRE_FOR_EXPECTED`'s keys restricted to the two directional labels."""


def is_change_wire(observed: str) -> bool:
    """`True` iff the judge's wire verdict is `"reverses"` or `"refines"` --
    the observed-side half of the undirected change/no-change collapse."""
    return observed in _CHANGE_WIRE_VALUES


@dataclass(frozen=True)
class ChangePrecisionRecall:
    """Precision/recall of the undirected change/no-change collapse
    (#1014 Plan 2, task T7): expected `REVERSES` or `REFINES` collapses to
    CHANGE; observed `reverses` or `refines` collapses to change. Both
    `(k, n)`, never a bare float, per this harness's `n of TOTAL` rule."""

    precision: tuple[int, int]
    recall: tuple[int, int]


def change_precision_recall(rows: Sequence[JudgeRow]) -> ChangePrecisionRecall:
    """Score `rows` -- meant to be `undirected_rows(...)`, never a mixed or
    directed set, though this function itself reads no `directed` field and
    trusts the caller's filtering, exactly as `precision_recall` trusts its
    caller's `rows` -- as one binary classifier: CHANGE vs NOT-CHANGE.
    `TP` = observed change AND expected change; `FP` = observed change AND
    expected NOT change (a labelled `REAFFIRMS`/`UNRELATED` row misread as
    a change); `FN` = observed NOT change AND expected change. A row with
    `expected=None` (unlabelled) can be neither a hit nor a miss against
    any label, so it counts toward nothing -- `precision_recall`'s own
    contract. `(0, 0)` on an empty denominator, never a
    `ZeroDivisionError`, mirroring `precision_recall`."""
    true_positive = sum(
        1
        for row in rows
        if is_change_wire(row.observed) and row.expected in _CHANGE_EXPECTED_LABELS
    )
    false_positive = sum(
        1
        for row in rows
        if is_change_wire(row.observed)
        and row.expected is not None
        and row.expected not in _CHANGE_EXPECTED_LABELS
    )
    false_negative = sum(
        1
        for row in rows
        if not is_change_wire(row.observed) and row.expected in _CHANGE_EXPECTED_LABELS
    )
    return ChangePrecisionRecall(
        precision=(true_positive, true_positive + false_positive),
        recall=(true_positive, true_positive + false_negative),
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
# Fixture integrity and the adjudication rendering -- pure, no LLM, no I/O.
# ---------------------------------------------------------------------------

_SHARED_SOURCE_TAG: Final = "shared-source"
"""The only hard-case tag allowed to pair two Decisions that cite a common
source -- the candidate stage must never propose such a pair, so it exists
only as a deliberate negative."""

_NO_DIRECTION_TAG_REASON: Final[dict[str, str]] = {
    "undated": "missing",
    "equal-date": "equal",
    "multi-date": "multiple",
}
"""A pair tagged with one of these hard cases must have NO direction, for
exactly this `pair_direction` reason -- otherwise the tag probes nothing."""


def fixture_integrity(fixture: Fixture) -> list[str]:
    """Every structural defect in `fixture`, as one message each; empty
    when sound. Checks: unique ids; every cited source and every paired
    Decision exists; no pair repeats or pairs a Decision with itself; two
    sides share a source iff the pair is tagged `shared-source`;
    `expected_later_id` equals `pair_direction`'s holder over the
    fixture's OWN dates (never narrative); a no-direction tag has exactly
    its `pair_direction` reason; a contested pair carries a note."""
    problems: list[str] = []
    sources_by_id = {s.source_id: s for s in fixture.sources}
    decisions_by_id = {d.concept_id: d for d in fixture.decisions}
    if len(sources_by_id) != len(fixture.sources):
        problems.append("duplicate source id")
    if len(decisions_by_id) != len(fixture.decisions):
        problems.append("duplicate decision id")
    for decision in fixture.decisions:
        for source_id in decision.source_ids:
            if source_id not in sources_by_id:
                problems.append(
                    f"{decision.concept_id}: cites unknown source {source_id}"
                )

    seen: set[tuple[str, str]] = set()
    for labelled in fixture.pairs:
        id_a, id_b = _pair_key(*labelled.decision_ids)
        label = f"{id_a} <-> {id_b}"
        missing = [i for i in (id_a, id_b) if i not in decisions_by_id]
        if missing:
            problems.append(f"{label}: unknown decision {', '.join(missing)}")
            continue
        if id_a == id_b:
            problems.append(f"{label}: pairs a decision with itself")
        if (id_a, id_b) in seen:
            problems.append(f"{label}: duplicate pair")
        seen.add((id_a, id_b))

        shares_source = bool(
            set(decisions_by_id[id_a].source_ids)
            & set(decisions_by_id[id_b].source_ids)
        )
        tagged_shared = labelled.hard_case == _SHARED_SOURCE_TAG
        if shares_source != tagged_shared:
            problems.append(
                f"{label}: shares a source = {shares_source}, but tagged "
                f"{labelled.hard_case!r}"
            )

        direction = pair_direction(
            id_a,
            resolve_decision_date(decisions_by_id[id_a], sources_by_id),
            id_b,
            resolve_decision_date(decisions_by_id[id_b], sources_by_id),
        )
        if direction.holder != labelled.expected_later_id:
            problems.append(
                f"{label}: expected_later_id {labelled.expected_later_id!r}, "
                f"but the dates make it {direction.holder!r}"
            )
        wanted_reason = _NO_DIRECTION_TAG_REASON.get(labelled.hard_case or "")
        if wanted_reason is not None and direction.reason != wanted_reason:
            problems.append(
                f"{label}: tagged {labelled.hard_case!r}, but the direction "
                f"reason is {direction.reason!r}"
            )
        if labelled.contested and not labelled.note.strip():
            problems.append(f"{label}: contested with no note")
        if labelled.split == "confirmation" and labelled.contested:
            problems.append(
                f"{label}: confirmation-split pair is contested -- it must "
                "be unambiguous by construction"
            )
    return problems


@dataclass(frozen=True)
class FixtureCounts:
    by_verdict: Counter[str]
    by_hard_case: Counter[str]
    by_split: Counter[str]
    contested: tuple[int, int]
    """`(k, n)`: contested pairs of every labelled pair."""


def fixture_counts(fixture: Fixture) -> FixtureCounts:
    return FixtureCounts(
        by_verdict=Counter(p.expected_verdict for p in fixture.pairs),
        by_hard_case=Counter(p.hard_case or "(none)" for p in fixture.pairs),
        by_split=Counter(p.split for p in fixture.pairs),
        contested=(sum(p.contested for p in fixture.pairs), len(fixture.pairs)),
    )


def _date_text(decision_id: str, fixture: Fixture) -> str:
    sources_by_id = {s.source_id: s for s in fixture.sources}
    decision = next(d for d in fixture.decisions if d.concept_id == decision_id)
    resolved = resolve_decision_date(decision, sources_by_id)
    if resolved.state == "dated" and resolved.value is not None:
        return resolved.value.isoformat()
    if resolved.state == "multiple":
        dates = sorted(
            str(sources_by_id[s].event_date)
            for s in decision.source_ids
            if s in sources_by_id and sources_by_id[s].event_date is not None
        )
        return f"multiple dates ({', '.join(dates)})"
    return "undated"


def render_fixture_markdown(fixture: Fixture) -> str:
    """Every labelled pair as markdown for owner adjudication, in three
    groups: contested pairs (all `split="original"`, since a confirmation
    pair must never be contested), the remaining uncontested original
    pairs, then every confirmation pair in its own section -- confirmation
    pairs are written to be unambiguous, so that section is meant to be a
    quick read (#1014 task T2), never mixed into the adjudication groups
    above it."""
    decisions_by_id = {d.concept_id: d for d in fixture.decisions}
    counts = fixture_counts(fixture)
    contested_pairs = [p for p in fixture.pairs if p.contested]
    uncontested_original = [
        p for p in fixture.pairs if not p.contested and p.split == "original"
    ]
    confirmation_pairs = [p for p in fixture.pairs if p.split == "confirmation"]

    k, n = counts.contested
    lines = [
        "# decision-revisions fixture -- adjudication sheet",
        "",
        "Generated by `run_decision_revisions_eval.py --print-fixture` from "
        "`revision_fixture_library.py`. Labels are BY CONSTRUCTION; settle "
        "every contested pair in the fixture module BEFORE any live score is "
        "trusted, then regenerate this sheet.",
        "",
        f"- decisions: {len(fixture.decisions)}; sources: "
        f"{len(fixture.sources)}; labelled pairs: {n}",
        f"- contested: {k} of {n}",
        "- split: " + ", ".join(f"{s} {c}" for s, c in sorted(counts.by_split.items())),
        "- by verdict: "
        + ", ".join(f"{v} {c}" for v, c in sorted(counts.by_verdict.items())),
        "- by hard case: "
        + ", ".join(f"{t} {c}" for t, c in sorted(counts.by_hard_case.items())),
        "",
    ]

    index = 0

    def render_group(heading: str, group: list[LabelledPair]) -> None:
        nonlocal index
        if not group:
            return
        lines.append(f"## {heading}")
        lines.append("")
        for labelled in group:
            index += 1
            later = labelled.expected_later_id or "none (direction not established)"
            flag = " -- CONTESTED" if labelled.contested else ""
            lines.extend(
                [
                    f"### {index}. expected {labelled.expected_verdict}{flag}",
                    "",
                    f"- split: {labelled.split}",
                    f"- hard case: {labelled.hard_case or '(none)'}",
                    f"- expected later: `{later}`",
                    f"- note: {labelled.note}",
                    "",
                ]
            )
            for side in labelled.decision_ids:
                decision = decisions_by_id[side]
                lines.extend(
                    [
                        f"> **{decision.title}** `{side}` "
                        f"({_date_text(side, fixture)})",
                        ">",
                        f"> {decision.body}",
                        "",
                    ]
                )

    render_group("Contested pairs", contested_pairs)
    render_group("Uncontested pairs", uncontested_original)
    render_group(
        "Confirmation pairs (written before the prompt fix -- unambiguous "
        "by construction, a quick read)",
        confirmation_pairs,
    )
    return "\n".join(lines)


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

    # --- split filtering (#1014 task T1): `office` is this placeholder's
    # one `split="confirmation"` pair; everything else stays "original" ---
    check(
        "stage (b) original split is 5 pairs x runs",
        len(rows_for_split(result.rows_b, "original")),
        5 * self_test_runs,
    )
    check(
        "stage (b) confirmation split is 1 pair x runs",
        len(rows_for_split(result.rows_b, "confirmation")),
        1 * self_test_runs,
    )
    check(
        "stage (b) split=None ('all') is unfiltered",
        len(rows_for_split(result.rows_b, None)),
        len(result.rows_b),
    )
    check(
        "stage (a) original split is 4 candidates x runs",
        len(rows_for_split(result.rows_a, "original")),
        4 * self_test_runs,
    )
    check(
        "stage (a) confirmation split is 1 candidate x runs",
        len(rows_for_split(result.rows_a, "confirmation")),
        1 * self_test_runs,
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

    # --- per-split judge metrics (#1014 task T1): "original" excludes the
    # confirmation-tagged `office` pair entirely, "confirmation" is ONLY
    # `office`, and neither ever silently reappears in the other -------
    original_b = rows_for_split(result.rows_b, "original")
    confirmation_b = rows_for_split(result.rows_b, "confirmation")
    office_key = _pair_key("decisions/office-seating", "decisions/office-snacks")

    check(
        "original split confusion matrix has no UNRELATED cell "
        "(office is the only UNRELATED pair, and it is confirmation-only)",
        confusion_matrix(original_b),
        Counter(
            {
                ("REVERSES", "reverses"): 2,
                ("REVERSES", "refines"): 1,
                ("REFINES", "refines"): 8,
                ("REFINES", "unrelated"): 1,
                ("REAFFIRMS", "reaffirms"): 3,
            }
        ),
    )
    check(
        "confirmation split confusion matrix is ONLY the office pair",
        confusion_matrix(confirmation_b),
        Counter({("UNRELATED", "unrelated"): 3}),
    )
    check(
        "original + confirmation confusion matrices sum to the unfiltered one",
        confusion_matrix(original_b) + confusion_matrix(confirmation_b),
        matrix,
    )
    check(
        "REVERSES precision/recall unchanged on the original split "
        "(office carries no REVERSES/REFINES rows)",
        precision_recall(original_b, "REVERSES"),
        PrecisionRecall(precision=(2, 2), recall=(2, 3)),
    )
    check(
        "REFINES precision/recall unchanged on the original split",
        precision_recall(original_b, "REFINES"),
        PrecisionRecall(precision=(8, 9), recall=(8, 9)),
    )
    check(
        "confirmation split has no REVERSES rows at all: (0, 0), never "
        "a ZeroDivisionError",
        precision_recall(confirmation_b, "REVERSES"),
        PrecisionRecall(precision=(0, 0), recall=(0, 0)),
    )
    check(
        "confirmation split has no REFINES rows at all: (0, 0)",
        precision_recall(confirmation_b, "REFINES"),
        PrecisionRecall(precision=(0, 0), recall=(0, 0)),
    )
    check(
        "REVERSES<->REFINES confusion unchanged on the original split",
        reverses_refines_confusion(original_b),
        ReversesRefinesConfusion(
            reverses_as_refines=(1, 3), refines_as_reverses=(0, 9)
        ),
    )
    check(
        "REVERSES<->REFINES confusion is (0, 0) on the confirmation split",
        reverses_refines_confusion(confirmation_b),
        ReversesRefinesConfusion(
            reverses_as_refines=(0, 0), refines_as_reverses=(0, 0)
        ),
    )
    check(
        "actionable rate, original split: 11 of 15 (office's 3 rows carried "
        "zero actionable hits, so removing them keeps the numerator)",
        actionable_rate(original_b),
        (11, 15),
    )
    check(
        "actionable rate, confirmation split: 0 of 3 (UNRELATED is never actionable)",
        actionable_rate(confirmation_b),
        (0, 3),
    )
    original_stability = pair_stability(original_b)
    check(
        "original split stability excludes the office pair",
        office_key in original_stability,
        False,
    )
    check(
        "confirmation split stability is ONLY the office pair, at 1.0",
        pair_stability(confirmation_b),
        {office_key: 1.0},
    )

    # --- directed_rows/undirected_rows + change_precision_recall (#1014
    # Plan 2, task T7): `parking` is this placeholder's ONLY undirected
    # labelled pair (its second side is deliberately undated), split
    # "original", expected REFINES -- runs: "refines" (change, hit),
    # malformed->"unrelated" (not change, MISS), "refines" (change, hit).
    # Every other labelled pair is directed. --------------------------------
    parking_key = _pair_key(
        "decisions/parking-policy-v1", "decisions/parking-policy-v2"
    )
    check(
        "stage (b) directed rows: 5 of 6 labelled pairs x 3 runs (every "
        "pair except parking)",
        len(directed_rows(result.rows_b)),
        5 * self_test_runs,
    )
    check(
        "stage (b) undirected rows: ONLY parking, 1 pair x 3 runs",
        len(undirected_rows(result.rows_b)),
        1 * self_test_runs,
    )
    check(
        "directed_rows + undirected_rows never double-counts a labelled row",
        len(directed_rows(result.rows_b)) + len(undirected_rows(result.rows_b)),
        len(result.rows_b),
    )
    check(
        "stage (a) directed rows: 4 of 5 candidates x 3 runs (parking is "
        "the only undirected candidate)",
        len(directed_rows(result.rows_a)),
        4 * self_test_runs,
    )
    check(
        "stage (a) undirected rows: ONLY parking, 1 candidate x 3 runs",
        len(undirected_rows(result.rows_a)),
        1 * self_test_runs,
    )
    check(
        "an unlabelled stage (a) row is neither directed nor undirected",
        [row.directed for row in result.rows_a if row.pair_ids == parking_key],
        [False, False, False],
    )

    directed_original_matrix = confusion_matrix(directed_rows(original_b))
    check(
        "directed-only original matrix drops parking's 2 REFINES-as-refines "
        "and 1 REFINES-as-unrelated rows out of the (unfiltered) original "
        "matrix's REFINES cells",
        directed_original_matrix,
        Counter(
            {
                ("REVERSES", "reverses"): 2,
                ("REVERSES", "refines"): 1,
                ("REFINES", "refines"): 6,
                ("REAFFIRMS", "reaffirms"): 3,
            }
        ),
    )
    check(
        "undirected-only original matrix is ONLY parking's own 3 rows",
        confusion_matrix(undirected_rows(original_b)),
        Counter({("REFINES", "refines"): 2, ("REFINES", "unrelated"): 1}),
    )
    check(
        "directed_rows(original) + undirected_rows(original) sums back to "
        "the unfiltered original matrix",
        directed_original_matrix + confusion_matrix(undirected_rows(original_b)),
        confusion_matrix(original_b),
    )

    change_original = change_precision_recall(undirected_rows(original_b))
    check(
        "undirected change precision 2 of 2, recall 2 of 3 (parking's one "
        "malformed run is the one miss)",
        change_original,
        ChangePrecisionRecall(precision=(2, 2), recall=(2, 3)),
    )
    change_confirmation = change_precision_recall(undirected_rows(confirmation_b))
    check(
        "undirected change precision/recall on the confirmation split: "
        "(0, 0) -- office is directed, so no undirected confirmation row "
        "exists, never a ZeroDivisionError",
        change_confirmation,
        ChangePrecisionRecall(precision=(0, 0), recall=(0, 0)),
    )
    check(
        "undirected change precision/recall over an empty row list: (0, 0)",
        change_precision_recall([]),
        ChangePrecisionRecall(precision=(0, 0), recall=(0, 0)),
    )

    # -- change_precision_recall's TP/FP/FN/TN cells, tested directly over
    # synthetic rows (the placeholder fixture has no undirected pair whose
    # expected verdict is REVERSES, UNRELATED, or REAFFIRMS, so a
    # false-positive/true-negative cell cannot arise from `run_pipeline`
    # alone) -- one row per cell, exact (1, 2)/(1, 2). ----------------------
    synthetic_change_rows = [
        JudgeRow(  # true positive: observed change, expected change
            pair_ids=("synthetic/tp-a", "synthetic/tp-b"),
            expected="REVERSES",
            split="original",
            directed=False,
            observed="reverses",
            confidence=0.9,
            quote_0="q0",
            quote_1="q1",
            malformed=False,
        ),
        JudgeRow(  # false positive: observed change, expected NOT change
            pair_ids=("synthetic/fp-a", "synthetic/fp-b"),
            expected="REAFFIRMS",
            split="original",
            directed=False,
            observed="refines",
            confidence=0.9,
            quote_0="q0",
            quote_1="q1",
            malformed=False,
        ),
        JudgeRow(  # false negative: observed NOT change, expected change
            pair_ids=("synthetic/fn-a", "synthetic/fn-b"),
            expected="REFINES",
            split="original",
            directed=False,
            observed="unrelated",
            confidence=0.1,
            quote_0=None,
            quote_1=None,
            malformed=False,
        ),
        JudgeRow(  # true negative: observed NOT change, expected NOT change
            pair_ids=("synthetic/tn-a", "synthetic/tn-b"),
            expected="UNRELATED",
            split="original",
            directed=False,
            observed="unrelated",
            confidence=0.5,
            quote_0=None,
            quote_1=None,
            malformed=False,
        ),
    ]
    check(
        "change_precision_recall's TP/FP/FN/TN table: precision 1 of 2, recall 1 of 2",
        change_precision_recall(synthetic_change_rows),
        ChangePrecisionRecall(precision=(1, 2), recall=(1, 2)),
    )

    # -- `--rescore` round trip: reconstruct stage (b) rows from the exact
    # JSON shape `_rows_to_json` writes, with zero model calls, and confirm
    # every field matches the live rows -- including `directed`, which is
    # NEVER read from the JSON (older stored runs carry no such key) but
    # re-derived fresh from the fixture, exactly as `judge_rows` derives it
    # from a live batch (#1014 Plan 2, task T7). --------------------------
    stored_rows_b = _rows_to_json(result.rows_b)
    check(
        "a stored row JSON entry carries no 'directed' key (older runs "
        "cannot carry one either)",
        all("directed" not in entry for entry in stored_rows_b),
        True,
    )
    rescored_rows_b = rows_from_stored_json(stored_rows_b, fixture.pairs)
    check(
        "--rescore round trip reconstructs every stage (b) row exactly, "
        "including 'directed' re-derived from the fixture",
        rescored_rows_b,
        result.rows_b,
    )

    # --- per-split fixture-level metrics (candidate recall, direction) ----
    original_pairs = pairs_for_split(fixture.pairs, "original")
    confirmation_pairs = pairs_for_split(fixture.pairs, "confirmation")
    recall_original = candidate_recall(result.candidate_pair_ids, original_pairs)
    recall_confirmation = candidate_recall(
        result.candidate_pair_ids, confirmation_pairs
    )
    check(
        "candidate recall, original split: 4 of 5 (same paraphrase miss)",
        recall_original.hit,
        (4, 5),
    )
    check(
        "candidate recall, confirmation split: 0 of 0 -- office is "
        "UNRELATED, so it contributes no adjudicated-true pair at all",
        recall_confirmation.hit,
        (0, 0),
    )
    direction_original = direction_accuracy(original_pairs, result.dates_by_id)
    direction_confirmation = direction_accuracy(confirmation_pairs, result.dates_by_id)
    check(
        "direction accuracy, original split: 5 of 5, still with the one "
        "missing-direction (parking) reason",
        (direction_original.correct, direction_original.no_direction_reasons),
        ((5, 5), Counter({"missing": 1})),
    )
    check(
        "direction accuracy, confirmation split: 1 of 1, no missing reason "
        "(office has an established direction)",
        (direction_confirmation.correct, direction_confirmation.no_direction_reasons),
        ((1, 1), Counter()),
    )

    # --- fixture_integrity flags a contested confirmation-split pair ------
    bogus_pair = LabelledPair(
        ("decisions/billing-tool-v1", "decisions/billing-tool-v2"),
        "REVERSES",
        "decisions/billing-tool-v2",
        contested=True,
        note="deliberately invalid: a confirmation pair must be unambiguous.",
        split="confirmation",
    )
    bogus_fixture = Fixture(
        sources=fixture.sources, decisions=fixture.decisions, pairs=(bogus_pair,)
    )
    check(
        "fixture_integrity flags a contested confirmation-split pair",
        any(
            "confirmation-split pair is contested" in problem
            for problem in fixture_integrity(bogus_fixture)
        ),
        True,
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

    # -- an UNLABELLED candidate pair (a live-run regression) --------------
    # The candidate stage may propose a pair the fixture never labelled;
    # stage (a) must keep it as a row (a production finding would come from
    # it) with no expected verdict, never raise. Built from a real verdict
    # so only the pair differs.
    unlabelled_ids = ("decisions/zz-unlabelled-a", "decisions/zz-unlabelled-b")
    undated = DecisionDate(value=None, state="missing")
    unlabelled = RevisionVerdict(
        pair_ids=unlabelled_ids,
        verdict=RevisionVerdictValue.UNRELATED,
        confidence=0.0,
        rationale="",
        quotes=(None, None),
        dates=(undated, undated),
    )
    try:
        unlabelled_rows = judge_rows(fixture.pairs, [[unlabelled]])
    except KeyError as exc:
        failures.append(f"an unlabelled candidate pair raised KeyError {exc}")
    else:
        check(
            "an unlabelled candidate pair becomes one row with no expected verdict",
            [(r.pair_ids, r.expected) for r in unlabelled_rows],
            [(unlabelled_ids, None)],
        )

    # -- fixture integrity, over the placeholder AND the real fixture ------
    check("placeholder fixture integrity", fixture_integrity(fixture), [])
    library = load_library_fixture()
    library_problems = fixture_integrity(library)
    check("library fixture integrity", library_problems, [])
    if not library_problems:
        # Rendering indexes every paired id, so it is only meaningful (and
        # only safe) over a fixture that passed integrity.
        check(
            "the adjudication sheet renders every labelled pair",
            render_fixture_markdown(library).count("\n### "),
            len(library.pairs),
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
            "split": row.split,
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


def _judge_section_lines(title: str, rows: Sequence[JudgeRow]) -> list[str]:
    """Every stage (b) judge metric this harness reports, rendered for one
    split's `rows` -- `title` names which one (`"original split"`,
    `"confirmation split"`, or `"all (never blended with the splits
    above)"`). Callers pass `rows_for_split(result.rows_b, <split>)`, so
    `original`/`confirmation`/`all` are always three independently
    computed blocks, never one pooled number that hides which split it
    came from (#1014 task T1)."""
    matrix = confusion_matrix(rows)
    reverses_pr = precision_recall(rows, "REVERSES")
    refines_pr = precision_recall(rows, "REFINES")
    confusion = reverses_refines_confusion(rows)
    actionable = actionable_rate(rows)
    stability = pair_stability(rows)
    mean_stability = statistics.fmean(stability.values()) if stability else 0.0
    return [
        f"## Judge -- stage (b), {title}",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("REVERSES precision", reverses_pr.precision),
        _rate_line("REVERSES recall", reverses_pr.recall),
        _rate_line("REFINES precision", refines_pr.precision),
        _rate_line("REFINES recall", refines_pr.recall),
        _rate_line("REVERSES judged REFINES", confusion.reverses_as_refines),
        _rate_line("REFINES judged REVERSES", confusion.refines_as_reverses),
        _rate_line("actionable rate", actionable),
        f"| mean pair stability | {mean_stability:.2f} |",
        "",
        "Confusion matrix (expected, observed) -> count:",
        "",
        "```",
        *(
            f"{expected:10s} -> {observed:10s}: {count}"
            for (expected, observed), count in sorted(matrix.items())
        ),
        "```",
        "",
    ]


def _undirected_section_lines(title: str, rows: Sequence[JudgeRow]) -> list[str]:
    """The undirected change/no-change counterpart to `_judge_section_lines`
    (#1014 Plan 2, task T7): expected `REVERSES`/`REFINES` collapses to
    CHANGE, observed `reverses`/`refines` collapses to change -- the judge
    cannot reliably type an undirected verdict (measured: it never answers
    REFINES without an established order), so this scores only whether a
    change was detected at all. Callers pass `undirected_rows(...)`, never
    a mixed or directed set."""
    change = change_precision_recall(rows)
    return [
        f"## Judge -- stage (b), {title}, UNDIRECTED pairs (change/no-change)",
        "",
        "Expected REVERSES or REFINES = CHANGE; observed reverses or refines "
        "= change. The judge cannot reliably tell REVERSES from REFINES apart "
        "without an established direction, so an undirected verdict is scored "
        "only on whether a change was detected, never on which type.",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("change precision", change.precision),
        _rate_line("change recall", change.recall),
        f"| undirected rows | {len(rows)} |",
        "",
    ]


def _bars_under_new_rule_lines(
    *,
    direction_original: DirectionAccuracy,
    recall_original: CandidateRecall,
    original_b: Sequence[JudgeRow],
) -> list[str]:
    """B1-B8 (this harness's own README table) evaluated under the #1014
    Plan 2 untyped-undirected rule, over the `original` split only -- the
    split the bars were set against before the confirmation split existed.
    B3/B4/B6/B7 (REVERSES/REFINES precision, recall and confusion -- the
    four bars the undirected-collapse diagnosis implicates) read
    `directed_rows(original_b)` only; B1/B2/B5/B8 are unaffected by the
    undirected rule and read exactly as the original bars do (all rows/all
    pairs). **Thresholds are UNCHANGED** -- this table never moves a bar,
    it only asks what each already-agreed bar reads once undirected rows
    are scored honestly. The undirected rule itself was motivated by a
    diagnosis made AFTER seeing the data (post-hoc; #1014's task doc
    records it), never by this table."""
    directed_original = directed_rows(original_b)
    reverses_pr = precision_recall(directed_original, "REVERSES")
    refines_pr_directed = precision_recall(directed_original, "REFINES")
    refines_pr_all = precision_recall(original_b, "REFINES")
    confusion = reverses_refines_confusion(directed_original)
    confusion_hits = confusion.reverses_as_refines[0] + confusion.refines_as_reverses[0]
    confusion_total = (
        confusion.reverses_as_refines[1] + confusion.refines_as_reverses[1]
    )
    stability = pair_stability(original_b)
    mean_stability = statistics.fmean(stability.values()) if stability else 0.0

    def verdict(k: int, n: int, *, minimum: float | None, maximum: float | None) -> str:
        if n == 0:
            return "n/a (empty denominator)"
        rate = k / n
        if minimum is not None:
            return "pass" if rate >= minimum else "fail"
        if maximum is not None:
            return "pass" if rate <= maximum else "fail"
        return "n/a"

    b1_k, b1_n = direction_original.correct
    b2_k, b2_n = recall_original.hit
    return [
        "## Bars B1-B8 under the untyped-undirected rule (original split)",
        "",
        "Thresholds are the SAME as this README's own bar table -- never "
        "moved by this rescoring. B3, B4, B6 and B7 read DIRECTED rows "
        "only; B1, B2, B5 and B8 are unaffected by the untyped-undirected "
        "rule and read exactly as the original bars do.",
        "",
        "| Bar | Metric | Result | Verdict |",
        "| --- | --- | --- | --- |",
        f"| B1 | Direction accuracy | {b1_k} of {b1_n} | "
        f"{'pass' if b1_k == b1_n else 'fail'} |",
        f"| B2 | Candidate-stage recall | {b2_k} of {b2_n} | "
        f"{verdict(b2_k, b2_n, minimum=0.75, maximum=None)} |",
        f"| B3 | REVERSES precision (directed) | {reverses_pr.precision[0]} of "
        f"{reverses_pr.precision[1]} | "
        f"{verdict(*reverses_pr.precision, minimum=0.80, maximum=None)} |",
        f"| B4 | REVERSES recall (directed) | {reverses_pr.recall[0]} of "
        f"{reverses_pr.recall[1]} | "
        f"{verdict(*reverses_pr.recall, minimum=0.60, maximum=None)} |",
        f"| B5 | REFINES precision (all rows, unaffected) | "
        f"{refines_pr_all.precision[0]} of {refines_pr_all.precision[1]} | "
        f"{verdict(*refines_pr_all.precision, minimum=0.60, maximum=None)} |",
        f"| B6 | REFINES recall (directed) | {refines_pr_directed.recall[0]} of "
        f"{refines_pr_directed.recall[1]} | "
        f"{verdict(*refines_pr_directed.recall, minimum=0.50, maximum=None)} |",
        f"| B7 | REVERSES<->REFINES confusion (directed) | {confusion_hits} of "
        f"{confusion_total} | "
        f"{verdict(confusion_hits, confusion_total, minimum=None, maximum=0.10)} |",
        f"| B8 | Mean modal-verdict share (all rows, unaffected) | "
        f"{mean_stability:.2f} | {'pass' if mean_stability >= 0.80 else 'fail'} |",
    ]


def _rescore(runs_path: pathlib.Path) -> int:
    """Recompute the judge-stage, candidate-recall, and direction-accuracy
    metrics from a stored `runs-*.json` -- zero model calls (#1014 Plan 2,
    task T7). Rebuilds `JudgeRow`s via `rows_from_stored_json` against the
    CURRENT `load_library_fixture()`, so `expected`/`split`/`directed` are
    always freshly derived from the fixture, never trusted from the
    (possibly older, possibly `directed`-less) stored JSON. Candidate
    recall is recomputed from the stored `candidates` list; direction
    accuracy is deterministic and reads the fixture's own dates, never the
    stored run at all. The subject-pass section CANNOT be rescored: its raw
    per-Decision replies are not persisted in `runs-*.json`, so this
    report omits it rather than fabricating it. Writes
    `decision-revisions-<stamp>-<model>-rescored.md` next to `runs_path`
    and prints it; returns `0`."""
    data = json.loads(runs_path.read_text(encoding="utf-8"))
    fixture = load_library_fixture()
    model = data.get("model", "unknown-model")
    stamp = data.get("generated_at", "unknown-stamp")
    slug = f"{stamp}-{str(model).replace(':', '-')}"

    rows_b = rows_from_stored_json(data.get("rows_b", []), fixture.pairs)
    rows_a = rows_from_stored_json(data.get("rows_a", []), fixture.pairs)

    candidate_pair_ids = {
        _pair_key(str(entry[0]), str(entry[1])) for entry in data.get("candidates", [])
    }
    recall = candidate_recall(candidate_pair_ids, fixture.pairs)
    recall_by_split = {
        split: candidate_recall(
            candidate_pair_ids, pairs_for_split(fixture.pairs, split)
        )
        for split in ("original", "confirmation")
    }

    sources_by_id = {s.source_id: s for s in fixture.sources}
    decisions_by_id = {d.concept_id: d for d in fixture.decisions}
    dates_by_id = {
        concept_id: resolve_decision_date(decision, sources_by_id)
        for concept_id, decision in decisions_by_id.items()
    }
    direction = direction_accuracy(fixture.pairs, dates_by_id)
    direction_by_split = {
        split: direction_accuracy(pairs_for_split(fixture.pairs, split), dates_by_id)
        for split in ("original", "confirmation")
    }
    actionable_a = actionable_rate(rows_a)

    lines = [
        "# decision-revision-detector eval -- RESCORE "
        "(#1014 Plan 2, task T7: the untyped-undirected rule)",
        "",
        f"_Rescored from `{runs_path.name}` -- zero model calls._",
        "",
        f"model `{model}`, {data.get('runs', '?')} run(s), judge prompt "
        f"`{data.get('judge_prompt_version', '?')}` (UNCHANGED by this "
        "rescore -- only the SCORING RULE for undirected pairs is new).",
        "",
        "This recomputes the judge-stage, candidate-recall and "
        "direction-accuracy metrics from the stored raw verdicts under the "
        "untyped-undirected scoring rule. It cannot recompute the "
        "subject-pass section: that stage's raw per-Decision replies are "
        "not stored in `runs-*.json`.",
        "",
        "## Candidate-stage recall",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("true pairs proposed as a candidate (all)", recall.hit),
        _rate_line(
            "true pairs proposed as a candidate (original)",
            recall_by_split["original"].hit,
        ),
        _rate_line(
            "true pairs proposed as a candidate (confirmation)",
            recall_by_split["confirmation"].hit,
        ),
        "",
        "## Direction accuracy (deterministic -- a lower number is a bug)",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line(
            "direction agrees with the fixture's own dates (all)", direction.correct
        ),
        _rate_line(
            "direction agrees with the fixture's own dates (original)",
            direction_by_split["original"].correct,
        ),
        _rate_line(
            "direction agrees with the fixture's own dates (confirmation)",
            direction_by_split["confirmation"].correct,
        ),
        f"| no-direction reasons | {dict(direction.no_direction_reasons)} |",
        "",
        *_judge_section_lines("original split", rows_for_split(rows_b, "original")),
        *_judge_section_lines(
            "original split, DIRECTED pairs only",
            directed_rows(rows_for_split(rows_b, "original")),
        ),
        *_undirected_section_lines(
            "original split", undirected_rows(rows_for_split(rows_b, "original"))
        ),
        *_judge_section_lines(
            "confirmation split", rows_for_split(rows_b, "confirmation")
        ),
        *_judge_section_lines(
            "confirmation split, DIRECTED pairs only",
            directed_rows(rows_for_split(rows_b, "confirmation")),
        ),
        *_undirected_section_lines(
            "confirmation split",
            undirected_rows(rows_for_split(rows_b, "confirmation")),
        ),
        *_judge_section_lines(
            "all (never blended with the splits above)", rows_for_split(rows_b, None)
        ),
        *_judge_section_lines(
            "all, DIRECTED pairs only", directed_rows(rows_for_split(rows_b, None))
        ),
        *_undirected_section_lines(
            "all", undirected_rows(rows_for_split(rows_b, None))
        ),
        *_bars_under_new_rule_lines(
            direction_original=direction_by_split["original"],
            recall_original=recall_by_split["original"],
            original_b=rows_for_split(rows_b, "original"),
        ),
        "",
        "## Judge -- stage (a), the real candidate set only",
        "",
        "| metric | value |",
        "| --- | --- |",
        _rate_line("actionable rate (a)", actionable_a),
    ]

    report = "\n".join(lines) + "\n"
    out_path = runs_path.parent / f"decision-revisions-{slug}-rescored.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {out_path}")
    return 0


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
    parser.add_argument(
        "--print-fixture",
        action="store_true",
        help="Print the real fixture's labelled pairs as a markdown "
        "adjudication sheet (contested first) and exit. No model call.",
    )
    parser.add_argument(
        "--rescore",
        metavar="RUNS_JSON",
        default=None,
        help="Recompute the report from a stored runs-*.json under the "
        "untyped-undirected scoring rule (#1014 Plan 2) -- zero model "
        "calls. Writes decision-revisions-<stamp>-<model>-rescored.md "
        "next to RUNS_JSON.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.print_fixture:
        print(render_fixture_markdown(load_library_fixture()))
        return 0
    if args.rescore is not None:
        return _rescore(pathlib.Path(args.rescore))

    from openkos.llm.ollama import OllamaClient

    fixture = load_library_fixture()
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
    recall_by_split = {
        split: candidate_recall(
            result.candidate_pair_ids, pairs_for_split(fixture.pairs, split)
        )
        for split in ("original", "confirmation")
    }
    direction = direction_accuracy(fixture.pairs, result.dates_by_id)
    direction_by_split = {
        split: direction_accuracy(
            pairs_for_split(fixture.pairs, split), result.dates_by_id
        )
        for split in ("original", "confirmation")
    }
    actionable_a = actionable_rate(result.rows_a)
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
        "Fixture: `evals/decision_revisions/revision_fixture_library.py` -- NOT AMI. "
        "Labels are owner-adjudicated (T3), never scored before settlement. "
        "The `original` and `confirmation` splits (#1014 task T1) are always "
        "reported separately below, plus `all`; never blended.",
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
        _rate_line("true pairs proposed as a candidate (all)", recall.hit),
        _rate_line(
            "true pairs proposed as a candidate (original)",
            recall_by_split["original"].hit,
        ),
        _rate_line(
            "true pairs proposed as a candidate (confirmation)",
            recall_by_split["confirmation"].hit,
        ),
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
        _rate_line(
            "direction agrees with the fixture's own dates (all)", direction.correct
        ),
        _rate_line(
            "direction agrees with the fixture's own dates (original)",
            direction_by_split["original"].correct,
        ),
        _rate_line(
            "direction agrees with the fixture's own dates (confirmation)",
            direction_by_split["confirmation"].correct,
        ),
        f"| no-direction reasons | {dict(direction.no_direction_reasons)} |",
        "",
        "Every split below reports the existing BLENDED four-way numbers "
        "unchanged (#1014 task T1), then the same rows restricted to "
        "DIRECTED pairs only (still four-way), then the UNDIRECTED pairs "
        "scored change/no-change only (#1014 Plan 2, task T7) -- an "
        "undirected verdict is never scored on WHICH type it named, only "
        "on whether it detected a change at all.",
        "",
        *_judge_section_lines(
            "original split", rows_for_split(result.rows_b, "original")
        ),
        *_judge_section_lines(
            "original split, DIRECTED pairs only",
            directed_rows(rows_for_split(result.rows_b, "original")),
        ),
        *_undirected_section_lines(
            "original split", undirected_rows(rows_for_split(result.rows_b, "original"))
        ),
        *_judge_section_lines(
            "confirmation split", rows_for_split(result.rows_b, "confirmation")
        ),
        *_judge_section_lines(
            "confirmation split, DIRECTED pairs only",
            directed_rows(rows_for_split(result.rows_b, "confirmation")),
        ),
        *_undirected_section_lines(
            "confirmation split",
            undirected_rows(rows_for_split(result.rows_b, "confirmation")),
        ),
        *_judge_section_lines(
            "all (never blended with the splits above)",
            rows_for_split(result.rows_b, None),
        ),
        *_judge_section_lines(
            "all, DIRECTED pairs only",
            directed_rows(rows_for_split(result.rows_b, None)),
        ),
        *_undirected_section_lines(
            "all", undirected_rows(rows_for_split(result.rows_b, None))
        ),
        *_bars_under_new_rule_lines(
            direction_original=direction_by_split["original"],
            recall_original=recall_by_split["original"],
            original_b=rows_for_split(result.rows_b, "original"),
        ),
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
