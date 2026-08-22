"""Unit tests for the `suggest-relations` CLI command: read-only LLM
relation-type suggestion over untyped body-link edges (MVP-2 slice 2b).

`suggest-relations`: `config.require_workspace` gate -> `config.read_config`
-> `resolution.edge_typing.candidate_edges` (owns the `build_graph` read, no
LLM) to count candidates -> a cost-preview confirmation gate (`--auto` skips
it, issue #134) -> a real `OllamaClient(model=cfg.model)` injected into
`resolution.edge_typing.suggest_edge_types`, which emits a per-edge progress
line. It is read-only: it never writes, whatever the gate answer.

A test that needs a specific suggestion OUTCOME patches
`openkos.cli.main.candidate_edges` (to control the candidate count without a
real graph, via `_patch_candidate_edges`) and
`openkos.cli.main.suggest_edge_types` (to control the typing result), and
passes `--auto` to skip the gate -- zero network, zero real Ollama process.
"""

import os
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli import main
from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.graph.base import Edge
from openkos.graph.proximity import ProximityPair
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.model.relations import ASYMMETRIC_RELATION_TYPES
from openkos.resolution.edge_typing import (
    LEAST_SPECIFIC_RELATION_TYPE,
    EdgeSuggestion,
    EdgeSuggestionBatch,
)
from tests.unit.cli.conftest import commit_pending_fixture_docs, disable_local_exemption
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot

runner = CliRunner()


def _break_os_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force `okf._walk_errors` to report exactly one directory-scan error,
    deterministically -- mirrors `tests/unit/model/test_okf.py`'s onerror
    monkeypatch pattern, without relying on real `chmod` bits."""
    original_walk = os.walk
    walk_error = OSError(13, "Permission denied", "locked")

    def fake_walk(
        top: str | os.PathLike[str],
        topdown: bool = True,
        onerror: Callable[[OSError], object] | None = None,
        followlinks: bool = False,
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        if onerror is not None:
            onerror(walk_error)
        yield from original_walk(top, topdown, onerror, followlinks)

    monkeypatch.setattr(os, "walk", fake_walk)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )
    # The workspace's git state must match a real session's before a verb
    # that deletes this document reaches its auto-commit (issue #819).
    commit_pending_fixture_docs()


def _write_confidential_doc(path: Path, *, title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\nsensitivity: confidential\n---\n"
        f"# {title}\n",
        encoding="utf-8",
    )
    # The workspace's git state must match a real session's before a verb
    # that deletes this document reaches its auto-commit (issue #819).
    commit_pending_fixture_docs()


def _suggestion(
    *,
    source: str = "concepts/a",
    target: str = "concepts/b",
    suggested_type: str | None = "references",
    rationale: str = "stub rationale",
) -> EdgeSuggestion:
    return EdgeSuggestion(
        edge=Edge(source_id=source, target_id=target),
        suggested_type=suggested_type,
        rationale=rationale,
    )


def _patch_candidate_edges(
    monkeypatch: pytest.MonkeyPatch, edges: list[Edge]
) -> dict[str, object]:
    """Patch `candidate_edges` to return a fixed edge list (no graph read),
    recording the kwargs it was called with. Lets a test drive the CLI's
    count/gate/progress path without a real bundle graph."""
    captured: dict[str, object] = {}

    def _fake(bundle_dir: Path, **kwargs: object) -> list[Edge]:
        captured["bundle_dir"] = bundle_dir
        captured["kwargs"] = kwargs
        return edges

    monkeypatch.setattr("openkos.cli.main.candidate_edges", _fake)
    return captured


def test_suggest_relations_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `suggest-relations` refuses (exit 1), prints the
    shared `require_workspace` reason under a `suggest-relations`-specific
    prefix, and never calls the library function (spec: mirrors `adjudicate`)."""
    monkeypatch.chdir(tmp_path)
    captured = _patch_candidate_edges(monkeypatch, [])

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos suggest-relations: refusing to run -- no OpenKOS workspace "
        "found in this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert "bundle_dir" not in captured  # candidate_edges never reached


def test_suggest_relations_malformed_config_maps_to_exit_one_before_calling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard, mirrors
    `adjudicate`) is caught, printed as a friendly stderr message, exits 1
    with no raw traceback, and the library function is never reached."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    captured = _patch_candidate_edges(monkeypatch, [])

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos suggest-relations: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert "bundle_dir" not in captured  # candidate_edges never reached


def test_suggest_relations_fresh_bundle_reports_no_untyped_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A freshly initialized, empty bundle has zero untyped edges, so the
    real `suggest_relations` never calls `llm.chat` -- a real `OllamaClient`
    is safe to construct here. With `vectors.db` present (state 1's
    precondition), prints the empty-graph message and exits 0 (specs/
    llm-edge-production: "Empty graph reports nothing-to-work-with")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "No concept relationships in the graph yet." in result.stdout


def test_suggest_relations_missing_vectors_db_reports_not_computable_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent `vectors.db` (state 3) reports candidates as not computable
    yet, using a message distinct from the empty-graph state (specs/
    llm-edge-production: "Missing embeddings reports not-computable-yet")."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "Candidate relations unavailable" in result.stdout
    assert "vectors.db missing" in result.stdout
    assert "No concept relationships in the graph yet." not in result.stdout


def test_suggest_relations_provenance_only_bundle_reports_zero_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A bundle whose only body link is a Concept->Source provenance-mirror
    edge (typed `derived_from` by graph projection, #135) reports zero
    candidates and zero LLM calls -- a real `OllamaClient` is safe to
    construct here because `candidate_edges` is empty (spec: "Bundle with
    only provenance-mirror edges surfaces zero candidates"; task 3.3). A
    Concept->Source edge is NOT concept-to-concept (issue #183 Fix 1), so
    `graph_edge_summary` counts zero -- this is state 1 ("no
    concept-to-concept edges at all"), NOT state 2, reported distinctly from
    the embeddings-missing state (specs/llm-edge-production: "Empty graph
    reports nothing-to-work-with")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    (tmp_path / "bundle" / "concepts" / "a.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "provenance:\n  - sources/foo\n"
        "---\n[Foo](/sources/foo.md)\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "sources" / "foo.md").write_text(
        "---\ntype: Source\ntitle: Foo\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "No concept relationships in the graph yet." in result.stdout


def test_suggest_relations_post_relate_reports_excluded_untyped_rows_not_none_untyped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The state a human leaves behind by running `relate`: the accepted
    suggestion becomes a NEW typed `relations:` row while the ORIGINAL
    untyped body-link row survives (`edge_typing.py:116-138` --
    "the original untyped body-link row is never removed by `relate`"), so
    `_candidate_edges`'s PAIR-level exclusion drops it and zero candidates
    remain.

    `graph_edge_summary` counts raw rows with neither the pair-level nor
    the confidentiality filter applied, so its total (2) says nothing about
    how many rows are untyped (1). Claiming "none are untyped" here is
    factually FALSE. Locks state 2b (specs/llm-edge-production: "Untyped
    edges exist but every one was excluded").

    graph-projection-reuse (#196) re-verification: `seed_vectors_db` stores
    a row for the dummy `concept_id` `stub` ONLY, so `concepts/a` and
    `concepts/b` are never embedded at all and `VectorStoreDB.neighbors`
    returns `[]` for them (`state/vectorstore.py`: an id with no stored
    embedding "returns `[]` rather than raising"). Not a too-distant match
    -- no match is attempted. The candidates-seeded store
    `_zero_edge_state_message` now reads therefore holds ZERO proximity
    rows, so the counts below (`2`/`1`) are UNCHANGED by the shared-store
    correctness fix. See
    `test_suggest_relations_all_excluded_message_counts_proximity_rows` for
    the bundle where the accepted count delta actually fires."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: relates_to\n"
        "---\nSee also [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (concepts / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\n# B\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert (
        "2 relation(s) exist; 1 untyped, but every untyped pair is already "
        "typed elsewhere or filtered as confidential -- nothing left to "
        "suggest." in result.stdout
    )
    assert "none are untyped" not in result.stdout


def test_suggest_relations_provenance_mirror_edge_excluded_genuine_edge_surfaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with one provenance-mirror edge (excluded, typed
    `derived_from`) and one genuine untyped concept-to-concept edge surfaces
    ONLY the genuine edge as a candidate (spec: "A genuine untyped
    concept-to-concept edge is still surfaced"; task 3.4). `suggest_edge_types`
    is patched to avoid a real LLM call; `candidate_edges` is the REAL,
    unpatched function reading the actual bundle graph."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "provenance:\n  - sources/foo\n"
        "---\n[Foo](/sources/foo.md)\n\nSee also [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "sources" / "foo.md").write_text(
        "---\ntype: Source\ntitle: Foo\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        captured["edges"] = edges
        return EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source="concepts/a",
                    target="concepts/b",
                    suggested_type="related_to",
                    rationale="mentions B",
                )
            ]
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    edges = captured["edges"]
    assert isinstance(edges, list)
    assert len(edges) == 1
    assert edges[0].source_id == "concepts/a"
    assert edges[0].target_id == "concepts/b"
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "sources/foo" not in result.stdout


def test_suggest_relations_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- byte contents AND `st_mtime_ns` both unchanged (spec: Verb
    performs zero writes)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["suggest-relations"], catch_exceptions=True)

    # Regardless of exit code (a real Ollama may or may not be reachable in
    # this environment), the workspace bytes/mtimes must be identical --
    # `suggest-relations` never writes, whether it succeeds or degrades.
    assert _snapshot(tmp_path) == before
    if result.exit_code == 0:
        assert "openkos suggest-relations: workspace at" in result.stdout


def test_suggest_relations_renders_type_source_target_and_rationale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A well-formed suggestion renders its suggested type, source, target,
    and rationale, plus the closing `relate` hint, and exits 0 (spec: Verb
    lists every untyped edge with a valid suggestion)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        captured["kwargs"] = kwargs
        return EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source="concepts/a",
                    target="concepts/b",
                    suggested_type="references",
                    rationale="mentions concept b",
                )
            ]
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "references" in result.stdout
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "mentions concept b" in result.stdout
    assert "openkos relate" in result.stdout
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["bundle_dir"] == tmp_path / "bundle"


def test_suggest_relations_degraded_item_renders_as_no_valid_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A degraded suggestion (`suggested_type=None`) renders as `[?]` +
    "no valid type suggested", never as if it were a valid suggestion
    (spec: Invalid suggested type is not surfaced as valid)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        return EdgeSuggestionBatch(
            results=[_suggestion(suggested_type=None, rationale="malformed reply")]
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "[?]" in result.stdout
    assert "no valid type suggested" in result.stdout


def test_suggest_relations_builds_ollama_client_from_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suggest-relations` builds the `OllamaClient` from the model
    configured in `openkos.yaml`, not a hardcoded value (spec: mirrors
    `adjudicate`'s wiring).

    `edge_typing: null` is kept here even though #650 removed the packaged
    default (nothing left to decline): an explicit null must still resolve
    to the global `model:`, and the no-`models:`-at-all path has its own
    test below."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\nmodels:\n  edge_typing: null\n",
        encoding="utf-8",
    )
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    captured: dict[str, object] = {}

    def _recording_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        captured["kwargs"] = kwargs
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _recording_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    llm = kwargs["llm"]
    assert isinstance(llm, OllamaClient)
    assert llm._model == configured_model


def test_suggest_relations_follows_the_global_model_without_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no `models:` key at all, this verb runs on the GLOBAL `model:`
    (#650) -- the packaged `gemma2:27b` default is gone; the 15.6 GB pull
    is now an explicit opt-in via `models: {edge_typing: gemma2:27b}`.

    This applies to the standalone verb as well as `curate`'s Structure
    stage — both run `suggest_edge_types`, and the task key is what keeps
    them together."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text(
        "model: llama3.2:1b-openkos-test\n", encoding="utf-8"
    )
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    captured: dict[str, object] = {}

    def _recording_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        captured["kwargs"] = kwargs
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _recording_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    llm = kwargs["llm"]
    assert isinstance(llm, OllamaClient)
    assert llm._model == "llama3.2:1b-openkos-test"


def test_suggest_relations_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suggest_relations()` raising `OllamaUnavailable` is caught, printed
    as a friendly stderr message with `ollama serve` remediation, exits 1,
    and writes nothing (spec: mirrors `adjudicate`'s degrade-on-no-model)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    before = _snapshot(tmp_path)

    def _raise_unavailable(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _raise_unavailable)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos suggest-relations: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stderr.rstrip("\n").endswith(
        "Or run `openkos doctor` to diagnose the environment."
    )
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_suggest_relations_model_not_found_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`suggest_relations()` raising `OllamaModelNotFound` is caught,
    printed with the CONFIGURED model tag and `ollama pull <model>`
    remediation, exits 1, and writes nothing."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    before = _snapshot(tmp_path)

    def _raise_model_not_found(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _raise_model_not_found)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos suggest-relations: failed -- ")
    assert "is not installed" in result.stderr
    assert f"ollama pull {configured_model}" in result.stderr
    assert "openkos doctor" not in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_suggest_relations_generic_ollama_error_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generic `OllamaError` (neither `OllamaUnavailable` nor
    `OllamaModelNotFound`) is caught by the 3rd-tier fallback handler."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    before = _snapshot(tmp_path)

    def _raise_generic(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        raise OllamaError("something else went wrong")

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _raise_generic)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos suggest-relations: failed -- something else went wrong.\n"
    )
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


# ---------------------------------------------------------------------------
# issue #441: a partial batch keeps its completed suggestions
# ---------------------------------------------------------------------------


def _two_edge_partial_batch(
    monkeypatch: pytest.MonkeyPatch, failure: OllamaError
) -> None:
    """Wire `candidate_edges` to two edges and `suggest_edge_types` to a
    batch whose first edge completed and whose second raised `failure` --
    the #441 mid-batch shape."""
    _patch_candidate_edges(
        monkeypatch,
        [
            Edge(source_id="concepts/a", target_id="concepts/b"),
            Edge(source_id="concepts/c", target_id="concepts/d"),
        ],
    )

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        return EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source="concepts/a",
                    target="concepts/b",
                    suggested_type="references",
                    rationale="kept work",
                )
            ],
            failure=failure,
            failed_index=2,
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)


def test_suggest_relations_partial_batch_reports_completed_then_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-batch `OllamaError` no longer discards completed suggestions
    (#441): the report renders them exactly as a complete run over that list
    would, THEN one stderr line reports the failure with completed-of-total
    counts, and the exit code stays the OllamaError-family 1."""
    _init_workspace(tmp_path, monkeypatch)
    _two_edge_partial_batch(monkeypatch, OllamaError("boom"))

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "[references] concepts/a -> concepts/b" in result.stdout
    assert "kept work" in result.stdout
    assert "openkos relate" in result.stdout
    assert result.stderr == (
        "openkos suggest-relations: failed after suggesting 1 of 2 untyped "
        "edge(s) -- boom.\n"
    )
    assert "Traceback" not in result.stderr


def test_suggest_relations_partial_batch_unavailable_keeps_remediation_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OllamaUnavailable` batch failure keeps its cause-specific
    remediation (`ollama serve` + doctor hint), gains the completed-of-total
    counts, and still exits 1 (#441)."""
    _init_workspace(tmp_path, monkeypatch)
    _two_edge_partial_batch(
        monkeypatch, OllamaUnavailable("Ollama not reachable at http://localhost:11434")
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 1
    assert "1 of 2" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stderr.rstrip("\n").endswith(
        "Or run `openkos doctor` to diagnose the environment."
    )
    assert "kept work" in result.stdout


def test_suggest_relations_partial_batch_model_not_found_keeps_pull_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OllamaModelNotFound` batch failure keeps the configured model's
    `ollama pull` remediation alongside the completed-of-total counts (#441)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    _two_edge_partial_batch(monkeypatch, OllamaModelNotFound("Model not found (404)"))

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 1
    assert "1 of 2" in result.stderr
    assert f"ollama pull {configured_model}" in result.stderr
    assert "kept work" in result.stdout


def test_suggest_relations_auto_flag_skips_the_confirmation_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """`--auto` is accepted and bypasses the confirmation gate (issue #134:
    the cost gate is opt-out for scripted/non-interactive use). On an empty
    bundle there is nothing to type, so it still exits 0 cleanly without ever
    prompting."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "No concept relationships in the graph yet." in result.stdout
    assert "Proceed?" not in result.stdout


# ---------------------------------------------------------------------------
# Cost gate + per-edge progress (issue #134)
# ---------------------------------------------------------------------------


def test_suggest_relations_gate_previews_cost_and_declining_generates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without `--auto`, the command previews the per-edge LLM cost and asks
    to proceed; answering "no" exits 0 and NEVER calls `suggest_edge_types`
    (no model contacted) -- the whole point of the gate (issue #134)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch,
        [
            Edge(source_id="concepts/a", target_id="concepts/b"),
            Edge(source_id="concepts/a", target_id="concepts/c"),
        ],
    )
    called = False

    def _must_not_run(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        nonlocal called
        called = True
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _must_not_run)

    result = runner.invoke(app, ["suggest-relations"], input="n\n")

    assert result.exit_code == 0
    assert "2 untyped edge(s) -> 2 LLM call(s)" in result.stderr
    assert "Aborted" in result.stdout
    assert called is False


def test_suggest_relations_gate_confirmed_runs_and_prints_per_edge_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Answering "yes" at the gate runs the typing pass, and each edge emits
    a `[i/N] source -> target [type]` progress line to stderr as it completes
    (issue #134): the run is no longer opaque."""
    _init_workspace(tmp_path, monkeypatch)
    edges = [
        Edge(source_id="concepts/a", target_id="concepts/b"),
        Edge(source_id="concepts/a", target_id="concepts/c"),
    ]
    _patch_candidate_edges(monkeypatch, edges)

    def _fake_suggest(edges_arg: list[Edge], **kwargs: object) -> EdgeSuggestionBatch:
        on_progress = kwargs["on_progress"]
        assert callable(on_progress)
        results = [
            _suggestion(
                source="concepts/a", target="concepts/b", suggested_type="references"
            ),
            _suggestion(
                source="concepts/a", target="concepts/c", suggested_type="related_to"
            ),
        ]
        for index, suggestion in enumerate(results, start=1):
            on_progress(index, len(results), suggestion)
        return EdgeSuggestionBatch(results=results)

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)

    result = runner.invoke(app, ["suggest-relations"], input="y\n")

    assert result.exit_code == 0
    assert "[1/2] concepts/a -> concepts/b  [references]" in result.stderr
    assert "[2/2] concepts/a -> concepts/c  [related_to]" in result.stderr
    assert "openkos relate" in result.stdout


# ---------------------------------------------------------------------------
# `--include-confidential` (sensitivity-fail-closed-filter S3a)
# ---------------------------------------------------------------------------


def test_suggest_relations_include_confidential_flag_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` is forwarded unchanged as
    `suggest_relations(..., include_confidential=True)` (spec:
    `--include-confidential` Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    captured = _patch_candidate_edges(monkeypatch, [])

    result = runner.invoke(app, ["suggest-relations", "--include-confidential"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is True


def test_suggest_relations_omitted_include_confidential_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--include-confidential` forwards the safe default
    `include_confidential=False` (spec: Confidential Excluded By Default)."""
    _init_workspace(tmp_path, monkeypatch)
    captured = _patch_candidate_edges(monkeypatch, [])

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is False


# ---------------------------------------------------------------------------
# directory-walk-observability follow-up: walk-incompleteness signal
# ---------------------------------------------------------------------------


def test_suggest_relations_warns_stderr_on_incomplete_walk_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete directory walk (`okf._walk_errors` non-empty) prints a
    self-explaining warning to STDERR and the command still exits 0 -- WARN,
    not refuse (spec: Incomplete walk warns and still exits 0). A freshly
    initialized, empty bundle has zero untyped edges, so the real
    `suggest_relations` never calls `llm.chat` -- a real `OllamaClient` is
    safe to construct here.

    The default workspace grants the confidential local exemption (#240),
    which is the OTHER hatch that suppresses the FILTER-SCOPED half of this
    signal -- see
    `test_suggest_relations_local_exemption_keeps_the_general_advisory`
    below. This test is about the run where NEITHER hatch is set, so it opts
    out of the exemption through the same workspace switch a user would use,
    and both advisories are then true at once.

    Asserted through text unique to each: since #356 both lines open with
    `bundle scan was incomplete`, so that prefix no longer tells them
    apart."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" in result.stderr


def test_suggest_relations_no_warning_on_clean_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully readable bundle produces NEITHER advisory (spec: Clean bundle
    produces no warning) -- the walk coming back empty is the only thing
    that silences the #356 one, which no hatch suppresses."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" not in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_suggest_relations_include_confidential_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` suppresses the FILTER-SCOPED advisory only:
    the filter is deliberately off, so its message would be a claim about a
    filter that never ran. The #356 incomplete-inputs advisory still prints,
    and the command still exits 0 -- the candidate-edge count is derived
    from the same graph projection an unreadable subtree shrinks, so the
    cost line and the suggestions both under-report either way.

    The default workspace ALSO grants the confidential local exemption
    (#240), which independently suppresses the same filter-scoped message.
    Without opting out of that here, this test would keep passing even if
    `--include-confidential` were silently dropped from the
    `warn_if_walk_incomplete` call site, so it disables the exemption to
    make the FLAG the thing that discriminates."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--include-confidential"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_suggest_relations_local_exemption_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confidential local exemption (#240) suppresses the FILTER-SCOPED
    advisory the SAME way `--include-confidential` does, and leaves the #356
    incomplete-inputs advisory printing.

    This is the STOCK workspace -- default local backend, exemption active
    -- which is exactly the path that emitted nothing at all before #356:
    fewer nodes and fewer edges reached the projection, so fewer untyped
    edges were ever offered for typing, and the run still exited 0."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


# --- integration proof (real bundle: examples/good-life-demo) ---------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOOD_LIFE_ROOT = _REPO_ROOT / "examples" / "good-life-demo"


def test_suggest_relations_over_good_life_demo_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running `suggest-relations` against the real `examples/good-life-demo`
    workspace writes nothing under the bundle regardless of outcome -- the
    real `OllamaClient` may or may not reach a live Ollama in this
    environment, but the zero-writes contract must hold either way."""
    assert _GOOD_LIFE_ROOT.is_dir(), f"missing example workspace: {_GOOD_LIFE_ROOT}"
    monkeypatch.chdir(_GOOD_LIFE_ROOT)
    bundle_dir = _GOOD_LIFE_ROOT / "bundle"
    before = _snapshot(bundle_dir)

    result = runner.invoke(app, ["suggest-relations"], catch_exceptions=True)

    assert _snapshot(bundle_dir) == before
    if result.exit_code == 0:
        assert "openkos suggest-relations: workspace at" in result.stdout


# --- Phase 3.2 (#183): the proximity seam ---------------------------------


def test_suggest_relations_opens_the_proximity_source_once_and_forwards_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """`suggest-relations` resolves the candidate source through ONE shared
    seam and hands it to `build_graph`, which produces the ONE shared store
    that `candidate_edges` then reuses via `store=` (graph-projection-reuse:
    `candidates` is consumed once by `build_graph`, never forwarded again).

    Opening the proximity source once matters twice over: it is a real
    SQLite connection, and the state-3 message must be driven by what the
    seam already learned rather than by a second probe of the same file."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    opened: list[Path] = []
    build_calls: list[object] = []
    forwarded: list[object] = []

    class _Source:
        def pairs(self, concept_ids: object) -> list[object]:
            return []

        def close(self) -> None:
            return None

    def _fake_open(path: Path) -> object:
        opened.append(path)
        return _Source()

    real_build_graph = sqlite_graph.build_graph

    def _recording_build_graph(
        bundle_dir: Path,
        *,
        candidates: sqlite_graph.CandidateSource | None = None,
        candidate_offset: int = 0,
    ) -> object:
        build_calls.append(candidates)
        return real_build_graph(
            bundle_dir, candidates=candidates, candidate_offset=candidate_offset
        )

    def _fake_candidate_edges(bundle_dir: Path, **kwargs: object) -> list[object]:
        forwarded.append(kwargs.get("store"))
        return []

    monkeypatch.setattr(main, "_open_proximity_or_degrade", _fake_open)
    monkeypatch.setattr("openkos.cli.main.build_graph", _recording_build_graph)
    monkeypatch.setattr(main, "candidate_edges", _fake_candidate_edges)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert len(opened) == 1
    assert opened[0] == tmp_path / ".openkos" / "vectors.db"
    assert len(build_calls) == 1
    assert isinstance(build_calls[0], _Source)
    assert len(forwarded) == 1
    assert forwarded[0] is not None


def test_suggest_relations_state_three_comes_from_the_seam_not_a_second_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the seam reports no source, the embeddings-missing message is
    printed WITHOUT re-reading `vectors.db`.

    PR1 shipped that message keyed on its own `vector_store_is_empty` call
    inside `_zero_edge_state_message`; once the seam already knows, that
    second read is redundant work on a path taken every run."""
    _init_workspace(tmp_path, monkeypatch)
    probes: list[Path] = []
    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: None)

    def _probing_is_empty(path: Path) -> bool:
        probes.append(path)
        return True

    monkeypatch.setattr(main, "vector_store_is_empty", _probing_is_empty)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "Candidate relations unavailable" in result.stdout
    assert probes == [], "the zero-edge message re-probed vectors.db"


def test_suggest_relations_closes_the_proximity_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """The seam hands back a real SQLite connection; the command owns
    closing it, or a long-lived shell leaks one handle per invocation."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    closed: list[bool] = []

    class _Source:
        def pairs(self, concept_ids: object) -> list[object]:
            return []

        def close(self) -> None:
            closed.append(True)

        def __enter__(self) -> "_Source":
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _Source())
    monkeypatch.setattr(main, "candidate_edges", lambda bundle_dir, **kwargs: [])

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert closed == [True]


# --- graph-projection-reuse (#196): one build per invocation ---------------


def test_suggest_relations_builds_the_graph_once_on_the_zero_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """#196: the zero-candidate path must not rebuild the projection for its
    "nothing to suggest" message."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    calls: list[Path] = []
    real = sqlite_graph.build_graph

    def _counting_build_graph(
        bundle_dir: Path,
        *,
        candidates: sqlite_graph.CandidateSource | None = None,
        candidate_offset: int = 0,
    ) -> sqlite_graph.SqliteGraphStore:
        calls.append(bundle_dir)
        return real(
            bundle_dir, candidates=candidates, candidate_offset=candidate_offset
        )

    monkeypatch.setattr("openkos.cli.main.build_graph", _counting_build_graph)
    monkeypatch.setattr("openkos.graph.summary.build_graph", _counting_build_graph)
    monkeypatch.setattr(
        "openkos.resolution.edge_typing.build_graph", _counting_build_graph
    )

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "No concept relationships in the graph yet." in result.stdout


def test_suggest_relations_builds_the_graph_once_on_the_non_zero_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the split-block refactor: exactly one `build_graph` call on
    the non-zero path, AND the shared store is already closed before the
    LLM run starts -- the store is only needed to materialize `edges`, not
    for the minutes-long `suggest_edge_types` loop (graph-projection-reuse)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n---\n"
        "See also [B](/concepts/b.md).\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )

    stores: list[sqlite_graph.SqliteGraphStore] = []
    real = sqlite_graph.build_graph

    def _counting_build_graph(
        bundle_dir: Path,
        *,
        candidates: sqlite_graph.CandidateSource | None = None,
        candidate_offset: int = 0,
    ) -> sqlite_graph.SqliteGraphStore:
        store = real(
            bundle_dir, candidates=candidates, candidate_offset=candidate_offset
        )
        stores.append(store)
        return store

    monkeypatch.setattr("openkos.cli.main.build_graph", _counting_build_graph)
    monkeypatch.setattr("openkos.graph.summary.build_graph", _counting_build_graph)
    monkeypatch.setattr(
        "openkos.resolution.edge_typing.build_graph", _counting_build_graph
    )

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        assert len(stores) == 1
        store = stores[0]
        with pytest.raises(sqlite3.ProgrammingError):
            store.edges()
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert len(stores) == 1


def test_suggest_relations_all_excluded_message_counts_proximity_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confidential concept reachable only via a proximity row is dropped
    from `candidate_edges` by the confidentiality filter (zero candidates),
    but the shared candidates-seeded store `_zero_edge_state_message` reads
    still counts that untyped proximity row -- `untyped` reflects the SAME
    projection the filtering ran on (graph-projection-reuse, the accepted
    behavior delta in design.md §4)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n---\nBody A.\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: confidential\n---\nBody B.\n",
        encoding="utf-8",
    )

    class _StubSource:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return [
                ProximityPair(
                    source_id="concepts/a", target_id="concepts/b", distance=0.1
                )
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _StubSource())
    # The suite's Ollama stand-in reports a LOCAL backend, where #240 grants
    # the confidential local exemption and the endpoint is legitimately kept.
    # This test is about the confidentiality FILTER, so opt out through the
    # same workspace switch a user would use.
    disable_local_exemption(tmp_path)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert (
        "1 relation(s) exist; 1 untyped, but every untyped pair is already "
        "typed elsewhere or filtered as confidential -- nothing left to "
        "suggest." in result.stdout
    )


def test_suggest_relations_reports_candidate_truncation_when_the_cap_is_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#378 slice 2: when pass 3's candidate cap truncates the set, the
    truncation notice ("{retained} of {produced} candidate edge(s) shown
    (cap reached)") must be rendered -- never silent."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "hub.md").write_text(
        "---\ntype: Concept\ntitle: Hub\n---\nBody.\n", encoding="utf-8"
    )
    pairs = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        (tmp_path / "bundle" / "concepts" / f"{leaf_id}.md").write_text(
            f"---\ntype: Concept\ntitle: {leaf_id}\n---\nBody.\n", encoding="utf-8"
        )
        pairs.append(
            ProximityPair(
                source_id="concepts/hub",
                target_id=f"concepts/{leaf_id}",
                distance=index * 0.001,
            )
        )

    class _StubSource:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _StubSource())
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(results=[]),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "50 of 60 candidate edge(s) shown (cap reached)" in result.stdout


def test_suggest_relations_no_truncation_notice_under_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the cap, `produced == retained` -- the truncation notice must
    NOT appear."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\nBody.\n", encoding="utf-8"
    )
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n---\nBody.\n", encoding="utf-8"
    )

    class _StubSource:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return [
                ProximityPair(
                    source_id="concepts/a", target_id="concepts/b", distance=0.1
                )
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _StubSource())
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(results=[]),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "cap reached" not in result.stdout


def test_suggest_relations_suppresses_notice_when_every_dropped_pair_is_confidential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#378 post-review correction: a truncated run whose dropped candidates
    are ALL confidential must print NO notice to a caller without
    `--include-confidential` -- the reader must never learn a pre-cap total
    that counts material the same command's edge list already withholds."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "hub.md").write_text(
        "---\ntype: Concept\ntitle: Hub\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    pairs = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        # The 10 farthest leaves (051-060) are exactly the ones the cap
        # drops -- mark all of them confidential so the WHOLE dropped set
        # is invisible to a caller without the escape flag. Every OTHER doc
        # (including the hub) explicitly declares `sensitivity: private` --
        # a missing field fails closed too (`blocks_llm_send`), which would
        # block everything and make this fixture prove nothing.
        if index > 50:
            _write_confidential_doc(
                tmp_path / "bundle" / "concepts" / f"{leaf_id}.md", title=leaf_id
            )
        else:
            (tmp_path / "bundle" / "concepts" / f"{leaf_id}.md").write_text(
                f"---\ntype: Concept\ntitle: {leaf_id}\nsensitivity: private\n---\n"
                "Body.\n",
                encoding="utf-8",
            )
        pairs.append(
            ProximityPair(
                source_id="concepts/hub",
                target_id=f"concepts/{leaf_id}",
                distance=index * 0.001,
            )
        )

    class _StubSource:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _StubSource())
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(results=[]),
    )

    default_run = runner.invoke(app, ["suggest-relations", "--auto"])
    flagged_run = runner.invoke(
        app, ["suggest-relations", "--auto", "--include-confidential"]
    )

    assert default_run.exit_code == 0
    assert "cap reached" not in default_run.stdout
    assert flagged_run.exit_code == 0
    assert "50 of 60 candidate edge(s) shown (cap reached)" in flagged_run.stdout


def test_suggest_relations_reports_only_visible_counts_for_a_mixed_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#378 post-review correction: when only SOME of the dropped candidates
    are confidential, the notice must report the VISIBLE counts, not the
    raw pre-cap totals."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "hub.md").write_text(
        "---\ntype: Concept\ntitle: Hub\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    pairs = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        # Of the 10 dropped leaves (051-060), only 051-055 are confidential
        # -- 056-060 stay visible, so the drop is a MIX. Every OTHER doc
        # explicitly declares `sensitivity: private` -- a missing field
        # fails closed too, which would block everything.
        if 50 < index <= 55:
            _write_confidential_doc(
                tmp_path / "bundle" / "concepts" / f"{leaf_id}.md", title=leaf_id
            )
        else:
            (tmp_path / "bundle" / "concepts" / f"{leaf_id}.md").write_text(
                f"---\ntype: Concept\ntitle: {leaf_id}\nsensitivity: private\n---\n"
                "Body.\n",
                encoding="utf-8",
            )
        pairs.append(
            ProximityPair(
                source_id="concepts/hub",
                target_id=f"concepts/{leaf_id}",
                distance=index * 0.001,
            )
        )

    class _StubSource:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _StubSource())
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(results=[]),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "50 of 55 candidate edge(s) shown (cap reached)" in result.stdout


# ---------------------------------------------------------------------------
# `--apply` with per-item consent (issue #560)
# ---------------------------------------------------------------------------


def _init_apply_workspace(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_init_workspace` plus a real, isolated git identity (mirrors
    `test_adjudicate.py`'s helper): `--apply` auto-commits each accepted
    relation, so tests exercising the accept path need an actual git
    identity, never the host machine's real `~/.gitconfig`."""
    from tests.unit.vcs.conftest import isolate_git_identity

    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def test_suggest_relations_apply_accepted_suggestion_is_written(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--apply` walks each valid suggestion with a per-item `[y/N]` prompt
    (issue #560, mirroring `adjudicate --apply`): an accepted `y` writes the
    typed relation through the SAME prepare/drift-guard/write path `relate`
    uses, and the summary counts it."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[_suggestion(suggested_type="references")]
        ),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"], input="y\n")

    assert result.exit_code == 0
    source_text = (tmp_path / "bundle" / "concepts" / "a.md").read_text(
        encoding="utf-8"
    )
    assert "relations:" in source_text
    assert "target: concepts/b" in source_text
    assert "type: references" in source_text
    assert "applied 1" in result.stdout
    assert "Relate**" in (tmp_path / "bundle" / "log.md").read_text(encoding="utf-8")


def test_suggest_relations_apply_declined_suggestion_writes_nothing(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A declined item writes nothing to the BUNDLE and is listed in the
    decline report (the #483/#398 revisitable-decline contract), so a
    typo-free decline set survives the run.

    Scoped to `bundle/` since #799: the run now persists its suggestion to
    `.openkos/findings.db` so the next one need not re-buy it. That is
    derived state, not knowledge -- the same distinction `contradictions`
    and `adjudicate` already draw on their own read-only runs -- and the
    assertion below pins that it landed, rather than letting a widened
    snapshot quietly stop noticing either write."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    before = _snapshot(tmp_path / "bundle")
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[_suggestion(suggested_type="references")]
        ),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"], input="n\n")

    assert result.exit_code == 0
    assert _snapshot(tmp_path / "bundle") == before
    assert "applied 0" in result.stdout
    assert "declined: concepts/a -> concepts/b [references]" in result.stdout
    # The decline withholds the WRITE, never the paid-for suggestion: it
    # is persisted regardless, so declining does not force the next run to
    # re-buy the same answer (#799).
    store = sqlite3.connect(tmp_path / ".openkos" / "findings.db")
    try:
        rows = store.execute("SELECT suggested_type FROM edge_suggestions").fetchall()
    finally:
        store.close()
    assert rows == [("references",)]


def test_suggest_relations_apply_degraded_suggestion_is_never_prompted(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded suggestion (`suggested_type=None`) has nothing applicable:
    no prompt is issued for it, nothing is written, and it counts as
    skipped -- never silently dropped."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    before = _snapshot(tmp_path)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[_suggestion(suggested_type=None, rationale="malformed reply")]
        ),
    )

    # No input at all: a prompt would fail the invocation, which is the point.
    result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"])

    assert result.exit_code == 0
    assert _snapshot(tmp_path) == before
    assert "no valid type suggested" in result.stdout
    assert "applied 0" in result.stdout


def test_suggest_relations_without_apply_names_the_apply_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read-only run's closing hint names `--apply` alongside `relate`,
    so the verb is no longer a dead end that bills for suggestions with no
    path to accept them (issue #560)."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[_suggestion(suggested_type="references")]
        ),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "--apply" in result.stdout
    assert "openkos relate" in result.stdout


def test_suggest_relations_truncated_run_points_at_the_next_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the candidate cap truncated the set, the run says how to reach
    the remainder -- typing the shown edges shrinks the untyped pool, so a
    re-run surfaces the next batch (issue #560's cap dead-end)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "hub.md").write_text(
        "---\ntype: Concept\ntitle: Hub\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    pairs = []
    for index in range(1, 56):
        leaf_id = f"leaf-{index:03d}"
        (tmp_path / "bundle" / "concepts" / f"{leaf_id}.md").write_text(
            f"---\ntype: Concept\ntitle: {leaf_id}\nsensitivity: private\n---\nBody.\n",
            encoding="utf-8",
        )
        pairs.append(
            ProximityPair(
                source_id="concepts/hub",
                target_id=f"concepts/{leaf_id}",
                distance=index * 0.001,
            )
        )

    class _StubSource:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _StubSource())
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(results=[]),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "cap reached" in result.stdout
    assert "re-run" in result.stdout


def _sixty_pair_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 60-candidate over-cap workspace with a stubbed proximity source and
    a no-op suggester -- the browsing fixture #567's offset paging needs."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "hub.md").write_text(
        "---\ntype: Concept\ntitle: Hub\n---\nBody.\n", encoding="utf-8"
    )
    pairs = []
    for index in range(1, 61):
        leaf_id = f"leaf{index:03d}"
        (tmp_path / "bundle" / "concepts" / f"{leaf_id}.md").write_text(
            f"---\ntype: Concept\ntitle: {leaf_id}\n---\nBody.\n", encoding="utf-8"
        )
        pairs.append(
            ProximityPair(
                source_id="concepts/hub",
                target_id=f"concepts/{leaf_id}",
                distance=index * 0.001,
            )
        )

    class _StubSource:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _StubSource())
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(results=[]),
    )


def test_suggest_relations_edge_offset_surfaces_the_next_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--edge-offset 50` over 60 candidates surfaces exactly the 10 the
    default window dropped (#567: the remaining candidates used to be
    unreachable without typing the first 50)."""
    _sixty_pair_workspace(tmp_path, monkeypatch)

    captured: list[list[Edge]] = []

    def _capture(edges: Sequence[Edge], **kwargs: object) -> EdgeSuggestionBatch:
        captured.append(list(edges))
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _capture)

    result = runner.invoke(app, ["suggest-relations", "--auto", "--edge-offset", "50"])

    assert result.exit_code == 0
    assert len(captured) == 1
    targets = {edge.target_id for edge in captured[0]}
    assert targets == {f"concepts/leaf{index:03d}" for index in range(51, 61)}
    assert "10 of 60 candidate edge(s) shown (cap reached)" in result.stdout


def test_suggest_relations_truncation_notice_names_the_next_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capped run points at the next batch's exact offset, so the
    remaining candidates are reachable by browsing, not only by typing the
    ones shown (#567)."""
    _sixty_pair_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "--edge-offset 50" in result.stdout


def test_suggest_relations_edge_offset_beyond_the_set_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An offset at or past the candidate set says so instead of printing
    the misleading zero-candidate state message (#567)."""
    _sixty_pair_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto", "--edge-offset", "60"])

    assert result.exit_code == 0
    assert "no candidate edges at --edge-offset 60" in result.stdout


# --- issue #778: the direction disclaimer reaches both surfaces -------------


def test_listing_marks_an_asymmetric_type_direction_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#778: the read-only listing carries the SAME
    `(direction model-suggested, unverified)` suffix `curate` shows on an
    asymmetric type -- the documented contract (`docs/testing.md`, Known
    issues), previously honoured by only one of the two surfaces."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="projects/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source="concepts/a",
                    target="projects/b",
                    suggested_type="produced_by",
                    rationale="the concept is an output of the project",
                )
            ]
        ),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert (
        "[produced_by] concepts/a -> projects/b "
        "(direction model-suggested, unverified)" in result.stdout
    )


def test_listing_leaves_a_specific_symmetric_type_unmarked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`references` is symmetric -- no direction to doubt -- and specific,
    so it asserts something. Neither caveat applies, and inventing one
    would train the operator to ignore them. (`related_to` is symmetric
    too, but carries #802's caveat for a different reason.)"""
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source="concepts/a",
                    target="concepts/b",
                    suggested_type="references",
                    rationale="the first names the second",
                )
            ]
        ),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "[references] concepts/a -> concepts/b\n" in result.stdout
    assert "direction model-suggested" not in result.stdout
    assert "the documents do not say how" not in result.stdout


def test_apply_prompt_carries_the_direction_disclaimer(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#778 on the consent surface -- the one that most invites bulk
    application: the `--apply` prompt spells the exact caveat `curate`'s
    Structure stage spells, on both the preview line and the [y/N] text."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "projects" / "b.md", title="B", doc_type="Project")
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="projects/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source="concepts/a",
                    target="projects/b",
                    suggested_type="produced_by",
                    rationale="the concept is an output of the project",
                )
            ]
        ),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"], input="n\n")

    assert result.exit_code == 0
    assert (
        "[produced_by] concepts/a -> projects/b "
        "(direction model-suggested, unverified)" in result.stdout
    )
    assert (
        "Relate concepts/a -> projects/b [produced_by] "
        "(direction model-suggested, unverified)? [y/N]" in result.stdout
    )


def test_listing_states_what_the_least_specific_type_does_not_say(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#802 on the read-only listing.

    Tested end to end, not against the helper: #778's defect was a surface
    that never CALLED the helper, and a helper-level assertion passes just
    as happily in that world.
    """
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source="concepts/a",
                    target="concepts/b",
                    suggested_type="related_to",
                    rationale="the two are discussed together",
                )
            ]
        ),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert (
        "[related_to] concepts/a -> concepts/b "
        "(connected; the documents do not say how)" in result.stdout
    )
    assert "direction model-suggested" not in result.stdout


def test_apply_prompt_states_what_the_least_specific_type_does_not_say(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#802 on the consent surface -- the one that actually writes the edge,
    and the one where the operator is answering a question about a claim
    nobody had stated."""
    _init_apply_workspace(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source="concepts/a",
                    target="concepts/b",
                    suggested_type="related_to",
                    rationale=(
                        "both are discussed in the same meeting, but "
                        "neither causes, produces or contains the other"
                    ),
                )
            ]
        ),
    )

    result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"], input="n\n")

    assert result.exit_code == 0
    assert (
        "[related_to] concepts/a -> concepts/b "
        "(connected; the documents do not say how)" in result.stdout
    )
    assert (
        "Relate concepts/a -> concepts/b [related_to] "
        "(connected; the documents do not say how)? [y/N]" in result.stdout
    )


# --- issue #799: persisted suggestions are served before re-typing --------


def _stub_one_edge_with_call_log(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[int],
    *,
    source: str = "concepts/a",
    target: str = "concepts/b",
    suggested_type: str | None = "references",
) -> None:
    """Patch both seams so a test can count how many edges the MODEL was
    handed per run (#779's own idiom, one verb over): `calls == [1, 0]`
    reads "the first run typed one edge, the second served it"."""
    edge = Edge(source_id=source, target_id=target)
    _patch_candidate_edges(monkeypatch, [edge])

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        handed = list(edges)  # type: ignore[call-overload]
        calls.append(len(handed))
        return EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source=e.source_id,
                    target=e.target_id,
                    suggested_type=suggested_type,
                )
                for e in handed
            ]
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)


def test_repeat_run_serves_persisted_suggestions_with_zero_model_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeat run on an unchanged bundle hands the model zero edges and
    still prints the same suggestion (spec: Persisted Suggestions Are
    Served Before Re-Typing)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)

    first = runner.invoke(app, ["suggest-relations", "--auto"])
    second = runner.invoke(app, ["suggest-relations", "--auto"])

    assert first.exit_code == 0, first.stderr
    assert second.exit_code == 0, second.stderr
    assert calls == [1, 0]
    assert "[references] concepts/a -> concepts/b" in second.stdout
    assert (
        "1 of 1 candidate edge(s) served from persisted suggestions; "
        "0 typed fresh." in second.stderr
    )


def test_the_cost_gate_states_the_served_subtracted_call_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-spend gate must name the calls the run will ACTUALLY make.
    Announcing the worst case after a paid run is the #799 defect."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)

    runner.invoke(app, ["suggest-relations", "--auto"])
    gated = runner.invoke(app, ["suggest-relations"], input="y\n")

    assert gated.exit_code == 0, gated.stderr
    assert "1 untyped edge(s), 1 served -> 0 LLM call(s)" in gated.stderr
    assert calls == [1, 0]


def test_endpoint_drift_retypes_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An edited endpoint invalidates the stored digest, so the edge is
    typed fresh rather than served stale."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    target = tmp_path / "bundle" / "concepts" / "b.md"
    _write_doc(target, title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)

    runner.invoke(app, ["suggest-relations", "--auto"])
    target.write_text(
        "---\ntype: Concept\ntitle: B\n---\n# B\n\nEdited since.\n",
        encoding="utf-8",
    )
    second = runner.invoke(app, ["suggest-relations", "--auto"])

    assert second.exit_code == 0, second.stderr
    assert calls == [1, 1]
    assert (
        "0 of 1 candidate edge(s) served from persisted suggestions; "
        "1 typed fresh." in second.stderr
    )


def test_the_reverse_direction_never_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direction is identity: half the vocabulary is asymmetric, so a
    suggestion for `a -> b` must never answer for `b -> a`."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)
    runner.invoke(app, ["suggest-relations", "--auto"])

    calls.clear()
    _stub_one_edge_with_call_log(
        monkeypatch, calls, source="concepts/b", target="concepts/a"
    )
    reversed_run = runner.invoke(app, ["suggest-relations", "--auto"])

    assert reversed_run.exit_code == 0, reversed_run.stderr
    assert calls == [1], "the reverse pair must be typed fresh, not served"


def test_fresh_flag_bypasses_the_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--fresh` re-types every edge and re-persists, mirroring
    `adjudicate --fresh` and `contradictions --fresh`."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)

    runner.invoke(app, ["suggest-relations", "--auto"])
    second = runner.invoke(app, ["suggest-relations", "--auto", "--fresh"])
    third = runner.invoke(app, ["suggest-relations", "--auto"])

    assert second.exit_code == 0, second.stderr
    assert calls == [1, 1, 0], "--fresh re-types, and its result is re-persisted"
    assert third.exit_code == 0, third.stderr


def test_include_confidential_mismatch_retypes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suggestion computed over a different projection must not serve a
    run whose EFFECTIVE confidential inclusion differs."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)

    runner.invoke(app, ["suggest-relations", "--auto"])
    second = runner.invoke(
        app, ["suggest-relations", "--auto", "--include-confidential"]
    )

    assert second.exit_code == 0, second.stderr
    assert calls == [1, 1]


def test_a_degrade_is_never_cached_and_never_costs_its_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fail-closed `None` type is a FAILURE, not a verdict: storing it
    would cache a transport hiccup as a durable answer and never retry.

    The batch is MIXED on purpose. `suggested_type` is `NOT NULL` in the
    schema, so a degrade that reached the insert raises `IntegrityError`
    for the whole `record_edge_suggestions` transaction -- which
    `_persist_edge_suggestions` catches as one advisory. One malformed
    reply among N would then silently cost all N their cache entry. Only
    a mixed batch can tell that apart from correct behaviour: the valid
    edge must still serve on the next run while the degraded one retries."""
    _init_workspace(tmp_path, monkeypatch)
    for name in ("a", "b", "c", "d"):
        _write_doc(tmp_path / "bundle" / "concepts" / f"{name}.md", title=name.upper())
    good = Edge(source_id="concepts/a", target_id="concepts/b")
    bad = Edge(source_id="concepts/c", target_id="concepts/d")
    _patch_candidate_edges(monkeypatch, [good, bad])
    handed: list[list[str]] = []

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        listed = list(edges)  # type: ignore[call-overload]
        handed.append([f"{e.source_id}->{e.target_id}" for e in listed])
        return EdgeSuggestionBatch(
            results=[
                _suggestion(
                    source=e.source_id,
                    target=e.target_id,
                    suggested_type=None
                    if e.source_id == "concepts/c"
                    else "references",
                )
                for e in listed
            ]
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)

    first = runner.invoke(app, ["suggest-relations", "--auto"])
    second = runner.invoke(app, ["suggest-relations", "--auto"])

    assert first.exit_code == 0, first.stderr
    assert second.exit_code == 0, second.stderr
    assert handed[0] == ["concepts/a->concepts/b", "concepts/c->concepts/d"]
    # The degraded edge retries; the valid one in the SAME batch does not.
    assert handed[1] == ["concepts/c->concepts/d"]
    assert "failed to persist" not in second.stderr
    assert (
        "1 of 2 candidate edge(s) served from persisted suggestions; "
        "1 typed fresh." in second.stderr
    )


def test_corrupt_store_degrades_to_a_full_fresh_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A present-but-unreadable store costs one stderr advisory and a full
    fresh run, never a crash before any model spend."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)
    runner.invoke(app, ["suggest-relations", "--auto"])
    (tmp_path / ".openkos" / "findings.db").write_bytes(b"not a database at all")

    second = runner.invoke(app, ["suggest-relations", "--auto"])

    assert second.exit_code == 0, second.stderr
    assert calls == [1, 1]
    assert "failed to read persisted suggestions" in second.stderr
    assert "Traceback" not in second.stderr


def test_an_unreadable_store_reports_no_split_it_could_not_measure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #809, finding 2: the split line must not contradict the
    warning printed one line above it.

    The gate was `layout.findings_db_path.exists()` -- file EXISTENCE --
    so a present-but-unreadable store produced this pair, in this order:

        openkos suggest-relations: warning -- failed to read persisted
          suggestions (...); typing every edge fresh.
        openkos suggest-relations: 0 of 1 candidate edge(s) served from
          persisted suggestions; 1 typed fresh.

    The second line implies the store was consulted successfully,
    immediately after the first says the read failed. The honest condition
    is whether the store was READ, not whether the file is there, and a
    count of zero that means "could not look" must not be presented as a
    count of zero that means "looked, found nothing".

    The warning itself must still appear -- silence here would be a
    different defect, and asserting only the absence of the split line
    would pass on a run that reported nothing at all."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)
    runner.invoke(app, ["suggest-relations", "--auto"])
    (tmp_path / ".openkos" / "findings.db").write_bytes(b"not a database at all")

    second = runner.invoke(app, ["suggest-relations", "--auto"])

    assert second.exit_code == 0, second.stderr
    assert "failed to read persisted suggestions" in second.stderr
    assert "served from persisted suggestions" not in second.stderr


def test_a_row_missing_an_endpoint_digest_never_serves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose digest set no longer matches the edge's endpoints was
    computed over a different prompt, however fresh each digest is."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)
    runner.invoke(app, ["suggest-relations", "--auto"])

    conn = sqlite3.connect(tmp_path / ".openkos" / "findings.db")
    conn.execute(
        "DELETE FROM edge_suggestion_input_digests WHERE input_ref = ?",
        ("concepts/b",),
    )
    conn.commit()
    conn.close()
    second = runner.invoke(app, ["suggest-relations", "--auto"])

    assert second.exit_code == 0, second.stderr
    assert calls == [1, 1]


def test_forget_sweep_erases_suggestions_referencing_the_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rationale quotes both endpoints' bodies, so a suggestion naming
    a forgotten concept must not survive it."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")
    calls: list[int] = []
    _stub_one_edge_with_call_log(monkeypatch, calls)
    runner.invoke(app, ["suggest-relations", "--auto"])

    conn = sqlite3.connect(tmp_path / ".openkos" / "findings.db")
    before = conn.execute("SELECT COUNT(*) FROM edge_suggestions").fetchone()[0]
    conn.close()
    assert before == 1

    forgotten = runner.invoke(app, ["forget", "concepts/b", "--auto"])

    assert forgotten.exit_code == 0, forgotten.stderr
    conn = sqlite3.connect(tmp_path / ".openkos" / "findings.db")
    after = conn.execute("SELECT COUNT(*) FROM edge_suggestions").fetchone()[0]
    conn.close()
    assert after == 0


# ---------------------------------------------------------------------------
# issue #812 -- the workspace's pinned rationale language reaches this verb too
# ---------------------------------------------------------------------------


def test_suggest_relations_forwards_the_pinned_rationale_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`rationale_language` is forwarded unchanged from `openkos.yaml`.

    Forwarded HERE and not only in `curate` for the reason `models:`'s own
    docstring gives for being keyed by task rather than by verb: this verb
    and curate's Structure stage run the same suggester, and a setting that
    reached only one of them would let the two print rationales in
    different languages from one workspace."""
    _init_workspace(tmp_path, monkeypatch)
    path = tmp_path / "openkos.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "rationale_language: Spanish\n",
        encoding="utf-8",
    )
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    captured: dict[str, object] = {}

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        captured["kwargs"] = kwargs
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["rationale_language"] == "Spanish"


def test_suggest_relations_forwards_no_language_when_unpinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stock workspace forwards `None`, which is the pre-#812 prompt."""
    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    captured: dict[str, object] = {}

    def _fake_suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        captured["kwargs"] = kwargs
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["rationale_language"] is None


# --------------------------------------------------------------------------- #
# #802: the least-specific type says what it does not assert
# --------------------------------------------------------------------------- #


def test_the_least_specific_type_carries_its_own_caveat() -> None:
    """`related_to` is the rubric's honest answer when no specific type
    holds -- "the two are connected, and the documents do not support
    saying how". The operator was shown `[related_to]` above a rationale
    explaining why it is NOT any specific type, and asked to approve it,
    with nothing saying what approving it asserts."""
    assert main._suggestion_caveat("related_to") == (
        " (connected; the documents do not say how)"
    )


def test_the_direction_caveat_is_unchanged_byte_for_byte() -> None:
    """#778 fixed exactly one surface spelling a caveat while another
    stayed silent, and `docs/testing.md` documents this wording as the
    contract. Widening the helper must not reword it."""
    assert main._suggestion_caveat("caused_by") == (
        " (direction model-suggested, unverified)"
    )


def test_a_specific_symmetric_type_carries_no_caveat() -> None:
    """`references` asserts something and is symmetric: neither caveat
    applies, and inventing one would train the operator to ignore them."""
    assert main._suggestion_caveat("references") == ""


def test_the_two_caveats_can_never_collide() -> None:
    """One helper answers for every type because the two classes are
    disjoint -- the least-specific type is symmetric. A type in both would
    silently get whichever branch is written first."""
    assert LEAST_SPECIFIC_RELATION_TYPE not in ASYMMETRIC_RELATION_TYPES
