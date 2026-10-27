"""Powered equivalence test: llama.cpp vs Ollama on the same weights (#1590).

The rules are registered in `PREREGISTRATION-1590.md` and implemented in
`equivalence_core.py`. This runner drives the existing #1557 concept-extraction
harness (`evals/concept_model_screen/run_concept_screen.py`) and reads its
outputs; it adds no measurement of its own.

    uv run python -u evals/runtime_equivalence/run_equivalence.py --self-test
    uv run python -u evals/runtime_equivalence/run_equivalence.py plan
    uv run python -u evals/runtime_equivalence/run_equivalence.py setup
    uv run python -u evals/runtime_equivalence/run_equivalence.py preflight --hash
    uv run python -u evals/runtime_equivalence/run_equivalence.py run
    uv run python -u evals/runtime_equivalence/run_equivalence.py decide

`run` is resumable: a finished phase is recorded in `state.json` under the
runs directory and skipped on the next start, and the harness itself skips
every record already on disk. Raw outputs live outside the repository.

The only server this runner starts is its own `llama-server`, bound to
127.0.0.1 and stopped by the PID it was given. Before it starts, `ollama ps`
must show no model it does not own, and the models it does own are unloaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Final

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import equivalence_core as core  # noqa: E402

REPO_ROOT: Final = _HERE.parents[1]
HARNESS: Final = REPO_ROOT / "evals" / "concept_model_screen" / "run_concept_screen.py"
DEFAULT_RUNS_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/equivalence-1590"
)
SOURCE_RUNS_DIR: Final = (
    pathlib.Path.home() / "Dev/Projects/openkos-eval-runs/concept-screen"
)
BLOB: Final = (
    pathlib.Path.home()
    / ".ollama/models/blobs"
    / "sha256-a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f"
)
BLOB_SHA256: Final = "a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f"
BLOB_BYTES: Final = 5_225_374_496
OLLAMA_ID: Final = "500a1f067a9f"
"""`ollama list`'s ID column for `qwen3:8b`; the full digest is in the stored stamp."""
PORT: Final = 8081
BASE_URL: Final = f"http://127.0.0.1:{PORT}"
UV: Final = shutil.which("uv") or "/opt/homebrew/bin/uv"
OLLAMA: Final = shutil.which("ollama") or "/opt/homebrew/bin/ollama"
LLAMA_SERVER: Final = shutil.which("llama-server") or "/opt/homebrew/bin/llama-server"
MIN_MEM_BYTES: Final = 40 * 1024**3
MIN_FREE_BYTES: Final = 10 * 1024**3

POOL_DA: Final = ("ES2015a", "IS1004b", "TS3009c", "ES2015d", "IS1004d")
POOL_F: Final = (
    "IS1002b",
    "IS1002c",
    "IS1002d",
    "IS1005a",
    "IS1005b",
    "IS1005c",
    "TS3012a",
    "TS3012b",
    "TS3012d",
)
POOLS: Final = {"DA": POOL_DA, "F": POOL_F}
FAILED_STATES: Final = frozenset(
    {"timeout", "capped", "error", "unparseable", "not_called"}
)


def safe(model: str) -> str:
    """The harness's directory name for a model label."""
    return model.replace(":", "_").replace("/", "_")


# --- command construction (pure) ---------------------------------------------


def llama_server_argv() -> list[str]:
    """#1586's registered llama.cpp configuration for `qwen3:8b`, unchanged."""
    return [
        LLAMA_SERVER, "-m", str(BLOB), "--host", "127.0.0.1", "--port", str(PORT),
        "-c", "12288", "-np", "1", "-ngl", "999", "-fa", "on",
        "-ctk", "q8_0", "-ctv", "q8_0", "--reasoning", "off", "-a", "qwen3:8b",
        "--temp", "0.6", "--top-k", "20", "--top-p", "0.95", "--min-p", "0",
        "--repeat-penalty", "1",
    ]  # fmt: skip


def harness(runs_dir: pathlib.Path, *args: str) -> list[str]:
    return [UV, "run", "python", "-u", str(HARNESS), "--runs-dir", str(runs_dir), *args]


def llama_run_argv(runs_dir: pathlib.Path, pool: str, runs: Sequence[int]) -> list[str]:
    return harness(
        runs_dir, "run", "--models", core.LLAMA_LABEL, "--pool", pool,
        "--runs", *map(str, runs), "--backend", "openai-compatible",
        "--base-url", BASE_URL, "--served-model", "qwen3:8b",
        "--digest", f"sha256:{BLOB_SHA256}", "--timeout-from", core.BASELINE_LABEL,
    )  # fmt: skip


def ollama_run_argv(
    runs_dir: pathlib.Path, pool: str, runs: Sequence[int]
) -> list[str]:
    return harness(
        runs_dir, "run", "--models", core.BASELINE_LABEL, "--pool", pool,
        "--runs", *map(str, runs),
    )  # fmt: skip


def judge_argv(runs_dir: pathlib.Path, model: str, pool: str, k: int) -> list[str]:
    return harness(
        runs_dir, "judge", "--models", model, "--pool", pool,
        "--runs", *map(str, range(1, k + 1)),
    )  # fmt: skip


def score_argv(runs_dir: pathlib.Path, pool: str, k: int) -> list[str]:
    return harness(
        runs_dir, "score", "--name", f"eq-{pool}-k{k}",
        "--models", core.BASELINE_LABEL, core.ALIAS_LABEL, "--pool", pool,
        "--runs", *map(str, range(1, k + 1)), "--g1",
    )  # fmt: skip


def phases(k_max: int) -> list[str]:
    """The registered order. A phase is skipped once done; the third-replicate
    phases run only when `core.third_replicate_allowed`."""
    out = ["llama-5-6", "judge-score-k2"]
    if k_max >= 3:
        out += ["ollama-da-3", "llama-7", "judge-score-k3"]
    return out


# --- state, processes ---------------------------------------------------------


def load_state(runs_dir: pathlib.Path) -> dict[str, Any]:
    path = runs_dir / "state.json"
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    return {"done": {}, "measured_s": 0.0, "started": None}


def save_state(runs_dir: pathlib.Path, state: dict[str, Any]) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    tmp = runs_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(runs_dir / "state.json")


def ollama_ps() -> list[str]:
    done = subprocess.run(  # noqa: S603 -- the local ollama CLI, a fixed verb
        [OLLAMA, "ps"], capture_output=True, text=True, check=False, timeout=60
    )
    return core.loaded_models(done.stdout)


def unload_owned() -> None:
    """Unload only the models this runner owns; refuse beside anything else."""
    loaded = ollama_ps()
    foreign = core.foreign_models(loaded)
    if foreign:
        raise SystemExit(
            f"refusing: Ollama holds models this runner does not own: {foreign}"
        )
    for name in loaded:
        subprocess.run(  # noqa: S603 -- the local ollama CLI, a fixed verb
            [OLLAMA, "stop", name], capture_output=True, check=False, timeout=120
        )
    deadline = time.monotonic() + 60
    while ollama_ps() and time.monotonic() < deadline:
        time.sleep(1)


def wait_health(deadline_s: float = 600.0) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=5) as r:  # noqa: S310 -- loopback
                if r.status == 200:
                    return
        except urllib.error.URLError, OSError:
            pass
        time.sleep(1)
    raise SystemExit("llama-server did not become healthy")


def run_logged(argv: Sequence[str], log: pathlib.Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as fh:
        done = subprocess.run(  # noqa: S603 -- argv built from fixed parts above
            list(argv), stdout=fh, stderr=subprocess.STDOUT, check=False, cwd=REPO_ROOT
        )
    return done.returncode


# --- preflight ---------------------------------------------------------------


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preflight(runs_dir: pathlib.Path, *, check_hash: bool) -> list[str]:
    """Every reason the run must not start. Empty means go."""
    problems: list[str] = []

    def out(argv: list[str]) -> str:
        done = subprocess.run(  # noqa: S603 -- fixed read-only verbs
            argv, capture_output=True, text=True, check=False, timeout=120
        )
        return done.stdout + done.stderr

    if core.LLAMA_BUILD_MARKER not in out([LLAMA_SERVER, "--version"]):
        problems.append(f"llama-server is not {core.LLAMA_BUILD_MARKER}")
    if not BLOB.is_file() or BLOB.stat().st_size != BLOB_BYTES:
        problems.append("the qwen3:8b Ollama blob is missing or has the wrong size")
    elif check_hash and sha256_file(BLOB) != BLOB_SHA256:
        problems.append(
            "the qwen3:8b Ollama blob does not hash to the registered digest"
        )
    if OLLAMA_ID not in out([OLLAMA, "list"]):
        problems.append(f"`ollama list` no longer shows qwen3:8b as {OLLAMA_ID}")
    loaded = ollama_ps()
    if loaded:
        problems.append(f"Ollama is not idle: {loaded}")
    running = subprocess.run(
        ["/usr/bin/pgrep", "-x", "llama-server"], capture_output=True, check=False
    )
    if running.returncode == 0:
        problems.append("a llama-server process is already running")
    mem = int(out(["/usr/sbin/sysctl", "-n", "hw.memsize"]).strip() or 0)
    if mem < MIN_MEM_BYTES:
        problems.append(f"memory {mem / 1024**3:.0f} GiB is below the registered floor")
    if shutil.disk_usage(runs_dir.parent).free < MIN_FREE_BYTES:
        problems.append("less than 10 GiB free beside the runs directory")
    if not (runs_dir / ".setup-done").is_file():
        problems.append("`setup` has not run")
    corpus = REPO_ROOT / "examples" / "extraction-corpus" / "sources"
    if len(list(corpus.glob("*.md"))) < 5:
        problems.append(
            "the five extraction-corpus sources are not all present (gitignored)"
        )
    return problems


# --- phases ------------------------------------------------------------------


def setup(runs_dir: pathlib.Path, source: pathlib.Path) -> None:
    """Copy the stored #1557 runs directory (inputs, caches, Ollama records and
    judgments) into a fresh runs directory. The source is only read."""
    marker = runs_dir / ".setup-done"
    if marker.is_file():
        print("setup already done")
        return
    if runs_dir.exists() and any(runs_dir.iterdir()):
        raise SystemExit(f"{runs_dir} exists and is not an empty setup")
    runs_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        runs_dir,
        ignore=shutil.ignore_patterns("records-pre-dedup-fix", "scores", "*.log"),
    )
    for name in ("qwen3.6_27b", "qwen3.6_35b-a3b", "qwen3_14b", "gemma4_26b-a4b"):
        for sub in ("records", "judge"):
            shutil.rmtree(runs_dir / sub / name, ignore_errors=True)
    marker.write_text(time.strftime("%Y-%m-%d %H:%M:%S\n"), encoding="utf-8")
    print(f"copied {source} -> {runs_dir}")


def reindex(runs_dir: pathlib.Path, k: int) -> int:
    """Copy llama.cpp runs 5..(4+k) to the alias label as runs 1..k."""
    moved = 0
    src_root = runs_dir / "records" / safe(core.LLAMA_LABEL)
    dst_root = runs_dir / "records" / safe(core.ALIAS_LABEL)
    for pool in POOLS.values():
        for source in pool:
            for i in range(k):
                src = src_root / source / f"r{core.LLAMA_RUNS[i]}.json"
                if not src.is_file():
                    continue
                record = json.loads(src.read_text(encoding="utf-8"))
                moved_record = core.reindex_record(
                    record, label=core.ALIAS_LABEL, new_run=i + 1
                )
                dst = dst_root / source / f"r{i + 1}.json"
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(
                    json.dumps(moved_record, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                moved += 1
    stamp = src_root / "stamp.json"
    if stamp.is_file():
        (dst_root).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stamp, dst_root / "stamp.json")
    return moved


def phase_llama(runs_dir: pathlib.Path, runs: Sequence[int]) -> None:
    unload_owned()
    log = runs_dir / "logs" / "llama-server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as fh:
        server = subprocess.Popen(  # noqa: S603 -- argv built from fixed parts above
            llama_server_argv(),
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        wait_health()
        for pool in POOLS:
            code = run_logged(
                llama_run_argv(runs_dir, pool, runs),
                runs_dir / "logs" / f"llama-{pool}.log",
            )
            if code != 0:
                raise SystemExit(
                    f"llama.cpp run failed on pool {pool}; see logs (no retry)"
                )
    finally:
        server.terminate()
        try:
            server.wait(timeout=60)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=30)


def phase_ollama_da3(runs_dir: pathlib.Path) -> None:
    unload_owned()
    code = run_logged(
        ollama_run_argv(runs_dir, "DA", [core.OLLAMA_RUNS[2]]),
        runs_dir / "logs" / "ollama-DA-3.log",
    )
    if code != 0:
        raise SystemExit("Ollama DA run 3 failed; see logs (no retry)")


def phase_judge_score(runs_dir: pathlib.Path, k: int) -> None:
    unload_owned()
    reindex(runs_dir, k)
    for pool in POOLS:
        for model in (core.BASELINE_LABEL, core.ALIAS_LABEL):
            code = run_logged(
                judge_argv(runs_dir, model, pool, k),
                runs_dir / "logs" / f"judge-{pool}.log",
            )
            if code != 0:
                raise SystemExit(f"judge failed ({model}, {pool}); see logs (no retry)")
    for pool in POOLS:
        code = run_logged(
            score_argv(runs_dir, pool, k), runs_dir / "logs" / f"score-{pool}.log"
        )
        if code != 0:
            raise SystemExit(f"score failed ({pool}); see logs (no retry)")
    unload_owned()


def cmd_run(args: argparse.Namespace) -> int:
    runs_dir: pathlib.Path = args.runs_dir
    problems = preflight(runs_dir, check_hash=args.hash)
    if problems:
        for p in problems:
            print(f"REFUSED: {p}")
        return 3
    state = load_state(runs_dir)
    state["started"] = state["started"] or time.strftime("%Y-%m-%d %H:%M:%S")
    plan = phases(core.K_REPLICATES)
    for name in plan:
        if state["done"].get(name):
            print(f"skip {name} (done)")
            continue
        if name == "ollama-da-3" and not core.third_replicate_allowed(
            elapsed_s=state["measured_s"]
        ):
            print("third replicate does not fit the budget: stopping at k=2")
            state["done"]["k3-skipped"] = True
            save_state(runs_dir, state)
            return cmd_decide(args)
        if name in {"llama-7", "judge-score-k3"} and state["done"].get("k3-skipped"):
            continue
        print(f"{time.strftime('%H:%M:%S')} phase {name}", flush=True)
        started = time.monotonic()
        if name == "llama-5-6":
            phase_llama(runs_dir, core.LLAMA_RUNS[:2])
        elif name == "llama-7":
            phase_llama(runs_dir, core.LLAMA_RUNS[2:])
        elif name == "ollama-da-3":
            phase_ollama_da3(runs_dir)
        elif name == "judge-score-k2":
            phase_judge_score(runs_dir, 2)
        elif name == "judge-score-k3":
            phase_judge_score(runs_dir, 3)
        state["done"][name] = True
        state["measured_s"] += time.monotonic() - started
        save_state(runs_dir, state)
    return cmd_decide(args)


# --- analysis ----------------------------------------------------------------


def _read(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def failure_rate(
    runs_dir: pathlib.Path, label: str, runs: Sequence[int]
) -> tuple[float, int]:
    failed = total = 0
    for pool in POOLS.values():
        for source in pool:
            for run in runs:
                path = runs_dir / "records" / safe(label) / source / f"r{run}.json"
                if not path.is_file():
                    continue
                for w in _read(path)["windows"]:
                    total += 1
                    failed += w["outcome"] in FAILED_STATES
    return (failed / total if total else 0.0), total


def median_window_s(
    runs_dir: pathlib.Path, label: str, runs: Sequence[int]
) -> float | None:
    times: list[float] = []
    for pool in POOLS.values():
        for source in pool:
            for run in runs:
                path = runs_dir / "records" / safe(label) / source / f"r{run}.json"
                if path.is_file():
                    times += [
                        float(c["wall_s"])
                        for c in _read(path)["calls"]
                        if c["kind"] == "window"
                    ]
    return statistics.median(times) if times else None


def load_judge(
    runs_dir: pathlib.Path, label: str, k: int
) -> dict[str, dict[int, list[dict[str, Any]]]]:
    out: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for pool in POOLS.values():
        for source in pool:
            for run in range(1, k + 1):
                path = runs_dir / "judge" / safe(label) / source / f"r{run}.json"
                if path.is_file():
                    out.setdefault(source, {})[run] = _read(path)["g1"]
    return out


def analyze(
    *,
    per_window: dict[str, dict[str, dict[str, list[float]]]],
    judge: dict[str, dict[str, dict[int, list[dict[str, Any]]]]],
    fail: dict[str, float],
    k: int,
    complete: bool,
    resamples: int = core.BOOTSTRAP_RESAMPLES,
    seed: int = core.BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """The registered statistics from already-scored inputs (model-free)."""
    diffs = core.window_diffs(per_window["ollama"], per_window["llama"], runs=k)
    lift_reps = core.bootstrap_means(diffs, resamples=resamples, seed=seed)
    lo90, hi90 = core.interval(lift_reps, level=0.90)
    lo95, hi95 = core.interval(lift_reps, level=0.95)
    flat = [d for ds in diffs.values() for d in ds]
    clusters = core.g1_clusters(judge["ollama"], judge["llama"], runs=k)
    g1_reps = core.bootstrap_precision(clusters, resamples, seed)
    g_lo, g_hi = core.interval(g1_reps, level=0.95, two_sided=False)
    point = core.pooled_precision_delta(clusters)
    veto = core.failure_veto(llama=fail["llama"], ollama=fail["ollama"])
    lift_v = core.lift_verdict(lo90, hi90, margin=core.LIFT_MARGIN)
    g1_v = core.g1_verdict(g_lo, g_hi, margin=core.G1_MARGIN)
    verdict = core.decide(lift=lift_v, g1=g1_v, veto=veto, complete=complete)
    return {
        "k": k,
        "windows_with_gold": len(flat),
        "lift": {
            "point": sum(flat) / len(flat) if flat else None,
            "ci90": [lo90, hi90],
            "ci95": [lo95, hi95],
            "margin": core.LIFT_MARGIN,
            "verdict": lift_v,
        },
        "g1": {
            "point": point,
            "one_sided_95": [g_lo, g_hi],
            "margin": core.G1_MARGIN,
            "verdict": g1_v,
            "reported": {
                str(m): core.g1_verdict(g_lo, g_hi, margin=m)
                for m in core.G1_REPORTED_MARGINS
            },
            "objects": {
                "llama": sum(c[1] for cs in clusters.values() for c in cs),
                "ollama": sum(c[3] for cs in clusters.values() for c in cs),
            },
        },
        "failure_rate": fail,
        "veto": veto,
        **verdict,
    }


def cmd_decide(args: argparse.Namespace) -> int:
    runs_dir: pathlib.Path = args.runs_dir
    state = load_state(runs_dir)
    done = state["done"]
    k = 3 if done.get("judge-score-k3") else 2 if done.get("judge-score-k2") else 0
    if k == 0:
        print("INCONCLUSIVE: no analyzable phase has completed")
        return 0
    per: dict[str, dict[str, dict[str, list[float]]]] = {"ollama": {}, "llama": {}}
    for pool in POOLS:
        scores = _read(runs_dir / "scores" / f"eq-{pool}-k{k}.json")["per_window"]
        per["ollama"].update(scores[core.BASELINE_LABEL])
        per["llama"].update(scores[core.ALIAS_LABEL])
    ollama_runs = core.OLLAMA_RUNS[:k]
    llama_runs = core.LLAMA_RUNS[:k]
    fo, n_o = failure_rate(runs_dir, core.BASELINE_LABEL, ollama_runs)
    fl, n_l = failure_rate(runs_dir, core.LLAMA_LABEL, llama_runs)
    complete = k == core.K_REPLICATES and n_l == core.WINDOWS * k
    result = analyze(
        per_window=per,
        judge={
            "ollama": load_judge(runs_dir, core.BASELINE_LABEL, k),
            "llama": load_judge(runs_dir, core.ALIAS_LABEL, k),
        },
        fail={"ollama": fo, "llama": fl},
        k=k,
        complete=complete,
    )
    m_o = median_window_s(runs_dir, core.BASELINE_LABEL, ollama_runs)
    m_l = median_window_s(runs_dir, core.LLAMA_LABEL, llama_runs)
    result["timing"] = {
        "median_window_call_s": {"ollama": m_o, "llama": m_l},
        "ratio": (m_l / m_o) if m_o and m_l else None,
        "note": "reported, not gating; #1590's end-to-end bar is a separate registration",
    }
    result["window_runs"] = {"ollama": n_o, "llama": n_l}
    result["measured_s"] = state["measured_s"]
    out = runs_dir / "results-1590.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"VERDICT: {result['verdict']}")
    return 0


# --- plan --------------------------------------------------------------------


def plan_text() -> str:
    sd3 = core.diff_sd(core.SIGMA_RUN, 3, 3)
    sd2 = core.diff_sd(core.SIGMA_RUN, 2, 2)
    need3 = core.required_windows(
        sd=sd3,
        margin=core.LIFT_MARGIN,
        deff=core.DEFF,
        alpha=core.ALPHA,
        power=core.POWER,
    )
    need2 = core.required_windows(
        sd=sd2,
        margin=core.LIFT_MARGIN,
        deff=core.DEFF,
        alpha=core.ALPHA,
        power=core.POWER,
    )
    p3 = core.tost_power(
        sd=sd3,
        n=core.GOLD_WINDOWS,
        margin=core.LIFT_MARGIN,
        true_diff=0.0,
        deff=core.DEFF,
    )
    p2 = core.tost_power(
        sd=sd2,
        n=core.GOLD_WINDOWS,
        margin=core.LIFT_MARGIN,
        true_diff=0.0,
        deff=core.DEFF,
    )
    g1n = 200
    gp = core.ni_power(
        p=core.G1_PRECISION,
        n_per_arm=g1n,
        margin=core.G1_MARGIN,
        true_diff=0.0,
        deff=core.DEFF,
    )
    g1_05 = core.ni_required_per_arm(
        p=core.G1_PRECISION,
        margin=0.05,
        deff=core.DEFF,
        alpha=core.ALPHA,
        power=core.POWER,
    )
    f3, f2 = core.forecast(k=3), core.forecast(k=2)
    return "\n".join(
        [
            f"sigma_run {core.SIGMA_RUN:.4f}; per-window difference SD k=3 {sd3:.4f}, k=2 {sd2:.4f}",
            f"lift TOST margin {core.LIFT_MARGIN}: windows needed k=3 {need3}, k=2 {need2}; available {core.GOLD_WINDOWS}",
            f"lift TOST power at the available windows: k=3 {p3:.3f}, k=2 {p2:.3f}",
            f"G1 NI margin {core.G1_MARGIN}: power with {g1n} objects/arm {gp:.3f}; margin 0.05 needs {g1_05} objects/arm",
            f"forecast k=3 {f3['total_s'] / 3600:.2f} h, k=2 {f2['total_s'] / 3600:.2f} h, budget {core.BUDGET_S / 3600:.1f} h",
            f"third replicate adds {core.third_replicate_cost_s() / 3600:.2f} h and runs only if it ends inside the budget",
        ]
    )


# --- self-test ---------------------------------------------------------------


def _self_test() -> int:
    """Model-free: the rules, the command shapes, the resume logic, the gate."""
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(("ok   " if cond else "FAIL ") + name)
        if not cond:
            failures.append(name)

    sd3 = core.diff_sd(core.SIGMA_RUN, 3, 3)
    need = core.required_windows(
        sd=sd3,
        margin=core.LIFT_MARGIN,
        deff=core.DEFF,
        alpha=core.ALPHA,
        power=core.POWER,
    )
    check(
        "registered sample needs 85 windows, 93 are available",
        need == 85 and need <= core.GOLD_WINDOWS,
    )
    check(
        "registered seeds are disjoint",
        not set(core.OLLAMA_RUNS) & set(core.LLAMA_RUNS),
    )
    check(
        "k=3 forecast is inside the budget",
        core.forecast(k=3)["total_s"] < core.BUDGET_S,
    )

    argv = llama_run_argv(pathlib.Path("/x"), "DA", [5, 6])
    check(
        "llama replay goes through the openai-compatible backend",
        "openai-compatible" in argv and "5" in argv,
    )
    check(
        "llama replay borrows Ollama's timeout",
        argv[argv.index("--timeout-from") + 1] == "qwen3:8b",
    )
    check(
        "llama server pins the registered flags",
        {"-np", "-fa", "--reasoning"} <= set(llama_server_argv()),
    )
    check(
        "llama server binds loopback only",
        llama_server_argv()[llama_server_argv().index("--host") + 1] == "127.0.0.1",
    )
    check(
        "score reads the alias beside the baseline",
        core.ALIAS_LABEL in score_argv(pathlib.Path("/x"), "F", 3),
    )
    check(
        "phases: k=2 stops after the first judge",
        phases(2) == ["llama-5-6", "judge-score-k2"],
    )
    check("phases: k=3 adds the third replicate", phases(3)[-1] == "judge-score-k3")

    check(
        "idle gate refuses a foreign model",
        core.foreign_models(["gemma4:26b-a4b"]) == ["gemma4:26b-a4b"],
    )
    check(
        "idle gate accepts the runner's own models",
        core.foreign_models(["qwen3:8b", "bge-m3:latest"]) == [],
    )

    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        check("fresh state is empty", load_state(d)["done"] == {})
        save_state(d, {"done": {"llama-5-6": True}, "measured_s": 7.0, "started": "t"})
        check(
            "state round-trips (resumable)",
            load_state(d)["done"] == {"llama-5-6": True},
        )
        rec = d / "records" / safe(core.LLAMA_LABEL) / "ES2015a"
        rec.mkdir(parents=True)
        for run in core.LLAMA_RUNS:
            (rec / f"r{run}.json").write_text(
                json.dumps({"run": run, "model": core.LLAMA_LABEL})
            )
        check("reindex renumbers runs 5 to 7 as 1 to 3", reindex(d, 3) == 3)
        moved = _read(d / "records" / safe(core.ALIAS_LABEL) / "ES2015a" / "r3.json")
        check(
            "reindexed record carries the new run and label",
            moved["run"] == 3 and moved["model"] == core.ALIAS_LABEL,
        )

    def pw(shift: float) -> dict[str, dict[str, list[float]]]:
        return {
            m: {str(w): [0.03 + shift] * 3 for w in range(8)} for m in "ABCDEFGHIJKLM"
        }

    def jd(sup: int, tot: int) -> dict[str, dict[int, list[dict[str, Any]]]]:
        labels = ["supported"] * sup + ["unsupported"] * (tot - sup)
        return {
            m: {r: [{"window": 0, "labels": labels}] for r in (1, 2, 3)}
            for m in "ABCDEFGHIJKLM"
        }

    same = analyze(
        per_window={"ollama": pw(0.0), "llama": pw(0.0)},
        judge={"ollama": jd(9, 10), "llama": jd(9, 10)},
        fail={"ollama": 0.01, "llama": 0.01}, k=3, complete=True, resamples=300,
    )  # fmt: skip
    check("identical outputs are EQUIVALENT", same["verdict"] == "EQUIVALENT")
    worse = analyze(
        per_window={"ollama": pw(0.0), "llama": pw(-0.05)},
        judge={"ollama": jd(9, 10), "llama": jd(9, 10)},
        fail={"ollama": 0.0, "llama": 0.0}, k=3, complete=True, resamples=300,
    )  # fmt: skip
    check("a clear lift loss is NOT EQUIVALENT", worse["verdict"] == "NOT EQUIVALENT")
    short = analyze(
        per_window={"ollama": pw(0.0), "llama": pw(0.0)},
        judge={"ollama": jd(9, 10), "llama": jd(9, 10)},
        fail={"ollama": 0.0, "llama": 0.0}, k=2, complete=False, resamples=300,
    )  # fmt: skip
    check("an incomplete design is INCONCLUSIVE", short["verdict"] == "INCONCLUSIVE")
    runaway = analyze(
        per_window={"ollama": pw(0.0), "llama": pw(0.0)},
        judge={"ollama": jd(9, 10), "llama": jd(9, 10)},
        fail={"ollama": 0.0, "llama": 0.05}, k=3, complete=True, resamples=300,
    )  # fmt: skip
    check("the failure veto is NOT EQUIVALENT", runaway["verdict"] == "NOT EQUIVALENT")
    print(f"{len(failures)} failing")
    return 1 if failures else 0


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--runs-dir", type=pathlib.Path, default=DEFAULT_RUNS_DIR)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("plan")
    s = sub.add_parser("setup")
    s.add_argument("--source", type=pathlib.Path, default=SOURCE_RUNS_DIR)
    for name in ("preflight", "run"):
        p = sub.add_parser(name)
        p.add_argument(
            "--hash", action="store_true", help="also verify the blob's sha256"
        )
    sub.add_parser("decide")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.command == "plan":
        print(plan_text())
        return 0
    if args.command == "setup":
        setup(args.runs_dir, args.source)
        return 0
    if args.command == "preflight":
        problems = preflight(args.runs_dir, check_hash=args.hash)
        for p in problems:
            print(f"REFUSED: {p}")
        print("preflight: ok" if not problems else "preflight: refused")
        return 3 if problems else 0
    if args.command == "run":
        return cmd_run(args)
    if args.command == "decide":
        return cmd_decide(args)
    raise SystemExit("choose a subcommand, or --self-test")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    raise SystemExit(main())
