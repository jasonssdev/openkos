"""Which pipeline stage kills the subjects? (#715)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the
REAL union pipeline and records the candidate set ENTERING and LEAVING every
transforming stage, so the stage that eliminates `Decision`/`Event`/
`Concept`/`Procedure` candidates is NAMED rather than guessed.

## Why a stage ledger, and not another outcome measurement

#715 established the WHAT across two harnesses and 21 runs: on meeting-shaped
sources the retained set is all-`Person`, and `_UNION_BACKSTOP` (20) never
binds because only 3-5 objects survive. It named three suspects and proved
none:

1. `_drop_framing_objects`, which runs on meeting-shaped sources and deletes
   container/framing candidates;
2. `judge.select`'s "prefer FEWER, RICHER objects" restraint plus its
   always-drop clause for candidates naming the source itself;
3. the survival asymmetry — since #668 `Person`/`Organization` reach the
   retained set through a DETERMINISTIC re-admission path, while a subject the
   judge drops is simply gone.

Those three predict the same final state and cannot be separated by looking at
it. They differ in WHERE the loss happens, which is what this probe records.

## What it does to production: nothing

Every stage is observed by wrapping the module-level function with a recorder
that delegates to the real one. No production file is modified, and the
pipeline runs exactly as shipped. This is the technique
`evals/participant_anchor` used for one seam, widened to the whole chain.

`install()` asserts each target attribute EXISTS before replacing it. A
renamed stage would otherwise be patched into nothing: the run would succeed,
the ledger would show that stage as a no-op, and the conclusion would be drawn
from a stage that was never observed.

## Reading the ledger

Per stage: `in` and `out` counts split into SUBJECT-typed and PARTICIPANT-typed
candidates, plus the titles dropped. A stage whose subject `out` is lower than
its subject `in` is a killer; the first such stage in order is the answer.

Both counts are always printed side by side, and the totals with them. A view
that shows only one class hides its own complement — the exact reason #715 sat
inside `evals/participant_anchor`'s stored data for a day without being seen.

## The treatment arm (#715 fix)

The ledger answered WHERE, and the answer was "nowhere -- generation never
produces them". `--arm both` measures the one lever the owner authorized
against that: an ADDITIVE clause next to the verbatim-pinned anti-enumeration
paragraph, asking for the second half of what that paragraph already promises.
The pinned paragraph is not edited and `_drop_framing_objects` is not relaxed.

The arm splices the clause into `_SYSTEM_PROMPT` at the exact position it would
ship, so the measurement is of the shippable text and not of a paraphrase.

Usage:

    uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --self-test
    uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --runs 3
    uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --fixture es-anchored
    uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --arm both --runs 3
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import _PARTICIPANT_TYPES, extract_concept_union
from openkos.llm.ollama import OllamaClient

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"

_AMI_SOURCE = HERE.parent / "decision_extraction" / "sources" / "TS3005a.transcript.txt"
_ANCHOR_PROBE = HERE.parent / "participant_anchor" / "run_participant_anchor_probe.py"

_MAX_GENERATION_TOKENS: Final = 8_192
"""The ceiling `openkos.yaml.template` ships, so a failure here is a failure a
real `ingest` would have. Never run an eval client uncapped."""


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _load_anchor_probe() -> Any:
    """Import `evals/participant_anchor`'s probe module for its fixtures.

    Imported rather than copied ON PURPOSE: #715's evidence was produced on
    that exact `es-anchored` transcript, and a second copy would drift from it
    silently, leaving two fixtures with one name and no way to tell which
    produced which number. The module is import-safe (its `main()` is behind
    `if __name__ == "__main__"`)."""
    spec = importlib.util.spec_from_file_location(
        "_participant_anchor_probe", _ANCHOR_PROBE
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import fixtures from {_ANCHOR_PROBE}")
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE execution: `@dataclass` resolves its own class's
    # module through `sys.modules`, and a module absent from there fails
    # inside `dataclasses._is_type` rather than at the import site.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class Fixture:
    """One meeting-shaped source, and what it is known to contain."""

    name: str
    title: str
    text: str
    known_subjects: tuple[str, ...]
    """Subjects the SOURCE demonstrably discusses, hand-written. Not a recall
    target — this probe scores stage attrition, not extraction quality. It
    exists so a reader can tell whether the candidates a stage killed were
    worth keeping, which a bare count cannot say."""


def build_fixtures() -> list[Fixture]:
    """Every fixture available in this checkout."""
    anchor = _load_anchor_probe()
    arms = {arm.name: arm for arm in anchor.build_arms()}
    fixtures: list[Fixture] = []

    if "es-anchored" in arms:
        arm = arms["es-anchored"]
        fixtures.append(
            Fixture(
                name="es-anchored",
                title=arm.title,
                text=arm.text,
                known_subjects=(
                    "the decision to incorporate Vega Ingeniería's minutes "
                    "corpus under an agreement, with personal-data review",
                    "the transcript ingestion pipeline and its quality review",
                    "the search index not rebuilding after each write",
                    "the evaluation report and its citation-retention numbers",
                ),
            )
        )
    if "es-bare" in arms:
        arm = arms["es-bare"]
        fixtures.append(
            Fixture(
                name="es-bare",
                title=arm.title,
                text=arm.text,
                known_subjects=(
                    "the decision to pin the context window and re-measure",
                    "citations pointing at the wrong document on long questions",
                    "the bundle backup being unrun since last week",
                ),
            )
        )
    try:
        ami_text = _AMI_SOURCE.read_text(encoding="utf-8")
    except OSError:
        ami_text = ""
    if ami_text:
        fixtures.append(
            Fixture(
                name="ami-ts3005a",
                title=_AMI_SOURCE.stem,
                text=ami_text,
                known_subjects=(
                    "the remote-control design project and its goals",
                    "the participants' assigned project roles",
                ),
            )
        )
    return fixtures


# --------------------------------------------------------------------------- #
# Arms
# --------------------------------------------------------------------------- #

_SPLICE_ANCHOR: Final = (
    "A source developing only one subject still yields exactly ONE object.\n\n"
)
"""End of the stated multiplicity test (design D3), which is itself ADDITIVE
next to the verbatim-pinned anti-enumeration paragraph. The treatment clause
goes immediately after it, so it lands in the same adjacency D3 established and
the pinned paragraph (#380) keeps every one of its pinned bytes."""

_TREATMENT_CLAUSE: Final = (
    "When the source is a meeting, call, or interview transcript, BOTH halves "
    "of that instruction are required: the gathering itself AND each distinct "
    "subject the participants worked through -- every decision reached, every "
    "problem raised, every topic resolved, every procedure agreed. A working "
    "transcript normally develops SEVERAL such subjects, and a reply naming "
    "only the gathering has not read the transcript for its content.\n\n"
)
"""The one lever authorized for #715, stated ADDITIVELY.

`evals/stage_attrition`'s own report established the mechanism: the pinned
anti-enumeration paragraph tells the model a transcript is "about the meeting
itself (an Event) and any Decisions reached", the model emits the Event and
stops, and `_drop_framing_objects` then correctly deletes it -- so a meeting
yields nothing.

This clause therefore does NOT contradict that paragraph; it COMPLETES it. The
failure is that only the first half of "extract the Event and the Decisions"
ever arrives, so the clause reinforces the second half rather than negating the
first. Negating it was the alternative and was deliberately not taken: a direct
contradiction inside one prompt degrades the 8B tier, and the anti-twin
experience (D4/5b) is that a narrower clause carrying a CONCRETE forbidden
example made its defect measurably WORSE via priming. There is no concrete
example here for the same reason."""


@dataclass(frozen=True)
class Arm:
    """One prompt configuration to measure."""

    name: str
    system_prompt: str
    """The exact `_SYSTEM_PROMPT` text this arm runs under."""


def build_arms() -> dict[str, Arm]:
    """`baseline` (shipped text) and `treatment` (shipped text + the clause).

    The splice is asserted, never assumed: if the anchor paragraph is reworded,
    a silent `str.replace` no-op would run the treatment arm on the baseline
    prompt and report "no effect" for a treatment that was never applied."""
    shipped = concept_mod._SYSTEM_PROMPT
    if shipped.count(_SPLICE_ANCHOR) != 1:
        raise SystemExit(
            "the treatment splice anchor is no longer present exactly once in "
            "_SYSTEM_PROMPT. Re-point it before trusting a number from the "
            "treatment arm -- an unspliced arm measures the baseline twice and "
            "reads as 'the treatment does nothing'."
        )
    treated = shipped.replace(_SPLICE_ANCHOR, _SPLICE_ANCHOR + _TREATMENT_CLAUSE, 1)
    return {
        "baseline": Arm(name="baseline", system_prompt=shipped),
        "treatment": Arm(name="treatment", system_prompt=treated),
    }


# --------------------------------------------------------------------------- #
# The stage recorder
# --------------------------------------------------------------------------- #

_DEDUP_STAGES: Final = frozenset({"_merge_union", "_dedup_merged"})
"""Stages that collapse DUPLICATES rather than eliminate candidates. Their
subject count legitimately falls -- the union path runs extraction twice, so
every genuine subject enters `_merge_union` twice. Counting that as a loss
would put a merge step at the top of a table headed "where subjects die"."""


_SUBJECT_LABEL: Final = "subject"
_PARTICIPANT_LABEL: Final = "participant"


def _classify(item: Any) -> str:
    """`participant` for `Person`/`Organization`, `subject` for everything
    else — the two lanes #712 names, used here only to read the ledger."""
    return (
        _PARTICIPANT_LABEL
        if getattr(item, "type", None) in _PARTICIPANT_TYPES
        else _SUBJECT_LABEL
    )


def _snapshot(items: Any) -> list[dict[str, str]]:
    """`(type, title, lane)` for each candidate, order preserved."""
    if items is None:
        return []
    out: list[dict[str, str]] = []
    for item in items:
        if not hasattr(item, "title"):
            continue
        out.append(
            {
                "type": str(getattr(item, "type", "?")),
                "title": str(item.title),
                "lane": _classify(item),
            }
        )
    return out


@dataclass
class StageEvent:
    """One stage's input and output, in call order."""

    stage: str
    entered: list[dict[str, str]]
    left: list[dict[str, str]]

    @property
    def dropped(self) -> list[dict[str, str]]:
        """Candidates present on entry and absent on exit, by (type, title)."""
        surviving = {(c["type"], c["title"]) for c in self.left}
        return [c for c in self.entered if (c["type"], c["title"]) not in surviving]

    def counts(self, lane: str) -> tuple[int, int]:
        """`(in, out)` for one lane."""
        return (
            sum(1 for c in self.entered if c["lane"] == lane),
            sum(1 for c in self.left if c["lane"] == lane),
        )


_LIST_STAGES: Final = (
    # (attribute, index of the candidate list in the call, returns_tuple)
    ("_strip_ungrounded_expansions", 0, False),
    ("_drop_framing_objects", 0, False),
    ("_merge_union", 0, False),
    ("_dedup_merged", 0, False),
    ("_drop_source_title_twins", 0, False),
    ("_drop_wrong_language_titles", 0, True),
    ("_add_reask_subjects", 0, True),
    ("_add_participant_capture", 0, True),
)
"""Every module-level stage that takes a candidate list first and returns
either a list or a tuple whose FIRST element is the list. Order here is
documentation only; the ledger records real call order."""


class _StageRecorder:
    """Wraps every pipeline stage with a recorder, restoring on exit.

    Production is untouched: each wrapper delegates to the real function and
    records what passed through it."""

    def __init__(self) -> None:
        self.events: list[StageEvent] = []
        self._originals: dict[str, Any] = {}

    def install(self) -> None:
        targets = [name for name, _, _ in _LIST_STAGES] + [
            "_extract_once",
            "_select_with_progress",
        ]
        missing = [name for name in targets if not hasattr(concept_mod, name)]
        if missing:
            raise SystemExit(
                "these pipeline stages no longer exist: "
                f"{', '.join(missing)}. Re-point this probe before trusting a "
                "single number from it -- a patched-into-nothing stage reads "
                "as a no-op and would exonerate itself."
            )

        for name, arg_index, returns_tuple in _LIST_STAGES:
            self._wrap_list_stage(name, arg_index, returns_tuple)
        self._wrap_extract_once()
        self._wrap_judge()

    def _wrap_list_stage(self, name: str, arg_index: int, returns_tuple: bool) -> None:
        original = getattr(concept_mod, name)
        self._originals[name] = original
        events = self.events

        def recording(*args: Any, **kwargs: Any) -> Any:
            entered = _snapshot(args[arg_index] if len(args) > arg_index else None)
            result = original(*args, **kwargs)
            produced = (
                result[0] if returns_tuple and isinstance(result, tuple) else result
            )
            events.append(
                StageEvent(stage=name, entered=entered, left=_snapshot(produced))
            )
            return result

        setattr(concept_mod, name, recording)

    def _wrap_extract_once(self) -> None:
        """`_extract_once` PRODUCES rather than filters: entry is empty."""
        original = concept_mod._extract_once
        self._originals["_extract_once"] = original
        events = self.events

        def recording(*args: Any, **kwargs: Any) -> Any:
            result = original(*args, **kwargs)
            events.append(
                StageEvent(stage="_extract_once", entered=[], left=_snapshot(result))
            )
            return result

        concept_mod._extract_once = recording

    def _wrap_judge(self) -> None:
        """The judge returns TITLES, not candidates, so its `left` is the
        subset of its input whose title the judge echoed back. Re-admission
        happens after this, in `extract_concept_union` itself, and is read
        off the final outcome rather than wrapped."""
        original = concept_mod._select_with_progress
        self._originals["_select_with_progress"] = original
        events = self.events

        def recording(
            source_text: Any, judge_input: Any, llm: Any, on_progress: Any
        ) -> Any:
            entered = _snapshot(judge_input)
            selected = original(source_text, judge_input, llm, on_progress)
            if selected is None:
                left = entered  # a failed judge degrades to the whole set
            else:
                chosen = {concept_mod._normalize_title(t) for t in selected}
                left = [
                    c
                    for c in entered
                    if concept_mod._normalize_title(c["title"]) in chosen
                ]
            events.append(StageEvent(stage="judge.select", entered=entered, left=left))
            return selected

        concept_mod._select_with_progress = recording

    def restore(self) -> None:
        for name, original in self._originals.items():
            setattr(concept_mod, name, original)
        self._originals.clear()

    def reset(self) -> None:
        """Clear IN PLACE, never rebind.

        Every wrapper closes over this exact list object at `install()` time.
        Rebinding `self.events` to a fresh list leaves them appending to an
        orphan while the recorder reports the new empty one — a run that
        succeeds with an empty ledger and reads as "no stage drops anything".
        The self-test's first assertion exists because this bug shipped once."""
        self.events.clear()


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


@dataclass
class RunRecord:
    """One (fixture, arm, run) with its full stage ledger."""

    fixture: str
    run: int
    model: str
    latency_s: float
    judge_status: str
    produced: int
    retained: int
    arm: str = "baseline"
    final_objects: list[dict[str, str]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def subjects(self) -> list[dict[str, str]]:
        return [c for c in self.final_objects if c["lane"] == _SUBJECT_LABEL]

    @property
    def participants(self) -> list[dict[str, str]]:
        return [c for c in self.final_objects if c["lane"] == _PARTICIPANT_LABEL]


def run_fixture(
    fixture: Fixture,
    llm: Any,
    runs: int,
    model: str,
    arm: Arm | None = None,
) -> list[RunRecord]:
    """Measure one fixture `runs` times under one arm, recording every stage.

    The arm's prompt is installed on the module for the duration and restored
    in the same `finally` that restores the recorder: a probe that leaves a
    treatment prompt installed would silently contaminate every later arm."""
    recorder = _StageRecorder()
    recorder.install()
    arm_name = arm.name if arm is not None else "baseline"
    original_prompt = concept_mod._SYSTEM_PROMPT
    if arm is not None:
        concept_mod._SYSTEM_PROMPT = arm.system_prompt
    records: list[RunRecord] = []
    try:
        for index in range(1, runs + 1):
            recorder.reset()
            started = time.monotonic()
            try:
                outcome = extract_concept_union(
                    fixture.text, source_title=fixture.title, llm=llm
                )
            except Exception as exc:
                records.append(
                    RunRecord(
                        fixture=fixture.name,
                        run=index,
                        model=model,
                        arm=arm_name,
                        latency_s=round(time.monotonic() - started, 1),
                        judge_status="error",
                        produced=0,
                        retained=0,
                        stages=[asdict(e) for e in recorder.events],
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(f"    run {index}: ERROR {type(exc).__name__}: {exc}")
                continue
            latency = round(time.monotonic() - started, 1)
            final = _snapshot(outcome.objects)
            records.append(
                RunRecord(
                    fixture=fixture.name,
                    run=index,
                    model=model,
                    arm=arm_name,
                    latency_s=latency,
                    judge_status=outcome.report.judge_status,
                    produced=outcome.report.produced,
                    retained=outcome.report.retained,
                    final_objects=final,
                    stages=[asdict(e) for e in recorder.events],
                )
            )
            subjects = sum(1 for c in final if c["lane"] == _SUBJECT_LABEL)
            print(
                f"    [{arm_name}] run {index}: retained {len(final)} "
                f"({subjects} subject / {len(final) - subjects} participant), "
                f"judge {outcome.report.judge_status}, {latency}s"
            )
    finally:
        recorder.restore()
        concept_mod._SYSTEM_PROMPT = original_prompt
    return records


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def _event_counts(event: dict[str, Any], lane: str) -> tuple[int, int]:
    entered = sum(1 for c in event["entered"] if c["lane"] == lane)
    left = sum(1 for c in event["left"] if c["lane"] == lane)
    return entered, left


def render_run(record: RunRecord) -> str:
    """The attrition ledger for one run, in call order."""
    lines = [
        f"  [{record.arm}] run {record.run} "
        f"(judge {record.judge_status}, {record.latency_s}s)",
        "    stage                         subj in→out   part in→out   dropped",
    ]
    for event in record.stages:
        s_in, s_out = _event_counts(event, _SUBJECT_LABEL)
        p_in, p_out = _event_counts(event, _PARTICIPANT_LABEL)
        surviving = {(c["type"], c["title"]) for c in event["left"]}
        dropped = [
            c for c in event["entered"] if (c["type"], c["title"]) not in surviving
        ]
        flag = " <<<" if s_out < s_in else ""
        names = ", ".join(f"{c['type']}:{c['title']}" for c in dropped[:3])
        if len(dropped) > 3:
            names += f" (+{len(dropped) - 3})"
        lines.append(
            f"    {event['stage']:<28} {s_in:>3} → {s_out:<3}    "
            f"{p_in:>3} → {p_out:<3}    {names}{flag}"
        )
    subjects = [c for c in record.final_objects if c["lane"] == _SUBJECT_LABEL]
    parts = [c for c in record.final_objects if c["lane"] == _PARTICIPANT_LABEL]
    lines.append(
        f"    FINAL RETAINED: {len(record.final_objects)} "
        f"({len(subjects)} subject / {len(parts)} participant)"
    )
    for candidate in record.final_objects:
        lines.append(f"      - {candidate['type']}: {candidate['title']}")
    return "\n".join(lines)


def render(records: list[RunRecord], fixtures: list[Fixture]) -> str:
    """Full report: per-run ledgers, then the per-stage subject-kill tally."""
    by_fixture: dict[str, list[RunRecord]] = {}
    for record in records:
        by_fixture.setdefault(record.fixture, []).append(record)
    known = {f.name: f for f in fixtures}

    lines = ["", "=" * 78, "STAGE ATTRITION (#715)", "=" * 78, ""]
    for name, runs in by_fixture.items():
        lines.append(f"## {name}")
        fixture = known.get(name)
        if fixture is not None:
            lines.append("   source demonstrably discusses:")
            for subject in fixture.known_subjects:
                lines.append(f"     * {subject}")
        for record in runs:
            if record.error:
                lines.append(f"  run {record.run}: ERROR {record.error}")
                continue
            lines.append(render_run(record))
        lines.append("")

    lines += ["", "=" * 78, "WHERE SUBJECTS DIE — tally over every run", "=" * 78, ""]
    killed: dict[str, int] = {}
    seen: dict[str, int] = {}
    for record in records:
        for event in record.stages:
            s_in, s_out = _event_counts(event, _SUBJECT_LABEL)
            seen[event["stage"]] = seen.get(event["stage"], 0) + s_in
            if s_out < s_in:
                killed[event["stage"]] = killed.get(event["stage"], 0) + (s_in - s_out)
    if not killed:
        lines.append("  No stage reduced the subject count on any run.")
    for stage, count in sorted(killed.items(), key=lambda kv: -kv[1]):
        note = (
            "  (dedup — collapses duplicates by design, NOT a loss)"
            if (stage in _DEDUP_STAGES)
            else ""
        )
        lines.append(
            f"  {stage:<30} killed {count:>3} subject(s) of "
            f"{seen.get(stage, 0)} seen{note}"
        )
    real = [s for s in killed if s not in _DEDUP_STAGES]
    lines.append("")
    if real:
        lines.append(f"  ELIMINATING STAGES: {', '.join(sorted(real))}")
    else:
        lines.append(
            "  No stage ELIMINATED a subject; every reduction was deduplication."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ArmSummary:
    """One arm's scored totals."""

    arm: str
    runs: int
    runs_with_subject: int
    mean_subjects: float
    mean_participants: float
    mean_latency: float


def summarize_arm(records: list[RunRecord]) -> ArmSummary:
    """Score one arm over every successful run it produced."""
    ok = [r for r in records if r.error is None]
    count = max(len(ok), 1)
    return ArmSummary(
        arm=ok[0].arm if ok else (records[0].arm if records else "?"),
        runs=len(ok),
        runs_with_subject=sum(1 for r in ok if r.subjects),
        mean_subjects=round(sum(len(r.subjects) for r in ok) / count, 2),
        mean_participants=round(sum(len(r.participants) for r in ok) / count, 2),
        mean_latency=round(sum(r.latency_s for r in ok) / count, 1),
    )


_LATENCY_REJECT_FACTOR: Final = 1.5
"""Slice 1's own reject threshold (`named-person-capture` Phase 0.2), reused
verbatim so two treatments aimed at the same defect are not scored against two
different bars."""

_PARTICIPANT_TOLERANCE: Final = 0.5
"""Participants reach retention deterministically, so their count should not
move at all. Half an object of slack absorbs a single stochastic capture
without excusing a real regression."""


def render_gate(records: list[RunRecord]) -> tuple[str, bool]:
    """Score the treatment against the baseline. Returns `(report, shippable)`.

    Four conditions, mirroring the slice-1 gate that rejected the D2 capture
    prompt. Any one firing means REJECT, and per the owner's standing ruling a
    REJECT ships the measurement only -- production is not touched and no
    fallback lever is taken.

    Condition 4 is deliberately NOT automated. Whether a retained subject is
    real cannot be decided by a substring test the way #712's absent-name check
    could, and a probe that scores it automatically would be inventing an
    oracle it does not have (#694 is that oracle, and it is unbuilt). The gate
    prints every retained subject and stops short of PASS until a human has
    adjudicated them."""
    by_arm: dict[str, list[RunRecord]] = {}
    for record in records:
        by_arm.setdefault(record.arm, []).append(record)

    lines = ["", "=" * 78, "GATE — treatment vs baseline (#715)", "=" * 78, ""]
    if "treatment" not in by_arm or "baseline" not in by_arm:
        lines.append("  Single-arm run: no gate. Use --arm both to score.")
        return "\n".join(lines), False

    base = summarize_arm(by_arm["baseline"])
    treat = summarize_arm(by_arm["treatment"])

    lines.append(
        f"  {'arm':<12}{'runs':>6}{'w/subject':>11}"
        f"{'subj/run':>10}{'part/run':>10}{'latency':>10}"
    )
    for summary in (base, treat):
        lines.append(
            f"  {summary.arm:<12}{summary.runs:>6}{summary.runs_with_subject:>11}"
            f"{summary.mean_subjects:>10}{summary.mean_participants:>10}"
            f"{summary.mean_latency:>9}s"
        )
    lines.append("")

    latency_ratio = (
        treat.mean_latency / base.mean_latency if base.mean_latency else float("inf")
    )
    conditions = [
        (
            "subject retention does not increase",
            treat.runs_with_subject <= base.runs_with_subject,
            f"{base.runs_with_subject}/{base.runs} → "
            f"{treat.runs_with_subject}/{treat.runs} runs retained a subject",
        ),
        (
            f"latency >= {_LATENCY_REJECT_FACTOR}x baseline",
            latency_ratio >= _LATENCY_REJECT_FACTOR,
            f"{base.mean_latency}s -> {treat.mean_latency}s ({latency_ratio:.2f}x)",
        ),
        (
            "participant retention degrades",
            treat.mean_participants < base.mean_participants - _PARTICIPANT_TOLERANCE,
            f"{base.mean_participants} → {treat.mean_participants} per run",
        ),
        (
            "a retained subject is not supported by the source",
            False,
            "NOT automated — adjudicate the titles below before reading a PASS",
        ),
    ]
    fired = [name for name, did_fire, _ in conditions if did_fire]
    for name, did_fire, evidence in conditions:
        mark = "FIRED  " if did_fire else "clear  "
        lines.append(f"  [{mark}] {name}\n             {evidence}")

    lines += ["", "  Subjects retained by the treatment arm (adjudicate each):"]
    treatment_subjects = [
        (r.fixture, c["type"], c["title"])
        for r in by_arm["treatment"]
        for c in r.subjects
    ]
    if not treatment_subjects:
        lines.append("    (none)")
    for fixture, type_name, title in sorted(set(treatment_subjects)):
        occurrences = sum(
            1 for row in treatment_subjects if row == (fixture, type_name, title)
        )
        lines.append(f"    {fixture:<14} x{occurrences}  {type_name}: {title}")

    lines.append("")
    if fired:
        lines.append(
            f"  VERDICT: REJECT — {len(fired)} condition(s) fired: {'; '.join(fired)}"
        )
        lines.append(
            "  Per the standing ruling, a REJECT ships the measurement only: "
            "production stays untouched."
        )
    else:
        lines.append(
            "  VERDICT: no automated condition fired. SHIPPABLE once the "
            "retained subjects above are adjudicated as real."
        )
    return "\n".join(lines), not fired


def write_results(records: list[RunRecord], stamp: str, model: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = model.replace(":", "-").replace("/", "-")
    path = RESULTS_DIR / f"stage-attrition-{stamp}-{slug}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


class _FakeLLM:
    """Scripted backend: two extraction passes proposing a Decision and a
    Concept, a participant pass proposing one Person, and a judge that keeps
    ONLY the Person. No model, no network."""

    def chat(self, messages: list[dict[str, str]]) -> str:
        system = messages[0]["content"]
        if "narrow follow-up question" in system:
            return json.dumps(
                [
                    {
                        "type": "Person",
                        "title": "Ana Ríos",
                        "description": "Chaired the meeting.",
                        "body": "",
                    }
                ],
                ensure_ascii=False,
            )
        if "selection step" in system:
            return json.dumps({"keep": ["Ana Ríos"]})
        return json.dumps(
            [
                {
                    "type": "Decision",
                    "title": "Adopt the minutes corpus",
                    "description": "The corpus is incorporated under agreement.",
                    "body": "",
                },
                {
                    "type": "Concept",
                    "title": "Transcript ingestion",
                    "description": "How transcripts enter the bundle.",
                    "body": "",
                },
            ],
            ensure_ascii=False,
        )


def _record(arm: str, subjects: int, participants: int, latency: float) -> RunRecord:
    """One synthetic outcome, for scoring the gate without a model."""
    final = [
        {"type": "Decision", "title": f"d{i}", "lane": _SUBJECT_LABEL}
        for i in range(subjects)
    ] + [
        {"type": "Person", "title": f"p{i}", "lane": _PARTICIPANT_LABEL}
        for i in range(participants)
    ]
    return RunRecord(
        fixture="es-bare",
        run=1,
        model="fake",
        arm=arm,
        latency_s=latency,
        judge_status="ok",
        produced=len(final),
        retained=len(final),
        final_objects=final,
    )


def _arm_and_gate_self_test() -> list[str]:
    """Prove the splice really splices and the gate really rejects.

    Both failures this guards are silent-success failures: an unspliced
    treatment arm runs the baseline prompt twice and reports "no effect", and a
    gate that never fires reports SHIPPABLE for a treatment that did nothing.
    Neither shows up as an error."""
    failures: list[str] = []
    arms = build_arms()
    baseline, treatment = arms["baseline"], arms["treatment"]

    if _TREATMENT_CLAUSE not in treatment.system_prompt:
        failures.append("the treatment arm's prompt does not carry the clause")
    if len(treatment.system_prompt) <= len(baseline.system_prompt):
        failures.append("the treatment prompt is not longer than the baseline")
    if _SPLICE_ANCHOR + _TREATMENT_CLAUSE not in treatment.system_prompt:
        failures.append("the clause did not land adjacent to the multiplicity test")
    if _TREATMENT_CLAUSE in baseline.system_prompt:
        failures.append("the baseline arm is contaminated with the clause")

    original = concept_mod._SYSTEM_PROMPT
    run_fixture(build_fixtures()[0], _FakeLLM(), runs=1, model="fake", arm=treatment)
    if concept_mod._SYSTEM_PROMPT is not original:
        failures.append(
            "run_fixture left an arm's prompt installed -- every later arm "
            "would be measured under it"
        )

    flat = [
        _record("baseline", subjects=0, participants=3, latency=50.0),
        _record("baseline", subjects=0, participants=3, latency=50.0),
        _record("treatment", subjects=0, participants=3, latency=55.0),
        _record("treatment", subjects=0, participants=3, latency=55.0),
    ]
    report, shippable = render_gate(flat)
    if shippable or "VERDICT: REJECT" not in report:
        failures.append("the gate must REJECT a treatment that retained no subject")

    better = [
        *flat[:2],
        _record("treatment", subjects=2, participants=3, latency=55.0),
        _record("treatment", subjects=1, participants=3, latency=55.0),
    ]
    report, shippable = render_gate(better)
    if not shippable:
        failures.append(
            "the gate must clear a treatment that lifted subject retention "
            "within budget -- an always-REJECT gate proves nothing"
        )

    slow = [
        *flat[:2],
        _record("treatment", subjects=2, participants=3, latency=95.0),
        _record("treatment", subjects=2, participants=3, latency=95.0),
    ]
    _, shippable = render_gate(slow)
    if shippable:
        failures.append("the gate must REJECT on latency at 1.9x baseline")

    thinner = [
        *flat[:2],
        _record("treatment", subjects=2, participants=1, latency=55.0),
        _record("treatment", subjects=2, participants=1, latency=55.0),
    ]
    _, shippable = render_gate(thinner)
    if shippable:
        failures.append(
            "the gate must REJECT when participants are traded for subjects"
        )
    return failures


def _self_test() -> int:
    """Prove the ledger localizes a known kill, with no model running.

    The scripted judge keeps only the `Person`, so `judge.select` MUST be the
    stage the tally blames. If the recorder were patched into nothing, every
    stage would read as a no-op and the tally would be empty — which is the
    first thing asserted."""
    fixture = build_fixtures()[0]
    records = run_fixture(fixture, _FakeLLM(), runs=1, model="fake")
    report = render(records, [fixture])
    record = records[0]

    stages_seen = [e["stage"] for e in record.stages]
    final_lanes = [c["lane"] for c in record.final_objects]
    judge_events = [e for e in record.stages if e["stage"] == "judge.select"]

    expectations = [
        (
            len(record.stages) >= 6,
            f"the recorder must observe the pipeline's stages (saw {len(stages_seen)})",
        ),
        (
            "_extract_once" in stages_seen and "_drop_framing_objects" in stages_seen,
            f"production and framing-drop must both be observed (saw {stages_seen})",
        ),
        (
            len(judge_events) == 1,
            f"the judge must be observed exactly once (saw {len(judge_events)})",
        ),
        (
            judge_events
            and sum(1 for c in judge_events[0]["entered"] if c["lane"] == "subject")
            == 2,
            "both scripted subjects must reach the judge -- if they die earlier "
            "this fixture no longer tests what the self-test claims",
        ),
        (
            judge_events
            and sum(1 for c in judge_events[0]["left"] if c["lane"] == "subject") == 0,
            "the scripted judge keeps only the Person, so no subject may leave it",
        ),
        (
            "judge.select" in report and "killed   2 subject(s)" in report,
            "the tally must blame judge.select for exactly the two subjects it dropped",
        ),
        (
            final_lanes == ["participant"],
            f"the final set must be participant-only here (got {final_lanes})",
        ),
    ]
    failures = [why for ok, why in expectations if not ok]
    failures.extend(_arm_and_gate_self_test())
    print(report)
    if failures:
        for why in failures:
            print(f"SELF-TEST FAILED: {why}")
        return 1
    print("self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument(
        "--fixture", action="append", help="measure only this fixture (repeatable)"
    )
    parser.add_argument(
        "--arm",
        default="baseline",
        choices=("baseline", "treatment", "both"),
        help="which prompt arm(s) to measure (default: baseline)",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    fixtures = build_fixtures()
    if args.fixture:
        wanted = set(args.fixture)
        unknown = wanted - {f.name for f in fixtures}
        if unknown:
            raise SystemExit(f"unknown fixture(s): {', '.join(sorted(unknown))}")
        fixtures = [f for f in fixtures if f.name in wanted]

    arms = build_arms()
    wanted_arms = ["baseline", "treatment"] if args.arm == "both" else [str(args.arm)]

    llm = OllamaClient(model=args.model, max_generation_tokens=_MAX_GENERATION_TOKENS)
    print(
        f"model {args.model}, {args.runs} run(s) per fixture, "
        f"arm(s): {', '.join(wanted_arms)}\n"
    )

    records: list[RunRecord] = []
    # Arm OUTERMOST so both arms see the same fixture order and the same warm
    # model, and so an interrupted `both` run still holds one complete arm.
    for arm_name in wanted_arms:
        for fixture in fixtures:
            print(f"  [{arm_name}] {fixture.name} ({len(fixture.text)} chars)")
            records.extend(
                run_fixture(fixture, llm, args.runs, args.model, arms[arm_name])
            )

    print(render(records, fixtures))
    if len(wanted_arms) > 1:
        gate_report, _ = render_gate(records)
        print(gate_report)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    print(f"stored {write_results(records, stamp, args.model)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
