"""Byte-identity characterization tests for `openkos suggest-relations`
(issue #1168, findings slice), the `test_reindex_characterization.py` pattern.

The verb's orchestration moved into `application/suggest_relations_service.py`;
the CLI kept rendering, the cost-gate and per-item prompts, and the exit-code
mapping. The existing `test_suggest_relations.py` assertions pin individual
substrings; this file pins the COMPLETE `stdout` + `stderr` + exit code, what
the typing seam was called with, the persisted `edge_suggestions` rows, and
(for `--apply`) the written relation and the auto-commit, for every path of
the verb against goldens recorded BEFORE the move.
"""

import sqlite3
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from openkos.cli import main
from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.graph.proximity import ProximityPair
from openkos.llm.base import BackendError
from openkos.llm.ollama import OllamaError, OllamaModelNotFound, OllamaUnavailable
from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch
from tests.unit.cli.conftest import commit_pending_fixture_docs, disable_local_exemption
from tests.unit.cli.golden_support import Goldens, normalise
from tests.unit.cli.test_suggest_relations import (
    _break_os_walk,
    _init_workspace,
    _suggestion,
    _write_confidential_doc,
    _write_doc,
)

runner = CliRunner()

_GOLDENS = Goldens(
    Path(__file__).parent
    / "fixtures"
    / "suggest_relations_characterization_goldens.json"
)

_A_B = Edge(source_id="concepts/a", target_id="concepts/b")
_C_D = Edge(source_id="concepts/c", target_id="concepts/d")


def _rows(tmp_path: Path) -> list[list[Any]]:
    path = tmp_path / ".openkos" / "findings.db"
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        return [
            list(row)
            for row in conn.execute(
                "SELECT pair_key, suggested_type, rationale, "
                "include_confidential FROM edge_suggestions "
                "ORDER BY pair_key"
            )
        ]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _patch_edges(monkeypatch: pytest.MonkeyPatch, edges: list[Edge]) -> None:
    monkeypatch.setattr(
        "openkos.cli.main.candidate_edges", lambda bundle_dir, **kwargs: edges
    )


def _patch_typing(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict[str, Any]],
    tmp_path: Path,
    *,
    typed: Callable[[Edge], EdgeSuggestion] | None = None,
    failure: BackendError | None = None,
    keep: int | None = None,
) -> None:
    """Record every call to the typing seam and answer it: `typed(edge)` per
    edge (default: `references`), optionally cut to the first `keep` with
    `failure` carried, exactly as a mid-batch backend error reaches it."""

    def _default(edge: Edge) -> EdgeSuggestion:
        return _suggestion(
            source=edge.source_id,
            target=edge.target_id,
            suggested_type="references",
            rationale=f"{edge.source_id} cites {edge.target_id}",
        )

    def _fake(edges: Sequence[Edge], **kwargs: Any) -> EdgeSuggestionBatch:
        on_progress = kwargs.pop("on_progress")
        llm = kwargs.pop("llm")
        calls.append(
            {
                "edges": [[e.source_id, e.target_id] for e in edges],
                "llm_type": type(llm).__name__,
                "on_progress": "callable"
                if callable(on_progress)
                else repr(on_progress),
                **{
                    key: normalise(repr(value), tmp_path)
                    for key, value in sorted(kwargs.items())
                },
            }
        )
        results = [(typed or _default)(edge) for edge in edges]
        if keep is not None:
            results = results[:keep]
        for index, result in enumerate(results, start=1):
            on_progress(index, len(edges), result)
        return EdgeSuggestionBatch(
            results=results,
            failure=failure,
            failed_index=(keep or 0) + 1 if failure is not None else None,
        )

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _fake)


def _git_subjects(tmp_path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%s"],  # noqa: S607 -- git is on PATH in every environment here
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.splitlines()


def _run(
    scenario: str,
    tmp_path: Path,
    args: list[str],
    *,
    stdin: str | None = None,
    calls: list[dict[str, Any]] | None = None,
    extra: Callable[[], dict[str, Any]] | None = None,
) -> Result:
    result = runner.invoke(app, ["suggest-relations", *args], input=stdin)
    actual: dict[str, Any] = {
        "exit_code": result.exit_code,
        "stdout": normalise(result.stdout, tmp_path),
        "stderr": normalise(result.stderr, tmp_path),
        "calls": list(calls) if calls is not None else [],
        "rows": _rows(tmp_path),
    }
    if calls is not None:
        calls.clear()
    if extra is not None:
        actual["extra"] = extra()
    _GOLDENS.check(scenario, actual)
    return result


def _two_docs(tmp_path: Path) -> None:
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")


# -- refusals and zero-candidate states --------------------------------------


def test_missing_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _run("missing_workspace", tmp_path, [])


def test_malformed_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    _run("malformed_config", tmp_path, [])


def test_zero_embeddings_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _run("zero_embeddings_missing", tmp_path, [])


def test_zero_no_relationships(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    _run("zero_no_relationships", tmp_path, [])


def test_zero_none_untyped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\nsensitivity: private\n"
        "relations:\n  - target: concepts/b\n    type: relates_to\n---\nBody.\n",
        encoding="utf-8",
    )
    (concepts / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\nsensitivity: private\n---\n# B\n",
        encoding="utf-8",
    )
    _run("zero_none_untyped", tmp_path, [])


def test_zero_all_excluded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
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
    _run("zero_all_excluded", tmp_path, [])


def test_zero_all_excluded_by_confidentiality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="A")
    _write_confidential_doc(tmp_path / "bundle" / "concepts" / "b.md", title="B")

    class _Source:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return [ProximityPair("concepts/a", "concepts/b", 0.1)]

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _Source())
    disable_local_exemption(tmp_path)
    _run("zero_all_excluded_by_confidentiality", tmp_path, [])


def _sixty_pair_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "hub.md").write_text(
        "---\ntype: Concept\ntitle: Hub\nsensitivity: private\n---\nBody.\n",
        encoding="utf-8",
    )
    pairs = []
    for index in range(1, 61):
        leaf = f"leaf{index:03d}"
        (concepts / f"{leaf}.md").write_text(
            f"---\ntype: Concept\ntitle: {leaf}\nsensitivity: private\n---\nBody.\n",
            encoding="utf-8",
        )
        pairs.append(ProximityPair("concepts/hub", f"concepts/{leaf}", index * 0.001))

    class _Source:
        def pairs(self, concept_ids: Sequence[str]) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(main, "_open_proximity_or_degrade", lambda path: _Source())


def test_capped_run_names_the_next_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sixty_pair_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run("capped_run", tmp_path, ["--auto"], calls=calls)


def test_edge_offset_browses_the_next_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sixty_pair_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run("edge_offset_50", tmp_path, ["--auto", "--edge-offset", "50"], calls=calls)


def test_edge_offset_beyond_the_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sixty_pair_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run("edge_offset_beyond", tmp_path, ["--auto", "--edge-offset", "60"], calls=calls)


# -- the listing -------------------------------------------------------------


def test_listing_with_every_caveat_and_a_degrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _two_docs(tmp_path)
    edges = [
        Edge("concepts/a", "concepts/b"),
        Edge("concepts/c", "concepts/d"),
        Edge("concepts/e", "concepts/f"),
        Edge("concepts/g", "concepts/h"),
        Edge("concepts/i", "concepts/j"),
    ]
    _patch_edges(monkeypatch, edges)
    by_source = {
        "concepts/a": ("references", None),
        "concepts/c": ("caused_by", None),
        "concepts/e": ("related_to", None),
        "concepts/g": (None, None),
        "concepts/i": ("member_of", Edge("concepts/j", "concepts/i")),
    }

    def _typed(edge: Edge) -> EdgeSuggestion:
        kind, corrected = by_source[edge.source_id]
        return _suggestion(
            source=edge.source_id,
            target=edge.target_id,
            suggested_type=kind,
            rationale="because",
            corrected_edge=corrected,
        )

    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path, typed=_typed)
    _run("listing_all_shapes", tmp_path, ["--auto"], calls=calls)


def test_walk_incomplete_warning_and_include_confidential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)
    _patch_edges(monkeypatch, [_A_B])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run(
        "walk_incomplete_include_confidential",
        tmp_path,
        ["--auto", "--include-confidential"],
        calls=calls,
    )


def test_pinned_rationale_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    with (tmp_path / "openkos.yaml").open("a", encoding="utf-8") as handle:
        handle.write("rationale_language: Spanish\n")
    _patch_edges(monkeypatch, [_A_B])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run("rationale_language", tmp_path, ["--auto"], calls=calls)


# -- the cost gate and progress ---------------------------------------------


def test_gate_declined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_edges(monkeypatch, [_A_B, _C_D])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run("gate_declined", tmp_path, [], stdin="n\n", calls=calls)


def test_gate_confirmed_with_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_edges(monkeypatch, [_A_B, _C_D])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run("gate_confirmed", tmp_path, [], stdin="y\n", calls=calls)


# -- serve, persist, fresh ---------------------------------------------------


def test_repeat_run_serves_then_fresh_bypasses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _two_docs(tmp_path)
    _write_doc(tmp_path / "bundle" / "concepts" / "c.md", title="C")
    _write_doc(tmp_path / "bundle" / "concepts" / "d.md", title="D")
    _patch_edges(monkeypatch, [_A_B, _C_D])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run("serve_first_run", tmp_path, ["--auto"], calls=calls)
    _run("serve_second_run_fully_served", tmp_path, ["--auto"], calls=calls)
    _run("serve_gate_fully_served", tmp_path, [], stdin="y\n", calls=calls)
    _run("serve_fresh_bypass", tmp_path, ["--auto", "--fresh"], calls=calls)


def test_partly_served_gate_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    for name in "abcd":
        _write_doc(tmp_path / "bundle" / "concepts" / f"{name}.md", title=name.upper())
    _patch_edges(monkeypatch, [_A_B])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    runner.invoke(app, ["suggest-relations", "--auto"])
    _patch_edges(monkeypatch, [_A_B, _C_D])
    calls.clear()
    _run("partly_served_gate", tmp_path, [], stdin="y\n", calls=calls)


def test_a_degrade_is_not_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    for name in "abcd":
        _write_doc(tmp_path / "bundle" / "concepts" / f"{name}.md", title=name.upper())
    _patch_edges(monkeypatch, [_A_B, _C_D])

    def _typed(edge: Edge) -> EdgeSuggestion:
        return _suggestion(
            source=edge.source_id,
            target=edge.target_id,
            suggested_type=None if edge.source_id == "concepts/c" else "references",
        )

    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path, typed=_typed)
    _run("degrade_first_run", tmp_path, ["--auto"], calls=calls)
    _run("degrade_second_run", tmp_path, ["--auto"], calls=calls)


def test_corrupt_store_degrades_to_a_full_fresh_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _two_docs(tmp_path)
    _patch_edges(monkeypatch, [_A_B])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    runner.invoke(app, ["suggest-relations", "--auto"])
    (tmp_path / ".openkos" / "findings.db").write_bytes(b"not a database at all")
    calls.clear()
    # The gate is exercised too: an unreadable store reports no split line.
    _run("corrupt_store", tmp_path, [], stdin="y\n", calls=calls)


def test_persist_failure_is_a_warning_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _two_docs(tmp_path)
    _patch_edges(monkeypatch, [_A_B])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)

    def _boom(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("openkos.state.edge_suggestions.record_edge_suggestions", _boom)
    _run("persist_failure", tmp_path, ["--auto"], calls=calls)


# -- backend failures --------------------------------------------------------


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
    _two_docs(tmp_path)
    _patch_edges(monkeypatch, [_A_B, _C_D])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path, failure=error, keep=1)
    _run(scenario, tmp_path, ["--auto"], calls=calls)


@pytest.mark.parametrize(
    ("scenario", "error", "wording"),
    [
        (
            "partial_unavailable_openai_compatible",
            OllamaUnavailable("server down"),
            "Start your OpenAI-compatible server at 127.0.0.1:1",
        ),
        (
            "partial_model_not_found_openai_compatible",
            OllamaModelNotFound("nope"),
            "Make sure your OpenAI-compatible server serves 'qwen3:8b'",
        ),
    ],
)
def test_partial_batch_on_an_openai_compatible_workspace_names_the_backend(
    scenario: str,
    error: OllamaError,
    wording: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The partial-batch line follows the configured backend too (#1175)."""
    _init_workspace(tmp_path, monkeypatch)
    with (tmp_path / "openkos.yaml").open("a", encoding="utf-8") as handle:
        handle.write("backend: openai-compatible\nbase_url: http://127.0.0.1:1/v1\n")
    monkeypatch.delenv("OPENKOS_OPENAI_API_KEY", raising=False)
    _two_docs(tmp_path)
    _patch_edges(monkeypatch, [_A_B, _C_D])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path, failure=error, keep=1)
    result = _run(scenario, tmp_path, ["--auto"], calls=calls)
    assert "ollama" not in result.stderr.lower()
    assert wording in result.stderr


def test_partial_batch_with_the_truncation_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sixty_pair_workspace(tmp_path, monkeypatch)
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path, failure=OllamaError("boom"), keep=2)
    _run("partial_with_truncation", tmp_path, ["--auto"], calls=calls)


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
    _patch_edges(monkeypatch, [_A_B])

    def _raise(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _raise)
    _run(scenario, tmp_path, ["--auto"])


def test_raise_path_on_an_openai_compatible_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    with (tmp_path / "openkos.yaml").open("a", encoding="utf-8") as handle:
        handle.write("backend: openai-compatible\nbase_url: http://127.0.0.1:1/v1\n")
    monkeypatch.delenv("OPENKOS_OPENAI_API_KEY", raising=False)
    _patch_edges(monkeypatch, [_A_B])

    def _raise_unavailable(*args: object, **kwargs: object) -> None:
        raise OllamaUnavailable("server down")

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _raise_unavailable)
    _run("raised_unavailable_openai_compatible", tmp_path, ["--auto"])

    def _raise_missing(*args: object, **kwargs: object) -> None:
        raise OllamaModelNotFound("nope")

    monkeypatch.setattr("openkos.cli.main.suggest_edge_types", _raise_missing)
    _run("raised_model_not_found_openai_compatible", tmp_path, ["--auto"])


# -- --apply ----------------------------------------------------------------


def _apply_state(tmp_path: Path, *concept_ids: str) -> Callable[[], dict[str, Any]]:
    def _state() -> dict[str, Any]:
        return {
            "files": {
                concept_id: (tmp_path / "bundle" / f"{concept_id}.md").read_text(
                    encoding="utf-8"
                )
                for concept_id in concept_ids
            },
            "git_subjects": _git_subjects(tmp_path),
        }

    return _state


def _apply_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    _write_doc(
        tmp_path / "bundle" / "projects" / "p.md", title="Proj", doc_type="Project"
    )
    commit_pending_fixture_docs()


@pytest.mark.usefixtures("pinned_git_identity")
def test_apply_accept_decline_skip_and_degrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply_workspace(tmp_path, monkeypatch)
    edges = [
        Edge("concepts/a", "concepts/b"),
        Edge("concepts/b", "projects/p"),
        Edge("projects/p", "concepts/a"),
        Edge("concepts/a", "projects/p"),
    ]
    _patch_edges(monkeypatch, edges)
    kinds = {
        ("concepts/a", "concepts/b"): "references",
        ("concepts/b", "projects/p"): "produced_by",
        ("projects/p", "concepts/a"): None,
        ("concepts/a", "projects/p"): "related_to",
    }

    def _typed(edge: Edge) -> EdgeSuggestion:
        return _suggestion(
            source=edge.source_id,
            target=edge.target_id,
            suggested_type=kinds[(edge.source_id, edge.target_id)],
            rationale="because",
        )

    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path, typed=_typed)
    _run(
        "apply_mixed_walk",
        tmp_path,
        ["--auto", "--apply"],
        stdin="y\nn\ny\n",
        calls=calls,
        extra=_apply_state(tmp_path, "concepts/a", "concepts/b", "projects/p"),
    )


@pytest.mark.usefixtures("pinned_git_identity")
def test_apply_corrected_direction_and_cap_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply_workspace(tmp_path, monkeypatch)
    _patch_edges(monkeypatch, [Edge("concepts/a", "concepts/b")])

    def _typed(edge: Edge) -> EdgeSuggestion:
        return _suggestion(
            source=edge.source_id,
            target=edge.target_id,
            suggested_type="member_of",
            rationale="corrected",
            corrected_edge=Edge("concepts/b", "concepts/a"),
        )

    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path, typed=_typed)
    _run(
        "apply_corrected",
        tmp_path,
        ["--auto", "--apply"],
        stdin="y\n",
        calls=calls,
        extra=_apply_state(tmp_path, "concepts/a", "concepts/b"),
    )


@pytest.mark.usefixtures("pinned_git_identity")
def test_apply_with_nothing_to_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply_workspace(tmp_path, monkeypatch)
    _patch_edges(monkeypatch, [Edge("concepts/a", "concepts/b")])
    calls: list[dict[str, Any]] = []
    _patch_typing(
        monkeypatch, calls, tmp_path, typed=lambda e: _suggestion(suggested_type=None)
    )
    _run("apply_only_degrades", tmp_path, ["--auto", "--apply"], calls=calls)
    _patch_typing(monkeypatch, calls, tmp_path, keep=0, failure=OllamaError("boom"))
    _run(
        "apply_empty_results_with_failure", tmp_path, ["--auto", "--apply"], calls=calls
    )


@pytest.mark.usefixtures("pinned_git_identity")
def test_apply_already_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: Alpha\nrelations:\n"
        "  - target: concepts/b\n    type: references\n---\n# Alpha\n",
        encoding="utf-8",
    )
    commit_pending_fixture_docs()
    _patch_edges(monkeypatch, [Edge("concepts/a", "concepts/b")])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run(
        "apply_already_present",
        tmp_path,
        ["--auto", "--apply"],
        stdin="y\n",
        calls=calls,
        extra=_apply_state(tmp_path, "concepts/a"),
    )


@pytest.mark.usefixtures("pinned_git_identity")
def test_apply_prepare_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_workspace(tmp_path, monkeypatch)
    _patch_edges(monkeypatch, [Edge("concepts/a", "concepts/b")])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)

    def _prepare_boom(*args: object, **kwargs: object) -> None:
        raise OSError("prepare exploded")

    monkeypatch.setattr("openkos.application.lifecycle.prepare_relate", _prepare_boom)
    _run("apply_prepare_failure", tmp_path, ["--auto", "--apply"], stdin="y\n")


@pytest.mark.usefixtures("pinned_git_identity")
def test_apply_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _apply_workspace(tmp_path, monkeypatch)
    _patch_edges(monkeypatch, [Edge("concepts/a", "concepts/b")])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)

    def _write_boom(*args: object, **kwargs: object) -> None:
        raise ValueError("write exploded")

    monkeypatch.setattr("openkos.application.lifecycle.relate_core", _write_boom)
    _run("apply_write_failure", tmp_path, ["--auto", "--apply"], stdin="y\n")


@pytest.mark.usefixtures("pinned_git_identity")
def test_apply_drift_between_prompt_and_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _apply_workspace(tmp_path, monkeypatch)
    _patch_edges(monkeypatch, [Edge("concepts/a", "concepts/b")])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)

    from openkos.application import lifecycle as application_lifecycle

    real_prepare = application_lifecycle.prepare_relate

    def _prepare_then_drift(*args: Any, **kwargs: Any) -> Any:
        prepared = real_prepare(*args, **kwargs)
        with (tmp_path / "bundle" / "concepts" / "a.md").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write("edited behind our back\n")
        return prepared

    monkeypatch.setattr(
        "openkos.application.lifecycle.prepare_relate", _prepare_then_drift
    )
    _run(
        "apply_drift",
        tmp_path,
        ["--auto", "--apply"],
        stdin="y\n",
        extra=_apply_state(tmp_path, "concepts/a"),
    )


def test_apply_without_a_git_identity_warns_after_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auto-commit degrades to a stderr warning; the write still lands.
    An isolated empty git config keeps the identity unset on every machine."""
    _apply_workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-such-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "no-home"))
    monkeypatch.delenv("GIT_AUTHOR_NAME", raising=False)
    monkeypatch.delenv("GIT_AUTHOR_EMAIL", raising=False)
    _patch_edges(monkeypatch, [Edge("concepts/a", "concepts/b")])
    calls: list[dict[str, Any]] = []
    _patch_typing(monkeypatch, calls, tmp_path)
    _run(
        "apply_no_git_identity",
        tmp_path,
        ["--auto", "--apply"],
        stdin="y\n",
        calls=calls,
        extra=lambda: {
            "a": (tmp_path / "bundle" / "concepts" / "a.md").read_text(encoding="utf-8")
        },
    )
