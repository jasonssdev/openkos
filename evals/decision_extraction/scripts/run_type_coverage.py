"""Which of the nine OKF types can extraction actually reach? (#379 follow-up)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the REAL
pipeline -- `openkos.extraction.concept.extract_concept` over
`openkos.llm.ollama.OllamaClient` -- across the sources `build_sources.py`
produced, and answers ONE question:

  When a type never appears in a bundle, is that the classifier or the corpus?

## The question is a confound, and this exists to break it

Every end-to-end run measured so far used clean prose about software, and the
bundles came out ~79% `Concept`: only `Concept`, `Event`, `Person` and
`Procedure` ever appeared. `Decision`, `Entity`, `Organization`, `Place` and
`Project` never did.

That is not evidence of a defect. Course material about protocols has no
meetings, no companies, no named decisions -- so "the classifier cannot reach
these types" and "the corpus contains none of them" predict the identical
observation. No amount of re-running that corpus separates them.

## How AMI's `namedEntities` layer breaks it, and the ONE inference it licenses

AMI ships a human-annotated named-entity layer whose ontology includes
`PERSON`, `LOCATION` and `ORGANIZATION`. That is independent evidence about
what the SOURCE CONTAINS, written by AMI's annotators before this project
existed.

The inference is deliberately ONE-DIRECTIONAL, and the whole value of this
harness depends on not widening it:

- **Zero mentions + zero emitted** explains the absence. The corpus afforded
  nothing, so the classifier is not implicated.
- **Many mentions + zero emitted** is an UNEXPLAINED ABSENCE. The corpus
  explanation is ruled out; something in the pipeline is.

What it does NOT license is the reverse. A meeting mentioning "Project Manager"
forty times does not mean extraction SHOULD emit forty `Person` objects, or
even one. Named-entity mentions are not knowledge objects: a mention is a span
of words, an object is a subject worth a document. Scoring emitted objects
AGAINST mention counts would be a scorer that rewards over-production, and
`evals/extraction_cap/run_cap_eval.py` already argues at length why a scorer
that can be generous toward the hypothesis measures nothing.

So mentions are reported as an AFFORDANCE FLOOR, never as a target, and the
only verdict this harness issues is "explained" or "unexplained".

## What this cannot answer, stated rather than fudged

- **`Decision`** has no named-entity backing. Its affordance comes from AMI's
  abstractive `decisions` section, which `build_sources.py` wrote to
  `ground_truth/`. Weaker evidence -- a summary-level claim rather than a span
  annotation -- and labelled as such in the report.
- **`Project`** and **`Entity`** have neither. Their absence stays
  uninterpretable here; this harness reports the count and says so, rather
  than inventing a proxy.

## The cap is reported separately, because it is a different failure

`_MAX_OBJECTS_PER_SOURCE` is 6. A thirty-minute transcript can propose more, so
a type the model DID produce can be truncated away before it reaches a bundle.
That is not a classification failure and must not be counted as one, so every
row reports produced-vs-retained and the discarded titles are printed.

## Variance

Extraction is non-deterministic. A single run showing zero `Organization` is
one sample, not a finding. `--runs` defaults to 3 and every count is reported
across runs, with the union over runs used for the reachability verdict: a type
emitted even once is reachable.

Usage:

    uv run python -u evals/decision_extraction/scripts/run_type_coverage.py
    uv run python -u evals/decision_extraction/scripts/run_type_coverage.py --runs 5
    uv run python -u evals/decision_extraction/scripts/run_type_coverage.py --self-test
"""

from __future__ import annotations

import argparse
import collections
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from openkos.extraction.concept import _MAX_OBJECTS_PER_SOURCE, extract_concept
from openkos.llm.ollama import OllamaClient
from openkos.model.types import CLASSIFIABLE_TYPES

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES_DIR = ROOT / "sources"
GROUND_TRUTH_DIR = ROOT / "ground_truth"

NE_TO_OKF = {
    "PERSON": "Person",
    "LOCATION": "Place",
    "ORGANIZATION": "Organization",
}
"""The three NE types that map onto an OKF type without interpretation.

`PERSON` covers its whole subtree (`PARTICIPANT`, `PROJECT_MANAGER`, ...).
Everything else in AMI's ontology -- `TIMEX`, `NUMEX`, `ARTEFACT`, `COLOUR`,
`SHAPE`, `MATERIALS` -- has no OKF counterpart, and inventing one would put a
judgement call inside the evidence this harness exists to be independent of."""

_NITE_ID = "{http://nite.sourceforge.net/}id"


def _parse_xml(path: Path) -> ET.Element:
    """See `build_sources.py::_parse_xml` -- same corpus, same reasoning: the
    archive is checksum-pinned and this is a local manual harness."""
    return ET.parse(path).getroot()  # noqa: S314


@dataclass
class SourceResult:
    """Every run's outcome for one source file."""

    name: str
    meeting: str
    variant: str
    types_per_run: list[collections.Counter[str]] = field(default_factory=list)
    produced: list[int] = field(default_factory=list)
    retained: list[int] = field(default_factory=list)
    discarded: list[tuple[str, ...]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def emitted_types(self) -> set[str]:
        """The union over runs -- a type emitted even once is reachable."""
        out: set[str] = set()
        for counter in self.types_per_run:
            out |= set(counter)
        return out


def _ne_type_names(root: Path) -> dict[str, str]:
    """`{ne-type-id: OKF type}`, resolving AMI's ontology subtree inheritance.

    `PERSON` has children (`PARTICIPANT` -> `PROJECT_MANAGER`, ...), and an
    annotation points at whichever node the annotator chose, so a flat
    name-to-id map would miss every specialised mention."""
    paths = sorted(root.rglob("ne-types.xml"))
    if not paths:
        raise SystemExit(f"no ne-types.xml found under {root}")

    mapping: dict[str, str] = {}

    def walk(element: ET.Element, inherited: str | None) -> None:
        name = element.get("name", "")
        okf = NE_TO_OKF.get(name, inherited)
        node_id = element.get(_NITE_ID)
        if node_id and okf:
            mapping[node_id] = okf
        for child in element:
            walk(child, okf)

    walk(_parse_xml(paths[0]), None)
    return mapping


def named_entity_floor(root: Path, meeting: str) -> collections.Counter[str]:
    """Annotated mentions per OKF type for `meeting` -- the affordance floor.

    A COUNT OF MENTIONS, never a target object count. See the module docstring
    for the one inference this licenses."""
    type_map = _ne_type_names(root)
    counts: collections.Counter[str] = collections.Counter()
    for path in sorted(root.rglob(f"{meeting}.*.ne.xml")):
        for entity in _parse_xml(path):
            for child in entity:
                if child.get("role") != "type":
                    continue
                href = child.get("href") or ""
                fragment = href.split("#", 1)[-1]
                if not (fragment.startswith("id(") and fragment.endswith(")")):
                    continue
                okf = type_map.get(fragment[3:-1])
                if okf:
                    counts[okf] += 1
    return counts


def _decisions_afforded(meeting: str) -> int:
    path = GROUND_TRUTH_DIR / f"{meeting}.decisions.txt"
    try:
        return len(
            [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        )
    except OSError:
        return 0


def _split_name(path: Path) -> tuple[str, str]:
    """`IS1001a.transcript.txt` -> `("IS1001a", "transcript")`."""
    parts = path.name.split(".")
    return (parts[0], parts[1]) if len(parts) >= 3 else (parts[0], "source")


def run_source(path: Path, llm: object, runs: int) -> SourceResult:
    meeting, variant = _split_name(path)
    result = SourceResult(name=path.name, meeting=meeting, variant=variant)
    text = path.read_text(encoding="utf-8")
    for index in range(1, runs + 1):
        try:
            outcome = extract_concept(text, source_title=path.stem, llm=llm)  # type: ignore[arg-type]
        except Exception as exc:
            result.errors.append(f"run {index}: {type(exc).__name__}: {exc}")
            continue
        result.types_per_run.append(
            collections.Counter(obj.type for obj in outcome.objects)
        )
        result.produced.append(outcome.report.produced)
        result.retained.append(outcome.report.retained)
        result.discarded.append(tuple(outcome.report.discarded_titles))
        print(
            f"    run {index}: {outcome.report.retained} kept "
            f"of {outcome.report.produced} proposed"
        )
    return result


def render(
    results: list[SourceResult], floors: dict[str, collections.Counter[str]]
) -> str:
    lines: list[str] = ["", "=" * 72, "TYPE COVERAGE", "=" * 72, ""]

    for result in results:
        lines.append(f"## {result.name}")
        if result.errors:
            for err in result.errors:
                lines.append(f"  ERROR {err}")
        if not result.types_per_run:
            lines.append("  no successful run\n")
            continue
        totals: collections.Counter[str] = collections.Counter()
        for counter in result.types_per_run:
            totals.update(counter)
        n = len(result.types_per_run)
        lines.append(
            f"  {n} run(s); mean {sum(result.retained) / n:.1f} kept "
            f"of {sum(result.produced) / n:.1f} proposed "
            f"(cap {_MAX_OBJECTS_PER_SOURCE})"
        )
        for okf_type in sorted(CLASSIFIABLE_TYPES):
            count = totals.get(okf_type, 0)
            mark = " " if count else "-"
            lines.append(f"    {mark} {okf_type:<14} {count}")
        truncated = [d for d in result.discarded if d]
        if truncated:
            lines.append("  discarded by the cap (NOT a classification failure):")
            for group in truncated:
                lines.append(f"    {', '.join(group)}")
        lines.append("")

    lines += ["", "=" * 72, "AFFORDANCE: what the corpus contains", "=" * 72, ""]
    lines.append("Named-entity MENTIONS annotated by AMI. A floor on what the")
    lines.append("source affords -- never a target object count.")
    lines.append("")

    # Scoped PER MEETING, never unioned across the run. A global set would
    # make one meeting's emitted type suppress another meeting's genuine
    # absence: with `Person` emitted for meeting A, meeting B's seventeen
    # annotated mentions and zero objects would match neither branch below and
    # vanish from the verdict entirely -- the harness silently dropping the one
    # finding it exists to produce.
    emitted_by_meeting: dict[str, set[str]] = {}
    for result in results:
        emitted_by_meeting.setdefault(result.meeting, set())
        emitted_by_meeting[result.meeting] |= result.emitted_types

    verdicts: list[str] = []
    for meeting, floor in sorted(floors.items()):
        emitted = emitted_by_meeting.get(meeting, set())
        lines.append(f"  {meeting}:")
        for okf_type in sorted(NE_TO_OKF.values()):
            mentions = floor.get(okf_type, 0)
            lines.append(f"    {okf_type:<14} {mentions} mention(s)")
            if mentions and okf_type not in emitted:
                verdicts.append(
                    f"UNEXPLAINED ABSENCE: {okf_type} -- {meeting} contains "
                    f"{mentions} annotated mention(s), extraction emitted none"
                )
            elif not mentions and okf_type not in emitted:
                verdicts.append(
                    f"explained: {okf_type} absent, and {meeting} affords none"
                )
        decisions = _decisions_afforded(meeting)
        lines.append(f"    {'Decision':<14} {decisions} annotated (summary-level)")
        if decisions and "Decision" not in emitted:
            verdicts.append(
                f"UNEXPLAINED ABSENCE: Decision -- {meeting} has {decisions} "
                "annotated decision(s), extraction emitted none "
                "(weaker evidence: summary-level, not span-level)"
            )
        lines.append("")

    lines += ["=" * 72, "VERDICT", "=" * 72, ""]
    for verdict in verdicts or ["(nothing to report)"]:
        lines.append(f"  {verdict}")
    unmeasurable = sorted(
        set(CLASSIFIABLE_TYPES) - set(NE_TO_OKF.values()) - {"Decision"}
    )
    lines += [
        "",
        "  NOT MEASURABLE from this corpus (no independent affordance evidence):",
        f"    {', '.join(unmeasurable)}",
        "  Their counts above are observations, not verdicts.",
        "",
    ]
    return "\n".join(lines)


def _self_test() -> int:
    """Exercise the verdict logic with no model.

    TWO meetings, deliberately. A one-meeting fixture cannot tell a correctly
    per-meeting-scoped verdict from one unioned across the whole run, and that
    union was a real defect here: a type emitted for meeting A silently
    suppressed meeting B's genuine unexplained absence. A self-test that
    structurally cannot fail on the shipped code's actual aggregation is
    decoration."""
    emitted_a = SourceResult(name="A.transcript.txt", meeting="A", variant="transcript")
    emitted_a.types_per_run = [collections.Counter({"Concept": 3, "Person": 1})]
    emitted_a.produced, emitted_a.retained, emitted_a.discarded = [4], [4], [()]

    silent_b = SourceResult(name="B.transcript.txt", meeting="B", variant="transcript")
    silent_b.types_per_run = [collections.Counter({"Concept": 1})]
    silent_b.produced, silent_b.retained, silent_b.discarded = [1], [1], [()]

    floors = {
        "A": collections.Counter({"Person": 12, "Organization": 5}),
        "B": collections.Counter({"Person": 17}),
    }
    report = render([emitted_a, silent_b], floors)

    # Explicit checks rather than `assert`: this file ships outside `tests/`,
    # where the project's per-file-ignores do not exempt S101, and a self-test
    # silently voided by `python -O` would be worse than no self-test.
    expectations: list[tuple[bool, str]] = [
        (
            "UNEXPLAINED ABSENCE: Organization -- A" in report,
            "5 Organization mentions in A with none emitted must read unexplained",
        ),
        (
            "UNEXPLAINED ABSENCE: Person -- A" not in report,
            "A DID emit Person, so A's mentions must not read as unexplained",
        ),
        (
            "UNEXPLAINED ABSENCE: Person -- B" in report,
            "B emitted no Person despite 17 mentions -- the verdict must be "
            "scoped per meeting, so A's Person must not suppress B's absence",
        ),
        (
            "explained: Place absent, and A affords none" in report,
            "zero Place mentions and none emitted must read as explained",
        ),
    ]
    failures = [why for ok, why in expectations if not ok]
    if failures:
        print(report)
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1

    print(report)
    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument(
        "--ami-root",
        default=str(Path.cwd() / "data" / "ami" / "raw" / "manual"),
        help="unpacked AMI manual annotations (for the namedEntities layer)",
    )
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
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    sources = sorted(SOURCES_DIR.glob("*.txt"))
    if not sources:
        raise SystemExit(
            f"no sources in {SOURCES_DIR}\n"
            "Run build_sources.py first -- this harness measures extraction, "
            "it does not build its own inputs."
        )
    ami_root = Path(args.ami_root).expanduser().resolve()
    if not ami_root.is_dir():
        raise SystemExit(f"--ami-root is not a directory: {ami_root}")

    llm = OllamaClient(model=args.model, temperature=args.temperature, seed=args.seed)
    sampling = (
        f", temperature {args.temperature}" if args.temperature is not None else ""
    ) + (f", seed {args.seed}" if args.seed is not None else "")
    print(f"model {args.model}, {args.runs} run(s) per source{sampling}\n")

    results: list[SourceResult] = []
    for path in sources:
        print(f"  {path.name}")
        results.append(run_source(path, llm, args.runs))

    floors = {
        meeting: named_entity_floor(ami_root, meeting)
        for meeting in sorted({r.meeting for r in results})
    }
    print(render(results, floors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
