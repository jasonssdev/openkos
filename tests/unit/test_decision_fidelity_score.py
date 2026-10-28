"""`evals/decision_fidelity/score_fidelity.py`: pooling runs into arms, the
registered contrasts and the report (#1649), over hand-built run scores.
"""

import importlib.util
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
score = _load("score_fidelity", "score_fidelity.py")

ITEMS = 30


def _run(
    verdicts: list[str | None],
    *,
    unlabelled: int = 0,
    labelled: int = 10,
    uncommitted: int = 3,
) -> dict[str, Any]:
    return {
        "items": {
            f"i{n}": {"recalled": int(v is not None or n % 2 == 0), "verdict": v}
            for n, v in enumerate(verdicts)
        },
        "evidence_only": 0,
        "value_hits": {
            "valued": len(verdicts),
            "correct": sum(v == "correct" for v in verdicts),
        },
        "decisions": {
            "total": labelled + unlabelled,
            "labelled": labelled,
            "unlabelled": unlabelled,
            "uncommitted": uncommitted,
            "discarded": 1,
            "none": 0,
        },
        "by_meeting": {
            f"ES2015{m}": {"labelled": labelled // 2, "uncommitted": uncommitted // 2}
            for m in "ab"
        },
        "guard": {"kept": labelled, "dropped": 0},
    }


def _arm(good: int, runs: int, **kwargs: Any) -> list[dict[str, Any]]:
    verdicts: list[str | None] = [*(["correct"] * good), *(["wrong"] * (ITEMS - good))]
    return [_run(verdicts, **kwargs) for _ in range(runs)]


def test_summarize_arm_pools_recall_value_and_proposals() -> None:
    summary = score.summarize_arm(_arm(15, 3))
    assert summary["runs"] == 3
    assert summary["value_fidelity"] == pytest.approx(0.5)
    assert summary["value_hit_rate"] == pytest.approx(0.5)
    assert summary["uncommitted_rate"] == pytest.approx(0.3)
    assert summary["unlabelled"] == 0


def test_a_prompt_that_fixes_values_without_losing_recall_is_adopted() -> None:
    report = score.compare(
        baseline=_arm(6, 15), prompt=_arm(24, 15), gate="UNDERPOWERED", seed=1, reps=400
    )
    assert report["prompt"]["verdict"] == "ADOPT"
    assert report["guard"]["verdict"] == "NOT RUN"


def test_a_prompt_with_no_gain_is_rejected() -> None:
    report = score.compare(
        baseline=_arm(15, 15), prompt=_arm(15, 15), gate="PASS", seed=1, reps=400
    )
    assert report["prompt"]["verdict"] == "REJECT"


def test_fewer_than_the_registered_runs_is_stamped_partial() -> None:
    report = score.compare(
        baseline=_arm(6, 5), prompt=_arm(24, 5), gate="PASS", seed=1, reps=200
    )
    assert report["partial"] is True
    assert score.PARTIAL_STAMP in score.render(report)


def test_the_registered_run_count_is_not_partial() -> None:
    report = score.compare(
        baseline=_arm(6, 15), prompt=_arm(24, 15), gate="PASS", seed=1, reps=200
    )
    assert report["partial"] is False


def test_unlabelled_lines_make_the_guard_verdict_incomplete() -> None:
    report = score.compare(
        baseline=_arm(6, 15, unlabelled=2),
        prompt=_arm(24, 15),
        gate="PASS",
        seed=1,
        reps=200,
    )
    assert report["guard"]["verdict"] == "INCOMPLETE"
    assert report["unlabelled"] > 0


def test_render_names_every_arm_and_both_verdicts() -> None:
    text = score.render(
        score.compare(
            baseline=_arm(6, 15), prompt=_arm(24, 15), gate="PASS", seed=1, reps=200
        )
    )
    for needle in ("baseline", "prompt", "ADOPT", "guard", "value_fidelity"):
        assert needle in text


def test_the_cli_self_test_passes() -> None:
    assert score.self_test() == 0
