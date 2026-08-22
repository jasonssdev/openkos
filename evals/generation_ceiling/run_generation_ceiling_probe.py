"""Which call hits the generation ceiling, and does chunking remove it? (#714)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the REAL
`extract_concept_union` pipeline and records EVERY chat call it makes -- prompt
tokens, generated tokens, `done_reason` -- tagged with the pipeline phase that
issued it, so a cut-off run names the call that was cut off instead of the run.

## Why the call, and not the run

#714's evidence, and #715's blocked treatment arm, both report
`OllamaGenerationCapped` out of a whole `extract_concept_union` run. That
exception does not say WHICH of the run's calls was truncated, and the run
makes several: two whole-document extractions, an optional re-ask, an optional
participant capture, and the judge.

That distinction decides whether the authorized fix can work at all.
`_CHUNK_THRESHOLD` governs the EXTRACTION fan-out only. The judge is called as

    judge_mod.select(source_text, ...)

with the WHOLE source text plus the candidate list, on both sides of the
threshold, so lowering that constant does not shrink this prompt at all. If the
ceiling is being hit in the judge, then lowering `_CHUNK_THRESHOLD` fixes
nothing, and a sweep that only measured `_extract_once` would have reported a
clean bill of health for a constant that changes nothing.

(Measured, that is NOT where it is hit, and not because the judge's prompt is
small in absolute terms -- it is that the judge's REPLY is. The ceiling caps
replies, and selection is a short answer. See `report.md`.)

So the probe measures the shipped pipeline and attributes the cap, rather than
narrowing to the call a reader would guess.

## The arms are the decision

`chunk_threshold` is an arm axis, not a constant. The `*-chunked` arms run the
identical fixture with `_CHUNK_THRESHOLD` lowered to `_CANDIDATE_THRESHOLD`, so
the same source takes the chunked path. That is a direct A/B on the authorized
lever: if the chunked arm still caps, the lever does not fix #714.

The `treatment*` arms carry #715's additive clause, IMPORTED from
`evals/stage_attrition` rather than copied. #714 exists to unblock #715, so a
threshold that gives the baseline headroom but not the treatment unblocks
nothing.

## The prose control

`_CHUNK_THRESHOLD`'s own docstring records a cost on the OTHER side of the
boundary: below it, the whole-document call is the path every existing
measurement was taken against, and it works on prose -- the #379 gate's 13-17 KB
documents produced 5-10 objects each. A threshold lowered far enough to protect
a 16.4 KB transcript moves that measured-working band onto a path nobody
measured for it.

`prose-large-03` is 16 948 chars -- LARGER than the transcript that fails -- and
runs both the baseline and chunked arms, so the collateral of the lever is
measured beside its benefit rather than assumed away.

Usage:

    uv run python -u evals/generation_ceiling/run_generation_ceiling_probe.py --self-test
    uv run python -u evals/generation_ceiling/run_generation_ceiling_probe.py --runs 3
    uv run python -u evals/generation_ceiling/run_generation_ceiling_probe.py --arm baseline --arm chunked
    uv run python -u evals/generation_ceiling/run_generation_ceiling_probe.py --rescore results/<file>.jsonl

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

from openkos.extraction import concept as concept_mod
from openkos.extraction.concept import _is_meeting_shaped, extract_concept_union
from openkos.llm.ollama import OllamaClient, OllamaGenerationCapped

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"

_AMI_SOURCE = HERE.parent / "decision_extraction" / "sources" / "TS3005a.transcript.txt"
_CORPUS_DIR = HERE.parent.parent / "examples" / "extraction-corpus" / "sources"
_STAGE_ATTRITION_PROBE = (
    HERE.parent / "stage_attrition" / "run_stage_attrition_probe.py"
)

_MAX_GENERATION_TOKENS: Final = 8_192
"""The ceiling `openkos.yaml.template` ships, so a cut-off here is a cut-off a
real `ingest` would have. Never run an eval client uncapped."""

_CANDIDATE_THRESHOLD: Final = 12_000
"""The `_CHUNK_THRESHOLD` value the `*-chunked` arms run under.

A CANDIDATE under test, not a proposal. It sits below the 16 440-char
transcript #714 was filed on (so that source chunks) and below the 16 948-char
prose control (so the collateral is measured on the same setting), and it is the
largest transcript prefix an earlier `_extract_once`-only sweep completed with
healthy replies. Whether it SHIPS depends on what the arms below show."""


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Fixture:
    name: str
    text: str
    shape: str
    """`"transcript"` or `"prose"` -- the probe's own label for the corpus this
    text came from, recorded so a reader can group the table without trusting
    the engine's detector. `meeting_shaped` records what the engine actually
    thinks, and the two are deliberately kept apart."""
    title: str
    arms: tuple[str, ...]
    """Which arms this fixture runs. The prose control exists to measure the
    lever's COLLATERAL, so it runs baseline vs chunked and skips #715's clause,
    which has nothing to do with prose."""


def build_fixtures() -> tuple[list[Fixture], list[str]]:
    """Every fixture available on this machine, plus a notice per absent corpus.

    Both corpora are optional, for the same reason `ami-ts3005a` is optional in
    `evals/stage_attrition`: `evals/decision_extraction/sources/` is built by
    `build_sources.py` from a third-party archive, and
    `examples/extraction-corpus/sources/` is git-ignored third-party material.
    A missing corpus must read as a NAMED gap, never as a fixture that silently
    was not measured."""
    fixtures: list[Fixture] = []
    notices: list[str] = []

    if _AMI_SOURCE.exists():
        fixtures.append(
            Fixture(
                name="ami-ts3005a-full",
                text=_AMI_SOURCE.read_text(encoding="utf-8"),
                shape="transcript",
                title="TS3005a transcript",
                arms=("baseline", "chunked", "treatment", "treatment-chunked"),
            )
        )
    else:
        notices.append(
            f"transcript fixture SKIPPED: {_AMI_SOURCE} absent "
            "(run evals/decision_extraction/scripts/build_sources.py)"
        )

    prose = _CORPUS_DIR / "large-03-skills-vs-tools.md"
    if prose.exists():
        fixtures.append(
            Fixture(
                name="prose-large-03",
                text=prose.read_text(encoding="utf-8"),
                shape="prose",
                title="large-03-skills-vs-tools",
                arms=("baseline", "chunked"),
            )
        )
    else:
        notices.append(f"prose control SKIPPED: {prose} absent (git-ignored corpus)")

    return fixtures, notices


# --------------------------------------------------------------------------- #
# Arms
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Arm:
    name: str
    system_prompt: str
    chunk_threshold: int


def _load_module(path: Path, name: str) -> Any:
    """Import a sibling probe module for reuse.

    Imported rather than copied, on the precedent `evals/stage_attrition` set
    when it took `evals/participant_anchor`'s fixtures: a second copy of #715's
    treatment clause would drift silently, leaving two clauses with one name and
    no way to say which produced which number."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_arms() -> dict[str, Arm]:
    """The four arms, over two independent axes: prompt text and threshold.

    #715's clause comes from `evals/stage_attrition`'s own splice, including its
    splice assertion, which fires there before anything reaches here."""
    if not _STAGE_ATTRITION_PROBE.exists():  # pragma: no cover - defensive
        raise SystemExit(f"cannot find {_STAGE_ATTRITION_PROBE}")
    module = _load_module(_STAGE_ATTRITION_PROBE, "_stage_attrition_probe")
    borrowed = module.build_arms()
    baseline_prompt = borrowed["baseline"].system_prompt
    treatment_prompt = borrowed["treatment"].system_prompt
    shipped = concept_mod._CHUNK_THRESHOLD
    return {
        "baseline": Arm("baseline", baseline_prompt, shipped),
        "chunked": Arm("chunked", baseline_prompt, _CANDIDATE_THRESHOLD),
        "treatment": Arm("treatment", treatment_prompt, shipped),
        "treatment-chunked": Arm(
            "treatment-chunked", treatment_prompt, _CANDIDATE_THRESHOLD
        ),
    }


# --------------------------------------------------------------------------- #
# Recording transport
# --------------------------------------------------------------------------- #


class _ReplayResponse:
    """A read-once HTTP response the recorder has already drained.

    `OllamaClient.chat` calls `.read()` exactly once on whatever `urlopen`
    returns, so handing back the buffered bytes keeps the client's own parsing,
    its `done_reason` guard and its error mapping completely untouched."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body


class _RecordingTransport:
    """A `urlopen` stand-in that logs every chat call's generation counters.

    `OllamaClient` takes `urlopen` as a constructor argument, so this needs no
    production change and no monkeypatch: the request that goes out is the one a
    real `ingest` sends, byte for byte.

    Each call is tagged with `phase`, which the probe sets from
    `extract_concept_union`'s own `on_progress` hook. That hook fires
    immediately BEFORE the call it describes at every seam
    (`_report(...)` then the chat), so the label in flight when a request goes
    out is that request's phase. It is the pipeline's own public reporting seam,
    not a guess reconstructed from prompt sizes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.phase = "(before first reported phase)"

    def reset(self) -> None:
        self.calls = []
        self.phase = "(before first reported phase)"

    def __call__(self, request: Any, timeout: float | None = None) -> _ReplayResponse:
        started = time.monotonic()
        # The URL is `OllamaClient`'s own `{configured host}/api/chat`, built and
        # normalized there from user/env config -- never from document content.
        # Same trusted-host rationale the client's own call site records.
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
        body: bytes = response.read()
        seconds = time.monotonic() - started
        entry: dict[str, Any] = {
            "phase": self.phase,
            "prompt_tokens": None,
            "gen_tokens": None,
            "done_reason": None,
            "seconds": round(seconds, 1),
        }
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            # Leave the counters unread and let the client raise its own
            # malformed-response error. A recorder must never be the thing that
            # decides a run failed.
            self.calls.append(entry)
            return _ReplayResponse(body)
        if isinstance(data, dict):
            entry["prompt_tokens"] = data.get("prompt_eval_count")
            entry["gen_tokens"] = data.get("eval_count")
            entry["done_reason"] = data.get("done_reason")
        self.calls.append(entry)
        return _ReplayResponse(body)


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunRecord:
    fixture: str
    shape: str
    arm: str
    run: int
    chars: int
    chunk_threshold: int
    meeting_shaped: bool
    capped: bool
    """The cut-off ESCAPED the pipeline and failed the run.

    Only `_extract_once` can produce this. Every other call in the run swallows
    its own backend failures by contract -- `judge.select`'s D7 fail-closed
    rule, and the broad `except Exception` in `_reask_for_further_subjects` and
    `_capture_further_participants` -- so a truncated reply there degrades the
    result instead of raising. That is why #714's `OllamaGenerationCapped` is
    necessarily an EXTRACTION cut-off, and why `_CHUNK_THRESHOLD` is aimed at
    the right call.

    Since #828 those two swallowed failures are NAMED rather than discarded:
    they degrade to empty additions exactly as before, and the cause reaches
    `ExtractionReport.optional_call_failures`. Nothing here changes -- what
    escapes is still only the extraction call -- but a cut-off bonus call is
    no longer invisible outside this probe."""
    capped_phases: list[str]
    """Every phase whose reply Ollama cut off, INCLUDING the ones swallowed.

    Kept separately from `capped` because the swallowed ones are invisible to
    the caller and to the exception: a cut-off judge reply silently becomes
    `judge_status="failed"` and the run keeps its full unfiltered candidate set,
    while a cut-off participant pass silently finds nobody. Those are quality
    failures that look like clean runs, and a probe that only counted raised
    exceptions would report them as zero."""
    error: str
    objects: int
    seconds: float
    model: str
    calls: list[dict[str, Any]] = field(default_factory=list)


def run_fixture(
    fixture: Fixture,
    llm: OllamaClient,
    transport: _RecordingTransport,
    runs: int,
    model: str,
    arm: Arm,
) -> list[RunRecord]:
    """`runs` full `extract_concept_union` runs, recorded win or lose.

    A cut-off run is DATA, not an error to abort on: the rate is the
    measurement. Every other exception is recorded with its class name rather
    than folded into the cap rate -- an unreachable backend and a truncated
    reply are different facts, and #714 is only about the second."""
    records: list[RunRecord] = []
    meeting_shaped = _is_meeting_shaped(fixture.title, fixture.text)
    original_prompt = concept_mod._SYSTEM_PROMPT
    original_thresholds = (
        concept_mod._CHUNK_THRESHOLD,
        concept_mod._MEETING_CHUNK_THRESHOLD,
    )
    try:
        concept_mod._SYSTEM_PROMPT = arm.system_prompt
        # BOTH constants, because production selects between them by shape
        # (`_chunk_threshold_for`). Patching only `_CHUNK_THRESHOLD` would leave
        # the arm axis INERT on exactly the meeting-shaped fixtures this probe
        # exists to measure: every arm would silently run at the unpatched
        # `_MEETING_CHUNK_THRESHOLD` while the ledger recorded the arm's
        # intended value. The arm's threshold is the one under test, so it wins
        # on both sides of that branch.
        concept_mod._CHUNK_THRESHOLD = arm.chunk_threshold
        concept_mod._MEETING_CHUNK_THRESHOLD = arm.chunk_threshold
        # Asserted, not assumed: this reads back what the engine will ACTUALLY
        # resolve for this fixture, so a future third threshold, or a renamed
        # selector, fails loudly here instead of producing a sweep whose stored
        # `chunk_threshold` column is fiction.
        resolved = concept_mod._chunk_threshold_for(meeting_shaped=meeting_shaped)
        if resolved != arm.chunk_threshold:
            raise SystemExit(
                f"arm {arm.name!r} asked for threshold {arm.chunk_threshold} but "
                f"the engine resolves {resolved} for this fixture -- the arm axis "
                "does not reach the code under test"
            )
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
            # Read off the ledger, never inferred from the exception: the
            # exception names the ceiling, never the caller -- and three of the
            # four call sites never raise one at all.
            capped_phases = [
                call["phase"]
                for call in transport.calls
                if call["done_reason"] == "length"
            ]
            record = RunRecord(
                fixture=fixture.name,
                shape=fixture.shape,
                arm=arm.name,
                run=index,
                chars=len(fixture.text),
                chunk_threshold=arm.chunk_threshold,
                meeting_shaped=meeting_shaped,
                capped=capped,
                capped_phases=capped_phases,
                error=error,
                objects=objects,
                seconds=round(seconds, 1),
                model=model,
                calls=list(transport.calls),
            )
            records.append(record)
            status = "RAISED-CAP" if capped else (error or f"{objects} obj")
            worst = max((call["gen_tokens"] or 0 for call in record.calls), default=0)
            biggest = max(
                (call["prompt_tokens"] or 0 for call in record.calls), default=0
            )
            detail = (
                f" [cut off at: {', '.join(capped_phases)}]" if capped_phases else ""
            )
            print(
                f"      run {index}/{runs}: {status}{detail}, "
                f"{len(record.calls)} call(s), worst gen {worst}, "
                f"biggest prompt {biggest}, {record.seconds}s",
                flush=True,
            )
    finally:
        concept_mod._SYSTEM_PROMPT = original_prompt
        (
            concept_mod._CHUNK_THRESHOLD,
            concept_mod._MEETING_CHUNK_THRESHOLD,
        ) = original_thresholds
    return records


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def render(records: list[RunRecord], notices: list[str]) -> str:
    """One row per (fixture, arm), plus a per-phase ledger underneath.

    `worst gen` is printed beside the cap rate on purpose. A 0/3 cap rate whose
    worst call generated 8 100 of 8 192 tokens is one sampling draw from a
    failure, and a table showing only the rate would call it clean -- the same
    "a filtered view hides its complement" mistake that let #715 sit unseen in
    stored data for a day."""
    lines: list[str] = []
    for notice in notices:
        lines.append(f"! {notice}")
    if notices:
        lines.append("")

    header = (
        f"{'fixture':<18} {'arm':<18} {'thresh':>7} {'raised':>7} {'cut':>5} "
        f"{'cut off at':<30} {'worst gen':>10} {'max prompt':>11} "
        f"{'obj':>4} {'sec':>6}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    seen: list[tuple[str, str]] = []
    for record in records:
        key = (record.fixture, record.arm)
        if key not in seen:
            seen.append(key)
    for fixture_name, arm_name in seen:
        group = [r for r in records if r.fixture == fixture_name and r.arm == arm_name]
        first = group[0]
        capped = sum(1 for r in group if r.capped)
        cut = sum(1 for r in group if r.capped_phases)
        phases = sorted({p for r in group for p in r.capped_phases})
        objects = [r.objects for r in group if not r.capped and not r.error]
        worst = max(
            (call["gen_tokens"] or 0 for r in group for call in r.calls), default=0
        )
        biggest = max(
            (call["prompt_tokens"] or 0 for r in group for call in r.calls), default=0
        )
        lines.append(
            f"{fixture_name:<18} {arm_name:<18} {first.chunk_threshold:>7} "
            f"{f'{capped}/{len(group)}':>7} {f'{cut}':>5} "
            f"{(', '.join(phases) or '-'):<30} "
            f"{worst:>10} {biggest:>11} "
            # An arm whose every run failed retained nothing MEASURABLE, which
            # is not the same fact as an arm that measurably retained zero. A
            # 3/3-capped sweep -- the rate #714 was filed on -- would otherwise
            # render as a confident `0` in the very table built to keep failed
            # and degraded outcomes distinguishable.
            f"{(str(int(statistics.median(objects))) if objects else '-'):>4} "
            f"{int(statistics.median([r.seconds for r in group])):>6}"
        )

    errors = [r for r in records if r.error]
    if errors:
        lines.append("")
        lines.append("Non-cap failures (NOT counted as caps):")
        for record in errors:
            lines.append(
                f"  {record.fixture} [{record.arm}] run {record.run}: {record.error}"
            )

    lines.append("")
    lines.append("Per-phase worst generated tokens (every call, every run):")
    by_phase: dict[str, list[int]] = {}
    prompts: dict[str, list[int]] = {}
    for record in records:
        for call in record.calls:
            by_phase.setdefault(call["phase"], []).append(call["gen_tokens"] or 0)
            prompts.setdefault(call["phase"], []).append(call["prompt_tokens"] or 0)
    for phase in sorted(by_phase, key=lambda p: -max(by_phase[p])):
        gen = by_phase[phase]
        lines.append(
            f"  {phase:<34} calls {len(gen):>3}  worst gen {max(gen):>5}  "
            f"median gen {int(statistics.median(gen)):>5}  "
            f"max prompt {max(prompts[phase]):>6}"
        )

    lines.append("")
    lines.append(f"ceiling: max_generation_tokens = {_MAX_GENERATION_TOKENS}")
    return "\n".join(lines)


def render_verdict(records: list[RunRecord]) -> str:
    """State what the numbers decide about the authorized lever, in words.

    Deliberately not a pass/fail gate: #714 sets a CONSTANT, and the honest
    output of a measurement feeding a constant is the evidence plus the
    reading. The readings below lead to opposite code changes, so naming which
    one the data supports is the point."""
    if not records:
        return "no runs to read"
    lines = ["Reading", "-------"]

    raised = [r for r in records if r.capped]
    cut = [r for r in records if r.capped_phases]
    phases = sorted({p for r in records for p in r.capped_phases})

    lines.append(
        f"{len(raised)} of {len(records)} runs FAILED on a cut-off reply "
        f"(the failure #714 reports)"
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
            "errors by contract, so those runs returned a silently degraded "
            "result that reads as clean."
        )
    if not cut:
        lines.append(
            "NOTHING was cut off in this sweep. #714 did not reproduce here; do "
            "not change a shipped constant on it. Check `worst gen` against the "
            "ceiling before calling the band safe."
        )
        return "\n".join(lines)

    judge_capped = [p for p in phases if p.startswith("judging")]
    extract_capped = [p for p in phases if p.startswith("extracting")]

    def _rate(arm: str) -> tuple[int, int]:
        group = [r for r in records if r.arm == arm]
        return sum(1 for r in group if r.capped), len(group)

    for arm in ("baseline", "chunked", "treatment", "treatment-chunked"):
        hit, total = _rate(arm)
        if total:
            lines.append(f"  {arm:<18} {hit}/{total} capped")

    base_hit, base_total = _rate("baseline")
    chunk_hit, chunk_total = _rate("chunked")
    lines.append("")
    if judge_capped and not extract_capped:
        lines.append(
            "Cut off in the JUDGE call only, never in extraction. "
            "`judge_mod.select` receives the WHOLE `source_text` on both sides "
            "of `_CHUNK_THRESHOLD`, so lowering that constant does not shrink "
            "this prompt -- and the judge swallows the error, so this never "
            "surfaces as #714's failure at all. The authorized lever does not "
            "reach this; report it as a separate, silent defect."
        )
    elif extract_capped and not judge_capped:
        lines.append(
            "Cut off in EXTRACTION, which is exactly what `_CHUNK_THRESHOLD` "
            "governs, and the only call whose cut-off propagates. The lever is "
            "aimed at the right call."
        )
    else:
        lines.append(
            "Cut off in MORE THAN ONE phase. `_CHUNK_THRESHOLD` addresses the "
            "extraction share only; the judge share is a separate, silent "
            "defect that the same threshold change will not fix."
        )

    if chunk_total and base_total:
        lines.append("")
        if chunk_hit == 0 and base_hit > 0:
            lines.append(
                f"The chunked arm removes the cap ({base_hit}/{base_total} -> "
                f"{chunk_hit}/{chunk_total}). Lowering the threshold works on "
                "this evidence; check the prose control's object count for "
                "collateral before choosing the value."
            )
        elif chunk_hit >= base_hit and base_hit > 0:
            lines.append(
                f"The chunked arm does NOT remove the cap ({base_hit}/"
                f"{base_total} -> {chunk_hit}/{chunk_total}). Lowering "
                "`_CHUNK_THRESHOLD` is the wrong instrument for this failure."
            )
    return "\n".join(lines)


def load_results(path: Path) -> list[RunRecord]:
    records: list[RunRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(RunRecord(**json.loads(line)))
    return records


def write_results(records: list[RunRecord], stamp: str, model: str) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = model.replace(":", "-").replace("/", "-")
    path = RESULTS_DIR / f"generation-ceiling-{stamp}-{slug}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return path


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    """Prove the ledger attributes a cap to the phase that caused it.

    Every failure this guards is a silent-success failure. A recorder that
    dropped the counters would print `None` headroom on every row and the table
    would still render. A recorder that consumed the body without replaying it
    would turn every run into a malformed-reply ERROR, and #714's rate would
    read as zero. A phase tag that lagged its call by one would blame the wrong
    stage -- and blaming extraction instead of the judge is precisely the
    mistake that would ship a constant that changes nothing."""
    transport = _RecordingTransport()

    def _ok_body(content: str, gen: int, reason: str) -> bytes:
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

    shipped_thresholds = (
        concept_mod._CHUNK_THRESHOLD,
        concept_mod._MEETING_CHUNK_THRESHOLD,
    )

    def _scenario(
        scripted: list[bytes],
        *,
        text: str = "line one\nline two",
        title: str = "T",
        threshold: int | None = None,
    ) -> tuple[RunRecord, list[bytes]]:
        sent: list[bytes] = []

        def _fake(request: Any, timeout: float | None = None) -> _ReplayResponse:
            sent.append(request.data)
            return _ReplayResponse(scripted[min(len(sent) - 1, len(scripted) - 1)])

        real_urlopen = urllib.request.urlopen
        urllib.request.urlopen = _fake  # type: ignore[assignment]
        try:
            client = OllamaClient(
                model="fake",
                max_generation_tokens=_MAX_GENERATION_TOKENS,
                urlopen=transport,
            )
            fixture = Fixture(
                name="fake",
                text=text,
                shape="prose",
                title=title,
                arms=(),
            )
            arm = Arm(
                "baseline",
                concept_mod._SYSTEM_PROMPT,
                shipped_thresholds[0] if threshold is None else threshold,
            )
            records = run_fixture(fixture, client, transport, 1, "fake", arm)
        finally:
            urllib.request.urlopen = real_urlopen
        return records[0], sent

    # Scenario 1 -- the judge is cut off. `judge.select`'s D7 contract swallows
    # the exception, so the run SUCCEEDS with a silently degraded result. The
    # ledger must still name it; `capped` must not.
    judge_cut, sent = _scenario(
        [
            _ok_body(candidates, 100, "stop"),  # pass 1/2
            _ok_body(candidates, 100, "stop"),  # pass 2/2
            _ok_body("[]", 4242, "length"),  # the judge, cut off
        ]
    )
    if judge_cut.capped:
        print("FAIL: a swallowed judge cut-off was reported as a run failure")
        return 1
    if not any(p.startswith("judging") for p in judge_cut.capped_phases):
        print(
            f"FAIL: judge cut-off missing from the ledger: {judge_cut.capped_phases!r}"
        )
        return 1
    if len(judge_cut.calls) != 3:
        print(f"FAIL: expected 3 recorded calls, got {len(judge_cut.calls)}")
        return 1
    if (
        judge_cut.calls[0]["gen_tokens"] != 100
        or judge_cut.calls[0]["prompt_tokens"] != 1234
    ):
        print("FAIL: counters were not recorded off the response")
        return 1
    if b'"num_predict": 8192' not in sent[0]:
        print("FAIL: the ceiling was not forwarded on the recorded request")
        return 1

    # Scenario 2 -- extraction is cut off. `_extract_once` propagates
    # unswallowed, which is the ONLY way #714's failure reaches a caller.
    extract_cut, _ = _scenario([_ok_body("[{", 4242, "length")])
    if not extract_cut.capped:
        print("FAIL: an extraction cut-off did not surface as a run failure")
        return 1
    if not any(p.startswith("extracting") for p in extract_cut.capped_phases):
        print(
            "FAIL: extraction cut-off attributed to "
            f"{extract_cut.capped_phases!r}, expected an extracting phase"
        )
        return 1

    # Scenario 3 -- a MEETING-SHAPED fixture at the arm's shipped threshold.
    # Production selects the boundary by shape (`_chunk_threshold_for`), so an
    # arm that patched only `_CHUNK_THRESHOLD` would leave this run resolving
    # the unpatched `_MEETING_CHUNK_THRESHOLD` and CHUNKING, while the ledger
    # still claimed the arm's value. Scenarios 1 and 2 cannot see that: their
    # fixture is two prose lines and never reaches the meeting-shaped branch.
    speakers = ("Ana", "Beto", "Caro")
    turns = [f"{speakers[i % 3]}: turno {i:04d} " + "palabra " * 5 for i in range(240)]
    meeting_text = "\n".join(turns)
    if not _is_meeting_shaped("TS3005a transcript", meeting_text):
        print("FAIL: the self-test's own meeting fixture is not meeting-shaped")
        return 1
    if not shipped_thresholds[1] < len(meeting_text) < shipped_thresholds[0]:
        print(
            "FAIL: the self-test's meeting fixture "
            f"({len(meeting_text)} chars) no longer sits between the two "
            f"shipped thresholds {shipped_thresholds}"
        )
        return 1
    meeting_run, _ = _scenario(
        [_ok_body("[]", 10, "stop")] * 8,
        text=meeting_text,
        title="TS3005a transcript",
        threshold=shipped_thresholds[0],
    )
    chunk_calls = [c for c in meeting_run.calls if c["phase"].startswith("extracting")]
    if any("chunk" in c["phase"] for c in chunk_calls):
        print(
            "FAIL: the arm asked for the prose threshold and the run chunked "
            "anyway -- the arm axis does not reach a meeting-shaped fixture"
        )
        return 1

    restored = (
        concept_mod._CHUNK_THRESHOLD,
        concept_mod._MEETING_CHUNK_THRESHOLD,
    )
    if restored != shipped_thresholds:
        print("FAIL: an arm's threshold patch was not restored")
        return 1

    fixtures, notices = build_fixtures()
    print(f"self-test OK ({len(fixtures)} fixture(s) available)")
    for notice in notices:
        print(f"  ! {notice}")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument(
        "--fixture", action="append", help="measure only this fixture (repeatable)"
    )
    parser.add_argument(
        "--arm", action="append", help="measure only this arm (repeatable)"
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
        print(render(stored, []))
        print()
        print(render_verdict(stored))
        return 0

    fixtures, notices = build_fixtures()
    if args.fixture:
        wanted = set(args.fixture)
        unknown = wanted - {f.name for f in fixtures}
        if unknown:
            raise SystemExit(f"unknown fixture(s): {', '.join(sorted(unknown))}")
        fixtures = [f for f in fixtures if f.name in wanted]
    if not fixtures:
        raise SystemExit("no fixtures available; see the notices above")

    arms = build_arms()
    if args.arm:
        unknown_arms = set(args.arm) - set(arms)
        if unknown_arms:
            raise SystemExit(f"unknown arm(s): {', '.join(sorted(unknown_arms))}")

    transport = _RecordingTransport()
    llm = OllamaClient(
        model=args.model,
        max_generation_tokens=_MAX_GENERATION_TOKENS,
        urlopen=transport,
    )
    print(f"model {args.model}, {args.runs} run(s) per fixture/arm\n")
    for notice in notices:
        print(f"  ! {notice}")

    records: list[RunRecord] = []
    for fixture in fixtures:
        wanted_arms = [a for a in fixture.arms if not args.arm or a in set(args.arm)]
        for arm_name in wanted_arms:
            arm = arms[arm_name]
            print(
                f"  [{arm_name}] {fixture.name} ({len(fixture.text)} chars, "
                f"threshold {arm.chunk_threshold})"
            )
            records.extend(
                run_fixture(fixture, llm, transport, args.runs, args.model, arm)
            )

    print()
    print(render(records, notices))
    print()
    print(render_verdict(records))
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    print(f"stored {write_results(records, stamp, args.model)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
