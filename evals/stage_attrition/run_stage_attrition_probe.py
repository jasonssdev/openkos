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

Usage:

    uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --self-test
    uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --runs 3
    uv run python -u evals/stage_attrition/run_stage_attrition_probe.py --fixture es-anchored
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
    """One (fixture, run) with its full stage ledger."""

    fixture: str
    run: int
    model: str
    latency_s: float
    judge_status: str
    produced: int
    retained: int
    final_objects: list[dict[str, str]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def run_fixture(fixture: Fixture, llm: Any, runs: int, model: str) -> list[RunRecord]:
    """Measure one fixture `runs` times, recording every stage."""
    recorder = _StageRecorder()
    recorder.install()
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
                f"    run {index}: retained {len(final)} "
                f"({subjects} subject / {len(final) - subjects} participant), "
                f"judge {outcome.report.judge_status}, {latency}s"
            )
    finally:
        recorder.restore()
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
        f"  run {record.run} (judge {record.judge_status}, {record.latency_s}s)",
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

    llm = OllamaClient(model=args.model, max_generation_tokens=_MAX_GENERATION_TOKENS)
    print(f"model {args.model}, {args.runs} run(s) per fixture\n")

    records: list[RunRecord] = []
    for fixture in fixtures:
        print(f"  {fixture.name} ({len(fixture.text)} chars)")
        records.extend(run_fixture(fixture, llm, args.runs, args.model))

    print(render(records, fixtures))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    print(f"stored {write_results(records, stamp, args.model)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
