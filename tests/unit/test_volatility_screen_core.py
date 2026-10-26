"""`evals/volatility_typing_screen/screen_core.py`: the deterministic half of the
volatility-typing screen (#1563) -- the fixture contract, the scoring of one
answer, the per-class confusion, the failure and latency numbers, the
anchoring probe, the forecast and the paired bootstrap.

These are the parts a paid run rests on and a model cannot exercise, so they
are executed here rather than trusted. The module is loaded straight from its
path, like `ask_screen_core` in `test_ask_screen_core.py`.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "volatility_typing_screen" / "screen_core.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vt_screen_core", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load()


def _item(
    item_id: str = "t1",
    type_: str = "Concept",
    label: str = "slow",
    alt: str | None = None,
) -> Any:
    return core.Item(
        id=item_id,
        type=type_,
        language="en",
        docs=(core.Doc(id="d", title="D", text="body", source={}),),
        label=label,
        alt_label=alt,
        type_assigned=False,
        how="test",
    )


def _row(
    item: str,
    tier: str | None,
    *,
    arm: str = "default",
    run: int = 1,
    latency: float = 1.0,
    failure: str | None = None,
) -> dict[str, Any]:
    return {
        "item": item,
        "arm": arm,
        "run": run,
        "model": "m",
        "tier": tier,
        "failure": failure,
        "latency_s": latency,
    }


# --- scoring one answer ------------------------------------------------------


def test_strict_needs_the_primary_label() -> None:
    item = _item(label="slow", alt="static")
    assert core.score_answer(item, "slow") == (True, True)


def test_lenient_accepts_the_flagged_alternative_but_strict_does_not() -> None:
    item = _item(label="slow", alt="static")
    assert core.score_answer(item, "static") == (False, True)


def test_a_third_tier_is_wrong_on_both() -> None:
    item = _item(label="slow", alt="static")
    assert core.score_answer(item, "volatile") == (False, False)


def test_an_unflagged_item_has_no_alternative() -> None:
    item = _item(label="slow", alt=None)
    assert core.score_answer(item, "static") == (False, False)


def test_a_failed_answer_scores_zero_never_skipped() -> None:
    assert core.score_answer(_item(), None) == (False, False)


# --- the anchoring probe -----------------------------------------------------


@pytest.mark.parametrize("label", core.TIERS)
@pytest.mark.parametrize("alt", [None, *core.TIERS])
def test_the_wrong_anchor_is_neither_label_nor_alternative(
    label: str, alt: str | None
) -> None:
    if alt == label:
        pytest.skip("an alternative never equals its label")
    anchor = core.anchor_tier(_item(label=label, alt=alt))
    assert anchor in core.TIERS
    assert anchor != label
    assert anchor != alt


# --- the fixture contract ----------------------------------------------------


def _fixture(tmp_path: Path, items: list[dict[str, Any]]) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"version": 1, "items": items}), encoding="utf-8")
    return path


def _raw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "x1",
        "type": "Concept",
        "language": "en",
        "docs": [{"id": "a", "title": "A", "text": "t", "source": {}}],
        "label": "slow",
        "alt_label": None,
        "ambiguous": False,
        "type_assigned": False,
        "how": "because",
    }
    base.update(over)
    return base


def test_load_fixture_round_trips_an_item(tmp_path: Path) -> None:
    items = core.load_fixture(_fixture(tmp_path, [_raw()]))
    assert [i.id for i in items] == ["x1"]
    assert items[0].label == "slow"
    assert items[0].alt_label is None


@pytest.mark.parametrize(
    "over",
    [
        {"label": "forever"},
        {"alt_label": "forever", "ambiguous": True},
        {"alt_label": "slow", "ambiguous": True},
        {"alt_label": "static", "ambiguous": False},
        {"ambiguous": True},
        {"type": "Spaceship"},
        {"docs": []},
        {"docs": [{"id": "a", "title": "A", "text": "t", "source": {}}] * 6},
        {"how": ""},
    ],
)
def test_load_fixture_refuses_an_incoherent_item(
    tmp_path: Path, over: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match=r"^\w+: |duplicate"):
        core.load_fixture(_fixture(tmp_path, [_raw(**over)]))


def test_load_fixture_refuses_duplicate_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        core.load_fixture(_fixture(tmp_path, [_raw(), _raw()]))


def test_the_committed_fixture_loads_and_is_balanced_enough() -> None:
    items = core.load_fixture(core.FIXTURE_PATH)
    counts = core.class_balance(items)
    assert set(counts) == set(core.TIERS)
    assert all(n >= 3 for n in counts.values())


def test_the_committed_fixture_quotes_its_sources() -> None:
    items = core.load_fixture(core.FIXTURE_PATH)
    assert core.provenance_problems(items) == []


def test_provenance_flags_text_that_drifted(tmp_path: Path) -> None:
    src = tmp_path / "s.md"
    src.write_text("the original sentence", encoding="utf-8")
    raw = _raw(
        docs=[
            {
                "id": "a",
                "title": "A",
                "text": "an invented sentence",
                "source": {"kind": "file", "ref": "s.md"},
            }
        ]
    )
    items = core.load_fixture(_fixture(tmp_path, [raw]))
    problems = core.provenance_problems(items, repo_root=tmp_path)
    assert len(problems) == 1
    assert "x1" in problems[0]


# --- the question the model sees ---------------------------------------------


def test_the_bundle_holds_exactly_the_items_docs_under_the_types_directory(
    tmp_path: Path,
) -> None:
    item = _item(type_="Person")
    bundle = core.write_item_bundle(tmp_path, item)
    files = sorted(p.relative_to(bundle).as_posix() for p in bundle.rglob("*.md"))
    assert files == ["people/d.md"]
    text = (bundle / "people" / "d.md").read_text(encoding="utf-8")
    assert "type: Person" in text or '"Person"' in text
    assert "# D" in text
    assert "body" in text


# --- aggregate numbers -------------------------------------------------------


def test_accuracy_weights_each_item_equally_across_runs() -> None:
    items = [_item("a", label="slow"), _item("b", label="static")]
    rows = [
        _row("a", "slow", run=1),
        _row("a", "slow", run=2),
        _row("b", "slow", run=1),
        _row("b", "static", run=2),
    ]
    summary = core.summarize(rows, items, "default")
    assert summary["strict_accuracy"] == pytest.approx((1.0 + 0.5) / 2)


def test_an_item_with_more_runs_does_not_outweigh_one_with_fewer() -> None:
    items = [_item("a", label="slow"), _item("b", label="static")]
    rows = [
        _row("a", "slow", run=1),
        _row("a", "slow", run=2),
        _row("a", "slow", run=3),
        _row("b", "slow", run=1),
    ]
    assert core.summarize(rows, items, "default")["strict_accuracy"] == pytest.approx(
        0.5
    )


def test_class_recall_is_strict_even_when_the_alternative_is_flagged() -> None:
    items = [_item("a", label="slow", alt="static")]
    summary = core.summarize([_row("a", "static")], items, "default")
    assert summary["per_class_recall"]["slow"] == 0.0


def test_lenient_is_at_least_strict_and_counts_the_alternative() -> None:
    items = [_item("a", label="slow", alt="static")]
    summary = core.summarize([_row("a", "static")], items, "default")
    assert summary["strict_accuracy"] == 0.0
    assert summary["lenient_accuracy"] == 1.0


def test_a_failed_call_counts_as_wrong_and_in_the_failure_rate() -> None:
    items = [_item("a", label="slow")]
    rows = [_row("a", None, failure="backend"), _row("a", "slow", run=2)]
    summary = core.summarize(rows, items, "default")
    assert summary["strict_accuracy"] == pytest.approx(0.5)
    assert summary["failure_rate"] == pytest.approx(0.5)
    assert summary["failures"] == {"backend": 1}


def test_per_class_recall_and_the_confusion_matrix() -> None:
    items = [
        _item("a", label="static"),
        _item("b", label="static"),
        _item("c", label="volatile"),
    ]
    rows = [
        _row("a", "static"),
        _row("b", "slow"),
        _row("c", None, failure="invalid_reply"),
    ]
    summary = core.summarize(rows, items, "default")
    assert summary["per_class_recall"]["static"] == pytest.approx(0.5)
    assert summary["per_class_recall"]["volatile"] == 0.0
    assert summary["per_class_recall"]["slow"] is None
    assert summary["confusion"]["static"] == {
        "static": 1,
        "slow": 1,
        "volatile": 0,
        "none": 0,
    }
    assert summary["confusion"]["volatile"]["none"] == 1


def test_registry_agreement_counts_answers_equal_to_the_types_default() -> None:
    # Person's registry default is `slow`, Procedure's is `volatile`.
    items = [_item("a", type_="Person"), _item("b", type_="Procedure")]
    rows = [_row("a", "slow"), _row("b", "static")]
    summary = core.summarize(rows, items, "default")
    assert summary["registry_agreement"] == pytest.approx(0.5)


def test_latency_median_and_p95_use_every_call_even_failures() -> None:
    items = [_item("a")]
    rows = [_row("a", "slow", run=i, latency=float(i)) for i in range(1, 21)]
    rows[-1]["failure"] = "backend"
    rows[-1]["tier"] = None
    summary = core.summarize(rows, items, "default")
    assert summary["median_latency_s"] == pytest.approx(10.5)
    assert summary["p95_latency_s"] == 19.0


def test_anchor_follow_rate_only_exists_on_the_wrong_anchor_arm() -> None:
    item = _item("a", label="slow", alt=None)
    anchor = core.anchor_tier(item)
    rows = [
        _row("a", anchor, arm="wrong-anchor", run=1),
        _row("a", "slow", arm="wrong-anchor", run=2),
    ]
    wrong = core.summarize(rows, [item], "wrong-anchor")
    assert wrong["anchor_follow_rate"] == pytest.approx(0.5)
    assert core.summarize(rows, [item], "default")["n_calls"] == 0
    default = core.summarize([_row("a", "slow")], [item], "default")
    assert default["anchor_follow_rate"] is None


def test_stability_is_the_share_of_items_answered_the_same_every_run() -> None:
    items = [_item("a"), _item("b")]
    rows = [
        _row("a", "slow", run=1),
        _row("a", "slow", run=2),
        _row("b", "slow", run=1),
        _row("b", "static", run=2),
    ]
    assert core.summarize(rows, items, "default")["stability"] == pytest.approx(0.5)
    assert core.summarize(rows[:1] + rows[2:3], items, "default")["stability"] is None


def test_an_empty_arm_summarizes_without_dividing_by_zero() -> None:
    summary = core.summarize([], [_item("a")], "default")
    assert summary["n_calls"] == 0
    assert summary["strict_accuracy"] is None
    assert summary["failure_rate"] is None


# --- the registry predictor, the zero-model floor ----------------------------


def test_the_registry_predictor_scores_by_the_types_default() -> None:
    items = [
        _item("a", type_="Person", label="slow"),  # default slow: right
        _item("b", type_="Procedure", label="static", alt="slow"),  # volatile: wrong
        _item("c", type_="Decision", label="volatile", alt="static"),  # static: lenient
    ]
    base = core.registry_baseline(items)
    assert base["strict_accuracy"] == pytest.approx(1 / 3)
    assert base["lenient_accuracy"] == pytest.approx(2 / 3)
    assert base["per_item_strict"] == {"a": 1.0, "b": 0.0, "c": 0.0}


def test_the_majority_class_floor() -> None:
    items = [
        _item("a", label="slow"),
        _item("b", label="slow"),
        _item("c", label="static"),
    ]
    assert core.majority_baseline(items) == pytest.approx(2 / 3)


# --- the paired bootstrap ----------------------------------------------------


def test_bootstrap_is_deterministic_for_a_seed_and_orders_the_interval() -> None:
    a = {"x": 1.0, "y": 1.0, "z": 0.0, "w": 1.0}
    b = {"x": 0.0, "y": 1.0, "z": 0.0, "w": 0.0}
    one = core.paired_bootstrap(a, b, resamples=500, seed=7)
    two = core.paired_bootstrap(a, b, resamples=500, seed=7)
    assert one == two
    assert one["point"] == pytest.approx(0.5)
    assert one["lo"] <= one["point"] <= one["hi"]


def test_bootstrap_of_identical_scores_is_exactly_zero() -> None:
    a = {"x": 1.0, "y": 0.0}
    result = core.paired_bootstrap(a, dict(a), resamples=200, seed=1)
    assert (result["point"], result["lo"], result["hi"]) == (0.0, 0.0, 0.0)


def test_bootstrap_refuses_different_item_sets() -> None:
    with pytest.raises(ValueError, match="same items"):
        core.paired_bootstrap({"x": 1.0}, {"y": 1.0}, resamples=10, seed=1)


# --- the decision rule -------------------------------------------------------


def _summary(**over: Any) -> dict[str, Any]:
    base = {
        "failure_rate": 0.0,
        "median_latency_s": 2.0,
        "anchor_follow_rate": 0.1,
    }
    base.update(over)
    return base


def test_vetoes_pass_for_a_model_equal_to_the_baseline() -> None:
    assert core.vetoes(_summary(), _summary(), _summary()) == []


def test_each_veto_names_itself() -> None:
    base = _summary()
    assert core.vetoes(_summary(failure_rate=0.08), base, base) == ["V1"]
    assert core.vetoes(_summary(median_latency_s=4.1), base, base) == ["V2"]
    anchored = _summary(anchor_follow_rate=0.25)
    assert core.vetoes(_summary(), base, anchored) == ["V3"]


def test_failure_veto_is_both_relative_and_absolute() -> None:
    base = _summary(failure_rate=0.0)
    assert core.vetoes(_summary(failure_rate=0.02), base, base) == []
    assert core.vetoes(_summary(failure_rate=0.0201), base, base) == ["V1"]
    hi_base = _summary(failure_rate=0.05)
    assert core.vetoes(_summary(failure_rate=0.0501), hi_base, base) == ["V1"]


def test_selection_needs_a_positive_lower_bound_and_no_veto() -> None:
    win = {"point": 0.2, "lo": 0.05, "hi": 0.3}
    assert core.selected(win, []) is True
    assert core.selected({"point": 0.2, "lo": 0.0, "hi": 0.3}, []) is False
    assert core.selected(win, ["V2"]) is False


def test_justified_needs_the_lower_bound_over_the_registry_predictor_above_zero() -> (
    None
):
    assert core.justified({"point": 0.1, "lo": 0.01, "hi": 0.2}) is True
    assert core.justified({"point": 0.1, "lo": -0.01, "hi": 0.2}) is False
    assert core.justified({"point": 0.1, "lo": 0.0, "hi": 0.2}) is False


# --- the forecast ------------------------------------------------------------


def test_call_count_is_items_times_arms_times_runs_times_models() -> None:
    assert core.call_count(27, arms=2, runs=3, models=1) == 162
    assert core.call_count(27, arms=1, runs=1, models=5) == 135


def test_forecast_adds_the_loads_and_scales_with_the_latency() -> None:
    forecast = core.forecast(
        n_items=10,
        arms=2,
        runs=3,
        median_latency_s={"a": 2.0, "b": 4.0},
        load_s=60.0,
    )
    assert forecast["calls_per_model"] == 60
    assert forecast["calls"] == 120
    assert forecast["seconds"]["a"] == pytest.approx(60 * 2.0 + 60.0)
    assert forecast["seconds"]["b"] == pytest.approx(60 * 4.0 + 60.0)
    assert forecast["total_seconds"] == pytest.approx(120 + 60 + 240 + 60)


# --- the adjudication record and the first-author sensitivity ----------------


def _adjudicated(**over: Any) -> dict[str, Any]:
    record = {"first": "static", "second": "slow", "resolved": "slow", "reason": "r"}
    fields: dict[str, Any] = {
        "label": "slow",
        "alt_label": "static",
        "ambiguous": True,
        "adjudication": {**record, **over.pop("record", {})},
    }
    return _raw(**{**fields, **over})


def test_an_adjudicated_item_keeps_the_first_label_recoverable(tmp_path: Path) -> None:
    (item,) = core.load_fixture(_fixture(tmp_path, [_adjudicated()]))
    assert (item.label, item.first_label, item.alt_label) == (
        "slow",
        "static",
        "static",
    )


def test_an_unadjudicated_item_has_its_label_as_first_label(tmp_path: Path) -> None:
    (item,) = core.load_fixture(_fixture(tmp_path, [_raw()]))
    assert item.first_label == item.label


@pytest.mark.parametrize(
    "over",
    [
        {"record": {"resolved": "volatile"}},
        {"record": {"first": "slow"}},
        {"alt_label": "volatile"},
    ],
)
def test_load_fixture_refuses_an_incoherent_adjudication(
    tmp_path: Path, over: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match=r"^\w+: "):
        core.load_fixture(_fixture(tmp_path, [_adjudicated(**over)]))


def test_the_first_author_sensitivity_scores_against_the_first_label() -> None:
    item = core.Item(
        id="a",
        type="Person",
        language="en",
        docs=(core.Doc(id="d", title="t", text="x", source={}),),
        label="volatile",
        alt_label="slow",
        type_assigned=False,
        how="h",
        adjudication={
            "first": "slow",
            "second": "volatile",
            "resolved": "volatile",
            "reason": "r",
        },
    )
    rows = [_row("a", "slow", run=1), _row("a", "slow", run=2), _row("a", None, run=3)]
    summary = core.summarize(rows, [item], "default")
    assert summary["strict_first_accuracy"] == pytest.approx(2 / 3)
    assert summary["strict_accuracy"] == 0.0
    assert core.registry_baseline([item])["strict_first_accuracy"] == 1.0
