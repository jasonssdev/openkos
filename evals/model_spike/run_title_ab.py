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
`avg_objects` is the primary signal, because the regression is a count (3 -> 1).
`twin_rate` is the mechanism: the fraction of produced objects whose title
merely restates the SOURCE TITLE. If the anchor is guilty, `h1` shows the
lowest `avg_objects` and the highest `twin_rate`, and `none` shows the highest
count with a `twin_rate` near zero. If the three arms are flat, the anchor is
innocent, the search widens inside slice 1, and no prompt gets rewritten
against a guess.

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
        """Mean produced-object count over responded runs. THE primary signal."""
        responded = self.responded
        if not responded:
            return 0.0
        return statistics.fmean(len(o.produced) for o in responded)

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


def verdict(reports: Sequence[ArmReport], by_name: dict[str, Fixture]) -> str:
    """State what the numbers support, and refuse to overclaim when flat.

    The threshold is deliberate: a spread below 0.5 objects per run over a
    handful of non-deterministic samples is noise, and calling it a cause is
    exactly the mistake this harness exists to prevent.
    """
    usable = [r for r in reports if r.responded]
    if len(usable) < 2:
        return (
            "**Inconclusive.** Fewer than two arms produced a usable run. Check "
            "that Ollama is running and the model is pulled, then re-run."
        )
    ranked = sorted(usable, key=lambda r: r.avg_objects)
    lowest, highest = ranked[0], ranked[-1]
    spread = highest.avg_objects - lowest.avg_objects
    lines = [
        f"- Object-count spread across arms: **{_fmt(spread)}** "
        f"(`{lowest.arm}` {_fmt(lowest.avg_objects)} -> "
        f"`{highest.arm}` {_fmt(highest.avg_objects)}).",
    ]
    for r in usable:
        lines.append(f"- `{r.arm}` twin_rate: **{_fmt(r.twin_rate())}**.")
    lines.append("")
    lines.append(_type_conditional_note(usable))
    lines.append("")
    if spread < 0.5:
        lines.append(
            "**The anchor looks innocent.** The arms are within noise of each "
            "other, so the `SOURCE TITLE:` value does not carry the 3->1 "
            "regression. Do NOT rewrite the prompt on this evidence: widen the "
            "search inside slice 1. The measured baseline recorded here is "
            "still what #379's gate needs."
        )
    else:
        lines.append(
            f"**The anchor moves extraction.** Removing or weakening the title "
            f"changes the object count by {_fmt(spread)} per run with "
            f"`_SYSTEM_PROMPT` held byte-identical, which is the D1 hypothesis "
            f"surviving its test. Record this in `design.md` before any prompt "
            f"edit, and let it decide whether `_build_messages`' title framing "
            f"changes."
        )
    return "\n".join(lines)


# The two rubric types that do NOT read "the source is fundamentally about ONE
# specific, named X" (`concept.py:38-63`; the code's own comment at
# `concept.py:68` records the seven/nine split).
_UNCAPPED_TYPES = frozenset({"Concept", "Entity"})


def _type_conditional_note(reports: Sequence[ArmReport]) -> str:
    """Report object counts split by the rubric's seven-vs-two type line.

    Pooled across arms on purpose: the arms are the title variable, and this
    probe asks a different question that the title was shown not to move.
    """
    capped: list[int] = []
    uncapped: list[int] = []
    for r in reports:
        for first_type, counts in r.counts_by_first_type().items():
            target = uncapped if first_type in _UNCAPPED_TYPES else capped
            target.extend(counts)
    if not capped or not uncapped:
        seen = "only capped-type runs" if capped else "only Concept/Entity runs"
        return (
            f"- Type-conditional probe: **not testable here** ({seen}). Add "
            f"sources on both sides of the line to read it."
        )
    cap_mean = statistics.fmean(capped)
    unc_mean = statistics.fmean(uncapped)
    verdict_word = (
        "**splits along the rubric line**"
        if unc_mean - cap_mean >= 1.0
        else "does NOT split along the rubric line"
    )
    return (
        f"- Type-conditional probe: runs landing on a named-entity type average "
        f"**{_fmt(cap_mean)}** objects (n={len(capped)}); runs landing on "
        f"`Concept`/`Entity` average **{_fmt(unc_mean)}** (n={len(uncapped)}). "
        f"Extraction {verdict_word} "
        f'(seven of nine types read "the source is fundamentally about ONE '
        f'specific, named X"; `Concept` and `Entity` are exempt).'
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
        "| Arm | avg_objects | twin_rate | schema_valid | type_acc | "
        "anti_enum | avg_lat_s | errors |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in reports:
        lines.append(
            f"| `{r.arm}` | **{_fmt(r.avg_objects)}** | {_fmt(r.twin_rate())} | "
            f"{_fmt(r.schema_valid_rate)} | {_fmt(r.type_accuracy(by_name))} | "
            f"{_fmt(r.anti_enumeration_score(by_name))} | "
            f"{_fmt(r.avg_latency_s)} | {r.backend_errors} |"
        )
    lines.append("")
    lines.append(
        "- **avg_objects**: mean produced-object count per run. The primary "
        "signal, because the regression is a count (3 -> 1)."
    )
    lines.append(
        "- **twin_rate**: fraction of produced objects whose title merely "
        "restates the SOURCE TITLE this arm sent (proposal D4). The anchor's "
        "fingerprint. The `none` arm has no title to echo, so it reads 0.00 by "
        "construction, not by merit."
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
    guilty = verdict([h1, none], by_name)
    if "anchor moves extraction" not in guilty:
        failures.append("verdict: a 2.0 spread should read as the anchor moving")
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
        failures.append("verdict: a 0.0 spread should read as innocent")

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

    # Type-conditional probe: Person runs cap at 1, Concept runs spread.
    probe_note = _type_conditional_note([h1, none, mixed])
    if "splits along the rubric line" not in probe_note:
        failures.append(f"type-conditional probe misread the split: {probe_note!r}")
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
