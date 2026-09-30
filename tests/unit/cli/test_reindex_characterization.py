"""Byte-identity characterization tests for `openkos reindex` (issue #1168,
first slice), the `test_ingest_characterization.py` pattern.

`reindex`'s orchestration moved into `application/reindex_service.py`; the
CLI kept rendering, the exit-code mapping and the TTY progress hook. The
existing `test_reindex_cmd.py` assertions pin individual substrings; this
file additionally pins the COMPLETE `stdout` + `stderr` + exit-code stream of
a scenario matrix against goldens recorded on the tree BEFORE the move, so the
move cannot introduce or drop a stray byte (an ordering slip between the
summary and the graph write, a reworded refusal, a lost advisory).
"""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.cli.main import app
from openkos.llm.ollama import (
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.state.fts import FtsUnavailable
from openkos.state.reindex import ReindexReport
from openkos.state.vectorstore import VecUnavailable, open_vector_store
from tests.unit.cli.test_reindex_cmd import _FakeEmbedder
from tests.unit.conftest import make_locked_error

runner = CliRunner()

_GOLDENS_PATH = (
    Path(__file__).parent / "fixtures" / "reindex_characterization_goldens.json"
)
_GOLDENS: dict[str, dict[str, Any]] = json.loads(
    _GOLDENS_PATH.read_text(encoding="utf-8")
)

_REINDEX = "openkos.cli.main.reindex_module.reindex"
_GRAPH = "openkos.cli.main.sqlite_graph.reindex_graph"


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _fake_reindex(monkeypatch: pytest.MonkeyPatch, report: ReindexReport) -> None:
    monkeypatch.setattr(_REINDEX, lambda *a, **k: report)


def _raising(monkeypatch: pytest.MonkeyPatch, target: str, exc: Exception) -> None:
    def _raise(*args: object, **kwargs: object) -> None:
        raise exc

    monkeypatch.setattr(target, _raise)


def _seed_model_tag(tmp_path: Path, tag: str) -> None:
    layout = config.WorkspaceLayout(tmp_path)
    with open_vector_store(layout.vectors_db_path) as db:
        db.write_model_tag(tag)
        db.commit()


def _run(scenario: str) -> None:
    result = runner.invoke(app, ["reindex"])
    actual = {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }

    expected = _GOLDENS[scenario]
    assert actual["exit_code"] == expected["exit_code"], scenario
    assert actual["stdout"] == expected["stdout"], scenario
    assert actual["stderr"] == expected["stderr"], scenario


def test_missing_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _run("missing_workspace")


def test_success_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_reindex(
        monkeypatch, ReindexReport(embedded=3, cache_hits=2, pruned=1, skipped=0)
    )
    _run("success_counts")


def test_success_singular_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_reindex(
        monkeypatch, ReindexReport(embedded=0, cache_hits=1, pruned=0, skipped=0)
    )
    _run("success_singular_cache_hit")


def test_prune_skipped_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_reindex(
        monkeypatch,
        ReindexReport(
            embedded=1, cache_hits=0, pruned=0, skipped=2, prune_skipped=True
        ),
    )
    _run("prune_skipped_notice")


def test_embed_failed_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_reindex(
        monkeypatch,
        ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0, embed_failed=2),
    )
    _run("embed_failed_notice")


def test_withheld_confidential_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_reindex(
        monkeypatch,
        ReindexReport(
            embedded=1, cache_hits=0, pruned=0, skipped=0, withheld_confidential=2
        ),
    )
    _run("withheld_confidential_warning")


def test_model_reembed_complete_with_previous_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_model_tag(tmp_path, "old-model#chunk-v1")
    _fake_reindex(
        monkeypatch,
        ReindexReport(
            embedded=2,
            cache_hits=0,
            pruned=0,
            skipped=0,
            model_reembedded=True,
            effective_model_tag="bge-m3#chunk-v1",
            embed_calls=2,
        ),
    )
    _run("model_reembed_complete_with_previous_tag")


def test_model_reembed_complete_with_absent_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_reindex(
        monkeypatch,
        ReindexReport(
            embedded=2,
            cache_hits=0,
            pruned=0,
            skipped=0,
            model_reembedded=True,
            effective_model_tag="bge-m3#chunk-v1",
            embed_calls=2,
        ),
    )
    _run("model_reembed_complete_with_absent_tag")


def test_model_reembed_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_model_tag(tmp_path, "bge-m3#compose-v1")
    _fake_reindex(
        monkeypatch,
        ReindexReport(
            embedded=1,
            cache_hits=0,
            pruned=0,
            skipped=1,
            embed_failed=1,
            withheld_confidential=1,
            model_reembedded=True,
            effective_model_tag="bge-m3#chunk-v1",
            embed_calls=3,
        ),
    )
    _run("model_reembed_incomplete")


def test_nonlocal_embed_host_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("OLLAMA_HOST", "http://embed.example.invalid:11434")
    _fake_reindex(
        monkeypatch, ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0)
    )
    _run("nonlocal_embed_host_advisory")


def test_backend_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _raising(monkeypatch, _REINDEX, OllamaUnavailable("Ollama not reachable at x"))
    _run("backend_unavailable")


def test_model_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _raising(monkeypatch, _REINDEX, OllamaModelNotFound("nope"))
    _run("model_not_found")


def test_dimension_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _raising(
        monkeypatch,
        _REINDEX,
        OllamaEmbeddingDimensionMismatch("expected 1024, got 7."),
    )
    _run("dimension_mismatch")


def test_generic_backend_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _raising(monkeypatch, _REINDEX, OllamaError("boom"))
    _run("generic_backend_error")


def test_vec_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _raising(monkeypatch, _REINDEX, VecUnavailable("sqlite-vec missing"))
    _run("vec_unavailable")


def test_fts_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _raising(monkeypatch, _REINDEX, FtsUnavailable("fts5 missing"))
    _run("fts_unavailable")


def test_lock_contention_at_store_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _raising(monkeypatch, "openkos.cli.main.open_vector_store", make_locked_error())
    _run("lock_contention_at_store_open")


def test_lock_contention_in_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _raising(monkeypatch, _REINDEX, make_locked_error())
    _run("lock_contention_in_orchestrator")


def test_lock_contention_in_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_reindex(
        monkeypatch, ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0)
    )
    _raising(monkeypatch, _GRAPH, make_locked_error())
    _run("lock_contention_in_graph")


def test_graph_failure_keeps_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _fake_reindex(
        monkeypatch,
        ReindexReport(
            embedded=3, cache_hits=1, pruned=0, skipped=0, prune_skipped=True
        ),
    )
    _raising(monkeypatch, _GRAPH, sqlite3.OperationalError("disk I/O error"))
    _run("graph_failure_keeps_the_summary")


def test_malformed_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    _run("malformed_config")


def test_real_run_on_a_small_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No orchestrator fake: the real walk, FTS and graph stores end to end."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\ndescription: ''\n---\ndichotomyzz\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeEmbedder)
    _run("real_run_on_a_small_bundle")
