"""Absence guards and service-level tests for the `reindex` application-service
extraction (issue #1168), the `test_ingest_service_seams.py` pattern.

The names that moved off `openkos.cli.main` are deliberately never aliased
back, so a stale `monkeypatch.setattr("openkos.cli.main.<name>", ...)` raises
`AttributeError` under pytest's default `raising=True` instead of silently
patching a name nothing reads.
"""

import inspect
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from openkos import config
from openkos.application import reindex_service
from openkos.cli import main as cli_main
from openkos.llm.base import BackendHostLocality, BackendUnavailable
from openkos.state.reindex import ReindexReport
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY, make_locked_error


@pytest.mark.parametrize("name", ["_LOCK_CONTENTION_MSG", "VecUnavailable"])
def test_moved_reindex_names_no_longer_live_on_cli_main(name: str) -> None:
    assert not hasattr(cli_main, name)


def test_the_moved_names_live_in_the_application_layer() -> None:
    assert callable(reindex_service.reindex_workspace)
    assert issubclass(reindex_service.LockContention, reindex_service.ReindexRefused)
    assert "{command}" in reindex_service.LOCK_CONTENTION_TEMPLATE


def test_reindex_verb_is_a_thin_adapter_that_delegates_to_the_service() -> None:
    source = inspect.getsource(cli_main.reindex)
    assert "reindex_service.reindex_workspace(" in source
    assert "reindex_module" not in source
    assert "sqlite_graph" not in source
    assert "sqlite3" not in source


# -- The service, without the CLI -------------------------------------------


class _Embedder:
    locality = LOCAL_BACKEND_LOCALITY

    def embed(self, texts: object) -> list[list[float]]:
        return []


class _Recorder:
    """A `ReindexObserver` that records the order of what it was told."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.previous: str | None = "unset"
        self.localities: list[BackendHostLocality] = []

    def embedder_ready(self, locality: BackendHostLocality, cfg: object) -> None:
        self.events.append("embedder_ready")
        self.localities.append(locality)

    def progress_callback(self) -> None:
        return None

    def vectors_indexed(
        self, report: ReindexReport, previous_model_tag: str | None, cfg: object
    ) -> None:
        self.events.append("vectors_indexed")
        self.previous = previous_model_tag


class _Store:
    def __init__(self, tag: str | None) -> None:
        self._tag = tag

    def __enter__(self) -> "_Store":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read_model_tag(self) -> str | None:
        return self._tag


def _ports(store: _Store | Callable[..., object]) -> reindex_service.ReindexPorts:
    return reindex_service.ReindexPorts(
        embed_client=lambda cfg: _Embedder(),
        open_vector_store=lambda path: store,  # type: ignore[arg-type, return-value]
        open_proximity=lambda path: None,
        local_exemption=lambda client, cfg: False,
    )


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "bundle").mkdir()
    (tmp_path / "bundle" / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / "bundle" / "log.md").write_text("# Log\n", encoding="utf-8")
    (tmp_path / "openkos.yaml").write_text("model: llama3.1\n", encoding="utf-8")
    return tmp_path


def test_a_non_workspace_root_is_refused_with_the_exact_text(tmp_path: Path) -> None:
    with pytest.raises(reindex_service.NotAWorkspace) as caught:
        reindex_service.reindex_workspace(
            tmp_path, force=False, ports=_ports(_Store(None)), observer=_Recorder()
        )
    reason = config.require_workspace(tmp_path)
    assert caught.value.message == f"openkos reindex: refusing to run -- {reason}."


def test_the_summary_reaches_the_observer_before_the_graph_write_and_carries_the_old_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    report = ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0)
    monkeypatch.setattr(
        "openkos.application.reindex_service.reindex_module.reindex",
        lambda *a, **k: report,
    )
    observer = _Recorder()

    def _graph_fails(*args: object, **kwargs: object) -> None:
        observer.events.append("graph")
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        "openkos.application.reindex_service.sqlite_graph.reindex_graph",
        _graph_fails,
    )

    with pytest.raises(reindex_service.GraphWriteFailed) as caught:
        reindex_service.reindex_workspace(
            root, force=False, ports=_ports(_Store("old#tag")), observer=observer
        )

    assert observer.events == ["embedder_ready", "vectors_indexed", "graph"]
    assert observer.previous == "old#tag"
    assert observer.localities == [LOCAL_BACKEND_LOCALITY]
    assert caught.value.message == (
        "openkos reindex: failed while writing the graph index -- disk I/O error."
    )


def test_a_locked_graph_store_is_lock_contention_not_a_graph_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)
    monkeypatch.setattr(
        "openkos.application.reindex_service.reindex_module.reindex",
        lambda *a, **k: ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0),
    )

    def _locked(*args: object, **kwargs: object) -> None:
        raise make_locked_error()

    monkeypatch.setattr(
        "openkos.application.reindex_service.sqlite_graph.reindex_graph", _locked
    )

    with pytest.raises(reindex_service.LockContention) as caught:
        reindex_service.reindex_workspace(
            root, force=True, ports=_ports(_Store(None)), observer=_Recorder()
        )
    assert caught.value.message == reindex_service.LOCK_CONTENTION_TEMPLATE.format(
        command="reindex"
    )


def test_an_unreachable_backend_refusal_names_the_remedy_and_the_doctor_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)

    def _unavailable(*args: object, **kwargs: object) -> None:
        raise BackendUnavailable("nobody home")

    monkeypatch.setattr(
        "openkos.application.reindex_service.reindex_module.reindex", _unavailable
    )

    with pytest.raises(reindex_service.BackendNotReachable) as caught:
        reindex_service.reindex_workspace(
            root, force=False, ports=_ports(_Store(None)), observer=_Recorder()
        )
    assert caught.value.message == (
        "openkos reindex: failed -- nobody home. Start it with `ollama serve`, "
        "then try again. Or run `openkos doctor` to diagnose the environment."
    )


def test_a_non_lock_operational_error_is_re_raised_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _workspace(tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        "openkos.application.reindex_service.reindex_module.reindex", _boom
    )
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        reindex_service.reindex_workspace(
            root, force=False, ports=_ports(_Store(None)), observer=_Recorder()
        )
