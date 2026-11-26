"""`openkos ingest` reports the tokens each source consumed (#1807): one
greppable `tokens:` line per ingested source, single and batch, also when
extraction failed, and an honest "not reported" instead of zeros.

The chat backend is a fake that hands the `usage_sink` the CLI wires in the
same `ChatUsage` values a real client would.
"""

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config
from openkos.application import backends
from openkos.cli import main
from openkos.cli.main import app
from openkos.llm.base import ChatUsage, Message, UsageSummary
from openkos.llm.ollama import OllamaUnavailable
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()

_DECLINE = '{"extract": false}'


class _UsageLLM:
    """A chat backend that reports `usages[i]` to its sink on call `i`."""

    locality = LOCAL_BACKEND_LOCALITY

    def __init__(
        self,
        sink: Callable[[ChatUsage], None] | None,
        usages: Sequence[ChatUsage],
        reply: str = _DECLINE,
        raises: Exception | None = None,
    ) -> None:
        self._sink = sink
        self._usages = list(usages)
        self._reply = reply
        self._raises = raises
        self.calls = 0

    def chat(self, messages: Sequence[Message]) -> str:
        usage = self._usages[min(self.calls, len(self._usages) - 1)]
        self.calls += 1
        if self._sink is not None:
            self._sink(usage)
        if self._raises is not None:
            raise self._raises
        return self._reply


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    usages: Sequence[ChatUsage],
    *,
    reply: str = _DECLINE,
    raises: Exception | None = None,
) -> list[_UsageLLM]:
    built: list[_UsageLLM] = []

    def _factory(*args: Any, **kwargs: Any) -> _UsageLLM:
        client = _UsageLLM(kwargs.get("usage_sink"), usages, reply, raises)
        built.append(client)
        return client

    monkeypatch.setattr("openkos.cli.main.OllamaClient", _factory)
    return built


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _tokens_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip().startswith("tokens:")]


def test_single_ingest_prints_one_tokens_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    built = _patch(monkeypatch, [ChatUsage(100, 20, 150)])
    (tmp_path / "a.txt").write_text("Alpha notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "a.txt", "--auto"])

    assert result.exit_code == 0
    calls = sum(client.calls for client in built)
    assert calls >= 1
    noun = "call" if calls == 1 else "calls"
    assert _tokens_lines(result.stdout) == [
        f"  tokens: {calls} {noun}, prompt {100 * calls}, "
        f"completion {20 * calls}, total {150 * calls}"
    ]


def test_batch_ingest_prints_one_line_per_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _patch(monkeypatch, [ChatUsage(10, 2, 12)])
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.txt").write_text("Alpha notes.", encoding="utf-8")
    (notes / "b.txt").write_text("Beta notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    assert result.exit_code == 0
    assert len(_tokens_lines(result.stdout)) == 2


def test_each_source_has_its_own_tally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _patch(monkeypatch, [ChatUsage(10, 2, 12)])
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.txt").write_text("Alpha notes.", encoding="utf-8")
    (notes / "b.txt").write_text("Beta notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes", "--auto"])

    first, second = _tokens_lines(result.stdout)
    assert first == second


def test_extraction_failure_still_reports_the_billed_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _patch(monkeypatch, [ChatUsage(40, 9, 60)], reply="this is not json at all")
    (tmp_path / "a.txt").write_text("Alpha notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "a.txt", "--auto"])

    assert result.exit_code == 0
    lines = _tokens_lines(result.stdout)
    assert len(lines) == 1
    assert "total" in lines[0]


def test_a_call_that_raised_after_responding_is_still_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capped path: usage arrives, then the client raises."""
    _workspace(tmp_path, monkeypatch)
    _patch(monkeypatch, [ChatUsage(40, 9, 60)], raises=OllamaUnavailable("cut"))
    (tmp_path / "a.txt").write_text("Alpha notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "a.txt", "--auto"])

    assert result.exit_code == 0
    assert len(_tokens_lines(result.stdout)) == 1


def test_no_reported_usage_says_so_instead_of_printing_zeros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _patch(monkeypatch, [ChatUsage()])
    (tmp_path / "a.txt").write_text("Alpha notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "a.txt", "--auto"])

    lines = _tokens_lines(result.stdout)
    assert len(lines) == 1
    assert lines[0].startswith("  tokens: not reported by the backend (")
    assert "prompt" not in lines[0]
    assert " 0" not in lines[0]


def test_partial_reporting_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _patch(monkeypatch, [ChatUsage(10, 2, 12), ChatUsage()])
    (tmp_path / "a.txt").write_text("Alpha notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "a.txt", "--auto"])

    lines = _tokens_lines(result.stdout)
    assert len(lines) == 1
    # The first call reports, every later one does not.
    assert " of " in lines[0]
    assert "calls reported" in lines[0] or "call reported" in lines[0]


def test_a_declined_review_still_reports_the_tokens_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extraction runs BEFORE the confirmation question, so declining it
    does not un-spend the calls."""
    _workspace(tmp_path, monkeypatch)
    _patch(monkeypatch, [ChatUsage(10, 2, 12)])
    (tmp_path / "a.txt").write_text("Alpha notes.", encoding="utf-8")
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)

    result = runner.invoke(app, ["ingest", "a.txt"], input="n\n")

    assert result.exit_code != 0
    assert not (tmp_path / "bundle" / "sources" / "a.md").exists()
    assert len(_tokens_lines(result.stdout)) == 1


def test_no_model_call_prints_no_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#773's short-circuit: a converged re-ingest contacts no model."""
    _workspace(tmp_path, monkeypatch)
    reply = json.dumps(
        {
            "extract": True,
            "type": "Concept",
            "title": "Stoic Dichotomy Of Control",
            "description": "A framework for what is within our control.",
            "body": "Alpha notes.",
        }
    )
    _patch(monkeypatch, [ChatUsage(10, 2, 12)], reply=reply)
    (tmp_path / "a.txt").write_text("Alpha notes.\n", encoding="utf-8")
    assert runner.invoke(app, ["ingest", "a.txt", "--auto"]).exit_code == 0

    second = runner.invoke(app, ["ingest", "a.txt", "--auto"])

    assert second.exit_code == 0
    assert _tokens_lines(second.stdout) == []


def test_chat_client_passes_the_sink_only_when_given() -> None:
    seen: list[dict[str, Any]] = []

    def _factory(**kwargs: Any) -> object:
        seen.append(kwargs)
        return object()

    factories = backends.BackendFactories(ollama=_factory, openai_compatible=_factory)
    cfg = _cfg()

    def sink(usage: ChatUsage) -> None:
        return None

    backends.chat_client(cfg, factories=factories, environ={})
    backends.chat_client(cfg, factories=factories, environ={}, usage_sink=sink)
    assert "usage_sink" not in seen[0]
    assert seen[1]["usage_sink"] is sink


def _cfg() -> config.Config:
    return config.parse_config(Path(), "model: gemma3\n")


def test_format_helpers_cover_every_shape() -> None:
    fmt = main._format_token_usage
    assert fmt(UsageSummary(0, 0, 0, 0, 0)) is None
    assert fmt(UsageSummary(14, 14, 52310, 9120, 61430)) == (
        "  tokens: 14 calls, prompt 52310, completion 9120, total 61430"
    )
    assert fmt(UsageSummary(1, 1, 5, 2, 7)) == (
        "  tokens: 1 call, prompt 5, completion 2, total 7"
    )
    assert fmt(UsageSummary(14, 12, 5, 2, 7)) == (
        "  tokens: 12 of 14 calls reported, prompt 5, completion 2, total 7"
    )
    assert fmt(UsageSummary(14, 0, 0, 0, 0)) == (
        "  tokens: not reported by the backend (14 calls)"
    )
    assert fmt(UsageSummary(1, 0, 0, 0, 0)) == (
        "  tokens: not reported by the backend (1 call)"
    )
