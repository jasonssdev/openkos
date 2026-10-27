"""`evals/edge_typing_screen/edge_screen_core.py`: the deterministic half of the
edge-typing model screen (#1563) -- the fixture contract, the scoring of one
answer, the direction trap, the model-free baselines, the vetoes, the paired
bootstrap and the forecast.

These are the parts a paid run rests on and a model cannot exercise, so they
are executed here rather than trusted. The module is loaded straight from its
path, like `screen_core` in `test_volatility_screen_core.py`.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "evals" / "edge_typing_screen" / "edge_screen_core.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("et_screen_core", _MODULE_PATH)
    assert spec is not None, _MODULE_PATH
    assert spec.loader is not None, _MODULE_PATH
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load()


def _doc(doc_id: str = "concepts/a", type_: str = "Concept") -> Any:
    return core.Doc(
        id=doc_id,
        type=type_,
        title=doc_id.split("/")[-1].title(),
        text="body",
        language="en",
        source={},
    )


def _item(
    item_id: str = "t1",
    label: str = "part_of",
    alt: str | None = None,
    trap: str | None = None,
    source_type: str = "Concept",
    target_type: str = "Concept",
) -> Any:
    return core.Item(
        id=item_id,
        stratum="constructed",
        source=_doc("concepts/a", source_type),
        target=_doc("concepts/b", target_type),
        label=label,
        alt_label=alt,
        trap_type=trap,
        how="test",
    )


def _row(
    item: str,
    answer: str | None,
    *,
    run: int = 1,
    corrected: bool = False,
    failure: str | None = None,
    latency: float = 1.0,
) -> dict[str, Any]:
    return {
        "item": item,
        "arm": "default",
        "run": run,
        "model": "m",
        "type": answer,
        "corrected": corrected,
        "withdrawn": False,
        "failure": failure,
        "latency_s": latency,
    }


# --- the vocabulary is production's ------------------------------------------


def test_the_vocabulary_is_exactly_what_production_may_suggest() -> None:
    from openkos.model import relations

    assert set(core.TYPES) == set(relations.SUGGESTABLE_RELATION_TYPES)
    assert "derived_from" not in core.TYPES
    assert len(core.TYPES) == len(set(core.TYPES))


# --- scoring one answer ------------------------------------------------------


def test_strict_needs_the_primary_label() -> None:
    assert core.score_answer(_item(label="part_of", alt="depends_on"), "part_of") == (
        True,
        True,
    )


def test_lenient_accepts_the_flagged_alternative_but_strict_does_not() -> None:
    item = _item(label="references", alt="related_to")
    assert core.score_answer(item, "related_to") == (False, True)


def test_a_third_type_is_wrong_on_both() -> None:
    item = _item(label="references", alt="related_to")
    assert core.score_answer(item, "caused_by") == (False, False)


def test_a_failed_answer_scores_zero_never_skipped() -> None:
    assert core.score_answer(_item(), None) == (False, False)


def test_a_direction_corrected_answer_is_not_the_edge_as_shown() -> None:
    """Production swapped the pair: the suggestion now describes the REVERSE
    edge, so it cannot score as the label of the edge that was shown."""
    assert core.score_answer(_item(label="member_of"), "member_of", True) == (
        False,
        False,
    )


# --- the direction trap ------------------------------------------------------


def test_the_trap_type_on_a_reversed_probe_is_a_hit() -> None:
    item = _item(label="related_to", trap="member_of")
    assert core.is_trap_hit(item, "member_of", False) is True


def test_an_honest_answer_on_a_reversed_probe_is_not_a_hit() -> None:
    item = _item(label="related_to", trap="member_of")
    assert core.is_trap_hit(item, "related_to", False) is False


def test_a_trap_answer_production_corrected_is_recovered_not_a_hit() -> None:
    item = _item(label="related_to", trap="member_of")
    assert core.is_trap_hit(item, "member_of", True) is False
    assert core.is_recovered(item, "member_of", True) is True


def test_an_item_without_a_trap_type_never_hits() -> None:
    assert core.is_trap_hit(_item(), "part_of", False) is False


# --- the fixture contract ----------------------------------------------------


def _fixture(tmp_path: Path, items: list[dict[str, Any]], docs: Any = None) -> Path:
    base_docs = {
        "concepts/a": {
            "id": "concepts/a",
            "type": "Concept",
            "title": "A",
            "text": "t",
            "language": "en",
            "source": {},
        },
        "concepts/b": {
            "id": "concepts/b",
            "type": "Concept",
            "title": "B",
            "text": "t",
            "language": "en",
            "source": {},
        },
    }
    path = tmp_path / "fixture.json"
    path.write_text(
        json.dumps({"version": 1, "docs": docs or base_docs, "items": items}),
        encoding="utf-8",
    )
    return path


def _raw(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "x1",
        "stratum": "constructed",
        "source": "concepts/a",
        "target": "concepts/b",
        "label": "part_of",
        "alt_label": None,
        "ambiguous": False,
        "trap_type": None,
        "how": "because",
        "adjudication": None,
    }
    base.update(over)
    return base


def test_a_coherent_fixture_loads(tmp_path: Path) -> None:
    items = core.load_fixture(_fixture(tmp_path, [_raw()]))
    assert [i.id for i in items] == ["x1"]
    assert items[0].source.id == "concepts/a"


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"label": "derived_from"}, "not a suggestable type"),
        ({"label": "nonsense"}, "not a suggestable type"),
        ({"alt_label": "part_of"}, "must be another type"),
        ({"alt_label": "nonsense", "ambiguous": True}, "must be another type"),
        ({"alt_label": "depends_on", "ambiguous": False}, "`ambiguous` must hold"),
        ({"trap_type": "references"}, "must be a structural type"),
        ({"how": "  "}, "states how it was decided"),
        ({"source": "concepts/missing"}, "is not in `docs`"),
        ({"target": "concepts/a"}, "two different documents"),
    ],
)
def test_an_incoherent_item_is_refused(
    tmp_path: Path, over: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        core.load_fixture(_fixture(tmp_path, [_raw(**over)]))


def test_duplicate_item_ids_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate item ids"):
        core.load_fixture(_fixture(tmp_path, [_raw(), _raw()]))


def test_the_adjudication_record_must_resolve_to_the_label(tmp_path: Path) -> None:
    record = {
        "first": "references",
        "second": "part_of",
        "resolved": "part_of",
        "reason": "r",
    }
    good = _raw(adjudication=record, alt_label="references", ambiguous=True)
    assert core.load_fixture(_fixture(tmp_path, [good]))[0].first_label == "references"
    with pytest.raises(ValueError, match="must resolve to `label`"):
        core.load_fixture(
            _fixture(
                tmp_path,
                [_raw(adjudication={**record, "resolved": "depends_on"})],
            )
        )


def test_the_shipped_fixture_loads_and_covers_every_type() -> None:
    items = core.load_fixture()
    balance = core.class_balance(items)
    assert set(balance) == set(core.TYPES)
    assert all(count >= 2 for count in balance.values())
    assert core.provenance_problems(items) == []


# --- baselines ---------------------------------------------------------------


def test_the_constant_predictors_score_their_label_share() -> None:
    items = [
        _item("a", "references"),
        _item("b", "references"),
        _item("c", "related_to"),
        _item("d", "part_of"),
    ]
    base = core.baselines(items)
    assert base["always references"]["strict_accuracy"] == 0.5
    assert base["always related_to"]["strict_accuracy"] == 0.25
    assert core.majority_baseline(items) == 0.5


def test_the_signature_predictor_reads_production_s_direction_table() -> None:
    assert core.signature_predict("Person", "Organization") == "member_of"
    assert core.signature_predict("Concept", "Project") == "produced_by"
    assert core.signature_predict("Concept", "Person") == "produced_by"
    assert core.signature_predict("Organization", "Person") == "related_to"
    assert core.signature_predict("Concept", "Concept") == "related_to"


def test_the_reference_baseline_is_the_strongest_model_free_predictor() -> None:
    items = [_item("a", "references"), _item("b", "references"), _item("c", "part_of")]
    name, _ = core.reference_baseline(items)
    assert name == "always references"


# --- aggregate numbers -------------------------------------------------------


def test_accuracy_is_the_mean_over_items_of_the_mean_over_runs() -> None:
    items = [_item("a", "part_of"), _item("b", "references")]
    rows = [
        _row("a", "part_of", run=1),
        _row("a", "references", run=2),
        _row("b", "references", run=1),
        _row("b", "references", run=2),
    ]
    summary = core.summarize(rows, items, "default")
    assert summary["strict_accuracy"] == pytest.approx((0.5 + 1.0) / 2)


def test_a_failed_call_counts_against_the_item_and_the_failure_rate() -> None:
    items = [_item("a", "part_of")]
    rows = [_row("a", None, failure="invalid_reply"), _row("a", "part_of", run=2)]
    summary = core.summarize(rows, items, "default")
    assert summary["strict_accuracy"] == 0.5
    assert summary["failure_rate"] == 0.5
    assert summary["failures"] == {"invalid_reply": 1}


def test_the_confusion_matrix_is_gold_by_answered_with_a_none_column() -> None:
    items = [_item("a", "part_of")]
    rows = [_row("a", "member_of"), _row("a", None, run=2, failure="backend")]
    confusion = core.summarize(rows, items, "default")["confusion"]
    assert confusion["part_of"]["member_of"] == 1
    assert confusion["part_of"]["none"] == 1
    assert confusion["part_of"]["part_of"] == 0


def test_per_class_recall_is_over_calls_of_that_class() -> None:
    items = [_item("a", "part_of"), _item("b", "part_of"), _item("c", "references")]
    rows = [_row("a", "part_of"), _row("b", "references"), _row("c", "references")]
    recall = core.summarize(rows, items, "default")["per_class_recall"]
    assert recall["part_of"] == 0.5
    assert recall["references"] == 1.0
    assert recall["caused_by"] is None


def test_specific_type_precision_counts_only_specific_answers() -> None:
    items = [_item("a", "part_of"), _item("b", "references"), _item("c", "member_of")]
    rows = [
        _row("a", "part_of"),  # specific, right
        _row("b", "caused_by"),  # specific, wrong
        _row("c", "related_to"),  # not specific
    ]
    summary = core.summarize(rows, items, "default")
    assert summary["specific_precision"] == 0.5
    assert summary["specific_answers"] == 2


def test_the_trap_hit_rate_is_over_reversed_probes_only() -> None:
    items = [
        _item("a", "part_of"),
        _item("b", "related_to", trap="member_of"),
        _item("c", "related_to", trap="member_of"),
    ]
    rows = [
        _row("a", "part_of"),
        _row("b", "member_of"),
        _row("c", "related_to"),
        _row("c", "member_of", run=2, corrected=True),
    ]
    summary = core.summarize(rows, items, "default")
    assert summary["trap_calls"] == 3
    assert summary["trap_hit_rate"] == pytest.approx(1 / 3)
    assert summary["recovered_rate"] == pytest.approx(1 / 3)


def test_stability_is_the_share_of_items_answered_the_same_every_run() -> None:
    items = [_item("a"), _item("b")]
    rows = [
        _row("a", "part_of", run=1),
        _row("a", "part_of", run=2),
        _row("b", "part_of", run=1),
        _row("b", "member_of", run=2),
    ]
    assert core.summarize(rows, items, "default")["stability"] == 0.5


def test_the_first_author_sensitivity_scores_the_same_answers_differently() -> None:
    item = core.Item(
        id="x",
        stratum="constructed",
        source=_doc("concepts/a"),
        target=_doc("concepts/b"),
        label="part_of",
        alt_label="references",
        trap_type=None,
        how="h",
        adjudication={
            "first": "references",
            "second": "part_of",
            "resolved": "part_of",
            "reason": "r",
        },
    )
    summary = core.summarize([_row("x", "references")], [item], "default")
    assert summary["strict_accuracy"] == 0.0
    assert summary["strict_first_accuracy"] == 1.0


def test_per_stratum_accuracy_is_reported_separately() -> None:
    a = _item("a", "part_of")
    b = core.Item(**{**a.__dict__, "id": "b", "stratum": "adr"})
    rows = [_row("a", "part_of"), _row("b", "references")]
    by = core.summarize(rows, [a, b], "default")["strict_by_stratum"]
    assert by == {"constructed": 1.0, "adr": 0.0}


# --- comparison --------------------------------------------------------------


def test_the_bootstrap_of_a_uniform_gain_is_that_gain() -> None:
    cand = {f"i{n}": 1.0 for n in range(10)}
    ref = {f"i{n}": 0.0 for n in range(10)}
    gap = core.paired_bootstrap(cand, ref, resamples=200)
    assert gap["point"] == gap["lo"] == gap["hi"] == 1.0


def test_the_bootstrap_refuses_unpaired_items() -> None:
    with pytest.raises(ValueError, match="same items"):
        core.paired_bootstrap({"a": 1.0}, {"b": 1.0})


def test_a_gain_with_a_negative_lower_bound_is_not_justified() -> None:
    assert core.justified({"point": 0.1, "lo": -0.02, "hi": 0.2}) is False
    assert core.justified({"point": 0.1, "lo": 0.01, "hi": 0.2}) is True


def test_selection_needs_a_positive_lower_bound_and_no_veto() -> None:
    gain = {"point": 0.1, "lo": 0.01, "hi": 0.2}
    assert core.selected(gain, []) is True
    assert core.selected(gain, ["V2"]) is False
    assert core.selected({"point": 0.1, "lo": -0.01, "hi": 0.2}, []) is False


def _summary(**over: Any) -> dict[str, Any]:
    base = {"failure_rate": 0.0, "median_latency_s": 1.0, "trap_hit_rate": 0.1}
    base.update(over)
    return base


def test_no_veto_trips_for_an_equal_candidate() -> None:
    assert core.vetoes(_summary(), _summary()) == []


def test_each_veto_trips_on_its_own_number() -> None:
    assert core.vetoes(_summary(failure_rate=0.06), _summary()) == ["V1"]
    assert core.vetoes(_summary(median_latency_s=2.1), _summary()) == ["V2"]
    assert core.vetoes(_summary(trap_hit_rate=0.21), _summary()) == ["V3"]


def test_a_missing_number_trips_its_veto() -> None:
    assert core.vetoes(_summary(trap_hit_rate=None), _summary()) == ["V3"]


# --- forecast ----------------------------------------------------------------


def test_the_forecast_is_calls_times_latency_plus_one_load() -> None:
    plan = core.forecast(
        n_items=10, arms=1, runs=3, median_latency_s={"m": 2.0}, load_s=60.0
    )
    assert plan["calls_per_model"] == 30
    assert plan["seconds"] == {"m": 120.0}
    assert plan["total_seconds"] == 120.0
