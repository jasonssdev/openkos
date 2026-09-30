"""End-to-end coverage for the `openai-compatible` backend (issue #1057,
Phase 14 -- the enabling slice).

Every test here runs the REAL CLI/MCP surface against a `tmp_path`
workspace configured with `backend: openai-compatible` and a loopback
`base_url`. No test opens a real socket: the suite's autouse
`_offline_ollama_by_default` fixture (`tests/unit/conftest.py`) already
patches both `cli/main.py`'s and `mcp/server.py`'s `OllamaClient`/
`OpenAICompatibleClient` globals to their offline doubles, and the
session-wide fail-closed network guard (`tests/unit/conftest.py`) would
fail loudly if any code path reached for the network regardless. Each test
here additionally proves the RIGHT concrete class was reached -- not just
"no crash" -- by replacing `OllamaClient` with a raiser and
`OpenAICompatibleClient` with a spying subclass of the offline double, in
both adapter modules.
"""

from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from openkos.application import query as query_service_mod
from openkos.cli.main import app
from openkos.mcp import server as mcp_server
from openkos.mcp import tools as mcp_tools
from openkos.retrieval.answer import AnswerResult
from openkos.state import reindex as reindex_module
from tests.unit.conftest import OfflineOpenAICompatible

runner = CliRunner()


def _never_ollama(*args: object, **kwargs: object) -> None:
    raise AssertionError(
        "OllamaClient must never be constructed when backend=openai-compatible"
    )


class _SpyOpenAICompatible(OfflineOpenAICompatible):
    """`OfflineOpenAICompatible`, with every construction recorded.

    Proves the resolver actually dispatched to the `openai-compatible`
    factory (design Decision 4), not merely that the run didn't crash --
    a stub-based test that only checks `exit_code == 0` could pass even if
    the CLI silently fell back to `OllamaClient`."""

    instances: ClassVar[list["_SpyOpenAICompatible"]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        type(self).instances.clear()
        type(self).instances.append(self)


def _patch_backend_seams_to_spy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch both adapter modules' backend seams (design Decision 4's two
    binding sites): `OllamaClient` raises if constructed at all;
    `OpenAICompatibleClient` becomes the recording spy."""
    for module_path in ("openkos.cli.main", "openkos.mcp.server"):
        monkeypatch.setattr(f"{module_path}.OllamaClient", _never_ollama)
        monkeypatch.setattr(
            f"{module_path}.OpenAICompatibleClient", _SpyOpenAICompatible
        )


def _init_openai_compatible_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    base_url: str = "http://127.0.0.1:8080",
    embedding_base_url: str | None = None,
) -> None:
    """`openkos init` (Ollama-only, workspace-init spec), then hand-edit the
    written `openkos.yaml` to activate `backend`/`base_url` -- exactly what
    a real operator does, since `init` itself never writes these keys
    active (task 14.6)."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output

    extra = f"\nbackend: openai-compatible\nbase_url: {base_url}\n"
    if embedding_base_url is not None:
        extra += f"embedding_base_url: {embedding_base_url}\n"
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + extra, encoding="utf-8"
    )


def test_ingest_query_reindex_doctor_through_offline_double(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ingest`, `query`, `reindex`, `doctor`, and MCP `query` all construct
    `OpenAICompatibleClient` (patched to the offline double), reach ONLY
    that double, and `doctor` reports the backend host as `this machine`
    (design Decision 10; proposal Success Criteria for the
    `openai-compatible` end-to-end path)."""
    _init_openai_compatible_workspace(tmp_path, monkeypatch)
    _patch_backend_seams_to_spy(monkeypatch)
    (tmp_path / "notes.txt").write_text(
        "Some raw notes about stoicism.", encoding="utf-8"
    )

    ingest_result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])
    assert ingest_result.exit_code == 0, ingest_result.output
    assert (tmp_path / "raw" / "notes.txt").is_file()

    query_result = runner.invoke(app, ["query", "what is stoicism?"])
    assert query_result.exit_code == 0, query_result.output

    reindex_result = runner.invoke(app, ["reindex"])
    assert reindex_result.exit_code == 0, reindex_result.output

    doctor_result = runner.invoke(app, ["doctor"])
    assert "this machine" in doctor_result.stdout

    # MCP: the same resolver seam, exercised through the real `query` tool
    # (`mcp/tools.py::execute`), not the JSON-RPC transport -- the transport
    # is `mcp/server.py::Server`'s own concern, already covered elsewhere.
    ctx = mcp_server._build_context(tmp_path, expose_confidential=False)
    is_error, _structured = mcp_tools.execute(
        mcp_tools.REGISTRY["query"],
        {"question": "what is stoicism?"},
        ctx,
        progress=None,
    )
    assert not is_error

    assert _SpyOpenAICompatible.instances, (
        "OpenAICompatibleClient was never constructed -- the resolver did "
        "not reach the openai-compatible factory"
    )
    for client in _SpyOpenAICompatible.instances:
        assert client.locality.is_local, (
            f"expected the loopback base_url to classify as local, got "
            f"{client.locality!r}"
        )


def _write_confidential_doc(tmp_path: Path) -> None:
    path = tmp_path / "bundle" / "concepts" / "secret.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: Concept\ntitle: Secret\ndescription: ''\n"
        "sensitivity: confidential\n---\nsalary figures\n",
        encoding="utf-8",
    )


def _capture_reindex(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Mirrors `tests/unit/cli/test_confidential_local_exemption.py`'s own
    helper of the same name: replaces `state.reindex.reindex` with a
    recorder so `reindex`'s resolved `local_exemption` can be read back
    without a real embedding pass."""
    seen: dict[str, Any] = {}

    def _recorder(*args: object, **kwargs: object) -> reindex_module.ReindexReport:
        seen["local_exemption"] = kwargs.get("local_exemption")
        return reindex_module.ReindexReport(
            embedded=0, cache_hits=0, pruned=0, skipped=0
        )

    monkeypatch.setattr(reindex_module, "reindex", _recorder)
    return seen


def test_remote_base_url_withholds_confidential_for_both_purposes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A NON-loopback `base_url`/`embedding_base_url` withholds confidential
    material from BOTH the chat and the embed send, independently -- mirrors
    `test_confidential_local_exemption.py`'s existing Ollama
    `test_remote_backend_denies_the_exemption_at_every_seam`/
    `test_reindex_withholds_a_confidential_doc_and_says_so` pair, adapted to
    the `openai-compatible` backend (backend-selection spec: "Endpoint
    Resolution Precedence"; Threat Matrix "Confidential exemption")."""
    remote_base_url = "http://models.example.internal:8080"
    remote_embedding_base_url = "http://models.example.internal:8081"
    _init_openai_compatible_workspace(
        tmp_path,
        monkeypatch,
        base_url=remote_base_url,
        embedding_base_url=remote_embedding_base_url,
    )
    monkeypatch.setenv("OPENKOS_OPENAI_API_KEY", "s3cret-token")

    # --- chat purpose: query's seam receives local_exemption=False ---------
    calls: list[dict[str, Any]] = []

    def _spy_answer(*args: object, **kwargs: object) -> Any:
        calls.append(kwargs)
        return AnswerResult(
            answer="a fake answer",
            citations=[],
            fts_hit_count=0,
            llm_invoked=True,
            no_match_cause="none",
            skip_notices=[],
        )

    monkeypatch.setattr(query_service_mod, "answer", _spy_answer)

    query_result = runner.invoke(app, ["query", "a question"])

    assert query_result.exit_code == 0, query_result.output
    assert calls, "query never reached its seam"
    assert calls[0]["local_exemption"] is False
    assert "s3cret-token" not in query_result.stdout
    assert "s3cret-token" not in query_result.stderr

    # --- embed purpose: a real reindex withholds the confidential doc ------
    _write_confidential_doc(tmp_path)

    reindex_result = runner.invoke(app, ["reindex"])

    assert reindex_result.exit_code == 0, reindex_result.output
    assert "0 embedded" in reindex_result.output
    assert "1 withheld" in reindex_result.output
    assert "withheld from embedding" in reindex_result.output
    assert "s3cret-token" not in reindex_result.output
