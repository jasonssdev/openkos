"""What collapses extraction to one object? (issue #522)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the
REAL pipeline -- `openkos.extraction.concept.extract_concept` over
`openkos.llm.ollama.OllamaClient` -- across the matched pairs in
`collapse_fixtures.py`, and answers ONE question per pair:

  When a source yields a single object, is that the one property this pair
  varies, or the content it carries?

Three axes ship today, and the harness is deliberately agnostic between them:

- **meeting register** (`producto`, `versioning`) -- #522's original claim,
  that a source recording a meeting collapses to the meeting.
- **announced multiplicity** (`anuncio`) -- that a source which does not
  enumerate its own topics collapses regardless of register.
- **short lesson framing** (`lesson`) -- that a short titled lesson collapses
  to a single object echoing its own umbrella title.

Plus one fixture that is NOT a pair and NOT a verdict: the NEGATIVE CONTROL,
a genuinely single-subject source where returning one object is CORRECT. It
runs unpaired, exactly as the positive control does, and answers the opposite
question -- what a change COSTS on material the collapse rules must not
touch. See its section in the report and `collapse_fixtures.py`.

## Why this exists before any prompt change

#522 requires that a fix be judged on whether it fixes the collapse. Nothing
in `evals/` could measure that when the issue was filed: `extraction_cap/`
holds expository prose that never collapses, so it can only witness a
regression, and `decision_extraction/` runs on AMI sources that are gitignored
and cost a 22 MB download plus tens of KB per run. A prompt change adopted
against either would be judged on an adjacent metric -- the exact trap that
already cost this project once, and that the loosened `Decision` rubric fell
into.

## The measurement, and the one inference it licenses

Every pair is the same facts twice: a TREATMENT arm (the one its hypothesis
predicts will collapse) and a FLOOR arm (see `collapse_fixtures.py`). Both
run `--runs` times, because extraction is non-deterministic and #522 records
a source yielding `1, 1, 5` across runs; a single run is a sample, not a
finding.

An arm COLLAPSES when a strict majority of its successful runs return exactly
one object. Majority rather than mean, because the failure is discrete -- the
model either enumerates the subjects or stops at one -- and a mean lets one
5-object run hide two collapses.

The inference is one-directional and stays that way:

- **Treatment collapses, floor does not.** The facts, the subjects and
  (within `MAX_LENGTH_SKEW`) the length are held fixed, so the pair's axis is
  implicated. This is the finding the probe exists to produce.
- **Both arms collapse.** The floor arm afforded no floor, so this pair says
  NOTHING about its axis. Reading the treatment arm alone here would be the
  unpaired reasoning #522 already has too much of.
- **Neither collapses.** The pair did not reproduce the defect at this model
  and size. Not evidence that the defect is absent -- evidence that this
  fixture does not reach it.

What it does NOT license is a target object count. The floor arm's count is
whatever the extractor found; it is a floor showing the text holds more than
one subject, never a number the treatment arm is scored against.

Usage:

    uv run python -u evals/extraction_collapse/run_collapse_probe.py
    uv run python -u evals/extraction_collapse/run_collapse_probe.py --runs 5
    uv run python -u evals/extraction_collapse/run_collapse_probe.py --self-test
"""

from __future__ import annotations

import argparse
import collections
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from collapse_fixtures import (  # noqa: E402
    AXES,
    FLOOR,
    HYPOTHESES,
    MAX_LENGTH_SKEW,
    NEGATIVE_CONTROL,
    NEGATIVE_CONTROL_OBJECTS,
    SHORT_SINGLE_TOPIC_BAND,
    SOURCES,
    STUB_TYPES,
    TREATMENT,
    PairedSource,
    length_skew,
    pairs,
    short_band_sources,
)

COLLAPSE_SIZE = 1
"""An arm's run collapsed when it returned exactly this many objects.

The same number as `NEGATIVE_CONTROL_OBJECTS`, and the opposite meaning: on a
paired arm one object is the defect, on the negative control it is the right
answer. Two names because one name would make a correct run read as a
collapse the first time somebody skimmed the code."""


def _normalize_title(title: str) -> str:
    """Strip, collapse internal whitespace, casefold.

    The same normalization `_drop_source_title_twins` applies, reimplemented
    here rather than imported: the harness must be able to report the twin
    shape with no model and no `openkos` import, which is what makes the
    self-test free."""
    return " ".join(title.split()).casefold()


@dataclass
class ArmResult:
    """Every run's outcome for one arm of one pair."""

    pair_id: str
    arm: str
    language: str
    retained: list[int] = field(default_factory=list)
    produced: list[int] = field(default_factory=list)
    """Objects the model PROPOSED, before the cap or backstop truncated
    them. Without it the probe cannot tell a source that yielded four from
    one that yielded fifteen and was cut to four."""
    types_per_run: list[collections.Counter[str]] = field(default_factory=list)
    titles_per_run: list[tuple[str, ...]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source_title: str = ""
    """The title this arm was extracted under. Recorded so `title_twin_runs`
    can be computed after the fact; empty when the caller did not supply it,
    in which case that count reports zero rather than guessing."""

    @property
    def runs(self) -> int:
        """Successful runs. A failed run is absent, never counted as zero
        objects -- a connection error is not a collapse."""
        return len(self.retained)

    @property
    def collapsed_runs(self) -> int:
        return sum(1 for n in self.retained if n == COLLAPSE_SIZE)

    @property
    def collapses(self) -> bool:
        """A STRICT majority of successful runs returned one object."""
        return self.runs > 0 and self.collapsed_runs * 2 > self.runs

    @property
    def correct_runs(self) -> int:
        """Runs that returned exactly `NEGATIVE_CONTROL_OBJECTS` objects.

        Arithmetically identical to `collapsed_runs`, and named twice on
        purpose. On a paired arm one object is the collapse; on a genuinely
        single-subject source it is the right answer. Only the negative
        control reads this."""
        return sum(1 for n in self.retained if n == NEGATIVE_CONTROL_OBJECTS)

    @property
    def title_twin_runs(self) -> int:
        """Runs in which SOME object's title restated the source title.

        The shape the floor in `_drop_source_title_twins` protects, and the
        reason the negative control is worth running at all: if the lone
        object here is a source-title twin, then the floor is the only thing
        standing between this document and `[]`."""
        if not self.source_title:
            return 0
        wanted = _normalize_title(self.source_title)
        return sum(
            1
            for titles in self.titles_per_run
            if any(_normalize_title(title) == wanted for title in titles)
        )

    @property
    def empty_runs(self) -> int:
        """Runs where the model itself returned `[]` (#524).

        Counted separately from `collapsed_runs` and never folded into it:
        collapsing to one object and returning nothing are different defects,
        and a single metric covering both could show one improving while the
        other got worse."""
        return sum(1 for n in self.retained if n == 0)

    @property
    def truncated_runs(self) -> int:
        """Runs where the cap or `_UNION_BACKSTOP` discarded proposals.

        Truncation is over-production that the retained count HIDES: 14b hit
        the backstop on the control while every verdict in the same report
        read NOT REPRODUCED."""
        return sum(
            1
            for kept, made in zip(self.retained, self.produced, strict=False)
            if made > kept
        )

    @property
    def stub_objects(self) -> int:
        """Objects of a type the prompt itself forbids for these fixtures.

        See `STUB_TYPES`: the people named in every pair are participants,
        and the anti-enumeration paragraph rules them out by name, so these
        need no adjudication to count as wrong."""
        return sum(
            count
            for types in self.types_per_run
            for name, count in types.items()
            if name in STUB_TYPES
        )

    @property
    def mean_objects(self) -> float:
        return sum(self.retained) / self.runs if self.runs else 0.0

    @property
    def collapsed_types(self) -> collections.Counter[str]:
        """The type of the lone object, over the runs that collapsed.

        #522's claim is not merely that one object comes back, but that the
        object IS the meeting. Reporting the type is what makes that claim
        falsifiable here."""
        out: collections.Counter[str] = collections.Counter()
        for count, types in zip(self.retained, self.types_per_run, strict=True):
            if count == COLLAPSE_SIZE:
                out.update(types)
        return out

    @property
    def emitted_types(self) -> set[str]:
        out: set[str] = set()
        for types in self.types_per_run:
            out |= set(types)
        return out


AXIS_IMPLICATED = "AXIS IMPLICATED"
NO_FLOOR = "NO FLOOR"
NOT_REPRODUCED = "NOT REPRODUCED"
INVERTED = "INVERTED"
NO_RESULT = "NO RESULT"


def verdict(
    treatment: ArmResult, floor: ArmResult, axis: str = "the varied axis"
) -> tuple[str, str]:
    """`(verdict, why)` for one pair. Pure; the self-test drives it.

    `axis` is what the pair varies. It belongs in every message because
    "the treatment arm collapsed" is not readable on its own -- the probe now
    carries two hypotheses (meeting register, announced multiplicity) and a
    verdict that cannot name which one it tested is a verdict about
    nothing."""
    if treatment.runs == 0 or floor.runs == 0:
        return NO_RESULT, "an arm had no successful run"
    if treatment.collapses and floor.collapses:
        return (
            NO_FLOOR,
            f"both arms collapsed, so the {floor.arm} arm affords no floor "
            f"and this pair says nothing about {axis}",
        )
    if treatment.collapses:
        return (
            AXIS_IMPLICATED,
            f"{axis}: the {treatment.arm} arm collapsed in "
            f"{treatment.collapsed_runs} of {treatment.runs} run(s) while "
            f"the {floor.arm} arm held {floor.mean_objects:.1f} object(s) on "
            "the same facts",
        )
    if floor.collapses:
        return (
            INVERTED,
            f"the {floor.arm} arm collapsed and the {treatment.arm} arm did "
            f"not -- the opposite of what {axis} predicts, and worth reading "
            "before any other row",
        )
    return (
        NOT_REPRODUCED,
        f"neither arm collapsed ({treatment.arm} "
        f"{treatment.mean_objects:.1f}, {floor.arm} "
        f"{floor.mean_objects:.1f} object(s)) -- this fixture does not reach "
        "the defect at this model and size",
    )


CONTROL_MODEL = "qwen3:8b"
"""The model the positive control's premise is pinned to.

#522 records `TS3005b.summary.txt` collapsing on `qwen3:8b`, and the SAME
table records `qwen3:14b` yielding 7 objects on it. So the control is
evidence about one model, not about the probe in general, and a sweep across
models must not read its silence there as the instrument being blind. It
also must not read it as an all-clear: on an unpinned model nothing in the
run establishes that this probe can see the defect at all."""


CONTROL_PROMPT_SHA = "295b498ef64340dc8f482f5a299c8df9e8fc53b53acda4d53309062bc67a9ddd"
"""The prompt identity the control's premise was recorded against.

The control says "this source collapses". That was measured under a
SPECIFIC prompt, and the whole point of the probe is to change that prompt.
So when a candidate actually works, the control stops collapsing and the
old note cried SENSITIVITY UNCONFIRMED -- reporting the best result of the
day as an instrument failure. Success and blindness were indistinguishable.

Pinning the prompt separates them: unchanged prompt plus a quiet control is
a genuine alarm; changed prompt plus a quiet control is the finding. Covers
BOTH channels, the system prompt and the user framing `_build_messages`
applies, because the language anchor moved only the second one."""


def current_prompt_sha() -> str:
    """Hash of both prompt channels, over a fixed canary source."""
    import hashlib

    from openkos.extraction import concept

    canary = concept._build_messages("probe canary text", "TS3005b.summary")
    blob = concept._SYSTEM_PROMPT + "\n\n" + str(canary[1]["content"])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def control_note(
    control: ArmResult | None,
    model: str = CONTROL_MODEL,
    prompt_sha: str = CONTROL_PROMPT_SHA,
) -> str:
    """How to read the positive control, or its absence.

    A probe that has never watched the defect fire cannot tell "framing is
    not the cause" from "this instrument is blind". The control is a source
    #522 already recorded as collapsing on `qwen3:8b`, so a run where it does
    NOT collapse says something about the probe, not about the fixtures --
    and every `NOT REPRODUCED` in the same report becomes unreadable until
    that is resolved."""
    if control is None:
        return (
            "positive control: not run (the AMI sources are gitignored; "
            "build them with decision_extraction/scripts/build_sources.py). "
            "Without it, a report of no collapses cannot be distinguished "
            "from a probe that cannot see one."
        )
    if control.runs == 0:
        errors = "; ".join(control.errors) or "no successful run"
        return f"positive control: SENSITIVITY UNCONFIRMED -- {errors}"
    if control.collapses:
        return (
            f"positive control: collapsed in {control.collapsed_runs} of "
            f"{control.runs} run(s), as #522 recorded. The probe can see the "
            "defect, so a verdict of NOT REPRODUCED elsewhere is about the "
            "fixture."
        )
    if model != CONTROL_MODEL:
        return (
            f"positive control: not applicable to {model} -- its premise is "
            f"pinned to {CONTROL_MODEL}, where #522 records this source "
            f"collapsing; the same table records larger models yielding "
            f"several objects on it, so holding at {control.mean_objects:.1f} "
            "here is expected rather than suspicious. This run therefore "
            f"carries no sensitivity evidence for {model}: nothing in it "
            "establishes that the probe can see the defect at this model, so "
            "read the verdicts below as observations, not as absence."
        )
    if prompt_sha != CONTROL_PROMPT_SHA:
        return (
            f"positive control: the prompt has CHANGED since the control's "
            f"premise was recorded, and the known-collapsing source now "
            f"holds at {control.mean_objects:.1f} object(s) over "
            f"{control.runs} run(s). That is the headline: this candidate "
            "moved the case #522 was built on. It is NOT a blind probe -- "
            "but it does mean nothing in this run re-confirms sensitivity, "
            "so weigh it against the over-enumeration and drift sections "
            "rather than on the collapse verdicts alone."
        )
    return (
        f"positive control: SENSITIVITY UNCONFIRMED -- the known-collapsing "
        f"source held at {control.mean_objects:.1f} object(s) over "
        f"{control.runs} run(s) under the UNCHANGED prompt. Either the "
        "defect is not firing today or this probe cannot see it; until that "
        "is settled, read no other verdict in this report."
    )


NEGATIVE_HELD = "no false positive"
NEGATIVE_FALSE_POSITIVE = "FALSE POSITIVE"
NEGATIVE_SPLIT = "SPLIT"
NEGATIVE_NO_RESULT = "NO RESULT"


def negative_control_note(negative: ArmResult | None) -> str:
    """How to read the negative control, or its absence.

    Every verdict above this reads one object as a collapse. The negative
    control is the one source in this harness where one object is CORRECT,
    so it answers the question the pairs structurally cannot: what does a
    candidate change COST on material the collapse rules must leave alone?

    The failure it watches for is `[]` or a dropped lone object, NOT a count
    of one. That is why it never goes through `verdict()`: `verdict()` maps
    `retained == 1` to a collapse by construction, and running this fixture
    through it would report the right answer as the defect."""
    if negative is None:
        return (
            "negative control: not run. Nothing in this report then says what "
            "a candidate costs on a source whose correct answer is exactly "
            f"{NEGATIVE_CONTROL_OBJECTS} object -- the only false positive "
            "the collapse verdicts cannot see."
        )
    if negative.runs == 0:
        errors = "; ".join(negative.errors) or "no successful run"
        return f"negative control: {NEGATIVE_NO_RESULT} -- {errors}"
    if negative.empty_runs:
        return (
            f"negative control: {NEGATIVE_FALSE_POSITIVE} -- "
            f"{negative.empty_runs} of {negative.runs} run(s) returned [] on "
            "a genuinely single-subject source, where one object is the right "
            "answer. That is silent data loss on healthy material, and it "
            "outweighs every collapse verdict above: a candidate that empties "
            "this document has traded one defect for a worse one."
        )
    if negative.correct_runs * 2 > negative.runs:
        return (
            f"negative control: {NEGATIVE_HELD} -- held at "
            f"{NEGATIVE_CONTROL_OBJECTS} object in {negative.correct_runs} of "
            f"{negative.runs} run(s), which is CORRECT here, and "
            f"{negative.title_twin_runs} of {negative.runs} run(s) restated "
            "the source title -- the twin shape the floor in "
            "_drop_source_title_twins keeps precisely because dropping it "
            "would emit []."
        )
    return (
        f"negative control: {NEGATIVE_SPLIT} -- a majority of "
        f"{negative.runs} run(s) returned more than "
        f"{NEGATIVE_CONTROL_OBJECTS} object (mean "
        f"{negative.mean_objects:.1f}) on a genuinely single-subject source. "
        "That is not the false positive this control exists to catch, which "
        "is []; it does mean the baseline here is not one object, so read any "
        "later [] rate against this run and not against a clean 1.0."
    )


def _arm_line(result: ArmResult) -> list[str]:
    lines = [
        f"    {result.arm:<8} {result.runs} run(s), "
        f"objects {result.retained or '-'}, "
        f"mean {result.mean_objects:.1f}, "
        f"collapsed {result.collapsed_runs}/{result.runs}, "
        f"empty {result.empty_runs}/{result.runs}"
    ]
    if result.emitted_types:
        seen = ", ".join(sorted(result.emitted_types))
        lines.append(f"             types seen: {seen}")
    if result.collapsed_types:
        lone = ", ".join(
            f"{name} x{count}" for name, count in sorted(result.collapsed_types.items())
        )
        lines.append(f"             the lone object was: {lone}")
    for err in result.errors:
        lines.append(f"             ERROR {err}")
    return lines


def render(
    results: dict[str, tuple[ArmResult, ArmResult]],
    control: ArmResult | None = None,
    model: str = CONTROL_MODEL,
    prompt_sha: str = CONTROL_PROMPT_SHA,
    negative: ArmResult | None = None,
) -> str:
    """The whole report, from the collected arms."""
    lines: list[str] = ["", "=" * 72, "COLLAPSE PROBE (#522)", "=" * 72, ""]
    lines.append(control_note(control, model, prompt_sha))
    if control is not None and control.retained:
        lines.append(f"  control objects per run: {control.retained}")
    lines.append("")

    for pair_id, (treatment, floor) in results.items():
        axis = AXES.get(pair_id, "the varied axis")
        call, why = verdict(treatment, floor, axis)
        lines.append(f"## {pair_id} [{treatment.language}]  varies: {axis}")
        hypothesis = HYPOTHESES.get(pair_id)
        if hypothesis:
            lines.append(f"    tests: {hypothesis}")
        lines += _arm_line(treatment)
        lines += _arm_line(floor)
        lines.append(f"    -> {call}: {why}")
        lines.append("")

    # Unconditional, and placed directly under the verdicts it reframes: every
    # row above reads one object as a collapse, and this is the one source
    # here where one object is right. A candidate that fixes the rows above by
    # emptying this document is a regression the pairs cannot express.
    lines += ["", "=" * 72, "NEGATIVE CONTROL -- ONE OBJECT IS CORRECT", "=" * 72, ""]
    lines.append(
        "A genuinely single-subject source (the mcp-launch shape: a title "
        "naming one thing, a body about that one thing). Run unpaired, like "
        "the positive control, because a pair's floor arm is multi-subject by "
        "construction and cannot host this question. The failure watched for "
        "here is [] or a dropped lone object -- never a count of one."
    )
    lines.append("")
    lines.append(f"  {negative_control_note(negative)}")
    if negative is not None:
        # NOT `_arm_line`, which reads "collapsed 3/3". Here that same number
        # is the arm being RIGHT 3 times, and a line that calls it a collapse
        # inside a section explaining that one object is correct is how a
        # reader ends up filing the control as a defect.
        lines.append(
            f"    {negative.arm:<8} {negative.runs} run(s), "
            f"objects {negative.retained or '-'}, "
            f"mean {negative.mean_objects:.1f}, "
            f"correct {negative.correct_runs}/{negative.runs}, "
            f"empty {negative.empty_runs}/{negative.runs}, "
            f"source-title twins {negative.title_twin_runs}/{negative.runs}"
        )
        if negative.emitted_types:
            negative_types = ", ".join(sorted(negative.emitted_types))
            lines.append(f"             types seen: {negative_types}")
        for err in negative.errors:
            lines.append(f"             ERROR {err}")
    lines.append("")

    # Its own section, and unconditional, for the same reason the empty
    # section is: the 14b run reported NOT REPRODUCED on every pair while
    # emitting participant stubs and hitting the backstop. A probe that
    # measures one failure mode and stays silent on its opposite will bless a
    # prompt change that trades one for the other.
    lines += ["", "=" * 72, "OVER-ENUMERATION (#522)", "=" * 72, ""]
    lines.append(
        "The opposite failure: enumerating every named entity instead of the "
        "source's subjects. Stub counts are objects of a type the prompt's "
        "own anti-enumeration paragraph forbids for these fixtures "
        "(see STUB_TYPES); truncation means the cap or backstop discarded "
        "proposals, which the retained count hides."
    )
    lines.append("")
    stubs = 0
    truncated = 0
    for pair_id, arms in results.items():
        for arm in arms:
            stubs += arm.stub_objects
            truncated += arm.truncated_runs
            if arm.stub_objects or arm.truncated_runs:
                lines.append(
                    f"  {pair_id} [{arm.arm}]: {arm.stub_objects} stub "
                    f"object(s), {arm.truncated_runs} truncated run(s), "
                    f"mean {arm.mean_objects:.1f}"
                )
    for label, unpaired in (
        ("positive control", control),
        ("negative control", negative),
    ):
        if unpaired is None:
            continue
        stubs += unpaired.stub_objects
        truncated += unpaired.truncated_runs
        if unpaired.stub_objects or unpaired.truncated_runs:
            lines.append(
                f"  {label}: {unpaired.stub_objects} stub object(s), "
                f"{unpaired.truncated_runs} truncated run(s), "
                f"mean {unpaired.mean_objects:.1f}"
            )
    if stubs or truncated:
        lines.append("")
        lines.append(
            f"  {stubs} stub object(s) and {truncated} truncated run(s) "
            "overall -- the model contradicting an instruction it was given, "
            "which needs no adjudication to score."
        )
    else:
        lines.append("  none -- no stub objects, no truncated runs.")
    lines.append("")

    # Its own section, and unconditional: an empty section that reads "none"
    # is the point. These were found by reading raw per-run lines, which is
    # how a defect survives a harness that technically recorded it.
    lines += ["", "=" * 72, "EMPTY EXTRACTIONS (#524)", "=" * 72, ""]
    lines.append(
        "The prompt's positive default says a source with substantive content "
        "yields AT LEAST ONE object, and that [] is a last resort for blank or "
        "unintelligible input. Every arm below is substantive."
    )
    lines.append("")
    empties = 0
    total = 0
    for pair_id, arms in results.items():
        for arm in arms:
            empties += arm.empty_runs
            total += arm.runs
            if arm.empty_runs:
                lines.append(
                    f"  {pair_id} [{arm.arm}]: {arm.empty_runs} of "
                    f"{arm.runs} run(s) returned []"
                )
    # The negative control is counted here too, not only in its own section:
    # an empty there is the worst empty in the report, and a reader who came
    # for #524 must not have to know the section exists to find it.
    for label, unpaired in (
        ("positive control", control),
        ("negative control", negative),
    ):
        if unpaired is None:
            continue
        empties += unpaired.empty_runs
        total += unpaired.runs
        if unpaired.empty_runs:
            lines.append(
                f"  {label}: {unpaired.empty_runs} of "
                f"{unpaired.runs} run(s) returned []"
            )
    if empties:
        rate = empties / total if total else 0.0
        lines.append("")
        lines.append(
            f"  {empties} of {total} run(s) overall ({rate:.0%}) -- silent "
            "data loss: in the real pipeline such a source contributes "
            "nothing to the knowledge base."
        )
    else:
        lines.append("  none -- every run returned at least one object.")
    lines.append("")

    # Reported per pair AND unioned, because they answer different questions:
    # the per-pair rows above say whether an axis is implicated, this says
    # whether `Decision` is reachable AT ALL on material that contains one --
    # the #521 thread, which a per-pair reading would leave scattered.
    lines += ["", "=" * 72, "TYPE REACH (#521)", "=" * 72, ""]
    lines.append(
        "Every pair carries a committed choice with its rationale. Whether "
        "any arm emits a Decision is therefore about the extractor, not the "
        "corpus."
    )
    lines.append("")
    # Unioned by ROLE, not by label: the labels differ across axes
    # (`meeting`/`flat` against `unannounced`/`announced`), so grouping by
    # label would split one question into four half-answered ones.
    for role, index in ((TREATMENT, 0), (FLOOR, 1)):
        seen: set[str] = set()
        for arms in results.values():
            seen |= arms[index].emitted_types
        mark = "yes" if "Decision" in seen else "NO"
        lines.append(f"  {role:<10} Decision emitted: {mark}")
        lines.append(f"             all types: {', '.join(sorted(seen)) or '-'}")
    lines.append("")

    calls = collections.Counter(
        verdict(t, f, AXES.get(pair_id, ""))[0] for pair_id, (t, f) in results.items()
    )
    lines += ["", "=" * 72, "VERDICT", "=" * 72, ""]
    for call, count in sorted(calls.items()):
        lines.append(f"  {call}: {count} pair(s)")
    if not calls.get(AXIS_IMPLICATED):
        lines.append("")
        lines.append(
            "  No pair implicated its axis. That is NOT evidence the collapse "
            "is absent -- it is evidence these fixtures did not reach it at "
            "this model and size."
        )
    lines.append("")
    return "\n".join(lines)


def run_arm(
    source: PairedSource, llm: object, runs: int, *, union_judge: bool = False
) -> ArmResult:
    """Extract from one arm `runs` times.

    `union_judge` (#456) selects `extract_concept_union` -- union-of-runs plus
    selector-judge -- over the single-pass `extract_concept`. It matters for
    #524 specifically: `DEFAULT_UNION_JUDGE` is `True`, so the union path is
    what production actually runs, and a single empty reply there may be
    covered by the other run. An empty rate measured on the single-pass path
    is therefore an upper bound on the shipped configuration, not its rate.

    `source_title` comes from the fixture rather than a filename: the title is
    part of what the meeting arm IS, and `extract_concept` feeds it to the
    twin-dropping pass, so borrowing one arm's title for the other would leak
    the framing this probe is trying to isolate."""
    from openkos.extraction.concept import extract_concept, extract_concept_union

    extractor = extract_concept_union if union_judge else extract_concept
    result = ArmResult(
        pair_id=source.pair_id,
        arm=source.arm,
        language=source.language,
        source_title=source.source_title,
    )
    for index in range(1, runs + 1):
        try:
            outcome = extractor(
                source.text,
                source_title=source.source_title,
                llm=llm,  # type: ignore[arg-type]
            )
        except Exception as exc:
            result.errors.append(f"run {index}: {type(exc).__name__}: {exc}")
            continue
        result.retained.append(outcome.report.retained)
        result.produced.append(outcome.report.produced)
        result.types_per_run.append(
            collections.Counter(obj.type for obj in outcome.objects)
        )
        result.titles_per_run.append(tuple(obj.title for obj in outcome.objects))
        print(
            f"    run {index}: {outcome.report.retained} kept "
            f"of {outcome.report.produced} proposed  "
            f"{list(result.titles_per_run[-1])}"
        )
    return result


def _axis_guard_rejects_undeclared() -> bool:
    """Does `pairs()` refuse a pair whose axis was never declared?

    Exercised against the REAL `AXES` mapping with one entry removed, rather
    than a stand-in: the guard is only worth anything if it fires on the data
    the probe actually ships."""
    import collapse_fixtures

    victim = next(iter(collapse_fixtures.AXES))
    saved = collapse_fixtures.AXES.pop(victim)
    try:
        pairs()
    except ValueError:
        return True
    else:
        return False
    finally:
        collapse_fixtures.AXES[victim] = saved


def _self_test() -> int:
    """Exercise the verdict logic with no model.

    THREE pairs, deliberately. A single collapsing pair cannot tell a paired
    verdict from one that reads the meeting arm alone, and reading the meeting
    arm alone is the specific error this probe was built to stop making: the
    `both` pair below collapses on BOTH arms and must NOT be reported as
    framing, while `framed` must."""
    made: dict[str, tuple[ArmResult, ArmResult]] = {}

    framed_meeting = ArmResult("framed", "meeting", "ES")
    framed_meeting.retained = [1, 1, 1]
    framed_meeting.types_per_run = [collections.Counter({"Event": 1})] * 3
    framed_flat = ArmResult("framed", "flat", "ES")
    framed_flat.retained = [3, 3, 2]
    framed_flat.types_per_run = [
        collections.Counter({"Concept": 2, "Decision": 1}),
        collections.Counter({"Concept": 2, "Decision": 1}),
        collections.Counter({"Concept": 1, "Decision": 1}),
    ]
    made["framed"] = (framed_meeting, framed_flat)

    both_meeting = ArmResult("both", "meeting", "EN")
    both_meeting.retained = [1, 1]
    both_meeting.types_per_run = [collections.Counter({"Event": 1})] * 2
    both_flat = ArmResult("both", "flat", "EN")
    both_flat.retained = [1, 1]
    both_flat.types_per_run = [collections.Counter({"Concept": 1})] * 2
    made["both"] = (both_meeting, both_flat)

    # One EMPTY run here on purpose (#524): the report must surface it even
    # in a pair whose verdict is unremarkable, because that is exactly where
    # an empty hides.
    quiet_meeting = ArmResult("quiet", "meeting", "EN")
    quiet_meeting.retained = [0, 4, 3]
    quiet_meeting.types_per_run = [
        collections.Counter(),
        collections.Counter({"Concept": 4}),
        collections.Counter({"Concept": 3}),
    ]
    quiet_flat = ArmResult("quiet", "flat", "EN")
    quiet_flat.retained = [4, 4]
    quiet_flat.types_per_run = [collections.Counter({"Concept": 4})] * 2
    made["quiet"] = (quiet_meeting, quiet_flat)

    # Reproduces the 14b shape (#522): many objects, three of them Person
    # stubs for participants merely named, plus a run the backstop truncated.
    over_enumerating_arm = ArmResult("quiet", "flat", "EN")
    over_enumerating_arm.retained = [4, 4]
    over_enumerating_arm.produced = [4, 7]
    over_enumerating_arm.types_per_run = [
        collections.Counter({"Concept": 3, "Person": 1}),
        collections.Counter({"Concept": 2, "Person": 2}),
    ]
    made["quiet"] = (quiet_meeting, over_enumerating_arm)

    # The negative control, where ONE object is the CORRECT answer. Its four
    # readings are driven with no model here because the whole value of the
    # control is that they are distinguishable BEFORE any of them costs a run.
    held = ArmResult(
        "negative-control",
        "single-subject",
        "EN",
        source_title="Nightly Index Rebuild",
    )
    held.retained = [1, 1, 1]
    held.types_per_run = [collections.Counter({"Event": 1})] * 3
    held.titles_per_run = [("Nightly Index Rebuild",)] * 3

    emptied = ArmResult("negative-control", "single-subject", "EN")
    emptied.retained = [0, 1, 1]
    emptied.types_per_run = [
        collections.Counter(),
        collections.Counter({"Event": 1}),
        collections.Counter({"Event": 1}),
    ]

    split = ArmResult("negative-control", "single-subject", "EN")
    split.retained = [3, 3, 1]
    split.types_per_run = [
        collections.Counter({"Concept": 3}),
        collections.Counter({"Concept": 3}),
        collections.Counter({"Event": 1}),
    ]

    report = render(made)
    negative_report = render(made, negative=held)

    # Explicit checks rather than `assert`: this file ships outside `tests/`,
    # where the project's per-file-ignores do not exempt S101, and a self-test
    # silently voided by `python -O` would be worse than no self-test.
    expectations: list[tuple[bool, str]] = [
        (
            verdict(framed_meeting, framed_flat)[0] == AXIS_IMPLICATED,
            "a collapsing meeting arm against a holding flat arm is the "
            "finding, and must read FRAMING IMPLICATED",
        ),
        (
            verdict(both_meeting, both_flat)[0] == NO_FLOOR,
            "BOTH arms collapsed, so the pair affords no floor -- reading the "
            "meeting arm alone here is the unpaired error this probe exists "
            "to prevent",
        ),
        (
            verdict(quiet_meeting, quiet_flat)[0] == NOT_REPRODUCED,
            "neither arm collapsed, so the fixture did not reach the defect",
        ),
        (
            verdict(ArmResult("x", "arm", "EN"), quiet_flat)[0] == NO_RESULT,
            "an arm with no successful run is not a collapse to zero objects",
        ),
        (
            framed_meeting.collapsed_types["Event"] == 3,
            "the lone object's TYPE must be reported: #522 claims the object "
            "IS the meeting, and only the type makes that falsifiable",
        ),
        (
            not ArmResult("x", "arm", "EN", retained=[1, 5, 5]).collapses,
            "one collapse in three runs is not a majority",
        ),
        (
            ArmResult("x", "arm", "EN", retained=[1, 1, 5]).collapses,
            "two collapses in three runs is a majority",
        ),
        (
            not ArmResult("x", "arm", "EN", retained=[1, 5]).collapses,
            "an even split is not a STRICT majority, and must not be read as "
            "a collapse",
        ),
        (
            "SENSITIVITY UNCONFIRMED"
            in control_note(ArmResult("ctl", "control", "EN", retained=[4, 5, 4])),
            "a positive control that does NOT collapse means the probe never "
            "observed the defect fire, and every NOT REPRODUCED below it is "
            "then unreadable -- that must be stated, not implied",
        ),
        (
            "SENSITIVITY UNCONFIRMED"
            not in control_note(ArmResult("ctl", "control", "EN", retained=[1, 1, 5])),
            "a control that collapses in a majority of runs confirms the "
            "probe can see the defect",
        ),
        (
            "not run" in control_note(None),
            "an absent control must read as absent, never as confirmation",
        ),
        (
            "SENSITIVITY UNCONFIRMED"
            not in control_note(
                ArmResult("ctl", "control", "EN", retained=[4, 5, 4]),
                prompt_sha="0" * 64,
            ),
            "a control that stops collapsing under a CHANGED prompt is the "
            "headline result, not a blind probe -- crying unconfirmed there "
            "makes every successful experiment look like an instrument "
            "failure, which is what happened to the enumerate-first run",
        ),
        (
            "moved the case"
            in control_note(
                ArmResult("ctl", "control", "EN", retained=[4, 5, 4]),
                prompt_sha="0" * 64,
            ),
            "and it must say so positively, so the result is not merely "
            "un-warned but reported",
        ),
        (
            "SENSITIVITY UNCONFIRMED"
            in control_note(
                ArmResult("ctl", "control", "EN", retained=[4, 5, 4]),
                prompt_sha=CONTROL_PROMPT_SHA,
            ),
            "under the UNCHANGED prompt a non-collapsing control is still a "
            "genuine alarm, and that must survive the fix",
        ),
        (
            "SENSITIVITY UNCONFIRMED"
            not in control_note(
                ArmResult("ctl", "control", "EN", retained=[8, 9, 8]),
                model="qwen3:14b",
            ),
            "the control is pinned to one model: on a DIFFERENT model it not "
            "collapsing is expected, and crying SENSITIVITY UNCONFIRMED makes "
            "the harness invalidate its own report on every model sweep",
        ),
        (
            "no sensitivity evidence"
            in control_note(
                ArmResult("ctl", "control", "EN", retained=[8, 9, 8]),
                model="qwen3:14b",
            ),
            "but a non-applicable control is not an all-clear either -- "
            "nothing then establishes the probe can see the defect at that "
            "model, and the report must say so",
        ),
        (
            "SENSITIVITY UNCONFIRMED"
            in control_note(
                ArmResult("ctl", "control", "EN", retained=[8, 9, 8]),
                model=CONTROL_MODEL,
            ),
            "on the pinned model a non-collapsing control IS unconfirmed "
            "sensitivity, and that must survive the model-awareness fix",
        ),
        (
            ArmResult(
                "x", "arm", "EN", retained=[12, 5], produced=[15, 5]
            ).truncated_runs
            == 1,
            "a run whose proposals were truncated by the backstop is "
            "over-production, and the probe was blind to it: 14b hit the cap "
            "while every verdict read NOT REPRODUCED",
        ),
        (
            over_enumerating_arm.stub_objects == 3,
            "objects of a type the prompt's own anti-enumeration paragraph "
            "forbids for this fixture must be counted -- 14b emitted Ana, "
            "Marta and Luis as Person stubs and no metric noticed",
        ),
        (
            "OVER-ENUMERATION (#522)" in report,
            "over-production needs its own section: a prompt change that "
            "fixes 8b by wrecking 14b would otherwise read as a clean win",
        ),
        (
            "meeting register"
            in verdict(framed_meeting, framed_flat, "meeting register")[1],
            "the verdict must NAME the axis it tested: the probe carries two "
            "hypotheses now, so 'the treatment arm collapsed' alone is a "
            "verdict about nothing",
        ),
        (
            _axis_guard_rejects_undeclared(),
            "a pair with no declared axis must raise, not silently report a "
            "verdict nobody can attribute to a hypothesis",
        ),
        (
            all(length_skew(pair) <= MAX_LENGTH_SKEW for pair in pairs().values()),
            "every shipped pair must sit within the length tolerance, checked "
            "here rather than only at run time so a fixture edit cannot reach "
            "a measurement first",
        ),
        (
            NEGATIVE_HELD in negative_control_note(held),
            "a single-subject source returning its one object is the CORRECT "
            "answer and must read as such -- reporting it as a collapse is "
            "the false positive this control exists to prevent",
        ),
        (
            NEGATIVE_FALSE_POSITIVE in negative_control_note(emptied),
            "[] on a genuinely single-subject source is the false positive "
            "being measured, and it must outrank every collapse verdict in "
            "the same report",
        ),
        (
            NEGATIVE_SPLIT in negative_control_note(split),
            "splitting a single-subject source is a DIFFERENT miss from "
            "emptying it, and one label covering both would hide a candidate "
            "trading one for the other",
        ),
        (
            NEGATIVE_FALSE_POSITIVE not in negative_control_note(split),
            "and a split must not be reported as the [] false positive: the "
            "objects are still there",
        ),
        (
            NEGATIVE_NO_RESULT
            in negative_control_note(
                ArmResult("negative-control", "single-subject", "EN")
            ),
            "a negative control whose every run errored has measured "
            "nothing, and must not be read as [] -- a connection failure is "
            "not a false positive",
        ),
        (
            "not run" in negative_control_note(None),
            "an absent negative control must read as absent, never as an "
            "all-clear on single-subject material",
        ),
        (
            held.correct_runs == 3 and held.collapses,
            "the paired verdict logic reads this control's CORRECT answer as "
            "a collapse -- which is exactly why the control never goes "
            "through verdict(), and why correct_runs is named separately "
            "from collapsed_runs",
        ),
        (
            held.title_twin_runs == 3
            and ArmResult(
                "x", "arm", "EN", titles_per_run=[("Nightly Index Rebuild",)]
            ).title_twin_runs
            == 0,
            "the lone object restating the source title is the shape the "
            "floor in _drop_source_title_twins protects, so it must be "
            "counted -- and counted as zero, not guessed, when no source "
            "title was recorded",
        ),
        (
            "correct 3/3" in negative_report,
            "the negative control's run line must count CORRECT runs, not "
            "collapsed ones: the paired wording would file three right "
            "answers as three defects",
        ),
        (
            "NEGATIVE CONTROL" in report and "NEGATIVE CONTROL" in negative_report,
            "the negative control needs its own section UNCONDITIONALLY: a "
            "report that omits it when it did not run reads as if nothing "
            "was at stake on single-subject material",
        ),
        (
            all(s.pair_id != NEGATIVE_CONTROL.pair_id for s in SOURCES),
            "the negative control must stay OUT of SOURCES -- pairs() would "
            "reject a lone arm, and it should: an unpaired fixture read "
            "through the paired verdict logic is not a finding",
        ),
        (
            all(
                SHORT_SINGLE_TOPIC_BAND[0]
                <= len(source.text.encode("utf-8"))
                <= SHORT_SINGLE_TOPIC_BAND[1]
                for source in short_band_sources()
            ),
            "the short single-topic fixtures must stay in the 1-4 KB band: "
            "the band IS the shape they were written to measure, and a "
            "fixture that drifts out of it measures something else while "
            "still reporting under the same name",
        ),
        (
            ArmResult("x", "arm", "EN", retained=[0, 1, 1]).empty_runs == 1,
            "an empty run must be counted (#524): the model returning [] on "
            "substantive content is silent data loss, not a small object count",
        ),
        (
            not ArmResult("x", "arm", "EN", retained=[0, 0, 1]).collapses,
            "empty runs must NOT count toward collapse -- #522 and #524 are "
            "different defects and a metric that merges them can show one "
            "fixed by making the other worse",
        ),
        (
            "EMPTY EXTRACTIONS (#524)" in report,
            "empties must get their own section: they were found by reading "
            "raw run lines, which is exactly how a defect stays unfixed",
        ),
        (
            "1 of 3" in report,
            "the empty section must carry the rate, not merely a flag",
        ),
        (AXIS_IMPLICATED in report, "the report must carry the verdict"),
        (
            NO_FLOOR in report,
            "the report must show the no-floor pair, not quietly drop it",
        ),
        (
            "Decision" in report,
            "whether Decision was ever reachable is the #521 link and must "
            "survive into the report",
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
    parser.add_argument(
        "--union-judge",
        action="store_true",
        help="run extract_concept_union (#456) instead of the single-pass "
        "extract_concept. DEFAULT_UNION_JUDGE is True, so this is the path "
        "production actually runs (#524).",
    )
    parser.add_argument(
        "--positive-control",
        default=str(
            HERE.parent / "decision_extraction" / "sources" / "TS3005b.summary.txt"
        ),
        help="a source #522 recorded as collapsing on qwen3:8b, run unpaired "
        "to show the probe can see the defect at all. Skipped when absent, "
        "since the AMI sources are gitignored.",
    )
    parser.add_argument(
        "--no-control",
        action="store_true",
        help="skip the positive control (it costs one source's runs)",
    )
    parser.add_argument(
        "--no-negative-control",
        action="store_true",
        help="skip the negative control -- a genuinely single-subject source "
        "where ONE object is correct, run unpaired to show what a candidate "
        "costs on material the collapse rules must leave alone. It ships in "
        "the repository, so unlike the positive control it always runs unless "
        "this flag says otherwise (it costs one source's runs).",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    from openkos.llm.ollama import OllamaClient

    llm = OllamaClient(model=args.model, temperature=args.temperature, seed=args.seed)
    sampling = (
        f", temperature {args.temperature}" if args.temperature is not None else ""
    ) + (f", seed {args.seed}" if args.seed is not None else "")
    path = "extract_concept_union" if args.union_judge else "extract_concept"
    print(f"model {args.model}, {args.runs} run(s) per arm, {path}{sampling}\n")

    results: dict[str, tuple[ArmResult, ArmResult]] = {}
    for pair_id, pair in pairs().items():
        skew = length_skew(pair)
        if skew > MAX_LENGTH_SKEW:
            raise SystemExit(
                f"pair {pair_id!r} arms differ by {skew:.0%} in length "
                f"(limit {MAX_LENGTH_SKEW:.0%}) -- it would measure length, "
                "not framing"
            )
        arms: list[ArmResult] = []
        for source in pair:
            print(f"  {pair_id} [{source.arm}]")
            arms.append(run_arm(source, llm, args.runs, union_judge=args.union_judge))
        results[pair_id] = (arms[0], arms[1])

    control: ArmResult | None = None
    control_path = Path(args.positive_control)
    if not args.no_control and control_path.is_file():
        print(f"  positive control [{control_path.name}]")
        control = run_arm(
            PairedSource(
                pair_id="control",
                role=TREATMENT,
                arm="control",
                language="EN",
                source_title=control_path.stem,
                text=control_path.read_text(encoding="utf-8"),
            ),
            llm,
            args.runs,
            union_judge=args.union_judge,
        )

    negative: ArmResult | None = None
    if not args.no_negative_control:
        print(f"  negative control [{NEGATIVE_CONTROL.arm}]")
        negative = run_arm(
            NEGATIVE_CONTROL, llm, args.runs, union_judge=args.union_judge
        )

    print(
        render(
            results,
            control,
            args.model,
            current_prompt_sha(),
            negative=negative,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
