"""`evals/ask_model_screen/ask_screen_core.py`: the deterministic half of the
Ask model screen (#1560) -- the generation cache, the per-call timeout rule,
the paired item-level bootstrap and the successive-halving cut.

These are the parts a paid run rests on and a model cannot exercise, so they
are executed here rather than trusted. The module is standard library only
and is loaded straight from its path, like `harness_report` in
`test_harness_report.py`, so the suite gains no import root.
"""

import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "ask_model_screen" / "ask_screen_core.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ask_screen_core", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    # Registered before executing: `dataclasses` resolves a class's module
    # through `sys.modules` while the class body runs.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load()


class _FakeClock:
    def __init__(self, steps: Sequence[float]) -> None:
        self._steps = list(steps)

    def __call__(self) -> float:
        return self._steps.pop(0)


class _Inner:
    def __init__(self, reply: str = "hello", exc: Exception | None = None) -> None:
        self.calls = 0
        self._reply = reply
        self._exc = exc

    def chat(self, messages: Sequence[Any]) -> str:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._reply


_MESSAGES = [{"role": "user", "content": "q"}]
_IDENTITY = {"model": "m:1", "digest": "abc", "seed": 1}


# --------------------------------------------------------------------------- #
# cache_key                                                                    #
# --------------------------------------------------------------------------- #


def test_cache_key_is_order_independent_over_mapping_keys() -> None:
    assert core.cache_key({"a": 1, "b": [1, 2]}) == core.cache_key(
        {"b": [1, 2], "a": 1}
    )


def test_cache_key_changes_with_any_byte_of_the_request() -> None:
    base = core.cache_key({"messages": _MESSAGES, **_IDENTITY})
    other_seed = core.cache_key({"messages": _MESSAGES, **{**_IDENTITY, "seed": 2}})
    other_text = core.cache_key(
        {"messages": [{"role": "user", "content": "q "}], **_IDENTITY}
    )
    assert len({base, other_seed, other_text}) == 3


# --------------------------------------------------------------------------- #
# classify / reusable: the timeout rule                                        #
# --------------------------------------------------------------------------- #


def test_a_reply_that_arrived_after_the_budget_is_a_timeout() -> None:
    record = core.CallRecord(outcome="ok", reply="x", elapsed_s=61.0, timeout_s=600.0)
    assert core.classify(record, 60.0) == "timeout"
    assert core.classify(record, 61.0) == "ok"


def test_a_cached_timeout_is_reused_only_under_a_budget_no_larger() -> None:
    record = core.CallRecord(
        outcome="timeout", reply=None, elapsed_s=60.0, timeout_s=60.0
    )
    assert core.reusable(record, 60.0)
    assert core.reusable(record, 30.0)
    assert not core.reusable(record, 120.0)


def test_an_error_is_never_reused_and_an_ok_always_is() -> None:
    err = core.CallRecord(outcome="error", reply=None, elapsed_s=1.0, timeout_s=60.0)
    ok = core.CallRecord(outcome="ok", reply="x", elapsed_s=1.0, timeout_s=60.0)
    assert not core.reusable(err, 60.0)
    assert core.reusable(ok, 600.0)


# --------------------------------------------------------------------------- #
# GenerationCache + CachedChat                                                  #
# --------------------------------------------------------------------------- #


def test_cache_round_trips_a_record_and_keeps_the_request(tmp_path: Path) -> None:
    cache = core.GenerationCache(tmp_path)
    record = core.CallRecord(outcome="ok", reply="é", elapsed_s=1.5, timeout_s=60.0)
    cache.put("ab" * 32, record, request={"messages": _MESSAGES})
    assert cache.get("ab" * 32) == record
    stored = json.loads(cache.path_for("ab" * 32).read_text(encoding="utf-8"))
    assert stored["request"] == {"messages": _MESSAGES}
    assert cache.get("cd" * 32) is None


def test_cached_chat_calls_once_then_serves_from_cache(tmp_path: Path) -> None:
    inner = _Inner("reply")
    cache = core.GenerationCache(tmp_path)
    chat = core.CachedChat(
        inner,
        cache=cache,
        identity=_IDENTITY,
        timeout_s=60.0,
        clock=_FakeClock([0.0, 2.0]),
    )
    assert chat.chat(_MESSAGES) == "reply"
    again = core.CachedChat(inner, cache=cache, identity=_IDENTITY, timeout_s=60.0)
    assert again.chat(_MESSAGES) == "reply"
    assert inner.calls == 1
    assert (chat.misses, chat.hits) == (1, 0)
    assert (again.misses, again.hits) == (0, 1)
    assert again.last_record is not None
    assert again.last_record.elapsed_s == 2.0


def test_cached_chat_counts_a_slow_reply_as_a_timeout_failure(tmp_path: Path) -> None:
    inner = _Inner("late")
    chat = core.CachedChat(
        inner,
        cache=core.GenerationCache(tmp_path),
        identity=_IDENTITY,
        timeout_s=60.0,
        clock=_FakeClock([0.0, 75.0]),
    )
    with pytest.raises(core.ScreenCallFailed) as info:
        chat.chat(_MESSAGES)
    assert info.value.outcome == "timeout"


def test_cached_chat_classifies_a_raised_deadline_as_timeout(tmp_path: Path) -> None:
    inner = _Inner(exc=TimeoutError("read timed out"))
    cache = core.GenerationCache(tmp_path)
    chat = core.CachedChat(
        inner,
        cache=cache,
        identity=_IDENTITY,
        timeout_s=60.0,
        clock=_FakeClock([0.0, 60.0]),
        is_timeout=lambda exc: isinstance(exc, TimeoutError),
    )
    with pytest.raises(core.ScreenCallFailed) as info:
        chat.chat(_MESSAGES)
    assert info.value.outcome == "timeout"
    # Cached: the same budget does not pay for the same timeout twice.
    replay = core.CachedChat(inner, cache=cache, identity=_IDENTITY, timeout_s=60.0)
    with pytest.raises(core.ScreenCallFailed):
        replay.chat(_MESSAGES)
    assert inner.calls == 1


def test_cached_chat_does_not_cache_an_ordinary_error(tmp_path: Path) -> None:
    inner = _Inner(exc=RuntimeError("boom"))
    cache = core.GenerationCache(tmp_path)
    chat = core.CachedChat(
        inner,
        cache=cache,
        identity=_IDENTITY,
        timeout_s=60.0,
        clock=_FakeClock([0.0, 1.0, 2.0, 3.0]),
    )
    for _ in range(2):
        with pytest.raises(core.ScreenCallFailed) as info:
            chat.chat(_MESSAGES)
        assert info.value.outcome == "error"
    assert inner.calls == 2


def test_cached_chat_refuses_to_call_out_when_offline(tmp_path: Path) -> None:
    inner = _Inner("never")
    chat = core.CachedChat(
        inner,
        cache=core.GenerationCache(tmp_path),
        identity=_IDENTITY,
        timeout_s=60.0,
        offline=True,
    )
    with pytest.raises(core.CacheMiss):
        chat.chat(_MESSAGES)
    assert inner.calls == 0


# --------------------------------------------------------------------------- #
# paired bootstrap                                                              #
# --------------------------------------------------------------------------- #


def test_item_means_averages_runs_per_item() -> None:
    assert core.item_means([("a", 1.0), ("a", 0.0), ("b", 1.0)]) == {
        "a": 0.5,
        "b": 1.0,
    }


def test_paired_bootstrap_of_identical_arms_is_exactly_zero() -> None:
    scores = {"a": 0.2, "b": 0.9, "c": 0.5}
    delta = core.paired_bootstrap(scores, dict(scores), resamples=200)
    assert (delta.n, delta.mean, delta.lo, delta.hi) == (3, 0.0, 0.0, 0.0)


def test_paired_bootstrap_brackets_a_constant_shift() -> None:
    base = {f"q{i}": i / 10 for i in range(10)}
    cand = {k: v + 0.1 for k, v in base.items()}
    delta = core.paired_bootstrap(base, cand, resamples=500)
    assert delta.mean == pytest.approx(0.1)
    assert delta.lo == pytest.approx(0.1)
    assert delta.hi == pytest.approx(0.1)


def test_paired_bootstrap_is_deterministic_and_seeded() -> None:
    base = {f"q{i}": float(i % 2) for i in range(12)}
    cand = {f"q{i}": float(i % 3 == 0) for i in range(12)}
    first = core.paired_bootstrap(base, cand, resamples=1000, seed=42)
    assert first == core.paired_bootstrap(base, cand, resamples=1000, seed=42)
    assert first.lo <= first.mean <= first.hi
    assert first.lo < first.hi


def test_paired_bootstrap_refuses_unpaired_items() -> None:
    with pytest.raises(ValueError, match="paired"):
        core.paired_bootstrap({"a": 1.0}, {"b": 1.0})
    with pytest.raises(ValueError, match="no items"):
        core.paired_bootstrap({}, {})


# --------------------------------------------------------------------------- #
# successive halving                                                            #
# --------------------------------------------------------------------------- #


def _standing(model: str, mean: float, lo: float, hi: float, *vetoes: str) -> Any:
    return core.Standing(
        model=model,
        delta=core.PairedDelta(n=10, mean=mean, lo=lo, hi=hi),
        vetoes=tuple(vetoes),
    )


def test_halving_drops_vetoes_and_clear_losers_then_keeps_the_top_half() -> None:
    standings = [
        _standing("a", 0.10, 0.02, 0.18),
        _standing("b", 0.05, -0.02, 0.12),
        _standing("c", 0.20, 0.10, 0.30, "V1 failures"),
        _standing("d", -0.10, -0.20, -0.01),
        _standing("e", 0.01, -0.05, 0.07),
        _standing("f", 0.03, -0.04, 0.10),
    ]
    kept, dropped = core.halving_cut(standings)
    # k = 6 entered, so ceil(6/2) = 3 are kept from the 4 eligible.
    assert kept == ["a", "b", "f"]
    assert dropped["c"].startswith("veto")
    assert dropped["d"].startswith("upper bound")
    assert dropped["e"].startswith("halving")


def test_halving_keeps_at_least_two_when_two_are_eligible() -> None:
    standings = [
        _standing("a", 0.10, 0.02, 0.18),
        _standing("b", 0.05, -0.02, 0.12),
        _standing("c", 0.00, -0.10, -0.01),
    ]
    kept, _ = core.halving_cut(standings)
    assert kept == ["a", "b"]


def test_select_needs_a_positive_lower_bound_and_no_veto() -> None:
    assert core.select([_standing("a", 0.10, -0.01, 0.2)]) is None
    assert (
        core.select(
            [
                _standing("a", 0.10, 0.01, 0.2),
                _standing("b", 0.30, 0.05, 0.5, "V3 language"),
                _standing("c", 0.20, 0.02, 0.4),
            ]
        )
        == "c"
    )


# --------------------------------------------------------------------------- #
# forecast                                                                      #
# --------------------------------------------------------------------------- #


def test_forecast_hours_is_calls_times_seconds_over_3600() -> None:
    assert core.forecast_hours(items=60, runs=2, calls_per_item=2, s_per_call=15.0) == (
        pytest.approx(1.0)
    )


# --------------------------------------------------------------------------- #
# the pre-registered shortening rule                                           #
# --------------------------------------------------------------------------- #


def test_shortening_keeps_the_full_design_when_it_fits() -> None:
    plan = core.shorten(
        fixed_h=1.0, round2_h_per_run=0.2, confirm_h_per_run=0.1, budget_h=4.0
    )
    # (3 - 1) x 0.2 + 10 x 0.1 + 1.0 = 2.4 h: the first rung, unshortened.
    assert (plan.round2_runs, plan.confirm_runs) == (3, 10)
    assert plan.hours == pytest.approx(2.4)
    assert plan.fits


def test_shortening_walks_the_ladder_in_order_until_the_budget_holds() -> None:
    # Rung by rung: (3,10) 1.0+0.6+3.0=4.6; (3,5) 1.0+0.6+1.5=3.1 fits.
    plan = core.shorten(
        fixed_h=1.0, round2_h_per_run=0.3, confirm_h_per_run=0.3, budget_h=4.0
    )
    assert (plan.round2_runs, plan.confirm_runs) == (3, 5)
    # Tighter: (3,5) 2.0+0.6+1.5=4.1 and (2,5) 2.0+0.3+1.5=3.8 fits.
    plan = core.shorten(
        fixed_h=2.0, round2_h_per_run=0.3, confirm_h_per_run=0.3, budget_h=4.0
    )
    assert (plan.round2_runs, plan.confirm_runs) == (2, 5)


def test_shortening_ends_at_no_confirmation_and_says_it_does_not_fit() -> None:
    plan = core.shorten(
        fixed_h=3.9, round2_h_per_run=0.5, confirm_h_per_run=0.5, budget_h=4.0
    )
    assert (plan.round2_runs, plan.confirm_runs) == (2, 0)
    assert plan.hours == pytest.approx(4.4)
    assert not plan.fits


def test_shortening_never_lengthens_past_a_rung_already_taken() -> None:
    plan = core.shorten(
        fixed_h=0.1,
        round2_h_per_run=0.1,
        confirm_h_per_run=0.1,
        budget_h=4.0,
        floor=(2, 5),
    )
    assert (plan.round2_runs, plan.confirm_runs) == (2, 5)
