"""The #1269 bake-off driver: eligibility gates, then the staged knockout.

Executes the plan pre-registered on #1269 ("Pre-registration: numeric bars,
latency budget, run plan"). It CALLS the existing harness CLIs; it does not
reimplement any of them. The bars, candidates, harness order and forecasts are
data in `bakeoff_spec.py`; the bar logic is `bakeoff_bars.py`; the numbers each
bar reads are `bakeoff_metrics.py`; the local Ollama gates are
`bakeoff_ollama.py`.

THE PLAN
1. Eligibility, per model, no quality run: license allowlist, native context
   >= 12288, and the `ollama ps` memory peak at num_ctx 12288 and 32768 with
   `bge-m3` loaded. A model that is not pulled is reported as pending its pull
   -- this driver never downloads anything.
2. The baseline (`qwen3:8b`) runs every harness at n=15 (twice where the
   pre-registration says no 15-run baseline exists on the current fixture,
   in two SEPARATE invocations: a baseline repeat never runs in the same
   invocation as repeat 1).
3. Per candidate and family -- Label, Judge, Generate -- the harnesses run
   cheapest-first and the candidate DROPS at its first failed gate. A candidate
   whose adjudication queue is unworked is BLOCKED, not dropped.
4. Write runs last, for the top 2 Generate survivors only.
5. No winner for a family means the baseline stays: recorded, never retried
   with tuned bars.

RESUMABLE. Every cell is a directory under `results/cells/` holding its
harness artifacts, its log and a `cell.json`; a `done` cell is skipped, a
`failed` one is retried. Eligibility likewise. `--evaluate-only` recomputes
every verdict and the report from what is on disk with no model call, which is
how an adjudication done by hand reaches the numbers.

Usage:

    uv run python evals/bakeoff/run_bakeoff.py --plan
    uv run python -u evals/bakeoff/run_bakeoff.py --run
    uv run python evals/bakeoff/run_bakeoff.py --evaluate-only
    uv run python evals/bakeoff/run_bakeoff.py --self-test

All output goes under `evals/bakeoff/results/`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
EVALS_ROOT = HERE.parent
REPO_ROOT = EVALS_ROOT.parent
sys.path.append(str(HERE))
sys.path.append(str(EVALS_ROOT))

import bakeoff_bars as bars_mod  # noqa: E402
import bakeoff_metrics as metrics_mod  # noqa: E402
import bakeoff_ollama as ollama_mod  # noqa: E402
import bakeoff_spec as spec_mod  # noqa: E402
import harness_stamp  # noqa: E402
from bakeoff_spec import Candidate, HarnessSpec  # noqa: E402

DEFAULT_RESULTS = HERE / "results"

Metrics = dict[str, float | None]
Runner = Callable[[list[str], pathlib.Path], tuple[int, str]]
MetricsLoader = Callable[[str, pathlib.Path], Metrics]


# --------------------------------------------------------------------------
# Layout and resume.
# --------------------------------------------------------------------------


def slug(tag: str) -> str:
    return tag.replace(":", "-").replace("/", "-")


def cell_dir(
    results: pathlib.Path, harness: str, model: str, repeat: int
) -> pathlib.Path:
    return results / "cells" / harness / slug(model) / f"r{repeat}"


def read_cell(directory: pathlib.Path) -> dict[str, Any] | None:
    path = directory / "cell.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None  # a torn write is "not done": the cell simply re-runs
    return data if isinstance(data, dict) else None


def decide_run(
    meta: Mapping[str, Any] | None,
    *,
    repeat: int,
    first_repeat_meta: Mapping[str, Any] | None,
    session: str,
) -> tuple[str, str]:
    """`(action, why)` for one cell: `skip` (done), `defer` (a second baseline
    session must be a later invocation) or `run`."""
    if meta is not None and meta.get("status") == "done":
        return "skip", "done"
    if (
        repeat > 1
        and first_repeat_meta is not None
        and first_repeat_meta.get("status") == "done"
        and first_repeat_meta.get("session") == session
    ):
        return (
            "defer",
            "baseline repeat 1 ran in this session; run in a later invocation",
        )
    if meta is not None and meta.get("status") == "failed":
        return "run", "retry after failure"
    return "run", "not run yet"


# --------------------------------------------------------------------------
# Plan.
# --------------------------------------------------------------------------


@dataclass
class PlanItem:
    kind: str  # eligibility | cell
    stage: str
    model: str
    harness: str = ""
    repeat: int = 1
    minutes: float = 0.0
    state: str = "todo"  # todo | done | deferred | conditional
    note: str = ""


def _models(only: Sequence[str] | None) -> list[Candidate]:
    pool = [spec_mod.BASELINE_CANDIDATE, *spec_mod.CANDIDATES]
    return [c for c in pool if not only or c.tag in only]


def build_plan(
    results: pathlib.Path,
    *,
    runs: int,
    session: str,
    only_models: Sequence[str] | None = None,
    only_families: Sequence[str] | None = None,
) -> list[PlanItem]:
    """Every step the bake-off would take, in execution order, with what the
    results directory already holds marked `done`. Candidate and Write cells
    past the first stage are `conditional`: they run only if the candidate
    clears the stage before them (the knockout), so their cost is an upper
    bound."""
    items: list[PlanItem] = []
    wanted = _models(only_models)
    families = [
        f for f in spec_mod.FAMILY_ORDER if not only_families or f in only_families
    ]

    for candidate in wanted:
        done = _eligibility_state(results, candidate.tag)
        items.append(
            PlanItem(
                "eligibility",
                "0-eligibility",
                candidate.tag,
                minutes=2.0 + (candidate.approx_gb / 5.0),
                state="done" if done in ("eligible", "ineligible") else "todo",
                note=f"license, native context, memory at {spec_mod.MEMORY_PROBE_CONTEXTS}"
                + (f" [{done}]" if done else ""),
            )
        )

    baseline = spec_mod.BASELINE_CANDIDATE
    for family in families:
        for spec in spec_mod.stage_order(family):
            for repeat in range(1, spec.baseline_repeats + 1):
                meta = read_cell(cell_dir(results, spec.name, baseline.tag, repeat))
                first = read_cell(cell_dir(results, spec.name, baseline.tag, 1))
                action, why = decide_run(
                    meta, repeat=repeat, first_repeat_meta=first, session=session
                )
                # Repeat 1 runs in THIS invocation unless it is already done, so
                # a repeat 2 can only run now if repeat 1 finished in an earlier one.
                if (
                    action == "run"
                    and repeat > 1
                    and not (
                        first is not None
                        and first.get("status") == "done"
                        and first.get("session") != session
                    )
                ):
                    action, why = "defer", "second baseline session: a later invocation"
                items.append(
                    PlanItem(
                        "cell",
                        f"{spec_mod.FAMILY_ORDER.index(family) + 1}-{family}-baseline",
                        baseline.tag,
                        spec.name,
                        repeat,
                        spec_mod.forecast_minutes(spec, baseline, runs),
                        {"skip": "done", "defer": "deferred", "run": "todo"}[action],
                        why if action != "run" else "",
                    )
                )

    for family in families:
        if family == "write":
            continue
        for candidate in spec_mod.candidates_for(family):
            if only_models and candidate.tag not in only_models:
                continue
            eligible = _eligibility_state(results, candidate.tag) == "eligible"
            for index, spec in enumerate(spec_mod.stage_order(family)):
                meta = read_cell(cell_dir(results, spec.name, candidate.tag, 1))
                action, _ = decide_run(
                    meta, repeat=1, first_repeat_meta=None, session=session
                )
                items.append(
                    PlanItem(
                        "cell",
                        f"{spec_mod.FAMILY_ORDER.index(family) + 1}-{family}",
                        candidate.tag,
                        spec.name,
                        1,
                        spec_mod.forecast_minutes(spec, candidate, runs),
                        "done"
                        if action == "skip"
                        else "todo"
                        if index == 0 and eligible
                        else "conditional",
                        (
                            "only if the previous stage clears it"
                            if index
                            else ""
                            if eligible
                            else "only if eligibility passes"
                        ),
                    )
                )

    if "write" in families:
        write_specs = (
            *spec_mod.stage_order("write"),
            spec_mod.harness("query_sufficiency"),
        )
        generate = spec_mod.candidates_for("generate")
        for spec in write_specs:
            items.append(
                PlanItem(
                    "cell",
                    "4-write",
                    f"<top {spec_mod.WRITE_TOP_N} Generate survivors>",
                    spec.name,
                    1,
                    # Upper bound: the slowest two Generate candidates.
                    sum(
                        sorted(
                            (
                                spec_mod.forecast_minutes(spec, c, runs)
                                for c in generate
                            ),
                            reverse=True,
                        )[: spec_mod.WRITE_TOP_N]
                    ),
                    "conditional",
                    "chosen after Generate; sufficiency reuses a Judge cell when one exists",
                )
            )
    return items


def render_plan(items: Sequence[PlanItem], *, runs: int, session: str) -> str:
    lines = [
        "# Bake-off plan (#1269)",
        "",
        f"session `{session}` · {runs} runs per arm"
        + ("" if runs == spec_mod.RUNS else f" (NOT the registered n={spec_mod.RUNS})"),
        "",
        "```",
        f"{'stage':<22} {'model':<24} {'harness':<20} {'rep':>3} {'min':>7}  state",
    ]
    for item in items:
        lines.append(
            f"{item.stage:<22} {item.model:<24} {item.harness or '-':<20} "
            f"{item.repeat:>3} {item.minutes:>7.1f}  {item.state}"
            + (f"  ({item.note})" if item.note else "")
        )
    lines.append("```")
    todo = sum(i.minutes for i in items if i.state == "todo")
    conditional = sum(i.minutes for i in items if i.state == "conditional")
    deferred = sum(i.minutes for i in items if i.state == "deferred")
    done = sum(1 for i in items if i.state == "done")
    lines += [
        "",
        "## Time forecast",
        "",
        "Minutes are the pre-registration's 15-run estimates on `qwen3:8b`, scaled "
        "by the pre-registered speed multipliers (measured: `phi4:14b` 2.2x; the "
        "rest assumed). Model swap time is recorded per cell, not forecast; the "
        "eligibility minutes are a rough load-time allowance, not a pre-registered "
        "figure. A candidate's cells are unconditional only once its eligibility is "
        "recorded as `eligible`.",
        "",
        f"- runs now (unconditional): **{todo / 60:.1f} h** ({todo:.0f} min)",
        f"- conditional on surviving an earlier stage: up to **{conditional / 60:.1f} h**",
        f"- deferred to a later invocation (second baseline session): {deferred / 60:.1f} h",
        f"- already done and skipped: {done} item(s)",
        f"- upper bound with no knockout: **{(todo + conditional + deferred) / 60:.1f} h**",
        "",
        "## Not measured this round",
        "",
        *(f"- {item}" for item in spec_mod.NOT_MEASURED_THIS_ROUND),
        "- Qwen3.6 runs thinking OFF only, as production does (clarification 2).",
        "",
        "The pre-registration forecasts about 65 h for the full serial battery and "
        "35-40 h for the staged plan; the upper bound above is the serial figure "
        "for THESE candidates, and the knockout is what brings it down.",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Eligibility.
# --------------------------------------------------------------------------


def _eligibility_path(results: pathlib.Path, tag: str) -> pathlib.Path:
    return results / "eligibility" / f"{slug(tag)}.json"


def _eligibility_state(results: pathlib.Path, tag: str) -> str | None:
    path = _eligibility_path(results, tag)
    if not path.is_file():
        return None
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("state"))
    except (OSError, ValueError):
        return None


def ensure_eligibility(
    client: ollama_mod.OllamaLocal,
    candidate: Candidate,
    results: pathlib.Path,
    *,
    accepted_licenses: Mapping[str, str],
    baseline_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure (or reuse) one model's eligibility. `eligible` and `ineligible`
    are final and reused; `pending` is recomputed."""
    path = _eligibility_path(results, candidate.tag)
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("state") in ("eligible", "ineligible"):
            return dict(stored)

    record: dict[str, Any] = {
        "model": candidate.tag,
        "measured_at": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "issue_stated_gb": candidate.approx_gb,
    }
    digest = ollama_mod.listed_digest(client.tags(), candidate.tag)
    record["pulled"] = digest is not None
    record["digest"] = digest
    license_id: str | None = None
    ctx: int | None = None
    memory: dict[str, Any] = {}
    if digest is not None:
        show = client.show(candidate.tag)
        license_id = ollama_mod.classify_license(show.get("license"))
        if candidate.tag in accepted_licenses:
            license_id = accepted_licenses[candidate.tag]
            record["license_attested_by_operator"] = True
        ctx = ollama_mod.native_context(show)
        record["license"] = license_id
        record["native_context"] = ctx
        # Memory is only worth a load for a model the cheap gates left standing.
        pre = ollama_mod.decide_eligibility(
            pulled=True,
            license_id=license_id,
            native_ctx=ctx,
            memory_bytes_at_production=None,
        )
        if pre["state"] != "ineligible":
            for num_ctx in spec_mod.MEMORY_PROBE_CONTEXTS:
                memory[str(num_ctx)] = client.measure_memory(candidate.tag, num_ctx)
    record["memory"] = memory
    at_production = memory.get(str(spec_mod.PRODUCTION_NUM_CTX), {}).get("total_bytes")
    verdict = ollama_mod.decide_eligibility(
        pulled=digest is not None,
        license_id=license_id,
        native_ctx=ctx,
        memory_bytes_at_production=at_production,
    )
    record.update(verdict)
    if baseline_record and memory:
        embedder = _embedder_bytes(memory)
        base_mem = baseline_record.get("memory", {})
        record["pair_with_baseline_resident"] = {
            num_ctx: (
                base_mem[num_ctx]["total_bytes"]
                + memory[num_ctx]["total_bytes"]
                - embedder
                <= spec_mod.BUDGET_GB * ollama_mod.GB
            )
            for num_ctx in memory
            if num_ctx in base_mem
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def _embedder_bytes(memory: Mapping[str, Any]) -> int:
    for entry in memory.values():
        for name, usage in entry.get("per_model", {}).items():
            if name.startswith(spec_mod.EMBEDDING_MODEL):
                return int(usage["size"])
    return 0


# --------------------------------------------------------------------------
# Running one cell.
# --------------------------------------------------------------------------


def default_runner(argv: list[str], log: pathlib.Path) -> tuple[int, str]:
    """Run a harness CLI, streaming its output to the console and to `log`."""
    tail: list[str] = []
    with (
        subprocess.Popen(  # noqa: S603 -- argv is built from this repo's own harness paths
            argv,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        ) as process,
        log.open("w", encoding="utf-8") as handle,
    ):
        for line in process.stdout or ():
            handle.write(line)
            print(line, end="", flush=True)
            tail.append(line)
            del tail[:-40]
        code = process.wait()
    return code, "".join(tail)


def _format(
    argv: Sequence[str], *, model: str, runs: int, label: str, out: pathlib.Path
) -> list[str]:
    return [
        part.replace("{model}", model)
        .replace("{runs}", str(runs))
        .replace("{label}", label)
        .replace("{out}", str(out))
        for part in argv
    ]


def _listing(directory: pathlib.Path) -> set[str]:
    return (
        {p.name for p in directory.glob("*") if p.is_file()}
        if directory.is_dir()
        else set()
    )


def execute_cell(
    spec: HarnessSpec,
    model: str,
    repeat: int,
    *,
    results: pathlib.Path,
    runs: int,
    session: str,
    runner: Runner,
    evals_root: pathlib.Path = EVALS_ROOT,
    swap_s: float | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run every invocation of one cell, gather its artifacts under the cell
    directory, and write `cell.json`. Never raises on a harness failure: a
    failed cell is recorded `failed` and re-runs on the next invocation."""
    directory = cell_dir(results, spec.name, model, repeat)
    directory.mkdir(parents=True, exist_ok=True)
    harness_results = evals_root / pathlib.Path(spec.script).parent / "results"
    label = spec_mod.cell_label(model, repeat)
    started = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    invocations: list[dict[str, Any]] = []
    ok = True

    def one(arm: str, argv: list[str]) -> bool:
        before = _listing(harness_results)
        wall_start = time.monotonic()
        code, _tail = runner(argv, directory / f"{arm}.log")
        wall = time.monotonic() - wall_start
        moved: list[str] = []
        if spec.writes_to_results_dir:
            for name in sorted(_listing(harness_results) - before):
                shutil.move(str(harness_results / name), str(directory / name))
                moved.append(name)
        invocations.append(
            {
                "arm": arm,
                "argv": argv[3:],
                "exit_code": code,
                "wall_s": round(wall, 1),
                "files": moved,
            }
        )
        log(
            f"    {spec.name} [{arm}] exit {code} in {wall:.0f}s, {len(moved)} file(s) moved"
        )
        return code == 0

    script = str(evals_root / spec.script)
    for inv in spec.invocations:
        argv = [
            sys.executable,
            "-u",
            script,
            *_format(inv.argv, model=model, runs=runs, label=label, out=directory),
        ]
        ok = one(inv.arm, argv) and ok
        if not ok:
            break
    if ok and spec.decide_after:
        cal = sorted(directory.glob("runs-calibration-*.json"))
        conf = sorted(directory.glob("runs-confirmation-*.json"))
        if cal and conf:
            argv = [
                sys.executable,
                "-u",
                script,
                "--decide",
                str(cal[-1]),
                str(conf[-1]),
            ]
            ok = one("decide", argv)
        else:
            ok = False
            invocations.append(
                {
                    "arm": "decide",
                    "exit_code": None,
                    "error": "an arm wrote no runs file",
                }
            )

    stamp = _find_stamp(directory)
    meta: dict[str, Any] = {
        "harness": spec.name,
        "model": model,
        "repeat": repeat,
        "runs": runs,
        "session": session,
        "started": started,
        "finished": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        "status": "done" if ok else "failed",
        "invocations": invocations,
        "swap_s": swap_s,
        "stamp": stamp,
        "stamp_problems": harness_stamp.stamp_problems(stamp)
        if stamp
        else ["no stamp found in the run's JSON"],
    }
    (directory / "cell.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _find_stamp(directory: pathlib.Path) -> dict[str, Any] | None:
    for path in sorted(directory.rglob("runs-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("stamp"), dict):
            return dict(data["stamp"])
    return None


# --------------------------------------------------------------------------
# Evaluation.
# --------------------------------------------------------------------------


@dataclass
class Evaluation:
    baseline: dict[str, Metrics] = field(default_factory=dict)
    spread: dict[str, dict[str, float]] = field(default_factory=dict)
    verdicts: dict[tuple[str, str], bars_mod.HarnessVerdict] = field(
        default_factory=dict
    )
    knockouts: dict[tuple[str, str], bars_mod.KnockoutOutcome] = field(
        default_factory=dict
    )
    write_choice: list[str] = field(default_factory=list)


def cell_metrics(
    results: pathlib.Path,
    spec: HarnessSpec,
    model: str,
    repeat: int,
    loader: MetricsLoader,
) -> Metrics | None:
    directory = cell_dir(results, spec.name, model, repeat)
    meta = read_cell(directory)
    if meta is None or meta.get("status") != "done":
        return None
    return loader(spec.name, directory)


def verdict_for_cell(
    spec: HarnessSpec, baseline: Metrics, candidate: Metrics
) -> bars_mod.HarnessVerdict:
    derive = metrics_mod.DERIVERS.get(spec.name)
    merged: Metrics = dict(candidate)
    if derive is not None:
        merged.update(derive(baseline, candidate))
    return bars_mod.evaluate_harness(spec.bars, baseline, merged)


def baseline_spread(first: Metrics, second: Metrics) -> dict[str, float]:
    """`|repeat 1 - repeat 2|` per shared numeric metric: the baseline's own
    session-to-session spread, reported beside every bar."""
    return {
        name: abs(first[name] - second[name])  # type: ignore[operator]
        for name in sorted(first)
        if first.get(name) is not None and second.get(name) is not None
    }


def rank_generate(
    survivors: Sequence[str], recalls: Mapping[str, Metrics], baseline: Metrics
) -> list[str]:
    """Order Generate survivors by mean per-fixture pre-cap recall gain over the
    baseline, best first (ties by tag). The pre-registration says "the top 2
    Generate candidates" without a ranking; this is the Generate row's primary
    metric (see the README's interpretation choices)."""

    def gain(tag: str) -> float:
        metrics = recalls.get(tag, {})
        deltas = [
            value - baseline[name]  # type: ignore[operator]
            for name, value in metrics.items()
            if name.startswith("recall_precap:")
            and value is not None
            and baseline.get(name) is not None
        ]
        return statistics.fmean(deltas) if deltas else float("-inf")

    return sorted(survivors, key=lambda tag: (-gain(tag), tag))


def evaluate_all(
    results: pathlib.Path,
    *,
    only_models: Sequence[str] | None = None,
    loader: MetricsLoader = metrics_mod.metrics_for,
) -> Evaluation:
    """Recompute every verdict from what is on disk. No model call."""
    ev = Evaluation()
    base_tag = spec_mod.BASELINE
    for spec in spec_mod.HARNESSES:
        first = cell_metrics(results, spec, base_tag, 1, loader)
        if first is None:
            continue
        ev.baseline[spec.name] = first
        second = cell_metrics(results, spec, base_tag, 2, loader)
        if second is not None:
            ev.spread[spec.name] = baseline_spread(first, second)

    for family in ("label", "judge", "generate"):
        order = spec_mod.stage_order(family)
        for candidate in spec_mod.candidates_for(family):
            if only_models and candidate.tag not in only_models:
                continue
            seen: dict[str, bars_mod.HarnessVerdict | None] = {}
            for spec in order:
                base = ev.baseline.get(spec.name)
                cand = cell_metrics(results, spec, candidate.tag, 1, loader)
                if base is None or cand is None:
                    seen[spec.name] = None
                    break
                verdict = verdict_for_cell(spec, base, cand)
                ev.verdicts[(spec.name, candidate.tag)] = verdict
                seen[spec.name] = verdict
                if verdict.dropped or verdict.blocked:
                    break
            ev.knockouts[(family, candidate.tag)] = bars_mod.knockout(
                [s.name for s in order], seen
            )

    survivors = [
        tag
        for (family, tag), outcome in ev.knockouts.items()
        if family == "generate" and outcome.status == "survived"
    ]
    cap_spec = spec_mod.harness("extraction_cap")
    recalls = {
        tag: cell_metrics(results, cap_spec, tag, 1, loader) or {} for tag in survivors
    }
    base_cap = ev.baseline.get("extraction_cap", {})
    ev.write_choice = rank_generate(survivors, recalls, base_cap)[
        : spec_mod.WRITE_TOP_N
    ]

    attribution = spec_mod.harness("query_attribution")
    sufficiency = spec_mod.harness("query_sufficiency")
    for tag in ev.write_choice:
        for spec in (attribution, sufficiency):
            base = ev.baseline.get(spec.name)
            cand = cell_metrics(results, spec, tag, 1, loader)
            if base is not None and cand is not None:
                ev.verdicts[(spec.name, tag)] = verdict_for_cell(spec, base, cand)
    return ev


def role_status(ev: Evaluation, family: str, role: str, tag: str) -> tuple[str, str]:
    """`(status, note)` for one role x model row of the report."""
    specs = [s for s in spec_mod.stage_order(family) if s.role == role]
    if family == "write":
        specs = [
            spec_mod.harness("query_attribution"),
            spec_mod.harness("query_sufficiency"),
        ]
    outcome = ev.knockouts.get((family, tag))
    verdicts = [ev.verdicts.get((s.name, tag)) for s in specs]
    if (
        outcome is not None
        and outcome.status == "dropped"
        and not any(v is not None and v.dropped for v in verdicts)
    ):
        return "SKIPPED", f"dropped earlier at {outcome.stopped_at}: {outcome.reason}"
    if any(v is None for v in verdicts):
        return "not run", ""
    present = [v for v in verdicts if v is not None]
    dropped = next((v for v in present if v.dropped), None)
    if dropped is not None:
        return "DROPPED", dropped.reason
    blocked = next((v for v in present if v.blocked), None)
    if blocked is not None:
        return "BLOCKED", blocked.reason
    unmeasured = sorted({b for v in present for b in v.unmeasured})
    wins = [v.wins for v in present]
    budgets = [v.budget_ok for v in present]
    if unmeasured:
        return "INCOMPLETE", "not measured: " + ", ".join(unmeasured)
    if False in budgets:
        return "OVER BUDGET", "a latency budget failed"
    if "won" in wins:
        return "MEETS BARS", "adoption candidate; the human adopts"
    if all(w == "none" for w in wins):
        return "VETOES HOLD", "veto-only role: no win of its own"
    return "NO WIN", "no primary metric won"


def render_report(
    results: pathlib.Path, ev: Evaluation, *, runs: int, session: str
) -> str:
    lines = [
        "# Model bake-off report (#1269)",
        "",
        f"_Generated {datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')} · session `{session}` · "
        f"{runs} runs per arm · bars: pre-registration comment on #1269._",
        "",
        "## Eligibility",
        "",
        "| model | pulled | digest | license | native ctx | GB @ 12288 | GB @ 32768 | "
        "pair with baseline (12288 / 32768) | state | reasons |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for candidate in [spec_mod.BASELINE_CANDIDATE, *spec_mod.CANDIDATES]:
        path = _eligibility_path(results, candidate.tag)
        if not path.is_file():
            lines.append(f"| `{candidate.tag}` | ? | | | | | | | not checked | |")
            continue
        rec = json.loads(path.read_text(encoding="utf-8"))
        mem = rec.get("memory", {})

        def gb(ctx: int, mem: Mapping[str, Any] = mem) -> str:
            entry = mem.get(str(ctx))
            return f"{entry['total_bytes'] / ollama_mod.GB:.1f}" if entry else "-"

        pair = rec.get("pair_with_baseline_resident", {})
        pair_text = " / ".join(
            "fits" if pair.get(str(c)) else "no" if str(c) in pair else "-"
            for c in spec_mod.MEMORY_PROBE_CONTEXTS
        )
        lines.append(
            f"| `{candidate.tag}` | {'yes' if rec.get('pulled') else 'no'} | "
            f"`{str(rec.get('digest') or '-')[:12]}` | {rec.get('license', '-')} | "
            f"{rec.get('native_context', '-')} | {gb(12288)} | {gb(32768)} | {pair_text} | "
            f"{rec.get('state')} | {'; '.join(rec.get('reasons', []))} |"
        )

    lines += [
        "",
        "## Not measured this round",
        "",
        *(f"- {item}" for item in spec_mod.NOT_MEASURED_THIS_ROUND),
        "",
        "## Role x model",
        "",
        "One row per role and model. `MEETS BARS` means the pre-registered bars "
        "hold; it is an adoption candidate, not an adoption. A family with no "
        "`MEETS BARS` keeps the baseline (recorded, not retried with tuned bars).",
        "",
        "| family | role | model | status | note |",
        "| --- | --- | --- | --- | --- |",
    ]
    for family in ("label", "judge", "generate"):
        roles = sorted({s.role for s in spec_mod.stage_order(family)})
        for tag in (c.tag for c in spec_mod.candidates_for(family)):
            for role in roles:
                status, note = role_status(ev, family, role, tag)
                lines.append(f"| {family} | {role} | `{tag}` | {status} | {note} |")
    lines.append(
        f"| write | answer | `{spec_mod.BASELINE}` | baseline | the attribution and sufficiency baseline |"
    )
    if ev.write_choice:
        for tag in ev.write_choice:
            status, note = role_status(ev, "write", "answer", tag)
            lines.append(f"| write | answer | `{tag}` | {status} | {note} |")
    else:
        lines.append("| write | answer | - | not run | no Generate survivor yet |")

    lines += ["", "## Baseline spread (|repeat 1 - repeat 2|)", ""]
    if ev.spread:
        for name, spread in sorted(ev.spread.items()):
            shown = ", ".join(
                f"{m} {v:.3g}"
                for m, v in spread.items()
                if not m.startswith(("case_wrong", "recall_precap", "latency_median_s"))
            )
            lines.append(f"- `{name}`: {shown}")
    else:
        lines.append("No harness has both baseline repeats yet.")

    lines += ["", "## Bars, per cell", ""]
    for (harness_name, tag), verdict in sorted(ev.verdicts.items()):
        lines += [f"### `{harness_name}` x `{tag}`", ""]
        for r in verdict.results:
            lines.append(f"- {r.bar_id} [{r.role}] **{r.status}**: {r.detail}")
        lines.append("")

    lines += [
        "## Identity stamps and swap time",
        "",
        "| harness | model | repeat | commit | digest | swap s | problems |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    cells_root = results / "cells"
    for meta_path in (
        sorted(cells_root.rglob("cell.json")) if cells_root.is_dir() else []
    ):
        meta = read_cell(meta_path.parent)
        if meta is None:  # a torn write: that cell simply re-runs
            continue
        stamp = meta.get("stamp") or {}
        harness_info = stamp.get("harness") or {}
        model_info = stamp.get("model") or {}
        lines.append(
            f"| {meta['harness']} | `{meta['model']}` | {meta['repeat']} | "
            f"`{str(harness_info.get('commit') or '-')[:10]}`"
            f"{' (dirty)' if harness_info.get('dirty') else ''} | "
            f"`{str(model_info.get('digest') or '-')[:12]}` | {meta.get('swap_s') or '-'} | "
            f"{'; '.join(meta.get('stamp_problems', [])) or '-'} |"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# The run loop.
# --------------------------------------------------------------------------


@dataclass
class RunContext:
    results: pathlib.Path
    runs: int
    session: str
    runner: Runner
    client: ollama_mod.OllamaLocal | None
    accepted_licenses: Mapping[str, str]
    only_models: Sequence[str] | None = None
    only_families: Sequence[str] | None = None
    loader: MetricsLoader = metrics_mod.metrics_for
    log: Callable[[str], None] = print
    last_model: str | None = None

    def swap(self, model: str) -> float | None:
        """Load `model` if it is not the resident one; returns the load seconds
        (recorded per cell), or `None` when no switch was needed."""
        if self.client is None or model == self.last_model:
            self.last_model = model
            return None
        self.client.unload_all()
        self.client.load_embedder()
        seconds = self.client.load_chat(model, spec_mod.PRODUCTION_NUM_CTX)
        self.last_model = model
        self.log(f"  swapped to {model} in {seconds:.1f}s")
        return round(seconds, 2)


def _run_cell_if_needed(
    ctx: RunContext, spec: HarnessSpec, model: str, repeat: int
) -> str:
    """Execute one cell unless it is done or deferred; returns what happened."""
    meta = read_cell(cell_dir(ctx.results, spec.name, model, repeat))
    first = (
        read_cell(cell_dir(ctx.results, spec.name, model, 1)) if repeat > 1 else None
    )
    action, why = decide_run(
        meta, repeat=repeat, first_repeat_meta=first, session=ctx.session
    )
    if action != "run":
        ctx.log(f"  {spec.name} x {model} r{repeat}: {action} ({why})")
        return action
    ctx.log(f"  {spec.name} x {model} r{repeat}: running ({why})")
    swap_s = ctx.swap(model)
    result = execute_cell(
        spec,
        model,
        repeat,
        results=ctx.results,
        runs=ctx.runs,
        session=ctx.session,
        runner=ctx.runner,
        swap_s=swap_s,
        log=ctx.log,
    )
    return str(result["status"])


def run_bakeoff(ctx: RunContext) -> Evaluation:
    """Eligibility, baseline, then the staged knockout, then Write."""
    families = [
        f
        for f in spec_mod.FAMILY_ORDER
        if not ctx.only_families or f in ctx.only_families
    ]
    pool = [c for c in _models(ctx.only_models)]
    baseline = spec_mod.BASELINE_CANDIDATE

    ctx.log("== eligibility")
    records: dict[str, dict[str, Any]] = {}
    if ctx.client is None:
        ctx.log("  no local Ollama reachable: eligibility cannot be measured; stopping")
        return evaluate_all(ctx.results, only_models=ctx.only_models, loader=ctx.loader)
    for candidate in sorted(pool, key=lambda c: c.tag != baseline.tag):
        records[candidate.tag] = ensure_eligibility(
            ctx.client,
            candidate,
            ctx.results,
            accepted_licenses=ctx.accepted_licenses,
            baseline_record=records.get(baseline.tag)
            if candidate.tag != baseline.tag
            else None,
        )
        ctx.log(
            f"  {candidate.tag}: {records[candidate.tag]['state']} {records[candidate.tag].get('reasons', '')}"
        )

    ctx.log("== baseline")
    for family in families:
        for spec in spec_mod.stage_order(family):
            for repeat in range(1, spec.baseline_repeats + 1):
                _run_cell_if_needed(ctx, spec, baseline.tag, repeat)

    for family in (f for f in families if f != "write"):
        ctx.log(f"== {family}")
        for candidate in spec_mod.candidates_for(family):
            if ctx.only_models and candidate.tag not in ctx.only_models:
                continue
            if records.get(candidate.tag, {}).get("state") != "eligible":
                ctx.log(
                    f"  {candidate.tag}: not eligible ({records.get(candidate.tag, {}).get('state')}); skipped"
                )
                continue
            for spec in spec_mod.stage_order(family):
                status = _run_cell_if_needed(ctx, spec, candidate.tag, 1)
                if status == "failed":
                    ctx.log(
                        f"  {candidate.tag}: {spec.name} failed; stopping this candidate"
                    )
                    break
                ev = evaluate_all(
                    ctx.results, only_models=[candidate.tag], loader=ctx.loader
                )
                verdict = ev.verdicts.get((spec.name, candidate.tag))
                if verdict is not None and (verdict.dropped or verdict.blocked):
                    ctx.log(f"  {candidate.tag}: {verdict.reason}")
                    break

    if "write" in families:
        ctx.log("== write")
        ev = evaluate_all(ctx.results, only_models=ctx.only_models, loader=ctx.loader)
        for spec in (
            spec_mod.harness("query_attribution"),
            spec_mod.harness("query_sufficiency"),
        ):
            for repeat in range(1, spec.baseline_repeats + 1):
                _run_cell_if_needed(ctx, spec, baseline.tag, repeat)
        for tag in ev.write_choice:
            for spec in (
                spec_mod.harness("query_attribution"),
                spec_mod.harness("query_sufficiency"),
            ):
                _run_cell_if_needed(ctx, spec, tag, 1)
        if not ev.write_choice:
            ctx.log("  no Generate survivor: Write stays on the baseline")

    return evaluate_all(ctx.results, only_models=ctx.only_models, loader=ctx.loader)


# --------------------------------------------------------------------------
# Self-test: fake runner, fake harness tree, fake metrics. No model, no network.
# --------------------------------------------------------------------------


def _self_test() -> int:
    failures: list[str] = []
    checks = 0

    def check(label: str, got: object, want: object) -> None:
        nonlocal checks
        checks += 1
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # ---- resume logic ----
    done = {"status": "done", "session": "s1"}
    failed = {"status": "failed", "session": "s1"}
    check(
        "a done cell is skipped",
        decide_run(done, repeat=1, first_repeat_meta=None, session="s2")[0],
        "skip",
    )
    check(
        "a failed cell is retried",
        decide_run(failed, repeat=1, first_repeat_meta=None, session="s1")[0],
        "run",
    )
    check(
        "a new cell runs",
        decide_run(None, repeat=1, first_repeat_meta=None, session="s1")[0],
        "run",
    )
    check(
        "baseline repeat 2 is deferred within repeat 1's session",
        decide_run(None, repeat=2, first_repeat_meta=done, session="s1")[0],
        "defer",
    )
    check(
        "baseline repeat 2 runs in a later session",
        decide_run(None, repeat=2, first_repeat_meta=done, session="s2")[0],
        "run",
    )
    check(
        "baseline repeat 2 runs when repeat 1 failed",
        decide_run(None, repeat=2, first_repeat_meta=failed, session="s1")[0],
        "run",
    )
    check(
        "a done repeat 2 is skipped even in repeat 1's session",
        decide_run(done, repeat=2, first_repeat_meta=done, session="s1")[0],
        "skip",
    )

    # ---- running a cell against a fake harness tree ----
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        evals = root / "evals"
        results = root / "results"
        spec_obj = spec_mod.harness("edge_typing")
        (evals / "edge_typing" / "results").mkdir(parents=True)
        (evals / "edge_typing" / "results" / "preexisting.json").write_text("{}")
        (evals / "edge_typing" / "run_edge_typing_eval.py").write_text("")
        seen_argv: list[list[str]] = []
        outcome = {"code": 0}

        def fake_runner(argv: list[str], log: pathlib.Path) -> tuple[int, str]:
            seen_argv.append(argv)
            log.write_text("ok\n")
            target = evals / "edge_typing" / "results"
            (target / "runs-new.json").write_text(
                json.dumps(
                    {
                        "stamp": {
                            "stamp_version": 1,
                            "harness": {"commit": "abc", "dirty": False},
                            "model": {"name": "m", "digest": "d"},
                            "prompts": [{"id": "p", "sha256_16": "0" * 16}],
                        }
                    }
                )
            )
            (target / "edge-typing-new.md").write_text("# report")
            return outcome["code"], "tail"

        meta = execute_cell(
            spec_obj,
            "phi4:14b",
            1,
            results=results,
            runs=15,
            session="s1",
            runner=fake_runner,
            evals_root=evals,
            swap_s=12.3,
            log=lambda _m: None,
        )
        directory = cell_dir(results, "edge_typing", "phi4:14b", 1)
        check("a clean run is done", meta["status"], "done")
        check(
            "the harness is called with the model and n=15",
            seen_argv[0][-6:],
            ["--arm", "bakeoff-phi4-14b-r1", "--runs", "15", "--model", "phi4:14b"][
                -6:
            ],
        )
        check(
            "new harness files are moved into the cell",
            sorted(
                p.name
                for p in directory.iterdir()
                if p.suffix in (".json", ".md") and p.name != "cell.json"
            ),
            ["edge-typing-new.md", "runs-new.json"],
        )
        check(
            "a pre-existing harness file is left alone",
            (evals / "edge_typing" / "results" / "preexisting.json").is_file(),
            True,
        )
        check(
            "the stamp is lifted into cell.json", meta["stamp"]["model"]["digest"], "d"
        )
        check("a complete stamp has no problems", meta["stamp_problems"], [])
        check("swap time is recorded", meta["swap_s"], 12.3)
        check("cell.json round-trips", read_cell(directory)["status"], "done")  # type: ignore[index]

        outcome["code"] = 1
        failed_meta = execute_cell(
            spec_obj,
            "gemma4:12b",
            1,
            results=results,
            runs=15,
            session="s1",
            runner=fake_runner,
            evals_root=evals,
            log=lambda _m: None,
        )
        check(
            "a nonzero exit is a failed cell, not an exception",
            failed_meta["status"],
            "failed",
        )
        check(
            "and it is retried next time",
            decide_run(failed_meta, repeat=1, first_repeat_meta=None, session="s1")[0],
            "run",
        )
        (directory / "cell.json").write_text("{torn")
        check("a torn cell.json reads as not done", read_cell(directory), None)

        # An explicit-output harness: files stay where the harness wrote them.
        cap = spec_mod.harness("extraction_cap")
        (evals / "extraction_cap").mkdir(parents=True)
        (evals / "extraction_cap" / "run_cap_eval.py").write_text("")
        out_argv: list[list[str]] = []

        def cap_runner(argv: list[str], log: pathlib.Path) -> tuple[int, str]:
            out_argv.append(argv)
            log.write_text("")
            return 0, ""

        execute_cell(
            cap,
            "qwen3:8b",
            1,
            results=results,
            runs=15,
            session="s1",
            runner=cap_runner,
            evals_root=evals,
            log=lambda _m: None,
        )
        check(
            "the cap harness is given its output path inside the cell",
            out_argv[0][-1],
            str(cell_dir(results, "extraction_cap", "qwen3:8b", 1) / "report.md"),
        )
        check(
            "and asked for union+judge on",
            "--union-judge" in out_argv[0]
            and out_argv[0][out_argv[0].index("--union-judge") + 1] == "on",
            True,
        )

        # auto_merge: two arms then --decide.
        (evals / "auto_merge").mkdir(parents=True)
        (evals / "auto_merge" / "results").mkdir()
        (evals / "auto_merge" / "run_auto_merge_eval.py").write_text("")
        am_calls: list[list[str]] = []

        def am_runner(argv: list[str], log: pathlib.Path) -> tuple[int, str]:
            am_calls.append(argv)
            log.write_text("")
            target = evals / "auto_merge" / "results"
            if "--arm" in argv:
                arm = argv[argv.index("--arm") + 1]
                (target / f"runs-{arm}-x.json").write_text("{}")
            else:
                (target / "auto-merge-verdict-x.md").write_text("**Verdict: FAIL**")
            return 0, ""

        am_meta = execute_cell(
            spec_mod.harness("auto_merge"),
            "qwen3:8b",
            1,
            results=results,
            runs=15,
            session="s1",
            runner=am_runner,
            evals_root=evals,
            log=lambda _m: None,
        )
        check(
            "auto_merge runs both arms then decides",
            [("--decide" in c) for c in am_calls],
            [False, False, True],
        )
        check(
            "auto_merge is done and collected its verdict",
            (
                am_meta["status"],
                any(
                    "verdict" in f
                    for i in am_meta["invocations"]
                    for f in i.get("files", [])
                ),
            ),
            ("done", True),
        )

        # ---- the plan ----
        elig = _eligibility_path(results, "phi4:14b")
        elig.parent.mkdir(parents=True, exist_ok=True)
        elig.write_text(json.dumps({"state": "eligible"}))
        plan = build_plan(results, runs=15, session="s1")
        baseline_edge = [
            i for i in plan if i.harness == "edge_typing" and i.model == "qwen3:8b"
        ]
        check(
            "edge_typing baseline is planned twice",
            [i.repeat for i in baseline_edge],
            [1, 2],
        )
        check(
            "a second baseline session is deferred, not forecast as unconditional",
            [i.state for i in baseline_edge],
            ["todo", "deferred"],
        )
        first_stage = {
            i.harness
            for i in plan
            if i.model == "phi4:14b" and i.state == "todo" and i.stage == "2-judge"
        }
        check(
            "the cheapest judge harness is the unconditional first stage",
            first_stage,
            {"query_sufficiency"},
        )
        check(
            "an unchecked candidate's first stage is conditional on eligibility",
            [
                i.state
                for i in plan
                if i.model == "gemma4:12b" and i.harness == "query_sufficiency"
            ],
            ["conditional"],
        )
        later = [
            i
            for i in plan
            if i.model == "phi4:14b"
            and i.stage == "2-judge"
            and i.harness == "decision_revisions"
        ]
        check(
            "later stages are conditional on surviving",
            [i.state for i in later],
            ["conditional"],
        )
        text = render_plan(plan, runs=15, session="s1")
        check(
            "the plan prints a time forecast",
            "## Time forecast" in text and "upper bound with no knockout" in text,
            True,
        )
        check(
            "the plan says the same-family arm is not measured",
            "same-family arm" in text and "Not measured this round" in text,
            True,
        )
        check(
            "the plan says Qwen3.6 runs thinking off only",
            "thinking OFF only" in text,
            True,
        )
        short = render_plan(plan, runs=5, session="s1")
        check(
            "a non-registered n is called out", "NOT the registered n=15" in short, True
        )
        done_plan = [
            i
            for i in plan
            if i.harness == "extraction_cap" and i.model == "qwen3:8b" and i.repeat == 1
        ]
        check("a finished cell shows as done in the plan", done_plan[0].state, "done")

        # ---- knockout through the loop, with synthetic metrics ----
        fake: dict[tuple[str, str, str], Metrics] = {}

        def fake_loader(harness_name: str, directory: pathlib.Path) -> Metrics:
            return dict(
                fake.get((harness_name, directory.parent.name, directory.name), {})
            )

        def mark(
            harness_name: str,
            model: str,
            metrics: Mapping[str, float | None],
            repeat: int = 1,
        ) -> None:
            d = cell_dir(results, harness_name, model, repeat)
            d.mkdir(parents=True, exist_ok=True)
            (d / "cell.json").write_text(
                json.dumps(
                    {
                        "status": "done",
                        "session": "s0",
                        "harness": harness_name,
                        "model": model,
                        "repeat": repeat,
                    }
                )
            )
            fake[(harness_name, slug(model), f"r{repeat}")] = dict(metrics)

        suff_base = {
            "grounded_refused": 0.0,
            "adjacent_missed": 0.0,
            "survivors_missed": 0.0,
            "latency_median_nonrefused_s": 1.0,
        }
        adj_base = {
            "hard_negative_different_rate": 0.6,
            "event_same_rate": 1.0,
            "person_same_rate": 1.0,
            "alias_same_rate": 1.0,
            "recurrence_precision": 1.0,
            "transitivity_violation_rate": 0.0,
            "latency_median_run_s": 60.0,
        }
        mark("query_sufficiency", "qwen3:8b", suff_base)
        mark("adjudication", "qwen3:8b", adj_base)
        # phi4 clears sufficiency; gemma4:12b is vetoed by it.
        mark("query_sufficiency", "phi4:14b", suff_base)
        mark("query_sufficiency", "gemma4:12b", {**suff_base, "grounded_refused": 3.0})
        mark(
            "adjudication", "gemma4:12b", adj_base
        )  # present on disk, but must NOT be consulted
        ev = evaluate_all(
            results, only_models=["phi4:14b", "gemma4:12b"], loader=fake_loader
        )
        check(
            "a vetoed candidate is dropped at the first stage",
            (
                ev.knockouts[("judge", "gemma4:12b")].status,
                ev.knockouts[("judge", "gemma4:12b")].stopped_at,
            ),
            ("dropped", "query_sufficiency"),
        )
        check(
            "the later harness is not consulted for a dropped candidate",
            ("adjudication", "gemma4:12b") in ev.verdicts,
            False,
        )
        check(
            "a candidate that cleared stage one but has not run stage two is pending",
            (
                ev.knockouts[("judge", "phi4:14b")].status,
                ev.knockouts[("judge", "phi4:14b")].stopped_at,
            ),
            ("pending", "adjudication"),
        )
        status, note = role_status(ev, "judge", "identity", "gemma4:12b")
        check("a role behind the drop reads SKIPPED", status, "SKIPPED")
        check("the skip names the drop", "query_sufficiency" in note, True)
        check(
            "the vetoed role reads DROPPED",
            role_status(ev, "judge", "sufficiency", "gemma4:12b")[0],
            "DROPPED",
        )

        # Clarification 3: a blown latency budget drops, at the first harness.
        mark(
            "query_sufficiency",
            "gemma4:12b",
            {**suff_base, "latency_median_nonrefused_s": 2.5},
        )
        ev = evaluate_all(results, only_models=["gemma4:12b"], loader=fake_loader)
        slow = ev.knockouts[("judge", "gemma4:12b")]
        check(
            "a candidate over 2x the baseline latency is dropped",
            (slow.status, slow.stopped_at),
            ("dropped", "query_sufficiency"),
        )
        check("the drop names the latency bar", "S4-latency" in slow.reason, True)
        mark(
            "query_sufficiency",
            "gemma4:12b",
            {**suff_base, "latency_median_nonrefused_s": 2.0},
        )
        ev = evaluate_all(results, only_models=["gemma4:12b"], loader=fake_loader)
        check(
            "exactly 2x the baseline latency is within budget",
            ev.verdicts[("query_sufficiency", "gemma4:12b")].dropped,
            False,
        )

        # The baseline's own failure on an absolute veto is waived, not a drop.
        mark("query_sufficiency", "qwen3:8b", {**suff_base, "grounded_refused": 2.0})
        mark("query_sufficiency", "phi4:14b", {**suff_base, "grounded_refused": 2.0})
        ev = evaluate_all(results, only_models=["phi4:14b"], loader=fake_loader)
        check(
            "a bar the baseline already fails does not gate",
            ev.verdicts[("query_sufficiency", "phi4:14b")].dropped,
            False,
        )

        # Baseline spread.
        mark(
            "adjudication",
            "qwen3:8b",
            {**adj_base, "hard_negative_different_rate": 0.7},
            repeat=2,
        )
        ev = evaluate_all(results, only_models=["phi4:14b"], loader=fake_loader)
        check(
            "baseline spread is |r1 - r2|",
            round(ev.spread["adjudication"]["hard_negative_different_rate"], 6),
            0.1,
        )

        # Write picks the top 2 Generate survivors by recall gain.
        base_gen = {
            "recall_precap:a": 0.5,
            "recall_precap:b": 0.5,
            "precision_precap": 0.9,
            "facets_per_run": 1.0,
            "unjudged_titles": 0.0,
            "latency_sweep_total_s": 100.0,
            "latency_median_s:a": 10.0,
            "latency_median_s:b": 10.0,
        }
        mark("extraction_cap", "qwen3:8b", base_gen)
        for tag, gain in (
            ("qwen3.6:27b", 0.30),
            ("gemma4:12b", 0.10),
            ("mistral-small3.2:24b", 0.20),
            ("qwen3.6:35b-a3b", 0.0),
        ):
            mark(
                "extraction_cap",
                tag,
                {
                    **base_gen,
                    "recall_precap:a": 0.5 + gain,
                    "recall_precap:b": 0.5 + gain,
                },
            )
        ev = evaluate_all(
            results,
            only_models=[
                "qwen3.6:27b",
                "gemma4:12b",
                "mistral-small3.2:24b",
                "qwen3.6:35b-a3b",
            ],
            loader=fake_loader,
        )
        check(
            "Write takes the two best Generate survivors",
            ev.write_choice,
            ["qwen3.6:27b", "mistral-small3.2:24b"],
        )
        mark(
            "extraction_cap",
            "mistral-small3.2:24b",
            {**base_gen, "unjudged_titles": 4.0},
        )
        ev = evaluate_all(
            results, only_models=["mistral-small3.2:24b"], loader=fake_loader
        )
        check(
            "an unworked adjudication queue BLOCKS, it does not drop",
            ev.knockouts[("generate", "mistral-small3.2:24b")].status,
            "blocked",
        )
        check(
            "and is not a Write candidate",
            "mistral-small3.2:24b" in ev.write_choice,
            False,
        )
        check(
            "rank is deterministic on ties",
            rank_generate(["b", "a"], {"a": {}, "b": {}}, {}),
            ["a", "b"],
        )

        # The report renders from the evaluation with no model.
        rendered = render_report(results, ev, runs=15, session="s1")
        check(
            "the report says the same-family arm is not measured",
            "same-family arm" in rendered,
            True,
        )
        check(
            "the report has a role x model table", "## Role x model" in rendered, True
        )
        check(
            "the report shows the baseline spread",
            "## Baseline spread" in rendered,
            True,
        )

    # ---- eligibility against a fake server ----
    class _Resp:
        def __init__(self, payload: object) -> None:
            self._raw = json.dumps(payload).encode()

        def read(self) -> bytes:
            return self._raw

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    resident: dict[str, int] = {}
    pulled_models = {
        "phi4:14b": ("MIT License", 16384),
        "big:35b": ("Apache License\nVersion 2.0", 40960),
        "gemma2:27b": ("Gemma Terms of Use", 8192),
    }

    def server(request: Any, timeout: float) -> _Resp:
        if isinstance(request, str):
            path, body = urllib.parse.urlsplit(request).path, {}
        else:
            path, body = (
                urllib.parse.urlsplit(request.full_url).path,
                json.loads(request.data),
            )
        if path == "/api/tags":
            return _Resp(
                {"models": [{"name": n, "digest": f"dig-{n}"} for n in pulled_models]}
            )
        if path == "/api/show":
            lic, ctx = pulled_models[body["model"]]
            return _Resp({"license": lic, "model_info": {"x.context_length": ctx}})
        if path == "/api/ps":
            return _Resp(
                {
                    "models": [
                        {"name": n, "size": s, "size_vram": s}
                        for n, s in resident.items()
                    ]
                }
            )
        if path == "/api/generate" and body.get("keep_alive") == 0:
            resident.pop(body["model"], None)
        elif path == "/api/generate":
            resident[body["model"]] = (30 if body["model"] == "big:35b" else 10) * 10**9
        elif path == "/api/embed":
            resident[spec_mod.EMBEDDING_MODEL] = int(1.2e9)
        return _Resp({})

    with tempfile.TemporaryDirectory() as tmp:
        results = pathlib.Path(tmp)
        client = ollama_mod.OllamaLocal("localhost", opener=server)
        fits = ensure_eligibility(
            client,
            Candidate("phi4:14b", ("judge",), 9.0, 2.2, ""),
            results,
            accepted_licenses={},
        )
        check("a small MIT model is eligible", fits["state"], "eligible")
        check(
            "memory was measured at both contexts",
            sorted(fits["memory"]),
            ["12288", "32768"],
        )
        big = ensure_eligibility(
            client,
            Candidate("big:35b", ("generate",), 23.5, 1.5, ""),
            results,
            accepted_licenses={},
        )
        check("a model that blows the budget is ineligible", big["state"], "ineligible")
        gemma2 = ensure_eligibility(
            client,
            Candidate("gemma2:27b", ("generate",), 15.0, 5.5, ""),
            results,
            accepted_licenses={},
        )
        check(
            "gemma2 fails on license AND context, with no memory load",
            (gemma2["state"], len(gemma2["reasons"]), gemma2["memory"]),
            ("ineligible", 2, {}),
        )
        absent = ensure_eligibility(
            client,
            Candidate("qwen3.6:27b", ("generate",), 17.0, 4.5, ""),
            results,
            accepted_licenses={},
        )
        check(
            "an unpulled model is pending",
            (absent["state"], absent["pulled"]),
            ("pending", False),
        )
        check(
            "eligible is cached across invocations",
            _eligibility_state(results, "phi4:14b"),
            "eligible",
        )
        again = ensure_eligibility(
            client,
            Candidate("phi4:14b", ("judge",), 9.0, 2.2, ""),
            results,
            accepted_licenses={},
        )
        check(
            "a cached verdict is reused, not re-measured",
            again["measured_at"],
            fits["measured_at"],
        )
        attested = ensure_eligibility(
            client,
            Candidate("gemma2:27b", ("generate",), 15.0, 5.5, ""),
            pathlib.Path(tmp) / "other",
            accepted_licenses={"gemma2:27b": "apache-2.0"},
        )
        check(
            "an operator-attested license is recorded as such",
            attested.get("license_attested_by_operator"),
            True,
        )

    for name in failures:
        print(f"FAIL: {name}")
    print(f"self-test: {checks - len(failures)}/{checks} passed")
    return 1 if failures else 0


# --------------------------------------------------------------------------
# CLI.
# --------------------------------------------------------------------------


def _split(value: str | None) -> list[str] | None:
    return (
        [part.strip() for part in value.split(",") if part.strip()] if value else None
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan",
        action="store_true",
        help="dry run: print the plan and the time forecast; no model call, nothing written",
    )
    mode.add_argument("--run", action="store_true", help="execute the plan (resumable)")
    mode.add_argument(
        "--evaluate-only",
        action="store_true",
        help="recompute verdicts and the report from stored cells; no model call",
    )
    mode.add_argument(
        "--self-test",
        action="store_true",
        help="check the driver with fakes: no model, no network",
    )
    parser.add_argument("--results-dir", type=pathlib.Path, default=DEFAULT_RESULTS)
    parser.add_argument(
        "--runs",
        type=int,
        default=spec_mod.RUNS,
        help="runs per arm (the registered value is 15)",
    )
    parser.add_argument(
        "--models",
        help="comma-separated subset of models (the baseline is always needed for a comparison)",
    )
    parser.add_argument(
        "--families", help="comma-separated subset of: label,judge,generate,write"
    )
    parser.add_argument(
        "--session",
        default=None,
        help="session id; a baseline repeat 2 never runs in repeat 1's session",
    )
    parser.add_argument(
        "--host", default=None, help="local Ollama host (loopback only)"
    )
    parser.add_argument(
        "--accept-license",
        action="append",
        default=[],
        metavar="TAG=SPDX",
        help="record an operator-verified license for a model whose card /api/show does not carry (apache-2.0 or mit)",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not (args.plan or args.run or args.evaluate_only):
        parser.error("choose one of --plan, --run, --evaluate-only")

    session = args.session or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    only_models = _split(args.models)
    only_families = _split(args.families)
    results: pathlib.Path = args.results_dir

    if args.plan:
        items = build_plan(
            results,
            runs=args.runs,
            session=session,
            only_models=only_models,
            only_families=only_families,
        )
        print(render_plan(items, runs=args.runs, session=session))
        print(_local_snapshot(args.host))
        return 0

    if args.evaluate_only:
        ev = evaluate_all(results, only_models=only_models)
        results.mkdir(parents=True, exist_ok=True)
        text = render_report(results, ev, runs=args.runs, session=session)
        (results / "report.md").write_text(text, encoding="utf-8")
        print(text)
        return 0

    accepted: dict[str, str] = {}
    for entry in args.accept_license:
        tag, _, spdx = entry.partition("=")
        if spdx not in spec_mod.LICENSE_ALLOWLIST:
            parser.error(
                f"--accept-license {entry!r}: {spdx!r} is not on the allowlist {list(spec_mod.LICENSE_ALLOWLIST)}"
            )
        accepted[tag] = spdx
    client: ollama_mod.OllamaLocal | None = None
    try:
        candidate_client = ollama_mod.OllamaLocal(args.host)
        candidate_client.tags()
        client = candidate_client
    except (OSError, ValueError) as exc:
        print(f"no usable local Ollama: {exc}", file=sys.stderr)
    results.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(
        results=results,
        runs=args.runs,
        session=session,
        runner=default_runner,
        client=client,
        accepted_licenses=accepted,
        only_models=only_models,
        only_families=only_families,
    )
    ev = run_bakeoff(ctx)
    text = render_report(results, ev, runs=args.runs, session=session)
    (results / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


def _local_snapshot(host: str | None) -> str:
    """What the local Ollama holds right now (read-only `/api/tags`), so the
    dry run says which candidates still need a pull."""
    try:
        tags = ollama_mod.OllamaLocal(host).tags()
    except (OSError, ValueError) as exc:
        return f"Local Ollama: unreachable ({type(exc).__name__}: {exc}). Pull state unknown.\n"
    lines = ["## Pull state (local `/api/tags`, read-only)", ""]
    for candidate in [spec_mod.BASELINE_CANDIDATE, *spec_mod.CANDIDATES]:
        digest = ollama_mod.listed_digest(tags, candidate.tag)
        lines.append(
            f"- `{candidate.tag}`: "
            + (
                f"pulled, digest `{digest[:12]}`"
                if digest
                else f"NOT pulled (about {candidate.approx_gb:g} GB; this driver never downloads)"
            )
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
