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
from openkos.llm.ollama import OllamaClient, OllamaModelNotFound, OllamaUnavailable
from openkos.resolution.edge_typing import EdgeSuggestion
from tests.unit.cli.conftest import disable_local_exemption
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

    def _fake_suggest(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
        captured["edges"] = edges
        return [
            _suggestion(
                source="concepts/a",
                target="concepts/b",
                suggested_type="related_to",
                rationale="mentions B",
            )
        ]

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

    def _fake_suggest(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
        captured["kwargs"] = kwargs
        return [
            _suggestion(
                source="concepts/a",
                target="concepts/b",
                suggested_type="references",
                rationale="mentions concept b",
            )
        ]

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

    def _fake_suggest(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
        return [_suggestion(suggested_type=None, rationale="malformed reply")]

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
    `adjudicate`'s wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    captured: dict[str, object] = {}

    def _recording_suggest(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _recording_suggest)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    llm = kwargs["llm"]
    assert isinstance(llm, OllamaClient)
    assert llm._model == configured_model


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

    def _raise_unavailable(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
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

    def _raise_model_not_found(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
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
    from openkos.llm.ollama import OllamaError

    _init_workspace(tmp_path, monkeypatch)
    _patch_candidate_edges(
        monkeypatch, [Edge(source_id="concepts/a", target_id="concepts/b")]
    )
    before = _snapshot(tmp_path)

    def _raise_generic(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
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

    def _must_not_run(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
        nonlocal called
        called = True
        return []

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

    def _fake_suggest(edges_arg: list[Edge], **kwargs: object) -> list[EdgeSuggestion]:
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
        return results

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
        bundle_dir: Path, *, candidates: sqlite_graph.CandidateSource | None = None
    ) -> object:
        build_calls.append(candidates)
        return real_build_graph(bundle_dir, candidates=candidates)

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
        bundle_dir: Path, *, candidates: sqlite_graph.CandidateSource | None = None
    ) -> sqlite_graph.SqliteGraphStore:
        calls.append(bundle_dir)
        return real(bundle_dir, candidates=candidates)

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
        bundle_dir: Path, *, candidates: sqlite_graph.CandidateSource | None = None
    ) -> sqlite_graph.SqliteGraphStore:
        store = real(bundle_dir, candidates=candidates)
        stores.append(store)
        return store

    monkeypatch.setattr("openkos.cli.main.build_graph", _counting_build_graph)
    monkeypatch.setattr("openkos.graph.summary.build_graph", _counting_build_graph)
    monkeypatch.setattr(
        "openkos.resolution.edge_typing.build_graph", _counting_build_graph
    )

    def _fake_suggest(edges: object, **kwargs: object) -> list[EdgeSuggestion]:
        assert len(stores) == 1
        store = stores[0]
        with pytest.raises(sqlite3.ProgrammingError):
            store.edges()
        return []

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
