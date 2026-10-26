"""Measures whether a re-ask after a generation cap recovers (#1564).

Question: when the decision pass's first ask of a window hits the generation
cap (`BackendGenerationCapped`), how often does the production re-ask return a
non-capped, parseable reply, and what does that re-ask cost in wall time? The
pre-registration is `PREREGISTRATION-1564.md`; read it before trusting a number
from here. Nothing here changes a production default.

Method: instead of replaying whole meetings (a capping window costs about 250 s,
and a meeting holds 6-11 windows), the harness asks single windows through
`decision_pass.run_decision_pass` with `retry_after_cap=True`, which is the
production path. A SWEEP asks every window of the population once; the windows
whose first ask capped (the cap-prone windows) are then REPLAYED round-robin
until the target number of capped first asks is reached or a budget runs out.

Usage:

    uv run python -u evals/transcript_decisions/run_cap_reask.py --self-test
    uv run python -u evals/transcript_decisions/run_cap_reask.py \\
        --transcripts <dir of MEETING.md> --results-dir <dir>
    uv run python -u evals/transcript_decisions/run_cap_reask.py --verdict <trials.json>
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any, Final

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.append(str(REPO_ROOT / "evals"))

import run_transcript_decisions as harness  # noqa: E402
from harness_stamp import TimedBackend, build_stamp  # noqa: E402

from openkos.config import (  # noqa: E402
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_MAX_GENERATION_TOKENS,
)
from openkos.extraction import decision_pass  # noqa: E402
from openkos.llm.base import LLMBackend  # noqa: E402
from openkos.llm.ollama import OllamaClient  # noqa: E402

DEFAULT_MODEL: Final = "qwen3:8b"
DEFAULT_MEETINGS: Final = ("TS3009a", "ES2015d", "TS3009b", "TS3009d")
TARGET_CAPPED: Final = 15
MAX_REPLAY_ASKS: Final = 60
MAX_SWEEPS: Final = 2
BUDGET_S: Final = 9000.0
"""Model-time ceiling for the whole run (2.5 h); the pre-registration forecasts
about 2.1 h and says to stop and report short rather than overrun."""

# The registered decision rule (PREREGISTRATION-1564.md). Fixed before any call.
LOWER_BOUND_BAR: Final = 0.30
COST_PER_ITEM_BAR_S: Final = 250.0
Z_95: Final = 1.959964


# --- classification and statistics -----------------------------------------


def classify(entry: dict[str, Any]) -> str:
    """`ok`, `capped`, `unparseable` or `error` for one stored call entry."""
    failure = entry.get("failure")
    if failure is None:
        return "ok"
    if "GenerationCapped" in failure:
        return "capped"
    if "unparseable" in failure:
        return "unparseable"
    return "error"


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson score interval for `k` of `n`; `(0, 1)` when `n` is 0."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + Z_95**2 / n
    centre = (p + Z_95**2 / (2 * n)) / denom
    half = Z_95 * math.sqrt(p * (1 - p) / n + Z_95**2 / (4 * n**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def summarize_trial(
    meeting: str,
    window: int,
    phase: str,
    call_log: Sequence[dict[str, Any]],
    items_admitted: int,
    wall_s: float,
) -> dict[str, Any]:
    """One trial: a window's first ask and, when production would, its re-ask."""
    attempts = [dict(e) for e in call_log]
    first = classify(attempts[0]) if attempts else "error"
    second = classify(attempts[1]) if len(attempts) > 1 else None
    return {
        "meeting": meeting,
        "window": window,
        "phase": phase,
        "first": first,
        "second": second,
        "recovered": first == "capped" and second == "ok",
        "items_admitted": items_admitted,
        "wall_s": round(wall_s, 2),
        "attempts": attempts,
    }


def verdict(trials: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Apply the registered rule to the capped-first trials.

    KEEP the re-ask when the recovery rate's Wilson lower bound is at least
    `LOWER_BOUND_BAR` AND the re-ask's wall seconds per recovered item is at
    most `COST_PER_ITEM_BAR_S`. SKIP it when the upper bound is below the bar,
    or when nothing was recovered, or when the cost per recovered item exceeds
    the bar. Otherwise INCONCLUSIVE, and the default stays."""
    capped = [t for t in trials if t["first"] == "capped"]
    n = len(capped)
    k = sum(1 for t in capped if t["recovered"])
    low, high = wilson(k, n)
    reask_s = [
        a["latency_s"]
        for t in capped
        for a in t["attempts"]
        if a["attempt"] == 2 and a.get("latency_s") is not None
    ]
    recovered_items = [t["items_admitted"] for t in capped if t["recovered"]]
    mean_reask_s = sum(reask_s) / len(reask_s) if reask_s else 0.0
    mean_items = sum(recovered_items) / len(recovered_items) if recovered_items else 0.0
    rate = k / n if n else 0.0
    expected_items = rate * mean_items
    cost_per_item = mean_reask_s / expected_items if expected_items > 0 else math.inf
    if n == 0:
        outcome = "INCONCLUSIVE"
    elif high < LOWER_BOUND_BAR or k == 0 or cost_per_item > COST_PER_ITEM_BAR_S:
        outcome = "SKIP"
    elif low >= LOWER_BOUND_BAR:
        outcome = "KEEP"
    else:
        outcome = "INCONCLUSIVE"
    return {
        "capped_first_asks": n,
        "recovered": k,
        "rate": round(rate, 4),
        "wilson_95": [round(low, 4), round(high, 4)],
        "mean_reask_wall_s": round(mean_reask_s, 2),
        "mean_items_per_recovered_reask": round(mean_items, 2),
        "expected_items_per_reask": round(expected_items, 3),
        "reask_seconds_per_recovered_item": (
            None if math.isinf(cost_per_item) else round(cost_per_item, 1)
        ),
        "lower_bound_bar": LOWER_BOUND_BAR,
        "cost_per_item_bar_s": COST_PER_ITEM_BAR_S,
        "verdict": outcome,
    }


# --- running ---------------------------------------------------------------


def run_trial(
    meeting: str,
    index: int,
    window: str,
    phase: str,
    llm: TimedBackend,
    recorder: harness.DiagnosticsRecorder | None,
) -> dict[str, Any]:
    """Ask one window through the production path, re-ask after a cap included."""
    call_log: list[dict[str, Any]] = []
    started = time.monotonic()

    def on_call(observation: decision_pass.CallObservation) -> None:
        latency = llm.call_latencies_s[-1] if llm.call_latencies_s else None
        call_log.append(harness.call_entry(observation, latency))

    outcome = decision_pass.run_decision_pass(
        [window],
        llm,
        on_call=on_call,
        diagnostics=recorder.last if recorder is not None else None,
        retry_after_cap=True,
    )
    return summarize_trial(
        meeting,
        index,
        phase,
        call_log,
        len(outcome.results),
        time.monotonic() - started,
    )


def run_measurement(
    windows: dict[str, list[str]],
    llm: TimedBackend,
    recorder: harness.DiagnosticsRecorder | None,
    *,
    target: int = TARGET_CAPPED,
    max_replay: int = MAX_REPLAY_ASKS,
    max_sweeps: int = MAX_SWEEPS,
    budget_s: float = BUDGET_S,
    log: Callable[[str], None] = print,
    persist: Callable[[list[dict[str, Any]]], None] = lambda _trials: None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, Any]], str]:
    """Sweep, then replay the cap-prone windows. Returns `(trials, stop_reason)`."""
    trials: list[dict[str, Any]] = []
    started = clock()

    def capped_count() -> int:
        return sum(1 for t in trials if t["first"] == "capped")

    def record(trial: dict[str, Any]) -> None:
        trials.append(trial)
        log(
            f"  {trial['phase']} {trial['meeting']} w{trial['window']}: "
            f"first={trial['first']} second={trial['second']} "
            f"items={trial['items_admitted']} {trial['wall_s']}s "
            f"(capped first asks {capped_count()} of target {target})"
        )
        persist(trials)

    prone: list[tuple[str, int]] = []
    for sweep in range(1, max_sweeps + 1):
        for meeting, wins in windows.items():
            for index, window in enumerate(wins):
                if clock() - started >= budget_s:
                    return trials, "budget"
                record(
                    run_trial(meeting, index, window, f"sweep{sweep}", llm, recorder)
                )
        prone = sorted(
            {(t["meeting"], t["window"]) for t in trials if t["first"] == "capped"}
        )
        if prone:
            break
    if not prone:
        return trials, "no-cap-observed"
    replay_asks = 0
    while True:
        for meeting, index in prone:
            if capped_count() >= target:
                return trials, "target"
            if replay_asks >= max_replay:
                return trials, "max-replay"
            if clock() - started >= budget_s:
                return trials, "budget"
            replay_asks += 1
            record(
                run_trial(
                    meeting, index, windows[meeting][index], "replay", llm, recorder
                )
            )


def build_windows(
    transcripts: pathlib.Path, meetings: Sequence[str]
) -> dict[str, list[str]]:
    texts = harness.load_transcripts(transcripts, meetings)
    unshaped = harness.check_gate(texts)
    if unshaped:
        raise SystemExit(
            f"REFUSED: _is_meeting_shaped is FALSE for {', '.join(unshaped)}."
        )
    return {m: harness.pass_windows(t, m) for m, t in texts.items()}


def write_trials(
    path: pathlib.Path,
    trials: list[dict[str, Any]],
    *,
    model: str,
    stop_reason: str | None,
    stamp: dict[str, Any] | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model": model,
                "retry_after_cap": True,
                "max_generation_tokens": DEFAULT_MAX_GENERATION_TOKENS,
                "context_window": DEFAULT_CONTEXT_WINDOW,
                "stop_reason": stop_reason,
                "stamp": stamp,
                "verdict": verdict(trials),
                "trials": trials,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def render(trials: Sequence[dict[str, Any]]) -> str:
    v = verdict(trials)
    lines = [
        f"capped first asks: {v['capped_first_asks']} (of {len(trials)} first asks)",
        f"recovered: {v['recovered']} of {v['capped_first_asks']}  "
        f"rate {v['rate']}  Wilson 95% {v['wilson_95']}",
        f"mean re-ask wall: {v['mean_reask_wall_s']} s; "
        f"items per recovered re-ask: {v['mean_items_per_recovered_reask']}; "
        f"re-ask seconds per recovered item: {v['reask_seconds_per_recovered_item']}",
        f"registered rule: KEEP needs lower bound >= {LOWER_BOUND_BAR} and "
        f"<= {COST_PER_ITEM_BAR_S} s per recovered item",
        f"VERDICT: {v['verdict']}",
    ]
    return "\n".join(lines)


# --- command line ----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--transcripts", type=pathlib.Path)
    parser.add_argument("--meetings", nargs="*", default=list(DEFAULT_MEETINGS))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--target", type=int, default=TARGET_CAPPED)
    parser.add_argument("--max-replay", type=int, default=MAX_REPLAY_ASKS)
    parser.add_argument("--budget-s", type=float, default=BUDGET_S)
    parser.add_argument("--results-dir", type=pathlib.Path, default=harness.RESULTS_DIR)
    parser.add_argument("--verdict", type=pathlib.Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.verdict is not None:
        print(render(json.loads(args.verdict.read_text())["trials"]))
        return 0
    if args.transcripts is None:
        parser.error("--transcripts is required to run")
    windows = build_windows(args.transcripts, args.meetings)
    print(
        f"model {args.model}, {sum(len(w) for w in windows.values())} window(s) in "
        f"{len(windows)} meeting(s), target {args.target} capped first asks"
    )
    recorder = harness.DiagnosticsRecorder()
    llm: LLMBackend = OllamaClient(
        model=args.model,
        max_generation_tokens=DEFAULT_MAX_GENERATION_TOKENS,
        context_window=DEFAULT_CONTEXT_WINDOW,
        diagnostics_sink=recorder.record,
    )
    timed = TimedBackend(llm)
    stamp = build_stamp(
        model=args.model,
        prompts={"extraction/decision_pass": decision_pass._SYSTEM_PROMPT},
    )
    now = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = args.results_dir / f"cap-reask-{now}-{args.model.replace(':', '-')}.json"

    def persist(trials: list[dict[str, Any]]) -> None:
        write_trials(path, trials, model=args.model, stop_reason=None, stamp=stamp)

    trials, reason = run_measurement(
        windows,
        timed,
        recorder,
        target=args.target,
        max_replay=args.max_replay,
        budget_s=args.budget_s,
        persist=persist,
    )
    write_trials(path, trials, model=args.model, stop_reason=reason, stamp=stamp)
    print(f"stopped: {reason}; stored {path}")
    print(render(trials))
    return 0


# --- self-test -------------------------------------------------------------


def _self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    from openkos.llm.base import BackendGenerationCapped

    good = json.dumps(
        [
            {
                "subject": "casing",
                "value": "Use rubber for the casing",
                "evidence": "rubber",
            }
        ]
    )
    window = "PM: we will go with rubber for the casing then."

    class Scripted:
        """Per-window script of outcomes; `Exception` instances are raised."""

        def __init__(self, script: dict[str, list[object]], recorder: Any) -> None:
            self.script = script
            self.recorder = recorder
            self.current = ""

        def chat(self, messages: Sequence[Any]) -> str:
            text = str(messages[-1]["content"])
            key = next((k for k in self.script if k in text), "")
            outcome = self.script[key].pop(0)
            from openkos.llm.ollama import ChatDiagnostics

            if isinstance(outcome, Exception):
                self.recorder.record(ChatDiagnostics("length", 8192, 0, 0))
                raise outcome
            self.recorder.record(ChatDiagnostics("stop", 30, 0, len(str(outcome))))
            return str(outcome)

    def trial_for(outcomes: list[object]) -> dict[str, Any]:
        rec = harness.DiagnosticsRecorder()
        llm = TimedBackend(Scripted({"rubber": outcomes}, rec))
        return run_trial("M", 0, window, "sweep1", llm, rec)

    cap = BackendGenerationCapped("cap")
    t_rec = trial_for([cap, good])
    check("capped then ok is recovered", t_rec["recovered"], True)
    check(
        "capped then ok: first/second",
        (t_rec["first"], t_rec["second"]),
        ("capped", "ok"),
    )
    check("both attempts logged", [a["attempt"] for a in t_rec["attempts"]], [1, 2])
    check("recovered re-ask admits its item", t_rec["items_admitted"], 1)
    t_twice = trial_for([BackendGenerationCapped("c"), BackendGenerationCapped("c")])
    check("capped twice is not recovered", t_twice["recovered"], False)
    check("capped twice: second", t_twice["second"], "capped")
    t_ok = trial_for([good])
    check(
        "an ok first ask has no re-ask", (t_ok["first"], t_ok["second"]), ("ok", None)
    )
    check("an ok first ask is not a cap event", t_ok["recovered"], False)
    t_junk = trial_for(["not json", good])
    check(
        "an unparseable first ask is not a cap event",
        (t_junk["first"], t_junk["second"], t_junk["recovered"]),
        ("unparseable", "ok", False),
    )
    t_unp = trial_for([cap, "prose, no json"])
    check("capped then unparseable is not recovered", t_unp["recovered"], False)
    check("capped then unparseable: second", t_unp["second"], "unparseable")

    # statistics
    check("wilson 8/15 lower bound rounds to 0.30", round(wilson(8, 15)[0], 2), 0.30)
    check("wilson 0/0 is vacuous", wilson(0, 0), (0.0, 1.0))
    check("wilson 0/15 lower is 0", wilson(0, 15)[0], 0.0)
    check("wilson 15/15 upper is 1", wilson(15, 15)[1], 1.0)

    # the rule, on synthetic trials
    def synth(n: int, k: int, reask_s: float, items: int) -> list[dict[str, Any]]:
        out = []
        for i in range(n):
            ok = i < k
            out.append(
                {
                    "first": "capped",
                    "recovered": ok,
                    "items_admitted": items if ok else 0,
                    "attempts": [
                        {"attempt": 1, "latency_s": 250.0},
                        {"attempt": 2, "latency_s": reask_s},
                    ],
                }
            )
        return out

    check(
        "12 of 15 cheap recoveries keep",
        verdict(synth(15, 12, 40.0, 3))["verdict"],
        "KEEP",
    )
    check(
        "8 of 15: lower bound 0.30 keeps",
        verdict(synth(15, 8, 40.0, 3))["verdict"],
        "KEEP",
    )
    check(
        "7 of 15 is inconclusive",
        verdict(synth(15, 7, 40.0, 3))["verdict"],
        "INCONCLUSIVE",
    )
    check("0 of 15 skips", verdict(synth(15, 0, 250.0, 3))["verdict"], "SKIP")
    check(
        "1 of 15 skips (upper bound under the bar)",
        verdict(synth(15, 1, 250.0, 3))["verdict"],
        "SKIP",
    )
    check(
        "a recovery too slow per item skips",
        verdict(synth(15, 12, 250.0, 0))["verdict"],
        "SKIP",
    )
    check("no capped first ask is inconclusive", verdict([])["verdict"], "INCONCLUSIVE")

    # sweep + replay control flow, on a scripted clock-free backend
    def measure(
        script: dict[str, list[object]], **kw: Any
    ) -> tuple[list[dict[str, Any]], str]:
        rec = harness.DiagnosticsRecorder()
        llm = TimedBackend(Scripted(script, rec))
        return run_measurement(
            {"M": ["window ALPHA", "window BETA"]},
            llm,
            rec,
            log=lambda _line: None,
            **kw,
        )

    trials, reason = measure(
        {
            "ALPHA": [cap, good, cap, good, cap, cap],
            "BETA": [good],
        },
        target=3,
    )
    check("stops at the target", reason, "target")
    check(
        "sweep asks every window once, replay only the cap-prone one",
        [(t["phase"], t["window"]) for t in trials],
        [("sweep1", 0), ("sweep1", 1), ("replay", 0), ("replay", 0)],
    )
    check("3 capped first asks counted", sum(t["first"] == "capped" for t in trials), 3)
    trials, reason = measure({"ALPHA": [good, good], "BETA": [good, good]})
    check("no cap anywhere stops after the sweeps", reason, "no-cap-observed")
    check("two sweeps were tried", len(trials), 4)
    trials, reason = measure(
        {"ALPHA": [cap, good] * 5, "BETA": [good]}, target=99, max_replay=2
    )
    check("replay is bounded", reason, "max-replay")
    check(
        "exactly max-replay replay asks", sum(t["phase"] == "replay" for t in trials), 2
    )
    ticks = iter(range(0, 1000, 10))
    rec = harness.DiagnosticsRecorder()
    llm = TimedBackend(Scripted({"ALPHA": [cap, good] * 5, "BETA": [good]}, rec))
    trials, reason = run_measurement(
        {"M": ["window ALPHA", "window BETA"]},
        llm,
        rec,
        budget_s=15.0,
        log=lambda _line: None,
        clock=lambda: float(next(ticks)),
    )
    check("a spent budget stops the run", reason, "budget")

    if failures:
        print("self-test: FAILED")
        for line in failures:
            print(f"  {line}")
        return 1
    print("self-test: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
