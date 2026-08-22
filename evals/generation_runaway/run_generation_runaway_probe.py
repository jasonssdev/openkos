"""Which call runs away on a 631-byte source, and can a lower bound cut it? (#828)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the REAL
`extract_concept_union` at the SHIPPED generation ceiling and records every chat
call it makes -- phase, prompt tokens, generated tokens, `done_reason`, seconds
-- so a run that burns 238s to produce nothing names the call that burned it.

## The failure

#828: `kickoff`, a 631-byte source, hits the shipped 8192-token
`max_generation_tokens` ceiling in 3 of 10 runs on `qwen3:8b`, twice after 222s
and 238s. Successful runs on the SAME bytes finish in 20-46s with 8-14 objects.
So this is not a source that is too large for the ceiling; it is a generation
that occasionally fails to terminate on a source that normally takes 30s.

The authorized scope is **diagnostic + fail-fast**: record WHICH call hit the
ceiling, and cut fast instead of burning 222s for a result that is unusable
either way.

## Why a lower generation ceiling is the ONLY fail-fast lever

`OllamaClient.chat` sends `"stream": False` (`src/openkos/llm/ollama.py`), so
the whole body arrives in one `read()` after generation has already finished.
There is no token stream to watch and no partial body to abandon, which means
the client CANNOT abort a runaway mid-generation. It cannot notice one either:
the first thing it learns about the reply is that the reply is over.

That leaves exactly one lever -- `options.num_predict`, the ceiling itself.
Lowering it from 8192 to some B makes the backend stop at B tokens instead of
8192, so the 238s runaway becomes a shorter failure. It does not become a
success: a cut-off reply is unusable (`extract_json_items` returns `[]` on a
mid-JSON truncation), so fail-fast shortens the failure, it never rescues it.

This probe exists to measure whether a SEPARATING B exists at all: one that
every legitimate reply stays strictly below, so that lowering the ceiling costs
no healthy run. If no such B exists, the fail-fast half of #828 is refuted and
only the diagnostic half survives.

## No arms, deliberately

This is a distribution measurement at shipped settings, not an A/B. There is no
treatment axis to build: the candidate bounds are swept ARITHMETICALLY over the
stored calls (Q3), because a call that generated 1180 tokens under an 8192
ceiling would have generated 1024 and been cut under a 1024 one -- no second
sweep can tell us more than the first sweep's own token counts already do.

Keeping the arm axis out is also the cheapest defence against the failure
`evals/` has already had once: a probe that shipped an INERT arm and reported
numbers for a treatment that never ran. An axis that does not exist cannot be
inert.

## What the ceiling ACTUALLY separates

Every capped call generated the ceiling's worth of tokens, by definition of the
cap -- so the runaway side of the distribution is degenerate, pinned at 8192,
and "is a runaway separable?" collapses to a single question about the OTHER
side: how close does the largest LEGITIMATE reply come? That number is the
floor any candidate bound must clear, and it is the whole of the answer. Q2
prints it per phase for exactly that reason.

Usage:

    uv run python -u evals/generation_runaway/run_generation_runaway_probe.py --self-test
    uv run python -u evals/generation_runaway/run_generation_runaway_probe.py --runs 10
    uv run python -u evals/generation_runaway/run_generation_runaway_probe.py --fixture kickoff
    uv run python -u evals/generation_runaway/run_generation_runaway_probe.py --rescore results/<file>.jsonl

**Use `-u`.** Piping through `tee` makes Python buffer and a long run looks hung.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from openkos.config import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
    minimum_context_window,
)
from openkos.extraction.concept import extract_concept_union
from openkos.llm.ollama import DEFAULT_TIMEOUT, OllamaClient, OllamaGenerationCapped

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"

_CEILING_PROBE = HERE.parent / "generation_ceiling" / "run_generation_ceiling_probe.py"
_SECTION_FIXTURES = HERE.parent / "section_coverage" / "section_fixtures.py"

MAX_GENERATION_TOKENS: Final = DEFAULT_MAX_GENERATION_TOKENS
"""The ceiling under test, READ from `openkos.config` rather than restated.

#828 is a report about the shipped setting, so the probe has to send the
shipped setting. A literal `8192` here would keep measuring 8192 after the
packaged default moved, and the sweep would silently be about a value nothing
ships -- the same class of defect as a doc that teaches a stale default."""

CONTEXT_WINDOW: Final = max(
    DEFAULT_CONTEXT_WINDOW, minimum_context_window(DEFAULT_MAX_GENERATION_TOKENS)
)
"""The window a real `ingest` resolves, derived the way `config.read_config`
derives it (`max(DEFAULT_CONTEXT_WINDOW, minimum_context_window(...))`).

It matters here and not only in production: `num_ctx` bounds the prompt and the
completion TOGETHER, so a client left unpinned would give generation a 32768-
token Modelfile window and could report a cut-off -- or the absence of one --
that no shipped configuration would ever see."""

CANDIDATE_BOUNDS: Final = (1024, 1536, 2048, 3072, 4096)
"""The `num_predict` values Q3 costs out. Candidates under test, not proposals.

Swept rather than singular because the two things a bound trades off move in
opposite directions: a lower B cuts the runaway sooner and falsely cuts more
healthy replies. The table exists so the trade is read off measured tokens
instead of chosen by intuition."""


def _load_module(path: Path, name: str) -> Any:
    """Import a sibling eval module for reuse.

    Imported rather than copied, on the precedent
    `evals/generation_ceiling/run_generation_ceiling_probe.py` set (and states
    in this function's twin): a second copy of the recording transport, or of
    #793's fixtures, would drift silently, leaving two artefacts with one name
    and no way to say which produced which number.

    This one loader is the unavoidable exception to its own rule -- an importer
    cannot import itself -- so it is the only thing here that is not borrowed.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if not _CEILING_PROBE.exists():  # pragma: no cover - defensive
    raise SystemExit(f"cannot find {_CEILING_PROBE}")
if not _SECTION_FIXTURES.exists():  # pragma: no cover - defensive
    raise SystemExit(f"cannot find {_SECTION_FIXTURES}")

_ceiling = _load_module(_CEILING_PROBE, "_generation_ceiling_probe")
_fixtures = _load_module(_SECTION_FIXTURES, "_section_fixtures")

_RecordingTransport = _ceiling._RecordingTransport
"""#714's `urlopen` stand-in: logs every chat call's counters and replays the
bytes unchanged, so no production file is touched and the request that goes out
is byte-identical to a real `ingest`'s. Borrowed whole -- the phase-attribution
guarantee it documents is the same guarantee #828 needs."""

_ReplayResponse = _ceiling._ReplayResponse

Fixture = _fixtures.Fixture
HELIOS_OVERVIEW = _fixtures.HELIOS_OVERVIEW
KICKOFF = _fixtures.KICKOFF


def build_fixtures() -> list[Any]:
    """Both #793 fixtures, `kickoff` first: it is the source #828 was filed on.

    Both are IN-REPO bytes (`evals/section_coverage/section_fixtures.py`), so
    this probe reproduces on a clean checkout with no private corpus and no
    build step. `helios-overview` rides along as the neighbouring source from
    the same bundle at comparable size: if the runaway is a property of the
    ceiling and the model rather than of `kickoff`'s text, it should appear
    there too, and if it appears ONLY on `kickoff` that is a fact about the
    source worth having before anyone lowers a global constant.
    """
    return [KICKOFF, HELIOS_OVERVIEW]


def fixture_from_path(path: Path, title: str | None = None) -> Any:
    """One `Fixture` read off disk, for a source this repo cannot commit.

    Exists because the two in-repo fixtures are ~600 bytes, and a bound that
    is safe there can still be wrong for the regime it would actually govern:
    `config.DEFAULT_MAX_GENERATION_TOKENS` was calibrated (2026-08-06) on 17 KB
    prose whose largest legitimate completed reply was 4154 tokens -- more than
    twice anything these fixtures produce. `_CHUNK_THRESHOLD` is 18 000, so a
    17 KB prose source is the LARGEST one that still extracts in a single
    unchunked call, which makes it the worst case for lowering the ceiling and
    the one arm a global bound must survive.

    The fixture is named by the file's STEM, never its path: a path carries a
    username and a directory layout, and this name is printed and stored.
    Nothing here records the source's TEXT or its objects -- `RunRecord` keeps
    counts, phases and token counters only -- so pointing this at a private
    corpus file stores no private content.

    `must_fire`/`must_stay_quiet` are `()`: they belong to #793's section
    signal and mean nothing to this probe, which reads only `name`, `title`
    and `text`.

    The TITLE is derived from the source's own first ATX heading, NOT from the
    filename, and falls back to the stem only when the source has no heading.
    That default is deliberate and load-bearing: the engine gates real
    behaviour on the title -- `_is_meeting_shaped(title, text)` selects between
    `_CHUNK_THRESHOLD` and `_MEETING_CHUNK_THRESHOLD`, and the user turn
    suppresses a meeting-shaped title -- so a harness that passes `path.stem`
    silently measures a pipeline nobody runs, and any title-gated mechanism
    reads as a false zero. `--source-title` overrides it; the resolved title is
    printed so the value under measurement is never implicit.
    """
    text = path.read_text(encoding="utf-8")
    return Fixture(
        name=path.stem,
        title=title if title is not None else _title_from_text(text, path),
        text=text,
        must_fire=(),
        must_stay_quiet=(),
    )


def _title_from_text(text: str, path: Path) -> str:
    """The source's first ATX heading, or the file stem when it has none."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return path.stem


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunRecord:
    fixture: str
    run: int
    chars: int
    capped: bool
    """The cut-off ESCAPED the pipeline and failed the run.

    Only `_extract_once` can produce this. Every other call in the run swallows
    its own backend failures by contract -- `judge.select`'s D7 fail-closed
    rule, and the broad `except Exception` in `_reask_for_further_subjects` and
    `_capture_further_participants` -- so a truncated reply there degrades the
    result instead of raising.

    Since #828 those two swallowed failures are NAMED rather than discarded:
    they still degrade to empty additions and still do not raise, and the cause
    now reaches `ExtractionReport.optional_call_failures`. Nothing this field
    records changes -- what escapes is still only the extraction call."""
    capped_phases: list[str]
    """Every phase whose reply Ollama cut off, INCLUDING the swallowed ones.

    Kept apart from `capped` because the swallowed ones are invisible to the
    EXCEPTION and so to `capped`: a cut-off judge reply becomes
    `judge_status="failed"` and the run keeps its full unfiltered candidate set,
    while a cut-off participant pass finds nobody. Those are quality failures
    that read as clean runs, and a table counting only raised exceptions
    reports them as zero. #828 names the two bonus calls' causes on the report
    the pipeline returns; this ledger is what attributes a cut-off to a PHASE,
    including the judge's, which that report does not cover."""
    error: str
    objects: int
    seconds: float
    model: str
    ceiling: int
    """The `max_generation_tokens` this run actually sent.

    Stored per run, not read from the module constant at report time, so
    `--rescore` on an old sweep cannot relabel it with today's packaged
    default. The bound sweep is arithmetic ON this number; getting it from
    somewhere other than the run that produced the tokens would make every Q3
    row fiction."""
    context_window: int
    calls: list[dict[str, Any]] = field(default_factory=list)


def raising_call_index(record: RunRecord) -> int | None:
    """Which recorded call RAISED, or `None` if the run had no escaping cap.

    `OllamaGenerationCapped` out of `_extract_once` aborts
    `extract_concept_union` outright -- no later call is issued -- so the
    raising call is necessarily the LAST one recorded, and it is a cut-off one.
    Derived from the ledger rather than from the exception, which names the
    ceiling and never the caller.

    Returns `None` if the last call was not cut off on a run that raised: that
    would mean the pipeline kept calling after a propagating cap, which is a
    change in the pipeline, not a fact about #828, and it must read as
    unattributed rather than be pinned on an innocent phase."""
    if not record.capped or not record.calls:
        return None
    last = len(record.calls) - 1
    if record.calls[last].get("done_reason") != "length":
        return None
    return last


def run_fixture(
    fixture: Any,
    llm: OllamaClient,
    transport: Any,
    runs: int,
    model: str,
) -> list[RunRecord]:
    """`runs` full `extract_concept_union` runs over one fixture, win or lose.

    A cut-off run is DATA, not an error to abort a sweep on: the rate is half
    the measurement and the runaway's token/second figures are the other half.
    Every other exception is recorded by class name instead, because an
    unreachable backend and a truncated reply are different facts and #828 is
    only about the second."""
    records: list[RunRecord] = []
    for index in range(1, runs + 1):
        transport.reset()
        started = time.monotonic()
        capped = False
        error = ""
        objects = 0
        try:
            outcome = extract_concept_union(
                fixture.text,
                source_title=fixture.title,
                llm=llm,
                on_progress=lambda phase: setattr(transport, "phase", phase),
            )
            objects = len(outcome.objects)
        except OllamaGenerationCapped:
            capped = True
        except Exception as exc:  # broad: every failure is data here
            error = type(exc).__name__
        seconds = time.monotonic() - started
        # Off the ledger, never inferred from the exception: three of the four
        # call sites in a run cannot raise one at all, so an exception-derived
        # list would report their cut-offs as zero.
        capped_phases = [
            call["phase"] for call in transport.calls if call["done_reason"] == "length"
        ]
        record = RunRecord(
            fixture=fixture.name,
            run=index,
            chars=len(fixture.text),
            capped=capped,
            capped_phases=capped_phases,
            error=error,
            objects=objects,
            seconds=round(seconds, 1),
            model=model,
            ceiling=MAX_GENERATION_TOKENS,
            context_window=CONTEXT_WINDOW,
            calls=list(transport.calls),
        )
        records.append(record)
        status = "RAISED-CAP" if capped else (error or f"{objects} obj")
        worst = max((call["gen_tokens"] or 0 for call in record.calls), default=0)
        detail = f" [cut off at: {', '.join(capped_phases)}]" if capped_phases else ""
        print(
            f"      run {index}/{runs}: {status}{detail}, "
            f"{len(record.calls)} call(s), worst gen {worst}, "
            f"{record.seconds}s",
            flush=True,
        )
    return records


# --------------------------------------------------------------------------- #
# Q1 -- which call runs away
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PhaseStats:
    phase: str
    calls: int
    raised: int
    swallowed: int
    max_gen: int
    median_gen: int
    max_prompt: int
    worst_seconds: float
    max_legit_gen: int
    """The largest reply this phase finished NORMALLY.

    The number every candidate bound has to clear, per phase. Reported beside
    the caps rather than under them because it, not the cap count, is what
    decides whether a lower ceiling is available."""
    legit_calls: int
    runaway_gens: list[int]


def phase_stats(records: list[RunRecord]) -> list[PhaseStats]:
    """One row per pipeline phase, with RAISED and SWALLOWED caps kept apart.

    A capped judge or participant reply looks like a clean run to the caller,
    so a ledger that counted only raised exceptions would report those phases
    as never having been cut off at all."""
    calls: dict[str, list[dict[str, Any]]] = {}
    raised: dict[str, int] = {}
    for record in records:
        raiser = raising_call_index(record)
        for position, call in enumerate(record.calls):
            phase = str(call["phase"])
            calls.setdefault(phase, []).append(call)
            if position == raiser:
                raised[phase] = raised.get(phase, 0) + 1
    rows: list[PhaseStats] = []
    for phase, entries in calls.items():
        gens = [int(call["gen_tokens"] or 0) for call in entries]
        prompts = [int(call["prompt_tokens"] or 0) for call in entries]
        seconds = [float(call["seconds"] or 0.0) for call in entries]
        # Split on `done_reason`, never by removing one list from another:
        # two calls in the same phase can carry byte-identical counters, and a
        # membership test would drop a legitimate twin of a runaway.
        cut = [call for call in entries if call["done_reason"] == "length"]
        legit = [
            int(call["gen_tokens"] or 0)
            for call in entries
            if call["done_reason"] != "length"
        ]
        rows.append(
            PhaseStats(
                phase=phase,
                calls=len(entries),
                raised=raised.get(phase, 0),
                swallowed=len(cut) - raised.get(phase, 0),
                max_gen=max(gens, default=0),
                median_gen=int(statistics.median(gens)) if gens else 0,
                max_prompt=max(prompts, default=0),
                worst_seconds=round(max(seconds, default=0.0), 1),
                max_legit_gen=max(legit, default=0),
                legit_calls=len(legit),
                runaway_gens=[int(c["gen_tokens"] or 0) for c in cut],
            )
        )
    rows.sort(key=lambda row: (-row.raised - row.swallowed, -row.max_gen))
    return rows


# --------------------------------------------------------------------------- #
# Q3 -- what a candidate bound would cost and save
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BoundCost:
    bound: int
    false_cuts: int
    """Legitimate replies that generated at least `bound` tokens, and so would
    have been CUT OFF had the ceiling been `bound`.

    The refutation criterion. Any non-zero value here is a healthy run this
    bound would have destroyed, and it is reported as such -- never averaged
    into a saving, never smoothed into a rate."""
    false_cut_detail: list[str]
    legit_calls: int
    runaway_calls: int
    seconds_saved: float
    """Wall clock a cut-off run would NOT have burned, derived per call from
    that call's OWN observed tokens per second.

    Never from an assumed throughput: the runaway calls are the slow ones, and
    borrowing a healthy call's rate would overstate the saving by whatever
    factor the machine was degraded by at exactly the moment that matters."""
    cut_run_seconds: float
    cut_runs: int


def bound_costs(records: list[RunRecord], bound: int) -> BoundCost:
    """Cost and saving of a `num_predict` of `bound`, computed from stored calls.

    Arithmetic on tokens already measured, so `--rescore` reproduces every row
    without a single model call. A reply that generated `n` tokens under the
    shipped ceiling would have stopped at `bound` under this one; whether that
    stop is a saving or a false cut depends only on whether the reply was a
    runaway or a finished one, which `done_reason` already recorded."""
    legit: list[tuple[str, int]] = []
    runaways: list[tuple[int, float]] = []
    for record in records:
        for call in record.calls:
            gen = int(call["gen_tokens"] or 0)
            if call["done_reason"] == "length":
                runaways.append((gen, float(call["seconds"] or 0.0)))
            else:
                legit.append((str(call["phase"]), gen))
    # `>=`, not `>`: a reply needing exactly `bound` tokens stops AT the
    # ceiling, which Ollama reports as `done_reason: "length"` -- the client
    # then raises on a reply that had in fact just finished. Counting it as a
    # false cut is the honest reading of the boundary.
    false = [(phase, gen) for phase, gen in legit if gen >= bound]
    saved = 0.0
    # `>=` here too, for ONE reading of the boundary across both halves: a
    # runaway that generated exactly `bound` tokens would have been stopped at
    # exactly the same token it in fact stopped at, so it belongs on the same
    # side of the comparison as the legitimate reply of the same size. The
    # NUMBER is unchanged either way -- its saving term is `gen - bound == 0`
    # -- which is why the asymmetry was invisible and why the choice is about
    # the reading, not the arithmetic. The self-test pins the boundary saving
    # at zero so a later edit cannot turn that no-op into a credit.
    for gen, seconds in runaways:
        if gen >= bound and gen > 0 and seconds > 0:
            per_second = gen / seconds
            saved += (gen - bound) / per_second
    cut_runs = [r for r in records if r.capped_phases]
    return BoundCost(
        bound=bound,
        false_cuts=len(false),
        false_cut_detail=[
            f"{phase} generated {gen} (>= {bound})"
            for phase, gen in sorted(false, key=lambda item: -item[1])
        ],
        legit_calls=len(legit),
        runaway_calls=len(runaways),
        seconds_saved=round(saved, 1),
        cut_run_seconds=round(sum(r.seconds for r in cut_runs), 1),
        cut_runs=len(cut_runs),
    )


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _ratio(numerator: float, denominator: float, unit: str = "") -> str:
    """`a / b = r`, with both terms printed.

    A bare ratio hides which of its two terms moved, and this repo has already
    shipped a wrong 355x from a units mismatch that both terms beside the
    quotient would have exposed on sight."""
    if denominator <= 0:
        return f"{numerator:.1f}{unit} / 0{unit} = n/a"
    return (
        f"{numerator:.1f}{unit} / {denominator:.1f}{unit} = "
        f"{numerator / denominator:.2f}"
    )


def render(records: list[RunRecord]) -> str:
    """The three questions #828 has to answer, in order, one section each."""
    if not records:
        return "no runs to read"
    lines: list[str] = []
    ceilings = sorted({r.ceiling for r in records})
    windows = sorted({r.context_window for r in records})
    models = sorted({r.model for r in records})
    lines.append(
        f"model {', '.join(models)} | ceiling (num_predict) "
        f"{', '.join(str(c) for c in ceilings)} | context window (num_ctx) "
        f"{', '.join(str(w) for w in windows)}"
    )
    lines.append("")

    header = (
        f"{'fixture':<18} {'runs':>5} {'raised':>7} {'cut':>5} {'errors':>7} "
        f"{'obj med':>8} {'sec med':>8} {'sec max':>8}"
    )
    lines.append("Runs")
    lines.append("----")
    lines.append(header)
    lines.append("-" * len(header))
    for name in dict.fromkeys(r.fixture for r in records):
        group = [r for r in records if r.fixture == name]
        raised = sum(1 for r in group if r.capped)
        cut = sum(1 for r in group if r.capped_phases)
        errors = sum(1 for r in group if r.error)
        objects = [r.objects for r in group if not r.capped and not r.error]
        seconds = [r.seconds for r in group]
        lines.append(
            f"{name:<18} {len(group):>5} {f'{raised}/{len(group)}':>7} {cut:>5} "
            f"{errors:>7} "
            # An all-capped fixture retained nothing MEASURABLE, which is not
            # the same fact as one that measurably retained zero.
            f"{(str(int(statistics.median(objects))) if objects else '-'):>8} "
            f"{statistics.median(seconds):>8.1f} {max(seconds):>8.1f}"
        )

    failures = [r for r in records if r.error]
    if failures:
        lines.append("")
        lines.append("Non-cap failures (NOT counted as caps):")
        for record in failures:
            lines.append(f"  {record.fixture} run {record.run}: {record.error}")

    rows = phase_stats(records)
    lines.append("")
    lines.append("Q1. Which call runs away")
    lines.append("------------------------")
    q1_header = (
        f"{'phase':<32} {'calls':>6} {'raised':>7} {'swallow':>8} {'max gen':>8} "
        f"{'med gen':>8} {'max prompt':>11} {'worst s':>8}"
    )
    lines.append(q1_header)
    lines.append("-" * len(q1_header))
    for row in rows:
        lines.append(
            f"{row.phase:<32} {row.calls:>6} {row.raised:>7} {row.swallowed:>8} "
            f"{row.max_gen:>8} {row.median_gen:>8} {row.max_prompt:>11} "
            f"{row.worst_seconds:>8.1f}"
        )
    lines.append(
        "  raised = the cut-off escaped and failed the run; swallow = the reply "
        "was cut off and the phase's own handler ate it, so the run read as "
        "clean while returning a degraded result."
    )

    lines.append("")
    lines.append("Q2. Is a runaway separable from a legitimate reply?")
    lines.append("--------------------------------------------------")
    q2_header = (
        f"{'phase':<32} {'legit':>6} {'max legit gen':>14} {'med legit':>10} "
        f"{'runaways':>9} {'runaway gen':>28}"
    )
    lines.append(q2_header)
    lines.append("-" * len(q2_header))
    for row in rows:
        gens = ", ".join(str(g) for g in sorted(row.runaway_gens, reverse=True)[:4])
        lines.append(
            f"{row.phase:<32} {row.legit_calls:>6} {row.max_legit_gen:>14} "
            f"{row.median_gen:>10} {len(row.runaway_gens):>9} "
            f"{(gens or '-'):>28}"
        )
    ceiling = max(ceilings)
    floor = max((row.max_legit_gen for row in rows), default=0)
    worst_phase = next(
        (row.phase for row in rows if row.max_legit_gen == floor), "(none)"
    )
    lines.append("")
    lines.append(
        "  The question in one line: does a bound B exist with every legitimate "
        "reply strictly below B and every runaway at or above it?"
    )
    if not any(row.legit_calls for row in rows):
        # `floor` is 0 here because the sample is EMPTY, not because some
        # reply measured 0 tokens -- and a 0-based band would advertise the
        # whole range as available on no evidence at all.
        lines.append(
            "  No legitimate reply finished anywhere in this sweep, so there "
            "is NO measured floor and no available band: a bound cannot be "
            "read off an empty sample. See the Reading below."
        )
    else:
        lines.append(
            f"  Largest legitimate reply anywhere in this sweep: {floor} tokens, "
            f"in {worst_phase}. That number IS the floor any candidate bound "
            "must clear."
        )
        lines.append(
            f"  Every runaway generated the ceiling's worth ({ceiling}) by "
            "definition of the cap, so the runaway side is degenerate and the "
            f"available band is B in ({floor}, {ceiling}] -- width "
            f"{ceiling} - {floor} = {ceiling - floor} tokens."
        )

    lines.append("")
    lines.append("Q3. What would a candidate bound cost and save?")
    lines.append("----------------------------------------------")
    q3_header = (
        f"{'B':>6} {'false cuts':>11} {'of legit':>9} {'saved':>10} "
        f"{'of cut-off wall clock':>34}"
    )
    lines.append(q3_header)
    lines.append("-" * len(q3_header))
    costs = [bound_costs(records, bound) for bound in CANDIDATE_BOUNDS]
    for cost in costs:
        lines.append(
            f"{cost.bound:>6} {cost.false_cuts:>11} {cost.legit_calls:>9} "
            f"{cost.seconds_saved:>9.1f}s "
            f"{_ratio(cost.seconds_saved, cost.cut_run_seconds, 's'):>34}"
        )
    lines.append(
        f"  'of cut-off wall clock' is measured over the {costs[0].cut_runs} run(s) "
        "that had some reply cut off, using each runaway call's OWN observed "
        "tokens per second."
    )
    lines.append(
        "  Fail-fast SHORTENS the failure, it never rescues the run: a reply cut "
        "at B is as unusable as one cut at the ceiling."
    )
    for cost in costs:
        if cost.false_cuts:
            lines.append("")
            lines.append(f"  B = {cost.bound} would have FALSELY CUT:")
            for detail in cost.false_cut_detail[:8]:
                lines.append(f"    {detail}")
            if len(cost.false_cut_detail) > 8:
                lines.append(
                    f"    ... and {len(cost.false_cut_detail) - 8} more legitimate "
                    "call(s)"
                )
    return "\n".join(lines)


def render_verdict(records: list[RunRecord]) -> str:
    """State, in words, what the numbers decide about each half of #828.

    Two halves with two independent verdicts. The diagnostic half is answered
    by any sweep that saw a cut-off at all. The fail-fast half is answerable
    only if a bound separates, and it is REFUTED, in that word, when none does
    -- an honest negative here is worth more than a bound chosen to have
    something to ship.

    A sweep that finished ZERO replies answers the fail-fast half neither way:
    every candidate scores zero false cuts because nothing legitimate was
    exposed to it, so the verdict reads UNFALSIFIABLE, in that word, and
    blesses no bound. A zero-exposure result read as a clean one is the
    failure mode this repo has already shipped once."""
    if not records:
        return "no runs to read"
    lines = ["Reading", "-------"]
    rows = phase_stats(records)
    raised = [r for r in records if r.capped]
    cut = [r for r in records if r.capped_phases]
    phases = sorted({p for r in records for p in r.capped_phases})
    ceiling = max(r.ceiling for r in records)

    lines.append(
        f"{len(raised)} of {len(records)} runs FAILED on a cut-off reply "
        f"(the failure #828 reports)"
    )
    lines.append(
        f"{len(cut)} of {len(records)} runs had SOME reply cut off, at: "
        f"{', '.join(phases) or 'none'}"
    )
    silent = len(cut) - len(raised)
    if silent > 0:
        lines.append(
            f"  -> {silent} run(s) were cut off WITHOUT failing: the judge, the "
            "re-ask and the participant pass all swallow their own backend "
            "errors by contract, so those returned a silently degraded result "
            "that reads as clean."
        )

    if not cut:
        lines.append("")
        lines.append(
            "DIAGNOSTIC: nothing was cut off in this sweep. #828 did not "
            "reproduce here, so there is no call to name and no bound to "
            "choose. Check Q2's max legit gen against the ceiling before "
            "calling the band safe -- a sweep whose worst reply came within a "
            "few hundred tokens of the ceiling is one sampling draw from the "
            "failure."
        )
        return "\n".join(lines)

    culprits = [row for row in rows if row.raised or row.swallowed]
    lines.append("")
    lines.append("DIAGNOSTIC half:")
    for row in culprits:
        lines.append(
            f"  {row.phase}: {row.raised} raised, {row.swallowed} swallowed, "
            f"worst latency {row.worst_seconds:.1f}s"
        )
    lines.append(
        "  Recording `done_reason` per call names the runaway without a second "
        "run; that half is answerable on this evidence."
    )

    floor = max((row.max_legit_gen for row in rows), default=0)
    costs = [bound_costs(records, bound) for bound in CANDIDATE_BOUNDS]
    clean = [cost for cost in costs if cost.false_cuts == 0]
    legit_calls = sum(row.legit_calls for row in rows)
    lines.append("")
    lines.append("FAIL-FAST half:")
    lines.append(
        "  `OllamaClient.chat` sends stream=False, so the only lever is a "
        "lower num_predict."
    )
    # Zero exposure is not a pass. With no finished reply in the sweep, EVERY
    # candidate scores `false_cuts == 0` -- not because it cuts nothing
    # legitimate, but because there was no legitimate reply for it to cut --
    # and the `best = clean[0]` branch below would bless the most aggressive
    # bound on that emptiness, printing "cut nothing legitimate (0 of 0)".
    # `floor` is 0 for the same reason, which would advertise the whole band
    # (0, ceiling] as available. Refuse both readings here, in the one word
    # that says the measurement could not have come out the other way.
    if not legit_calls:
        lines.append(
            f"  UNFALSIFIABLE: this sweep finished ZERO replies, over "
            f"{sum(row.calls for row in rows)} call(s). No candidate bound was "
            "ever exposed to a legitimate reply, so none can be declared safe "
            "and none can be refuted: every B scores zero false cuts on an "
            "EMPTY sample, which is not the same result as cutting nothing. "
            "Q2's floor and band are 0-based for that same reason and must not "
            "be read as an available band. Re-run until at least one reply "
            "finishes before choosing any bound."
        )
        return "\n".join(lines)
    lines.append(
        f"  The bound must clear {floor} (the largest legitimate reply "
        f"measured) and sit below {ceiling}."
    )
    if not clean:
        lines.append(
            f"  REFUTED at these candidates: every B in "
            f"{', '.join(str(b) for b in CANDIDATE_BOUNDS)} falsely cuts at "
            "least one healthy reply, so no swept bound separates a runaway "
            "from a legitimate one. Lowering the ceiling to any of them buys a "
            "shorter failure by breaking runs that work today."
        )
        lines.append(
            f"  The smallest bound that could separate on THIS sample is "
            f"{floor + 1}; whether it is worth setting depends on how much of "
            f"the {ceiling}-token runaway it actually cuts, and on the fact "
            "that a sample max is not a distribution ceiling."
        )
        return "\n".join(lines)

    best = clean[0]
    lines.append(
        f"  B = {best.bound} cut nothing legitimate in this sweep "
        f"(0 of {best.legit_calls} finished replies reached it) and would have "
        f"saved {_ratio(best.seconds_saved, best.cut_run_seconds, 's')} of the "
        f"wall clock the {best.cut_runs} cut-off run(s) burned."
    )
    lines.append(
        f"  Caveat, not a footnote: {floor} is a SAMPLE maximum over "
        f"{sum(row.legit_calls for row in rows)} finished replies, not a "
        "distribution ceiling. A bound set just above it will falsely cut some "
        "rate of healthy replies that this sweep did not draw; the wider the "
        f"margin between {floor} and the chosen B, the smaller that rate."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


def load_results(path: Path) -> list[RunRecord]:
    records: list[RunRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(RunRecord(**json.loads(line)))
    return records


def _result_path(stamp: str, model: str, suffix: str) -> Path:
    """A stamped, model-tagged path under `results/`, and never a doc.

    "Never a doc" is a property of the NAME, not of a check: every name this
    builds starts with the literal `generation-runaway-`, and the only
    caller-supplied part is `model`, whose `:` and `/` are folded to `-`. So
    `README.md` and `report.md` -- the hand-written prose no sweep may clobber
    -- are unreachable here, and a stamping bug can only produce another
    `generation-runaway-` name inside `results/`.

    An earlier version raised on those two names. It could not fire, carried
    `# pragma: no cover` saying so, and the README advertised it as a
    protection: a guard that cannot run is not one, and documenting it as one
    is worse than having neither."""
    slug = model.replace(":", "-").replace("/", "-")
    return RESULTS_DIR / f"generation-runaway-{stamp}-{slug}{suffix}"


def write_results(
    records: list[RunRecord], report: str, stamp: str, model: str
) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _result_path(stamp, model, ".jsonl")
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    _result_path(stamp, model, ".md").write_text(report, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    """Prove the ledger and the bound arithmetic before any GPU second is spent.

    Every failure guarded here is a SILENT-SUCCESS failure -- the report still
    renders, with the wrong call named or the wrong bound blessed:

    1. a phase tag lagging its call by one blames the wrong phase, and #828's
       whole diagnostic half is a claim about which phase;
    2. a swallowed cap counted as a run failure, or dropped entirely, moves the
       runaway between two columns that lead to different fixes;
    3. a raised cap that did not set `capped` would report #828's own failure
       rate as zero;
    4. a bound sweep that missed a false cut would recommend a ceiling that
       breaks healthy runs, which is precisely the outcome the refutation
       criterion exists to catch.
    """
    failures: list[str] = []
    transport = _RecordingTransport()

    def _body(content: str, gen: int, reason: str) -> bytes:
        return json.dumps(
            {
                "message": {"content": content},
                "eval_count": gen,
                "prompt_eval_count": 1234,
                "done_reason": reason,
            }
        ).encode("utf-8")

    candidates = json.dumps(
        [
            {"type": "Concept", "title": "Alpha", "description": "A"},
            {"type": "Concept", "title": "Beta", "description": "B"},
        ]
    )

    def _scenario(scripted: list[bytes]) -> tuple[RunRecord, list[bytes]]:
        sent: list[bytes] = []

        def _fake(request: Any, timeout: float | None = None) -> Any:
            sent.append(request.data)
            return _ReplayResponse(scripted[min(len(sent) - 1, len(scripted) - 1)])

        real_urlopen = urllib.request.urlopen
        urllib.request.urlopen = _fake  # type: ignore[assignment]
        try:
            client = OllamaClient(
                model="fake",
                max_generation_tokens=MAX_GENERATION_TOKENS,
                context_window=CONTEXT_WINDOW,
                urlopen=transport,
            )
            fixture = Fixture(
                name="fake",
                title="T",
                text="line one\nline two",
                must_fire=(),
                must_stay_quiet=(),
            )
            records = run_fixture(fixture, client, transport, 1, "fake")
        finally:
            urllib.request.urlopen = real_urlopen
        return records[0], sent

    # Scenario 1 -- the JUDGE's first attempt is cut off and its retry answers.
    # `judge.select`'s D7 contract swallows the cut-off, so the run SUCCEEDS
    # with the ledger as the only witness; `capped` must NOT be set. The judge
    # gets two calls (`judge.JUDGE_ATTEMPTS`, #795), which also gives one phase
    # holding a runaway and a legitimate reply at once -- the case a ledger that
    # split its two lists by membership instead of by `done_reason` would get
    # wrong.
    judge_cut, sent = _scenario(
        [
            _body(candidates, 900, "stop"),  # extracting pass 1/2
            _body(candidates, 1100, "stop"),  # extracting pass 2/2
            _body("{", MAX_GENERATION_TOKENS, "length"),  # judge attempt 1, cut off
            _body('{"keep": ["Alpha", "Beta"]}', 300, "stop"),  # judge attempt 2
        ]
    )
    if len(judge_cut.calls) != 4:
        failures.append(f"expected 4 recorded calls, got {len(judge_cut.calls)}")
    else:
        # The phase-attribution check, stated positionally: a tag lagging its
        # call by one would still produce four plausible labels, and only the
        # exact call-to-phase mapping distinguishes that from a correct ledger.
        expected = [
            "extracting pass 1/2",
            "extracting pass 2/2",
            "judging",
            "judging",
        ]
        actual = [str(call["phase"]) for call in judge_cut.calls]
        if not all(a.startswith(e) for a, e in zip(actual, expected, strict=True)):
            failures.append(
                f"phase tags misattributed: {actual!r}, expected {expected!r}"
            )
        if [call["gen_tokens"] for call in judge_cut.calls] != [900, 1100, 8192, 300]:
            failures.append(
                "counters were not recorded off the responses in call order: "
                f"{[call['gen_tokens'] for call in judge_cut.calls]!r}"
            )
        if judge_cut.calls[0]["prompt_tokens"] != 1234:
            failures.append("prompt counters were not recorded off the response")
    if judge_cut.capped:
        failures.append("a SWALLOWED judge cut-off was reported as a run failure")
    if judge_cut.capped_phases != ["judging 2 candidates"]:
        failures.append(
            f"swallowed cut-off missing from capped_phases: {judge_cut.capped_phases!r}"
        )
    if raising_call_index(judge_cut) is not None:
        failures.append("a swallowed cut-off was attributed to a raising call")
    judge_rows = {row.phase: row for row in phase_stats([judge_cut])}
    judging = next(
        (row for phase, row in judge_rows.items() if phase.startswith("judging")), None
    )
    if judging is None or judging.swallowed != 1 or judging.raised != 0:
        failures.append(f"judge row is not 1 swallowed / 0 raised: {judging!r}")
    elif judging.max_legit_gen != 300 or judging.legit_calls != 1:
        failures.append(
            "the judge's legitimate retry was not separated from its runaway: "
            f"{judging.legit_calls} legit call(s), max legit {judging.max_legit_gen}"
        )
    if sent and b'"num_predict": 8192' not in sent[0]:
        failures.append("the shipped ceiling was not forwarded on the request")
    if sent and f'"num_ctx": {CONTEXT_WINDOW}'.encode() not in sent[0]:
        failures.append("the shipped context window was not forwarded on the request")

    # Scenario 2 -- EXTRACTION is cut off. `_extract_once` propagates
    # unswallowed, which is the only way #828's failure reaches a caller: both
    # `capped` and `capped_phases` must be set, and the raiser must be the
    # extracting call rather than an unattributed one.
    extract_cut, _ = _scenario([_body("[{", MAX_GENERATION_TOKENS, "length")])
    if not extract_cut.capped:
        failures.append("a RAISED extraction cut-off did not fail the run")
    if not any(p.startswith("extracting") for p in extract_cut.capped_phases):
        failures.append(
            f"raised cut-off attributed to {extract_cut.capped_phases!r}, "
            "expected an extracting phase"
        )
    if raising_call_index(extract_cut) != len(extract_cut.calls) - 1:
        failures.append("the raising call was not identified as the last call")
    extract_rows = phase_stats([extract_cut])
    if not any(row.raised == 1 and row.swallowed == 0 for row in extract_rows):
        failures.append(
            f"extraction row is not 1 raised / 0 swallowed: {extract_rows!r}"
        )

    # Scenario 3 -- the bound arithmetic, on a hand-built ledger whose numbers
    # are known exactly. A legit reply of 1500 tokens must count as a false cut
    # at B = 1024 and must NOT at B = 2048; the runaway's saving must come from
    # its OWN 8192 / 200.0 = 40.96 tok/s.
    synthetic = RunRecord(
        fixture="synthetic",
        run=1,
        chars=10,
        capped=True,
        capped_phases=["extracting pass 2/2"],
        error="",
        objects=0,
        seconds=215.0,
        model="fake",
        ceiling=8192,
        context_window=CONTEXT_WINDOW,
        calls=[
            {
                "phase": "extracting pass 1/2",
                "prompt_tokens": 200,
                "gen_tokens": 1500,
                "done_reason": "stop",
                "seconds": 15.0,
            },
            {
                "phase": "extracting pass 2/2",
                "prompt_tokens": 200,
                "gen_tokens": 8192,
                "done_reason": "length",
                "seconds": 200.0,
            },
        ],
    )
    tight = bound_costs([synthetic], 1024)
    if tight.false_cuts != 1:
        failures.append(
            f"B=1024 must falsely cut the 1500-token legit reply, got "
            f"{tight.false_cuts}"
        )
    loose = bound_costs([synthetic], 2048)
    if loose.false_cuts != 0:
        failures.append(f"B=2048 must cut nothing legitimate, got {loose.false_cuts}")
    expected_saved = round((8192 - 2048) / (8192 / 200.0), 1)
    if abs(loose.seconds_saved - expected_saved) > 0.05:
        failures.append(
            f"saving must use the runaway's own throughput: expected "
            f"{expected_saved}s, got {loose.seconds_saved}s"
        )
    if loose.cut_run_seconds != 215.0 or loose.cut_runs != 1:
        failures.append(
            f"cut-off wall clock must come from the run: got "
            f"{loose.cut_run_seconds}s over {loose.cut_runs} run(s)"
        )
    boundary = bound_costs([synthetic], 1500)
    if boundary.false_cuts != 1:
        failures.append("a reply that generated exactly B must count as a false cut")
    # The other half of the same boundary, which the false-cut check above
    # leaves unpinned: a RUNAWAY that generated exactly B saves nothing, since
    # a ceiling of B would have stopped it at the token it already stopped at.
    # Unpinned, a saving computed off `gen` rather than `gen - bound` would
    # credit this bound with the runaway's whole 200s wall clock and read as a
    # free win.
    runaway_boundary = bound_costs([synthetic], 8192)
    if runaway_boundary.seconds_saved != 0.0:
        failures.append(
            "a runaway that generated exactly B must save nothing, got "
            f"{runaway_boundary.seconds_saved}s"
        )

    # The verdict must SAY "REFUTED" when nothing separates. Built from a legit
    # reply above every candidate bound, so no swept B can be clean.
    unseparable = RunRecord(
        **{
            **asdict(synthetic),
            "calls": [
                {**synthetic.calls[0], "gen_tokens": 5000},
                synthetic.calls[1],
            ],
        }
    )
    if "REFUTED" not in render_verdict([unseparable]):
        failures.append("an overlapping distribution did not read as REFUTED")
    if "REFUTED" in render_verdict([synthetic]):
        failures.append("a separable distribution was reported as REFUTED")

    # A sweep with ZERO finished replies: every call was cut off, so every
    # candidate bound scores `false_cuts == 0` on an empty sample. Unguarded,
    # the verdict blesses the most aggressive bound with "cut nothing
    # legitimate (0 of 0 finished replies)" and Q2 offers the whole 0-based
    # band -- a clean verdict no measurement could have contradicted.
    starved = RunRecord(
        **{
            **asdict(synthetic),
            "calls": [
                {**synthetic.calls[0], "gen_tokens": 8192, "done_reason": "length"},
                synthetic.calls[1],
            ],
        }
    )
    starved_verdict = render_verdict([starved])
    if "UNFALSIFIABLE" not in starved_verdict:
        failures.append(
            "a sweep with zero finished replies did not read as UNFALSIFIABLE"
        )
    if "cut nothing legitimate" in starved_verdict:
        failures.append("a bound was blessed as clean against zero finished replies")
    if "REFUTED" in starved_verdict:
        failures.append(
            "an empty legitimate sample was reported as a refutation, which "
            "claims a measurement that was never taken"
        )
    # The band's own syntax, not the words around it: the zero-sample line
    # says "no available band" and a substring check on that phrase would
    # match the very message it is meant to demand.
    if "B in (" in render([starved]):
        failures.append("Q2 offered an available band with no legitimate reply in it")
    # Both reports must render off stored calls alone, which is what `--rescore`
    # depends on.
    for sample in ([synthetic], [judge_cut], [extract_cut]):
        if not render(sample):
            failures.append("render produced nothing for a stored sweep")

    if failures:
        print(f"self-test FAILED ({len(failures)} failure(s)):")
        for failure in failures:
            print(f"  FAIL: {failure}")
        return 1
    fixtures = build_fixtures()
    print(
        f"self-test OK ({len(fixtures)} fixture(s): "
        # chars AND bytes: #828 quotes 631 B for a source of 629 characters,
        # and a report that printed one number under the other's name would
        # look like a fixture mismatch it is not.
        + ", ".join(
            f"{f.name} {len(f.text)} chars / {len(f.text.encode('utf-8'))} B"
            for f in fixtures
        )
        + ")"
    )
    print(
        f"  shipped settings read from config: num_predict "
        f"{MAX_GENERATION_TOKENS}, num_ctx {CONTEXT_WINDOW}"
    )
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    doc = __doc__ or ""
    parser = argparse.ArgumentParser(description=doc.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument(
        "--host", default=None, help="Ollama host (default: config/env)"
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--fixture", action="append", help="measure only this fixture (repeatable)"
    )
    parser.add_argument(
        "--source",
        action="append",
        help=(
            "measure an off-repo source file instead of the built-in fixtures "
            "(repeatable); its text and objects are never stored"
        ),
    )
    parser.add_argument(
        "--source-title",
        action="append",
        help=(
            "title for the matching --source (repeatable, positional); "
            "defaults to the source's own first heading"
        ),
    )
    parser.add_argument(
        "--rescore", help="re-read a stored results JSONL, no model calls"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore:
        stored = load_results(Path(args.rescore))
        print(render(stored))
        print()
        print(render_verdict(stored))
        return 0

    if args.source:
        titles = args.source_title or []
        if titles and len(titles) != len(args.source):
            raise SystemExit(
                f"--source-title given {len(titles)} time(s) for "
                f"{len(args.source)} --source(s): pair them positionally or "
                "give none at all, never a partial list that silently "
                "mistitles a source"
            )
        paths = [Path(p) for p in args.source]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise SystemExit(f"no such source file(s): {', '.join(missing)}")
        fixtures = [
            fixture_from_path(path, titles[index] if titles else None)
            for index, path in enumerate(paths)
        ]
        if args.fixture:
            raise SystemExit(
                "--source replaces the built-in fixtures; --fixture selects "
                "among them, so passing both asks for two different sets"
            )
        for fixture in fixtures:
            print(
                f"  source {fixture.name}: title {fixture.title!r} "
                f"({len(fixture.text)} chars)",
                flush=True,
            )
        print(flush=True)
        return _measure(fixtures, args)

    fixtures = build_fixtures()
    if args.fixture:
        wanted = set(args.fixture)
        unknown = wanted - {f.name for f in fixtures}
        if unknown:
            raise SystemExit(f"unknown fixture(s): {', '.join(sorted(unknown))}")
        fixtures = [f for f in fixtures if f.name in wanted]
    if not fixtures:
        raise SystemExit("no fixtures selected")
    return _measure(fixtures, args)


def _measure(fixtures: list[Any], args: argparse.Namespace) -> int:
    """Sweep `fixtures` and render, shared by the built-in and `--source`
    paths so an off-repo source is measured by the same code, at the same
    shipped settings, as the committed ones."""
    transport = _RecordingTransport()
    llm = OllamaClient(
        model=args.model,
        host=args.host,
        timeout=args.timeout,
        max_generation_tokens=MAX_GENERATION_TOKENS,
        context_window=CONTEXT_WINDOW,
        urlopen=transport,
    )
    print(
        f"model {args.model} at {llm.resolved_host}, {args.runs} run(s) per "
        f"fixture, num_predict {MAX_GENERATION_TOKENS}, num_ctx {CONTEXT_WINDOW}, "
        f"timeout {args.timeout}s\n",
        flush=True,
    )

    records: list[RunRecord] = []
    for fixture in fixtures:
        print(f"  {fixture.name} ({len(fixture.text)} chars)", flush=True)
        records.extend(run_fixture(fixture, llm, transport, args.runs, args.model))

    report = f"{render(records)}\n\n{render_verdict(records)}"
    print()
    print(report)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    print(f"stored {write_results(records, report, stamp, args.model)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
