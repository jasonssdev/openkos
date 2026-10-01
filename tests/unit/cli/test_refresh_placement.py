"""Where a write's derived refresh runs relative to the workspace lock (#1137,
#1143, ADR-0036; tasks.md 1.8).

Every verb with a commit phase refreshes the derived stores the same way: the
FTS and graph projections (pure SQLite, no model call) with the lock HELD, the
embedding calls with it NOT held, and only the vector-store write under a brief
re-take of the lock. The lock is probed from inside the stubbed seams, by
trying to take it from the same process: `flock` on a second open file
description refuses even within one process.

Every "lock free" assertion is paired, in the same run, with a "lock held" one,
so "never held at all" (or "always held") cannot pass.
"""

import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli import main
from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.llm.base import EMBED_DIM
from openkos.state import reindex as reindex_module
from tests.unit.cli.commit_phase_support import (
    init_workspace,
    lock_is_free,
    simulate_tty,
    write_doc,
)
from tests.unit.cli.conftest import commit_pending_fixture_docs
from tests.unit.cli.test_commit_phase_maintenance_verbs import (
    _git_identity,  # noqa: F401 -- autouse fixture, re-exported into this module
    _seed_repair,
    _seed_sync_tags,
)
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()


class _Probe:
    """What each derived-refresh seam observed about the lock."""

    def __init__(self) -> None:
        self.fts: list[bool] = []
        self.graph: list[bool] = []
        self.embed: list[bool] = []


def _install_probe(monkeypatch: pytest.MonkeyPatch, root: Path) -> _Probe:
    probe = _Probe()
    real_fts = reindex_module._reindex_fts
    real_graph = sqlite_graph.reindex_graph

    def _fts(*args: object, **kwargs: object) -> object:
        probe.fts.append(lock_is_free(root))
        return real_fts(*args, **kwargs)  # type: ignore[arg-type]

    def _graph(*args: object, **kwargs: object) -> object:
        probe.graph.append(lock_is_free(root))
        return real_graph(*args, **kwargs)  # type: ignore[arg-type]

    class _Embedder:
        locality = LOCAL_BACKEND_LOCALITY

        def embed(self, texts: Sequence[str]) -> list[list[float]]:
            probe.embed.append(lock_is_free(root))
            return [[0.5] * EMBED_DIM for _ in texts]

    monkeypatch.setattr(reindex_module, "_reindex_fts", _fts)
    monkeypatch.setattr(sqlite_graph, "reindex_graph", _graph)
    monkeypatch.setattr(main, "_embed_client", lambda cfg: _Embedder())
    return probe


def _two_concepts(tmp_path: Path) -> None:
    write_doc(tmp_path, "concepts/a", {"type": "Concept", "title": "A"}, "Alpha.\n")
    write_doc(tmp_path, "concepts/b", {"type": "Concept", "title": "B"}, "Beta.\n")
    commit_pending_fixture_docs()


def _seed_normalize(tmp_path: Path) -> None:
    nfd = unicodedata.normalize("NFD", "café") + ".md"
    write_doc(tmp_path, nfd.removesuffix(".md"), {"type": "Concept", "title": "Cafe"})
    commit_pending_fixture_docs()


_Scenario = tuple[Callable[[Path], None], list[str], str]

_SCENARIOS: dict[str, _Scenario] = {
    "forget": (_two_concepts, ["forget", "concepts/b", "--auto"], ""),
    "relate": (
        _two_concepts,
        ["relate", "concepts/a", "references", "concepts/b", "--auto"],
        "",
    ),
    "set-sensitivity": (
        _two_concepts,
        ["set-sensitivity", "concepts/a", "confidential", "--auto"],
        "",
    ),
    "merge": (_two_concepts, ["merge", "concepts/a", "concepts/b", "--auto"], ""),
    "sync-tags": (_seed_sync_tags, ["sync-tags", "sources/notes"], "y\n"),
    "normalize-names": (_seed_normalize, ["normalize-names"], "y\n"),
    "repair": (_seed_repair, ["repair"], ""),
    "reconcile": (_two_concepts, ["reconcile", "concepts/a", "concepts/b"], "y\n"),
}


def _assert_placement(probe: _Probe, stderr: str) -> None:
    assert "derived-index refresh incomplete" not in stderr
    assert probe.fts, "the FTS refresh never ran"
    assert probe.graph, "the graph refresh never ran"
    assert probe.embed, "the embedding stage never ran"
    assert set(probe.fts) == {False}, "FTS refreshed without the lock"
    assert set(probe.graph) == {False}, "graph refreshed without the lock"
    assert set(probe.embed) == {True}, "an embedding call held the lock"


@pytest.mark.parametrize("verb", sorted(_SCENARIOS))
def test_fts_and_graph_refresh_hold_the_lock_and_embedding_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    seed, argv, answer = _SCENARIOS[verb]
    init_workspace(tmp_path, monkeypatch)
    seed(tmp_path)
    simulate_tty(monkeypatch)
    probe = _install_probe(monkeypatch, tmp_path)

    result = runner.invoke(app, argv, input=answer)

    assert result.exit_code == 0, result.stderr
    _assert_placement(probe, result.stderr)


def test_unmerge_refresh_holds_the_lock_for_fts_and_graph_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_workspace(tmp_path, monkeypatch)
    _two_concepts(tmp_path)
    assert (
        runner.invoke(app, ["merge", "concepts/a", "concepts/b", "--auto"]).exit_code
        == 0
    )
    simulate_tty(monkeypatch)
    probe = _install_probe(monkeypatch, tmp_path)

    result = runner.invoke(app, ["unmerge", "concepts/a", "concepts/b", "--auto"])

    assert result.exit_code == 0, result.stderr
    _assert_placement(probe, result.stderr)
