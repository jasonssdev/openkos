"""`evals/decision_revisions_screen/revisions_screen_core.py`: the deterministic
half of the decision-revisions model screen (#1563) -- the item contract, the
scoring of one verdict, the zero-model baselines, the vetoes and the summary.

These are the parts a paid run rests on and a model cannot exercise, so they
are executed here rather than trusted. The module is loaded straight from its
path, like `edge_screen_core` in `test_edge_typing_screen_core.py`.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = (
    _REPO_ROOT / "evals" / "decision_revisions_screen" / "revisions_screen_core.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rev_screen_core", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load()


def _item(
    item_id: str = "r01",
    label: str = "REVERSES",
    *,
    directed: bool = True,
    first: str | None = None,
    split: str = "original",
    contested: bool = False,
    bodies: tuple[str, str] = ("alpha beta gamma", "alpha beta delta"),
) -> Any:
    record = None
    if first is not None and first != label:
        record = {
            "first": first,
            "second": label,
            "resolved": label,
            "reason": "r",
        }
    return core.Item(
        id=item_id,
        pair_ids=(f"decisions/{item_id}-a", f"decisions/{item_id}-b"),
        label=label,
        directed=directed,
        split=split,
        contested=contested,
        hard_case=None,
        note="n",
        titles=("A", "B"),
        bodies=bodies,
        adjudication=record,
    )


def _row(
    item: str,
    verdict: str | None,
    *,
    run: int = 1,
    failure: str | None = None,
    actionable: bool = False,
    latency: float = 1.0,
) -> dict[str, Any]:
    return {
        "item": item,
        "run": run,
        "model": "m",
        "verdict": verdict,
        "confidence": 0.9,
        "actionable": actionable,
        "failure": failure,
        "latency_s": latency,
    }


# --- the fixture -------------------------------------------------------------


def test_the_real_fixture_loads_as_58_items_with_the_library_balance() -> None:
    items = core.load_items()
    assert len(items) == 58
    assert len({i.id for i in items}) == 58
    assert core.class_balance(items) == {
        "REVERSES": 14,
        "REFINES": 14,
        "REAFFIRMS": 6,
        "UNRELATED": 24,
    }
    assert sum(not i.directed for i in items) == 9
    assert sum(i.contested for i in items) == 16


def test_item_ids_follow_the_sorted_pair_key_so_they_are_stable() -> None:
    items = core.load_items()
    assert [i.pair_ids for i in items] == sorted(i.pair_ids for i in items)
    assert items[0].id == "r01"


def test_an_adjudication_overlay_resolves_a_label_and_keeps_the_first(
    tmp_path: Path,
) -> None:
    base = core.load_items()
    victim = base[0]
    other = "REFINES" if victim.label != "REFINES" else "REVERSES"
    path = tmp_path / "adj.json"
    path.write_text(
        json.dumps(
            {
                "|".join(victim.pair_ids): {
                    "first": victim.label,
                    "second": other,
                    "resolved": other,
                    "reason": "second reading",
                }
            }
        )
    )
    items = core.load_items(adjudication_path=path)
    changed = items[0]
    assert changed.label == other
    assert changed.first_label == victim.label
    assert [i.label for i in items[1:]] == [i.label for i in base[1:]]


def test_an_overlay_whose_first_label_differs_from_the_fixture_is_refused(
    tmp_path: Path,
) -> None:
    victim = core.load_items()[0]
    wrong_first = "UNRELATED" if victim.label != "UNRELATED" else "REAFFIRMS"
    path = tmp_path / "adj.json"
    path.write_text(
        json.dumps(
            {
                "|".join(victim.pair_ids): {
                    "first": wrong_first,
                    "second": "REFINES",
                    "resolved": "REFINES",
                    "reason": "r",
                }
            }
        )
    )
    with pytest.raises(ValueError, match="first"):
        core.load_items(adjudication_path=path)


def test_an_overlay_for_an_unknown_pair_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "adj.json"
    path.write_text(
        json.dumps(
            {
                "x|y": {
                    "first": "REFINES",
                    "second": "REVERSES",
                    "resolved": "REVERSES",
                    "reason": "r",
                }
            }
        )
    )
    with pytest.raises(ValueError, match="unknown pair"):
        core.load_items(adjudication_path=path)


# --- scoring one verdict -----------------------------------------------------


def test_a_matching_verdict_is_correct_and_a_different_one_is_not() -> None:
    item = _item(label="REAFFIRMS")
    assert core.is_correct(item, "reaffirms") is True
    assert core.is_correct(item, "unrelated") is False


def test_a_failed_call_is_wrong_never_skipped() -> None:
    assert core.is_correct(_item(label="UNRELATED"), None) is False


def test_on_an_undirected_pair_reverses_and_refines_are_one_untyped_change() -> None:
    undirected = _item(label="REFINES", directed=False)
    assert core.is_correct(undirected, "reverses") is True
    assert core.is_correct(undirected, "refines") is True
    assert core.is_correct(undirected, "reaffirms") is False


def test_on_a_directed_pair_reverses_and_refines_stay_distinct() -> None:
    directed = _item(label="REFINES", directed=True)
    assert core.is_correct(directed, "reverses") is False


def test_an_undirected_change_label_is_never_matched_by_unrelated() -> None:
    undirected = _item(label="REVERSES", directed=False)
    assert core.is_correct(undirected, "unrelated") is False


# --- the summary -------------------------------------------------------------


def test_accuracy_is_the_mean_over_items_of_each_items_mean_over_runs() -> None:
    items = [_item("r01", "REVERSES"), _item("r02", "UNRELATED")]
    rows = [
        _row("r01", "reverses", run=1),
        _row("r01", "refines", run=2),
        _row("r02", "unrelated", run=1),
        _row("r02", "unrelated", run=2),
    ]
    s = core.summarize(rows, items)
    assert s["strict_accuracy"] == pytest.approx((0.5 + 1.0) / 2)
    assert s["n_calls"] == 4


def test_sensitivity_scores_the_first_authors_label_and_the_uncontested_subset() -> (
    None
):
    items = [
        _item("r01", "REFINES", first="REVERSES", contested=True),
        _item("r02", "UNRELATED"),
    ]
    rows = [_row("r01", "reverses"), _row("r02", "unrelated")]
    s = core.summarize(rows, items)
    assert s["strict_accuracy"] == pytest.approx(0.5)
    assert s["strict_first_accuracy"] == pytest.approx(1.0)
    assert s["strict_uncontested_accuracy"] == pytest.approx(1.0)


def test_accuracy_is_reported_per_split_and_never_only_pooled() -> None:
    items = [
        _item("r01", "REVERSES", split="original"),
        _item("r02", "REVERSES", split="confirmation"),
    ]
    rows = [_row("r01", "reverses"), _row("r02", "refines")]
    s = core.summarize(rows, items)
    assert s["strict_by_split"] == {"original": 1.0, "confirmation": 0.0}


def test_the_confusion_matrix_counts_failed_calls_in_a_none_column() -> None:
    items = [_item("r01", "REVERSES")]
    rows = [_row("r01", "refines"), _row("r01", None, failure="backend")]
    s = core.summarize(rows, items)
    assert s["confusion"]["REVERSES"]["refines"] == 1
    assert s["confusion"]["REVERSES"]["none"] == 1
    assert s["failures"] == {"backend": 1}
    assert s["failure_rate"] == pytest.approx(0.5)


def test_a_false_supersede_is_reverses_on_a_directed_non_reversal() -> None:
    items = [
        _item("r01", "REFINES"),
        _item("r02", "UNRELATED"),
        _item("r03", "REFINES", directed=False),
        _item("r04", "REVERSES"),
    ]
    rows = [
        _row("r01", "reverses"),
        _row("r02", "unrelated"),
        _row("r03", "reverses"),
        _row("r04", "reverses"),
    ]
    s = core.summarize(rows, items)
    # r01 is the only one: r03 is undirected (an untyped change, never a
    # supersedes write) and r04 is a real reversal. Denominator: r01, r02.
    assert s["false_supersede_rate"] == pytest.approx(1 / 2)


def test_queue_burden_and_queue_recall_follow_production_actionability() -> None:
    items = [
        _item("r01", "REVERSES"),
        _item("r02", "UNRELATED"),
        _item("r03", "REAFFIRMS"),
    ]
    rows = [
        _row("r01", "reverses", actionable=True),
        _row("r02", "refines", actionable=True),
        _row("r03", "reaffirms", actionable=False),
    ]
    s = core.summarize(rows, items)
    assert s["queue_recall"] == pytest.approx(1.0)
    assert s["queue_noise_rate"] == pytest.approx(1 / 2)


def test_stability_is_the_share_of_items_with_one_answer_across_runs() -> None:
    items = [_item("r01", "REVERSES"), _item("r02", "UNRELATED")]
    rows = [
        _row("r01", "reverses", run=1),
        _row("r01", "reverses", run=2),
        _row("r02", "unrelated", run=1),
        _row("r02", "refines", run=2),
    ]
    assert core.summarize(rows, items)["stability"] == pytest.approx(0.5)


def test_latency_median_and_p95_come_from_the_rows() -> None:
    items = [_item("r01", "REVERSES")]
    rows = [_row("r01", "reverses", run=n, latency=float(n)) for n in range(1, 21)]
    s = core.summarize(rows, items)
    assert s["median_latency_s"] == pytest.approx(10.5)
    assert s["p95_latency_s"] == 19.0


# --- baselines ---------------------------------------------------------------


def test_constant_baselines_score_their_class_share() -> None:
    items = [
        _item("r01", "UNRELATED"),
        _item("r02", "UNRELATED"),
        _item("r03", "REVERSES"),
        _item("r04", "REFINES", directed=False),
    ]
    table = core.baselines(items)
    assert table["always unrelated"]["strict_accuracy"] == pytest.approx(0.5)
    # the undirected REFINES counts as a change, so always-reverses gets it
    assert table["always reverses"]["strict_accuracy"] == pytest.approx(0.5)
    assert table["always reaffirms"]["strict_accuracy"] == 0.0


def test_the_lexical_baseline_is_an_oracle_cut_that_can_beat_every_constant() -> None:
    low = _item("r01", "UNRELATED", bodies=("red green blue", "one two three"))
    high = _item("r02", "REAFFIRMS", bodies=("red green blue", "red green blue"))
    table = core.baselines([low, high])
    assert table["lexical overlap (oracle cut)"]["strict_accuracy"] == 1.0
    assert table["always unrelated"]["strict_accuracy"] == 0.5


def test_the_reference_is_the_strongest_baseline() -> None:
    items = [
        _item("r01", "UNRELATED"),
        _item("r02", "UNRELATED"),
        _item("r03", "REFINES"),
    ]
    name, scores = core.reference_baseline(items)
    assert name in core.BASELINE_NAMES
    assert scores["strict_accuracy"] == max(
        v["strict_accuracy"] for v in core.baselines(items).values()
    )


def test_majority_baseline_is_the_commonest_label_share() -> None:
    items = [
        _item("r01", "UNRELATED"),
        _item("r02", "UNRELATED"),
        _item("r03", "REFINES"),
    ]
    assert core.majority_baseline(items) == pytest.approx(2 / 3)


def test_per_item_strict_matches_the_summary_mean() -> None:
    items = [_item("r01", "REVERSES"), _item("r02", "UNRELATED")]
    rows = [_row("r01", "reverses"), _row("r02", "refines")]
    assert core.per_item_strict(rows, items) == {"r01": 1.0, "r02": 0.0}


# --- vetoes and decision -----------------------------------------------------


def _summary(failure: float, latency: float, supersede: float) -> dict[str, Any]:
    return {
        "failure_rate": failure,
        "median_latency_s": latency,
        "false_supersede_rate": supersede,
    }


def test_a_candidate_inside_every_margin_trips_no_veto() -> None:
    base = _summary(0.0, 2.0, 0.10)
    assert core.vetoes(_summary(0.01, 3.9, 0.15), base) == []


def test_each_veto_trips_on_its_own_measure() -> None:
    base = _summary(0.0, 2.0, 0.10)
    assert core.vetoes(_summary(0.06, 2.0, 0.10), base) == ["V1"]
    assert core.vetoes(_summary(0.0, 4.1, 0.10), base) == ["V2"]
    assert core.vetoes(_summary(0.0, 2.0, 0.21), base) == ["V3"]


def test_a_missing_number_trips_its_veto() -> None:
    base = _summary(0.0, 2.0, 0.10)
    assert core.vetoes({"failure_rate": None}, base) == ["V1", "V2", "V3"]


def test_selection_needs_a_positive_lower_bound_and_no_veto() -> None:
    good = {"point": 0.1, "lo": 0.01, "hi": 0.2}
    assert core.selected(good, []) is True
    assert core.selected(good, ["V2"]) is False
    assert core.selected({"point": 0.1, "lo": -0.01, "hi": 0.2}, []) is False
    assert core.justified({"point": 0.1, "lo": 0.0, "hi": 0.2}) is False


def test_the_paired_bootstrap_is_reproducible_and_brackets_the_mean() -> None:
    cand = {f"r{n}": 1.0 for n in range(10)}
    ref = {f"r{n}": 0.0 if n < 4 else 1.0 for n in range(10)}
    a = core.paired_bootstrap(cand, ref, resamples=500)
    assert a == core.paired_bootstrap(cand, ref, resamples=500)
    assert a["point"] == pytest.approx(0.4)
    assert a["lo"] <= a["point"] <= a["hi"]


def test_the_forecast_multiplies_calls_by_latency_and_adds_one_load() -> None:
    plan = core.forecast(
        n_items=58, runs=3, median_latency_s={"a": 2.0, "b": 4.0}, load_s=60.0
    )
    assert plan["calls_per_model"] == 174
    assert plan["calls"] == 348
    assert plan["seconds"]["a"] == pytest.approx(174 * 2.0 + 60.0)
    assert plan["total_seconds"] == pytest.approx(174 * 6.0 + 120.0)
