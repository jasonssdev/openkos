"""Why does the selector judge fail, and does a cold model start cause it? (#754)

MANUAL eval tool (NOT pytest, NOT part of the shipped package). Drives the
REAL judge -- `openkos.extraction.judge` over `openkos.llm.ollama.OllamaClient`
-- and answers TWO questions the shipped code cannot answer about itself:

  1. WHICH of `select()`'s three failure causes fires? The function degrades
     an `llm.chat` exception, an unparseable reply, and a wrong-shaped reply
     to the SAME `None`, so `judge selection unavailable` names an outcome
     and hides the cause.
  2. Does a COLD model start change that rate? #754 reports the failure on
     file 1 of a batch with `ollama ps` empty beforehand.

## Why this probe exists at all, and what it is guarding against

#644 filed the same symptom, hypothesised cold loading, and the hypothesis
was FALSIFIED by measurement: the real cause was the model echoing a whole
candidate line, which `_salvage_full_line_echoes` now resolves. #754 filed it
again with `ollama ps` evidence. That evidence establishes the model was
cold; it does NOT establish that being cold is what broke the call, because
nothing recorded what came back.

So this probe records the RAW REPLY and the exception, and classifies the
failure with production's own parse chain. A retry is worth shipping for a
transport error and is close to useless against a model that reliably
answers in the wrong shape -- the fix depends on the cause.

## The judge call is FROZEN, on purpose

`--capture` runs the real union pipeline once and freezes the exact
`(source_text, candidates)` the judge received into `judge_call.json`. Every
measured run then replays THAT call. Extraction is stochastic (~40% yield
variance, #694), so re-extracting per run would vary the judge's input and
the arms would differ by more than their arm.

## What the arms can and cannot show

`cold` unloads the model before each call; `warm` leaves it resident. A
difference between them is evidence about cold starts. NO difference is the
more useful outcome to be able to state honestly: it would mean #754's
`ollama ps` evidence is a coincidence of batch position, and the cause is
whatever the classifier names in BOTH arms.

Usage:

    uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --self-test
    uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --capture
    uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --runs 15
    uv run python -u evals/judge_cold_start/run_judge_cold_start_probe.py --rescore

**Use `-u`.** Same reason as every other harness here: piping a run through
`tee` makes Python buffer, and a long run then looks hung.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Final

# APPENDED, not inserted at zero: an insert would put the evals root AHEAD
# of this harness's own directory, so a module added at the root would shadow
# a same-named one beside this file. #742 recorded that exact defect.
sys.path.append(str(Path(__file__).resolve().parent.parent))

from harness_report import arm_identity_line

from openkos.extraction import concept as concept_mod
from openkos.extraction import judge as judge_mod
from openkos.extraction.concept import ExtractionResult, extract_concept_union
from openkos.llm import parsing
from openkos.llm.base import LLMBackend
from openkos.llm.ollama import OllamaClient

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
JUDGE_CALL_PATH = HERE / "judge_call.json"

_AMI_SOURCE = HERE.parent / "decision_extraction" / "sources" / "TS3005b.transcript.txt"
_PAD_SOURCE = HERE.parent / "decision_extraction" / "sources" / "TS3005a.transcript.txt"
"""The sibling fixture, captured only to supply REAL spare candidates for the
candidate-count arm (`--pad-to`). Its source text is never measured."""
"""The 20 KB transcript. Deliberately the LARGER of the two AMI fixtures:
#754's failing file produced 23 candidates, and the judge prompt carries the
whole source plus every candidate's description, so the prompt size this
probe must reproduce is the large one."""

_MAX_GENERATION_TOKENS: Final = 8_192
_CONTEXT_WINDOW: Final = 12_288
"""`openkos.yaml.template`'s shipped pair, restated as literals for the same
reason `evals/ingest_concurrency/` restates `num_ctx`: this probe is about
how the judge behaves under PRODUCTION's bounds, and it must keep meaning
that in a checkout where the constants have moved. `--self-test` pins them
against `openkos.config` so a drift is reported, not silently measured."""

_MODEL: Final = "qwen3:8b"
_DEFAULT_RUNS: Final = 15
"""n=15 per arm. Five was measured to swing wider than the effects this repo
chases: one arm spanned 0.25 against ITSELF on the contradiction harness."""

COLD = "cold"
WARM = "warm"

OK = "ok"
CHAT_ERROR = "chat_error"
UNPARSEABLE = "unparseable"
WRONG_SHAPE = "wrong_shape"
"""The four outcomes. The last three are the causes `select()` collapses into
one `None`, named after the branch that produces each:
`chat_error` = the `except Exception` around `llm.chat`;
`unparseable` = `parsing.extract_json_object` returned `None`;
`wrong_shape` = `_validate_selection` returned `None`.

`unparseable` covers MORE than malformed JSON, and the first self-test run
found it: `extract_json_object` looks for an OBJECT, so a bare
`["title", ...]` array -- the exact shape the judge prompt spends a sentence
forbidding -- returns `None` there and never reaches `_validate_selection`.
Reporting that as "unparseable" would send someone hunting truncation when
the reply is well-formed JSON of the wrong type, so `unparseable_detail`
splits the two. It is an ANNOTATION, not a fifth outcome: production makes
no such distinction and the classification must keep matching production's
chain exactly."""

NO_JSON = "no-json"
JSON_NOT_OBJECT = "json-not-object"

_REPLY_HEAD_CHARS: Final = 600
"""How much of a reply is stored verbatim. Enough to read the shape and the
first few kept titles; not so much that a 20-candidate full-line echo buries
the JSONL. `reply_chars` carries the true length either way."""


# --------------------------------------------------------------------------- #
# Capture: freeze one real judge call
# --------------------------------------------------------------------------- #


def capture(llm: OllamaClient, source_path: Path) -> dict[str, Any]:
    """Run the REAL union pipeline once and freeze the judge's input.

    Wraps `concept._select_with_progress` -- the one seam that sees the full
    candidate list on its way to the judge -- exactly as
    `evals/participant_anchor/` does. Production is untouched: the wrapper
    delegates to the real function and records what passes through.
    """
    source_text = source_path.read_text(encoding="utf-8")
    source_title = source_path.stem
    seen: dict[str, Any] = {}
    original = concept_mod._select_with_progress

    def _recording(
        source_text: str,
        judge_input: list[ExtractionResult],
        llm: LLMBackend,
        on_progress: Callable[[str], None] | None,
    ) -> tuple[str, ...] | None:
        # Parameter NAMES mirror `_select_with_progress` exactly, not just the
        # types: mypy compares callables by name too, and a rebind that only
        # matched positionally would break any keyword call site.
        seen["source_text"] = source_text
        seen["candidates"] = [
            {"type": c.type, "title": c.title, "description": c.description}
            for c in judge_input
        ]
        return original(source_text, judge_input, llm, on_progress)

    concept_mod._select_with_progress = _recording
    try:
        extract_concept_union(source_text, source_title=source_title, llm=llm)
    finally:
        concept_mod._select_with_progress = original

    if "candidates" not in seen:
        raise SystemExit(
            "capture failed: the judge was never called. The union path skips "
            "it for an empty or single-candidate merged set, so this source "
            "produced fewer than two candidates -- pick a richer one."
        )
    seen["source_title"] = source_title
    # REPO-RELATIVE, never absolute: `judge_call.json` is committed, and an
    # absolute path would publish the machine's username and directory layout
    # into repository history for no measurement benefit.
    seen["source_path"] = _repo_relative(source_path)
    return seen


def _repo_relative(path: Path) -> str:
    """`path` relative to the repository root, or its bare name if it somehow
    sits outside — never an absolute path (see `capture`)."""
    root = HERE.parent.parent
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return path.name


def _as_candidates(rows: list[dict[str, str]]) -> tuple[judge_mod.JudgeCandidate, ...]:
    return tuple(
        judge_mod.JudgeCandidate(
            type=c["type"], title=c["title"], description=c["description"]
        )
        for c in rows
    )


def load_judge_call(
    *, pad_to: int | None = None
) -> tuple[str, tuple[judge_mod.JudgeCandidate, ...]]:
    """The frozen judge call, optionally PADDED to `pad_to` candidates.

    The first sweep replayed a 9-candidate call and failed zero times in 30
    runs. #754's failing call carried 23, which is the one variable this
    probe had not put under a dose: candidate count drives both the prompt's
    tail and how many titles the model must echo back, and the reply is where
    a shape or a length failure would show.

    Padding draws from `pad_candidates` -- a SECOND real capture, from the
    sibling AMI fixture -- never from invented strings. A synthetic candidate
    would be a fact about my prose, and the judge is being asked to read
    candidates the way extraction actually writes them. The pad is recorded
    in the arm name, so a padded run can never be read as the plain one.
    """
    if not JUDGE_CALL_PATH.is_file():
        raise SystemExit(
            f"no frozen judge call at {JUDGE_CALL_PATH}. Run --capture first."
        )
    data = json.loads(JUDGE_CALL_PATH.read_text(encoding="utf-8"))
    candidates = _as_candidates(data["candidates"])
    if pad_to is None or pad_to <= len(candidates):
        return data["source_text"], candidates

    pool = _as_candidates(data.get("pad_candidates", []))
    have = {c.title for c in candidates}
    extra = tuple(c for c in pool if c.title not in have)[: pad_to - len(candidates)]
    if len(candidates) + len(extra) < pad_to:
        raise SystemExit(
            f"cannot pad to {pad_to}: the frozen call has {len(candidates)} "
            f"candidates and only {len(extra)} distinct spares. Run "
            "`--capture --pad-source` to add a second real capture."
        )
    return data["source_text"], candidates + extra


# --------------------------------------------------------------------------- #
# Classification: production's own parse chain, one stage at a time
# --------------------------------------------------------------------------- #


def _unparseable_detail(reply: str) -> str:
    """Why `extract_json_object` said no: no JSON at all, or JSON that is not
    an object. Probe-side annotation only -- see `UNPARSEABLE`."""
    try:
        json.loads(reply.strip())
    except (json.JSONDecodeError, ValueError):
        return NO_JSON
    return JSON_NOT_OBJECT


def classify_reply(
    reply: str,
    candidates: list[judge_mod.JudgeCandidate] | tuple[judge_mod.JudgeCandidate, ...],
) -> tuple[str, tuple[str, ...] | None, str | None]:
    """`(outcome, selected_titles, unparseable_detail)` for a reply that
    arrived without raising.

    Calls production's OWN `parsing.extract_json_object`,
    `judge._validate_selection` and `judge._salvage_full_line_echoes` in
    `select()`'s order, so the only thing this probe adds is WHICH stage said
    no. `--self-test` pins that agreement: for every synthetic reply, this
    returning `ok` must coincide with production `select()` returning a
    non-`None` value, or the probe is measuring a chain production does not
    run.
    """
    parsed = parsing.extract_json_object(reply)
    if parsed is None:
        return UNPARSEABLE, None, _unparseable_detail(reply)
    validated = judge_mod._validate_selection(parsed)
    if validated is None:
        return WRONG_SHAPE, None, None
    return OK, judge_mod._salvage_full_line_echoes(validated, candidates), None


# --------------------------------------------------------------------------- #
# Ollama residency control
# --------------------------------------------------------------------------- #


def _post(host: str, path: str, body: dict[str, Any], *, timeout: float) -> Any:
    request = urllib.request.Request(  # noqa: S310
        f"{host}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())


def loaded_models(host: str, *, timeout: float = 10.0) -> tuple[str, ...]:
    """Model names currently resident, per `/api/ps` -- the same surface
    #754's evidence quotes."""
    try:
        with urllib.request.urlopen(  # noqa: S310
            f"{host}/api/ps", timeout=timeout
        ) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return ()
    return tuple(str(m.get("name", "")) for m in data.get("models", []))


def unload(host: str, model: str, *, timeout: float = 60.0) -> bool:
    """Evict `model`, then WAIT until `/api/ps` agrees it is gone.

    `keep_alive: 0` is Ollama's documented unload. The readback is the point:
    an unload that had not taken effect yet would silently turn a `cold` run
    into a warm one, and the arm would report a difference it never tested.
    """
    try:
        _post(host, "/api/generate", {"model": model, "keep_alive": 0}, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if model not in loaded_models(host):
            return True
        time.sleep(0.5)
    return False


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


@dataclass
class RunRecord:
    arm: str
    run: int
    model: str
    resident_before: tuple[str, ...]
    unload_confirmed: bool | None
    """`None` on the warm arm, which never asks. `False` means the cold arm
    could NOT prove the model was evicted -- that run measured an unknown
    residency and is excluded from the arm, never counted as cold."""
    latency_s: float
    outcome: str
    error_class: str | None
    error_message: str | None
    reply_chars: int | None
    reply_head: str | None
    unparseable_detail: str | None
    selected: int | None
    salvaged: int | None
    """How many kept strings `_salvage_full_line_echoes` had to rewrite --
    the #644 defect, counted rather than assumed absent."""

    @property
    def usable(self) -> bool:
        """A run whose arm is what it claims to be."""
        return self.arm == WARM or self.unload_confirmed is True


def run_once(
    llm: OllamaClient,
    source_text: str,
    candidates: tuple[judge_mod.JudgeCandidate, ...],
    *,
    arm: str,
    run: int,
    host: str,
    model: str,
) -> RunRecord:
    unload_confirmed: bool | None = None
    if arm == COLD:
        unload_confirmed = unload(host, model)
    resident = loaded_models(host)

    started = time.monotonic()
    reply: str | None = None
    error_class: str | None = None
    error_message: str | None = None
    try:
        reply = llm.chat(judge_mod._build_judge_messages(source_text, candidates))
    except Exception as exc:  # broad on purpose: mirrors `select`'s own D7 catch
        error_class = type(exc).__name__
        error_message = str(exc)[:400]
    latency = time.monotonic() - started

    if reply is None:
        return RunRecord(
            arm=arm,
            run=run,
            model=model,
            resident_before=resident,
            unload_confirmed=unload_confirmed,
            latency_s=latency,
            outcome=CHAT_ERROR,
            error_class=error_class,
            error_message=error_message,
            reply_chars=None,
            reply_head=None,
            unparseable_detail=None,
            selected=None,
            salvaged=None,
        )

    outcome, selected, detail = classify_reply(reply, candidates)
    salvaged: int | None = None
    if selected is not None:
        titles = {judge_mod._normalize_title(c.title) for c in candidates}
        parsed = parsing.extract_json_object(reply) or {}
        raw = judge_mod._validate_selection(parsed) or ()
        salvaged = sum(1 for t in raw if judge_mod._normalize_title(t) not in titles)
    return RunRecord(
        arm=arm,
        run=run,
        model=model,
        resident_before=resident,
        unload_confirmed=unload_confirmed,
        latency_s=latency,
        outcome=outcome,
        error_class=None,
        error_message=None,
        reply_chars=len(reply),
        reply_head=reply[:_REPLY_HEAD_CHARS],
        unparseable_detail=detail,
        selected=len(selected) if selected is not None else None,
        salvaged=salvaged,
    )


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


@dataclass
class ArmScore:
    arm: str
    runs: int
    excluded: int
    """Cold runs whose eviction could not be confirmed. Reported, never
    silently dropped: a hidden exclusion turns a small arm into a smaller one
    without saying so."""
    outcomes: dict[str, int]

    @property
    def failures(self) -> int:
        return self.runs - self.outcomes.get(OK, 0)

    @property
    def failure_rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0


def score(records: Sequence[RunRecord], arm: str) -> ArmScore:
    in_arm = [r for r in records if r.arm == arm]
    usable = [r for r in in_arm if r.usable]
    outcomes: dict[str, int] = {}
    for record in usable:
        outcomes[record.outcome] = outcomes.get(record.outcome, 0) + 1
    return ArmScore(
        arm=arm,
        runs=len(usable),
        excluded=len(in_arm) - len(usable),
        outcomes=outcomes,
    )


def verdict(cold: ArmScore, warm: ArmScore) -> str:
    """The reading, stated so a zero cannot be mistaken for a pass."""
    if not cold.runs and not warm.runs:
        return (
            "UNFALSIFIABLE -- no usable run in either arm, so nothing was put "
            "to the test."
        )
    if not cold.runs or not warm.runs:
        # A SINGLE-ARM sweep is a legitimate experiment, not a broken one:
        # `--arms warm` exists to isolate the candidate-count question from
        # the cold-start one the paired sweep already answered. Reading it as
        # UNFALSIFIABLE would discard a real result -- but it must still say
        # plainly which question went unasked, or a warm-only run would read
        # as evidence about cold starts.
        only = cold if cold.runs else warm
        outcome = (
            "did not fail once"
            if not only.failures
            else f"failed {only.failures} of {only.runs} time(s)"
        )
        return (
            f"SINGLE ARM (`{only.arm}`) -- the judge {outcome}. This says "
            "nothing about cold starts either way: the other arm was not run, "
            "so the comparison that would answer that question was never made."
        )
    if not cold.failures and not warm.failures:
        return (
            "NOT REPRODUCED -- the judge did not fail once in either arm. "
            "#754's failure is real (it is in the e2e log), so this frozen "
            "call is not the one that triggers it; widen the fixture before "
            "concluding anything about cold starts."
        )
    if cold.failure_rate > warm.failure_rate and not warm.failures:
        return (
            "COLD START IMPLICATED -- failures occur only on the evicted arm. "
            "A retry is the direct fix: the second call finds the model warm."
        )
    if warm.failures and abs(cold.failure_rate - warm.failure_rate) < 0.2:
        return (
            "COLD START NOT THE CAUSE -- the warm arm fails at a comparable "
            "rate, so #754's `ollama ps` evidence is batch position, not "
            "causation. Read the outcome column: the named cause is what a "
            "fix must attack, and a retry only helps if it is transient."
        )
    return (
        "MIXED -- both arms fail, at different rates. Cold start contributes "
        "but is not the whole cause; the outcome column names the rest."
    )


def render(records: Sequence[RunRecord], *, host: str, parallel: str) -> str:
    cold, warm = score(records, COLD), score(records, WARM)
    lines = [
        "# Judge failure causes, cold vs warm (#754)",
        "",
        arm_identity_line(
            max_generation_tokens=_MAX_GENERATION_TOKENS,
            context_window=_CONTEXT_WINDOW,
            extra=(f"host `{host}`", f"`OLLAMA_NUM_PARALLEL` {parallel}"),
        ),
        "",
        f"| {'arm':<6} | runs | excluded | ok | chat_error | unparseable "
        "| wrong_shape | fail rate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm_score in (cold, warm):
        counts = arm_score.outcomes
        lines.append(
            f"| {arm_score.arm} | {arm_score.runs} | {arm_score.excluded} "
            f"| {counts.get(OK, 0)} | {counts.get(CHAT_ERROR, 0)} "
            f"| {counts.get(UNPARSEABLE, 0)} | {counts.get(WRONG_SHAPE, 0)} "
            f"| {arm_score.failure_rate:.2f} |"
        )
    lines += ["", f"**Verdict:** {verdict(cold, warm)}", ""]

    failures = [r for r in records if r.usable and r.outcome != OK]
    if failures:
        lines += ["## Every failure, verbatim", ""]
        for record in failures:
            lines.append(
                f"- `{record.arm}` run {record.run}: **{record.outcome}** "
                f"({record.latency_s:.1f}s)"
            )
            if record.error_class:
                lines.append(f"  - `{record.error_class}`: {record.error_message}")
            if record.unparseable_detail is not None:
                lines.append(f"  - detail: `{record.unparseable_detail}`")
            if record.reply_head is not None:
                lines.append(
                    f"  - reply ({record.reply_chars} chars): `{record.reply_head!r}`"
                )
    salvaged = sum(r.salvaged or 0 for r in records if r.usable)
    lines += [
        "",
        f"Full-line echoes salvaged across all runs: **{salvaged}** "
        "(#644's defect, counted rather than assumed gone).",
    ]
    return "\n".join(lines)


def write_results(records: Sequence[RunRecord], *, stamp: str) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"runs-{stamp}-{_MODEL.replace(':', '-')}.json"
    out.write_text(
        json.dumps(
            {"stamp": stamp, "model": _MODEL, "rows": [asdict(r) for r in records]},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return out


def load_records(path: Path) -> list[RunRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        RunRecord(**{**row, "resident_before": tuple(row["resident_before"])})
        for row in data["rows"]
    ]


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #


def _self_test() -> int:
    """Prove the classifier, the arm bookkeeping and the verdict, no model."""
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    from openkos import config as config_mod

    check(
        "the restated ceiling still matches production",
        (_MAX_GENERATION_TOKENS, _CONTEXT_WINDOW),
        (config_mod.DEFAULT_MAX_GENERATION_TOKENS, config_mod.DEFAULT_CONTEXT_WINDOW),
    )

    candidates = (
        judge_mod.JudgeCandidate("Concept", "Trazabilidad", "d1"),
        judge_mod.JudgeCandidate("Decision", "Cifrado de respaldos", "d2"),
    )

    # THE ANTI-DRIFT GUARD. For every reply shape, this probe's classifier
    # must agree with production `select()` on the ONE thing production can
    # say: usable or not. A probe whose chain has drifted from `select`'s
    # would name a cause for a call production never made.
    cases: list[tuple[str, str, str, str | None]] = [
        ("clean selection", '{"keep": ["Trazabilidad"]}', OK, None),
        ("prose, no JSON", "Sure! I kept the first one.", UNPARSEABLE, NO_JSON),
        # The prompt forbids this shape by name, and production rejects it one
        # stage EARLIER than the shape validator -- the detail is what keeps
        # the report from calling well-formed JSON malformed.
        ("bare array", '["Trazabilidad"]', UNPARSEABLE, JSON_NOT_OBJECT),
        ("empty keep", '{"keep": []}', WRONG_SHAPE, None),
        ("non-string entries", '{"keep": [1, 2]}', WRONG_SHAPE, None),
        ("truncated JSON", '{"keep": ["Trazabi', UNPARSEABLE, NO_JSON),
        (
            "full-line echo",
            "{\"keep\": [\"type='Concept' title='Trazabilidad' description='d1'\"]}",
            OK,
            None,
        ),
    ]

    class _Canned:
        def __init__(self, reply: str) -> None:
            self.reply = reply

        def chat(self, messages: object) -> str:
            return self.reply

        @property
        def locality(self) -> object:
            raise NotImplementedError

    for label, reply, want, want_detail in cases:
        got, _selected, got_detail = classify_reply(reply, candidates)
        check(f"classifier: {label}", got, want)
        check(f"detail: {label}", got_detail, want_detail)
        production = judge_mod.select("src", candidates, _Canned(reply))
        check(
            f"agrees with production select(): {label}",
            production is not None,
            want == OK,
        )

    # The echo must be RESOLVED to the bare title, not merely accepted --
    # accepting it while leaving the line unresolved is what #644 fixed, and
    # a probe reporting `ok` for a string the union cannot match would hide a
    # regression as a success.
    _outcome, selected, _detail = classify_reply(
        "{\"keep\": [\"type='Concept' title='Trazabilidad' description='d1'\"]}",
        candidates,
    )
    check("the echo resolves to the bare title", selected, ("Trazabilidad",))

    # Arm bookkeeping: an unconfirmed eviction is EXCLUDED, not counted cold.
    def _rec(arm: str, outcome: str, *, confirmed: bool | None) -> RunRecord:
        return RunRecord(
            arm=arm,
            run=1,
            model="m",
            resident_before=(),
            unload_confirmed=confirmed,
            latency_s=1.0,
            outcome=outcome,
            error_class=None,
            error_message=None,
            reply_chars=10,
            reply_head="{}",
            unparseable_detail=None,
            selected=None,
            salvaged=0,
        )

    mixed = [
        _rec(COLD, CHAT_ERROR, confirmed=True),
        _rec(COLD, OK, confirmed=False),
        _rec(WARM, OK, confirmed=None),
    ]
    cold_score = score(mixed, COLD)
    check("an unconfirmed eviction is excluded", cold_score.runs, 1)
    check("and the exclusion is reported", cold_score.excluded, 1)
    check("the cold arm failed its one usable run", cold_score.failure_rate, 1.0)
    check("the warm arm is clean", score(mixed, WARM).failure_rate, 0.0)

    # The verdicts, including the two that must NOT read as a pass.
    empty_cold = ArmScore(COLD, 0, 0, {})
    check(
        "a single-arm sweep reports its own result, not UNFALSIFIABLE",
        verdict(empty_cold, ArmScore(WARM, 3, 0, {OK: 3})).startswith("SINGLE ARM"),
        True,
    )
    check(
        "and says the cold question went unasked",
        "nothing about cold starts"
        in verdict(empty_cold, ArmScore(WARM, 3, 0, {OK: 3})),
        True,
    )
    check(
        "two empty arms ARE unfalsifiable",
        verdict(empty_cold, ArmScore(WARM, 0, 0, {})).startswith("UNFALSIFIABLE"),
        True,
    )
    check(
        "zero failures everywhere is NOT REPRODUCED, never a pass",
        verdict(
            ArmScore(COLD, 3, 0, {OK: 3}), ArmScore(WARM, 3, 0, {OK: 3})
        ).startswith("NOT REPRODUCED"),
        True,
    )
    check(
        "failures only when cold implicates cold",
        verdict(
            ArmScore(COLD, 4, 0, {OK: 1, CHAT_ERROR: 3}), ArmScore(WARM, 4, 0, {OK: 4})
        ).startswith("COLD START IMPLICATED"),
        True,
    )
    check(
        "both arms failing alike exonerates cold",
        verdict(
            ArmScore(COLD, 4, 0, {OK: 2, WRONG_SHAPE: 2}),
            ArmScore(WARM, 4, 0, {OK: 2, WRONG_SHAPE: 2}),
        ).startswith("COLD START NOT THE CAUSE"),
        True,
    )

    # The report renders, and renders ONE identity line.
    report = render(mixed, host="http://h", parallel="1")
    check("the report names its ceiling", "Generation ceiling `8192`" in report, True)
    check("the report names its window", "context window `12288`" in report, True)

    if failures:
        print("SELF-TEST FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        "SELF-TEST PASSED: the classifier names the stage that rejected each "
        "reply and agrees with production `select()` on every shape, the "
        "full-line echo resolves to a bare title, an unconfirmed eviction is "
        "excluded and reported rather than counted cold, and neither the "
        "empty-arm nor the no-failure case renders as a pass."
    )
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=_DEFAULT_RUNS)
    parser.add_argument("--model", default=_MODEL)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--parallel", default="1", help="server OLLAMA_NUM_PARALLEL")
    parser.add_argument("--capture", action="store_true")
    parser.add_argument(
        "--pad-source",
        action="store_true",
        help="with --capture: capture the SIBLING fixture into pad_candidates",
    )
    parser.add_argument(
        "--pad-from",
        choices=("main", "sibling"),
        default="sibling",
        help="with --capture --pad-source: which fixture supplies the spares",
    )
    parser.add_argument(
        "--pad-to",
        type=int,
        default=None,
        help="replay with this many candidates, padded from pad_candidates",
    )
    parser.add_argument(
        "--arms",
        choices=("both", "cold", "warm"),
        default="both",
        help=(
            "which arms to run. `warm` isolates the CANDIDATE-COUNT question "
            "from the cold-start one, which the 9-candidate sweep already "
            "answered on 15 confirmed evictions"
        ),
    )
    parser.add_argument("--rescore", type=Path, default=None)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.rescore is not None:
        stored = load_records(args.rescore)
        print(render(stored, host=args.host, parallel=args.parallel))
        return 0

    llm = OllamaClient(
        model=args.model,
        host=args.host,
        max_generation_tokens=_MAX_GENERATION_TOKENS,
        context_window=_CONTEXT_WINDOW,
    )

    if args.capture:
        source_path = _AMI_SOURCE
        if args.pad_source:
            # Either fixture may supply spares. TS3005a is the smaller one and
            # proposes only a couple of objects, so reaching a realistic
            # 23-candidate list needs repeated captures of the LARGER source,
            # whose stochastic yield differs run to run.
            source_path = _AMI_SOURCE if args.pad_from == "main" else _PAD_SOURCE
        if not source_path.is_file():
            print(f"error: no source at {source_path}", file=sys.stderr)
            return 2
        captured = capture(llm, source_path)
        if args.pad_source:
            # A SECOND real capture, kept beside the frozen call rather than
            # replacing it: the measured call must stay byte-identical across
            # sweeps, or the candidate-count arm would also change the source.
            existing = json.loads(JUDGE_CALL_PATH.read_text(encoding="utf-8"))
            # ACCUMULATES across invocations, union by title. Extraction is
            # stochastic (~40% yield variance, #694), so repeated captures of
            # the same fixture propose partly different objects -- which is
            # the only way to reach a realistic 23-candidate list out of real
            # extraction output rather than invented strings.
            pool = {c["title"]: c for c in existing.get("pad_candidates", [])}
            for candidate in captured["candidates"]:
                pool.setdefault(candidate["title"], candidate)
            existing["pad_candidates"] = list(pool.values())
            existing["pad_source_path"] = captured["source_path"]
            JUDGE_CALL_PATH.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(
                f"captured {len(captured['candidates'])} from "
                f"{source_path.name}; spare pool now "
                f"{len(existing['pad_candidates'])} -> {JUDGE_CALL_PATH}"
            )
            return 0
        JUDGE_CALL_PATH.write_text(
            json.dumps(captured, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"froze {len(captured['candidates'])} candidates over "
            f"{len(captured['source_text'])} source chars -> {JUDGE_CALL_PATH}"
        )
        return 0

    source_text, candidates = load_judge_call(pad_to=args.pad_to)
    if len(candidates) < 2:
        print(
            f"error: the frozen call has {len(candidates)} candidate(s); "
            "production skips the judge below two, so this would measure a "
            "call that never happens.",
            file=sys.stderr,
        )
        return 2
    print(
        f"replaying a frozen judge call: {len(candidates)} candidates over "
        f"{len(source_text)} source chars",
        flush=True,
    )

    records: list[RunRecord] = []
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    arms = {"both": (COLD, WARM), "cold": (COLD,), "warm": (WARM,)}[args.arms]
    for arm in arms:
        for run in range(1, args.runs + 1):
            record = run_once(
                llm,
                source_text,
                candidates,
                arm=arm,
                run=run,
                host=args.host,
                model=args.model,
            )
            records.append(record)
            flag = "" if record.usable else "  [EXCLUDED: eviction unconfirmed]"
            print(
                f"  {arm} {run}/{args.runs}: {record.outcome} "
                f"{record.latency_s:.1f}s{flag}",
                flush=True,
            )

    report = render(records, host=args.host, parallel=args.parallel)
    saved = write_results(records, stamp=stamp)
    saved.with_suffix(".md").write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"\nSaved raw runs: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
