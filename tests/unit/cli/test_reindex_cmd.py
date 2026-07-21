"""Unit tests for the `reindex` CLI command: thin wiring over
`state/reindex.py`.

`reindex` mirrors `query`'s D1 gate shape (bare `require_workspace`, no
Phase B, no confirm gate, no `--auto`) and its ordered Ollama error ladder,
substituting `VecUnavailable` for `FtsUnavailable`. Every test patches
`openkos.cli.main.reindex_module.reindex` (the orchestrator call) or
`openkos.cli.main.open_vector_store` -- zero network, zero real Ollama
process, zero real embedding calls. `open_vector_store` itself runs for
real against a `tmp_path` workspace where the successful-run/error-ladder
scenarios don't specifically fake it, since it only touches local SQLite
(no network).
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.llm.ollama import OllamaError, OllamaModelNotFound, OllamaUnavailable
from openkos.state.reindex import ReindexReport
from openkos.state.vectorstore import VecUnavailable

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def test_reindex_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `reindex` refuses (exit 1), prints the shared
    `require_workspace` reason under a `reindex`-specific prefix, and never
    calls the orchestrator (spec: Run outside a workspace refuses)."""
    monkeypatch.chdir(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos reindex: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_reindex_successful_run_prints_summary_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful run prints embedded/cache-hit/pruned/skipped counts and
    exits 0 (spec: Successful run prints a summary and exits 0)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(embedded=3, cache_hits=2, pruned=1, skipped=0)
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "3 embedded" in result.stdout
    assert "2 cache-hit" in result.stdout
    assert "1 pruned" in result.stdout
    assert "0 skipped" in result.stdout


def test_reindex_builds_ollama_client_from_configured_embedding_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reindex` builds the `Embedder` from the workspace's configured
    `embedding_model`, not a hardcoded value."""
    _init_workspace(tmp_path, monkeypatch)
    configured_embedding_model = "custom-embed:test"
    (tmp_path / "openkos.yaml").write_text(
        f"embedding_model: {configured_embedding_model}\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def _recording_reindex(
        bundle_dir: object, db: object, embedder: object, **kwargs: object
    ) -> ReindexReport:
        captured["embedder"] = embedder
        return ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    embedder = captured["embedder"]
    assert embedder._model == configured_embedding_model  # type: ignore[attr-defined]


def test_reindex_force_flag_is_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` is forwarded unchanged to the orchestrator."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_reindex(*args: object, **kwargs: object) -> ReindexReport:
        captured["kwargs"] = kwargs
        return ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex", "--force"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["force"] is True


def test_reindex_omitted_force_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--force` forwards `force=False`."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_reindex(*args: object, **kwargs: object) -> ReindexReport:
        captured["kwargs"] = kwargs
        return ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["force"] is False


def test_reindex_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator raising `OllamaUnavailable` is caught, printed as a
    friendly stderr message with `ollama serve` remediation, and exits 1
    with no raw traceback (spec: Ollama unreachable exits 1 with a clear
    message)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_unavailable(*args: object, **kwargs: object) -> ReindexReport:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _raise_unavailable)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_model_not_found_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator raising `OllamaModelNotFound` is caught, printed
    with the configured embedding model tag and an `ollama pull`
    remediation, and exits 1 with no raw traceback."""
    _init_workspace(tmp_path, monkeypatch)
    configured_embedding_model = "custom-embed:test"
    (tmp_path / "openkos.yaml").write_text(
        f"embedding_model: {configured_embedding_model}\n", encoding="utf-8"
    )

    def _raise_model_not_found(*args: object, **kwargs: object) -> ReindexReport:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", _raise_model_not_found
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert "is not installed" in result.stderr
    assert f"ollama pull {configured_embedding_model}" in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_generic_ollama_error_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain `OllamaError` (neither `OllamaUnavailable` nor
    `OllamaModelNotFound`) is caught, printed as a generic friendly message,
    and exits 1 with no raw traceback (spec: Error Ladder Mirrors query)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_generic(*args: object, **kwargs: object) -> ReindexReport:
        raise OllamaError("boom")

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _raise_generic)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == "openkos reindex: failed -- boom.\n"
    assert "Traceback" not in result.stderr


def test_reindex_vec_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`open_vector_store` raising `VecUnavailable` (`sqlite-vec` cannot be
    loaded) is caught, printed as a friendly stderr message, and exits 1
    with no raw traceback (spec: Vector extension unavailable exits 1 with
    a clear message)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_vec_unavailable(*args: object, **kwargs: object) -> None:
        raise VecUnavailable("the sqlite-vec extension could not be loaded")

    monkeypatch.setattr("openkos.cli.main.open_vector_store", _raise_vec_unavailable)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert "sqlite-vec" in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_malformed_config_maps_to_exit_one_before_calling_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard) is caught,
    printed as a friendly stderr message, exits 1 with no raw traceback, and
    the orchestrator is never reached."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos reindex: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert calls == []
