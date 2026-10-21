"""The refusal-wording A/B the Ask screen's harness carries (#1583): an arm
with its own system prompt, its own runs projection, and the pre-registered
paired comparison against the control arm."""

import importlib.util
import pathlib
import sys
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_EVALS = _REPO_ROOT / "evals"


def _load(name: str, path: pathlib.Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ask = _load(
    "run_ask_model_screen_ab",
    _EVALS / "ask_model_screen" / "run_ask_model_screen.py",
)


def _row(
    item: str,
    label: str,
    lang: str | None,
    *,
    model: str = "m",
    run: int = 1,
    answer: str = "",
    cited: list[str] | None = None,
    attribution: str = "reported",
    anchored: int | None = None,
    fact_recall: float | None = None,
) -> Any:
    return ask.Row(
        model=model,
        run=run,
        set="es-es",
        item=item,
        label=label,
        anchored=anchored,
        blocks=3,
        gold_in_context=None,
        gold_hit=None,
        suff_outcome=None,
        sufficient=None,
        suff_elapsed_s=None,
        ans_outcome="ok",
        attribution=attribution,
        cited=cited or [],
        lang=lang,
        fact_recall=fact_recall,
        ans_elapsed_s=1.0,
        answer_chars=len(answer),
        answer=answer,
    )


def test_runs_path_without_an_arm_is_unchanged(tmp_path: pathlib.Path) -> None:
    assert ask.runs_path(tmp_path, "qwen3:8b") == tmp_path / "runs/runs-qwen3-8b.json"


def test_runs_path_with_an_arm_never_collides_with_the_control(
    tmp_path: pathlib.Path,
) -> None:
    arm = ask.runs_path(tmp_path, "qwen3:8b", arm="lang")
    assert arm == tmp_path / "runs/runs-qwen3-8b-arm-lang.json"
    assert ask.runs_path(tmp_path, "qwen3:8b", 11, arm="lang") == (
        tmp_path / "runs/runs-qwen3-8b-arm-lang-from11.json"
    )


def test_arm_prompt_fills_the_production_placeholders(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("a {{attribution_keyword}} b {{attribution_none}}", "utf-8")
    text = ask.arm_system_prompt(path)
    assert "{{" not in text
    assert text == f"a {ask._ATTRIBUTION_KEYWORD} b {ask._ATTRIBUTION_NONE}"


def test_arm_prompt_refuses_an_unknown_placeholder(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("a {{nope}}", "utf-8")
    with pytest.raises(SystemExit):
        ask.arm_system_prompt(path)


def test_synthesis_messages_use_the_arm_prompt_when_given() -> None:
    default = ask.synthesis_messages("ctx", None)
    assert default == ask._build_messages("ctx")
    arm = ask.synthesis_messages("ctx", "ARM")
    assert arm[0] == {"role": "system", "content": "ARM"}
    assert arm[1] == default[1]


def test_prompt_metrics_count_the_adjacent_language_and_the_english_phrase() -> None:
    rows = [
        _row("a1", ask.ADJACENT, "es"),
        _row("a2", ask.ADJACENT, "en", answer="The bundle does not cover this."),
        _row("a3", ask.ADJACENT, None),
        _row("g1", ask.GROUNDED, "es", cited=["d1"]),
    ]
    m = ask.prompt_metrics(rows)
    assert m["adjacent_spanish"] == pytest.approx(1 / 3)
    assert m["adjacent_english_phrase"] == pytest.approx(1 / 3)
    assert m["grounded_spanish"] == 1.0
    assert m["adjacent"] == 3


def _arm(
    adj_es: int, *, model: str = "m", n: int = 10, grounded_lang: str = "es"
) -> list[Any]:
    rows = [
        _row(
            f"a{i}",
            ask.ADJACENT,
            "es" if i < adj_es else "en",
            model=model,
        )
        for i in range(n)
    ]
    rows += [
        _row(
            f"g{i}",
            ask.GROUNDED,
            grounded_lang,
            model=model,
            cited=["d"],
            anchored=1,
            fact_recall=1.0,
        )
        for i in range(n)
    ]
    return rows


def test_verdict_adopts_when_gemma_improves_and_the_baseline_holds() -> None:
    control = {"qwen3:8b": _arm(9), "gemma4:26b-a4b": _arm(3)}
    treated = {"qwen3:8b": _arm(9), "gemma4:26b-a4b": _arm(9)}
    result = ask.prompt_verdict(control, treated, resamples=500)
    assert result["failed"] == []
    assert result["adopt"] is True


def test_verdict_rejects_when_gemma_stays_english() -> None:
    control = {"qwen3:8b": _arm(9), "gemma4:26b-a4b": _arm(3)}
    treated = {"qwen3:8b": _arm(9), "gemma4:26b-a4b": _arm(4)}
    result = ask.prompt_verdict(control, treated, resamples=500)
    assert result["adopt"] is False
    assert any(f.startswith("E1") for f in result["failed"])


def test_verdict_rejects_a_baseline_regression() -> None:
    control = {"qwen3:8b": _arm(9), "gemma4:26b-a4b": _arm(3)}
    treated = {"qwen3:8b": _arm(5), "gemma4:26b-a4b": _arm(9)}
    result = ask.prompt_verdict(control, treated, resamples=500)
    assert result["adopt"] is False
    assert any(f.startswith("N1") and "qwen3:8b" in f for f in result["failed"])


def test_verdict_rejects_a_grounded_language_regression() -> None:
    control = {"qwen3:8b": _arm(9), "gemma4:26b-a4b": _arm(3)}
    treated = {"qwen3:8b": _arm(9, grounded_lang="en"), "gemma4:26b-a4b": _arm(9)}
    result = ask.prompt_verdict(control, treated, resamples=500)
    assert result["adopt"] is False
    assert any(f.startswith("N2") for f in result["failed"])


def test_verdict_rejects_lost_fact_recall() -> None:
    control = {"qwen3:8b": _arm(9), "gemma4:26b-a4b": _arm(3)}
    worse = _arm(9)
    for r in worse:
        if r.anchored is not None:
            r.fact_recall = 0.0
    treated = {"qwen3:8b": worse, "gemma4:26b-a4b": _arm(9)}
    result = ask.prompt_verdict(control, treated, resamples=500)
    assert result["adopt"] is False
    assert any(f.startswith("N3") for f in result["failed"])


def test_verdict_refuses_unpaired_arms() -> None:
    control = {"qwen3:8b": _arm(9), "gemma4:26b-a4b": _arm(3)}
    treated = {"qwen3:8b": _arm(9, n=9), "gemma4:26b-a4b": _arm(9)}
    with pytest.raises(ValueError, match="not paired"):
        ask.prompt_verdict(control, treated, resamples=500)


def test_production_prompt_is_the_adopted_arm_byte_for_byte() -> None:
    arm = _EVALS / "ask_model_screen" / "candidate-answer-system-1583.md"
    shipped = _REPO_ROOT / "src/openkos/prompts/answer/system.md"
    assert shipped.read_bytes() == arm.read_bytes()


def test_the_refusal_asks_for_the_questions_language_and_quotes_no_sentence() -> None:
    text = ask._SYSTEM_PROMPT
    assert "in the language of the question" in text
    assert 'an honest "the compiled bundle' not in text
