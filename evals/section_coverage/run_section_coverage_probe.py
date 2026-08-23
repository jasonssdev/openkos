"""Does a per-section coverage signal separate on real extraction? (#793)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the
REAL pipeline -- `openkos.extraction.concept.extract_concept_union` over
`openkos.llm.ollama.OllamaClient` -- across the two sources #793 compares,
and asks ONE question:

  When the coverage signal flags a section, is that a section the extraction
  actually lost, or is it flagging sections that produced objects perfectly
  well?

The first predicate measured, `quote`, answered the second: it is REFUTED
and `evals/section_coverage/README.md` carries the numbers. The second,
`overlap`, has since been swept over a threshold ladder on the same arms and
DOES separate -- at B in [0.20, 0.25], which is not the value its constant
still holds, on 17 runs of one model, with the window selected from two of
the three arms it is reported from. Read the README's *Predicate 2* before
quoting any of it.

## It imports the shipped function, and that is the point

`evals/judge_cold_start/` had to grow its own copy of production's parse
chain and then pin, in its self-test, that the copy still agreed -- because
the thing it measures is buried inside `select()`. This signal is a leaf
taking plain strings, so there is nothing to copy: the probe calls
`section_coverage` itself, whose `quote` predicate is shipped
`extraction/evidence.py` unchanged. A number measured here is a number about
the code that ships, with no drift to guard against.

## Predicates, and why rescoring is free

`--predicate NAME` selects the covering test; repeat it to score several
over ONE sweep and read them side by side. The comparison IS the finding --
a single column cannot tell an improvement from a relabelling.

Every stored run carries its full `objects` (type/title/description/body),
so any predicate can be scored against a stored sweep with ZERO model
calls:

    --rescore results/runs-....json --predicate quote --predicate overlap

`--rescore` therefore always RECOMPUTES from the stored objects rather than
reading the stored verdicts back. The stored verdicts are `quote`'s, and
the self-test pins that recomputing them reproduces the committed file
exactly -- which is what makes the refactor behind this seam
behaviour-preserving rather than merely plausible.

## The two published tables, and the commands that regenerate them

A number this repository cannot re-derive from its own harness is a number
nobody can check, so both of the README's `overlap` tables are modes of this
probe rather than throwaway scripts:

- `--overlap-threshold B`, repeatable, scores `overlap` at B. Each rung gets
  its OWN `overlap@B` column, because `overlap_predicate(B)` names the
  predicate after the value it used and `summarize` heads every column with
  that name -- a ladder reached by editing `OVERLAP_COVERED_FRACTION` would
  print eight columns all labelled `overlap`. The constant is not touched by
  any of this and is still the shipped default.
- `--ablate`, with `--rescore`, is the UNDER-FIRE arm: each stored run is cut
  down to `Fixture.reported_objects` -- the three objects the reported 0.2.8
  run produced -- and scored, reporting both the uncovered share and whether
  the run named BOTH sections the issue says were lost. The loss is
  constructed by deletion, so nothing here grades a run against what it
  should have found.

Both are `--rescore` paths: zero model calls, milliseconds, no GPU.

## What would make a candidate predicate ship, and what would kill it

Stated here BEFORE any new number exists, because a criterion written after
the measurement is a criterion fitted to it:

- The reported #793 failure -- `helios-overview` with `## Storage` and
  `## Components` lost -- must score HIGH uncovered.
- Healthy runs on ORDINARY sources must score LOW. Especially discursive
  meeting transcripts, which are the corpus openkos is for and are where
  `quote` scored 98.0%/31.3%/97.6% against the defect's 62.0%.
- A candidate that cannot put the first above the second is REFUTED exactly
  as `quote` was. Not "needs tuning": `quote` inverted, and a signal whose
  distributions cross has no threshold to tune to.

The probe prints both halves side by side and computes the reported
failure's score under each predicate, so that comparison can be read
without arithmetic.

## What is automated, and what is not

The **over-fire** half is fully mechanical, and it is the half that can
condemn the design:

- Every section listed in a fixture's `must_stay_quiet` produced objects in
  the reported run. If the signal flags one, it is calling a section
  uncovered that the extraction covered -- a false positive on a CORRECT
  extraction, on a marker that is not retryable debt.
- The whole `kickoff` fixture is that check at source scale: eight objects
  came out of it and the issue names no lost section, so any flag there is
  over-firing.

The **under-fire** half is NOT automated, deliberately. Whether a given run
lost `## Storage` is a fact about that run's objects, and extraction is
stochastic -- #793 says so itself. A run where nothing was lost SHOULD flag
nothing, and scoring that as a miss would punish the signal for the model
behaving. So the probe prints every run's objects beside its flags and
leaves that reading to a human, rather than inventing a second heuristic to
decide what the first heuristic should have found.

Two verdicts it does compute, because both are failures no reading can
rescue:

- `VACUOUS` -- every checkable section flagged in every run. A signal that
  fires everywhere separates nothing.
- `BLIND` -- no section flagged in any run, on the very source the issue was
  filed about. Read no other line of the report.

## The regime the committed fixtures do not cover

Both fixtures here are small and four-sectioned. The real corpus behind the
0.2.8 E2E is not: its three meeting transcripts carry **44, 41 and 9**
headings across 53 KB, 55 KB and 9.7 KB. A source with 44 sections and at
most 24 retained candidates cannot possibly cover every section, so the
question there is not whether the signal is right but whether it is
USEFUL -- a report naming thirty-odd sections is a wall of text, not a
finding.

`--source PATH --source-title TITLE` measures exactly that, on a real file.
Those transcripts carry real names and addresses and are NOT committed, on
the same footing as the gitignored AMI corpus `evals/decision_extraction/`
reads. Point the flag at your own.

```
uv run python -u evals/section_coverage/run_section_coverage_probe.py --self-test
uv run python -u evals/section_coverage/run_section_coverage_probe.py --runs 5
uv run python -u evals/section_coverage/run_section_coverage_probe.py --runs 3 \
    --source ~/corpus/transcript.md --source-title "Some Meeting"
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --predicate all
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --overlap-threshold 0.15 --overlap-threshold 0.20 --overlap-threshold 0.25
uv run python -u evals/section_coverage/run_section_coverage_probe.py \
    --rescore evals/section_coverage/results/runs-20260821T233809Z-qwen3-8b.json \
    --ablate --overlap-threshold 0.15 --overlap-threshold 0.20
```

`--self-test`, `--rescore`, `--overlap-threshold` and `--ablate` make no model
calls and need no Ollama.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Final

_REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))
if str(pathlib.Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import section_coverage as coverage  # noqa: E402
from section_fixtures import Fixture, build_fixtures  # noqa: E402

from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

_MAX_GENERATION_TOKENS: Final = 8_192
RESULTS_DIR: Final = pathlib.Path(__file__).resolve().parent / "results"

COMMITTED_RUNS: Final = RESULTS_DIR / "runs-20260821T233809Z-qwen3-8b.json"
"""The sweep the README's first three rows are read from.

The self-test rescores it under `quote` and requires the recomputed
verdicts to equal the stored ones exactly. That equivalence is the entire
safety net under the predicate seam: `quote` is supposed to be the shipped
behaviour moved, not changed, and nothing else in this repo would notice if
it drifted.
"""

ALL_PREDICATES: Final = "all"
"""`--predicate all`, expanding to every registered predicate in report
order. Spelled as a name rather than as a separate flag so the common case
-- score everything over one stored sweep -- is one token, and so adding a
third predicate needs no change at any call site."""


@dataclass
class RunRecord:
    """One extraction of one fixture, with what the signal said about it.

    The flat verdict fields (`uncovered`, `checkable`, `uncovered_chars`,
    `checkable_chars`) are `quote`'s, and the stored schema is unchanged
    from the committed sweep on purpose. They are NOT the report's input:
    `rescore` recomputes every predicate's verdicts, `quote` included, from
    `objects`. Keeping them serves two things -- an older file still reads,
    and the self-test has a committed value to prove the recomputation
    against.
    """

    fixture: str
    run: int
    model: str
    error: str | None
    latency_s: float
    objects: list[dict[str, str]] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    checkable: list[str] = field(default_factory=list)
    judge_status: str = ""
    uncovered_chars: int = 0
    checkable_chars: int = 0


def resolve_predicates(names: list[str]) -> tuple[coverage.CoveragePredicate, ...]:
    """The predicates `names` selects, in registry order, deduplicated.

    Registry order rather than the order they were typed, so `--predicate
    overlap --predicate quote` and `--predicate all` render identical
    tables: the baseline column is always the leftmost one, and a reader
    comparing two runs of this probe is never comparing transposed
    columns.

    Raises `KeyError` on an unknown name rather than silently dropping it.
    A typo that quietly scored one predicate instead of two would print a
    table that looks complete.
    """
    wanted = set(names)
    if ALL_PREDICATES in wanted:
        wanted.discard(ALL_PREDICATES)
        wanted.update(coverage.PREDICATES)
    unknown = sorted(wanted - set(coverage.PREDICATES))
    if unknown:
        raise KeyError(
            f"unknown predicate(s) {unknown}; known: "
            f"{sorted(coverage.PREDICATES)} plus {ALL_PREDICATES!r}"
        )
    return tuple(p for name, p in coverage.PREDICATES.items() if name in wanted)


def select_predicates(
    names: list[str] | None,
    thresholds: list[float] | None,
    min_word_gates: list[int] | None = None,
) -> tuple[coverage.CoveragePredicate, ...]:
    """The registry selections, then one `overlap` column per swept
    threshold -- a whole ladder in ONE invocation.

    A ladder has to be one process, not one process per rung. The README
    publishes a threshold table, and a table assembled by hand from eight
    separate runs is a table nobody can regenerate; that is the defect this
    function exists to close.

    Registry columns first and swept columns after, in the order the
    thresholds were typed, so a ladder reads low-to-high down the table
    beside the baseline it is being compared against. `resolve_predicates`
    normalises the registry half to registry order for the reason it
    documents; the swept half deliberately does NOT sort, because the
    reader's chosen ladder order is information.

    Deduplicated BY NAME, which is how `--overlap-threshold 0.5` alongside
    `--predicate overlap` resolves to one column rather than two identical
    ones: the factory names the default `overlap`, so the two genuinely are
    the same predicate.

    With thresholds given and no `--predicate`, only the swept columns are
    scored. Defaulting to `quote` there would put the refuted baseline
    beside a ladder that says nothing about it, and a reader who wants both
    asks for both.

    Two swept constants make the ladder a CROSS PRODUCT, and it is built as
    one deliberately. `overlap` has two knobs -- the covering threshold and
    `OVERLAP_MIN_CONTENT_WORDS`, the gate deciding which sections are
    scorable at all -- and every number committed in this directory was
    measured at the second one's 4 without ever varying it. Sweeping them
    independently would answer neither question: the gate changes the
    DENOMINATOR, so a threshold's share is not comparable across two gates,
    and a table pairing each threshold with one gate would hide exactly that
    interaction. Gate-major order keeps each gate's whole threshold ladder
    together, which is how a reader compares two denominators.
    """
    wanted = list(dict.fromkeys(thresholds or ()))
    gates = list(dict.fromkeys(min_word_gates or ()))
    if names:
        chosen = list(resolve_predicates(names))
    elif wanted or gates:
        chosen = []
    else:
        chosen = [coverage.QUOTE]
    seen = {predicate.name for predicate in chosen}
    # Only when something was ACTUALLY swept. Falling back to both defaults
    # unconditionally would append an `overlap` column to a bare invocation,
    # which asked for nothing and must keep scoring the baseline alone.
    if not wanted and not gates:
        return tuple(chosen)
    for gate in gates or [coverage.OVERLAP_MIN_CONTENT_WORDS]:
        for threshold in wanted or [coverage.OVERLAP_COVERED_FRACTION]:
            predicate = coverage.overlap_predicate(threshold, gate)
            if predicate.name in seen:
                continue
            seen.add(predicate.name)
            chosen.append(predicate)
    return tuple(chosen)


def run_share(record: dict[str, Any]) -> float | None:
    """One stored run's uncovered share, via the leaf's own arithmetic, or
    `None` when the run predates the char accounting.

    `None` rather than `0.0`, and rather than a `KeyError`. Runs stored
    before those fields existed genuinely have no share, and 0.0 is not a
    neutral stand-in here -- it is the value a PERFECTLY covered run gets,
    so defaulting would render the oldest results as the best ones. The
    report prints "not recorded" for them instead.
    """
    if not record["uncovered"]:
        # Nothing uncovered is a share of zero whatever the section weights
        # are, so this is exact rather than a default -- and it is what lets
        # runs stored before the char accounting existed still report their
        # share, since every one of them flagged nothing.
        return 0.0
    if "uncovered_chars" not in record or "checkable_chars" not in record:
        return None
    return coverage.CoverageReport(
        uncovered=tuple(record["uncovered"]),
        uncovered_chars=record["uncovered_chars"],
        checkable_chars=record["checkable_chars"],
    ).uncovered_share


def render_shares(shares: list[float | None]) -> str:
    """Per-run shares for the report, naming the unrecorded ones."""
    return ", ".join("not recorded" if v is None else f"{v:.1%}" for v in shares)


def stores_results(source: pathlib.Path | None) -> bool:
    """Whether this invocation may write its runs into `results/`.

    Only the committed fixtures may. A `--source` file is somebody's real
    corpus -- the flag exists precisely because those transcripts carry
    names and addresses and are not committed -- and its extracted objects
    carry that content onward. Writing them into a tracked directory would
    launder private text into version control by default, which is not a
    hypothetical: it happened once here, caught before any push.

    A named predicate rather than an `if` inside `main`, so the self-test
    can fail on it.
    """
    return source is None


def object_texts(objects: list[dict[str, str]]) -> list[str]:
    """The text each object will BE WRITTEN with, in object order.

    `body` when it has one, `description` otherwise -- the exact fallback
    `ExtractionResult.body` documents and `_unevidenced_titles` reads, so
    the probe asks the coverage question about the same strings production
    will.
    """
    return [
        (obj.get("body") or "").strip() or obj.get("description", "") for obj in objects
    ]


def checkable_headings(
    fixture: Fixture, predicate: coverage.CoveragePredicate
) -> list[str]:
    """The headings this predicate's gate admits, one entry per SECTION.

    Per section rather than per distinct heading, because `evaluate` divides
    by `checkable.count(section)` to rate a repeated `## Notes`. Computed
    from the predicate's own gate, never from `is_quotable`: two predicates
    do not admit the same sections, and that difference is exactly what the
    report has to show rather than average away.
    """
    return [
        section.heading
        for section in coverage.split_sections(fixture.text)
        if predicate.checkable(section.body)
    ]


def rescore(
    record: dict[str, Any], fixture: Fixture, predicate: coverage.CoveragePredicate
) -> dict[str, Any]:
    """One stored run's verdicts recomputed under `predicate`, from its
    stored objects alone. NO model call, for any predicate.

    This is why the stored sweep carries whole objects rather than only the
    verdicts derived from them: a sweep costs minutes of GPU and can be
    scored by every future predicate for free, so a candidate is never
    rejected because re-measuring it was expensive.

    An errored run is returned untouched -- `evaluate` excludes it, and
    inventing verdicts for a run that never produced objects would render a
    backend failure as a clean run that found nothing.

    A run stored WITHOUT an `objects` key is converted into an errored one.
    It predates this file's storage schema, nothing can be recomputed from
    it, and reading its stored `quote` verdicts back instead would put one
    predicate's numbers in another predicate's column -- the single failure
    the whole seam exists to make impossible. An empty `objects` list is a
    different thing entirely and is scored normally: a run that produced no
    objects covers nothing, which is a real reading.
    """
    if record["error"] is not None:
        return record
    if "objects" not in record:
        return {
            **record,
            "error": "stored without objects: nothing to rescore from",
        }
    report = coverage.coverage_report(
        object_texts(record["objects"]), fixture.text, predicate
    )
    return {
        **record,
        "uncovered": list(report.uncovered),
        "uncovered_chars": report.uncovered_chars,
        "checkable_chars": report.checkable_chars,
        "checkable": checkable_headings(fixture, predicate),
    }


def rescored_runs(
    records: list[dict[str, Any]],
    fixture: Fixture,
    predicate: coverage.CoveragePredicate,
) -> list[dict[str, Any]]:
    """`records` belonging to `fixture`, each rescored under `predicate`."""
    return [
        rescore(record, fixture, predicate)
        for record in records
        if record["fixture"] == fixture.name
    ]


def reported_failure_share(
    fixture: Fixture, predicate: coverage.CoveragePredicate
) -> float | None:
    """What the run #793 REPORTS would score under `predicate`, or `None`
    for a fixture that records no lost section.

    A RECONSTRUCTION, and the word is load-bearing. This repo does not hold
    the 0.2.8 run's object texts, only the adjudicated outcome recorded in
    `Fixture.must_fire`: `## Storage` and `## Components` produced nothing,
    every other section produced objects. So the reconstruction hands each
    surviving section its OWN body as the object text -- perfect coverage
    for the half that was covered -- and lets the predicate score the rest.

    That makes this the FLOOR of what the reported failure scores, not a
    measurement of it: a predicate that also over-fires on a surviving
    section scores higher here in reality. The floor is the useful end,
    because the criterion in the module docstring asks this number to be
    HIGH, and a floor that is already low refutes the candidate outright.

    Predicate-dependent through the gate, which is the whole reason it is
    not a constant: `quote` and `overlap` admit different sections, so the
    same lost text is a different share of a different denominator. Under
    `quote` it is 276/445 = 62.0%, the figure the README publishes.
    """
    if not fixture.must_fire:
        return None
    covered = [
        section.body
        for section in coverage.split_sections(fixture.text)
        if section.heading not in fixture.must_fire
    ]
    return coverage.coverage_report(covered, fixture.text, predicate).uncovered_share


def object_identity(obj: dict[str, str]) -> str:
    """One object as `type: title` -- the shape `Fixture.reported_objects`
    names the 0.2.8 objects in, and the shape the report already prints.

    Identity by type AND title, not title alone: `Concept: Marta Ruiz` and
    `Person: Marta Ruiz` are different objects, and a run that produced the
    wrong type of the right title is not the run the issue reports.
    """
    return f"{obj['type']}: {obj['title']}"


def ablate(record: dict[str, Any], keep: tuple[str, ...]) -> dict[str, Any]:
    """`record` with every object outside `keep` removed.

    The under-fire reconstruction that does NOT hand a section its own body
    back. `reported_failure_share` does exactly that, and says so: it is a
    FLOOR computed from text the model never wrote. This is the same
    reported failure built from a real run's real object texts instead --
    keep the three objects the 0.2.8 run produced, drop the rest, and the
    two sections the issue says produced nothing have nothing behind them.

    Ablation, not judgment. Nothing here grades a run against what it should
    have found -- the probe's whole under-fire discipline is that it does
    not -- because the loss is CONSTRUCTED by deletion and the outcome is
    therefore known before the predicate is asked.

    An errored run is returned untouched, on the same terms as `rescore`:
    it has no objects to ablate, and inventing a total loss for a backend
    failure would put the loudest possible number in the arm this is
    reported from.
    """
    if record["error"] is not None:
        return record
    return {
        **record,
        "objects": [
            obj for obj in record.get("objects", []) if object_identity(obj) in keep
        ],
    }


@dataclass(frozen=True)
class AblationRow:
    """One predicate's row of the under-fire ablation table.

    `named_both` is the column to read first, and it is deliberately not a
    share. The share says how LOUD the signal is; `named_both` says whether
    it is pointing at the two sections the issue reports as lost. A ladder
    point can be loud and wrong -- the README's 0.15 rung scores a third of
    the source while naming both lost sections in none of five runs -- and a
    table carrying only shares would read that as a smaller version of the
    right answer.
    """

    predicate: str
    ablated: tuple[float | None, ...]
    named_both: int
    ok_runs: int
    full: tuple[float | None, ...]


def ablation_rows(
    records: list[dict[str, Any]],
    fixture: Fixture,
    predicates: tuple[coverage.CoveragePredicate, ...],
) -> tuple[AblationRow, ...]:
    """Each predicate scored twice over `fixture`'s stored runs: once with
    the objects ablated to `fixture.reported_objects`, once whole.

    Both columns from ONE stored sweep and no model call, which is the
    point: the ablated share alone cannot be read. 62% uncovered is a
    finding only beside the same runs scoring 0% unablated, or the number is
    just a property of the source.
    """
    ok = [
        record
        for record in records
        if record["fixture"] == fixture.name and record["error"] is None
    ]
    rows: list[AblationRow] = []
    for predicate in predicates:
        ablated: list[float | None] = []
        full: list[float | None] = []
        named_both = 0
        for record in ok:
            cut = rescore(ablate(record, fixture.reported_objects), fixture, predicate)
            whole = rescore(record, fixture, predicate)
            # `run_share`, never `0.0`, for a run whose char accounting is
            # missing: 0.0 is the value a PERFECTLY covered run gets, so
            # defaulting would print the worst-recorded run as the best one.
            # `render_shares` names the gap instead.
            ablated.append(run_share(cut))
            full.append(run_share(whole))
            if set(fixture.must_fire) <= set(cut["uncovered"]):
                named_both += 1
        rows.append(
            AblationRow(
                predicate=predicate.name,
                ablated=tuple(ablated),
                named_both=named_both,
                ok_runs=len(ok),
                full=tuple(full),
            )
        )
    return tuple(rows)


def ablation_table(
    records: list[dict[str, Any]],
    fixtures: tuple[Fixture, ...],
    predicates: tuple[coverage.CoveragePredicate, ...],
) -> str:
    """The README's under-fire arm, regenerated from a stored sweep.

    Only fixtures carrying BOTH an adjudicated `must_fire` and the
    `reported_objects` that survived it can be ablated. The others are named
    and skipped rather than silently absent: a table that quietly dropped an
    arm would read as an arm that came back clean.
    """
    lines: list[str] = ["", "=" * 72, "UNDER-FIRE ABLATION (#793)", "=" * 72, ""]
    lines.append(_CRITERION)
    lines.append("")
    lines.append(
        "Each stored healthy run is cut down to the objects the reported 0.2.8 "
        "run produced, then scored. The loss is CONSTRUCTED by deletion, so "
        "the outcome is known before any predicate is asked -- this grades no "
        "run against what it should have found."
    )
    for fixture in fixtures:
        lines.append("")
        lines.append(f"## {fixture.name}")
        if not fixture.must_fire or not fixture.reported_objects:
            lines.append("")
            lines.append(
                "   NOT ABLATABLE: needs both an adjudicated must-fire section "
                "and the objects the reported run produced; this fixture "
                f"records must_fire={list(fixture.must_fire)} and "
                f"reported_objects={list(fixture.reported_objects)}"
            )
            continue
        lines.append("")
        lines.append(f"   kept objects: {', '.join(fixture.reported_objects)}")
        lines.append(f"   must fire: {', '.join(fixture.must_fire)}")
        lines.append("")
        width = max(len("predicate"), *(len(p.name) for p in predicates))
        lines.append(
            f"   {'predicate':<{width}}  {'names BOTH':>10}  "
            "ablated share per run / full healthy run"
        )
        for row in ablation_rows(records, fixture, predicates):
            share = render_shares(list(row.ablated)) or "(no ok runs)"
            whole = render_shares(list(row.full)) or "(no ok runs)"
            lines.append(
                f"   {row.predicate:<{width}}  "
                f"{f'{row.named_both}/{row.ok_runs}':>10}  {share}"
            )
            lines.append(f"   {'':<{width}}  {'':>10}  full: {whole}")
    lines.append("")
    lines.append(
        "Read `names BOTH` before the shares. A rung can be loud and still "
        "point somewhere else, and only the naming column tells the two apart."
    )
    return "\n".join(lines)


def usable_runs(
    records: list[dict[str, Any]], fixture_name: str
) -> list[dict[str, Any]]:
    """The runs of `fixture_name` both leave-one-out functions agree are
    scorable, in stored order.

    ONE predicate, shared, because the two had drifted: the table's NO DATA
    guard asked `record.get("error") is None`, which a record carrying no
    `"error"` key at all satisfies, and the tally then subscripted
    `record["fixture"]` and raised `KeyError` on that same record -- a raw
    traceback from inside the tally where the guard had been written to
    print an honest NO DATA. A stored file predating the current record
    shape is exactly how that arrives.

    Every key the tally reads is required here, so a record that reaches the
    tally cannot fail in it.
    """
    return [
        record
        for record in records
        if record.get("error") is None
        and record.get("fixture") == fixture_name
        and "objects" in record
    ]


@dataclass(frozen=True)
class LeaveOneOutTally:
    """One predicate's leave-one-out result over every run of one source.

    `blind` is the column this arm exists to produce, and it is the only one
    that is bad news: a section that WAS covered, whose quoting objects were
    then deleted, and which the predicate still calls covered. That is a
    real loss the signal would not have reported.

    `trials` counts only rows carrying `covered_before`. A section the
    predicate already flagged cannot be made more flagged by deleting
    objects, so including it would pad the denominator with rows that were
    decided before the arm ran.
    """

    predicate: str
    trials: int
    named: int
    blind: int
    skipped_uncovered: int
    ok_runs: int
    excluded_unscorable: int = 0
    """Sections the predicate could not check, summed over the runs."""
    excluded_unquoted: int = 0
    """Sections no object quoted, so no loss could be constructed. The
    dominant exclusion on a discursive source and the reason its `trials`
    is small; without it the denominator cannot be audited."""
    excluded_total_removal: int = 0
    """Sections every object quoted, where the trial would be vacuous."""
    refused: str | None = None
    """Why this predicate cannot be scored by this arm at all, or `None`.

    Carried as a value rather than raised past the table, because a
    refusal has to be PRINTED. A predicate silently dropped from the rows
    reads as one that was measured and came back clean, which is the exact
    misreading `ablation_table` already refuses for a non-ablatable
    fixture."""

    @property
    def hit_rate(self) -> float | None:
        """`named / trials`, or `None` when nothing was scorable -- never
        `0.0`, which is the value a signal that saw NOTHING would also get
        and is exactly the confusion `render_shares` exists to prevent."""
        if not self.trials:
            return None
        return self.named / self.trials


def leave_one_out_tallies(
    records: list[dict[str, Any]],
    fixture: Fixture,
    predicates: tuple[coverage.CoveragePredicate, ...],
) -> tuple[LeaveOneOutTally, ...]:
    """Every ok run of `fixture` put through `coverage.leave_one_section_out`,
    tallied per predicate.

    Costs no model call: the trials are built by DELETING objects a stored
    or just-completed run already produced. That is what lets this arm run
    over a private transcript without persisting anything from it -- the
    tally is counts, and counts are publishable where objects are not.
    """
    ok = usable_runs(records, fixture.name)
    tallies: list[LeaveOneOutTally] = []
    for predicate in predicates:
        if predicate.covers_by_quoting:
            tallies.append(
                LeaveOneOutTally(
                    predicate=predicate.name,
                    trials=0,
                    named=0,
                    blind=0,
                    skipped_uncovered=0,
                    ok_runs=len(ok),
                    refused=(
                        "covers by the same verbatim-quoting rule this arm "
                        "attributes by: every trial is a hit by construction"
                    ),
                )
            )
            continue
        trials = named = blind = skipped = 0
        unscorable = unquoted = total_removal = 0
        for record in ok:
            texts = object_texts(record.get("objects", []))
            scan = coverage.leave_one_out_report(texts, fixture.text, predicate)
            unscorable += scan.unscorable
            unquoted += scan.unquoted
            total_removal += scan.total_removal
            for row in scan.rows:
                if not row.covered_before:
                    skipped += 1
                    continue
                trials += 1
                if row.named_after:
                    named += 1
                else:
                    blind += 1
        tallies.append(
            LeaveOneOutTally(
                predicate=predicate.name,
                trials=trials,
                named=named,
                blind=blind,
                skipped_uncovered=skipped,
                ok_runs=len(ok),
                excluded_unscorable=unscorable,
                excluded_unquoted=unquoted,
                excluded_total_removal=total_removal,
            )
        )
    return tuple(tallies)


def leave_one_out_table(
    records: list[dict[str, Any]],
    fixtures: tuple[Fixture, ...],
    predicates: tuple[coverage.CoveragePredicate, ...],
) -> str:
    """The under-fire arm that reaches a discursive source.

    Reported beside `--ablate` rather than replacing it. The two construct
    different losses and neither subsumes the other: `--ablate` reconstructs
    ONE reported failure exactly, with the objects that real run produced;
    this one builds many small losses from any run's own objects and can
    therefore be pointed at a transcript nobody has adjudicated.
    """
    lines: list[str] = [
        "",
        "=" * 72,
        "LEAVE-ONE-SECTION-OUT (#793)",
        "=" * 72,
        "",
        _CRITERION,
        "",
        "Per section, the objects that QUOTE it verbatim are deleted and the "
        "predicate is asked again. Attribution is `evidence_line`; coverage "
        "is the predicate under test -- two different mechanisms, so a row "
        "is not true by construction. Sections nothing quotes, sections the "
        "predicate cannot check, and sections whose deletion would empty the "
        "object list are not scored, and none of those is a hit declined.",
        "",
        "Read BLIND first. It counts a section that was covered, lost every "
        "object quoting it, and was STILL called covered -- a real loss this "
        "signal would not report.",
    ]
    for fixture in fixtures:
        lines.append("")
        lines.append(f"## {fixture.name}")
        # PER FIXTURE, not table-wide. A guard spanning the whole table
        # stays silent whenever ANY other fixture has runs, so a fixture
        # with none would print trials 0 NAMED 0 BLIND 0 beside a populated
        # one -- a signal that was never asked reading exactly like one that
        # was asked and found nothing. `hit_rate` refuses that confusion one
        # level down by answering None rather than 0.0; this is the report
        # saying it out loud, and it has to say it where the rows are.
        if not usable_runs(records, fixture.name):
            lines.append("")
            lines.append("   NO DATA -- no ok run to build a trial from.")
            continue
        lines.append("")
        width = max(len("predicate"), *(len(p.name) for p in predicates))
        lines.append(
            f"   {'predicate':<{width}}  {'trials':>6}  {'NAMED':>6}  "
            f"{'BLIND':>6}  {'hit rate':>8}  (skipped: already uncovered)"
        )
        for tally in leave_one_out_tallies(records, fixture, predicates):
            if tally.refused is not None:
                lines.append(
                    f"   {tally.predicate:<{width}}  NOT SCORABLE -- {tally.refused}"
                )
                continue
            rate = tally.hit_rate
            shown = "     n/a" if rate is None else f"{rate * 100:7.1f}%"
            lines.append(
                f"   {tally.predicate:<{width}}  {tally.trials:>6}  "
                f"{tally.named:>6}  {tally.blind:>6}  {shown}  "
                f"({tally.skipped_uncovered} over {tally.ok_runs} ok run(s))"
            )
            # The denominator, spelled out. `trials` alone reads the same
            # whether one section was excluded or forty, and three of the
            # four exclusions produce no row to notice.
            lines.append(
                f"   {'':<{width}}  excluded: {tally.excluded_unquoted} "
                f"unquoted, {tally.excluded_unscorable} unscorable, "
                f"{tally.excluded_total_removal} total-removal"
            )
    lines.append("")
    lines.append(
        "A hit rate of 100% is NOT a validated signal on its own: it says "
        "the predicate notices a loss it was pointed at, not that it stays "
        "quiet on a healthy run. Read it against the over-fire arm, which is "
        "the half that fails when a threshold is too loud."
    )
    return "\n".join(lines)


def run_once(fixture: Fixture, run: int, llm: Any, model: str) -> RunRecord:
    """Extract once and record what `quote` reports about the result.

    Only `quote`'s verdicts are stored, and that is not a preference for it:
    the stored `objects` are what every predicate is scored from, so the
    verdict fields are a cross-check on the recomputation rather than the
    report's input. Storing one predicate's numbers keeps the file's schema
    identical to the committed sweep, which is what lets the self-test
    compare the two.
    """
    checkable = checkable_headings(fixture, coverage.QUOTE)
    started = time.monotonic()
    try:
        outcome = concept_mod.extract_concept_union(
            fixture.text, source_title=fixture.title, llm=llm
        )
    except Exception as exc:  # a backend failure is a run outcome, not a crash
        return RunRecord(
            fixture=fixture.name,
            run=run,
            model=model,
            error=f"{type(exc).__name__}: {exc}",
            latency_s=round(time.monotonic() - started, 2),
            checkable=checkable,
        )
    objects = [
        {
            "type": result.type,
            "title": result.title,
            "description": result.description,
            "body": result.body,
        }
        for result in outcome.objects
    ]
    report = coverage.coverage_report(object_texts(objects), fixture.text)
    return RunRecord(
        fixture=fixture.name,
        run=run,
        model=model,
        error=None,
        latency_s=round(time.monotonic() - started, 2),
        objects=objects,
        uncovered=list(report.uncovered),
        checkable=checkable,
        judge_status=outcome.report.judge_status,
        uncovered_chars=report.uncovered_chars,
        checkable_chars=report.checkable_chars,
    )


@dataclass(frozen=True)
class FixtureVerdict:
    """What the runs of one fixture say about the signal."""

    fixture: str
    ok_runs: int
    verdict: str
    reasons: tuple[str, ...]
    flagged_rate: tuple[tuple[str, float], ...]


def evaluate(fixture: Fixture, records: list[dict[str, Any]]) -> FixtureVerdict:
    """Read the mechanical verdicts off one fixture's runs.

    `records` must already be rescored under ONE predicate (`rescored_runs`)
    -- this function reads `uncovered` and `checkable` and has no way to
    tell which predicate produced them, which is why the caller labels the
    column.

    `OVER-FIRES` is the condemning one and it takes precedence over both
    `VACUOUS` and `BLIND`: a signal that flags a section which produced
    objects is wrong in a way that no amount of separation elsewhere
    redeems, and reporting it as merely vacuous would understate it.
    """
    ok = [r for r in records if r["error"] is None and r["fixture"] == fixture.name]
    if not ok:
        return FixtureVerdict(fixture.name, 0, "NO DATA", ("every run errored",), ())

    checkable = tuple(ok[0]["checkable"])
    # COUNT occurrences rather than test membership. Both lists preserve one
    # entry per SECTION, and headings are not unique -- a transcript with a
    # `## Notes` block per agenda item repeats one. Membership would ask
    # "was any section with this heading flagged" and then average that over
    # runs as though it were one section, collapsing two independent
    # outcomes into a shared rate. Counting gives the fraction of THAT
    # heading's sections that were flagged, which is exact for duplicates
    # and reduces to the old expression when a heading appears once.
    #
    # `section_coverage.CoverageReport` was redesigned to remove exactly
    # this collision; leaving it standing one file over would have put the
    # bug back on the display side of the same number.
    rate = {
        section: sum(r["uncovered"].count(section) for r in ok)
        / (len(ok) * checkable.count(section))
        for section in dict.fromkeys(checkable)
    }

    reasons: list[str] = []

    if not fixture.must_fire and not fixture.must_stay_quiet:
        # An `--source` file. Nobody has adjudicated which of its sections
        # SHOULD have produced an object, so every verdict below would be
        # scoring against an empty expectation and would read as a pass. Give
        # the one number this arm exists for instead.
        flagged = [len(r["uncovered"]) for r in ok]
        shares = [run_share(r) for r in ok]
        reasons.append(
            f"{len(checkable)} checkable section(s); flagged per run: "
            f"{', '.join(str(n) for n in flagged)}"
        )
        reasons.append(
            "uncovered share of checkable text per run: " + render_shares(shares)
        )
        reasons.append(
            "no adjudicated expectations for this file -- this arm measures "
            "how LOUD the signal is here, not whether it is right"
        )
        return FixtureVerdict(
            fixture.name, len(ok), "UNADJUDICATED", tuple(reasons), tuple(rate.items())
        )

    over_fired = tuple(
        section
        for section in fixture.must_stay_quiet
        if section in rate and rate[section] > 0
    )
    for section in over_fired:
        reasons.append(
            f"{section!r} produced objects in the reported run and was flagged "
            f"in {rate[section]:.0%} of runs"
        )

    unchecked = tuple(s for s in fixture.must_stay_quiet if s not in rate)
    for section in unchecked:
        reasons.append(
            f"{section!r} is skipped by this predicate's checkability gate, so "
            "it was never checked -- it cannot over-fire, and it cannot help"
        )

    if over_fired:
        return FixtureVerdict(
            fixture.name, len(ok), "OVER-FIRES", tuple(reasons), tuple(rate.items())
        )
    if checkable and all(rate[s] == 1.0 for s in checkable):
        reasons.append("every checkable section flagged in every run")
        return FixtureVerdict(
            fixture.name, len(ok), "VACUOUS", tuple(reasons), tuple(rate.items())
        )
    if fixture.must_fire and all(rate.get(s, 0.0) == 0.0 for s in checkable):
        reasons.append(
            "nothing flagged on the source the issue was filed about -- either "
            "no run lost a section, or the signal cannot see it"
        )
        return FixtureVerdict(
            fixture.name, len(ok), "BLIND", tuple(reasons), tuple(rate.items())
        )
    reasons.append("no section that produced objects was flagged")
    return FixtureVerdict(
        fixture.name, len(ok), "NO OVER-FIRE", tuple(reasons), tuple(rate.items())
    )


_CRITERION: Final = (
    "SHIP/KILL CRITERION (fixed before any predicate was written): the "
    "reported #793 failure must score HIGH uncovered while healthy runs on "
    "ordinary sources -- above all discursive transcripts -- score LOW. A "
    "predicate that cannot put the first above the second is REFUTED, as "
    "`quote` was: its distributions did not overlap, they inverted."
)
"""Printed at the top of every report, under every predicate.

In the output and not only in the README, because the number and the bar it
has to clear should not be readable one without the other. A criterion a
reader has to go looking for is one that gets written after the result.
"""


def _rate_cell(rate: dict[str, float], section: str, width: int) -> str:
    """One predicate's cell for one section: its flagged rate, or `skipped`.

    `skipped` is not 0%. A section this predicate's gate rejected entered
    NEITHER total, so it was never given the chance to fire; printing 0%
    would read as "checked, and clean" and would make two predicates with
    different denominators look like they measured the same source.
    """
    if section not in rate:
        return f"{'skipped':>{width}}"
    return f"{rate[section]:>{width - 1}.0%} "


def summarize(
    records: list[dict[str, Any]],
    fixtures: tuple[Fixture, ...],
    predicates: tuple[coverage.CoveragePredicate, ...] = (coverage.QUOTE,),
) -> str:
    """The report: every predicate scored over the same runs, side by side.

    Side by side and never one table per predicate. The finding is the
    COMPARISON -- `quote`'s refutation is not that 62.0% is low, it is that
    98.0% on an ordinary transcript sits above it -- and two tables a page
    apart are read as two results.
    """
    lines: list[str] = ["", "=" * 72, "SECTION COVERAGE (#793)", "=" * 72, ""]
    lines.append(_CRITERION)
    lines.append("")
    lines.append("predicates scored:")
    for predicate in predicates:
        lines.append(f"   {predicate.name:<10} {predicate.describe}")

    # Wide enough for the widest predicate name AND for the word `predicate`
    # heading the column, so the verdict table cannot shear when a short name
    # like `quote` is scored alone.
    name_width = max(len("predicate"), *(len(p.name) for p in predicates))
    cell = max(name_width + 2, 9)

    for fixture in fixtures:
        lines.append("")
        lines.append(f"## {fixture.name}")
        lines.append("")

        # Rescored ONCE per predicate, then read three times below: for the
        # verdicts, for the shares line, and for the objects-per-run loop.
        # Recomputing each time ran `coverage_report` over the whole source
        # three times per run per predicate, and under `overlap` that
        # retokenizes every object text once per section.
        #
        # `rescored_runs` filters on `record["fixture"]` and preserves order,
        # so `own[i]` and `rescored[name][i]` are the same run. That
        # alignment is what lets the objects loop index instead of rescoring.
        own = [record for record in records if record["fixture"] == fixture.name]
        rescored = {
            predicate.name: rescored_runs(records, fixture, predicate)
            for predicate in predicates
        }
        verdicts = {
            predicate.name: evaluate(fixture, rescored[predicate.name])
            for predicate in predicates
        }

        lines.append(
            f"   {'predicate':<{name_width}}  {'verdict':<14} {'ok':>3}  "
            "uncovered share of checkable text per run"
        )
        for predicate in predicates:
            verdict = verdicts[predicate.name]
            shares = [
                run_share(r) for r in rescored[predicate.name] if r["error"] is None
            ]
            lines.append(
                f"   {predicate.name:<{name_width}}  {verdict.verdict:<14} "
                f"{verdict.ok_runs:>3}  {render_shares(shares) or '(no ok runs)'}"
            )

        floor = {p.name: reported_failure_share(fixture, p) for p in predicates}
        if any(v is not None for v in floor.values()):
            rendered = "   ".join(
                f"{name} {value:.1%}"
                for name, value in floor.items()
                if value is not None
            )
            lines.append("")
            lines.append(
                f"   the failure #793 reports would score at least: {rendered}"
            )
            lines.append(
                "   (reconstruction from the adjudicated outcome -- see "
                "`reported_failure_share`)"
            )

        lines.append("")
        header = "".join(f"{p.name:>{cell - 1}} " for p in predicates)
        lines.append(f"   {'section':<45}{header}  expectation")
        rates = {name: dict(v.flagged_rate) for name, v in verdicts.items()}
        for section in dict.fromkeys(
            s.heading for s in coverage.split_sections(fixture.text)
        ):
            if section in fixture.must_fire:
                expect = "MUST FIRE"
            elif section in fixture.must_stay_quiet:
                expect = "must stay quiet"
            else:
                expect = "(unadjudicated)"
            cells = "".join(
                _rate_cell(rates[p.name], section, cell) for p in predicates
            )
            lines.append(f"   {section:<45}{cells}  {expect}")

        lines.append("")
        for predicate in predicates:
            for reason in verdicts[predicate.name].reasons:
                lines.append(f"   - [{predicate.name}] {reason}")

        lines.append("")
        lines.append("   objects per run (adjudicate the under-fire half by hand):")
        for index, record in enumerate(own):
            if record["error"]:
                lines.append(f"     run {record['run']}: ERROR {record['error']}")
                continue
            titles = (
                ", ".join(object_identity(o) for o in record.get("objects", []))
                or "(none)"
            )
            lines.append(f"     run {record['run']}: {titles}")
            for predicate in predicates:
                scored = rescored[predicate.name][index]
                if scored["error"]:
                    lines.append(f"             [{predicate.name}] {scored['error']}")
                    continue
                flagged = ", ".join(scored["uncovered"]) or "(none flagged)"
                lines.append(f"             [{predicate.name}] flagged: {flagged}")
    lines.append("")
    lines.append(
        "The under-fire half is NOT scored here: whether a run lost a section "
        "is a fact about that run's objects, printed above."
    )
    return "\n".join(lines)


# The paraphrase the `overlap` hypothesis exists for, and the exact case
# `quote` fails on: the object reorders the sentence, nominalizes its verb
# and drops nothing, so every content word survives and no substring does.
# Spanish because that is the corpus, and because it exercises the accent
# folding at the same time.
_PARAPHRASE_SOURCE: Final = (
    "El equipo acordó migrar el servicio de facturación a PostgreSQL 16 "
    "antes de la entrega de marzo."
)
_PARAPHRASE_OBJECT: Final = (
    "Migración del servicio de facturación a PostgreSQL 16: el equipo lo "
    "acordó antes de la entrega de marzo."
)

# The README's own hand-checked mechanism example, verbatim from the
# transcript arm's `## Resumen`.
_RESUMEN_SOURCE: Final = (
    "El equipo definió el alcance del sistema y acordó usar minutas reales "
    "para validar la arquitectura propuesta."
)
_RESUMEN_OBJECT: Final = "Uso de Minutas Reales para Validación"


def _self_test() -> int:
    """Prove the harness's own machinery with no model running.

    The FIRST assertion is that a predicate can see a section AT ALL when the
    section's own text is handed to it as an object -- the floor every other
    reading rests on. A probe whose signal cannot cover a section under
    perfect input would report `VACUOUS` on any model and look like a
    finding.
    """
    failures: list[str] = []

    def check(condition: bool, why: str) -> None:
        if not condition:
            failures.append(why)

    every = tuple(coverage.PREDICATES.values())

    for predicate in every:
        for fixture in build_fixtures():
            sections = coverage.split_sections(fixture.text)
            perfect = [section.body for section in sections]
            got = coverage.uncovered_sections(perfect, fixture.text, predicate)
            check(
                got == (),
                f"[{predicate.name}] {fixture.name}: every section must be covered "
                f"when its own body is the object text (got {got})",
            )
            headings = {section.heading for section in sections}
            for named in fixture.must_fire + fixture.must_stay_quiet:
                check(
                    named in headings,
                    f"{fixture.name}: fixture names section {named!r}, which the "
                    f"splitter does not produce (it produces {sorted(headings)})",
                )

    helios = build_fixtures()[0]
    check(
        coverage.uncovered_sections([], helios.text)
        == (
            "# Helios Data Platform (HDP) — Overview",
            "## Storage",
            "## Components",
            "## Ownership",
        ),
        "with no objects at all, every checkable section of helios-overview "
        f"must be flagged (got {coverage.uncovered_sections([], helios.text)})",
    )

    # ------------------------------------------------------------------
    # The refactor's safety net: `quote` is the shipped behaviour MOVED,
    # not changed. Recomputing the committed sweep from its stored objects
    # must reproduce its stored verdicts exactly. Nothing else in this repo
    # would notice if the seam altered them.
    # ------------------------------------------------------------------
    # Read inside a guard, for the same reason `resolve_predicates` is
    # called inside one about a hundred lines below: an uncaught
    # `FileNotFoundError` here aborts `_self_test` before `failures` is
    # printed, so renaming or losing this file would suppress every
    # assertion already collected above -- reporting a traceback where the
    # real outcome is "and also these six other things are broken". A
    # missing or unreadable sweep is the equivalence pin failing, so it is
    # recorded as one failure and the rest of the run continues on an empty
    # list, which the `reproduced == 9` count below then also catches.
    committed: list[dict[str, Any]] = []
    try:
        committed = json.loads(COMMITTED_RUNS.read_text())
    except (OSError, ValueError) as exc:
        check(
            False,
            f"the committed sweep {COMMITTED_RUNS.name} must be readable JSON -- "
            f"it is the only pin under `quote` being the shipped behaviour "
            f"moved rather than changed ({type(exc).__name__}: {exc})",
        )
    by_name = {f.name: f for f in build_fixtures()}
    reproduced = 0
    for record in committed:
        if record["error"] is not None:
            continue
        again = rescore(record, by_name[record["fixture"]], coverage.QUOTE)
        check(
            (
                again["uncovered"],
                again["uncovered_chars"],
                again["checkable_chars"],
                again["checkable"],
            )
            == (
                record["uncovered"],
                record["uncovered_chars"],
                record["checkable_chars"],
                record["checkable"],
            ),
            f"[quote] {COMMITTED_RUNS.name} {record['fixture']} run {record['run']}: "
            f"rescoring the stored objects must reproduce the stored verdicts "
            f"(stored {record['uncovered']}/{record['uncovered_chars']}/"
            f"{record['checkable_chars']}, recomputed {again['uncovered']}/"
            f"{again['uncovered_chars']}/{again['checkable_chars']})",
        )
        reproduced += 1
    # A loop over an empty list passes every assertion inside it. Count the
    # exposure, or a renamed results file turns this net into a decoration.
    check(
        reproduced == 9,
        f"the committed sweep must contribute 9 rescorable runs to the "
        f"equivalence pin (got {reproduced})",
    )

    published = reported_failure_share(helios, coverage.QUOTE)
    check(
        published is not None and abs(published - 276 / 445) < 1e-12,
        "[quote] the reported #793 failure must still score exactly 276/445 = "
        f"62.0%, the figure the README publishes (got {published})",
    )
    check(
        reported_failure_share(build_fixtures()[1], coverage.QUOTE) is None,
        "a fixture recording no lost section has no reported-failure share to "
        "reconstruct, and must answer None rather than 0.0",
    )

    # `reported_failure_share` is printed for EVERY selected predicate, as the
    # first half of the stated ship/kill criterion. Pinning it under `quote`
    # alone left the number the report presents as evidence unconstrained the
    # moment a second predicate was selected -- which is now every ladder
    # invocation. Pinned here at the shipped default AND at both edges of the
    # measured window, because those are the three values the README quotes.
    #
    # It coming out at 276/445 under both predicates is not a tautology and
    # not a copy: the two gates admit sections on unrelated grounds (a
    # four-WORD evidence line against four DISTINCT content words) and the
    # covering tests share no code. They agree here because this source's four
    # sections all clear both gates and the same two are lost. Raise
    # `OVERLAP_MIN_CONTENT_WORDS` past a section's content-word count and this
    # denominator moves while `quote`'s does not.
    for threshold in (
        coverage.OVERLAP_COVERED_FRACTION,
        *coverage.OVERLAP_MEASURED_WINDOW,
    ):
        swept = coverage.overlap_predicate(threshold)
        floor_share = reported_failure_share(helios, swept)
        check(
            floor_share is not None and abs(floor_share - 276 / 445) < 1e-12,
            f"[{swept.name}] the reported #793 failure must score exactly "
            f"276/445 = 62.0% under overlap too -- same four sections admitted, "
            f"same two lost, a different covering test (got {floor_share})",
        )
        check(
            reported_failure_share(build_fixtures()[1], swept) is None,
            f"[{swept.name}] a fixture recording no lost section must answer "
            "None rather than 0.0, under every predicate and not only `quote`",
        )

    # ------------------------------------------------------------------
    # Each predicate carries its OWN checkability gate, and the two gates
    # genuinely disagree. Both directions are pinned: a single direction
    # would also pass if one gate merely delegated to the other.
    # ------------------------------------------------------------------
    stopwords_only = "de la que en el"
    check(
        coverage.QUOTE.checkable(stopwords_only)
        and not coverage.OVERLAP.checkable(stopwords_only),
        "a line of pure function words clears the four-WORD evidence floor "
        "but carries no content word, so `quote` must check it and `overlap` "
        f"must skip it (quote {coverage.QUOTE.checkable(stopwords_only)}, "
        f"overlap {coverage.OVERLAP.checkable(stopwords_only)})",
    )
    short_lines = "Marta Ruiz\nTom Becker"
    check(
        not coverage.QUOTE.checkable(short_lines)
        and coverage.OVERLAP.checkable(short_lines),
        "four content words spread over two-word LINES clear no evidence line "
        "but do clear a content-word floor, so `overlap` must check it and "
        f"`quote` must skip it (quote {coverage.QUOTE.checkable(short_lines)}, "
        f"overlap {coverage.OVERLAP.checkable(short_lines)})",
    )

    # ...and a skipped section enters NEITHER total, for each gate on its own
    # terms. Measured against the same source scored without the thin
    # section, so the assertion cannot pass by the numbers merely being
    # equal to something.
    keeper = "## Kept\na sentence with several real content words in it\n"
    for predicate, thin in ((coverage.QUOTE, "TBD"), (coverage.OVERLAP, "de la que")):
        alone = coverage.coverage_report([], keeper, predicate)
        withthin = coverage.coverage_report([], f"{keeper}## Thin\n{thin}\n", predicate)
        check(
            (withthin.uncovered, withthin.checkable_chars, withthin.uncovered_chars)
            == (alone.uncovered, alone.checkable_chars, alone.uncovered_chars),
            f"[{predicate.name}] a section its gate skips must change nothing: "
            f"not `uncovered`, not the numerator, not the denominator "
            f"(alone {alone}, with the thin section {withthin})",
        )

    # ------------------------------------------------------------------
    # The `overlap` hypothesis, pinned against its own reason for existing.
    # ------------------------------------------------------------------
    section = f"## Decisión\n{_PARAPHRASE_SOURCE}\n"
    check(
        coverage.uncovered_sections([_PARAPHRASE_OBJECT], section, coverage.QUOTE)
        == ("## Decisión",),
        "the paraphrase case must be one `quote` MISSES, or the next "
        "assertion proves nothing about overlap",
    )
    check(
        coverage.uncovered_sections([_PARAPHRASE_OBJECT], section, coverage.OVERLAP)
        == (),
        "[overlap] a reordered, nominalized paraphrase that keeps every "
        "content word must be covered -- this is the entire hypothesis, and "
        "the accented Spanish is half of it",
    )

    # The README's OWN mechanism example, which is a harder paraphrase: the
    # object keeps four of eleven content words, scoring 0.1818. `quote` sees
    # a hard nothing; `overlap` sees a non-zero score that clears no threshold
    # anyone has proposed. Both halves are asserted, and the second is
    # asserted against the named constant rather than a literal -- so lowering
    # the threshold past this case turns this line red and makes the reader
    # decide deliberately instead of discovering it in a sweep.
    #
    # The message below no longer says UNCALIBRATED, because the ladder in
    # the README measured one. It says what that ladder does NOT rescue:
    # 0.1818 sits below the measured window's own floor of 0.20, so `overlap`
    # at the separating threshold ALSO reports this correct extraction as
    # uncovered. The mechanism that killed `quote` is not repaired here, only
    # outvoted by the rest of the source -- and that is the finding, not a
    # detail. The numeric bound is unchanged: no measurement contradicts it.
    resumen = coverage.overlap_fraction([_RESUMEN_OBJECT], _RESUMEN_SOURCE)
    check(
        not coverage.QUOTE.covers([_RESUMEN_OBJECT], _RESUMEN_SOURCE),
        "the README's `## Resumen` example must remain one `quote` reports as "
        "uncovered -- it is the mechanism the refutation rests on",
    )
    check(
        0.0 < resumen < coverage.OVERLAP_COVERED_FRACTION,
        "[overlap] the README's `## Resumen` example scores above zero and "
        f"below the threshold this constant holds "
        f"({coverage.OVERLAP_COVERED_FRACTION}); got {resumen:.4f}, which is "
        "also below the 0.20 floor of the MEASURED window, so overlap does "
        "not rescue this hand-checked paraphrase at any separating value",
    )

    # ------------------------------------------------------------------
    # An object is scored against EACH section's own body. Quoting one
    # section must not clear its neighbour, for any predicate -- otherwise
    # one well-covered section would launder a whole source.
    # ------------------------------------------------------------------
    two = (
        "## Storage\nHDP standardized on MySQL 8 as its primary datastore.\n"
        "## Ownership\nMarta Ruiz leads the platform team day to day.\n"
    )
    for predicate in every:
        got = coverage.uncovered_sections(
            ["HDP standardized on MySQL 8 as its primary datastore."], two, predicate
        )
        check(
            got == ("## Ownership",),
            f"[{predicate.name}] an object covering `## Storage` must leave "
            f"`## Ownership` uncovered, not be credited to it (got {got})",
        )

    # ------------------------------------------------------------------
    # The registry and its selector.
    # ------------------------------------------------------------------
    check(
        all(name == p.name for name, p in coverage.PREDICATES.items()),
        f"every registry key must equal its predicate's name (got "
        f"{[(k, p.name) for k, p in coverage.PREDICATES.items()]})",
    )
    # Caught rather than allowed to propagate: an uncaught raise here would
    # abort `_self_test` before the failure list is printed, hiding every
    # assertion already collected above it. A raise on a KNOWN name is the
    # same claim failing, so it is recorded as one.
    try:
        check(
            resolve_predicates([ALL_PREDICATES]) == every,
            f"--predicate all must expand to every predicate in registry order "
            f"(got {[p.name for p in resolve_predicates([ALL_PREDICATES])]})",
        )
        check(
            resolve_predicates(["overlap", "quote"]) == every,
            "predicates must render in registry order however they were typed, "
            "so the baseline column is always leftmost",
        )
    except KeyError as exc:
        check(
            False,
            f"every registry key must resolve as a --predicate name, and "
            f"{exc.args[0]!r} says one does not",
        )
    try:
        resolve_predicates(["quoet"])
    except KeyError:
        pass
    else:
        check(False, "an unknown predicate name must raise, never score silently")

    # A run stored before `objects` existed cannot be rescored under ANY
    # predicate, and must not fall back to its stored `quote` verdicts.
    legacy = rescore(
        {"fixture": "helios-overview", "run": 1, "error": None, "uncovered": ["## A"]},
        helios,
        coverage.OVERLAP,
    )
    check(
        legacy["error"] is not None,
        "a run stored without objects must become an errored run, never be "
        f"read back under another predicate's column (got {legacy})",
    )

    # Verdict logic, each branch triggered independently.
    over = evaluate(
        helios,
        [
            {
                "fixture": "helios-overview",
                "error": None,
                "uncovered": ["## Ownership"],
                "checkable": ["## Storage", "## Ownership"],
            }
        ],
    )
    check(
        over.verdict == "OVER-FIRES",
        f"a flag on a must-stay-quiet section must OVER-FIRE (got {over.verdict})",
    )

    vacuous = evaluate(
        helios,
        [
            {
                "fixture": "helios-overview",
                "error": None,
                "uncovered": ["## Storage", "## Components"],
                "checkable": ["## Storage", "## Components"],
            }
        ],
    )
    check(
        vacuous.verdict == "VACUOUS",
        f"every checkable section flagged every run must be VACUOUS (got {vacuous.verdict})",
    )

    blind = evaluate(
        helios,
        [
            {
                "fixture": "helios-overview",
                "error": None,
                "uncovered": [],
                "checkable": ["## Storage", "## Components"],
            }
        ],
    )
    check(
        blind.verdict == "BLIND",
        f"nothing flagged on the treatment source must be BLIND (got {blind.verdict})",
    )

    clean = evaluate(
        helios,
        [
            {
                "fixture": "helios-overview",
                "error": None,
                "uncovered": ["## Storage"],
                "checkable": ["## Storage", "## Components"],
            }
        ],
    )
    check(
        clean.verdict == "NO OVER-FIRE",
        f"one section flagged and no quiet one must pass (got {clean.verdict})",
    )

    check(
        stores_results(None) and not stores_results(pathlib.Path("/x/private.md")),
        "results/ must accept the committed fixtures and refuse a --source file",
    )

    # `run_share`'s three branches. The middle one is the trap: a run stored
    # before the char accounting must NOT read as 0.0%, because that is the
    # value a perfectly covered run gets.
    check(
        run_share({"uncovered": [], "uncovered_chars": 0, "checkable_chars": 445})
        == 0.0,
        "nothing uncovered must be a zero share",
    )
    check(
        run_share({"uncovered": ["## A"]}) is None,
        "a run with flags but no char accounting must report None, never 0.0",
    )
    check(
        run_share(
            {"uncovered": ["## A"], "uncovered_chars": 276, "checkable_chars": 445}
        )
        is not None,
        "a fully recorded run must report a share",
    )
    # Duplicate headings on the display side, the collision the leaf's
    # CoverageReport was redesigned to remove.
    dup = evaluate(
        Fixture(name="dup", title="d", text="x", must_fire=(), must_stay_quiet=()),
        [
            {
                "fixture": "dup",
                "error": None,
                "uncovered": ["## Notes"],
                "checkable": ["## Notes", "## Notes"],
                "uncovered_chars": 10,
                "checkable_chars": 40,
            }
        ],
    )
    check(
        dict(dup.flagged_rate) == {"## Notes": 0.5},
        "one of two same-named sections flagged must rate 0.5, not 1.0 "
        f"(got {dict(dup.flagged_rate)})",
    )
    both = evaluate(
        Fixture(name="dup", title="d", text="x", must_fire=(), must_stay_quiet=()),
        [
            {
                "fixture": "dup",
                "error": None,
                "uncovered": ["## Notes", "## Notes"],
                "checkable": ["## Notes", "## Notes"],
                "uncovered_chars": 40,
                "checkable_chars": 40,
            }
        ],
    )
    check(
        dict(both.flagged_rate) == {"## Notes": 1.0},
        f"both same-named sections flagged must rate 1.0 "
        f"(got {dict(both.flagged_rate)})",
    )
    # TWO flagged sections under ONE heading: the case that separates
    # counting sections from counting distinct headings. The single-flag
    # case above cannot -- a one-element list and its set are the same
    # length, so it would pass against either.
    check(
        "flagged per run: 2" in " ".join(both.reasons),
        f"the flagged count must count SECTIONS, not distinct headings "
        f"(got {both.reasons})",
    )
    check(
        render_shares([0.62, None]) == "62.0%, not recorded",
        f"unrecorded shares must be named, not printed as a number "
        f"(got {render_shares([0.62, None])!r})",
    )
    check(
        object_texts([{"body": "  ", "description": "fallback text"}])
        == ["fallback text"],
        "a blank body must fall back to the description, as the builder does",
    )
    check(
        object_texts([{"body": "real body", "description": "fallback text"}])
        == ["real body"],
        "a non-blank body must win over the description",
    )
    # A skipped section must render as `skipped`, never as 0%: the two mean
    # opposite things and the whole side-by-side table turns on the
    # difference.
    check(
        _rate_cell({}, "## Thin", 9).strip() == "skipped"
        and _rate_cell({"## Thin": 0.0}, "## Thin", 9).strip() == "0%",
        "a gate-skipped cell must read `skipped` and a clean checked one `0%` "
        f"(got {_rate_cell({}, '## Thin', 9)!r} and "
        f"{_rate_cell({'## Thin': 0.0}, '## Thin', 9)!r})",
    )

    # ------------------------------------------------------------------
    # The threshold FACTORY. A swept value must announce itself, or a
    # ladder prints eight columns all headed `overlap` and every published
    # number becomes unattributable to the threshold that produced it.
    # ------------------------------------------------------------------
    check(
        coverage.overlap_predicate().name == "overlap"
        and coverage.PREDICATES["overlap"] is coverage.OVERLAP,
        "the factory at its default must BE the registry entry, or "
        "`--predicate overlap` and the committed numbers stop meaning the "
        f"same thing (got {coverage.overlap_predicate().name!r})",
    )
    swept_names = [coverage.overlap_predicate(b).name for b in (0.15, 0.2, 0.25, 0.5)]
    check(
        swept_names == ["overlap@0.15", "overlap@0.2", "overlap@0.25", "overlap"]
        and len(set(swept_names)) == len(swept_names),
        "every swept threshold must produce its OWN name, and only the "
        f"shipped default may be the bare `overlap` (got {swept_names})",
    )
    check(
        all(
            f"{b:.10g}" in coverage.overlap_predicate(b).describe
            for b in (0.15, 0.2, 0.5)
        ),
        "a predicate's `describe` must state the threshold it actually used -- "
        "it is the only line of the report that carries the value",
    )
    check(
        "inside" in coverage.overlap_predicate(0.2).describe
        and "outside" in coverage.OVERLAP.describe,
        "`describe` must say whether THIS value sits in the measured window "
        f"{coverage.OVERLAP_MEASURED_WINDOW}; the shipped default does not "
        f"(got {coverage.OVERLAP.describe!r})",
    )
    # The swept threshold must actually drive the covering test, against the
    # README's own hand-checked 0.1818 case -- so this pins the factory AND
    # the floor of the measured window in one assertion. A factory that
    # ignored its argument and read the module constant would score BOTH of
    # these uncovered and turn the first check red.
    check(
        coverage.overlap_predicate(0.15).covers([_RESUMEN_OBJECT], _RESUMEN_SOURCE)
        and not coverage.overlap_predicate(coverage.OVERLAP_MEASURED_WINDOW[0]).covers(
            [_RESUMEN_OBJECT], _RESUMEN_SOURCE
        ),
        "the README's 0.1818 `## Resumen` case must be COVERED at B = 0.15 and "
        "UNCOVERED at the window's own floor -- the swept value has to reach "
        "the covering test, not just the column header",
    )
    for bad in (0.0, -0.1, 1.5):
        try:
            coverage.overlap_predicate(bad)
        except ValueError:
            pass
        else:
            check(
                False,
                f"a threshold outside (0.0, 1.0] must raise, never print a "
                f"column of 0% or 100% that looks measured (got {bad})",
            )

    # ------------------------------------------------------------------
    # `select_predicates`: a ladder is ONE invocation, or the published
    # table is assembled by hand and nobody can regenerate it.
    # ------------------------------------------------------------------
    def selected(names: list[str] | None, thresholds: list[float] | None) -> list[str]:
        return [p.name for p in select_predicates(names, thresholds)]

    def line_with(report: str, *needles: str) -> str:
        """The first line of `report` carrying every needle, or `""`.

        Total, and `next(...)` deliberately is not. A report missing the line
        is the assertion below failing; a `StopIteration` here would abort
        `_self_test` and suppress every failure already collected -- the exact
        hazard the committed-sweep read a hundred lines above is guarded
        against, arriving through the assertions added to catch it.
        """
        for line in report.splitlines():
            if all(needle in line for needle in needles):
                return line
        return ""

    check(
        selected(None, None) == ["quote"],
        f"no flags at all must still score the refuted baseline "
        f"(got {selected(None, None)})",
    )
    check(
        selected(None, [0.05, 0.2, 0.15])
        == ["overlap@0.05", "overlap@0.2", "overlap@0.15"],
        "a ladder must keep the order it was typed in, and must not drag the "
        f"baseline in uninvited (got {selected(None, [0.05, 0.2, 0.15])})",
    )
    check(
        selected(["quote"], [0.2, 0.2]) == ["quote", "overlap@0.2"],
        f"a repeated threshold is one column, and registry columns come first "
        f"(got {selected(['quote'], [0.2, 0.2])})",
    )
    check(
        selected(["overlap"], [coverage.OVERLAP_COVERED_FRACTION]) == ["overlap"],
        "sweeping the shipped default alongside `--predicate overlap` is the "
        "same predicate twice and must render as ONE column "
        f"(got {selected(['overlap'], [coverage.OVERLAP_COVERED_FRACTION])})",
    )

    # ------------------------------------------------------------------
    # The under-fire ablation, pinned against the exact table the README
    # publishes. This is the arm the whole `overlap` result rests on, and
    # before this it was produced by a script that is not in the repo.
    # ------------------------------------------------------------------
    kept = ablate(
        {
            "error": None,
            "objects": [
                {"type": "Concept", "title": "Helios Data Platform"},
                {"type": "Concept", "title": "MySQL 8"},
                {"type": "Person", "title": "Marta Ruiz"},
            ],
        },
        helios.reported_objects,
    )
    check(
        [object_identity(o) for o in kept["objects"]]
        == ["Concept: Helios Data Platform", "Person: Marta Ruiz"],
        "ablation must keep exactly the objects the reported run produced and "
        f"drop the rest (got {[object_identity(o) for o in kept['objects']]})",
    )
    check(
        ablate({"error": "boom", "objects": [{"type": "C", "title": "x"}]}, ())[
            "objects"
        ]
        == [{"type": "C", "title": "x"}],
        "an errored run must be returned untouched, never ablated into the "
        "loudest possible number",
    )

    # The two rungs the README's arm 1 turns on: at 0.15 the signal is
    # already loud and names neither lost section; at 0.20 it names both in
    # every run. The flip is the finding, and it is a flip in WHAT IS NAMED
    # rather than in how much fires.
    for threshold, expect_named, expect_shares in (
        (0.15, 0, (0.337, 0.337, 0.337, 0.445, 0.337)),
        (coverage.OVERLAP_MEASURED_WINDOW[0], 5, (0.620, 0.620, 0.620, 0.728, 0.620)),
    ):
        rows = ablation_rows(
            committed, helios, (coverage.overlap_predicate(threshold),)
        )
        row = rows[0]
        check(
            row.ok_runs == 5 and row.named_both == expect_named,
            f"[{row.predicate}] the README publishes {expect_named}/5 runs "
            f"naming BOTH lost sections at B = {threshold} "
            f"(got {row.named_both}/{row.ok_runs})",
        )
        check(
            len(row.ablated) == len(expect_shares)
            and all(
                got is not None and abs(got - want) < 5e-4
                for got, want in zip(row.ablated, expect_shares, strict=True)
            ),
            f"[{row.predicate}] the ablated shares must reproduce the README's "
            f"ladder row {expect_shares} (got {row.ablated})",
        )
        check(
            all(value == 0.0 for value in row.full),
            f"[{row.predicate}] the SAME runs unablated must score 0% -- the "
            f"ablated number is a finding only beside that column "
            f"(got {row.full})",
        )

    # 276/445 arriving from a second, independent direction: the ablation
    # keeps the model's own real object texts, `reported_failure_share` hands
    # each surviving section its own body. Two reconstructions, one number.
    inside_window = ablation_rows(
        committed,
        helios,
        (coverage.overlap_predicate(coverage.OVERLAP_MEASURED_WINDOW[0]),),
    )[0]
    # Indexed through a guard rather than directly: a sweep that failed to
    # load leaves this row empty, and an `IndexError` here would abort the
    # run and hide the failure that already recorded WHY it is empty.
    inside_first = inside_window.ablated[0] if inside_window.ablated else None
    check(
        inside_first is not None and abs(inside_first - 276 / 445) < 1e-12,
        "inside the window the ablated share must be exactly 276/445, the same "
        "numerator over the same denominator the README publishes for `quote` "
        f"(got {inside_first})",
    )

    ablated_report = ablation_table(
        committed, build_fixtures(), (coverage.overlap_predicate(0.2),)
    )
    check(
        "NOT ABLATABLE" in ablated_report and "kickoff" in ablated_report,
        "a fixture with no reported object list must be NAMED and skipped, "
        "never silently absent -- a missing arm reads as a clean one",
    )
    check(
        "names BOTH" in ablated_report and "5/5" in ablated_report,
        f"the ablation table must carry the naming column, not shares alone "
        f"(got {ablated_report!r})",
    )

    # ------------------------------------------------------------------
    # `summarize` itself. Everything above tests the numbers it prints;
    # nothing tested the printing, which carries the multi-column header,
    # the per-section cell loop and the floor line.
    # ------------------------------------------------------------------
    swept = coverage.overlap_predicate(coverage.OVERLAP_MEASURED_WINDOW[0])
    report = summarize(committed, build_fixtures(), (coverage.QUOTE, swept))
    for wanted in (
        "## helios-overview",
        "## kickoff",
        swept.describe,
        # The floor line, under BOTH predicates and labelled by name.
        f"quote 62.0%   {swept.name} 62.0%",
        # An errored run must not render in the shape of a result.
        "ERROR OllamaGenerationCapped",
        "Concept: Helios Data Platform",
        f"[{swept.name}] flagged: (none flagged)",
    ):
        check(
            wanted in report,
            f"the report must carry {wanted!r} (it does not; "
            f"{len(report.splitlines())} lines rendered)",
        )
    # The multi-column table must not SHEAR: the cells occupy the same width
    # on the header line and on every section row, whatever the predicate
    # names are. A long swept name is what stresses it -- `overlap@0.2` is
    # wider than the word `predicate` the header column is sized against.
    header_line = line_with(report, "section", "expectation")
    storage_line = line_with(report, "## Storage", "MUST FIRE")
    check(
        len(header_line) - len("  expectation")
        == len(storage_line) - len("  MUST FIRE"),
        "the section rows must line up under the column header for every "
        f"selected predicate (header {header_line!r}, row {storage_line!r})",
    )
    # A gate DISAGREEMENT rendered end to end: `quote` checks a line of pure
    # function words, `overlap` skips it, and the row has to say so in the
    # two columns rather than printing 0% under both.
    thin_fixture = Fixture(
        name="thin",
        title="thin",
        text="## Kept\na sentence with several real content words in it\n"
        "## Thin\nde la que en el\n",
        must_fire=(),
        must_stay_quiet=(),
    )
    thin_report = summarize(
        [{"fixture": "thin", "run": 1, "error": None, "objects": []}],
        (thin_fixture,),
        (coverage.QUOTE, coverage.OVERLAP),
    )
    thin_line = line_with(thin_report, "## Thin")
    check(
        "skipped" in thin_line and "100%" in thin_line,
        "a section one gate checks and the other skips must render as `100%` "
        f"and `skipped` side by side, never as two zeros (got {thin_line!r})",
    )
    # The objects-per-run block reads a list rescored ONCE, by index, rather
    # than rescoring each record a third time. Two runs of one fixture with
    # OPPOSITE outcomes pin that alignment: an off-by-one, or one cached
    # entry reused for every row, prints one run's flags under another run's
    # objects -- a wrong verdict that reads as a correct one.
    aligned = summarize(
        [
            {
                "fixture": "thin",
                "run": 1,
                "error": None,
                "objects": [
                    {
                        "type": "Concept",
                        "title": "kept",
                        "body": "a sentence with several real content words in it",
                        "description": "",
                    }
                ],
            },
            {"fixture": "thin", "run": 2, "error": None, "objects": []},
        ],
        (thin_fixture,),
        (coverage.QUOTE,),
    )
    check(
        "run 1: Concept: kept\n             [quote] flagged: ## Thin" in aligned
        and "run 2: (none)\n             [quote] flagged: ## Kept, ## Thin" in aligned,
        f"each run's flags must be printed beside ITS OWN objects (got {aligned!r})",
    )
    check(
        "NO DATA" in summarize([], build_fixtures(), (coverage.QUOTE,)),
        "a report over no runs at all must say NO DATA rather than raise",
    )

    # ------------------------------------------------------------------
    # LEAVE-ONE-SECTION-OUT (#793): the under-fire arm that needs no
    # adjudication and therefore reaches a DISCURSIVE source.
    #
    # `--ablate` cuts a run down to the objects one reported run produced,
    # which pins it to the two committed fixtures and to the one terse
    # bullet-shaped file the README names as its single biggest gap. This
    # arm constructs the loss per SECTION instead: drop the objects that
    # demonstrably QUOTE a section, then ask the predicate whether it
    # notices. Attribution is `evidence_line`, verbatim quoting; coverage
    # is the predicate under test. Two different mechanisms, which is what
    # keeps the row from being true by construction.
    # ------------------------------------------------------------------
    loso_source = (
        "# Title\n"
        "The platform is the ingestion and query layer for the team.\n"
        "## Storage\n"
        "The platform standardized on MySQL 8 as its primary datastore.\n"
        "## Ownership\n"
        "The technical lead for this workstream is Marta Ruiz.\n"
    )
    quoting_object = "The platform standardized on MySQL 8 as its primary datastore."
    other_object = "The technical lead for this workstream is Marta Ruiz."
    # `quote` CANNOT be scored by this arm, and that is an assertion rather
    # than a caveat. Its covering test IS `evidence_line`, the same rule
    # `quoting_objects` attributes with, so deleting the quoting objects
    # makes `any(...)` false by construction and every row would be a hit.
    # The arm's first published table printed `quote` at 100.0% and that
    # number measured nothing at all.
    try:
        coverage.leave_one_section_out(
            [quoting_object, other_object], loso_source, coverage.QUOTE
        )
    except ValueError:
        pass
    else:
        check(
            False,
            "leave-one-out must REFUSE a predicate that covers by the same "
            "rule it attributes by -- scoring `quote` there yields 100% by "
            "construction and reads as a result",
        )

    loso = coverage.leave_one_section_out(
        [quoting_object, other_object], loso_source, coverage.OVERLAP
    )
    by_heading = {row.heading: row for row in loso}
    check(
        "## Storage" in by_heading,
        "a section a supplied object quotes verbatim must produce a "
        f"leave-one-out row (got {sorted(by_heading)})",
    )
    storage = by_heading.get("## Storage")
    if storage is not None:
        check(
            storage.quoting == 1,
            "exactly the one object quoting `## Storage` must be attributed to "
            f"it (got {storage.quoting})",
        )
        check(
            storage.covered_before,
            "`## Storage` must be COVERED before its own quoting object is "
            "removed, or the row measures nothing",
        )
        check(
            storage.remaining == 1,
            "removing `## Storage`'s one quoting object must leave the other "
            f"object behind (got {storage.remaining})",
        )
        check(
            storage.named_after,
            "with its only quoting object removed, `overlap` must name "
            "`## Storage` -- the floor the arm rests on, and NOT a tautology "
            "here: `overlap` scores content-word share, so the remaining "
            "object could have kept the section covered",
        )

    # ------------------------------------------------------------------
    # The SECOND swept constant (#793). `OVERLAP_MIN_CONTENT_WORDS` gates
    # which sections are scorable at all, and every published number in
    # this directory was measured at its 4 without ever testing it. A
    # ladder over it has the same naming duty the threshold ladder has: the
    # swept value must reach the predicate's `name`, or a column lies about
    # which gate produced it.
    # ------------------------------------------------------------------
    check(
        coverage.overlap_predicate().name == "overlap",
        "the fully-default predicate must keep the bare registry name, which "
        "every committed number here was recorded under (got "
        f"{coverage.overlap_predicate().name!r})",
    )
    check(
        coverage.overlap_predicate(min_content_words=8).name == "overlap@0.5/8",
        "a swept WORD GATE must reach the name even when the threshold is the "
        f"default (got {coverage.overlap_predicate(min_content_words=8).name!r})",
    )
    check(
        coverage.overlap_predicate(0.2, min_content_words=8).name == "overlap@0.2/8",
        "both swept values must reach the name (got "
        f"{coverage.overlap_predicate(0.2, min_content_words=8).name!r})",
    )
    five_words = "alpha beta gamma delta epsilon"
    check(
        coverage.overlap_predicate(min_content_words=4).checkable(five_words)
        and not coverage.overlap_predicate(min_content_words=8).checkable(five_words),
        "the swept word gate must actually reach `checkable`, not just the "
        "name -- a five-content-word section is scorable at 4 and not at 8",
    )
    try:
        coverage.overlap_predicate(min_content_words=0)
    except ValueError:
        pass
    else:
        check(
            False,
            "a word gate below 1 must raise: at 0 every section is scorable "
            "including an empty one, which is vacuity rather than leniency",
        )

    # ------------------------------------------------------------------
    # The TALLY arithmetic, which produces every published leave-one-out
    # number and was asserted by nothing until a review said so. The three
    # regimes are built into one source so a single run exercises all of
    # them, and the expected counts are hand-derived below rather than read
    # back from the function under test.
    # ------------------------------------------------------------------
    tally_source = (
        "# Title\n"
        "Alpha beta gamma delta epsilon zeta.\n"
        "## Sec A\n"
        "The quick brown fox jumps over the lazy dog today.\n"
        "## Sec B\n"
        "Sailing vessels navigate treacherous northern waters annually.\n"
        "## Sec C\n"
        "The quick brown fox jumps over the lazy dog today.\n"
        "Meanwhile numerous unrelated tangential subjects occupy considerable "
        "additional discussion throughout the session.\n"
    )
    # `a_quote` quotes Sec A and Sec C verbatim; `b_quote` quotes Sec B;
    # `a_paraphrase` reorders Sec A's words so it quotes NOTHING while still
    # carrying every one of Sec A's seven content words.
    a_quote = "The quick brown fox jumps over the lazy dog today."
    b_quote = "Sailing vessels navigate treacherous northern waters annually."
    a_paraphrase = "Today the lazy dog and the quick brown fox jumps."
    tally_fixture = Fixture(
        name="tally",
        title="Tally",
        text=tally_source,
        must_fire=(),
        must_stay_quiet=(),
    )
    tally_record = {
        "fixture": "tally",
        "run": 1,
        "error": None,
        "objects": [
            {"type": "Concept", "title": "a", "body": a_quote, "description": ""},
            {"type": "Concept", "title": "b", "body": b_quote, "description": ""},
            {"type": "Concept", "title": "c", "body": a_paraphrase, "description": ""},
        ],
    }
    # Derived by hand from the four sections, and each one lands in a
    # DIFFERENT bucket, which is the whole point of this fixture:
    #   `# Title`  no object quotes it            -> no row at all
    #   `## Sec A` covered; its quoting object removed, but `a_paraphrase`
    #             still carries all seven content words                 -> BLIND
    #   `## Sec B` covered; nothing else carries its words              -> NAMED
    #   `## Sec C` 18 content words, only 7 shared, so overlap is 0.389
    #             and it is UNCOVERED before the arm runs         -> skipped
    (tally,) = leave_one_out_tallies([tally_record], tally_fixture, (coverage.OVERLAP,))
    check(
        (tally.trials, tally.named, tally.blind, tally.skipped_uncovered)
        == (2, 1, 1, 1),
        "the tally must count one NAMED, one BLIND and one already-uncovered "
        "section as skipped, over two trials (got "
        f"trials={tally.trials} named={tally.named} blind={tally.blind} "
        f"skipped={tally.skipped_uncovered})",
    )
    check(
        tally.trials == tally.named + tally.blind,
        "every trial must land in exactly one of NAMED or BLIND -- a count "
        f"that does not partition is arithmetic nobody can read (got "
        f"{tally.trials} != {tally.named} + {tally.blind})",
    )
    check(
        tally.hit_rate == 0.5,
        f"hit_rate must be named/trials (got {tally.hit_rate})",
    )
    # `None`, never `0.0`. The docstring says why and nothing proved it: a
    # signal that saw NOTHING and a signal that saw everything and named
    # none of it would otherwise print the same number.
    (empty_tally,) = leave_one_out_tallies([], tally_fixture, (coverage.OVERLAP,))
    check(
        empty_tally.hit_rate is None and empty_tally.trials == 0,
        "a tally with no trials must report hit_rate None rather than 0.0, "
        "which is also what a signal that named nothing would score (got "
        f"{empty_tally.hit_rate!r})",
    )
    # The DENOMINATOR must be auditable. Three of the four exclusions produce
    # no row at all, so without counts a table reading "5 trials" is the same
    # whether 1 section or 40 were dropped -- the silent-cap failure this
    # directory refuses everywhere else.
    tally_texts = [a_quote, b_quote, a_paraphrase]
    scan = coverage.leave_one_out_report(tally_texts, tally_source, coverage.OVERLAP)
    check(
        len(scan.rows) == 3,
        f"the scan must carry one row per scorable section (got {len(scan.rows)})",
    )
    check(
        scan.unquoted == 1,
        "`# Title` is checkable and nothing quotes it, so it must be counted "
        f"as an unquoted exclusion rather than vanish (got {scan.unquoted})",
    )
    check(
        (scan.unscorable, scan.total_removal) == (0, 0),
        "this source has no section the predicate cannot check and none whose "
        "ablation empties the object list (got "
        f"unscorable={scan.unscorable} total_removal={scan.total_removal})",
    )
    check(
        coverage.leave_one_section_out(tally_texts, tally_source, coverage.OVERLAP)
        == scan.rows,
        "the rows-only accessor must return exactly the report's rows -- two "
        "spellings of the same scan is how a published count and its audit "
        "drift apart",
    )
    check(
        (
            tally.excluded_unquoted,
            tally.excluded_unscorable,
            tally.excluded_total_removal,
        )
        == (1, 0, 0),
        "the tally must carry every exclusion count forward, not only the "
        f"already-uncovered one (got unquoted={tally.excluded_unquoted} "
        f"unscorable={tally.excluded_unscorable} "
        f"total_removal={tally.excluded_total_removal})",
    )

    # A record shape the NO DATA guard accepts must not then raise inside the
    # tally. The guard was the lenient one and it runs first, so a stale
    # stored file produced a raw KeyError where the honest line was written.
    malformed = [{"run": 1, "objects": []}]
    check(
        "NO DATA"
        in leave_one_out_table(malformed, (tally_fixture,), (coverage.OVERLAP,)),
        "a record the NO DATA guard accepts must not raise inside the tally -- "
        "the guard and its consumer must agree on what a usable run is",
    )

    check(
        "NO DATA" in leave_one_out_table([], (tally_fixture,), (coverage.OVERLAP,)),
        "a leave-one-out report over no runs at all must say NO DATA rather "
        "than print a table of zeroes that reads as a measurement",
    )

    refused_table = leave_one_out_table(
        [
            {
                "fixture": "thin",
                "run": 1,
                "error": None,
                "objects": [
                    {
                        "type": "Concept",
                        "title": "kept",
                        "body": "a sentence with several real content words in it",
                        "description": "",
                    }
                ],
            }
        ],
        (thin_fixture,),
        (coverage.QUOTE, coverage.OVERLAP),
    )
    check(
        any(
            "quote" in line and "NOT SCORABLE" in line
            for line in refused_table.splitlines()
        ),
        "the refused predicate's NAME and its refusal must share ONE LINE -- "
        "two independent substring searches over the whole table pass even "
        "when the refusal is printed against a different predicate, which is "
        f"the one thing this assertion exists to catch (got {refused_table!r})",
    )

    # Both new refusals in `main()` are user-facing failure paths, and a
    # failure path no test walks is one nobody can prove still fires. Each
    # must exit non-zero AND say which flag is wrong: the two were written
    # in the wrong order once, so `--ablate --leave-one-out` reported "no
    # scorable predicate" and sent the reader to fix an argument that was
    # not the problem.
    def refusal(argv: list[str]) -> str:
        import contextlib
        import io

        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                main(argv)
        except SystemExit as exit_code:
            return f"exit={exit_code.code} {err.getvalue()}"
        return f"NO REFUSAL {err.getvalue()}"

    both_arms = refusal(
        ["--rescore", str(COMMITTED_RUNS), "--ablate", "--leave-one-out"]
    )
    check(
        "exit=2" in both_arms and "neither subsumes the other" in both_arms,
        "--ablate with --leave-one-out must refuse and name the FLAG CONFLICT, "
        f"not some other complaint (got {both_arms!r})",
    )
    no_scorable = refusal(["--rescore", str(COMMITTED_RUNS), "--leave-one-out"])
    check(
        "exit=2" in no_scorable and "no scorable predicate" in no_scorable,
        "--leave-one-out selecting only `quote` must refuse rather than print "
        f"a table whose every row reads NOT SCORABLE (got {no_scorable!r})",
    )

    # The branch that actually PRINTS the arm, walked through `main` rather
    # than by calling the table directly: a refusal path that is tested and
    # a success path that is not leaves the wiring itself unproved.
    import contextlib
    import io

    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        rescore_exit = main(
            [
                "--rescore",
                str(COMMITTED_RUNS),
                "--leave-one-out",
                "--predicate",
                "overlap",
            ]
        )
    rendered = printed.getvalue()
    check(
        rescore_exit == 0
        and "LEAVE-ONE-SECTION-OUT" in rendered
        and "excluded:" in rendered,
        "the --rescore --leave-one-out branch must print the arm's table "
        f"including its exclusion counts and exit 0 (exit={rescore_exit}, "
        f"got {rendered[:200]!r})",
    )
    check(
        "NO DATA"
        in leave_one_out_table(
            [
                {
                    "fixture": "tally",
                    "run": 1,
                    "error": None,
                    "objects": tally_record["objects"],
                }
            ],
            (tally_fixture, thin_fixture),
            (coverage.OVERLAP,),
        ),
        "a fixture with no usable run must say NO DATA even when a SIBLING "
        "fixture has runs -- a table-wide guard prints zeroes for the empty "
        "one beside real numbers, which reads as a measurement",
    )

    # The positional-scoring rule the report docstring calls load-bearing:
    # headings are not unique, and a scan that keyed rows by heading would
    # score one `## Notes` against the other's objects.
    repeated = (
        "## Notes\n"
        "The quick brown fox jumps over the lazy dog today.\n"
        "## Notes\n"
        "Sailing vessels navigate treacherous northern waters annually.\n"
    )
    repeated_rows = coverage.leave_one_section_out(
        [a_quote, b_quote, a_paraphrase], repeated, coverage.OVERLAP
    )
    check(
        [r.heading for r in repeated_rows] == ["## Notes", "## Notes"]
        and [r.named_after for r in repeated_rows] == [False, True],
        "two sections sharing a heading must each get their OWN row and their "
        "OWN verdict -- the first stays covered by the paraphrase, the second "
        f"does not (got {[(r.heading, r.named_after) for r in repeated_rows]})",
    )

    # The early return in `select_predicates` is load-bearing: without it a
    # bare invocation grows an `overlap` column it never asked for. An
    # existing assertion covers that, and this one names the guard so a
    # reader of either finds the other.
    check(
        [p.name for p in select_predicates(None, None, None)] == ["quote"],
        "with nothing swept, no `overlap` column may be appended -- the "
        "`if not wanted and not gates` early return is what holds that (got "
        f"{[p.name for p in select_predicates(None, None, None)]})",
    )

    # The ladder is a CROSS PRODUCT once there are two swept constants, and
    # the column names have to prove it: 2 thresholds x 2 gates is 4
    # distinct predicates, and any collapse would silently drop a rung.
    crossed = select_predicates(None, [0.2, 0.25], [4, 8])
    check(
        [p.name for p in crossed]
        == ["overlap@0.2", "overlap@0.25", "overlap@0.2/8", "overlap@0.25/8"],
        "two thresholds and two word gates must produce four uniquely named "
        f"columns (got {[p.name for p in crossed]})",
    )
    check(
        [p.name for p in select_predicates(None, [0.2], None)] == ["overlap@0.2"],
        "a threshold ladder with no gate given must stay on the default gate "
        "and keep its committed column names (got "
        f"{[p.name for p in select_predicates(None, [0.2], None)]})",
    )

    # The row that must NOT exist. A section no object quotes has no
    # constructed loss to measure, so it cannot be scored -- and inventing a
    # row for it would count the model's own silence as a hit.
    check(
        "# Title" not in by_heading,
        "a section no object quotes must produce NO row: there is nothing to "
        "ablate, and scoring one would grade the run against what it should "
        "have found",
    )

    # Non-vacuity, stated as an assertion rather than as prose. If EVERY
    # object quotes the section, removing them leaves nothing at all, and a
    # predicate handed an empty list flags every section it can check. Such
    # a row is true by construction and must be excluded from the arm.
    only_quoting = coverage.leave_one_section_out(
        [quoting_object], loso_source, coverage.OVERLAP
    )
    check(
        all(row.remaining > 0 for row in only_quoting),
        "a row whose ablation empties the object list is true by construction "
        f"and must not be scored (got {[(r.heading, r.remaining) for r in only_quoting]})",
    )

    if failures:
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1
    print(f"self-test OK ({len(every)} predicate(s), no model calls)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
    parser.add_argument(
        "--predicate",
        action="append",
        default=None,
        metavar="NAME",
        help=(
            "covering predicate; repeat to score several over ONE sweep and "
            f"read them side by side, or {ALL_PREDICATES!r} for every one. "
            f"Known: {', '.join(coverage.PREDICATES)}. Default: quote (the "
            "refuted baseline the committed numbers were measured under)"
        ),
    )
    parser.add_argument(
        "--overlap-threshold",
        type=float,
        action="append",
        default=None,
        metavar="B",
        dest="overlap_threshold",
        help=(
            "score `overlap` at B instead of its shipped default; repeat for a "
            "whole ladder in ONE invocation. Each value becomes its own "
            "`overlap@B` column, so no column can be read under a threshold it "
            f"did not use. Default: {coverage.OVERLAP_COVERED_FRACTION} "
            f"(unchanged; the measured window is "
            f"{coverage.OVERLAP_MEASURED_WINDOW})"
        ),
    )
    parser.add_argument(
        "--ablate",
        action="store_true",
        help=(
            "the under-fire arm: cut each stored run's objects down to the "
            "ones the reported run produced, then score. Needs --rescore, and "
            "makes no model call"
        ),
    )
    parser.add_argument(
        "--overlap-min-words",
        type=int,
        action="append",
        default=None,
        metavar="W",
        dest="overlap_min_words",
        help=(
            "score `overlap` with a word gate of W instead of its shipped "
            f"{coverage.OVERLAP_MIN_CONTENT_WORDS}; repeat to sweep it. "
            "Crossed with every --overlap-threshold, because the gate moves "
            "the denominator and a share is not comparable across two gates. "
            "Each rung becomes its own `overlap@B/W` column"
        ),
    )
    parser.add_argument(
        "--leave-one-out",
        action="store_true",
        dest="leave_one_out",
        help=(
            "the under-fire arm that needs no adjudication: per section, "
            "delete the objects quoting it and ask the predicate again. "
            "Unlike --ablate it works on ANY source, including a --source "
            "transcript, and makes no model call"
        ),
    )
    parser.add_argument(
        "--source",
        type=pathlib.Path,
        default=None,
        help=(
            "measure one file from disk INSTEAD of the committed fixtures -- "
            "for the many-section regime they do not cover"
        ),
    )
    parser.add_argument("--source-title", default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        predicates = select_predicates(
            args.predicate, args.overlap_threshold, args.overlap_min_words
        )
    except KeyError as exc:
        parser.error(str(exc.args[0]))
    except ValueError as exc:
        parser.error(str(exc))

    if args.ablate and args.leave_one_out:
        # Both are under-fire arms and neither subsumes the other, so the
        # rescore branch would have to pick one -- and picking silently is
        # the failure this directory refuses everywhere else. Two
        # invocations, two tables, no arm dropped without saying so.
        parser.error(
            "--ablate and --leave-one-out are different under-fire arms and "
            "neither subsumes the other; run them as two invocations so both "
            "tables are printed"
        )

    if args.leave_one_out and all(p.covers_by_quoting for p in predicates):
        # The bare `--rescore FILE --leave-one-out` invocation selects
        # `quote` alone, which this arm refuses, so every row would read
        # NOT SCORABLE and the run would exit 0 having measured nothing.
        # The refusal is honest per row and useless as a whole report.
        parser.error(
            "--leave-one-out has no scorable predicate: "
            f"every selected predicate ({', '.join(p.name for p in predicates)}) "
            "covers by the same verbatim-quoting rule this arm attributes "
            "by. Pass --predicate "
            "overlap, or an --overlap-threshold ladder"
        )

    if args.ablate and args.rescore is None:
        # The arm is defined as a reconstruction FROM stored runs -- it keeps
        # the objects the reported run produced out of runs somebody already
        # paid for. Ablating a live sweep would spend GPU to build a loss
        # that is then constructed by deletion anyway.
        parser.error("--ablate scores a stored sweep: pass --rescore PATH too")

    fixtures = build_fixtures()
    if args.source is not None:
        if args.source_title is None:
            parser.error(
                "--source needs --source-title: extraction is prompted with it"
            )
        # No `must_fire`/`must_stay_quiet`: nobody has adjudicated this file's
        # sections, so every mechanical verdict that reads those lists is
        # inapplicable and the report says so rather than scoring against an
        # empty expectation. What this arm produces is the flagged COUNT per
        # run, which is the whole question in this regime.
        fixtures = (
            Fixture(
                name=args.source.name,
                title=args.source_title,
                text=args.source.read_text(),
                must_fire=(),
                must_stay_quiet=(),
            ),
        )

    if args.rescore is not None:
        # Named apart from the live `records` below: one is stored dicts read
        # back from disk, the other is `RunRecord`s this process built, and
        # reusing the name made them one variable of two types.
        stored_records = json.loads(args.rescore.read_text())
        if args.ablate:
            print(ablation_table(stored_records, fixtures, predicates))
            return 0
        if args.leave_one_out:
            print(leave_one_out_table(stored_records, fixtures, predicates))
            return 0
        print(summarize(stored_records, fixtures, predicates))
        return 0

    llm = OllamaClient(
        model=args.model,
        temperature=args.temperature,
        seed=args.seed,
        max_generation_tokens=_MAX_GENERATION_TOKENS,
    )
    print(
        f"model {args.model}, {args.runs} run(s) per fixture, "
        f"predicate(s) {', '.join(p.name for p in predicates)}\n"
    )

    records: list[RunRecord] = []
    for fixture in fixtures:
        for run in range(1, args.runs + 1):
            record = run_once(fixture, run, llm, args.model)
            records.append(record)
            if record.error:
                # Never printed in the same shape as a result. An errored run
                # has an empty object list and an empty flagged list, so the
                # success wording would render it as "0 object(s), flagged:
                # (none)" -- a backend failure reading as a clean run that
                # found nothing, which is the one confusion this line must
                # not create.
                print(
                    f"  {fixture.name} run {run}: FAILED after "
                    f"{record.latency_s}s -- {record.error}"
                )
                continue
            print(
                f"  {fixture.name} run {run}: {len(record.objects)} object(s), "
                f"{record.latency_s}s"
            )
            for predicate in predicates:
                scored = rescore(asdict(record), fixture, predicate)
                flagged = ", ".join(scored["uncovered"]) or "(none)"
                print(f"      [{predicate.name}] flagged: {flagged}")

    stored = [asdict(r) for r in records]
    if not stores_results(args.source):
        # A `--source` file is somebody's real corpus: the whole reason the
        # flag exists is that those transcripts carry names and addresses
        # and are not committed. Its extracted OBJECTS carry that content
        # onward, so persisting them into `results/` -- a tracked directory
        # inside the repo -- would launder private text into version
        # control by default. It already happened once, caught before any
        # push. The derived verdicts are what this arm is for; write those
        # by hand from the report and leave the objects on the terminal.
        print(
            "\nNOT STORED: --source runs are not written to results/. "
            "The report below carries the section verdicts; the objects "
            "belong to a corpus this repo does not hold."
        )
    else:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        path = RESULTS_DIR / f"runs-{stamp}-{args.model.replace(':', '-')}.json"
        path.write_text(json.dumps(stored, indent=2, ensure_ascii=False))
        print(f"\nstored {path}")
    print(summarize(stored, fixtures, predicates))
    if args.leave_one_out:
        # Printed from the SAME `stored` dicts the summary reads, live run
        # or not. That is what lets a --source transcript be measured
        # without ever being written to results/: the tally is counts, and
        # the objects behind them stay on this terminal.
        print(leave_one_out_table(stored, fixtures, predicates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
