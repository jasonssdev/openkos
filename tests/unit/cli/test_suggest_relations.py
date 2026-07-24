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
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.llm.ollama import OllamaClient, OllamaModelNotFound, OllamaUnavailable
from openkos.resolution.edge_typing import EdgeSuggestion

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


def _snapshot_entry(path: Path) -> tuple[bytes, int] | None:
    if path.is_dir():
        return None
    return path.read_bytes(), path.stat().st_mtime_ns


def _snapshot(root: Path) -> dict[Path, tuple[bytes, int] | None]:
    """Capture every entry under `root`, keyed by relative path, as its byte
    contents and `st_mtime_ns` -- so a rewrite-with-identical-bytes (touch)
    regression is caught, not just a content change."""
    return {path.relative_to(root): _snapshot_entry(path) for path in root.rglob("*")}


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle has zero untyped edges, so the
    real `suggest_relations` never calls `llm.chat` -- a real `OllamaClient`
    is safe to construct here. Prints a clear "no untyped relations" line
    and exits 0."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "No untyped relations found." in result.stdout


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--auto` is accepted and bypasses the confirmation gate (issue #134:
    the cost gate is opt-out for scripted/non-interactive use). On an empty
    bundle there is nothing to type, so it still exits 0 cleanly without ever
    prompting."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto"])

    assert result.exit_code == 0
    assert "No untyped relations found." in result.stdout
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
    safe to construct here."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "bundle scan was incomplete" in result.stderr


def test_suggest_relations_no_warning_on_clean_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully readable bundle produces no incomplete-walk warning (spec:
    Clean bundle produces no warning)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["suggest-relations"])

    assert result.exit_code == 0
    assert "bundle scan was incomplete" not in result.stderr


def test_suggest_relations_include_confidential_suppresses_the_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` suppresses the incomplete-walk warning too --
    the filter is deliberately off (spec: `--include-confidential`
    suppresses the warning)."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--include-confidential"])

    assert result.exit_code == 0
    assert "bundle scan was incomplete" not in result.stderr


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
