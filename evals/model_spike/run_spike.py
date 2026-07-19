"""Model-spike evaluation harness for openkos derived-object extraction.

MANUAL spike tool (NOT pytest, NOT part of the shipped package). It drives the
REAL extraction pipeline -- `openkos.extraction.concept.extract_concept` over
`openkos.llm.ollama.OllamaClient` -- across a small set of ground-truth
fixtures, once per `--runs` sample per candidate model, and scores which local
7-8B model gives the best extraction. See `README.md` and AGENTS.md sec. 46
("spike-then-test the fuzzy extraction parts").

The harness NEVER crashes on a single failure: every model call is wrapped in a
per-run `try/except`, uninstalled models are skipped with a note, and an
`OllamaError`-family backend failure is recorded as a failed run rather than
propagated.

Run it against a live Ollama with the candidate models pulled:

    uv run python evals/model_spike/run_spike.py

Prove the scoring/report logic without any model (synthetic self-test):

    uv run python evals/model_spike/run_spike.py --self-test
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from openkos.extraction.concept import ExtractionResult, extract_concept
from openkos.llm.ollama import OllamaClient, OllamaError, model_tag_matches

# --------------------------------------------------------------------------- #
# Fixtures: ground-truth derived objects for two `good-life-demo` raw sources. #
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RAW = _REPO_ROOT / "examples" / "good-life-demo" / "raw"

# Default candidate models. `qwen3:8b` is the current `config.DEFAULT_MODEL`.
DEFAULT_MODELS: tuple[str, ...] = ("qwen3:8b", "mistral:7b", "gemma4:e4b")
DEFAULT_RUNS = 3


@dataclass(frozen=True)
class Fixture:
    """A raw source with its known-correct derived objects (ground truth).

    `target_types` is a MULTISET of the expected `ExtractionResult.type`
    values; `target_count` is its length. The `call-with-maria` fixture is the
    anti-enumeration probe: the correct answer is the rich `Decision` plus the
    one salient `Person`, NOT a `Person`/`Entity` stub for every name mentioned.
    """

    name: str
    raw_path: Path
    source_title: str
    target_types: tuple[str, ...]

    @property
    def target_count(self) -> int:
        """Number of expected derived objects (length of the target multiset)."""
        return len(self.target_types)

    @property
    def target_multiset(self) -> Counter[str]:
        """The expected type multiset as a `Counter` (type -> expected count)."""
        return Counter(self.target_types)

    def read_text(self) -> str:
        """Read the immutable raw source text from disk."""
        return self.raw_path.read_text(encoding="utf-8")


FIXTURES: tuple[Fixture, ...] = (
    Fixture(
        name="call-with-maria",
        raw_path=_RAW / "call-with-maria-2026-07-14.txt",
        source_title="Call with Maria Salazar — 2026-07-14",
        # Anti-enumeration probe: one Decision + one salient Person, not a
        # flood of shallow Person/Entity stubs for every name mentioned.
        target_types=("Decision", "Person"),
    ),
    Fixture(
        name="notes-on-enchiridion",
        raw_path=_RAW / "notes-on-the-enchiridion-2026-07-05.txt",
        source_title="Reading notes — Enchiridion, 2026-07-05",
        target_types=("Concept", "Concept"),
    ),
)

# --------------------------------------------------------------------------- #
# Per-run outcomes.                                                            #
# --------------------------------------------------------------------------- #

# Run status vocabulary.
_OK = "ok"  # backend replied, >= 1 valid ExtractionResult
_EMPTY = "empty"  # backend replied, but [] (nothing valid extracted)
_ERROR = "backend_error"  # OllamaError-family (or other) failure -- no reply


@dataclass(frozen=True)
class RunOutcome:
    """The result of ONE (model, fixture, run) extraction attempt."""

    model: str
    fixture: str
    run_index: int
    status: str
    produced: tuple[tuple[str, str], ...]  # ordered (type, title) pairs
    latency_s: float
    error: str | None = None

    @property
    def responded(self) -> bool:
        """True when the backend replied (`ok`/`empty`), i.e. not a failure."""
        return self.status in (_OK, _EMPTY)

    @property
    def produced_multiset(self) -> Counter[str]:
        """The produced type multiset (type -> produced count)."""
        return Counter(obj_type for obj_type, _title in self.produced)


# --------------------------------------------------------------------------- #
# Scoring (pure functions -- exercised by `--self-test` on synthetic data).   #
# --------------------------------------------------------------------------- #


def type_accuracy(produced: Counter[str], target: Counter[str]) -> float:
    """Multiset recall of the target types: fraction of target "slots" filled.

    For each target type `t` with expected count `n_t`, credit
    `min(produced[t], n_t)` (a produced object of the right type fills at most
    one target slot for that type). The score is the sum of credits divided by
    the total target count:

        type_accuracy = sum_t min(produced[t], target[t]) / sum_t target[t]

    Result is in `[0.0, 1.0]`. Missing target types are penalized (they
    contribute 0 credit); wrong or extra types do NOT raise it (they cannot
    fill a slot). Example -- target `{Concept: 2}`: produced `{Concept: 2}` ->
    `1.0`; produced `{Concept: 1, Person: 1}` -> `0.5`; produced `{Person: 2}`
    -> `0.0`. An empty target yields `1.0` by convention (nothing to miss).
    """
    total_target = sum(target.values())
    if total_target == 0:
        return 1.0
    credit = sum(min(produced.get(t, 0), n) for t, n in target.items())
    return credit / total_target


def anti_enumeration_score(produced: Counter[str], target: Counter[str]) -> float:
    """Penalize OVER-production of shallow stubs; reward staying at/under target.

    "Over-production" is counted per type as any produced object beyond the
    target count for that type:

        over = sum_t max(0, produced[t] - target[t])
        score = 1.0                     if over == 0
              = target_count / (target_count + over)   otherwise

    where `target_count = sum_t target[t]`. Result is in `(0.0, 1.0]` and
    strictly decreases as excess or wrong-type stubs pile up. Under-production
    is NOT penalized here (that is `type_accuracy`'s job) -- a model that emits
    fewer objects than target still scores `1.0`.

    Example -- target `{Decision: 1, Person: 1}` (`target_count = 2`): the
    ideal `{Decision: 1, Person: 1}` -> `1.0`; a stub flood
    `{Decision: 1, Person: 3, Entity: 1, Event: 1}` has `over = 2 + 1 + 1 = 4`
    -> `2 / (2 + 4) = 0.333`. Both an extra same-type object (the third
    `Person`) and an off-target type (`Entity`, `Event`) are counted.
    """
    target_count = sum(target.values())
    over = sum(max(0, produced.get(t, 0) - target.get(t, 0)) for t in produced)
    # Also count over-production of types absent from the target (produced has
    # them, target does not) -- the loop above already covers those because it
    # iterates `produced`'s keys, treating a missing target key as 0.
    if over == 0:
        return 1.0
    if target_count == 0:
        # No target objects at all: any production is pure over-production.
        return 1.0 / (1.0 + over)
    return target_count / (target_count + over)


# --------------------------------------------------------------------------- #
# Aggregation.                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class ModelReport:
    """Aggregated metrics for one candidate model across all fixtures/runs."""

    model: str
    installed: bool
    skip_reason: str | None = None
    outcomes: list[RunOutcome] = field(default_factory=list)

    # --- aggregate metrics (computed over recorded outcomes) --- #

    @property
    def attempted(self) -> int:
        """Number of runs actually attempted (0 when the model was skipped)."""
        return len(self.outcomes)

    @property
    def responded_outcomes(self) -> list[RunOutcome]:
        """Runs where the backend replied (`ok`/`empty`)."""
        return [o for o in self.outcomes if o.responded]

    @property
    def backend_errors(self) -> int:
        """Count of runs that failed with a backend error."""
        return sum(1 for o in self.outcomes if o.status == _ERROR)

    @property
    def schema_valid_rate(self) -> float:
        """Fraction of attempted runs that returned a non-empty valid list.

        Empty replies and backend errors both count against this rate.
        """
        if self.attempted == 0:
            return 0.0
        return sum(1 for o in self.outcomes if o.status == _OK) / self.attempted

    @property
    def avg_object_count(self) -> float:
        """Mean produced-object count over runs where the backend replied."""
        responded = self.responded_outcomes
        if not responded:
            return 0.0
        return statistics.fmean(len(o.produced) for o in responded)

    @property
    def avg_latency_s(self) -> float:
        """Mean extraction latency over runs where the backend replied."""
        responded = self.responded_outcomes
        if not responded:
            return 0.0
        return statistics.fmean(o.latency_s for o in responded)

    def type_accuracy(self, fixtures: dict[str, Fixture]) -> float:
        """Mean per-run `type_accuracy` over responded runs (0.0 if none)."""
        scores = [
            type_accuracy(o.produced_multiset, fixtures[o.fixture].target_multiset)
            for o in self.responded_outcomes
        ]
        return statistics.fmean(scores) if scores else 0.0

    def anti_enumeration_score(self, fixtures: dict[str, Fixture]) -> float:
        """Mean per-run `anti_enumeration_score` over responded runs."""
        scores = [
            anti_enumeration_score(
                o.produced_multiset, fixtures[o.fixture].target_multiset
            )
            for o in self.responded_outcomes
        ]
        return statistics.fmean(scores) if scores else 0.0

    def composite(self, fixtures: dict[str, Fixture]) -> float:
        """Equal-weight mean of the three quality metrics (drives the pick).

        Combines `schema_valid_rate`, `type_accuracy`, and
        `anti_enumeration_score`. Latency is a tie-breaker, not a term.
        """
        return statistics.fmean(
            (
                self.schema_valid_rate,
                self.type_accuracy(fixtures),
                self.anti_enumeration_score(fixtures),
            )
        )


# --------------------------------------------------------------------------- #
# Driving the real pipeline.                                                   #
# --------------------------------------------------------------------------- #


def run_one(
    fixture: Fixture, source_text: str, client: OllamaClient, run_index: int, model: str
) -> RunOutcome:
    """Drive the REAL extraction pipeline once; never raise.

    Wraps `extract_concept` in a try/except so an `OllamaError`-family backend
    failure (or any other unexpected error) is recorded as a failed run instead
    of crashing the whole spike.
    """
    started = time.perf_counter()
    try:
        results: list[ExtractionResult] = extract_concept(
            source_text, source_title=fixture.source_title, llm=client
        )
    except OllamaError as exc:
        latency = time.perf_counter() - started
        return RunOutcome(
            model=model,
            fixture=fixture.name,
            run_index=run_index,
            status=_ERROR,
            produced=(),
            latency_s=latency,
            error=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:  # robustness: one bad run must not abort the spike
        latency = time.perf_counter() - started
        return RunOutcome(
            model=model,
            fixture=fixture.name,
            run_index=run_index,
            status=_ERROR,
            produced=(),
            latency_s=latency,
            error=f"unexpected {type(exc).__name__}: {exc}",
        )
    latency = time.perf_counter() - started
    produced = tuple((r.type, r.title) for r in results)
    status = _OK if produced else _EMPTY
    return RunOutcome(
        model=model,
        fixture=fixture.name,
        run_index=run_index,
        status=status,
        produced=produced,
        latency_s=latency,
    )


def evaluate_model(
    model: str,
    installed_tags: list[str],
    fixtures: Sequence[Fixture],
    runs: int,
    host: str | None,
    timeout: float,
) -> ModelReport:
    """Run every fixture `runs` times for one model; skip if not installed."""
    if not model_tag_matches(model, installed_tags):
        return ModelReport(
            model=model,
            installed=False,
            skip_reason="not installed on the Ollama host (pull it to include)",
        )

    client = OllamaClient(model=model, host=host, timeout=timeout)
    report = ModelReport(model=model, installed=True)
    for fixture in fixtures:
        source_text = fixture.read_text()
        for run_index in range(1, runs + 1):
            outcome = run_one(fixture, source_text, client, run_index, model)
            report.outcomes.append(outcome)
            _print_run_line(outcome)
    return report


def _print_run_line(outcome: RunOutcome) -> None:
    """Emit a one-line progress trace for a completed run."""
    summary = (
        ", ".join(f"{t}:{title}" for t, title in outcome.produced)
        if outcome.produced
        else (outcome.error or "[] (nothing extracted)")
    )
    print(
        f"  [{outcome.model}] {outcome.fixture} run {outcome.run_index} "
        f"({outcome.status}, {outcome.latency_s:.1f}s): {summary}"
    )


# --------------------------------------------------------------------------- #
# Reporting.                                                                   #
# --------------------------------------------------------------------------- #


def _fmt(value: float) -> str:
    """Format a metric to two decimals."""
    return f"{value:.2f}"


def build_report(
    reports: Sequence[ModelReport],
    fixtures: Sequence[Fixture],
    runs: int,
    generated_at: datetime,
) -> str:
    """Render the full markdown comparison report, incl. the recommendation."""
    by_name = {f.name: f for f in fixtures}
    total_target = sum(f.target_count for f in fixtures)
    lines: list[str] = []
    lines.append("# openkos model-spike: derived-object extraction comparison")
    lines.append("")
    lines.append(f"_Generated: {generated_at.isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append(
        f"Runs per fixture: **{runs}**. Fixtures: "
        + ", ".join(f"`{f.name}` (target {f.target_count})" for f in fixtures)
        + f". Total target objects per run-set: **{total_target}**."
    )
    lines.append("")

    # --- per-model summary table --- #
    lines.append("## Per-model summary")
    lines.append("")
    lines.append(
        "| Model | Installed | schema_valid | type_acc | anti_enum | "
        "avg_objs | avg_lat_s | errors | composite |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in reports:
        if not r.installed:
            lines.append(f"| `{r.model}` | no (skipped) | - | - | - | - | - | - | - |")
            continue
        lines.append(
            f"| `{r.model}` | yes | {_fmt(r.schema_valid_rate)} | "
            f"{_fmt(r.type_accuracy(by_name))} | "
            f"{_fmt(r.anti_enumeration_score(by_name))} | "
            f"{_fmt(r.avg_object_count)} | {_fmt(r.avg_latency_s)} | "
            f"{r.backend_errors} | {_fmt(r.composite(by_name))} |"
        )
    lines.append("")
    lines.append(
        "- **schema_valid**: fraction of attempted runs returning a non-empty "
        "list of valid objects (empty replies and backend errors count against "
        "it)."
    )
    lines.append(
        "- **type_acc**: multiset recall of target types "
        "(`sum min(produced[t], target[t]) / sum target[t]`)."
    )
    lines.append(
        "- **anti_enum**: over-production penalty "
        "(`1.0` at/under target; `target_count / (target_count + over)` when "
        "excess/wrong-type stubs appear)."
    )
    lines.append(
        "- **composite**: equal-weight mean of schema_valid, type_acc, "
        "anti_enum; latency breaks ties."
    )
    lines.append("")

    # --- per-fixture, per-run detail --- #
    lines.append("## Per-fixture detail (raw [type:title] per run)")
    lines.append("")
    for fixture in fixtures:
        lines.append(f"### `{fixture.name}`")
        lines.append("")
        lines.append(
            f"- Source title: {fixture.source_title}\n"
            f"- Target: {fixture.target_count} -> "
            f"{dict(fixture.target_multiset)}"
        )
        lines.append("")
        for r in reports:
            if not r.installed:
                lines.append(f"- `{r.model}`: skipped ({r.skip_reason}).")
                continue
            lines.append(f"- `{r.model}`:")
            fixture_runs = [o for o in r.outcomes if o.fixture == fixture.name]
            for o in fixture_runs:
                if o.status == _ERROR:
                    detail = f"ERROR -- {o.error}"
                elif not o.produced:
                    detail = "[] (nothing extracted)"
                else:
                    detail = ", ".join(f"[{t}:{title}]" for t, title in o.produced)
                lines.append(f"    - run {o.run_index} ({o.latency_s:.1f}s): {detail}")
        lines.append("")

    # --- recommendation --- #
    lines.append("## Recommendation")
    lines.append("")
    lines.append(_recommendation(reports, by_name))
    lines.append("")
    return "\n".join(lines)


def _recommendation(
    reports: Sequence[ModelReport], fixtures: dict[str, Fixture]
) -> str:
    """Pick the default model by composite score; explain the reasoning."""
    ran = [r for r in reports if r.installed and r.attempted > 0]
    if not ran:
        return (
            "**No recommendation possible** -- no candidate model was installed "
            "on the Ollama host. Pull at least one candidate and re-run."
        )
    # Best composite; latency as the tie-breaker (lower is better).
    best = max(ran, key=lambda r: (r.composite(fixtures), -r.avg_latency_s))
    reason = (
        f"**Recommended default: `{best.model}`** "
        f"(composite {_fmt(best.composite(fixtures))}).\n\n"
        f"Reasoning: it leads on the equal-weight blend of schema-valid rate "
        f"({_fmt(best.schema_valid_rate)}), type accuracy "
        f"({_fmt(best.type_accuracy(fixtures))}), and anti-enumeration "
        f"({_fmt(best.anti_enumeration_score(fixtures))}) at "
        f"{_fmt(best.avg_latency_s)}s avg latency and "
        f"{best.backend_errors} backend error(s)."
    )
    others = [r for r in ran if r.model != best.model]
    if others:
        ranked = sorted(others, key=lambda r: r.composite(fixtures), reverse=True)
        runners = "; ".join(
            f"`{r.model}` ({_fmt(r.composite(fixtures))})" for r in ranked
        )
        reason += f"\n\nOther candidates by composite: {runners}."
    reason += (
        "\n\nSanity-check the raw [type:title] lists above before committing to "
        "a default -- the composite cannot see extraction QUALITY, only shape."
    )
    return reason


# --------------------------------------------------------------------------- #
# Self-test: prove the scoring/report logic on SYNTHETIC data (no model).      #
# --------------------------------------------------------------------------- #


def _synthetic_outcome(
    model: str, fixture: str, run_index: int, produced: Sequence[tuple[str, str]]
) -> RunOutcome:
    """Build a RunOutcome from synthetic (type, title) pairs (no model call)."""
    return RunOutcome(
        model=model,
        fixture=fixture,
        run_index=run_index,
        status=_OK if produced else _EMPTY,
        produced=tuple(produced),
        latency_s=1.0,
    )


def self_test() -> int:
    """Feed synthetic ExtractionResults through scoring; assert correctness.

    Returns a process exit code (0 = all checks passed). Runs WITHOUT any
    Ollama backend -- it proves the metric and report logic in isolation.
    """
    failures: list[str] = []

    def check(label: str, got: float, want: float) -> None:
        if abs(got - want) > 1e-9:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    maria = Counter(("Decision", "Person"))  # target multiset
    concepts = Counter(("Concept", "Concept"))

    # 1. Prove ExtractionResult flows through unchanged: build real dataclasses,
    #    derive the multiset the way RunOutcome does.
    ideal = [
        ExtractionResult("Decision", "Frame the essay", "d", ""),
        ExtractionResult("Person", "Maria Salazar", "p", ""),
    ]
    ideal_ms = Counter(r.type for r in ideal)
    check("ideal type_acc", type_accuracy(ideal_ms, maria), 1.0)
    check("ideal anti_enum", anti_enumeration_score(ideal_ms, maria), 1.0)

    # 2. type_accuracy: multiset recall.
    check("concept full", type_accuracy(Counter(("Concept", "Concept")), concepts), 1.0)
    check("concept half", type_accuracy(Counter(("Concept", "Person")), concepts), 0.5)
    check("concept miss", type_accuracy(Counter(("Person", "Person")), concepts), 0.0)
    check("empty target", type_accuracy(Counter(("Person",)), Counter()), 1.0)

    # 3. anti_enumeration_score: over-production penalty.
    flood = Counter(("Decision", "Person", "Person", "Person", "Entity", "Event"))
    # over = Person(3-1=2) + Entity(1) + Event(1) = 4; target_count=2 -> 2/6.
    check("flood anti_enum", anti_enumeration_score(flood, maria), 2.0 / 6.0)
    check(
        "under anti_enum",
        anti_enumeration_score(Counter(("Decision",)), maria),
        1.0,
    )
    # One extra off-target stub: over=1 -> 2/3.
    single_extra = Counter(("Decision", "Person", "Entity"))
    check(
        "single_extra anti_enum", anti_enumeration_score(single_extra, maria), 2.0 / 3.0
    )

    # 4. Aggregation + composite over synthetic runs (two models, two fixtures).
    fixtures = {f.name: f for f in FIXTURES}
    good = ModelReport(model="good", installed=True)
    good.outcomes = [
        _synthetic_outcome("good", "call-with-maria", 1, ideal_ms_list()),
        _synthetic_outcome("good", "call-with-maria", 2, ideal_ms_list()),
        _synthetic_outcome(
            "good",
            "notes-on-enchiridion",
            1,
            [("Concept", "Stoicism"), ("Concept", "Epicureanism")],
        ),
        _synthetic_outcome(
            "good",
            "notes-on-enchiridion",
            2,
            [("Concept", "Stoicism"), ("Concept", "Epicureanism")],
        ),
    ]
    check("good schema_valid", good.schema_valid_rate, 1.0)
    check("good type_acc", good.type_accuracy(fixtures), 1.0)
    check("good anti_enum", good.anti_enumeration_score(fixtures), 1.0)
    check("good composite", good.composite(fixtures), 1.0)
    check("good avg_objs", good.avg_object_count, 2.0)

    noisy = ModelReport(model="noisy", installed=True)
    noisy.outcomes = [
        # Over-enumerates the meeting: extra Person + Entity stubs.
        _synthetic_outcome(
            "noisy",
            "call-with-maria",
            1,
            [("Decision", "d"), ("Person", "a"), ("Person", "b"), ("Entity", "e")],
        ),
        # An empty reply -> not schema-valid, anti_enum 1.0, type_acc 0.0.
        _synthetic_outcome("noisy", "notes-on-enchiridion", 1, []),
    ]
    check("noisy schema_valid", noisy.schema_valid_rate, 0.5)
    # call-maria: over = Person(2-1=1)+Entity(1)=2 -> 2/4=0.5; enchiridion empty -> 1.0
    check("noisy anti_enum", noisy.anti_enumeration_score(fixtures), (0.5 + 1.0) / 2)
    # call-maria type_acc: Decision+Person present -> 2/2=1.0; enchiridion 0.0
    check("noisy type_acc", noisy.type_accuracy(fixtures), (1.0 + 0.0) / 2)

    # 5. Recommendation prefers the higher-composite model.
    rec = _recommendation([good, noisy], fixtures)
    if "`good`" not in rec or "Recommended default" not in rec:
        failures.append(f"recommendation did not pick `good`: {rec!r}")

    # 6. Backend-error robustness: an ERROR run drags schema_valid_rate but is
    #    excluded from responded-only aggregates.
    erroring = ModelReport(model="err", installed=True)
    erroring.outcomes = [
        RunOutcome(
            "err", "call-with-maria", 1, _ERROR, (), 5.0, "OllamaUnavailable: x"
        ),
        _synthetic_outcome("err", "call-with-maria", 2, ideal_ms_list()),
    ]
    check("err schema_valid", erroring.schema_valid_rate, 0.5)
    check("err backend_errors", float(erroring.backend_errors), 1.0)
    check("err avg_objs", erroring.avg_object_count, 2.0)  # only responded run

    # 7. Report renders without raising and includes the recommendation.
    text = build_report(
        [
            good,
            noisy,
            ModelReport(model="ghost", installed=False, skip_reason="not installed"),
        ],
        FIXTURES,
        runs=2,
        generated_at=datetime(2026, 7, 19, tzinfo=UTC),
    )
    for needle in (
        "Per-model summary",
        "Per-fixture detail",
        "Recommendation",
        "ghost",
    ):
        if needle not in text:
            failures.append(f"report missing section: {needle!r}")

    if failures:
        print("SELF-TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELF-TEST PASSED: scoring, aggregation, recommendation, and report OK.")
    return 0


def ideal_ms_list() -> list[tuple[str, str]]:
    """The ideal `call-with-maria` extraction as (type, title) pairs."""
    return [("Decision", "Frame the essay"), ("Person", "Maria Salazar")]


# --------------------------------------------------------------------------- #
# CLI.                                                                         #
# --------------------------------------------------------------------------- #


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the harness CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="run_spike.py",
        description="Manual model-spike eval for openkos derived-object extraction.",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help=f"Comma-separated model tags (default: {','.join(DEFAULT_MODELS)}).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Samples per fixture per model (default: {DEFAULT_RUNS}).",
    )
    parser.add_argument(
        "--host", default=None, help="Ollama host (else OLLAMA_HOST/default)."
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-call timeout in seconds (default: 120).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "report.md",
        help="Markdown report path (default: evals/model_spike/report.md).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the synthetic scoring self-test and exit (no Ollama needed).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: self-test, or drive the real spike and write the report."""
    args = parse_args(argv)
    if args.self_test:
        return self_test()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.runs < 1:
        print("error: --runs must be >= 1", file=sys.stderr)
        return 2

    # Detect installed models once (config-free: OllamaClient.list_models).
    probe = OllamaClient(model=models[0], host=args.host, timeout=args.timeout)
    try:
        installed = probe.list_models()
    except OllamaError as exc:
        print(f"error: could not reach Ollama to list models: {exc}", file=sys.stderr)
        print(
            "Is Ollama running? Start it and pull the candidate models.",
            file=sys.stderr,
        )
        return 1
    print(f"Installed models on host: {', '.join(installed) or '(none)'}")

    reports: list[ModelReport] = []
    for model in models:
        print(f"\n=== {model} ===")
        report = evaluate_model(
            model, installed, FIXTURES, args.runs, args.host, args.timeout
        )
        if not report.installed:
            print(f"  skipped: {report.skip_reason}")
        reports.append(report)

    generated_at = datetime.now(UTC)
    text = build_report(reports, FIXTURES, args.runs, generated_at)

    args.output.write_text(text, encoding="utf-8")
    results_dir = args.output.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"report-{stamp}.md").write_text(text, encoding="utf-8")

    print(f"\nWrote report: {args.output}")
    print(f"Archived copy: {results_dir / f'report-{stamp}.md'}")
    print("\n" + _recommendation(reports, {f.name: f for f in FIXTURES}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
