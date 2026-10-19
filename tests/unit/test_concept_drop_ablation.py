"""`evals/concept_model_screen/drop_ablation.py`: the per-rule drop ablation
on the cached #1557 records (#1572).

The ablation replays each record's raw window objects through production's
own L6/L7 functions with one rule turned off. These tests pin the parts a
number rests on: the replay order, which objects a toggle restores, the
cache-only embedding guard, and the per-window aggregation. The module is
loaded straight from its path, like `test_ask_screen_core.py`, so the suite
gains no import root.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "concept_model_screen" / "drop_ablation.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("drop_ablation", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ab = _load()

_SOURCE_TITLE = "TS2099a"
_SOURCE_TEXT = "A: we discuss the remote control and its battery.\n"


def _obj(title: str, window: int, kind: str = "Concept") -> dict[str, Any]:
    return {
        "type": kind,
        "title": title,
        "description": f"{title} described.",
        "body_chars": 10,
        "window": window,
    }


def _titles(objs: list[dict[str, Any]]) -> list[str]:
    return [o["title"] for o in objs]


_RAW = [
    _obj("Kickoff Meeting", 0, "Event"),
    _obj("Remote Control", 0),
    _obj("Remote Control", 1),
    _obj("Battery Life", 1),
    _obj(_SOURCE_TITLE, 2),
]


def _replay(off: str | None) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = ab.replay(
        _RAW,
        source_text=_SOURCE_TEXT,
        source_title=_SOURCE_TITLE,
        framing_shaped=True,
        off=off,
    )
    return out


def test_full_replay_applies_every_rule_in_production_order() -> None:
    out = _replay(None)
    assert _titles(out["dedup"]) == [
        "Kickoff Meeting",
        "Remote Control",
        "Battery Life",
        _SOURCE_TITLE,
    ]
    assert _titles(out["drops"]) == ["Remote Control", "Battery Life"]


def test_window_attribution_survives_the_replay() -> None:
    out = _replay(None)
    assert [o["window"] for o in out["drops"]] == [0, 1]
    assert out["drops"][0]["body_chars"] == 10


@pytest.mark.parametrize(
    ("off", "restored"),
    [
        ("framing", ["Kickoff Meeting"]),
        ("twins", [_SOURCE_TITLE]),
        ("dedup", ["Remote Control"]),
        ("language", []),
    ],
)
def test_each_toggle_restores_only_its_own_drops(off: str, restored: list[str]) -> None:
    full = _replay(None)["drops"]
    without = _replay(off)["drops"]
    assert _titles(ab.restored_objects(without, full)) == restored


def test_an_unknown_toggle_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown rule"):
        _replay("ceiling")


def test_framing_is_inert_on_a_source_that_is_not_framing_shaped() -> None:
    out = ab.replay(
        _RAW,
        source_text=_SOURCE_TEXT,
        source_title=_SOURCE_TITLE,
        framing_shaped=False,
        off=None,
    )
    assert "Kickoff Meeting" in _titles(out["drops"])


def test_matches_recorded_compares_order_and_window() -> None:
    a = [_obj("A", 0), _obj("B", 1)]
    assert ab.matches_recorded(a, [dict(o) for o in a])
    assert not ab.matches_recorded(a, list(reversed(a)))
    assert not ab.matches_recorded(a, [_obj("A", 0), _obj("B", 2)])


def test_cache_only_embed_never_computes_a_vector() -> None:
    with pytest.raises(ab.EmbeddingCacheMiss):
        ab.cache_only_embed(["anything"])


def test_windows_emptied_counts_gold_windows_left_without_objects() -> None:
    full = [_obj("B", 1)]
    without = [_obj("A", 0), _obj("B", 1), _obj("C", 3)]
    assert ab.windows_emptied(without, full, gold_windows={0, 1, 2}) == 1


def test_restore_only_keeps_full_plus_the_chosen_restored_objects() -> None:
    full = [_obj("B", 1)]
    without = [_obj("A", 0), _obj("B", 1), _obj("C", 0)]
    kept = ab.restore_only(without, full, keep={ab.object_key(without[2])})
    assert _titles(kept) == ["B", "C"]


def test_effect_is_paired_per_window_and_averaged_over_runs() -> None:
    # Two runs of one meeting; window 0 gains 0.2 then 0.0, window 1 is
    # unchanged: the per-window run means are 0.1 and 0.0.
    per_record = [
        ("M1", {"full": {0: 0.1, 1: 0.3}, "framing": {0: 0.3, 1: 0.3}}),
        ("M1", {"full": {0: 0.1, 1: 0.3}, "framing": {0: 0.1, 1: 0.3}}),
    ]
    effect = ab.variant_effect(per_record, "framing", resamples=200)
    assert effect["lift"] == pytest.approx((0.2 + 0.3) / 2)
    assert effect["base_lift"] == pytest.approx((0.1 + 0.3) / 2)
    assert effect["delta"]["point"] == pytest.approx(0.05)
