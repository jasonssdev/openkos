"""How much generated text extraction throws away — the #692 measurement.

Issue #692 observes that extraction generates every candidate in full — type,
title, description and body — and that the judge and the deterministic gates
discard candidates only AFTER that text exists. On a local model, generated
tokens are the wall clock, so a discarded candidate is time the user waited
for nothing. Its e2e evidence was 13 objects written and thrown away against
9 kept, on a source that took 4m28s.

This probe answers the question that evidence raises and stops there. Per the
owner ruling for this round it MEASURES the waste and does not implement the
two-phase extraction #692 proposes: a lever gets built after its measurement
exists, never beside it.

**What it measures, and why in characters.**

Every candidate carries a `head` (its type and title) and a `tail` (its
description and body). A title-first extraction would still generate every
`head` — that is the phase-1 reply — so the tail of a DISCARDED candidate is
exactly what two-phase would recover, and the tail of a KEPT one is work that
had to happen either way. The recoverable share is therefore:

    discarded tail chars / all generated candidate chars

Characters, not tokens, on purpose. The number that decides anything here is
a RATIO, and a chars-to-tokens factor cancels out of a ratio — so measuring
in characters removes a tokenizer, an assumed factor, and a whole class of
argument about which of them is right, while changing no conclusion. The
absolute scale is reported beside it in characters too, with the conversion
stated once rather than baked into every figure.

**What it deliberately does NOT claim.** The saving is an upper bound on the
generation half only. A two-phase pipeline adds a second round trip per
source, re-sends the window for the surviving candidates, and pays prompt
processing again; none of that is modelled here, and a projection that
ignored it would read as a promise. This measures what is thrown away. What
it would cost to stop throwing it away is the follow-up's job.

    uv run python -u evals/discarded_generation/run_discarded_generation_probe.py --self-test
    uv run python -u evals/discarded_generation/run_discarded_generation_probe.py --runs 5
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATTRITION_PROBE = (
    _REPO_ROOT / "evals" / "stage_attrition" / "run_stage_attrition_probe.py"
)
_RESULTS = Path(__file__).resolve().parent / "results"

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 5
DEFAULT_TIMEOUT = 600.0

CHARS_PER_TOKEN = 3.7
"""Spanish characters per token, the mid-point of the 3.5-4.0 band #692's own
table uses. Applied ONCE, to render an absolute character count as an
approximate token count for readers who think in tokens. Every ratio in this
report is computed in characters and never touches this constant."""


def _load_attrition_probe() -> Any:
    """Import `evals/stage_attrition`'s probe for its recorder and fixtures.

    Imported, never copied. That probe already wraps every pipeline stage,
    already refuses to run when a stage it patches has been renamed, and
    already snapshots each candidate's generated size. A second recorder
    would have to re-derive which candidates a stage dropped, and would drift
    from the one #715 was measured with -- two ledgers under one name, with
    no way to tell which produced which number."""
    spec = importlib.util.spec_from_file_location(
        "_stage_attrition_probe", _ATTRITION_PROBE
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import the stage recorder from {_ATTRITION_PROBE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Accounting                                                                   #
# --------------------------------------------------------------------------- #


_PRODUCING_STAGES = ("_extract_once",)
"""Stages that MINT candidates rather than filter them. Only these contribute
to the generated total, and each candidate is counted once per minting, so a
subject the union path generates twice costs twice -- which is the truth: two
calls generated it."""


def _int(value: str | int) -> int:
    """A ledger field, tolerant of the JSON round trip that makes it a str."""
    return int(value)


def _key(candidate: dict[str, str]) -> tuple[str, str]:
    return (candidate["type"], candidate["title"])


@dataclass
class Accounting:
    """One run's generation ledger, in characters."""

    generated_head: int = 0
    generated_tail: int = 0
    kept_head: int = 0
    kept_tail: int = 0

    @property
    def generated(self) -> int:
        return self.generated_head + self.generated_tail

    @property
    def discarded_head(self) -> int:
        return self.generated_head - self.kept_head

    @property
    def discarded_tail(self) -> int:
        """The chars a two-phase extraction would not have generated."""
        return self.generated_tail - self.kept_tail

    @property
    def discarded(self) -> int:
        return self.generated - (self.kept_head + self.kept_tail)

    @property
    def discarded_share(self) -> float:
        """Share of generated candidate text belonging to discarded candidates
        -- #692's headline, and the number a count of objects cannot give: 13
        discarded stubs and 13 discarded essays are the same count and very
        different waste."""
        return self.discarded / self.generated if self.generated else 0.0

    @property
    def recoverable_share(self) -> float:
        """Share a title-first phase 1 could have avoided generating. Strictly
        smaller than `discarded_share`: every candidate's head is generated
        either way, so the heads of discarded candidates are waste that
        two-phase does NOT recover."""
        return self.discarded_tail / self.generated if self.generated else 0.0


def account_run(
    stages: Sequence[dict[str, Any]], final: Sequence[dict[str, str]]
) -> Accounting:
    """Total the generated and surviving candidate text of one run.

    `final` is the run's retained objects. Survival is judged against THAT,
    not against any intermediate stage: a candidate that survives the judge
    and then falls to the cap was still generated for nothing, and #692 is
    about generation, not about which gate did the discarding.
    """
    ledger = Accounting()
    kept = {_key(c) for c in final}
    counted_kept: set[tuple[str, str]] = set()
    for event in stages:
        if event["stage"] not in _PRODUCING_STAGES:
            continue
        for candidate in event["left"]:
            head, tail = _int(candidate["head_chars"]), _int(candidate["tail_chars"])
            ledger.generated_head += head
            ledger.generated_tail += tail
            # A retained object is credited ONCE however many calls minted it.
            # The union path generates each subject twice and keeps one; the
            # second generation is waste, and crediting both would report a
            # pipeline that discards nothing.
            if _key(candidate) in kept and _key(candidate) not in counted_kept:
                counted_kept.add(_key(candidate))
                ledger.kept_head += head
                ledger.kept_tail += tail
    return ledger


@dataclass
class RunRecord:
    """One (fixture, run) with its accounting and its shape."""

    fixture: str
    run: int
    model: str
    latency_s: float
    produced: int
    retained: int
    generated_head: int
    generated_tail: int
    kept_head: int
    kept_tail: int
    error: str | None = None
    discarded_titles: list[str] = field(default_factory=list)

    @property
    def accounting(self) -> Accounting:
        return Accounting(
            generated_head=self.generated_head,
            generated_tail=self.generated_tail,
            kept_head=self.kept_head,
            kept_tail=self.kept_tail,
        )


# --------------------------------------------------------------------------- #
# Running                                                                      #
# --------------------------------------------------------------------------- #


def run_fixture(
    attrition: Any, fixture: Any, llm: Any, runs: int, model: str
) -> list[RunRecord]:
    """Measure one fixture `runs` times, recording every stage's candidates."""
    records: list[RunRecord] = []
    recorder = attrition._StageRecorder()
    recorder.install()
    try:
        for index in range(1, runs + 1):
            recorder.reset()
            started = time.perf_counter()
            try:
                outcome = attrition.concept_mod.extract_concept_union(
                    fixture.text, source_title=fixture.title, llm=llm
                )
            except Exception as exc:  # one bad run must not lose the others
                records.append(
                    RunRecord(
                        fixture=fixture.name,
                        run=index,
                        model=model,
                        latency_s=time.perf_counter() - started,
                        produced=0,
                        retained=0,
                        generated_head=0,
                        generated_tail=0,
                        kept_head=0,
                        kept_tail=0,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(f"  {fixture.name} run {index}: ERROR {type(exc).__name__}")
                continue
            latency = time.perf_counter() - started
            stages = [
                {
                    "stage": event.stage,
                    "entered": event.entered,
                    "left": event.left,
                }
                for event in recorder.events
            ]
            final = attrition._snapshot(outcome.objects)
            ledger = account_run(stages, final)
            kept_keys = {_key(c) for c in final}
            discarded = [
                candidate["title"]
                for event in stages
                if event["stage"] in _PRODUCING_STAGES
                for candidate in event["left"]
                if _key(candidate) not in kept_keys
            ]
            records.append(
                RunRecord(
                    fixture=fixture.name,
                    run=index,
                    model=model,
                    latency_s=latency,
                    produced=outcome.report.produced,
                    retained=outcome.report.retained,
                    generated_head=ledger.generated_head,
                    generated_tail=ledger.generated_tail,
                    kept_head=ledger.kept_head,
                    kept_tail=ledger.kept_tail,
                    discarded_titles=sorted(set(discarded)),
                )
            )
            print(
                f"  {fixture.name} run {index} ({latency:.1f}s): "
                f"generated {ledger.generated} chars, discarded "
                f"{ledger.discarded} ({ledger.discarded_share:.0%}), "
                f"recoverable {ledger.recoverable_share:.0%}"
            )
    finally:
        recorder.restore()
    return records


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #


def _spread(values: Sequence[float], fmt: str = "{:.2f}") -> str:
    """`mean ±sd [min-max] n=N`, the shape #694 requires of a metric."""
    if not values:
        return "-"
    if len(values) == 1:
        return fmt.format(values[0]) + " n=1"
    return (
        f"{fmt.format(statistics.fmean(values))} "
        f"±{fmt.format(statistics.stdev(values))} "
        f"[{fmt.format(min(values))}-{fmt.format(max(values))}] n={len(values)}"
    )


def render(records: Sequence[RunRecord]) -> str:
    """The markdown report: one section per fixture, no cross-fixture mean."""
    lines = [
        "# What extraction generates and throws away — #692",
        "",
        f"_Generated: {datetime.now(UTC).isoformat(timespec='seconds')}_",
        "",
        "Ratios are computed in CHARACTERS; a chars-to-token factor cancels "
        f"out of a ratio. Absolute sizes are rendered as tokens at ~"
        f"{CHARS_PER_TOKEN} chars/token where that helps a reader, and the "
        "conversion is applied nowhere else.",
        "",
        "- **discarded share** — generated candidate text belonging to "
        "candidates the run did not retain.",
        "- **recoverable share** — the description+body half of that, the "
        "only part a title-first phase 1 would not have generated. Every "
        "candidate's type and title is generated either way.",
        "",
    ]

    by_fixture: dict[str, list[RunRecord]] = {}
    for record in records:
        by_fixture.setdefault(record.fixture, []).append(record)

    for fixture, runs in by_fixture.items():
        ok = [r for r in runs if r.error is None]
        errored = [r for r in runs if r.error is not None]
        lines.append(f"## `{fixture}`")
        lines.append("")
        if errored:
            # Errored runs are NAMED, never silently dropped from the means:
            # a treatment that breaks a source looks BETTER when its crashed
            # runs stop being counted (the #714/#715 gate defect).
            lines.append(
                f"**{len(errored)} of {len(runs)} runs errored** and are "
                f"excluded from every figure below: "
                + ", ".join(sorted({str(r.error) for r in errored}))
            )
            lines.append("")
        if not ok:
            lines.append("_No successful run._")
            lines.append("")
            continue
        ledgers = [r.accounting for r in ok]
        lines.append("| metric | value |")
        lines.append("| --- | --- |")
        lines.append(
            f"| discarded share | **{_spread([led.discarded_share for led in ledgers])}** |"
        )
        lines.append(
            f"| recoverable share (two-phase) | "
            f"**{_spread([led.recoverable_share for led in ledgers])}** |"
        )
        lines.append(
            f"| generated candidate chars | "
            f"{_spread([float(led.generated) for led in ledgers], '{:.0f}')} |"
        )
        lines.append(
            f"| discarded chars | "
            f"{_spread([float(led.discarded) for led in ledgers], '{:.0f}')} |"
        )
        lines.append(
            f"| recoverable chars | "
            f"{_spread([float(led.discarded_tail) for led in ledgers], '{:.0f}')} |"
        )
        lines.append(
            f"| ≈ recoverable tokens | "
            f"{_spread([led.discarded_tail / CHARS_PER_TOKEN for led in ledgers], '{:.0f}')} |"
        )
        lines.append(f"| produced | {_spread([float(r.produced) for r in ok])} |")
        lines.append(f"| retained | {_spread([float(r.retained) for r in ok])} |")
        lines.append(f"| latency (s) | {_spread([r.latency_s for r in ok])} |")
        lines.append("")
        lines.append("Discarded candidate titles, by run:")
        lines.append("")
        for record in ok:
            titles = ", ".join(record.discarded_titles) or "(none)"
            lines.append(f"- run {record.run}: {titles}")
        lines.append("")
    return "\n".join(lines)


def write_results(records: Sequence[RunRecord], model: str) -> Path:
    """Save the raw ledger, always — never behind a flag."""
    _RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = _RESULTS / f"runs-{stamp}-{model.replace(':', '-')}.json"
    path.write_text(
        json.dumps(
            {"model": model, "stamp": stamp, "runs": [asdict(r) for r in records]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    """Check the accounting on hand-built ledgers. No model needed."""
    failures: list[str] = []

    def check(name: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    def candidate(title: str, head: int, tail: int) -> dict[str, str]:
        return {
            "type": "Concept",
            "title": title,
            "lane": "subject",
            "head_chars": str(head),
            "tail_chars": str(tail),
        }

    stages = [
        {
            "stage": "_extract_once",
            "entered": [],
            "left": [candidate("Kept", 10, 90), candidate("Dropped", 10, 190)],
        },
        # A filtering stage must contribute NOTHING to the generated total,
        # or every candidate would be counted once per stage it survived.
        {
            "stage": "_drop_framing_objects",
            "entered": [candidate("Kept", 10, 90), candidate("Dropped", 10, 190)],
            "left": [candidate("Kept", 10, 90)],
        },
    ]
    final = [candidate("Kept", 10, 90)]
    ledger = account_run(stages, final)
    check("generated", ledger.generated, 300)
    check("discarded", ledger.discarded, 200)
    check("discarded share", round(ledger.discarded_share, 4), round(200 / 300, 4))
    check("recoverable chars", ledger.discarded_tail, 190)
    check("recoverable share", round(ledger.recoverable_share, 4), round(190 / 300, 4))

    # The union path mints the same subject twice and keeps one. The second
    # generation IS waste; crediting both would report a pipeline that
    # discards nothing, which is the failure this case pins.
    twice = [
        {
            "stage": "_extract_once",
            "entered": [],
            "left": [candidate("Kept", 10, 90)],
        },
        {
            "stage": "_extract_once",
            "entered": [],
            "left": [candidate("Kept", 10, 90)],
        },
    ]
    doubled = account_run(twice, final)
    check("union double-generation counted once as kept", doubled.kept_tail, 90)
    check("union double-generation counted twice as generated", doubled.generated, 200)
    check("union waste is visible", round(doubled.discarded_share, 4), 0.5)

    # An empty run must not divide by zero, and must not read as 0% waste
    # from a position of having generated nothing.
    empty = account_run([], [])
    check("empty run share", empty.discarded_share, 0.0)
    check("empty run generated", empty.generated, 0)

    # The recorder this probe rides must still expose what it reads.
    attrition = _load_attrition_probe()
    for attribute in ("_StageRecorder", "_snapshot", "concept_mod"):
        if not hasattr(attrition, attribute):
            failures.append(
                f"stage_attrition no longer exposes {attribute!r} -- re-point "
                f"this probe before trusting a number from it"
            )
    sample = attrition._snapshot([])
    if sample != []:
        failures.append("snapshot of nothing is not empty")

    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "SELF-TEST PASSED: generation accounting, filtering stages excluded, "
        "union double-generation, empty run, recorder seam."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_discarded_generation_probe.py",
        description="Measure the generated text extraction discards (#692).",
    )
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--fixture", action="append", default=None)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parent / "report.md"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.runs < 1:
        print("error: --runs must be >= 1", file=sys.stderr)
        return 2

    attrition = _load_attrition_probe()
    fixtures = attrition.build_fixtures()
    if args.fixture:
        wanted = set(args.fixture)
        fixtures = [f for f in fixtures if f.name in wanted]
    if not fixtures:
        print("error: no fixture selected", file=sys.stderr)
        return 2

    from openkos.llm.ollama import OllamaClient

    llm = OllamaClient(model=args.model, host=args.host, timeout=args.timeout)
    records: list[RunRecord] = []
    for fixture in fixtures:
        print(f"\n=== {fixture.name} ===")
        records.extend(run_fixture(attrition, fixture, llm, args.runs, args.model))

    args.output.write_text(render(records), encoding="utf-8")
    saved = write_results(records, args.model)
    print(f"\nWrote report: {args.output}")
    print(f"Saved raw ledger: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
