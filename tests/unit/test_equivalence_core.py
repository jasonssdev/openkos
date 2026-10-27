"""`evals/runtime_equivalence/equivalence_core.py`: the deterministic half of the
llama.cpp vs Ollama equivalence test (#1590) -- the power computation, the
cluster bootstrap, the TOST and non-inferiority verdicts, the decision rule,
the forecast and the run bookkeeping.

A model cannot exercise any of these, and a measured run rests on every one.
The module is standard library only and is loaded straight from its path, like
`runtime_core` in `test_runtime_screen_core.py`.
"""

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "runtime_equivalence" / "equivalence_core.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("equivalence_core", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load()


# --- power ------------------------------------------------------------------


def test_diff_sd_shrinks_with_replicates() -> None:
    assert core.diff_sd(0.05, 1, 1) == pytest.approx(0.05 * math.sqrt(2))
    assert core.diff_sd(0.05, 3, 3) == pytest.approx(0.05 * math.sqrt(2 / 3))
    assert core.diff_sd(0.05, 3, 3) < core.diff_sd(0.05, 2, 2)


def test_required_windows_matches_the_registered_design() -> None:
    sd = core.diff_sd(core.SIGMA_RUN, core.K_REPLICATES, core.K_REPLICATES)
    needed = core.required_windows(
        sd=sd, margin=core.LIFT_MARGIN, deff=core.DEFF, alpha=0.05, power=0.8
    )
    assert needed == 85
    assert needed <= core.GOLD_WINDOWS


def test_required_windows_grows_as_the_margin_shrinks() -> None:
    sd = 0.04
    wide = core.required_windows(sd=sd, margin=0.02, deff=1.0, alpha=0.05, power=0.8)
    narrow = core.required_windows(sd=sd, margin=0.01, deff=1.0, alpha=0.05, power=0.8)
    assert narrow == pytest.approx(4 * wide, rel=0.05)


def test_tost_power_at_the_registered_design_clears_point_eight() -> None:
    sd = core.diff_sd(core.SIGMA_RUN, 3, 3)
    p = core.tost_power(
        sd=sd, n=core.GOLD_WINDOWS, margin=core.LIFT_MARGIN, true_diff=0.0, deff=1.5
    )
    assert 0.80 <= p <= 0.90


def test_tost_power_falls_with_a_true_difference_and_with_fewer_replicates() -> None:
    sd3 = core.diff_sd(core.SIGMA_RUN, 3, 3)
    sd2 = core.diff_sd(core.SIGMA_RUN, 2, 2)
    kw = {"n": 93, "margin": 0.016, "deff": 1.5}
    at0 = core.tost_power(sd=sd3, true_diff=0.0, **kw)
    at4 = core.tost_power(sd=sd3, true_diff=0.004, **kw)
    k2 = core.tost_power(sd=sd2, true_diff=0.0, **kw)
    assert at4 < at0
    assert k2 < at0
    assert core.tost_power(sd=sd3, true_diff=0.016, **kw) < 0.1


def test_noninferiority_power_and_required_objects() -> None:
    p = core.ni_power(
        p=0.92, n_per_arm=200, margin=0.10, true_diff=0.0, deff=1.5, alpha=0.05
    )
    assert 0.88 <= p <= 0.95
    harder = core.ni_power(
        p=0.92, n_per_arm=200, margin=0.05, true_diff=0.0, deff=1.5, alpha=0.05
    )
    assert harder < 0.5
    needed = core.ni_required_per_arm(
        p=0.92, margin=0.05, deff=1.5, alpha=0.05, power=0.8
    )
    assert needed > 400


# --- bootstrap and intervals ------------------------------------------------


def _clusters() -> dict[str, list[float]]:
    return {
        "A": [0.01, 0.02, 0.03],
        "B": [-0.01, 0.0],
        "C": [0.02, 0.02, 0.04, 0.0],
    }


def test_bootstrap_is_deterministic_for_a_seed() -> None:
    a = core.bootstrap_means(_clusters(), resamples=200, seed=42)
    b = core.bootstrap_means(_clusters(), resamples=200, seed=42)
    c = core.bootstrap_means(_clusters(), resamples=200, seed=43)
    assert a == b
    assert a != c
    assert len(a) == 200


def test_bootstrap_of_a_constant_has_no_width() -> None:
    reps = core.bootstrap_means({"A": [0.5, 0.5], "B": [0.5]}, resamples=100, seed=1)
    assert set(reps) == {0.5}


def test_interval_takes_percentiles() -> None:
    values = [float(i) for i in range(101)]
    lo, hi = core.interval(values, level=0.90)
    assert lo == pytest.approx(5.0)
    assert hi == pytest.approx(95.0)
    lo1, hi1 = core.interval(values, level=0.90, two_sided=False)
    assert lo1 == pytest.approx(10.0)
    assert hi1 == pytest.approx(90.0)


def test_pooled_precision_delta() -> None:
    clusters = {"A": [(9, 10, 8, 10)], "B": [(1, 2, 2, 2)]}
    # candidate (9+1)/(10+2) minus baseline (8+2)/(10+2)
    assert core.pooled_precision_delta(clusters) == pytest.approx(0.0)
    assert core.pooled_precision_delta({"A": [(0, 0, 1, 1)]}) is None


def test_precision_bootstrap_skips_resamples_without_a_denominator() -> None:
    reps = core.bootstrap_precision({"A": [(1, 1, 1, 1)], "B": [(0, 0, 0, 0)]}, 50, 7)
    assert reps
    assert all(r == 0.0 for r in reps)


# --- verdicts ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("lo", "hi", "expected"),
    [
        (-0.010, 0.012, "equivalent"),
        (-0.016, 0.012, "inconclusive"),  # the bound sits ON the margin: not inside
        (-0.020, 0.010, "inconclusive"),
        (-0.030, -0.017, "different"),
        (0.017, 0.040, "different"),
        (-0.030, 0.030, "inconclusive"),
    ],
)
def test_lift_verdict(lo: float, hi: float, expected: str) -> None:
    assert core.lift_verdict(lo, hi, margin=0.016) == expected


@pytest.mark.parametrize(
    ("lo", "hi", "expected"),
    [
        (-0.05, 0.08, "noninferior"),
        (-0.10, 0.08, "inconclusive"),
        (-0.30, -0.11, "inferior"),
        (-0.30, 0.05, "inconclusive"),
    ],
)
def test_g1_verdict(lo: float, hi: float, expected: str) -> None:
    assert core.g1_verdict(lo, hi, margin=0.10) == expected


def test_failure_veto_is_a_point_rule() -> None:
    assert not core.failure_veto(llama=0.03, ollama=0.01)
    assert core.failure_veto(llama=0.031, ollama=0.01)
    assert not core.failure_veto(llama=0.0, ollama=0.05)


def _decide(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "lift": "equivalent",
        "g1": "noninferior",
        "veto": False,
        "complete": True,
    }
    base.update(over)
    result: dict[str, Any] = core.decide(**base)
    return result


def test_decide_equivalent_needs_everything() -> None:
    assert _decide()["verdict"] == "EQUIVALENT"
    assert _decide(lift="inconclusive")["verdict"] == "INCONCLUSIVE"
    assert _decide(g1="inconclusive")["verdict"] == "INCONCLUSIVE"
    assert _decide(complete=False)["verdict"] == "INCONCLUSIVE"


def test_decide_not_equivalent_on_a_demonstrated_difference_or_veto() -> None:
    assert _decide(lift="different")["verdict"] == "NOT EQUIVALENT"
    assert _decide(g1="inferior")["verdict"] == "NOT EQUIVALENT"
    assert _decide(veto=True)["verdict"] == "NOT EQUIVALENT"


def test_decide_a_demonstrated_difference_outranks_incompleteness() -> None:
    assert _decide(complete=False, g1="inferior")["verdict"] == "NOT EQUIVALENT"


def test_decide_names_its_reasons() -> None:
    reasons = _decide(lift="different", veto=True)["reasons"]
    assert any("lift" in r for r in reasons)
    assert any("failure" in r for r in reasons)


# --- window pairing ---------------------------------------------------------


def test_window_diffs_pair_by_window_and_average_the_runs() -> None:
    ollama = {"M1": {"0": [0.10, 0.20, 0.30], "1": [0.0, 0.0, 0.0]}}
    llama = {
        "M1": {"0": [0.40, 0.40, 0.40], "1": [0.1, 0.1, 0.1]},
        "M2": {"0": [1, 1, 1]},
    }
    out = core.window_diffs(ollama, llama, runs=3)
    assert out == {"M1": [pytest.approx(0.2), pytest.approx(0.1)]}


def test_window_diffs_use_only_the_first_k_runs() -> None:
    ollama = {"M": {"0": [0.0, 0.0, 9.0]}}
    llama = {"M": {"0": [0.2, 0.4, 9.0]}}
    assert core.window_diffs(ollama, llama, runs=2) == {"M": [pytest.approx(0.3)]}


def test_g1_clusters_pool_runs_per_window() -> None:
    judge = {
        "ollama": {"M": {1: [{"window": 0, "labels": ["supported", "partial"]}]}},
        "llama": {"M": {1: [{"window": 0, "labels": ["supported"]}]}},
    }
    got = core.g1_clusters(judge["ollama"], judge["llama"], runs=1)
    assert got == {"M": [(1, 1, 1, 2)]}


def test_g1_clusters_drop_unparsed_labels() -> None:
    ollama = {"M": {1: [{"window": 0, "labels": None}]}}
    llama = {"M": {1: [{"window": 0, "labels": ["unsupported"]}]}}
    assert core.g1_clusters(ollama, llama, runs=1) == {"M": [(0, 1, 0, 0)]}


# --- run bookkeeping --------------------------------------------------------


def test_common_replicates_counts_the_complete_prefix() -> None:
    assert core.common_replicates([1, 2, 3], [5, 6, 7]) == 3
    assert core.common_replicates([1, 2, 3], [5, 6]) == 2
    assert core.common_replicates([1, 2], [5, 6, 7]) == 2
    assert core.common_replicates([1, 3], [5, 6, 7]) == 1
    assert core.common_replicates([], [5]) == 0


def test_alias_runs_are_renumbered_from_one() -> None:
    record = {"run": 6, "model": "qwen3:8b@llama.cpp", "source": "ES2015a"}
    moved = core.reindex_record(record, label="qwen3:8b@llama.cpp-eq", new_run=2)
    assert moved["run"] == 2
    assert moved["model"] == "qwen3:8b@llama.cpp-eq"
    assert record["run"] == 6  # the original is untouched


def test_prompt_drift_names_the_changed_prompt() -> None:
    stored = [{"id": "extraction/system", "sha256_16": "aaaa"}]
    assert core.prompt_drift(stored, {"extraction/system": "aaaa"}) == []
    assert core.prompt_drift(stored, {"extraction/system": "bbbb"}) == [
        "extraction/system"
    ]
    assert core.prompt_drift(stored, {}) == ["extraction/system"]


# --- forecast ---------------------------------------------------------------


def test_forecast_totals_the_registered_phases() -> None:
    f = core.forecast(k=3)
    assert f["llama_runs_s"] == pytest.approx(330 * core.LLAMA_WINDOW_RUN_S)
    assert f["ollama_new_s"] == pytest.approx(41 * core.OLLAMA_WINDOW_RUN_S)
    assert 4.0 * 3600 < f["total_s"] < core.BUDGET_S


def test_forecast_for_two_replicates_is_shorter() -> None:
    assert core.forecast(k=2)["total_s"] < core.forecast(k=3)["total_s"]


def test_third_replicate_runs_only_when_it_fits_the_budget() -> None:
    need = core.third_replicate_cost_s()
    assert core.third_replicate_allowed(elapsed_s=core.BUDGET_S - need - 1)
    assert not core.third_replicate_allowed(elapsed_s=core.BUDGET_S - need + 1)


# --- idle gate --------------------------------------------------------------


def test_loaded_models_parses_ollama_ps() -> None:
    text = (
        "NAME ID SIZE PROCESSOR CONTEXT UNTIL\n"
        "gemma4:26b-a4b 001e5dafc3c7 865 MB 100% GPU 8192 4 minutes from now\n"
        "bge-m3:latest 790764642607 1.2 GB 100% GPU 8192 2 minutes from now\n"
    )
    assert core.loaded_models(text) == ["gemma4:26b-a4b", "bge-m3:latest"]
    assert core.loaded_models("NAME ID SIZE PROCESSOR CONTEXT UNTIL\n") == []
    assert core.loaded_models("") == []


def test_foreign_models_are_those_the_runner_does_not_own() -> None:
    owned = core.RUNNER_MODELS
    assert core.foreign_models(["qwen3:8b", "gpt-oss:20b", "bge-m3:latest"]) == []
    assert core.foreign_models(["qwen3:8b", "gemma4:26b-a4b"]) == ["gemma4:26b-a4b"]
    assert "qwen3:8b" in owned
