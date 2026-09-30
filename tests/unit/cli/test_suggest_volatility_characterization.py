"""Byte-identity characterization tests for `openkos suggest-volatility`
(issue #1168, findings slice), the `test_reindex_characterization.py` pattern.

The verb's orchestration moved into `application/suggest_volatility_service.py`;
the CLI kept rendering and the exit-code mapping. This file pins the COMPLETE
`stdout` + `stderr` + exit code, and what the library seam was called with,
for every path of the verb against goldens recorded BEFORE the move.
"""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.llm.ollama import OllamaError, OllamaModelNotFound, OllamaUnavailable
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from tests.unit.cli.conftest import disable_local_exemption
from tests.unit.cli.golden_support import Goldens, normalise
from tests.unit.cli.test_suggest_volatility import _break_os_walk, _init_workspace

runner = CliRunner()

_GOLDENS = Goldens(
    Path(__file__).parent
    / "fixtures"
    / "suggest_volatility_characterization_goldens.json"
)


def _tier(
    type_name: str, tier: str | None, rationale: str = "steady"
) -> TierSuggestion:
    return TierSuggestion(
        type_name=type_name,
        current_default="slow",
        suggested_tier=tier,
        rationale=rationale,
    )


def _patch_suggest(
    monkeypatch: pytest.MonkeyPatch,
    batch: TierSuggestionBatch,
    calls: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    def _fake(bundle_dir: Path, **kwargs: Any) -> TierSuggestionBatch:
        on_progress = kwargs.pop("on_progress")
        llm = kwargs.pop("llm")
        calls.append(
            {
                "bundle_dir": normalise(str(bundle_dir), tmp_path),
                "llm_type": type(llm).__name__,
                "on_progress": "callable"
                if callable(on_progress)
                else repr(on_progress),
                **{key: repr(value) for key, value in sorted(kwargs.items())},
            }
        )
        if callable(on_progress):
            for index, suggestion in enumerate(batch.results, start=1):
                on_progress(index, len(batch.results), suggestion)
        return batch

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _fake)


def _run(
    scenario: str,
    tmp_path: Path,
    args: list[str],
    calls: list[dict[str, Any]] | None = None,
) -> None:
    result = runner.invoke(app, args)
    actual = {
        "exit_code": result.exit_code,
        "stdout": normalise(result.stdout, tmp_path),
        "stderr": normalise(result.stderr, tmp_path),
        "calls": calls if calls is not None else [],
    }
    _GOLDENS.check(scenario, actual)


def test_missing_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _run("missing_workspace", tmp_path, ["suggest-volatility"])


def test_malformed_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    _run("malformed_config", tmp_path, ["suggest-volatility"])


def test_real_library_on_an_empty_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run("empty_bundle_real_library", tmp_path, ["suggest-volatility"])


def test_no_types_from_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    _patch_suggest(monkeypatch, TierSuggestionBatch(results=[]), calls, tmp_path)
    _run("no_types", tmp_path, ["suggest-volatility"], calls)


def test_valid_and_degraded_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    batch = TierSuggestionBatch(
        results=[
            _tier("Person", "slow", "people change slowly"),
            _tier("Event", None, "malformed reply"),
            _tier("Decision", "fast", "decisions churn"),
        ]
    )
    _patch_suggest(monkeypatch, batch, calls, tmp_path)
    _run("valid_and_degraded", tmp_path, ["suggest-volatility"], calls)


def test_tty_progress_and_include_confidential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    calls: list[dict[str, Any]] = []
    _patch_suggest(
        monkeypatch,
        TierSuggestionBatch(results=[_tier("Person", "slow")]),
        calls,
        tmp_path,
    )
    result = runner.invoke(app, ["suggest-volatility", "--include-confidential"])
    actual = {
        "exit_code": result.exit_code,
        "stdout": normalise(result.stdout, tmp_path),
        # the elapsed-seconds clause varies run to run; pin the stable prefix
        "stderr_has_progress": "openkos suggest-volatility: suggesting type 1/1 - "
        in result.stderr,
        "calls": calls,
    }
    _GOLDENS.check("tty_progress_include_confidential", actual)


def test_pinned_rationale_language_and_local_exemption_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    with (tmp_path / "openkos.yaml").open("a", encoding="utf-8") as handle:
        handle.write("rationale_language: Spanish\n")
    calls: list[dict[str, Any]] = []
    _patch_suggest(monkeypatch, TierSuggestionBatch(results=[]), calls, tmp_path)
    _run("rationale_language", tmp_path, ["suggest-volatility"], calls)


def test_incomplete_walk_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)
    calls: list[dict[str, Any]] = []
    _patch_suggest(monkeypatch, TierSuggestionBatch(results=[]), calls, tmp_path)
    _run("incomplete_walk_warning", tmp_path, ["suggest-volatility"], calls)


@pytest.mark.parametrize(
    ("scenario", "error"),
    [
        ("partial_generic", OllamaError("boom")),
        ("partial_unavailable", OllamaUnavailable("Ollama not reachable at x")),
        ("partial_model_not_found", OllamaModelNotFound("nope")),
    ],
)
def test_partial_batch(
    scenario: str,
    error: OllamaError,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    batch = TierSuggestionBatch(
        results=[_tier("Person", "slow", "kept work")], failure=error, failed_index=2
    )
    _patch_suggest(monkeypatch, batch, calls, tmp_path)
    _run(scenario, tmp_path, ["suggest-volatility"], calls)


def test_first_type_fails_carries_no_empty_bundle_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    batch = TierSuggestionBatch(results=[], failure=OllamaError("down"), failed_index=1)
    _patch_suggest(monkeypatch, batch, calls, tmp_path)
    _run("first_type_fails", tmp_path, ["suggest-volatility"], calls)


@pytest.mark.parametrize(
    ("scenario", "error"),
    [
        ("raised_generic", OllamaError("boom")),
        ("raised_unavailable", OllamaUnavailable("Ollama not reachable at x")),
        ("raised_model_not_found", OllamaModelNotFound("nope")),
    ],
)
def test_raise_path(
    scenario: str,
    error: OllamaError,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    def _raise(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _raise)
    _run(scenario, tmp_path, ["suggest-volatility"])


def test_raise_path_on_an_openai_compatible_workspace_names_the_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raise-path remediation follows the configured backend, exactly as
    `suggest-relations` words it (#1175): no Ollama wording on an
    `openai-compatible` workspace."""
    _init_workspace(tmp_path, monkeypatch)
    with (tmp_path / "openkos.yaml").open("a", encoding="utf-8") as handle:
        handle.write("backend: openai-compatible\nbase_url: http://127.0.0.1:1/v1\n")
    monkeypatch.delenv("OPENKOS_OPENAI_API_KEY", raising=False)

    def _raise(*args: object, **kwargs: object) -> None:
        raise OllamaUnavailable("server down")

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _raise)
    result = runner.invoke(app, ["suggest-volatility"])
    assert result.exit_code == 1
    assert "Ollama" not in result.stderr
    assert "ollama" not in result.stderr
    assert "Start your OpenAI-compatible server at 127.0.0.1:1" in result.stderr
    _run("raised_unavailable_openai_compatible", tmp_path, ["suggest-volatility"])

    def _raise_missing(*args: object, **kwargs: object) -> None:
        raise OllamaModelNotFound("nope")

    monkeypatch.setattr("openkos.cli.main.suggest_volatility", _raise_missing)
    result = runner.invoke(app, ["suggest-volatility"])
    assert "ollama" not in result.stderr
    assert "Make sure your OpenAI-compatible server serves" in result.stderr
    _run("raised_model_not_found_openai_compatible", tmp_path, ["suggest-volatility"])
