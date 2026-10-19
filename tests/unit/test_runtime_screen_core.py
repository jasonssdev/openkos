"""`evals/runtime_screen/runtime_core.py`: the deterministic half of the local
runtime screen (#1586) -- the streaming rate arithmetic, the memory parsers,
the stage-1 performance gate, the stage-2 equivalence rules and the
shortening ladder.

These are the parts a measured run rests on and a model cannot exercise. The
module is standard library only and is loaded straight from its path, like
`ask_screen_core` in `test_ask_screen_core.py`, so the suite gains no import
root.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "runtime_screen" / "runtime_core.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runtime_core", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load()


# --- streaming --------------------------------------------------------------


def test_iter_sse_yields_data_payloads_and_stops_at_done() -> None:
    lines = [
        b": keep-alive\n",
        b'data: {"a": 1}\n',
        b"\n",
        b'data: {"a": 2}\n',
        b"data: [DONE]\n",
        b'data: {"a": 3}\n',
    ]
    assert list(core.iter_sse(lines)) == [{"a": 1}, {"a": 2}]


def test_iter_sse_skips_a_line_that_is_not_json() -> None:
    assert list(core.iter_sse([b"data: {broken\n", b'data: {"ok": true}\n'])) == [
        {"ok": True}
    ]


def test_stream_rates_split_prefill_from_decode_at_the_first_token() -> None:
    rates = core.stream_rates(
        started=10.0,
        first_token=12.0,
        finished=17.0,
        prompt_tokens=10_000,
        completion_tokens=501,
    )
    assert rates["ttft_s"] == pytest.approx(2.0)
    assert rates["prefill_tps"] == pytest.approx(5_000.0)
    # The first token closes the prefill, so 500 tokens are decoded in 5 s.
    assert rates["decode_tps"] == pytest.approx(100.0)


def test_stream_rates_report_no_decode_rate_for_a_single_token() -> None:
    rates = core.stream_rates(
        started=0.0,
        first_token=1.0,
        finished=1.0,
        prompt_tokens=10,
        completion_tokens=1,
    )
    assert rates["decode_tps"] is None


def test_stream_rates_refuse_a_first_token_before_the_start() -> None:
    with pytest.raises(ValueError, match="first token"):
        core.stream_rates(
            started=5.0,
            first_token=4.0,
            finished=6.0,
            prompt_tokens=10,
            completion_tokens=5,
        )


def test_ollama_rates_read_the_server_durations_in_nanoseconds() -> None:
    final = {
        "prompt_eval_count": 9_000,
        "prompt_eval_duration": 3_000_000_000,
        "eval_count": 400,
        "eval_duration": 8_000_000_000,
        "load_duration": 1_000_000,
    }
    assert core.ollama_rates(final) == {
        "prompt_tokens": 9_000,
        "completion_tokens": 400,
        "prefill_tps": pytest.approx(3_000.0),
        "decode_tps": pytest.approx(50.0),
    }


def test_llamacpp_rates_read_the_timings_block() -> None:
    timings = {
        "prompt_n": 9_000,
        "prompt_per_second": 2_500.0,
        "predicted_n": 300,
        "predicted_per_second": 60.0,
    }
    assert core.llamacpp_rates({"timings": timings}) == {
        "prompt_tokens": 9_000,
        "completion_tokens": 300,
        "prefill_tps": 2_500.0,
        "decode_tps": 60.0,
    }
    assert core.llamacpp_rates({}) is None


# --- memory -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("0K", 0),
        ("2976K", 2976 * 1024),
        ("33.2M", int(33.2 * 1024**2)),
        ("1.5G", int(1.5 * 1024**3)),
        ("512", 512),
    ],
)
def test_parse_size(token: str, expected: int) -> None:
    assert core.parse_size(token) == expected


_VMMAP = """\
Physical footprint:         5.9G
Physical footprint (peak):  6.1G

==========
REGION TYPE                        SIZE     SIZE     SIZE     SIZE     SIZE     SIZE     SIZE    COUNT (non-coalesced)
===========                     ======= ========    =====  ======= ========   ======    =====  =======
IOAccelerator                      1.0G     1.0G     1.0G       0K       0K       0K       0K        3
mapped file                        5.0G     4.8G       0K       0K       0K       0K       0K        2
===========                     ======= ========    =====  ======= ========   ======    =====  =======
TOTAL                              7.0G     6.5G     1.2G       0K       0K      16K       0K      356

                                          VIRTUAL   RESIDENT      DIRTY    SWAPPED ALLOCATION      BYTES DIRTY+SWAP          REGION
MALLOC ZONE                                  SIZE       SIZE       SIZE       SIZE      COUNT  ALLOCATED  FRAG SIZE  % FRAG   COUNT
===========                               =======  =========  =========  =========  =========  =========  =========  ======  ======
TOTAL                                       33.2M      1904K      1904K         0K       4090       757K      1147K     61%      12
"""


def test_parse_vmmap_reads_the_region_total_not_the_malloc_total() -> None:
    parsed = core.parse_vmmap(_VMMAP)
    assert parsed["resident_bytes"] == int(6.5 * 1024**3)
    assert parsed["footprint_bytes"] == int(5.9 * 1024**3)
    assert parsed["footprint_peak_bytes"] == int(6.1 * 1024**3)


def test_parse_vmmap_refuses_output_without_a_region_total() -> None:
    with pytest.raises(ValueError, match="TOTAL"):
        core.parse_vmmap("Physical footprint: 1G\n")


_LOADER_LOG = """\
load_tensors:   CPU_Mapped model buffer size =   100.00 MiB
llama_kv_cache:       MTL0 KV buffer size =   999.00 MiB
something unrelated
load_tensors:   CPU_Mapped model buffer size =   333.84 MiB
load_tensors:  MTL0_Mapped model buffer size =  4977.62 MiB
llama_context:        CPU  output buffer size =     0.58 MiB
llama_kv_cache:       MTL0 KV buffer size =   918.00 MiB
sched_reserve:       MTL0 compute buffer size =   147.82 MiB
sched_reserve:        CPU compute buffer size =    51.29 MiB
sched_reserve:       MTL0 compute buffer size =   216.10 MiB
sched_reserve:        CPU compute buffer size =    56.10 MiB
"""


def test_loader_report_sums_the_last_load_with_the_largest_compute_per_device() -> None:
    # ADR-0047's method: weights + KV + output + the LARGEST compute
    # reservation per device, for the most recent load only.
    expected = 333.84 + 4977.62 + 0.58 + 918.00 + 216.10 + 56.10
    assert core.loader_report_mib(_LOADER_LOG) == pytest.approx(expected)


def test_loader_report_is_none_without_a_load() -> None:
    assert core.loader_report_mib("nothing here\n") is None


# --- prompt and identity ----------------------------------------------------


def test_filler_text_is_deterministic_and_salted_by_the_nonce() -> None:
    a = core.filler_text(500, nonce="r1")
    assert a == core.filler_text(500, nonce="r1")
    assert a != core.filler_text(500, nonce="r2")
    assert len(a.split()) >= 500


def test_weights_digest_is_order_free_and_content_bound() -> None:
    one = core.weights_digest([("b.safetensors", "22"), ("a.safetensors", "11")])
    assert one == core.weights_digest(
        [("a.safetensors", "11"), ("b.safetensors", "22")]
    )
    assert one != core.weights_digest(
        [("a.safetensors", "11"), ("b.safetensors", "23")]
    )
    assert one.startswith("sha256:")


# --- stage 1 ----------------------------------------------------------------


def _perf(**over: Any) -> Any:
    base = {
        "prefill_tps": 1000.0,
        "decode_tps": 50.0,
        "resident_gib": 7.0,
        "load_s": 5.0,
    }
    base.update(over)
    return core.Perf(**base)


def test_stage1_passes_a_material_gain_without_a_regression() -> None:
    verdict = core.stage1_verdict(_perf(), _perf(decode_tps=58.0))
    assert verdict["passes"] is True
    assert verdict["gains"] == ["decode 1.16x"]
    assert verdict["regressions"] == []


def test_stage1_fails_a_gain_paid_for_with_memory() -> None:
    verdict = core.stage1_verdict(_perf(), _perf(decode_tps=80.0, resident_gib=7.8))
    assert verdict["passes"] is False
    assert verdict["regressions"] == ["memory 1.11x"]


def test_stage1_fails_a_runtime_that_is_only_equal() -> None:
    verdict = core.stage1_verdict(_perf(), _perf(decode_tps=55.0, prefill_tps=1100.0))
    assert verdict["passes"] is False
    assert verdict["gains"] == []


def test_stage1_counts_less_memory_as_a_gain() -> None:
    verdict = core.stage1_verdict(_perf(), _perf(resident_gib=5.9))
    assert verdict["gains"] == ["memory 0.84x"]
    assert verdict["passes"] is True


def test_stage1_load_regression_needs_both_the_ratio_and_the_slack() -> None:
    # 2.4x but only +7 s: within the absolute slack, so not a regression.
    assert (
        core.stage1_verdict(_perf(), _perf(load_s=12.0, decode_tps=60.0))["regressions"]
        == []
    )
    verdict = core.stage1_verdict(_perf(), _perf(load_s=16.0, decode_tps=60.0))
    assert verdict["regressions"] == ["load 3.20x"]


def test_stage1_counts_a_cheaper_swap_as_a_gain() -> None:
    base_swap = core.Swap(swap_s=20.0, both_resident=False, both_gib=None)
    cand_swap = core.Swap(swap_s=9.0, both_resident=False, both_gib=None)
    verdict = core.stage1_verdict(_perf(), _perf(), base_swap, cand_swap)
    assert verdict["gains"] == ["swap 0.45x"]


def test_stage1_counts_co_residence_within_budget_as_a_gain() -> None:
    base_swap = core.Swap(swap_s=20.0, both_resident=False, both_gib=None)
    cand_swap = core.Swap(swap_s=19.0, both_resident=True, both_gib=23.0)
    verdict = core.stage1_verdict(_perf(), _perf(), base_swap, cand_swap)
    assert verdict["gains"] == ["both resident in 23.66 GiB"]


def test_stage1_co_residence_over_budget_is_not_a_gain() -> None:
    base_swap = core.Swap(swap_s=20.0, both_resident=False, both_gib=None)
    cand_swap = core.Swap(swap_s=19.0, both_resident=True, both_gib=23.5)
    verdict = core.stage1_verdict(_perf(), _perf(), base_swap, cand_swap)
    assert verdict["gains"] == []


def test_stage1_refuses_a_missing_measurement() -> None:
    with pytest.raises(ValueError, match="decode_tps"):
        core.stage1_verdict(_perf(), _perf(decode_tps=None))


# --- stage 2 ----------------------------------------------------------------


def _ask(lo: float, hi: float, vetoes: list[str]) -> dict[str, Any]:
    return {
        "roles": {
            "sufficiency": {
                "candidates": {
                    "qwen3:8b@llama.cpp": {
                        "delta": {"n": 96, "mean": (lo + hi) / 2, "lo": lo, "hi": hi},
                        "vetoes": vetoes,
                    }
                }
            }
        }
    }


def test_ask_equivalence_passes_no_clear_loss() -> None:
    verdict = core.ask_equivalence(_ask(-0.05, 0.03, []), "qwen3:8b@llama.cpp")
    assert verdict == {"passes": True, "reasons": [], "reported": []}


def test_ask_equivalence_fails_a_clear_loss() -> None:
    verdict = core.ask_equivalence(_ask(-0.12, -0.01, []), "qwen3:8b@llama.cpp")
    assert verdict["passes"] is False
    assert verdict["reasons"] == ["sufficiency delta upper bound -0.010 < 0"]


def test_ask_equivalence_gates_on_registered_margins_and_only_reports_s1() -> None:
    verdict = core.ask_equivalence(
        _ask(-0.02, 0.04, ["S1 grounded refusals above baseline", "S2 adjacent"]),
        "qwen3:8b@llama.cpp",
    )
    assert verdict["passes"] is False
    assert verdict["reasons"] == ["S2 adjacent"]
    assert verdict["reported"] == ["S1 grounded refusals above baseline"]


def test_ask_equivalence_refuses_a_missing_candidate() -> None:
    with pytest.raises(KeyError):
        core.ask_equivalence(_ask(0.0, 0.0, []), "qwen3:8b@mlx")


def _concept(**over: Any) -> dict[str, Any]:
    base = {
        "failures": {"num": 1, "den": 82},
        "objects_per_meeting": 5.0,
        "verbatim": {"num": 0, "den": 50},
        "prose": {"wrong_language": {"num": 0, "den": 16}},
    }
    cand = {
        "failures": {"num": 1, "den": 82},
        "objects_per_meeting": 5.4,
        "verbatim": {"num": 0, "den": 54},
        "prose": {"wrong_language": {"num": 0, "den": 15}},
        "dlift": {"point": -0.004, "low": -0.03, "high": 0.02},
    }
    cand.update(over)
    return {
        "models": {"qwen3:8b": base, "qwen3:8b@llama.cpp": cand},
        "g1": {
            "qwen3:8b@llama.cpp": {
                "dprecision": {"point": -0.01, "low": -0.04, "high": 0.03}
            }
        },
    }


def test_concept_equivalence_passes_when_every_registered_bar_holds() -> None:
    verdict = core.concept_equivalence(_concept(), "qwen3:8b@llama.cpp")
    assert verdict == {"passes": True, "reasons": []}


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"dlift": {"point": -0.03, "low": -0.06, "high": -0.001}}, "dlift"),
        ({"failures": {"num": 5, "den": 82}}, "V1"),
        ({"prose": {"wrong_language": {"num": 2, "den": 15}}}, "V3"),
        ({"objects_per_meeting": 7.6}, "V4"),
        ({"objects_per_meeting": 2.4}, "V4"),
        ({"verbatim": {"num": 7, "den": 54}}, "V5"),
    ],
)
def test_concept_equivalence_fails_each_registered_bar(
    over: dict[str, Any], reason: str
) -> None:
    verdict = core.concept_equivalence(_concept(**over), "qwen3:8b@llama.cpp")
    assert verdict["passes"] is False
    assert [r.split()[0] for r in verdict["reasons"]] == [reason]


def test_concept_equivalence_fails_g1_below_its_floor() -> None:
    scores = _concept()
    scores["g1"]["qwen3:8b@llama.cpp"]["dprecision"]["low"] = -0.06
    verdict = core.concept_equivalence(scores, "qwen3:8b@llama.cpp")
    assert [r.split()[0] for r in verdict["reasons"]] == ["G1"]


def test_concept_equivalence_fails_closed_without_g1() -> None:
    scores = _concept()
    scores["g1"] = None
    verdict = core.concept_equivalence(scores, "qwen3:8b@llama.cpp")
    assert verdict["passes"] is False
    assert verdict["reasons"] == ["G1 not measured"]


# --- shortening -------------------------------------------------------------


def test_shorten_takes_steps_in_order_until_the_budget_holds() -> None:
    parts = {"stage1": 0.6, "ask": 1.4, "concept": 2.2}
    steps = [("concept at n = 1", "concept", 0.5), ("ask at n = 3", "ask", 0.6)]
    taken, total = core.shorten(parts, steps, budget_h=4.0)
    assert taken == ["concept at n = 1"]
    assert total == pytest.approx(0.6 + 1.4 + 1.1)


def test_shorten_reports_an_overrun_after_every_step() -> None:
    parts = {"stage1": 2.0, "ask": 2.0, "concept": 4.0}
    steps = [("concept at n = 1", "concept", 0.5), ("ask at n = 3", "ask", 0.6)]
    taken, total = core.shorten(parts, steps, budget_h=4.0)
    assert taken == ["concept at n = 1", "ask at n = 3"]
    assert total == pytest.approx(2.0 + 1.2 + 2.0)


# --- #1587: the sufficiency swap on the floor -------------------------------


def _seq(arm: str, cold: float, warm: list[float]) -> dict[str, Any]:
    times = [{"q": 0, "seconds": cold, "rc": 0}]
    times += [{"q": i + 1, "seconds": w, "rc": 0} for i, w in enumerate(warm)]
    return {"arm": arm, "times": times}


def test_floor_verdict_uses_the_median_cold_and_the_pooled_warm_median() -> None:
    results = [
        _seq("a", 22.0, [10.0, 10.0, 12.0, 14.0, 14.0]),
        _seq("b", 25.0, [14.0, 15.0, 15.0, 16.0, 20.0]),
        _seq("a", 23.0, [10.0, 11.0, 12.0, 13.0, 14.0]),
        _seq("b", 31.0, [15.0, 15.0, 16.0, 17.0, 18.0]),
        _seq("a", 24.0, [10.0, 12.0, 12.0, 13.0, 15.0]),
        _seq("b", 29.0, [15.0, 16.0, 16.0, 17.0, 19.0]),
    ]
    verdict = core.floor_verdict(results)
    assert verdict["cold_s"] == {"a": 23.0, "b": 29.0}
    assert verdict["warm_median_s"] == {"a": 12.0, "b": 16.0}
    assert verdict["warm_ratio"] == pytest.approx(16.0 / 12.0)
    assert verdict["passes"] is True


def test_floor_verdict_fails_a_slow_cold_question_or_warm_ratio() -> None:
    slow_cold = [_seq("a", 20.0, [10.0] * 5), _seq("b", 31.0, [10.0] * 5)]
    assert core.floor_verdict(slow_cold)["reasons"] == ["cold 31.00 s > 30 s"]
    slow_warm = [_seq("a", 20.0, [10.0] * 5), _seq("b", 20.0, [15.1] * 5)]
    assert core.floor_verdict(slow_warm)["reasons"] == ["warm ratio 1.51 > 1.5"]


def test_floor_verdict_refuses_a_failed_question() -> None:
    results = [_seq("a", 20.0, [10.0] * 5), _seq("b", 20.0, [10.0] * 5)]
    results[1]["times"][2]["rc"] = 1
    with pytest.raises(ValueError, match="exit code"):
        core.floor_verdict(results)
