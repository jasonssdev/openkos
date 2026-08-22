"""Does a per-section coverage signal separate on real extraction? (#793)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the
REAL pipeline -- `openkos.extraction.concept.extract_concept_union` over
`openkos.llm.ollama.OllamaClient` -- across the two sources #793 compares,
and asks ONE question:

  When `coverage.uncovered_sections` flags a section, is that a section the
  extraction actually lost, or is it flagging sections that produced
  objects perfectly well?

## It imports the shipped function, and that is the point

`evals/judge_cold_start/` had to grow its own copy of production's parse
chain and then pin, in its self-test, that the copy still agreed -- because
the thing it measures is buried inside `select()`. This signal is a leaf
taking plain strings, so there is nothing to copy: the probe calls
`openkos.extraction.coverage.uncovered_sections` itself. A number measured
here is a number about the code that ships, with no drift to guard against.

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
uv run python -u evals/section_coverage/run_section_coverage_probe.py --rescore <runs.json>
```

`--self-test` makes no model calls and needs no Ollama.
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


@dataclass
class RunRecord:
    """One extraction of one fixture, with what the signal said about it."""

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


def run_once(fixture: Fixture, run: int, llm: Any, model: str) -> RunRecord:
    """Extract once and record what the signal reports about the result."""
    checkable = [
        section.heading
        for section in coverage.split_sections(fixture.text)
        if coverage.is_quotable(section.body)
    ]
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
            f"{section!r} carries no line clearing the evidence floor, so it "
            "was never checked -- it cannot over-fire, and it cannot help"
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


def summarize(records: list[dict[str, Any]], fixtures: tuple[Fixture, ...]) -> str:
    lines: list[str] = ["", "=" * 72, "SECTION COVERAGE (#793)", "=" * 72]
    for fixture in fixtures:
        verdict = evaluate(fixture, records)
        lines.append("")
        lines.append(
            f"## {fixture.name} -- {verdict.verdict} ({verdict.ok_runs} ok runs)"
        )
        for reason in verdict.reasons:
            lines.append(f"   - {reason}")
        lines.append("")
        lines.append(f"   {'section':<45} {'flagged':>8}  expectation")
        for section, rate in verdict.flagged_rate:
            if section in fixture.must_fire:
                expect = "MUST FIRE"
            elif section in fixture.must_stay_quiet:
                expect = "must stay quiet"
            else:
                expect = "(unadjudicated)"
            lines.append(f"   {section:<45} {rate:>7.0%}  {expect}")
        lines.append("")
        shares = [
            run_share(r)
            for r in records
            if r["fixture"] == fixture.name and r["error"] is None
        ]
        if shares:
            lines.append(
                "   uncovered share of checkable text per run: " + render_shares(shares)
            )
            lines.append("")
        lines.append("   objects per run (adjudicate the under-fire half by hand):")
        for record in records:
            if record["fixture"] != fixture.name:
                continue
            if record["error"]:
                lines.append(f"     run {record['run']}: ERROR {record['error']}")
                continue
            titles = (
                ", ".join(f"{o['type']}:{o['title']}" for o in record["objects"])
                or "(none)"
            )
            flagged = ", ".join(record["uncovered"]) or "(none flagged)"
            lines.append(f"     run {record['run']}: {titles}")
            lines.append(f"             flagged: {flagged}")
    lines.append("")
    lines.append(
        "The under-fire half is NOT scored here: whether a run lost a section "
        "is a fact about that run's objects, printed above."
    )
    return "\n".join(lines)


def _self_test() -> int:
    """Prove the harness's own machinery with no model running.

    The FIRST assertion is that the signal can see a section AT ALL when the
    section's own text is handed to it as an object -- the floor every other
    reading rests on. A probe whose signal cannot cover a section under
    perfect input would report `VACUOUS` on any model and look like a
    finding.
    """
    failures: list[str] = []

    def check(condition: bool, why: str) -> None:
        if not condition:
            failures.append(why)

    for fixture in build_fixtures():
        sections = coverage.split_sections(fixture.text)
        perfect = [section.body for section in sections]
        check(
            coverage.uncovered_sections(perfect, fixture.text) == (),
            f"{fixture.name}: every section must be covered when its own body "
            f"is the object text (got {coverage.uncovered_sections(perfect, fixture.text)})",
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

    if failures:
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1
    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--rescore", type=pathlib.Path, default=None)
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
        print(summarize(json.loads(args.rescore.read_text()), fixtures))
        return 0

    llm = OllamaClient(
        model=args.model,
        temperature=args.temperature,
        seed=args.seed,
        max_generation_tokens=_MAX_GENERATION_TOKENS,
    )
    print(f"model {args.model}, {args.runs} run(s) per fixture\n")

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
            flagged = ", ".join(record.uncovered) or "(none)"
            print(
                f"  {fixture.name} run {run}: {len(record.objects)} object(s), "
                f"{record.latency_s}s, flagged: {flagged}"
            )

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
    print(summarize(stored, fixtures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
