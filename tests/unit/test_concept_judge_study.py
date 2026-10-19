"""`evals/concept_model_screen/judge_study.py`: the selector-judge loss study
on the cached #1557 records (#1571).

The study replays each record through production `extract_concept_union` with
every extraction call served from the #1557 cache and only the selector judge
routed to a chosen model. These tests pin what a number rests on: the routing,
the refusal to regenerate an extraction, the read-only fallback cache, the
seed schedule, the per-arm aggregation, and the registered decision rules.
The module is loaded from its path, like `test_concept_drop_ablation.py`.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "concept_model_screen" / "judge_study.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("judge_study", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


js = _load()
sp = js.sp
concept_mod = js.concept_mod
judge_mod = js.judge_mod

_WINDOWS = ["first window text", "second window text"]


def _ok(content: str) -> dict[str, Any]:
    return {"outcome": "ok", "wall_s": 1.0, "content": content}


def _router(
    tmp_path: Path,
    *,
    judge_send: Any = None,
    judge_model: str = "fixed",
    seed_index: int = 0,
    run: int = 1,
) -> Any:
    old = sp.ChatCache(tmp_path / "old")
    cache = js.LayeredCache(tmp_path / "new", fallbacks=[tmp_path / "old"])
    judge = None
    if judge_send is not None:
        judge = sp.ScreenBackend(
            model=judge_model,
            digest="dj",
            run=js.judge_run(run, seed_index),
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=cache,
            send=judge_send,
            windows=_WINDOWS,
        )
    router = js.JudgeRouter(
        model="ext",
        digest="dx",
        run=run,
        window_timeout_s=60,
        whole_timeout_s=180,
        cache=cache,
        windows=_WINDOWS,
        judge=judge,
    )
    return old, router


def test_judge_run_offsets_by_one_hundred_per_seed_index() -> None:
    assert js.judge_run(1, 0) == 1
    assert js.judge_run(2, 1) == 102
    assert js.judge_run(1, 2) == 201


def test_layered_cache_reads_the_fallback_and_writes_only_the_new_root(
    tmp_path: Path,
) -> None:
    sp.ChatCache(tmp_path / "old").put("ab12", _ok("old"))
    cache = js.LayeredCache(tmp_path / "new", fallbacks=[tmp_path / "old"])
    assert cache.get("ab12") == _ok("old")
    cache.put("cd34", _ok("new"))
    assert sp.ChatCache(tmp_path / "new").get("cd34") == _ok("new")
    assert sp.ChatCache(tmp_path / "old").get("cd34") is None
    assert cache.get("ef56") is None


def test_layered_cache_prefers_the_new_root(tmp_path: Path) -> None:
    sp.ChatCache(tmp_path / "old").put("ab12", _ok("old"))
    sp.ChatCache(tmp_path / "new").put("ab12", _ok("new"))
    cache = js.LayeredCache(tmp_path / "new", fallbacks=[tmp_path / "old"])
    assert cache.get("ab12") == _ok("new")


def test_router_serves_an_extraction_call_from_the_cache(tmp_path: Path) -> None:
    old, router = _router(tmp_path)
    messages = concept_mod._build_messages(_WINDOWS[0], "T")
    key = sp.call_key(model="ext", digest="dx", messages=messages, seed=1)
    old.put(key, _ok("[]"))
    assert router.chat(messages) == "[]"
    assert router.misses == []
    assert router.records[0].cache_hit


def test_router_refuses_to_regenerate_an_extraction(tmp_path: Path) -> None:
    _old, router = _router(tmp_path)
    messages = concept_mod._build_messages(_WINDOWS[1], "T")
    with pytest.raises(js.ExtractionCacheMiss):
        router.chat(messages)
    assert router.misses == ["ext"]


def test_extraction_cache_miss_is_not_a_backend_error() -> None:
    # Production retries, then skips, a window whose call raises a
    # BackendError; a miss must stop the run, never pass as a failed window.
    assert not issubclass(js.ExtractionCacheMiss, js.BackendError)


def test_router_sends_the_judge_call_to_the_judge_model(tmp_path: Path) -> None:
    sent: list[tuple[str, int, float]] = []

    def judge_send(
        model: str, messages: Any, seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        sent.append((model, seed, timeout))
        reply = '{"keep": ["A"]}'
        return reply, _ok(reply)

    _old, router = _router(tmp_path, judge_send=judge_send, seed_index=2, run=1)
    messages = judge_mod._build_judge_messages(
        "src", [judge_mod.JudgeCandidate(type="Concept", title="A", description="d")]
    )
    assert router.chat(messages) == '{"keep": ["A"]}'
    assert sent == [("fixed", 201, 180)]
    assert router.misses == []
    assert [r.kind for r in router.records] == [sp.KIND_JUDGE]


def test_router_without_a_judge_serves_the_self_judge_from_the_cache(
    tmp_path: Path,
) -> None:
    old, router = _router(tmp_path)
    messages = judge_mod._build_judge_messages(
        "src", [judge_mod.JudgeCandidate(type="Concept", title="A", description="d")]
    )
    key = sp.call_key(model="ext", digest="dx", messages=messages, seed=1)
    old.put(key, _ok('{"keep": ["A"]}'))
    assert router.chat(messages) == '{"keep": ["A"]}'


def _chunking_fixture() -> tuple[str, list[str]]:
    lines = [
        f"{'AB'[i % 2]}: speaker line number {i} about the remote control design "
        + "y" * 60
        for i in range(160)
    ]
    text = "\n".join(lines)
    return text, concept_mod._chunk_lines(text)


def test_replay_with_a_fixed_judge_keeps_the_judge_input(tmp_path: Path) -> None:
    text, windows = _chunking_fixture()

    def extractor(
        model: str, messages: Any, seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        system = str(messages[0]["content"])
        user = str(messages[-1]["content"])
        if system == concept_mod._SYSTEM_PROMPT:
            idx = next(i for i, w in enumerate(windows) if w in user)
            reply = json.dumps(
                [
                    {
                        "type": "Concept",
                        "title": f"Topic {idx}",
                        "description": f"About topic {idx}.",
                        "body": "b",
                    }
                ]
            )
            return reply, _ok(reply)
        if system == judge_mod._JUDGE_SYSTEM_PROMPT:
            reply = json.dumps({"keep": [f"Topic {i}" for i in range(len(windows))]})
            return reply, _ok(reply)
        return "[]", _ok("[]")

    old = sp.ChatCache(tmp_path / "old")
    original = sp.run_source(
        text=text,
        title="ES2015a",
        backend=sp.ScreenBackend(
            model="ext",
            digest="dx",
            run=1,
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=old,
            send=extractor,
            windows=windows,
        ),
        cache=old,
    )
    old_files = sorted(p.name for p in (tmp_path / "old").rglob("*.json"))

    def fixed(
        model: str, messages: Any, seed: int, timeout: float
    ) -> tuple[str | None, dict[str, Any]]:
        reply = json.dumps({"keep": ["Topic 0"]})
        return reply, _ok(reply)

    cache = js.LayeredCache(tmp_path / "new", fallbacks=[tmp_path / "old"])
    router = js.JudgeRouter(
        model="ext",
        digest="dx",
        run=1,
        window_timeout_s=60,
        whole_timeout_s=180,
        cache=cache,
        windows=windows,
        judge=sp.ScreenBackend(
            model="fixed",
            digest="dj",
            run=js.judge_run(1, 0),
            window_timeout_s=60,
            whole_timeout_s=180,
            cache=cache,
            send=fixed,
            windows=windows,
        ),
    )
    replayed = sp.run_source(text=text, title="ES2015a", backend=router, cache=cache)

    assert router.misses == []
    assert js.same_objects(replayed["stages"]["ceiling"], original["stages"]["ceiling"])
    assert [o["title"] for o in replayed["stages"]["final"]] == ["Topic 0"]
    assert len(original["stages"]["final"]) == len(windows)
    assert sorted(p.name for p in (tmp_path / "old").rglob("*.json")) == old_files


def test_same_objects_compares_type_title_description_and_window_in_order() -> None:
    a = {"type": "Concept", "title": "A", "description": "d", "window": 0}
    b = {"type": "Concept", "title": "B", "description": "d", "window": 1}
    assert js.same_objects([a, b], [dict(a, body_chars=3), b])
    assert not js.same_objects([a, b], [b, a])
    assert not js.same_objects([a], [dict(a, window=1)])
    assert not js.same_objects(None, [a])


def test_seed_mean_window_lifts_averages_each_window_over_seeds() -> None:
    assert js.seed_mean([{0: 0.1, 1: 0.0}, {0: 0.3, 1: 0.2}]) == pytest.approx(
        {0: 0.2, 1: 0.1}
    )


def test_seed_mean_refuses_seeds_with_different_windows() -> None:
    with pytest.raises(ValueError, match="windows"):
        js.seed_mean([{0: 0.1}, {1: 0.2}])


def test_arm_counts_separate_windowed_from_whole_source_cuts() -> None:
    ceiling = [
        {"title": "A", "window": 0},
        {"title": "B", "window": 1},
        {"title": "P", "window": None},
    ]
    finals = [
        [{"title": "A", "window": 0}],
        [{"title": "A", "window": 0}, {"title": "P", "window": None}],
    ]
    counts = js.arm_counts(ceiling, finals)
    assert counts == {
        "input": 3,
        "kept": pytest.approx(1.5),
        "judged_out": pytest.approx(1.5),
        "judged_out_windowed": pytest.approx(1.0),
    }


def _ci(point: float, low: float, high: float) -> dict[str, float]:
    return {"point": point, "low": low, "high": high}


def test_classify_coupling_when_the_fixed_judge_keeps_more_lift() -> None:
    label = js.classify_extractor(
        coupling=_ci(0.02, 0.001, 0.04), fixed_loss=_ci(-0.01, -0.03, 0.01)
    )
    assert label == "coupling"


def test_classify_judge_when_a_fixed_judge_still_costs_lift() -> None:
    label = js.classify_extractor(
        coupling=_ci(0.0, -0.01, 0.01), fixed_loss=_ci(-0.02, -0.04, -0.001)
    )
    assert label == "judge"


def test_classify_unresolved_when_both_intervals_hold_zero() -> None:
    label = js.classify_extractor(
        coupling=_ci(0.01, -0.01, 0.03), fixed_loss=_ci(-0.01, -0.03, 0.01)
    )
    assert label == "unresolved"


def test_classify_coupling_wins_over_judge() -> None:
    label = js.classify_extractor(
        coupling=_ci(0.02, 0.001, 0.04), fixed_loss=_ci(-0.02, -0.04, -0.001)
    )
    assert label == "coupling"


@pytest.mark.parametrize(
    ("labels", "verdict"),
    [
        (["coupling", "coupling", "judge", "unresolved"], "coupling"),
        (["judge", "judge", "unresolved", "unresolved"], "judge"),
        (["judge", "judge", "coupling", "unresolved"], "not_resolved"),
        (["coupling", "unresolved", "unresolved", "unresolved"], "not_resolved"),
        (["unresolved"] * 4, "not_resolved"),
    ],
)
def test_overall_verdict_follows_the_registered_counts(
    labels: list[str], verdict: str
) -> None:
    assert js.overall_verdict(labels) == verdict


def test_follow_up_needed_when_any_extractor_is_attributable() -> None:
    assert js.follow_up_needed(["unresolved", "judge"])
    assert js.follow_up_needed(["coupling"])
    assert not js.follow_up_needed(["unresolved", "unresolved"])


@pytest.mark.parametrize(
    ("fixed_s", "self_per_seed_s", "expected"),
    [
        (1500.0, 1000.0, (0, 1, 2)),
        (1500.0, 3000.0, (0, 1)),
        (1500.0, 6000.0, (0,)),
    ],
)
def test_self_seeds_follow_the_shortening_rule(
    fixed_s: float, self_per_seed_s: float, expected: tuple[int, ...]
) -> None:
    assert (
        js.self_seed_indices(
            fixed_s=fixed_s, self_per_seed_s=self_per_seed_s, loads_s=300.0
        )
        == expected
    )
