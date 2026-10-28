"""`evals/decision_fidelity/`: reading AMI, the three blind sheets and the
import of a filled sheet (#1649), over a synthetic archive and transcripts.

The real archive and transcripts stay outside the repository; here a tiny
archive with the same file layout stands in, so the anchoring (`summlink` to
turn positions), the strata, the blinding and the import validation are
executed rather than trusted.
"""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_DIR = Path(__file__).resolve().parents[2] / "evals" / "decision_fidelity"
sys.path.insert(0, str(_DIR))


def _load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _DIR / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core = _load("fidelity_core", "fidelity_core.py")
ami = _load("fidelity_ami", "fidelity_ami.py")
build = _load("build_sheets", "build_sheets.py")

Workspace = tuple[Path, Path]


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    workspace: Workspace = build.make_synthetic(tmp_path)
    return workspace


def _archive(workspace: Workspace) -> Any:
    return ami.open_archive(workspace[0], verify=False)


def test_decision_sentences_anchor_by_summlink_and_skip_na(
    workspace: Workspace,
) -> None:
    anchored, unanchored = ami.decision_sentences(
        _archive(workspace), workspace[1], "ES2015a"
    )
    by_id = {s["id"]: s for s in anchored}
    assert by_id["ES2015a.elana.s.15"]["turns"] == [1, 2]
    assert by_id["ES2015a.elana.s.12"]["turns"] == [4]
    assert unanchored == ["ES2015a.elana.s.98"]
    assert "ES2015a.elana.s.99" not in by_id


def test_context_indices_merge_and_clip() -> None:
    assert ami.context_indices([1, 2], 8, 1) == [0, 1, 2, 3]
    assert ami.context_indices([0], 3, 2) == [0, 1, 2]
    assert ami.context_indices([], 3, 2) == []


def test_value_sheet_marks_anchors_and_is_deterministic(workspace: Workspace) -> None:
    sheet = ami.value_sheet(_archive(workspace), workspace[1], ["ES2015a"], seed=5)
    again = ami.value_sheet(_archive(workspace), workspace[1], ["ES2015a"], seed=5)
    assert sheet == again
    assert {i["id"] for i in sheet["items"]} == {
        "ES2015a.elana.s.15",
        "ES2015a.elana.s.12",
    }
    item = next(i for i in sheet["items"] if i["id"].endswith("s.15"))
    assert [t["i"] for t in item["turns"] if t["anchor"]] == [1, 2]
    assert sheet["unanchored"] == ["ES2015a.elana.s.98"]


def test_value_sheet_refuses_a_calibration_meeting(workspace: Workspace) -> None:
    with pytest.raises(core.MeetingRefused):
        ami.value_sheet(_archive(workspace), workspace[1], ["IS1002b"], seed=1)


def test_reading_a_ts3012_meeting_is_refused_before_any_file(tmp_path: Path) -> None:
    with pytest.raises(core.MeetingRefused, match="1556"):
        ami.load_turns(tmp_path, "TS3012a")


def test_cue_sheet_hides_the_stratum_and_draws_each_one(workspace: Workspace) -> None:
    sheet, strata = ami.cue_sheet(
        _archive(workspace), workspace[1], ["IS1002b"], seed=3, per_stratum=2
    )
    assert all("stratum" not in item for item in sheet["items"])
    assert set(strata.values()) == set(ami.STRATA)
    assert set(strata) == {i["id"] for i in sheet["items"]}
    again, _ = ami.cue_sheet(
        _archive(workspace), workspace[1], ["IS1002b"], seed=3, per_stratum=2
    )
    assert again == sheet


def test_cue_sheet_skips_backchannel(workspace: Workspace) -> None:
    sheet, _ = ami.cue_sheet(
        _archive(workspace), workspace[1], ["IS1002b"], seed=3, per_stratum=50
    )
    assert sheet["items"]
    assert all(
        len(core.tokens(i["text"])) >= ami.MIN_LINE_TOKENS for i in sheet["items"]
    )


def test_cue_sheet_refuses_a_gold_meeting(workspace: Workspace) -> None:
    with pytest.raises(core.MeetingRefused):
        ami.cue_sheet(
            _archive(workspace), workspace[1], ["ES2015a"], seed=1, per_stratum=2
        )


def _runs() -> list[dict[str, list[dict[str, Any]]]]:
    quote = "so we will use rubber for the case"
    a = {"title": "t", "description": "d", "body": f"Rubber.\n\n> {quote}"}
    b = {"title": "t2", "description": "d2", "body": f"Rubber again.\n\n> {quote}"}
    c = {"title": "t3", "description": "d3", "body": "x\n\n> the sky is blue today"}
    return [{"ES2015a": [a, c]}, {"ES2015a": [b]}]


def test_line_sheet_dedupes_across_arms_and_locates(workspace: Workspace) -> None:
    sheet = ami.line_sheet(_runs(), workspace[1], seed=2)
    assert len(sheet["items"]) == 2
    located = {i["text"]: i["located"] for i in sheet["items"]}
    assert located["so we will use rubber for the case"] is True
    assert located["the sky is blue today"] is False


def test_line_sheet_is_blind_to_the_arm(workspace: Workspace) -> None:
    sheet = ami.line_sheet(_runs(), workspace[1], seed=2)
    assert all(
        set(i) == {"id", "meeting", "text", "located", "turns"} for i in sheet["items"]
    )


def test_locate_line_finds_a_token_run_inside_a_turn() -> None:
    turns = ["hello there", "okay so we will use rubber for the case then"]
    assert ami.locate_line(turns, "we will use rubber") == 1
    assert ami.locate_line(turns, "") is None
    assert ami.locate_line(turns, "nothing like it at all") is None


def _filled_value(sheet: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in sheet["items"]:
        if item["id"].endswith("s.15"):
            out.append(
                {
                    "id": item["id"],
                    "kind": "amount",
                    "forms": ["12.50", "twelve fifty"],
                    "distractors": ["1.20"],
                    "exclude": False,
                    "note": "",
                }
            )
        else:
            out.append(
                {
                    "id": item["id"],
                    "kind": "choice",
                    "forms": ["rubber"],
                    "distractors": ["banana"],
                    "exclude": False,
                    "note": "",
                }
            )
    return out


def _value_sheet(workspace: Workspace) -> dict[str, Any]:
    sheet: dict[str, Any] = ami.value_sheet(
        _archive(workspace), workspace[1], ["ES2015a"], seed=5
    )
    return sheet


def test_import_value_roundtrips_into_a_valid_gold(workspace: Workspace) -> None:
    sheet = _value_sheet(workspace)
    items = core.load_value_gold(
        ami.import_value(sheet, _filled_value(sheet), "claude")
    )
    assert {i.item_id: i.turns for i in items}["ES2015a.elana.s.15"] == (1, 2)


def test_import_value_refuses_an_unanswered_item(workspace: Workspace) -> None:
    sheet = _value_sheet(workspace)
    with pytest.raises(ValueError, match="unanswered"):
        ami.import_value(sheet, _filled_value(sheet)[:1], "claude")


def test_import_value_refuses_a_duplicate_answer(workspace: Workspace) -> None:
    sheet = _value_sheet(workspace)
    filled = _filled_value(sheet)
    with pytest.raises(ValueError, match="twice"):
        ami.import_value(sheet, [*filled, filled[0]], "claude")


def test_import_value_refuses_an_id_not_on_the_sheet(workspace: Workspace) -> None:
    sheet = _value_sheet(workspace)
    stray = {"id": "ES2015a.nope", "kind": "choice", "forms": ["x"]}
    with pytest.raises(ValueError, match="not on the sheet"):
        ami.import_value(sheet, [*_filled_value(sheet), stray], "claude")


def test_import_value_refuses_an_unknown_kind(workspace: Workspace) -> None:
    sheet = _value_sheet(workspace)
    filled = _filled_value(sheet)
    filled[0]["kind"] = "vibes"
    with pytest.raises(ValueError, match="kind"):
        ami.import_value(sheet, filled, "claude")


def test_import_labels_validates_the_vocabulary(workspace: Workspace) -> None:
    sheet = ami.line_sheet(_runs(), workspace[1], seed=2)
    good = [{"id": i["id"], "label": "committed"} for i in sheet["items"]]
    assert set(ami.import_labels(sheet, good, "claude")["labels"].values()) == {
        "committed"
    }
    bad = [{"id": i["id"], "label": "sure"} for i in sheet["items"]]
    with pytest.raises(ValueError, match="label"):
        ami.import_labels(sheet, bad, "claude")


def test_markdown_rendering_carries_the_rubric_and_anchor_flags(
    workspace: Workspace,
) -> None:
    sheet = _value_sheet(workspace)
    text = ami.render_markdown(sheet)
    assert sheet["rubric"] in text
    assert "ANCHOR" in text


def test_a_value_gold_scores_a_run_end_to_end(workspace: Workspace) -> None:
    sheet = _value_sheet(workspace)
    items = core.load_value_gold(
        ami.import_value(sheet, _filled_value(sheet), "claude")
    )
    anchors = ami.load_anchors(workspace[1], ["ES2015a"])
    good = {
        "title": "Cost cap",
        "description": "cost: The cap is 12.50 euros.",
        "body": (
            "The cap is 12.50 euros.\n\n"
            "> So the control will cost a maximum of twelve fifty euros to produce."
        ),
    }
    score = core.score_run({"ES2015a": [good]}, items, anchors, {})
    assert score["items"]["ES2015a.elana.s.15"] == {"recalled": 1, "verdict": "correct"}
    assert score["items"]["ES2015a.elana.s.12"]["recalled"] == 0


def test_load_runs_refuses_an_ineligible_meeting() -> None:
    doc = {"arm": "x", "runs": [{"meetings": {"TS3012a": {"decisions": []}}}]}
    with pytest.raises(core.MeetingRefused):
        core.load_runs(doc)


def test_load_runs_returns_the_decisions_per_run() -> None:
    doc = {
        "arm": "x",
        "runs": [
            {"meetings": {"ES2015a": {"decisions": [{"title": "t"}]}}},
            {"meetings": {}},
        ],
    }
    assert core.load_runs(doc) == [{"ES2015a": [{"title": "t"}]}, {}]


def test_the_cli_self_test_passes() -> None:
    assert build.self_test() == 0


def test_dump_writes_stable_json(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    ami.dump(path, {"b": 1, "a": "é"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"b": 1, "a": "é"}
