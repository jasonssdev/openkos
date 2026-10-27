"""The deterministic half of the llama.cpp vs Ollama equivalence test (#1590).

Standard library only, so `tests/unit/test_equivalence_core.py` can load it
from its path and the runner's `--self-test` can exercise it without a model:

- the power computation behind the registered sample (TOST on the window
  coverage lift, non-inferiority on G1 precision);
- the two-stage cluster bootstrap (meetings, then windows within them) the
  harness already uses, and the interval and verdict rules read from it;
- the decision rule (EQUIVALENT / NOT EQUIVALENT / INCONCLUSIVE);
- the forecast and the one time-based rule that may shorten the run;
- the bookkeeping that pairs stored Ollama runs with new llama.cpp runs.

Every number a rule compares against is a named constant here, and
`PREREGISTRATION-1590.md` quotes the same values.
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable, Mapping, Sequence
from statistics import NormalDist
from typing import Any, Final

# --- the registered design ---------------------------------------------------

LLAMA_BUILD_MARKER: Final = "build 11669"
"""`llama-server --version` for 0.7.0 (commit 620d2225c), the build #1586 measured."""
BASELINE_LABEL: Final = "qwen3:8b"
LLAMA_LABEL: Final = "qwen3:8b@llama.cpp"
ALIAS_LABEL: Final = "qwen3:8b@llama.cpp-eq"
"""The llama.cpp records renumbered 1..k so the harness's `score`, which pairs
models by run number, can read them beside Ollama's (the seeds stay disjoint:
the originals are runs 5 to 7)."""

OLLAMA_RUNS: Final = (1, 2, 3)
LLAMA_RUNS: Final = (5, 6, 7)
"""Seeds are the run numbers. The two arms use DISJOINT seeds, so no sampling
stream is shared between the engines being compared."""
K_REPLICATES: Final = 3
MIN_REPLICATES: Final = 2

DA_WINDOWS: Final = 41
F_WINDOWS: Final = 69
WINDOWS: Final = DA_WINDOWS + F_WINDOWS
GOLD_WINDOWS: Final = 93
"""Windows with gold: 37 in the five development meetings (pool DA) and 56 in
the nine free-pool meetings with `summlink` (pool F)."""

# --- the registered margins and assumptions ----------------------------------

LIFT_MARGIN: Final = 0.016
"""Half of Ollama's own mean window lift (0.0319 over 37 windows, #1586)."""
G1_MARGIN: Final = 0.10
G1_REPORTED_MARGINS: Final = (0.08, 0.05)
ALPHA: Final = 0.05
POWER: Final = 0.8
DEFF: Final = 1.5
"""Design effect for windows nested in meetings. #1586's two-stage bootstrap
interval was 1.085x wider than the naive one (variance ratio 1.18); 1.5 is the
registered allowance above that."""
NOISE_SD_DIFF: Final = 0.07128
"""SD, over the 37 windows with gold, of the difference in window lift between
two Ollama runs of `qwen3:8b` on the same windows (runs 1 and 2, #1586's
stored data). The measured run-to-run noise: the floor of the margin."""
SIGMA_RUN: Final = NOISE_SD_DIFF / math.sqrt(2)
"""One run's per-window SD, assuming independent run noise of equal size."""
G1_PRECISION: Final = 0.92
"""Ollama's G1 precision (38 of 41 supported; 0.927), rounded down."""
FAILURE_MARGIN: Final = 0.02
"""The failure veto's margin, #1557's `CONCEPT_FAILURE_MARGIN`."""

BOOTSTRAP_RESAMPLES: Final = 10_000
BOOTSTRAP_SEED: Final = 42

# --- the forecast, from #1586's measured timings ------------------------------

OLLAMA_WINDOW_RUN_S: Final = 35.6
"""2,920 s over 82 window-runs (#1586's `qwen3:8b` DA replay on Ollama)."""
LLAMA_WINDOW_RUN_S: Final = 33.2
"""2,725 s over the same 82 window-runs on llama.cpp."""
JUDGE_ENTRIES_PER_WINDOW_RUN: Final = 0.45
JUDGE_CALL_S: Final = 6.5
SCORING_S: Final = 900.0
CONTINGENCY: Final = 0.10
BUDGET_S: Final = 5.0 * 3600

RUNNER_MODELS: Final = frozenset(
    {"qwen3:8b", "bge-m3", "bge-m3:latest", "gpt-oss:20b", "gpt-oss"}
)
"""The only models this runner ever loads in Ollama. Anything else loaded is
somebody else's job, and the runner refuses to start beside it."""


# --- power -------------------------------------------------------------------


def _z(p: float) -> float:
    return NormalDist().inv_cdf(p)


def _phi(x: float) -> float:
    return NormalDist().cdf(x)


def diff_sd(sigma_run: float, k_a: int, k_b: int) -> float:
    """SD of one window's difference between arm means of `k_a` and `k_b` runs."""
    return sigma_run * math.sqrt(1 / k_a + 1 / k_b)


def required_windows(
    *, sd: float, margin: float, deff: float, alpha: float, power: float
) -> int:
    """Windows for a TOST to reach `power` when the true difference is zero."""
    ratio = _z(1 - alpha) + _z((1 + power) / 2)
    return math.ceil(deff * (sd * ratio / margin) ** 2)


def tost_power(
    *,
    sd: float,
    n: int,
    margin: float,
    true_diff: float,
    deff: float,
    alpha: float = ALPHA,
) -> float:
    """Normal-approximation power of the two one-sided tests at `true_diff`."""
    se = sd * math.sqrt(deff / n)
    z = _z(1 - alpha)
    upper = _phi((margin - true_diff) / se - z)
    lower = _phi((margin + true_diff) / se - z)
    return max(0.0, upper + lower - 1)


def ni_power(
    *,
    p: float,
    n_per_arm: int,
    margin: float,
    true_diff: float,
    deff: float,
    alpha: float = ALPHA,
) -> float:
    """Power of the one-sided non-inferiority test on a precision difference."""
    se = math.sqrt(2 * p * (1 - p) / n_per_arm * deff)
    return _phi((true_diff + margin) / se - _z(1 - alpha))


def ni_required_per_arm(
    *, p: float, margin: float, deff: float, alpha: float, power: float
) -> int:
    ratio = _z(1 - alpha) + _z(power)
    return math.ceil(2 * p * (1 - p) * deff * (ratio / margin) ** 2)


# --- bootstrap and intervals -------------------------------------------------


def _two_stage[T](
    clusters: Mapping[str, Sequence[T]],
    statistic: Callable[[list[T]], float | None],
    resamples: int,
    seed: int,
) -> list[float]:
    rng = random.Random(seed)  # noqa: S311 -- a seeded resampler, not a secret
    names = sorted(clusters)
    out: list[float] = []
    for _ in range(resamples):
        pooled: list[T] = []
        for name in (rng.choice(names) for _ in names):
            items = clusters[name]
            pooled.extend(rng.choice(items) for _ in items)
        value = statistic(pooled)
        if value is not None:
            out.append(value)
    return out


def _mean(items: list[float]) -> float | None:
    return sum(items) / len(items) if items else None


def bootstrap_means(
    clusters: Mapping[str, Sequence[float]], *, resamples: int, seed: int
) -> list[float]:
    """Two-stage bootstrap of the pooled mean (meetings, then windows)."""
    return _two_stage(clusters, _mean, resamples, seed)


def pooled_precision_delta(
    clusters: Mapping[str, Sequence[tuple[int, int, int, int]]],
) -> float | None:
    """`(sc, tc, sb, tb)` per window: candidate minus baseline pooled precision."""
    return _precision([w for ws in clusters.values() for w in ws])


def _precision(items: list[tuple[int, int, int, int]]) -> float | None:
    sc = sum(i[0] for i in items)
    tc = sum(i[1] for i in items)
    sb = sum(i[2] for i in items)
    tb = sum(i[3] for i in items)
    if tc == 0 or tb == 0:
        return None
    return sc / tc - sb / tb


def bootstrap_precision(
    clusters: Mapping[str, Sequence[tuple[int, int, int, int]]],
    resamples: int,
    seed: int,
) -> list[float]:
    """Two-stage bootstrap of the pooled precision delta; a resample with no
    judged object in either arm has no delta and is skipped."""
    return _two_stage(clusters, _precision, resamples, seed)


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def interval(
    values: Sequence[float], *, level: float, two_sided: bool = True
) -> tuple[float, float]:
    """Percentile bounds. Two-sided: the middle `level`. One-sided: each bound
    is the `level` bound on its own side (so 0.95 is a 95% one-sided bound)."""
    if not values:
        raise ValueError("no bootstrap replicates")
    if two_sided:
        tail = (1 - level) / 2
        return _quantile(values, tail), _quantile(values, 1 - tail)
    return _quantile(values, 1 - level), _quantile(values, level)


# --- verdicts ----------------------------------------------------------------


def lift_verdict(lo: float, hi: float, *, margin: float) -> str:
    """TOST on the window lift from its 90% interval: inside the margin on both
    sides is equivalent; wholly outside it is a demonstrated difference."""
    if lo > -margin and hi < margin:
        return "equivalent"
    if hi < -margin or lo > margin:
        return "different"
    return "inconclusive"


def g1_verdict(lo: float, hi: float, *, margin: float) -> str:
    """Non-inferiority on the precision delta from its one-sided 95% bounds."""
    if lo > -margin:
        return "noninferior"
    if hi < -margin:
        return "inferior"
    return "inconclusive"


def failure_veto(*, llama: float, ollama: float) -> bool:
    """llama.cpp's window failure rate above Ollama's by more than the margin."""
    return llama > ollama + FAILURE_MARGIN + 1e-12


def decide(*, lift: str, g1: str, veto: bool, complete: bool) -> dict[str, Any]:
    reasons: list[str] = []
    if lift == "different":
        reasons.append("window lift: the 90% interval lies outside the margin")
    if g1 == "inferior":
        reasons.append("G1 precision: the loss is demonstrated beyond the margin")
    if veto:
        reasons.append("failure rate: llama.cpp above Ollama by more than the margin")
    if reasons:
        return {"verdict": "NOT EQUIVALENT", "reasons": reasons}
    if not complete:
        return {
            "verdict": "INCONCLUSIVE",
            "reasons": ["the registered design did not complete"],
        }
    if lift == "equivalent" and g1 == "noninferior":
        return {
            "verdict": "EQUIVALENT",
            "reasons": ["TOST on lift and non-inferiority on G1 both hold"],
        }
    if lift != "equivalent":
        reasons.append("window lift: the 90% interval is not inside the margin")
    if g1 != "noninferior":
        reasons.append("G1 precision: the lower bound is not above -margin")
    return {"verdict": "INCONCLUSIVE", "reasons": reasons}


# --- pairing -----------------------------------------------------------------


def window_diffs(
    ollama: Mapping[str, Mapping[str, Sequence[float]]],
    llama: Mapping[str, Mapping[str, Sequence[float]]],
    *,
    runs: int,
) -> dict[str, list[float]]:
    """Per window, llama.cpp's mean lift over the first `runs` runs minus
    Ollama's; windows and meetings present in both arms only."""
    out: dict[str, list[float]] = {}
    for meeting, windows in ollama.items():
        other = llama.get(meeting)
        if other is None:
            continue
        diffs: list[float] = []
        for window in sorted(windows, key=int):
            if window not in other:
                continue
            a = list(windows[window])[:runs]
            b = list(other[window])[:runs]
            diffs.append(sum(b) / len(b) - sum(a) / len(a))
        if diffs:
            out[meeting] = diffs
    return out


def g1_clusters(
    ollama: Mapping[str, Mapping[int, Sequence[Mapping[str, Any]]]],
    llama: Mapping[str, Mapping[int, Sequence[Mapping[str, Any]]]],
    *,
    runs: int,
) -> dict[str, list[tuple[int, int, int, int]]]:
    """`(sc, tc, sb, tb)` per window, pooled over the first `runs` runs.

    `ollama[meeting][run]` is that run's judged items (`window`, `labels`); a
    `labels` of `None` is an unparsed reply and counts nothing."""

    def tally(
        side: Mapping[str, Mapping[int, Sequence[Mapping[str, Any]]]],
        meeting: str,
    ) -> dict[int, tuple[int, int]]:
        counts: dict[int, tuple[int, int]] = {}
        for run in range(1, runs + 1):
            for item in side.get(meeting, {}).get(run, []):
                labels = item.get("labels")
                if labels is None:
                    continue
                s, t = counts.get(int(item["window"]), (0, 0))
                counts[int(item["window"])] = (
                    s + sum(1 for lab in labels if lab == "supported"),
                    t + len(labels),
                )
        return counts

    out: dict[str, list[tuple[int, int, int, int]]] = {}
    for meeting in sorted(set(ollama) | set(llama)):
        base = tally(ollama, meeting)
        cand = tally(llama, meeting)
        rows = [
            (*cand.get(w, (0, 0)), *base.get(w, (0, 0)))
            for w in sorted(set(base) | set(cand))
        ]
        if rows:
            out[meeting] = rows
    return out


def common_replicates(ollama_done: Sequence[int], llama_done: Sequence[int]) -> int:
    """The largest k whose first k registered runs are complete in BOTH arms."""
    k = 0
    for o, ll in zip(OLLAMA_RUNS, LLAMA_RUNS, strict=True):
        if o in ollama_done and ll in llama_done:
            k += 1
        else:
            break
    return k


def reindex_record(
    record: Mapping[str, Any], *, label: str, new_run: int
) -> dict[str, Any]:
    moved = dict(record)
    moved["run"] = new_run
    moved["model"] = label
    return moved


def prompt_drift(
    stored: Sequence[Mapping[str, str]], current: Mapping[str, str]
) -> list[str]:
    """Ids of stored prompts whose hash is not the current one."""
    return [p["id"] for p in stored if current.get(p["id"]) != p["sha256_16"]]


# --- forecast ----------------------------------------------------------------


def forecast(*, k: int) -> dict[str, float]:
    """Seconds of measured model time for `k` replicates per arm.

    Ollama's runs 1 and 2 on pool DA and runs 1 to 4 on pool F are stored; a
    third DA run is the only new Ollama chat work. Every llama.cpp window-run
    is new."""
    llama_runs = WINDOWS * k * LLAMA_WINDOW_RUN_S
    ollama_new = DA_WINDOWS * max(0, k - 2) * OLLAMA_WINDOW_RUN_S
    judged_runs = WINDOWS * k + DA_WINDOWS * max(0, k - 2) + F_WINDOWS * min(k, 4)
    judge = judged_runs * JUDGE_ENTRIES_PER_WINDOW_RUN * JUDGE_CALL_S
    total = (llama_runs + ollama_new + judge + SCORING_S) * (1 + CONTINGENCY)
    return {
        "llama_runs_s": llama_runs,
        "ollama_new_s": ollama_new,
        "judge_s": judge,
        "scoring_s": SCORING_S,
        "total_s": total,
    }


def third_replicate_cost_s() -> float:
    return forecast(k=3)["total_s"] - forecast(k=2)["total_s"]


def third_replicate_allowed(*, elapsed_s: float) -> bool:
    """The only rule that can shorten the run: the third replicate starts only
    if it still ends inside the budget."""
    return elapsed_s + third_replicate_cost_s() <= BUDGET_S


# --- the idle gate -----------------------------------------------------------

_PS_ROW: Final = re.compile(r"^(\S+)\s+\S+\s+")


def loaded_models(ps_text: str) -> list[str]:
    """Model names in `ollama ps` output (the header row is skipped)."""
    names: list[str] = []
    for line in ps_text.splitlines()[1:]:
        match = _PS_ROW.match(line)
        if match:
            names.append(match.group(1))
    return names


def foreign_models(loaded: Sequence[str]) -> list[str]:
    """Loaded models this runner does not own."""
    return [m for m in loaded if m not in RUNNER_MODELS]
