"""`evals/decision_fidelity/`: the deterministic half of the Decision-fidelity
measurement (#1649) -- the eligibility allowlist, the value check, the commitment
cue, the scoring of one run, the clustered bootstrap and the decision rule.

These are the parts a paid run rests on and a model cannot exercise, so they are
executed here rather than trusted. The module is loaded straight from its path,
like the other eval cores.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DIR = _REPO_ROOT / "evals" / "decision_fidelity"


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _DIR / filename)
    assert spec is not None, filename
    assert spec.loader is not None, filename
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("fidelity_core", "fidelity_core.py")


# --- eligibility ------------------------------------------------------------


def test_the_gold_meetings_are_the_twelve_development_meetings() -> None:
    assert len(core.GOLD_MEETINGS) == 12
    assert {m[:6] for m in core.GOLD_MEETINGS} == {"ES2015", "IS1004", "TS3009"}


def test_the_cue_pool_is_disjoint_from_the_gold_meetings() -> None:
    assert not set(core.CUE_MEETINGS) & set(core.GOLD_MEETINGS)


@pytest.mark.parametrize(
    "meeting", ["TS3012a", "TS3012d", "TS3005a", "ES2002a", "IB4003", "nonsense", ""]
)
def test_an_ineligible_meeting_is_refused(meeting: str) -> None:
    with pytest.raises(core.MeetingRefused):
        core.require_eligible(meeting)


def test_ts3012_is_refused_with_its_reason() -> None:
    with pytest.raises(core.MeetingRefused, match="1556"):
        core.require_eligible("TS3012b")


@pytest.mark.parametrize("meeting", ["ES2015a", "IS1004d", "TS3009c", "IS1005b"])
def test_an_eligible_meeting_passes(meeting: str) -> None:
    assert core.require_eligible(meeting) == meeting


# --- value check ------------------------------------------------------------


def test_states_matches_a_whole_token_sequence_only() -> None:
    assert core.states("The cap is 12.50 euros.", ["12.50"])
    assert not core.states("The cap is 112.50 euros.", ["12.50"])
    assert core.states("a maximum of twelve fifty euros", ["twelve fifty"])
    assert not core.states("a maximum of twelve euros", ["twelve fifty"])


def test_states_ignores_case_and_punctuation() -> None:
    assert core.states("Kinetic-Battery, please", ["kinetic battery"])


def test_states_with_no_forms_is_false() -> None:
    assert not core.states("anything", [])


def test_classify_value_four_ways() -> None:
    forms, distractors = ["12.50"], ["1.20"]
    assert core.classify_value("budget cap is 12.50", forms, distractors) == "correct"
    assert core.classify_value("cost is 1.20", forms, distractors) == "wrong"
    assert core.classify_value("12.50 against 1.20", forms, distractors) == "mixed"
    assert core.classify_value("something was capped", forms, distractors) == "missing"


def test_split_decision_separates_the_statement_from_the_quoted_evidence() -> None:
    record = {
        "title": "Use rubber",
        "description": "case: Use rubber for the case.",
        "body": "Use rubber for the case.\n\n> okay so we'll use rubber",
    }
    statement, evidence = core.split_decision(record)
    assert "rubber for the case" in statement
    assert ">" not in statement
    assert "okay so we'll use rubber" not in statement
    assert evidence == "okay so we'll use rubber"


def test_split_decision_prefers_an_explicit_evidence_field() -> None:
    record = {"title": "t", "description": "d", "body": "b", "evidence": "the line"}
    assert core.split_decision(record)[1] == "the line"


# --- commitment cue ---------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "So we'll go with the kinetic battery.",
        "Let's use rubber.",
        "It's going to be a television remote.",
        "We agreed on twenty five euros.",
        "The cost has to be under twelve fifty.",
    ],
)
def test_commitment_cue_fires_on_commitment_language(line: str) -> None:
    assert core.commitment_cue(line)


@pytest.mark.parametrize(
    "line",
    [
        "What about a banana shape?",
        "Maybe a solar cell.",
        "Twenty five euros.",
        "Should we go with rubber?",
        "",
    ],
)
def test_commitment_cue_is_silent_without_it(line: str) -> None:
    assert not core.commitment_cue(line)


def test_a_question_never_carries_the_cue_even_with_a_cue_word() -> None:
    assert not core.commitment_cue("Are we going to use rubber?")


# --- guard ------------------------------------------------------------------


def _rec(title: str, evidence: str | None, body: str | None = None) -> dict[str, Any]:
    quote = f"\n\n> {evidence}" if evidence else ""
    return {
        "title": title,
        "description": f"x: {title}",
        "body": body if body is not None else f"{title}{quote}",
    }


def test_apply_guard_drops_a_decision_without_a_cue_and_keeps_one_with() -> None:
    keep = _rec("a", "we'll go with it")
    drop = _rec("b", "maybe a banana")
    kept, dropped = core.apply_guard([keep, drop])
    assert kept == [keep]
    assert dropped == [drop]


def test_apply_guard_treats_missing_evidence_as_no_cue() -> None:
    bare = _rec("a", None)
    kept, dropped = core.apply_guard([bare])
    assert kept == []
    assert dropped == [bare]


# --- labels and the line key -----------------------------------------------


def test_line_key_ignores_case_speaker_and_punctuation() -> None:
    assert core.line_key("A: Okay, we'll use rubber.") == core.line_key(
        "okay we'll use rubber"
    )
    assert core.line_key("one thing") != core.line_key("another thing")


def test_validate_label_rejects_an_unknown_label() -> None:
    assert core.validate_label("committed") == "committed"
    with pytest.raises(ValueError, match="label"):
        core.validate_label("maybe")


# --- stage 0 gate -----------------------------------------------------------


def _cue_pool(
    committed_hit: int, committed_miss: int, prop_hit: int, prop_miss: int
) -> tuple[dict[str, str], dict[str, str]]:
    labels: dict[str, str] = {}
    texts: dict[str, str] = {}
    n = 0
    for count, label, text in (
        (committed_hit, "committed", "we'll go with it"),
        (committed_miss, "committed", "rubber"),
        (prop_hit, "open", "we'll see about it"),
        (prop_miss, "discarded", "maybe not"),
    ):
        for _ in range(count):
            key = f"IS1002b#{n}"
            labels[key] = label
            texts[key] = text
            n += 1
    return labels, texts


def test_stage0_passes_when_the_cue_keeps_commitments_and_rejects_proposals() -> None:
    labels, texts = _cue_pool(18, 2, 5, 15)
    gate = core.stage0_gate(labels, texts)
    assert gate["sensitivity"] == pytest.approx(0.9)
    assert gate["specificity"] == pytest.approx(0.75)
    assert gate["verdict"] == "PASS"


def test_stage0_fails_when_the_cue_loses_commitments() -> None:
    labels, texts = _cue_pool(10, 10, 2, 18)
    assert core.stage0_gate(labels, texts)["verdict"] == "FAIL"


def test_stage0_is_underpowered_below_the_label_floor() -> None:
    labels, texts = _cue_pool(5, 0, 5, 0)
    assert core.stage0_gate(labels, texts)["verdict"] == "UNDERPOWERED"


def test_stage0_ignores_none_labelled_lines() -> None:
    labels, texts = _cue_pool(18, 2, 5, 15)
    labels["IS1002b#999"] = "none"
    texts["IS1002b#999"] = "um"
    assert core.stage0_gate(labels, texts)["committed"] == 20


# --- value gold -------------------------------------------------------------


def _gold_doc() -> dict[str, Any]:
    return {
        "version": 1,
        "items": [
            {
                "id": "ES2015a.elana.s.15",
                "meeting": "ES2015a",
                "turns": [3, 4],
                "kind": "amount",
                "forms": ["12.50", "twelve fifty"],
                "distractors": ["25"],
                "exclude": False,
            },
            {
                "id": "ES2015a.elana.s.11",
                "meeting": "ES2015a",
                "turns": [1],
                "kind": "choice",
                "forms": [],
                "distractors": [],
                "exclude": True,
            },
        ],
    }


def test_load_value_gold_marks_an_excluded_item_recall_only() -> None:
    items = core.load_value_gold(_gold_doc())
    assert [i.valued for i in items] == [True, False]


def test_load_value_gold_refuses_an_ineligible_meeting() -> None:
    doc = _gold_doc()
    doc["items"][0]["meeting"] = "TS3012a"
    with pytest.raises(core.MeetingRefused):
        core.load_value_gold(doc)


def test_load_value_gold_refuses_a_valued_item_with_no_forms() -> None:
    doc = _gold_doc()
    doc["items"][0]["forms"] = []
    with pytest.raises(ValueError, match="forms"):
        core.load_value_gold(doc)


def test_load_value_gold_refuses_a_distractor_equal_to_a_form() -> None:
    doc = _gold_doc()
    doc["items"][0]["distractors"] = ["12.50"]
    with pytest.raises(ValueError, match="distractor"):
        core.load_value_gold(doc)


def test_load_value_gold_refuses_a_duplicate_id() -> None:
    doc = _gold_doc()
    doc["items"][1]["id"] = doc["items"][0]["id"]
    with pytest.raises(ValueError, match="duplicate"):
        core.load_value_gold(doc)


# --- scoring one run --------------------------------------------------------

_ANCHORS = {
    "ES2015a": {
        3: "A: So the control will cost a maximum of twelve fifty euros to produce.",
        4: "B: Okay twelve fifty euros maximum, that's the cap.",
        1: "A: It will be a television remote control then.",
    }
}


def _decision(title: str, statement_value: str, evidence: str) -> dict[str, Any]:
    return {
        "title": title,
        "description": f"cost: {statement_value}",
        "body": f"{statement_value}\n\n> {evidence}",
    }


def _items() -> list[Any]:
    items: list[Any] = core.load_value_gold(_gold_doc())
    return items


def test_a_decision_stating_the_cap_scores_correct() -> None:
    run = {
        "ES2015a": [
            _decision(
                "cap",
                "The cost cap is 12.50 euros.",
                "the control will cost a maximum of twelve fifty euros to produce",
            )
        ]
    }
    score = core.score_run(run, _items(), _ANCHORS, {})
    assert score["items"]["ES2015a.elana.s.15"] == {
        "recalled": 1,
        "verdict": "correct",
    }


def test_a_decision_stating_the_wrong_value_scores_wrong() -> None:
    run = {
        "ES2015a": [
            _decision(
                "cost",
                "The selling price is 25 euros.",
                "the control will cost a maximum of twelve fifty euros to produce",
            )
        ]
    }
    score = core.score_run(run, _items(), _ANCHORS, {})
    assert score["items"]["ES2015a.elana.s.15"]["verdict"] == "wrong"


def test_the_value_only_in_the_quoted_evidence_does_not_count_as_stated() -> None:
    run = {
        "ES2015a": [
            _decision(
                "cost",
                "The cost was discussed at length.",
                "the control will cost a maximum of twelve fifty euros to produce",
            )
        ]
    }
    score = core.score_run(run, _items(), _ANCHORS, {})
    entry = score["items"]["ES2015a.elana.s.15"]
    assert entry == {"recalled": 1, "verdict": "missing"}
    assert score["evidence_only"] == 1


def test_an_unmatched_item_is_not_recalled_and_has_no_verdict() -> None:
    score = core.score_run({"ES2015a": []}, _items(), _ANCHORS, {})
    assert score["items"]["ES2015a.elana.s.15"] == {"recalled": 0, "verdict": None}


def test_the_best_verdict_among_matching_decisions_wins() -> None:
    evidence = "the control will cost a maximum of twelve fifty euros to produce"
    run = {
        "ES2015a": [
            _decision("a", "The price is 25 euros.", evidence),
            _decision("b", "The cap is twelve fifty euros.", evidence),
        ]
    }
    score = core.score_run(run, _items(), _ANCHORS, {})
    assert score["items"]["ES2015a.elana.s.15"]["verdict"] == "correct"


def test_an_excluded_item_is_recalled_but_has_no_value_verdict() -> None:
    run = {
        "ES2015a": [
            _decision(
                "tv",
                "It is a television remote.",
                "it will be a television remote control then",
            )
        ]
    }
    score = core.score_run(run, _items(), _ANCHORS, {})
    assert score["items"]["ES2015a.elana.s.11"] == {"recalled": 1, "verdict": None}


def test_the_guard_drops_a_decision_before_it_can_recall() -> None:
    run = {
        "ES2015a": [
            _decision(
                "tv", "It is a television remote.", "a television remote control then"
            )
        ]
    }
    score = core.score_run(run, _items(), _ANCHORS, {}, guard=True)
    assert score["items"]["ES2015a.elana.s.11"]["recalled"] == 0
    assert score["guard"] == {"kept": 0, "dropped": 1}


def test_score_run_counts_labelled_unlabelled_and_uncommitted_decisions() -> None:
    kept = _decision("a", "A thing is chosen here.", "we'll go with rubber")
    prop = _decision("b", "Another thing is chosen.", "what about a banana")
    stray = _decision("c", "A third thing is chosen.", "never labelled line")
    labels = {
        core.line_key("we'll go with rubber"): "committed",
        core.line_key("what about a banana"): "discarded",
    }
    score = core.score_run({"ES2015a": [kept, prop, stray]}, _items(), _ANCHORS, labels)
    assert score["decisions"] == {
        "total": 3,
        "labelled": 2,
        "unlabelled": 1,
        "uncommitted": 1,
        "discarded": 1,
        "none": 0,
    }
    assert score["by_meeting"]["ES2015a"] == {"labelled": 2, "uncommitted": 1}


# --- aggregation and the bootstrap -----------------------------------------


def test_item_clusters_pool_runs_per_item() -> None:
    scores = [
        {"items": {"i": {"recalled": 1, "verdict": "correct"}}},
        {"items": {"i": {"recalled": 1, "verdict": "wrong"}}},
        {"items": {"i": {"recalled": 0, "verdict": None}}},
    ]
    assert core.item_clusters(scores, "recall") == {"i": (2, 3)}
    assert core.item_clusters(scores, "value") == {"i": (1, 2)}


def test_meeting_clusters_pool_uncommitted_over_labelled_and_skip_empty() -> None:
    scores = [
        {
            "by_meeting": {
                "ES2015a": {"labelled": 4, "uncommitted": 1},
                "IS1004a": {"labelled": 0, "uncommitted": 0},
            }
        },
        {"by_meeting": {"ES2015a": {"labelled": 2, "uncommitted": 2}}},
    ]
    assert core.meeting_clusters(scores) == {"ES2015a": (3, 6)}


def test_ratio_bootstrap_is_deterministic_for_a_seed() -> None:
    a = {f"i{n}": (n % 2, 1) for n in range(20)}
    b = {f"i{n}": (1, 1) for n in range(20)}
    first = core.ratio_bootstrap(a, b, seed=7, reps=500)
    second = core.ratio_bootstrap(a, b, seed=7, reps=500)
    assert first == second
    assert first["diff"] == pytest.approx(0.5 - 1.0)
    assert first["lo"] <= first["diff"] <= first["hi"]


def test_ratio_bootstrap_uses_only_common_clusters() -> None:
    a = {"x": (1, 1), "y": (0, 1)}
    b = {"x": (1, 1)}
    assert core.ratio_bootstrap(a, b, seed=1, reps=50)["clusters"] == 1


def test_ratio_bootstrap_of_identical_arms_straddles_zero() -> None:
    a = {f"i{n}": (n % 2, 1) for n in range(30)}
    result = core.ratio_bootstrap(a, dict(a), seed=3, reps=500)
    assert result["diff"] == 0
    assert result["lo"] <= 0 <= result["hi"]


# --- the decision rule ------------------------------------------------------


def _boot(diff: float, lo: float, hi: float) -> dict[str, float]:
    return {"diff": diff, "lo": lo, "hi": hi, "clusters": 30}


def test_prompt_is_adopted_when_recall_holds_and_value_fidelity_clears_the_bar() -> (
    None
):
    verdict = core.decide_prompt(
        recall=_boot(-0.01, -0.04, 0.02), value=_boot(0.15, 0.05, 0.25)
    )
    assert verdict == "ADOPT"


def test_prompt_is_rejected_when_recall_is_clearly_lost() -> None:
    verdict = core.decide_prompt(
        recall=_boot(-0.10, -0.15, -0.07), value=_boot(0.20, 0.10, 0.30)
    )
    assert verdict == "REJECT"


def test_prompt_is_rejected_when_the_value_gain_cannot_reach_the_bar() -> None:
    verdict = core.decide_prompt(
        recall=_boot(0.0, -0.02, 0.02), value=_boot(0.01, -0.03, 0.05)
    )
    assert verdict == "REJECT"


def test_prompt_is_inconclusive_when_the_interval_straddles_the_bar() -> None:
    verdict = core.decide_prompt(
        recall=_boot(0.0, -0.02, 0.02), value=_boot(0.08, -0.05, 0.20)
    )
    assert verdict == "INCONCLUSIVE"


def test_guard_needs_the_stage0_pass_to_be_run_at_all() -> None:
    verdict = core.decide_guard(
        gate="FAIL", recall=_boot(0, -0.01, 0.01), uncommitted=_boot(-0.2, -0.3, -0.1)
    )
    assert verdict == "NOT RUN"


def test_guard_is_adopted_when_proposals_fall_and_recall_holds() -> None:
    verdict = core.decide_guard(
        gate="PASS",
        recall=_boot(-0.02, -0.04, 0.0),
        uncommitted=_boot(-0.12, -0.2, -0.05),
    )
    assert verdict == "ADOPT"


def test_guard_is_rejected_when_it_costs_recall() -> None:
    verdict = core.decide_guard(
        gate="PASS", recall=_boot(-0.2, -0.3, -0.1), uncommitted=_boot(-0.3, -0.4, -0.2)
    )
    assert verdict == "REJECT"


def test_guard_is_inconclusive_when_the_reduction_is_uncertain() -> None:
    verdict = core.decide_guard(
        gate="PASS", recall=_boot(0, -0.02, 0.02), uncommitted=_boot(-0.06, -0.14, 0.02)
    )
    assert verdict == "INCONCLUSIVE"


def test_prompt_needs_the_point_gain_as_well_as_a_positive_interval() -> None:
    verdict = core.decide_prompt(
        recall=_boot(0.0, -0.02, 0.02), value=_boot(0.06, 0.02, 0.14)
    )
    assert verdict == "INCONCLUSIVE"


def test_guard_needs_the_point_drop_as_well_as_a_negative_interval() -> None:
    verdict = core.decide_guard(
        gate="PASS",
        recall=_boot(0, -0.02, 0.02),
        uncommitted=_boot(-0.03, -0.045, -0.01),
    )
    assert verdict == "REJECT"


def test_stage0_fails_when_the_cue_does_not_reject_proposals() -> None:
    labels, texts = _cue_pool(20, 0, 20, 0)
    gate = core.stage0_gate(labels, texts)
    assert gate["specificity"] == 0
    assert gate["verdict"] == "FAIL"


def test_score_run_counts_valued_and_correct_items_for_the_unconditional_rate() -> None:
    run = {
        "ES2015a": [
            _decision(
                "cap",
                "The cost cap is 12.50 euros.",
                "the control will cost a maximum of twelve fifty euros to produce",
            )
        ]
    }
    score = core.score_run(run, _items(), _ANCHORS, {})
    assert score["value_hits"] == {"valued": 1, "correct": 1}


def test_the_prompt_clause_replaces_exactly_one_bullet_of_the_shipped_prompt() -> None:
    shipped = (
        _REPO_ROOT / "src" / "openkos" / "prompts" / "extraction" / "decision_pass.md"
    ).read_text(encoding="utf-8")
    clause = (_DIR / "prompt_value_clause.md").read_text(encoding="utf-8").strip()
    bullets = [line for line in shipped.splitlines() if line.startswith("- value:")]
    assert len(bullets) == 1
    assert clause.startswith("- value:")
    assert "\n" not in clause
    # The clause keeps the shipped bullet's own words and only adds to them.
    assert clause.startswith(bullets[0].rstrip("."))
