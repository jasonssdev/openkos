"""Unit tests for the `query` CLI command: the MVP-1 query chain entry point.

`query` is the read-only counterpart to `status`/`lint` (D1: bare
`require_workspace` gate, no Phase B, no confirm gate, no `--auto`), followed
by a Phase-A `read_config` guard (`except (OSError, ValueError)`, lint
parity) and a Phase-B `answer()` call guarded by `except (FtsUnavailable,
OllamaError)`. Every test patches `openkos.cli.main.answer` (D4) -- zero
network, zero real Ollama process, zero real FTS5 index.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.llm.ollama import OllamaUnavailable
from openkos.retrieval.answer import NO_MATCH, AnswerResult, Citation
from openkos.state.fts import FtsUnavailable

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def test_query_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `query` refuses (exit 1), prints the shared
    `require_workspace` reason under a `query`-specific prefix, and never
    calls `answer()` (spec: Run outside a workspace)."""
    monkeypatch.chdir(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos query: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_query_matching_answer_renders_citations_in_hit_rank_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace whose bundle answers the question renders the answer text
    followed by one `  → {concept_id} ({title})` line per citation, in the
    exact order `AnswerResult.citations` returns them, and exits 0 (spec:
    Matching answer with citations; Citation order matches the answer; Run
    inside a workspace)."""
    _init_workspace(tmp_path, monkeypatch)
    citation_one = Citation(concept_id="concepts/stoicism", title="Stoicism")
    citation_two = Citation(concept_id="concepts/epictetus", title="Epictetus")
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[citation_one, citation_two],
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Stoicism teaches the dichotomy of control.\n"
        "\n"
        "Citations:\n"
        "  → concepts/stoicism (Stoicism)\n"
        "  → concepts/epictetus (Epictetus)\n"
    )


def test_query_no_match_renders_answer_line_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-match `AnswerResult` (empty `citations`) renders only the
    no-match answer line, with no `Citations:` section, and exits 0 -- not
    an error (spec: Zero matching concepts)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(answer=NO_MATCH, citations=[])
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is nothing?"])

    assert result.exit_code == 0
    assert result.stdout == f"{NO_MATCH}\n"
    assert "Citations:" not in result.stdout


def test_query_limit_flag_is_forwarded_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit 3` is forwarded unchanged as `answer(..., limit=3)` (spec:
    Caller overrides the default limit)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["question"] = question
        captured["kwargs"] = kwargs
        return AnswerResult(answer=NO_MATCH, citations=[])

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?", "--limit", "3"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["bundle_dir"] == tmp_path / "bundle"
    assert kwargs["limit"] == 3
    assert captured["question"] == "what is stoicism?"


def test_query_omitted_limit_defaults_to_five(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--limit` forwards the default `limit=5` (spec: Caller omits
    `--limit`)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(answer=NO_MATCH, citations=[])

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["limit"] == 5


def test_query_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`answer()` raising `OllamaUnavailable` (an `OllamaError` subclass) is
    caught, printed as a friendly stderr message, and exits 1 with no raw
    traceback (spec: Ollama backend unreachable)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_unavailable(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.answer", _raise_unavailable)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos query: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "Traceback" not in result.stderr


def test_query_fts_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`answer()` raising `FtsUnavailable` is caught, printed as a friendly
    stderr message, and exits 1 with no raw traceback (spec: FTS index
    unavailable)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_fts_unavailable(*args: object, **kwargs: object) -> AnswerResult:
        raise FtsUnavailable("fts5 not compiled in")

    monkeypatch.setattr("openkos.cli.main.answer", _raise_fts_unavailable)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos query: failed -- ")
    assert "fts5 not compiled in" in result.stderr
    assert "Traceback" not in result.stderr


def test_query_malformed_config_maps_to_exit_one_before_calling_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard, D2 lint
    parity) is caught, printed as a friendly stderr message, exits 1 with no
    raw traceback, and `answer()` is never reached."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text("model: [unclosed\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos query: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert calls == []
