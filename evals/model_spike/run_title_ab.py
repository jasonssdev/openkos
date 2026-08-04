"""Title-anchor A/B for the 1:1 extraction collapse (issue #377, proposal D1).

MANUAL spike tool (NOT pytest, NOT part of the shipped package). Sibling of
`run_spike.py`, which it imports rather than re-implements: same fixtures, same
scoring primitives, same never-crash discipline.

WHAT THIS SETTLES
-----------------
#377 names the extraction prompt as the likely cause of the 3->1 regression
between v0.2.0 and v0.2.1. It cannot be:

    $ git diff v0.2.0 v0.2.1 -- src/openkos/extraction/     # empty
    $ git tag --contains e2669c6                            # v0.1.2 v0.2.0 v0.2.1

The whole extraction module is byte-identical across both releases. What landed
in v0.2.1 alone is `7f29cdd` (#248), which moved the ingest title from
`_titleize(src.stem)` to the document's own H1 and feeds it to
`_stage_derived_objects`' LLM prompt. That value reaches `_build_messages` as
`SOURCE TITLE:` in the user turn, next to a system prompt whose framing line
(`concept.py:37`) says "Classify by what the source is fundamentally about".

So this harness holds EVERYTHING constant -- corpus, model, `_SYSTEM_PROMPT`,
sample count -- and varies exactly one thing: the `SOURCE TITLE:` value.

    h1     the v0.2.1 value: `derive_source_title(raw)`, e.g.
           "Call with Maria Salazar - 2026-07-14"
    stem   the v0.2.0 value: `titleize(path.stem)`, e.g.
           "call with maria 2026 07 14"
    none   no `SOURCE TITLE:` line at all (the control)

THE `none` ARM AND WHY IT MONKEYPATCHES
---------------------------------------
`extract_concept` always emits the `SOURCE TITLE:` line, and proposal slice 1
writes NO production code. So the `none` arm swaps `concept._build_messages`
for a title-free builder, for the duration of that arm only, and restores it in
a `finally`. Everything else in the real pipeline -- reply parsing, validation,
`_MAX_OBJECTS_PER_SOURCE` -- still runs untouched, which is the point: one
variable, not two.

Note what `none` does NOT do: both fixture raws open with their own title line,
so that text still reaches the model inside `SOURCE TEXT:`. That is deliberate.
The arm removes the ANCHOR -- a labeled, pre-computed, authoritative answer to
the system prompt's framing question -- not the information. Stripping the line
from the body too would vary two things at once and make the result unreadable.

READING THE RESULT
------------------
Two independent signals, because the anchor turned out to act on more than one
thing:

- `multi_object_rate` -- how OFTEN the model enumerates (>= 2 objects in a run).
- `twin_rate` -- WHAT it produces: the fraction of objects whose title merely
  restates the SOURCE TITLE this arm sent.

Either one moving implicates the anchor; both flat clears it.

`avg_objects` is reported but is NOT the criterion, and the first corpus run is
why. The distribution is bimodal -- the mode is 1, the tail spikes to 3-5 -- so
the mean barely moves when the rare event doubles in frequency. Measured: means
1.22 (`h1`) vs 1.50 (`stem`), a spread an earlier version of this file called
noise, while the underlying rates were 8% vs 17% over 2 vs 5 distinct sources
and twin_rate ran 0.30 vs 0.11. The single clearest case was `05-workflow`:
`h1` produced `Concept:Workflow`, echoing the heading verbatim; `stem` produced
`Concept:Explore, Plan, Code, and Commit Workflow`. Same document, same prompt.
That is the anchor flattening content, and a mean cannot see it.

USAGE
-----
Requires a running Ollama with the model pulled.

    uv run python evals/model_spike/run_title_ab.py
    uv run python evals/model_spike/run_title_ab.py --model qwen3:8b --runs 5
    uv run python evals/model_spike/run_title_ab.py --arms h1,none

Measure an external corpus instead of the two labeled fixtures -- this is how
#377's 15-source corpus enters, and it is the ONLY place the 3->1 regression
was ever observed:

    uv run python evals/model_spike/run_title_ab.py --corpus path/to/raw/
    uv run python evals/model_spike/run_title_ab.py --corpus path/to/raw/ --with-fixtures

Prove the scoring/report logic with no model at all:

    uv run python evals/model_spike/run_title_ab.py --self-test
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Sibling spike module. Importable because running this file as a script puts
# `evals/model_spike/` on `sys.path[0]`; both live in the same directory and
# neither is part of the shipped package.
from run_spike import (
    _EMPTY,
    _ERROR,
    _OK,
    FIXTURES,
    Fixture,
    RunOutcome,
    anti_enumeration_score,
    type_accuracy,
)

from openkos.bundle.source_titles import titleize
from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import ExtractionResult, extract_concept
from openkos.llm.ollama import OllamaClient, OllamaError, model_tag_matches
from openkos.source_title import derive_source_title

_HERE = Path(__file__).resolve().parent

DEFAULT_MODEL = "qwen3:8b"
DEFAULT_RUNS = 3

# --------------------------------------------------------------------------- #
# The one variable: how the SOURCE TITLE is derived.                           #
# --------------------------------------------------------------------------- #

ARM_H1 = "h1"
ARM_STEM = "stem"
ARM_NONE = "none"
ALL_ARMS: tuple[str, ...] = (ARM_H1, ARM_STEM, ARM_NONE)

_ARM_BLURB = {
    ARM_H1: "v0.2.1 -- `derive_source_title(raw)` (the document's own H1)",
    ARM_STEM: "v0.2.0 -- `titleize(path.stem)` (the filename)",
    ARM_NONE: "control -- no `SOURCE TITLE:` line in the user turn",
}


def is_labeled(fixture: Fixture) -> bool:
    """True when the fixture declares ground-truth target types.

    `run_spike.py`'s two `good-life-demo` fixtures are labeled from the
    reference bundle. Sources loaded with `--corpus` are NOT: nobody has
    declared what they should yield, so only count-shaped metrics
    (`avg_objects`, `twin_rate`, the per-type breakdown) apply to them.
    """
    return bool(fixture.target_types)


def load_corpus(corpus_dir: Path) -> tuple[Fixture, ...]:
    """Load every `.md`/`.txt` under `corpus_dir` as an UNLABELED fixture.

    This is how the 15-source corpus from #377 enters the harness. That corpus
    is where the 3->1 regression was actually observed -- the `good-life-demo`
    fixtures never showed it, in any arm -- so #379's baseline cannot be
    measured without it.

    No target types are invented. #377's evidence is counts ("3 objects under
    v0.2.0, 1 under v0.2.1"), and counts are what this measures. Declaring
    guessed targets would manufacture a ground truth nobody verified, which is
    exactly the defect this harness already had to fix once.
    """
    if not corpus_dir.is_dir():
        raise NotADirectoryError(f"corpus path is not a directory: {corpus_dir}")
    paths = sorted(
        p
        for p in corpus_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in {".md", ".txt"}
    )
    if not paths:
        raise FileNotFoundError(f"no .md/.txt sources under {corpus_dir}")
    return tuple(
        Fixture(
            name=p.stem,
            raw_path=p,
            # Unused: every arm recomputes the title from the raw. Kept
            # non-empty so a `Fixture` printed on its own stays readable.
            source_title=titleize(p.stem),
            target_types=(),
        )
        for p in paths
    )


def arm_title(arm: str, fixture: Fixture, raw_text: str) -> str | None:
    """The `SOURCE TITLE:` value this arm hands the model (`None` = omit).

    `h1` reproduces `_ingest_single`'s v0.2.1 behavior exactly, fallback and
    all: `derive_source_title` returning `None` (no usable candidate) falls
    back to the slug title, which is what `main.py:2574-2579` does.
    """
    if arm == ARM_NONE:
        return None
    if arm == ARM_STEM:
        return titleize(fixture.raw_path.stem)
    if arm == ARM_H1:
        derived = derive_source_title(raw_text)
        return derived if derived is not None else titleize(fixture.raw_path.stem)
    raise ValueError(f"unknown arm: {arm!r}")


def _titleless_messages(source_text: str, source_title: str) -> list[dict[str, str]]:
    """`_build_messages` with the `SOURCE TITLE:` line removed entirely.

    Deliberately keeps the same 2-message shape and the same system prompt --
    only the user turn's title line is gone, so the arm varies one thing.
    """
    return [
        {"role": "system", "content": concept_mod._SYSTEM_PROMPT},
        {"role": "user", "content": f"SOURCE TEXT:\n{source_text}"},
    ]


# --------------------------------------------------------------------------- #
# Per-arm accumulation.                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class ArmReport:
    """Every outcome recorded for one arm, plus the metrics derived from them."""

    arm: str
    titles_used: dict[str, str | None] = field(default_factory=dict)
    outcomes: list[RunOutcome] = field(default_factory=list)

    @property
    def responded(self) -> list[RunOutcome]:
        """Runs where the backend replied at all (errors excluded)."""
        return [o for o in self.outcomes if o.status != _ERROR]

    @property
    def backend_errors(self) -> int:
        """Count of runs the backend never answered."""
        return sum(1 for o in self.outcomes if o.status == _ERROR)

    @property
    def schema_valid_rate(self) -> float:
        """Fraction of attempted runs returning >= 1 valid object."""
        if not self.outcomes:
            return 0.0
        return sum(1 for o in self.outcomes if o.status == _OK) / len(self.outcomes)

    @property
    def avg_objects(self) -> float:
        """Mean produced-object count over responded runs.

        NOT the primary signal, despite the obvious reading. The distribution
        is bimodal -- the mode is 1 and the tail spikes to 3-5 -- so the mean
        is dominated by the mode and moves barely at all when the rare event
        doubles in frequency. Measured: 1.22 vs 1.50 across arms whose
        multi-object RATES were 8% and 17%. Use `multi_object_rate`.
        """
        responded = self.responded
        if not responded:
            return 0.0
        return statistics.fmean(len(o.produced) for o in responded)

    @property
    def multi_object_events(self) -> int:
        """Responded runs that produced two or more objects."""
        return sum(1 for o in self.responded if len(o.produced) >= 2)

    @property
    def multi_object_rate(self) -> float:
        """Fraction of responded runs producing >= 2 objects. THE primary signal.

        Extraction does not fail by shaving a fraction off every source; it
        fails by enumerating on far fewer of them. A rate reads that directly
        where a mean cannot.
        """
        responded = self.responded
        if not responded:
            return 0.0
        return self.multi_object_events / len(responded)

    @property
    def enumerating_sources(self) -> int:
        """Distinct sources that enumerated at least once under this arm.

        Guards against one chatty source carrying the whole rate: three
        multi-object runs on one file is a very different claim from three
        spread over three files.
        """
        return len({o.fixture for o in self.responded if len(o.produced) >= 2})

    @property
    def max_objects(self) -> int:
        """Largest object count seen in any responded run under this arm."""
        counts = [len(o.produced) for o in self.responded]
        return max(counts) if counts else 0

    @property
    def avg_latency_s(self) -> float:
        """Mean extraction latency over responded runs."""
        responded = self.responded
        if not responded:
            return 0.0
        return statistics.fmean(o.latency_s for o in responded)

    def twin_rate(self) -> float:
        """Fraction of produced objects whose title merely restates the source's.

        The twin is the anchor's fingerprint (proposal D4): a derived object
        echoing the Source's own title. Compared case-insensitively on
        stripped text, against the title THIS arm actually sent; the `none`
        arm has no title to echo, so every object counts as non-twin.
        """
        produced_total = 0
        twins = 0
        for outcome in self.responded:
            sent = self.titles_used.get(outcome.fixture)
            for _type, title in outcome.produced:
                produced_total += 1
                if (
                    sent is not None
                    and title.strip().casefold() == sent.strip().casefold()
                ):
                    twins += 1
        return twins / produced_total if produced_total else 0.0

    def type_accuracy(self, by_name: dict[str, Fixture]) -> float:
        """Mean per-run multiset recall of the target types.

        Only LABELED fixtures contribute. An unlabeled corpus source has no
        target to recall against, and scoring it would report a number that
        means nothing.
        """
        scores = [
            type_accuracy(o.produced_multiset, by_name[o.fixture].target_multiset)
            for o in self.responded
            if is_labeled(by_name[o.fixture])
        ]
        return statistics.fmean(scores) if scores else 0.0

    def anti_enumeration_score(self, by_name: dict[str, Fixture]) -> float:
        """Mean per-run over-production penalty (LABELED fixtures only).

        Unlabeled sources are excluded deliberately: with `target_count == 0`
        the formula degenerates to `0 / (0 + over) = 0.0`, which would read as
        catastrophic over-production rather than as "no target declared".
        """
        scores = [
            anti_enumeration_score(
                o.produced_multiset, by_name[o.fixture].target_multiset
            )
            for o in self.responded
            if is_labeled(by_name[o.fixture])
        ]
        return statistics.fmean(scores) if scores else 0.0

    def counts_by_first_type(self) -> dict[str, list[int]]:
        """Object counts grouped by the type of the run's FIRST object.

        The type-conditional probe. The rubric classifies the SOURCE, so the
        model settles on one type for the document and then enumerates -- or
        does not. Seven of the nine types read "the source is fundamentally
        about ONE specific, named X"; `Concept` and `Entity` are exempt. If
        that split is what caps extraction, runs landing on a named-entity
        type cluster at 1 object while `Concept` runs spread higher.
        """
        grouped: dict[str, list[int]] = {}
        for outcome in self.responded:
            if not outcome.produced:
                continue
            first_type = outcome.produced[0][0]
            grouped.setdefault(first_type, []).append(len(outcome.produced))
        return grouped


# --------------------------------------------------------------------------- #
# Driving the real pipeline.                                                   #
# --------------------------------------------------------------------------- #


def run_one(
    fixture: Fixture,
    source_text: str,
    title: str | None,
    client: OllamaClient,
    run_index: int,
    arm: str,
) -> RunOutcome:
    """Drive the REAL extraction pipeline once for one arm; never raise."""
    started = time.perf_counter()
    try:
        results: list[ExtractionResult] = extract_concept(
            source_text, source_title=title or "", llm=client
        )
    except OllamaError as exc:
        return RunOutcome(
            model=arm,
            fixture=fixture.name,
            run_index=run_index,
            status=_ERROR,
            produced=(),
            latency_s=time.perf_counter() - started,
            error=f"{type(exc).__name__}: {exc}",
        )
    except Exception as exc:  # one bad run must not abort the spike
        return RunOutcome(
            model=arm,
            fixture=fixture.name,
            run_index=run_index,
            status=_ERROR,
            produced=(),
            latency_s=time.perf_counter() - started,
            error=f"unexpected {type(exc).__name__}: {exc}",
        )
    latency = time.perf_counter() - started
    produced = tuple((r.type, r.title) for r in results)
    return RunOutcome(
        model=arm,
        fixture=fixture.name,
        run_index=run_index,
        status=_OK if produced else _EMPTY,
        produced=produced,
        latency_s=latency,
    )


def evaluate_arm(
    arm: str,
    fixtures: Sequence[Fixture],
    runs: int,
    client: OllamaClient,
) -> ArmReport:
    """Run every fixture `runs` times under one arm.

    The `none` arm swaps `_build_messages` for the title-free builder and
    ALWAYS restores it, so a crash mid-arm cannot leak into the next one.
    """
    report = ArmReport(arm=arm)
    original = concept_mod._build_messages
    if arm == ARM_NONE:
        concept_mod._build_messages = _titleless_messages  # type: ignore[assignment]
    try:
        for fixture in fixtures:
            source_text = fixture.read_text()
            title = arm_title(arm, fixture, source_text)
            report.titles_used[fixture.name] = title
            for run_index in range(1, runs + 1):
                outcome = run_one(fixture, source_text, title, client, run_index, arm)
                report.outcomes.append(outcome)
                _print_run_line(arm, outcome)
    finally:
        concept_mod._build_messages = original
    return report


def _print_run_line(arm: str, outcome: RunOutcome) -> None:
    """Emit a one-line progress trace for a completed run."""
    summary = (
        ", ".join(f"{t}:{title}" for t, title in outcome.produced)
        if outcome.produced
        else (outcome.error or "[] (nothing extracted)")
    )
    print(
        f"  [{arm}] {outcome.fixture} run {outcome.run_index} "
        f"({outcome.status}, {outcome.latency_s:.1f}s): {summary}"
    )


# --------------------------------------------------------------------------- #
# Reporting.                                                                   #
# --------------------------------------------------------------------------- #


def _fmt(value: float) -> str:
    """Format a metric to two decimals."""
    return f"{value:.2f}"


# A rare-event rate needs a ratio, not an absolute gap: 8% -> 17% is a doubling
# that an absolute threshold tuned for means would discard. Both must hold, so a
# 1% -> 2% flicker on tiny n cannot pass.
_RATE_RATIO = 1.5
_RATE_MIN_GAP = 0.05
# Twins are per-object, not per-run, so their denominator is large enough for an
# absolute threshold. Measured 0.30 vs 0.11 when the anchor was acting.
_TWIN_MIN_GAP = 0.10
# Below this many multi-object runs pooled across arms, the rate is a handful of
# events and cannot carry a conclusion on its own.
_RATE_MIN_EVENTS = 10


def verdict(reports: Sequence[ArmReport], by_name: dict[str, Fixture]) -> str:
    """State what the numbers support, and refuse to overclaim when flat.

    Reads TWO independent signals, because the anchor turned out to act on
    more than one thing:

    - `multi_object_rate` -- how OFTEN the model enumerates. Replaces the mean
      object count, which is blind here: measured 1.22 vs 1.50 for arms whose
      rates were 8% and 17%.
    - `twin_rate` -- WHAT it produces. An arm can enumerate just as often and
      still collapse every title onto the source's own heading, which is the
      failure #377 actually describes.

    Either signal alone implicates the anchor. Both flat clears it.
    """
    usable = [r for r in reports if r.responded]
    if len(usable) < 2:
        return (
            "**Inconclusive.** Fewer than two arms produced a usable run. Check "
            "that Ollama is running and the model is pulled, then re-run."
        )

    by_rate = sorted(usable, key=lambda r: r.multi_object_rate)
    rate_low, rate_high = by_rate[0], by_rate[-1]
    by_twin = sorted(usable, key=lambda r: r.twin_rate())
    twin_low, twin_high = by_twin[0], by_twin[-1]

    total_events = sum(r.multi_object_events for r in usable)
    rate_gap = rate_high.multi_object_rate - rate_low.multi_object_rate
    rate_ratio = (
        rate_high.multi_object_rate / rate_low.multi_object_rate
        if rate_low.multi_object_rate
        else float("inf")
        if rate_high.multi_object_rate
        else 1.0
    )
    twin_gap = twin_high.twin_rate() - twin_low.twin_rate()

    lines = []
    for r in usable:
        lines.append(
            f"- `{r.arm}`: multi-object rate **{_fmt(r.multi_object_rate)}** "
            f"({r.multi_object_events}/{len(r.responded)} runs, across "
            f"{r.enumerating_sources} source(s), max {r.max_objects}); "
            f"twin_rate **{_fmt(r.twin_rate())}**; mean {_fmt(r.avg_objects)}."
        )
    lines.append("")
    lines.append(_type_conditional_note(usable))
    lines.append("")

    rate_moves = rate_gap >= _RATE_MIN_GAP and rate_ratio >= _RATE_RATIO
    twin_moves = twin_gap >= _TWIN_MIN_GAP
    underpowered = total_events < _RATE_MIN_EVENTS

    if not rate_moves and not twin_moves:
        lines.append(
            "**The anchor looks innocent.** Neither how often the model "
            "enumerates nor what it titles moved across arms, so the "
            "`SOURCE TITLE:` value does not carry the regression. Do NOT "
            "rewrite the prompt on this evidence: widen the search inside "
            "slice 1. The measured baseline recorded here is still what "
            "#379's gate needs."
        )
        return "\n".join(lines)

    lines.append("**The anchor moves extraction.** With `_SYSTEM_PROMPT` held")
    lines.append("byte-identical across arms:")
    lines.append("")
    if rate_moves:
        lines.append(
            f"- It changes **how often** the model enumerates: "
            f"`{rate_low.arm}` {_fmt(rate_low.multi_object_rate)} -> "
            f"`{rate_high.arm}` {_fmt(rate_high.multi_object_rate)} "
            f"({rate_ratio:.1f}x), over "
            f"{rate_low.enumerating_sources} vs "
            f"{rate_high.enumerating_sources} distinct sources."
        )
    if twin_moves:
        lines.append(
            f"- It changes **what** the model produces: twin_rate "
            f"`{twin_low.arm}` {_fmt(twin_low.twin_rate())} -> "
            f"`{twin_high.arm}` {_fmt(twin_high.twin_rate())}. Objects under "
            f"`{twin_high.arm}` restate the source's own heading instead of "
            f"naming what the document contains -- the twin #377 describes."
        )
    lines.append("")
    if underpowered:
        lines.append(
            f"**Underpowered on the rate signal:** only {total_events} "
            f"multi-object runs pooled across arms (threshold "
            f"{_RATE_MIN_EVENTS}). Re-run with more `--runs` before treating "
            f"the rate as settled."
            + (
                " The twin signal has a per-object denominator and is not "
                "subject to this caveat."
                if twin_moves
                else ""
            )
        )
        lines.append("")
    lines.append(
        "Record this in `design.md` before any prompt edit. Note what it "
        "implicates: a title-framing change lives in `_build_messages`, not in "
        "the rubric -- which per the proposal is an ingest/extraction interface "
        "decision and returns through the ADR gate."
    )
    return "\n".join(lines)


# The two rubric types that do NOT read "the source is fundamentally about ONE
# specific, named X" (`concept.py:38-63`; the code's own comment at
# `concept.py:68` records the seven/nine split).
_UNCAPPED_TYPES = frozenset({"Concept", "Entity"})


def _side_summary(label: str, counts: list[int]) -> str:
    """Describe one side of the rubric line by spread, not by mean alone.

    A mean hides the finding that matters most here. Measured: named-entity
    runs came back n=24, mean 1.00, max 1 -- every single run produced exactly
    one object. Zero variance IS the result, and a mean difference of 0.54
    against a noisier other side got reported as "no split".
    """
    n = len(counts)
    top = max(counts)
    if top == min(counts):
        return f"`{label}` n={n}, **every run produced exactly {top}** (zero variance)"
    return (
        f"`{label}` n={n}, mean {_fmt(statistics.fmean(counts))}, max {top}, "
        f"{sum(1 for c in counts if c >= 2)} run(s) enumerated"
    )


def _type_conditional_note(reports: Sequence[ArmReport]) -> str:
    """Report object counts split by the rubric's seven-vs-two type line.

    Pooled across arms on purpose: this probe asks about the rubric, not the
    title. It reports SPREAD rather than ranking means, because the meaningful
    outcomes here are "one side never enumerates" and "both sides mostly
    produce one" -- neither of which a difference of means expresses.
    """
    capped: list[int] = []
    uncapped: list[int] = []
    for r in reports:
        for first_type, counts in r.counts_by_first_type().items():
            target = uncapped if first_type in _UNCAPPED_TYPES else capped
            target.extend(counts)
    if not capped or not uncapped:
        seen = "only named-entity runs" if capped else "only Concept/Entity runs"
        return (
            f"- Type-conditional probe: **not testable here** ({seen}). Add "
            f"sources on both sides of the line to read it."
        )

    cap_hard = max(capped) == 1
    unc_enumerates = any(c >= 2 for c in uncapped)
    unc_mostly_one = statistics.fmean(uncapped) < 2.0
    if cap_hard and unc_enumerates:
        reading = (
            "**A hard cap on the named-entity side.** No run landing on one of "
            "the seven types phrased ONE-specific-named-X ever produced a "
            "second object, while the exempt side did."
        )
        if unc_mostly_one:
            reading += (
                " But the exempt side still produces one object most of the "
                "time, so the rubric wording explains those capped runs and "
                "NOT the collapse as a whole. Do not read this as the cause."
            )
    elif not cap_hard:
        reading = (
            "**No cap.** Named-entity runs did produce multiple objects, so "
            "that wording is not constraining them."
        )
    else:
        reading = (
            "**Not readable.** Neither side enumerated, so the line cannot be "
            "tested on this sample. More sources, or more runs."
        )
    return (
        f"- Type-conditional probe: {_side_summary('named-entity', capped)}; "
        f"{_side_summary('Concept/Entity', uncapped)}. {reading}"
    )


def build_report(
    reports: Sequence[ArmReport],
    fixtures: Sequence[Fixture],
    model: str,
    runs: int,
    generated_at: datetime,
) -> str:
    """Render the markdown A/B report."""
    by_name = {f.name: f for f in fixtures}
    lines: list[str] = []
    lines.append("# openkos title-anchor A/B (issue #377, proposal D1)")
    lines.append("")
    lines.append(f"_Generated: {generated_at.isoformat(timespec='seconds')}_")
    lines.append("")
    labeled = [f for f in fixtures if is_labeled(f)]
    unlabeled = [f for f in fixtures if not is_labeled(f)]
    lines.append(f"Model: **`{model}`**. Runs per fixture per arm: **{runs}**.")
    lines.append("")
    if labeled:
        lines.append(
            "Labeled fixtures (ground truth from the reference bundle): "
            + ", ".join(f"`{f.name}` (target {f.target_count})" for f in labeled)
            + "."
        )
    if unlabeled:
        lines.append(
            f"Unlabeled corpus sources: **{len(unlabeled)}**. No target types "
            "are declared for these, so `type_acc` and `anti_enum` exclude them "
            "and only count-shaped metrics apply."
        )
    lines.append("")
    lines.append(
        "`_SYSTEM_PROMPT` is byte-identical across every arm. The only variable "
        "is the `SOURCE TITLE:` value in the user turn."
    )
    lines.append("")

    lines.append("## Arms")
    lines.append("")
    lines.append("| Arm | Meaning | Title sent |")
    lines.append("| --- | --- | --- |")
    for r in reports:
        shown = list(r.titles_used.items())[:2]
        sent = "; ".join(
            f"`{name}`: " + ("(omitted)" if t is None else f'"{t}"')
            for name, t in shown
        )
        if len(r.titles_used) > len(shown):
            sent += f"; … +{len(r.titles_used) - len(shown)} more"
        lines.append(f"| `{r.arm}` | {_ARM_BLURB.get(r.arm, '-')} | {sent} |")
    lines.append("")

    lines.append("## Per-arm summary")
    lines.append("")
    lines.append(
        "| Arm | multi_obj_rate | twin_rate | avg_objects | max | schema_valid | "
        "type_acc | anti_enum | avg_lat_s | errors |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in reports:
        lines.append(
            f"| `{r.arm}` | **{_fmt(r.multi_object_rate)}** "
            f"({r.multi_object_events}/{len(r.responded)}, "
            f"{r.enumerating_sources} src) | **{_fmt(r.twin_rate())}** | "
            f"{_fmt(r.avg_objects)} | {r.max_objects} | "
            f"{_fmt(r.schema_valid_rate)} | {_fmt(r.type_accuracy(by_name))} | "
            f"{_fmt(r.anti_enumeration_score(by_name))} | "
            f"{_fmt(r.avg_latency_s)} | {r.backend_errors} |"
        )
    lines.append("")
    lines.append(
        "- **multi_obj_rate**: fraction of runs producing >= 2 objects, with the "
        "raw event count and how many distinct sources contributed. THE primary "
        "signal -- extraction does not fail by shaving a fraction off every "
        "source, it fails by enumerating on far fewer of them."
    )
    lines.append(
        "- **twin_rate**: fraction of produced objects whose title merely "
        "restates the SOURCE TITLE this arm sent (proposal D4). The anchor's "
        "fingerprint, and independent of the rate: an arm can enumerate just as "
        "often and still collapse every title onto the heading. The `none` arm "
        "has no title to echo, so it reads 0.00 by construction, not by merit."
    )
    lines.append(
        "- **avg_objects**: reported for continuity, NOT the criterion. The "
        "distribution is bimodal (mode 1, tail to 3-5), so the mean barely moves "
        "when the rare event doubles: measured 1.22 vs 1.50 for arms whose rates "
        "were 0.08 and 0.17."
    )
    lines.append("")

    # Per-source counts. On a 15-source corpus this table IS the finding --
    # #377's evidence is per-source counts, and a mean hides which sources
    # collapsed and which did not.
    lines.append("## Object count per source")
    lines.append("")
    lines.append("| Source | " + " | ".join(f"`{r.arm}`" for r in reports) + " |")
    lines.append("| --- |" + " --- |" * len(reports))
    for fixture in fixtures:
        cells = []
        for r in reports:
            counts = [len(o.produced) for o in r.responded if o.fixture == fixture.name]
            cells.append("/".join(str(c) for c in counts) if counts else "-")
        flag = "" if is_labeled(fixture) else " *(unlabeled)*"
        lines.append(f"| `{fixture.name}`{flag} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "One cell per arm, showing every run's count in order (`1/1/1` means "
        "three runs of one object each)."
    )
    lines.append("")

    lines.append("## Per-fixture detail (raw [type:title] per run)")
    lines.append("")
    for fixture in fixtures:
        lines.append(f"### `{fixture.name}`")
        lines.append("")
        target_line = (
            f"- Target: {fixture.target_count} -> {dict(fixture.target_multiset)}"
            if is_labeled(fixture)
            else "- Target: none declared (unlabeled corpus source)"
        )
        lines.append(target_line)
        lines.append("")
        for r in reports:
            lines.append(f"- `{r.arm}`:")
            for o in (o for o in r.outcomes if o.fixture == fixture.name):
                if o.status == _ERROR:
                    detail = f"ERROR -- {o.error}"
                elif not o.produced:
                    detail = "[] (nothing extracted)"
                else:
                    detail = ", ".join(f"[{t}:{title}]" for t, title in o.produced)
                lines.append(f"    - run {o.run_index} ({o.latency_s:.1f}s): {detail}")
        lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(verdict(reports, by_name))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Self-test: prove the scoring and report logic with no Ollama.                #
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    """Score synthetic outcomes so the harness is provable without a model."""
    by_name = {f.name: f for f in FIXTURES}
    maria = "call-with-maria"
    sent = "Call with Maria Salazar — 2026-07-14"

    # h1: the collapse -- one object, and it is the twin.
    h1 = ArmReport(arm=ARM_H1, titles_used={maria: sent})
    h1.outcomes.append(RunOutcome(ARM_H1, maria, 1, _OK, (("Event", sent),), 4.0, None))
    # none: three objects, none of them a twin.
    none = ArmReport(arm=ARM_NONE, titles_used={maria: None})
    none.outcomes.append(
        RunOutcome(
            ARM_NONE,
            maria,
            1,
            _OK,
            (
                ("Person", "Maria Salazar"),
                ("Concept", "Apatheia"),
                ("Decision", "Frame the essay on the dichotomy of control"),
            ),
            5.0,
            None,
        )
    )

    failures: list[str] = []
    if h1.avg_objects != 1.0:
        failures.append(f"h1.avg_objects: expected 1.0, got {h1.avg_objects}")
    if h1.twin_rate() != 1.0:
        failures.append(f"h1.twin_rate: expected 1.0, got {h1.twin_rate()}")
    if none.avg_objects != 3.0:
        failures.append(f"none.avg_objects: expected 3.0, got {none.avg_objects}")
    if none.twin_rate() != 0.0:
        failures.append(f"none.twin_rate: expected 0.0, got {none.twin_rate()}")
    if none.type_accuracy(by_name) != 1.0:
        failures.append(
            f"none.type_accuracy: expected 1.0, got {none.type_accuracy(by_name)}"
        )
    if none.anti_enumeration_score(by_name) != 1.0:
        failures.append(
            "none.anti_enumeration_score: expected 1.0 against the corrected "
            f"3-object target, got {none.anti_enumeration_score(by_name)}"
        )
    if h1.multi_object_rate != 0.0:
        failures.append(
            f"h1.multi_object_rate: expected 0.0, got {h1.multi_object_rate}"
        )
    if none.multi_object_rate != 1.0 or none.max_objects != 3:
        failures.append(
            f"none rate/max: expected 1.0/3, got "
            f"{none.multi_object_rate}/{none.max_objects}"
        )

    guilty = verdict([h1, none], by_name)
    if "anchor moves extraction" not in guilty:
        failures.append("verdict: a 0.0 -> 1.0 rate jump should implicate the anchor")
    if "Underpowered" not in guilty:
        failures.append("verdict: 1 pooled multi-object run must flag underpowered")

    flat = verdict(
        [
            h1,
            ArmReport(
                arm=ARM_STEM, titles_used={maria: sent}, outcomes=list(h1.outcomes)
            ),
        ],
        by_name,
    )
    if "anchor looks innocent" not in flat:
        failures.append("verdict: two identical arms should read as innocent")

    # REGRESSION GUARD for the defect this replaced. These are the real
    # 2026-08-04 corpus numbers: means 1.22 vs 1.50 (a spread the old
    # 0.5-threshold called noise) over rates of 8% vs 17%, with twin_rate
    # 0.30 vs 0.11. Both signals must now fire.
    def _arm(name: str, counts: list[int], twins: int) -> ArmReport:
        rep = ArmReport(
            arm=name, titles_used={f"s{i}": "T" for i in range(len(counts))}
        )
        for i, c in enumerate(counts):
            produced = tuple(
                ("Concept", "T" if j < twins else f"x{i}{j}") for j in range(c)
            )
            rep.outcomes.append(RunOutcome(name, f"s{i}", 1, _OK, produced, 1.0, None))
            twins -= min(twins, c)
        return rep

    real_h1 = _arm(ARM_H1, [1] * 33 + [3, 3, 5], 13)
    real_stem = _arm(ARM_STEM, [1] * 30 + [3, 3, 3, 5, 5, 5], 6)
    if abs(real_h1.avg_objects - 1.22) > 0.02:
        failures.append(f"guard: h1 mean should be ~1.22, got {real_h1.avg_objects}")
    if abs(real_stem.avg_objects - 1.50) > 0.02:
        failures.append(
            f"guard: stem mean should be ~1.50, got {real_stem.avg_objects}"
        )
    real = verdict([real_h1, real_stem], by_name)
    if "anchor moves extraction" not in real:
        failures.append(
            "REGRESSION: the real corpus numbers must implicate the anchor. A "
            "mean-spread criterion called them noise; that is the bug this "
            "guard exists to catch."
        )
    if "how often" not in real:
        failures.append("guard: the rate signal should fire on 8% vs 17%")

    report = build_report([h1, none], FIXTURES, "self-test", 1, datetime.now(UTC))
    for needle in ("## Verdict", "## Object count per source", "## Arms"):
        if needle not in report:
            failures.append(f"build_report: missing section {needle!r}")

    # Unlabeled corpus sources must not be scored on type_acc/anti_enum: with
    # target_count == 0 the anti_enum formula degenerates to 0.0, which would
    # read as catastrophic over-production instead of "no target declared".
    unlabeled = Fixture(
        name="tutorial-01",
        raw_path=Path("tutorial-01.md"),
        source_title="Tutorial 01",
        target_types=(),
    )
    if is_labeled(unlabeled):
        failures.append("is_labeled: a fixture with no target_types is unlabeled")
    mixed = ArmReport(arm=ARM_H1, titles_used={maria: sent, "tutorial-01": "T"})
    mixed.outcomes = [
        RunOutcome(
            ARM_H1,
            maria,
            1,
            _OK,
            (("Person", "Maria Salazar"), ("Concept", "Apatheia"), ("Decision", "F")),
            4.0,
            None,
        ),
        RunOutcome(ARM_H1, "tutorial-01", 1, _OK, (("Concept", "A"),) * 6, 4.0, None),
    ]
    by_mixed = {**by_name, "tutorial-01": unlabeled}
    if mixed.type_accuracy(by_mixed) != 1.0:
        failures.append(
            "type_accuracy must ignore unlabeled sources, got "
            f"{mixed.type_accuracy(by_mixed)}"
        )
    if mixed.anti_enumeration_score(by_mixed) != 1.0:
        failures.append(
            "anti_enumeration_score must ignore unlabeled sources, got "
            f"{mixed.anti_enumeration_score(by_mixed)}"
        )
    if mixed.avg_objects != 4.5:  # counts still include them: (3 + 6) / 2
        failures.append(
            f"avg_objects must count unlabeled runs, got {mixed.avg_objects}"
        )

    # Type-conditional probe. The branch that matters is the real one: the
    # named-entity side capped at exactly 1 across every run while the exempt
    # side enumerated. A mean-difference criterion scored that 1.00 vs 1.54 and
    # reported "no split"; zero variance on one side IS the finding.
    capped_arm = ArmReport(arm=ARM_H1, titles_used={})
    capped_arm.outcomes = [
        RunOutcome(ARM_H1, f"p{i}", 1, _OK, (("Person", f"P{i}"),), 1.0, None)
        for i in range(8)
    ] + [
        RunOutcome(ARM_H1, "c0", 1, _OK, (("Concept", "A"),), 1.0, None),
        RunOutcome(
            ARM_H1, "c1", 1, _OK, (("Concept", "A"), ("Concept", "B")), 1.0, None
        ),
    ]
    probe_note = _type_conditional_note([capped_arm])
    if "hard cap on the named-entity side" not in probe_note:
        failures.append(f"probe missed the zero-variance cap: {probe_note!r}")
    if "zero variance" not in probe_note:
        failures.append("probe must name zero variance rather than report a mean")
    if "NOT the collapse as a whole" not in probe_note:
        failures.append(
            "probe must refuse to read the cap as the cause while the exempt "
            "side also mostly yields one object"
        )

    uncapped_arm = ArmReport(arm=ARM_H1, titles_used={})
    uncapped_arm.outcomes = [
        RunOutcome(ARM_H1, "p0", 1, _OK, (("Person", "A"), ("Person", "B")), 1.0, None),
        RunOutcome(ARM_H1, "c0", 1, _OK, (("Concept", "A"),), 1.0, None),
    ]
    if "No cap." not in _type_conditional_note([uncapped_arm]):
        failures.append("probe must report no cap when named-entity runs enumerate")

    one_sided = _type_conditional_note([none])
    if "not testable here" not in one_sided:
        failures.append("probe must decline when only one side of the line is present")

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1
    print("self-test OK: scoring, twin detection, verdict, and report all pass.")
    print()
    print(report)
    return 0


# --------------------------------------------------------------------------- #
# CLI.                                                                         #
# --------------------------------------------------------------------------- #


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the A/B harness command line."""
    parser = argparse.ArgumentParser(
        prog="run_title_ab.py",
        description=(
            "Title-anchor A/B for issue #377: hold the corpus, model and "
            "_SYSTEM_PROMPT constant, vary only the SOURCE TITLE value."
        ),
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model tag to test (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--arms",
        default=",".join(ALL_ARMS),
        help=f"Comma-separated arms to run (default: {','.join(ALL_ARMS)}).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"Samples per fixture per arm (default: {DEFAULT_RUNS}).",
    )
    parser.add_argument(
        "--corpus",
        default=None,
        help=(
            "Directory of .md/.txt sources to measure INSTEAD of the two "
            "good-life-demo fixtures. Loaded unlabeled (counts only). This is "
            "how #377's 15-source corpus enters the harness -- the only place "
            "the 3->1 regression was ever observed."
        ),
    )
    parser.add_argument(
        "--with-fixtures",
        action="store_true",
        help="With --corpus, also run the two labeled good-life-demo fixtures.",
    )
    parser.add_argument("--host", default=None, help="Ollama host override.")
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="Per-call seconds (default: 120)."
    )
    parser.add_argument(
        "--output",
        default=str(_HERE / "report-title-ab.md"),
        help="Report path (default: evals/model_spike/report-title-ab.md).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Prove scoring/report logic on synthetic data; no Ollama needed.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the A/B and write the report. Returns a process exit code."""
    args = parse_args(argv)
    if args.self_test:
        return _self_test()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ALL_ARMS]
    if unknown:
        print(
            f"unknown arm(s): {', '.join(unknown)} (valid: {', '.join(ALL_ARMS)})",
            file=sys.stderr,
        )
        return 2

    if args.corpus:
        try:
            corpus = load_corpus(Path(args.corpus).expanduser())
        except (NotADirectoryError, FileNotFoundError) as exc:
            print(f"--corpus: {exc}", file=sys.stderr)
            return 2
        fixtures = (*FIXTURES, *corpus) if args.with_fixtures else corpus
    else:
        fixtures = FIXTURES

    probe = OllamaClient(model=args.model, host=args.host, timeout=args.timeout)
    try:
        installed = [m.tag for m in probe.list_models()]
    except OllamaError as exc:
        print(
            f"cannot reach Ollama ({type(exc).__name__}: {exc}). "
            "Start it, or use --self-test to prove the harness offline.",
            file=sys.stderr,
        )
        return 1
    if not model_tag_matches(args.model, installed):
        print(
            f"model {args.model!r} is not installed on the host "
            f"(`ollama pull {args.model}`).",
            file=sys.stderr,
        )
        return 1

    client = OllamaClient(model=args.model, host=args.host, timeout=args.timeout)
    total_calls = len(arms) * args.runs * len(fixtures)
    print(
        f"title-anchor A/B: model={args.model}, arms={','.join(arms)}, "
        f"runs={args.runs}, sources={len(fixtures)} "
        f"({total_calls} LLM calls total)"
    )
    if total_calls > 100:
        # Observed mean was ~24s/call on qwen3:8b. A 15-source corpus over
        # three arms is 135 calls -- nearly an hour. Say so before spending it,
        # not after (issue #382 is this same lesson, one verb over).
        print(
            f"  heads-up: at ~25s/call that is roughly "
            f"{total_calls * 25 // 60} minutes. Narrow with --arms or --runs "
            f"if that is more than you meant to spend."
        )
    reports = [evaluate_arm(arm, fixtures, args.runs, client) for arm in arms]

    generated_at = datetime.now(UTC)
    report = build_report(reports, fixtures, args.model, args.runs, generated_at)
    out = Path(args.output)
    out.write_text(report, encoding="utf-8")
    stamped = (
        _HERE
        / "results"
        / (
            f"title-ab-{generated_at.strftime('%Y%m%dT%H%M%SZ')}-{args.model.replace(':', '-')}.md"
        )
    )
    stamped.parent.mkdir(parents=True, exist_ok=True)
    stamped.write_text(report, encoding="utf-8")
    print(f"\nwrote {out}\nwrote {stamped}")
    print()
    print(verdict(reports, {f.name: f for f in fixtures}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
