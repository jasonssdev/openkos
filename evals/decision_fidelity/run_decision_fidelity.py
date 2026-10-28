"""The run driver of the Decision-fidelity measurement (#1649).

Runs the two model arms the pre-registration fixes, `A0` (the production
decision pass over the unchanged prompt) and `A1` (the same with the value
clause of `prompt_value_clause.md`), 15 runs each over the 12 development
meetings, and stores them in the schema `fidelity_core.load_runs` reads.

    uv run python -u evals/decision_fidelity/run_decision_fidelity.py --self-test
    uv run python -u evals/decision_fidelity/run_decision_fidelity.py \\
        --transcripts <dir> --preflight                       # no model
    uv run python -u evals/decision_fidelity/run_decision_fidelity.py \\
        --transcripts <dir> --wait-gpu                        # the registered run

What it guarantees, each pinned by the unit suite:

- ELIGIBILITY. Only the registered development meetings are read; anything
  else (TS3012, a thesis evaluation or reserve series, a calibration meeting)
  is refused before a call is made.
- REGISTERED CONFIGURATION. `qwen3:8b`, generation ceiling 8192, context window
  12288, `chat_timeout` 600 s, production `retry_after_cap`, no sampling seed.
  A production default that has moved away from these is named and refuses.
- INTERLEAVED. `A0 r1, A1 r1, A0 r2, A1 r2, ...`, so drift hits both arms.
- RESUMABLE. Each (arm, run, meeting) cell is stored the moment it finishes, so
  a stop (the budget, a crash, a closed laptop) loses at most one cell and a
  rerun of the same command continues. A resume refuses when the model digest,
  a prompt hash or a pinned source file moved under the run.
- BUDGET. The accumulated model time, across resumes, stops the drive at 15 h
  and the arms are assembled as `PARTIAL -- NOT THE PRE-REGISTERED RESULT`.
- STAMPED. Each arm carries `harness_stamp.build_stamp` (commit, model digest,
  prompt hash of the bytes actually sent); an unpinned stamp refuses to start.
- OUTSIDE THE REPOSITORY. Results and logs are written under
  `~/openkos-eval-runs/decision-fidelity/` by default; a path inside the
  repository is refused. A stored run holds Decisions, never a transcript.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(HERE))
sys.path.append(str(REPO_ROOT / "evals"))

import fidelity_core as core  # noqa: E402
import harness_stamp  # noqa: E402

from openkos import config as openkos_config  # noqa: E402
from openkos.extraction import concept as concept_mod  # noqa: E402
from openkos.extraction import decision_pass  # noqa: E402
from openkos.llm import ollama  # noqa: E402

ARMS: Final = ("A0", "A1")
"""REGISTERED. The two arms that need the model."""

DEFAULT_RUNS: Final = 15
"""REGISTERED. Runs per LLM arm."""

DEFAULT_MODEL: Final = "qwen3:8b"
"""REGISTERED. The production default."""

BUDGET_HOURS: Final = 15.0
"""REGISTERED. Stop and report short at 15 h of accumulated model time."""

REGISTERED_WINDOWS: Final = 101
"""The window count the registration states per run. The pinned transcripts
give 99 (ES2015 33, IS1004 28, TS3009 38): the prose miscounted. The sha256
pins are the registered input, so the figure is a disclosed note, not a gate."""

REGISTERED_MAX_GENERATION_TOKENS: Final = 8192
REGISTERED_CONTEXT_WINDOW: Final = 12288
REGISTERED_CHAT_TIMEOUT_S: Final = 600.0

REGISTRATION_COMMIT: Final = "f71779a3"
"""The `main` commit the pre-registration merged at; the baseline pin."""

PINNED_FILES: Final = (
    "src/openkos/extraction/decision_pass.py",
    "src/openkos/prompts/extraction/decision_pass.md",
    "src/openkos/llm/ollama.py",
)
"""REGISTERED. If any differs at the run commit from the registration commit,
RESULTS says so and the affected comparison is re-run, not patched."""

CLAUSE_PATH: Final = HERE / "prompt_value_clause.md"
SOURCES_PATH: Final = REPO_ROOT / "evals" / "retrieval_gold" / "bundle_sources.json"
DEFAULT_OUT: Final = Path.home() / "openkos-eval-runs" / "decision-fidelity" / "runs"
PARTIAL_NOTICE: Final = "PARTIAL -- NOT THE PRE-REGISTERED RESULT"
REPLY_HEAD_CHARS: Final = 300
GPU_POLL_S: Final = 60.0

Log = Callable[[str], None]


class DriftError(RuntimeError):
    """The model or a pinned source moved between a run and its resume."""


class StampError(RuntimeError):
    """A stamp that cannot tie a result to what it measured."""


class BackendDown(RuntimeError):
    """The backend was unreachable; nothing was stored for the cell."""


# --- the treatment and the configuration -------------------------------------


def treated_prompt(base: str, clause: str) -> str:
    """`base` with its single `- value:` bullet replaced by `clause`."""
    lines = base.split("\n")
    hits = [n for n, line in enumerate(lines) if line.startswith("- value:")]
    if len(hits) != 1:
        raise ValueError(
            f"the prompt must carry exactly one '- value:' bullet, found {len(hits)}"
        )
    lines[hits[0]] = clause.strip()
    return "\n".join(lines)


def arm_prompt(arm: str, base: str, clause: str) -> str:
    """The system prompt `arm` sends."""
    if arm == "A0":
        return base
    if arm == "A1":
        return treated_prompt(base, clause)
    raise ValueError(f"unknown arm {arm!r}")


def frozen_config_problems() -> list[str]:
    """Where a production default no longer equals the registered value."""
    problems: list[str] = []
    checks = (
        (
            "generation ceiling",
            openkos_config.DEFAULT_MAX_GENERATION_TOKENS,
            REGISTERED_MAX_GENERATION_TOKENS,
        ),
        (
            "context window",
            openkos_config.DEFAULT_CONTEXT_WINDOW,
            REGISTERED_CONTEXT_WINDOW,
        ),
        ("chat timeout", ollama.DEFAULT_TIMEOUT, REGISTERED_CHAT_TIMEOUT_S),
        (
            "config chat timeout",
            openkos_config.DEFAULT_CHAT_TIMEOUT,
            REGISTERED_CHAT_TIMEOUT_S,
        ),
    )
    for name, shipped, registered in checks:
        if shipped != registered:
            problems.append(f"{name}: shipped {shipped}, registered {registered}")
    default = (
        inspect.signature(decision_pass.run_decision_pass)
        .parameters["retry_after_cap"]
        .default
    )
    if default is not True:
        problems.append("retry_after_cap: production default is no longer True")
    return problems


def require_outside_repo(path: Path) -> Path:
    """`path` if it is not inside the repository, else `SystemExit`."""
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit(
            f"REFUSED: {resolved} is inside the repository; results and logs "
            "are written outside the repository"
        )
    return resolved


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pin_report(
    repo_root: Path = REPO_ROOT, *, runner: Callable[..., Any] = subprocess.run
) -> dict[str, Any]:
    """Hashes of the pinned sources, and whether git says they equal the
    registration commit's (`None` when git could not answer)."""
    files = {name: _sha256(repo_root / name) for name in PINNED_FILES}
    try:
        done = runner(
            [
                "git",
                "diff",
                "--quiet",
                REGISTRATION_COMMIT,
                "HEAD",
                "--",
                *PINNED_FILES,
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        unchanged: bool | None = done.returncode == 0
    except OSError, subprocess.SubprocessError:
        unchanged = None
    return {
        "files": files,
        "registration_commit": REGISTRATION_COMMIT,
        "unchanged": unchanged,
    }


# --- windows and the schedule -------------------------------------------------


def pass_windows(text: str, title: str) -> list[str]:
    """The windows `concept._run_decision_pass` builds, by the same helpers."""
    if concept_mod.fans_out(text, source_title=title):
        return concept_mod._chunk_lines(text)
    return [text]


def check_texts(texts: Mapping[str, str]) -> None:
    """Refuse an ineligible meeting, then one the production gate would not
    treat as a meeting (the pass would not fire: a false zero)."""
    for meeting in texts:
        core.require_eligible(meeting)
        if meeting not in core.GOLD_MEETINGS:
            raise core.MeetingRefused(
                f"{meeting!r} is a calibration meeting: no arm may read it"
            )
    unshaped = [m for m, t in texts.items() if not concept_mod._is_meeting_shaped(m, t)]
    if unshaped:
        raise SystemExit(
            f"REFUSED: not meeting-shaped by the production gate: {', '.join(unshaped)}; "
            "the pass would not fire and a run would measure a false zero"
        )


def schedule(arms: Sequence[str], runs: int) -> list[tuple[int, str]]:
    """`(run, arm)` in the interleaved order `r1 A0, r1 A1, r2 A0, ...`."""
    return [(run, arm) for run in range(1, runs + 1) for arm in arms]


def load_transcript_pins(path: Path = SOURCES_PATH) -> dict[str, str]:
    """`{meeting: sha256}` of the rendered transcripts the registration pins."""
    return {s["id"]: s["sha256"] for s in _read_json(path)["sources"]}


def pin_problems(texts: Mapping[str, str], pins: Mapping[str, str]) -> list[str]:
    """Meetings whose bytes are not the pinned bytes (or have no pin)."""
    problems: list[str] = []
    for meeting, text in texts.items():
        pinned = pins.get(meeting)
        if pinned is None:
            problems.append(f"{meeting}: no sha256 pin")
        elif hashlib.sha256(text.encode("utf-8")).hexdigest() != pinned:
            problems.append(f"{meeting}: sha256 differs from bundle_sources.json")
    return problems


def preflight(
    texts: Mapping[str, str], pins: Mapping[str, str]
) -> tuple[list[str], list[str]]:
    """Model-free `(problems, notes)` about the data and the configuration.

    A problem refuses the run. A note must be disclosed in RESULTS: the window
    count is a registered figure, but the pinned bytes are the registered
    input, so a count that disagrees with the prose is a miscount to report,
    not a reason to run on other bytes."""
    check_texts(texts)
    problems = frozen_config_problems() + pin_problems(texts, pins)
    notes: list[str] = []
    windows = sum(len(pass_windows(t, m)) for m, t in texts.items())
    if set(texts) == set(core.GOLD_MEETINGS) and windows != REGISTERED_WINDOWS:
        notes.append(
            f"windows per run: {windows} on the pinned transcripts, the registration "
            f"states {REGISTERED_WINDOWS}; disclose in RESULTS"
        )
    return problems, notes


# --- storage ------------------------------------------------------------------


def _write_json(path: Path, document: Any) -> None:
    """Atomic: a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def cell_path(out_dir: Path, arm: str, run: int, meeting: str) -> Path:
    return out_dir / "cells" / f"{arm}-r{run:02d}" / f"{meeting}.json"


def _spent(out_dir: Path) -> float:
    state = out_dir / "state.json"
    return float(_read_json(state)["spent_s"]) if state.is_file() else 0.0


def _stamp_drift(
    old: Mapping[str, Any], new: Mapping[str, Any], old_pin: Any, new_pin: Any
) -> list[str]:
    drift: list[str] = []
    if old["model"].get("digest") != new["model"].get("digest"):
        drift.append("model digest")
    if old["prompts"] != new["prompts"]:
        drift.append("prompt hash")
    if old_pin["files"] != new_pin["files"]:
        drift.append("pinned source file")
    return drift


def _check_stamp(
    out_dir: Path, arm: str, stamp: dict[str, Any], pin: dict[str, Any]
) -> None:
    problems = harness_stamp.stamp_problems(stamp)
    if problems:
        raise StampError(f"{arm}: " + "; ".join(problems))
    stored = out_dir / f"stamp-{arm}.json"
    if stored.is_file():
        previous = _read_json(stored)
        drift = _stamp_drift(previous["stamp"], stamp, previous["pin"], pin)
        if drift:
            raise DriftError(
                f"{arm}: {', '.join(drift)} changed since the first cell was stored; "
                "the stored cells no longer describe this setup"
            )
    else:
        _write_json(stored, {"stamp": stamp, "pin": pin})


def assemble(
    out_dir: Path,
    arm: str,
    meetings: Sequence[str],
    runs_wanted: int,
    *,
    model: str,
) -> dict[str, Any]:
    """The arm's stored document: every COMPLETE run, in `load_runs` shape."""
    runs: list[dict[str, Any]] = []
    for run in range(1, runs_wanted + 1):
        paths = [cell_path(out_dir, arm, run, m) for m in meetings]
        if not all(p.is_file() for p in paths):
            continue
        cells = {m: _read_json(p) for m, p in zip(meetings, paths, strict=True)}
        runs.append(
            {
                "run": run,
                "meetings": {
                    m: {
                        "decisions": c["decisions"],
                        "windows": c["windows"],
                        "calls": c["calls"],
                        "failure": c["failure"],
                        "wall_s": c["wall_s"],
                    }
                    for m, c in cells.items()
                },
            }
        )
    stored = _read_json(out_dir / f"stamp-{arm}.json")
    partial = len(runs) < runs_wanted
    document: dict[str, Any] = {
        "arm": arm,
        "model": model,
        "runs_requested": runs_wanted,
        "runs_complete": len(runs),
        "partial": partial,
        "stamp": stored["stamp"],
        "pin": stored["pin"],
        "runs": runs,
    }
    if partial:
        document["partial_notice"] = PARTIAL_NOTICE
    return document


# --- driving ------------------------------------------------------------------


@dataclass(frozen=True)
class DriveResult:
    status: str
    """`complete`, or `budget` when the accumulated model time ran out."""
    cells_run: int
    spent_s: float


def _call_entry(
    observation: decision_pass.CallObservation, latency_s: float | None
) -> dict[str, Any]:
    diagnostics = observation.diagnostics
    entry: dict[str, Any] = {
        "window": observation.window_index,
        "attempt": observation.attempt,
        "latency_s": None if latency_s is None else round(latency_s, 2),
        "failure": observation.failure,
        "done_reason": getattr(diagnostics, "done_reason", None),
        "eval_count": getattr(diagnostics, "eval_count", None),
    }
    if observation.failure is not None and observation.reply is not None:
        entry["reply_head"] = observation.reply[:REPLY_HEAD_CHARS]
    return entry


def run_cell(
    text: str,
    meeting: str,
    prompt: str,
    llm: Any,
    clock: Callable[[], float],
    recorder: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """One meeting under one prompt: the record to store. The module prompt is
    swapped for the call and restored."""
    windows = pass_windows(text, meeting)
    log: list[dict[str, Any]] = []
    latencies: list[float] = []
    unreachable = False

    def on_call(observation: decision_pass.CallObservation) -> None:
        nonlocal unreachable
        unreachable = unreachable or bool(
            observation.failure and observation.failure.endswith("BackendUnavailable")
        )
        log.append(_call_entry(observation, latencies[-1] if latencies else None))

    class _Timed:
        def chat(self, messages: Sequence[Any]) -> str:
            began = clock()
            try:
                return str(llm.chat(messages))
            finally:
                latencies.append(clock() - began)

    # `_SYSTEM_PROMPT` is a `Final` the pass reads at call time; the swap is the
    # treatment, so it goes through the module namespace and is always undone.
    namespace = vars(decision_pass)
    original = namespace["_SYSTEM_PROMPT"]
    namespace["_SYSTEM_PROMPT"] = prompt
    started = clock()
    try:
        outcome = decision_pass.run_decision_pass(
            windows, _Timed(), on_call=on_call, diagnostics=recorder
        )
    finally:
        namespace["_SYSTEM_PROMPT"] = original
    if unreachable:
        raise BackendDown(f"the backend was unreachable on {meeting}")
    return {
        "meeting": meeting,
        "windows": len(windows),
        "calls": len(latencies),
        "failure": outcome.failure,
        "wall_s": round(clock() - started, 2),
        "call_latencies_s": [round(v, 2) for v in latencies],
        "call_log": log,
        "decisions": [
            {"title": r.title, "description": r.description, "body": r.body}
            for r in outcome.results
        ],
    }


def drive(
    *,
    texts: Mapping[str, str],
    out_dir: Path,
    log_dir: Path,
    model: str,
    runs: int,
    llm_factory: Callable[[], Any],
    stamp_fn: Callable[[str, str], dict[str, Any]],
    pin: dict[str, Any],
    clock: Callable[[], float],
    budget_s: float,
    base_prompt: str,
    clause: str,
    log: Log,
    arms: Sequence[str] = ARMS,
    recorder: Callable[[], Any] | None = None,
) -> DriveResult:
    """Run every missing (arm, run, meeting) cell, then assemble the arms."""
    check_texts(texts)
    meetings = [m for m in core.GOLD_MEETINGS if m in texts]
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "drive.log"

    def say(line: str) -> None:
        log(line)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    prompts = {arm: arm_prompt(arm, base_prompt, clause) for arm in arms}
    for arm in arms:
        _check_stamp(out_dir, arm, stamp_fn(arm, prompts[arm]), pin)
    llm = llm_factory()
    prior = _spent(out_dir)
    began = clock()
    cells_run = 0
    status = "complete"

    def spent() -> float:
        return prior + (clock() - began)

    for run, arm in schedule(arms, runs):
        for meeting in meetings:
            path = cell_path(out_dir, arm, run, meeting)
            if path.is_file():
                continue
            if spent() >= budget_s:
                status = "budget"
                break
            cell = run_cell(texts[meeting], meeting, prompts[arm], llm, clock, recorder)
            cell.update(arm=arm, run=run)
            _write_json(path, cell)
            cells_run += 1
            _write_json(out_dir / "state.json", {"spent_s": round(spent(), 2)})
            say(
                f"{arm} r{run:02d} {meeting}: decisions={len(cell['decisions'])} "
                f"windows={cell['windows']} calls={cell['calls']} "
                f"failure={cell['failure']} {cell['wall_s']}s "
                f"(spent {spent() / 3600:.2f}h of {budget_s / 3600:.1f}h)"
            )
        if status == "budget":
            break
    _write_json(out_dir / "state.json", {"spent_s": round(spent(), 2)})
    for arm in arms:
        _write_json(
            out_dir / f"runs-{arm}.json",
            assemble(out_dir, arm, meetings, runs, model=model),
        )
    say(f"drive {status}: {cells_run} cell(s) this session")
    return DriveResult(status, cells_run, spent())


def wait_for_gpu(
    probe: Callable[[], Mapping[str, Any]],
    *,
    sleep: Callable[[float], None],
    log: Log,
    poll_s: float = GPU_POLL_S,
) -> None:
    """Block until the local Ollama reports no loaded model: it serializes, and
    nothing else may use it during the run."""
    while probe().get("models"):
        log("waiting for the GPU: another model is loaded")
        sleep(poll_s)


# --- command line --------------------------------------------------------------


def load_transcripts(directory: Path) -> dict[str, str]:
    texts: dict[str, str] = {}
    for meeting in core.GOLD_MEETINGS:
        path = directory / f"{core.require_eligible(meeting)}.md"
        if not path.is_file():
            raise SystemExit(f"no transcript for {meeting}: {path} does not exist")
        texts[meeting] = path.read_text(encoding="utf-8")
    return texts


def _client(model: str, recorder: Any) -> Any:
    return ollama.OllamaClient(
        model=model,
        timeout=REGISTERED_CHAT_TIMEOUT_S,
        max_generation_tokens=REGISTERED_MAX_GENERATION_TOKENS,
        context_window=REGISTERED_CONTEXT_WINDOW,
        diagnostics_sink=recorder.record,
    )


class _Recorder:
    def __init__(self) -> None:
        self.latest: Any = None

    def record(self, diagnostics: Any) -> None:
        self.latest = diagnostics

    def last(self) -> Any:
        latest, self.latest = self.latest, None
        return latest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--transcripts", type=Path)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--budget-hours", type=float, default=BUDGET_HOURS)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--wait-gpu", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.transcripts is None:
        parser.error("--transcripts is required unless --self-test")
    out_dir = require_outside_repo(args.out_dir)
    log_dir = require_outside_repo(args.log_dir or out_dir.parent / "logs")
    texts = load_transcripts(args.transcripts)
    problems, notes = preflight(texts, load_transcript_pins())
    windows = sum(len(pass_windows(t, m)) for m, t in texts.items())
    print(f"preflight: {len(texts)} meetings, {windows} windows per run")
    for note in notes:
        print(f"NOTE: {note}")
    if problems:
        raise SystemExit("REFUSED: " + "; ".join(problems))
    pin = pin_report()
    print(f"pin: unchanged since {REGISTRATION_COMMIT}: {pin['unchanged']}")
    if pin["unchanged"] is not True:
        print(
            "NOTE: a pinned source differs from the registration commit; RESULTS must say so"
        )
    if args.preflight:
        print("preflight: OK (no model was called)")
        return 0

    base = decision_pass._SYSTEM_PROMPT
    clause = CLAUSE_PATH.read_text(encoding="utf-8").strip()
    recorder = _Recorder()
    if args.wait_gpu:
        wait_for_gpu(
            lambda: harness_stamp.ollama_get_json("/api/ps"),
            sleep=time.sleep,
            log=print,
        )

    def stamp_fn(arm: str, prompt: str) -> dict[str, Any]:
        prompt_id = "extraction/decision_pass" + (
            "+value_clause" if arm == "A1" else ""
        )
        return harness_stamp.build_stamp(model=args.model, prompts={prompt_id: prompt})

    result = drive(
        texts=texts,
        out_dir=out_dir,
        log_dir=log_dir,
        model=args.model,
        runs=args.runs,
        llm_factory=lambda: _client(args.model, recorder),
        stamp_fn=stamp_fn,
        pin=pin,
        clock=time.monotonic,
        budget_s=args.budget_hours * 3600,
        base_prompt=base,
        clause=clause,
        log=print,
        recorder=recorder.last,
    )
    print(f"{result.status}: stored under {out_dir}")
    return 0


# --- self-test -----------------------------------------------------------------


class _EchoLLM:
    """Answers every window with one verbatim-evidence Decision; records the
    system prompts it was sent."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def chat(self, messages: Sequence[Any]) -> str:
        self.systems.append(messages[0]["content"])
        return json.dumps(
            [
                {
                    "subject": "casing",
                    "value": "Use rubber for the casing",
                    "evidence": "Alright, so we'll go with rubber for the casing then.",
                }
            ]
        )


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def self_test() -> int:
    """Model-free: a fake backend, a step clock and a synthetic transcript."""
    base = "- subject: s\n- value: one complete sentence.\n- evidence: e"
    clause = "- value: one complete sentence. Keep the figure."

    def text(meeting: str) -> str:
        turns = "".join(
            f"{'PM' if n % 2 == 0 else 'ID'}: Turn number {n} of the meeting today.\n\n"
            for n in range(12)
        )
        return (
            f"---\nmeeting_id: {meeting}\n---\n\n{turns}"
            "PM: Alright, so we'll go with rubber for the casing then.\n"
        )

    texts = {m: text(m) for m in ("ES2015a", "ES2015b")}

    def stamp(arm: str, prompt: str) -> dict[str, Any]:
        return {
            "stamp_version": harness_stamp.STAMP_VERSION,
            "harness": {"commit": "c" * 40, "dirty": False},
            "model": {"name": DEFAULT_MODEL, "digest": "self-test"},
            "prompts": [harness_stamp.prompt_identity(f"decision_pass/{arm}", prompt)],
        }

    class Step:
        now = 0.0

        def __call__(self) -> float:
            self.now += 1.0
            return self.now

    def run(root: Path, llm: _EchoLLM, budget_s: float) -> DriveResult:
        return drive(
            texts=texts,
            out_dir=root / "out",
            log_dir=root / "logs",
            model=DEFAULT_MODEL,
            runs=2,
            llm_factory=lambda: llm,
            stamp_fn=stamp,
            pin={"files": {}, "registration_commit": "-", "unchanged": True},
            clock=Step(),
            budget_s=budget_s,
            base_prompt=base,
            clause=clause,
            log=lambda _line: None,
        )

    _check(
        not frozen_config_problems(), "a production default moved off the registration"
    )
    _check(
        schedule(ARMS, 2) == [(1, "A0"), (1, "A1"), (2, "A0"), (2, "A1")],
        "runs are not interleaved",
    )
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first, second = _EchoLLM(), _EchoLLM()
        _check(
            run(root, first, 5.0).status == "budget",
            "the budget did not stop the drive",
        )
        _check(
            run(root, second, 1e9).status == "complete", "the resume did not complete"
        )
        whole_root = root / "whole"
        whole = _EchoLLM()
        _check(
            run(whole_root, whole, 1e9).status == "complete", "the full drive failed"
        )
        _check(
            len(first.systems) + len(second.systems) == len(whole.systems),
            "a resume re-ran or skipped a cell",
        )
        for arm in ARMS:
            document = _read_json(root / "out" / f"runs-{arm}.json")
            _check(
                len(core.load_runs(document)) == 2, f"{arm}: two complete runs expected"
            )
            _check(not document["partial"], f"{arm}: still partial after the resume")
        _check(
            set(whole.systems) == {base, treated_prompt(base, clause)},
            "the arms did not send the baseline and the treated prompt",
        )
        _check(clause != decision_pass._SYSTEM_PROMPT, "the module prompt leaked")
        for bad in ("TS3012a", "TS3005a", "ES2002a"):
            try:
                check_texts({bad: text(bad)})
            except core.MeetingRefused:
                continue
            raise AssertionError(f"{bad} was not refused")
    print("self-test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
