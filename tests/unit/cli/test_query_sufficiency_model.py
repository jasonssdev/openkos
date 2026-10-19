"""`query` runs its sufficiency check on the `sufficiency` task model (#1560).

Synthesis stays on `model:`; only the pre-synthesis check moves. These pin the
CLI wiring: which client reaches `answer()` for each side, that the
confidential local exemption needs EVERY chat client that receives the context
to be local, and that a check model that is not pulled degrades the check
instead of failing the query.
"""

from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import query as query_service_mod
from openkos.cli import main as main_mod
from openkos.cli.main import app
from openkos.llm.ollama import OllamaClient
from openkos.retrieval.answer import AnswerResult

runner = CliRunner()


def _answer_result(**overrides: Any) -> AnswerResult:
    fields: dict[str, Any] = {
        "answer": "a fake answer",
        "citations": [],
        "fts_hit_count": 0,
        "llm_invoked": True,
        "no_match_cause": "none",
        "skip_notices": [],
    }
    fields.update(overrides)
    return AnswerResult(**fields)


class _AnswerSpy:
    def __init__(self, result: AnswerResult | None = None) -> None:
        self.result = result or _answer_result()
        self.calls: list[dict[str, Any]] = []

    def __call__(self, question: str, **kwargs: Any) -> AnswerResult:
        self.calls.append(kwargs)
        return self.result


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: str = ""
) -> _AnswerSpy:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert runner.invoke(app, ["init"]).exit_code == 0
    if extra:
        path = tmp_path / "openkos.yaml"
        path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    spy = _AnswerSpy()
    monkeypatch.setattr(query_service_mod, "answer", spy)
    return spy


def _model(client: object) -> str:
    return cast(OllamaClient, client)._model


def test_query_builds_the_sufficiency_client_on_the_packaged_judge_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["query", "a question"])

    assert result.exit_code == 0, result.stderr
    call = spy.calls[0]
    assert _model(call["sufficiency_llm"]) == config.DEFAULT_JUDGE_MODEL
    assert _model(call["llm"]) == config.DEFAULT_MODEL


def test_the_null_opt_out_shares_the_synthesis_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _workspace(tmp_path, monkeypatch, "\nmodels:\n  sufficiency: null\n")

    result = runner.invoke(app, ["query", "a question"])

    assert result.exit_code == 0, result.stderr
    assert spy.calls[0]["sufficiency_llm"] is None


def test_a_disabled_check_builds_no_sufficiency_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _workspace(tmp_path, monkeypatch, "\nsufficiency_check: false\n")

    result = runner.invoke(app, ["query", "a question"])

    assert result.exit_code == 0, result.stderr
    assert spy.calls[0]["sufficiency_llm"] is None


class _StubLocality:
    def __init__(self, is_local: bool) -> None:
        self.is_local = is_local
        self.display_host = "stub"


class _StubChat:
    def __init__(self, *, is_local: bool) -> None:
        self.locality = _StubLocality(is_local)

    def chat(self, messages: object) -> str:  # pragma: no cover - never called
        raise AssertionError("answer() is patched")


@pytest.mark.parametrize(
    ("synthesis_local", "check_local", "expected"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
def test_the_exemption_needs_every_chat_client_to_be_local(
    synthesis_local: bool,
    check_local: bool,
    expected: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confidential bodies reach the check model too (#1560), so a remote
    check client withdraws the exemption even when synthesis is local."""
    spy = _workspace(tmp_path, monkeypatch)

    def fake_chat_client(cfg: config.Config, *, task: str | None = None) -> object:
        return _StubChat(
            is_local=check_local if task == "sufficiency" else synthesis_local
        )

    monkeypatch.setattr(main_mod, "_chat_client", fake_chat_client)

    result = runner.invoke(app, ["query", "a question"])

    assert result.exit_code == 0, result.stderr
    assert spy.calls[0]["local_exemption"] is expected


def test_a_missing_sufficiency_model_is_named_with_its_pull_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The query still answers (exit 0) and the notice names the check model
    and how to install it, plus the opt-out."""
    spy = _workspace(tmp_path, monkeypatch)
    spy.result = _answer_result(
        sufficiency_degraded=True, sufficiency_model_missing=True
    )

    result = runner.invoke(app, ["query", "a question"])

    assert result.exit_code == 0, result.stderr
    assert "sufficiency check" in result.stderr
    assert "gemma4:26b-a4b" in result.stderr
    assert "ollama pull gemma4:26b-a4b" in result.stderr
    assert "sufficiency: null" in result.stderr


def test_a_transient_degradation_keeps_the_generic_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = _workspace(tmp_path, monkeypatch)
    spy.result = _answer_result(sufficiency_degraded=True)

    result = runner.invoke(app, ["query", "a question"])

    assert "the sufficiency check could not run this call" in result.stderr
    assert "ollama pull" not in result.stderr
