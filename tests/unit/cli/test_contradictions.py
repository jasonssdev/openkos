"""Unit tests for the `contradictions` CLI command: read-only LLM
contradiction-detection over typed graph-edge pairs (MVP-2 slice 3,
freshness-lint-v1 S3).

`contradictions` mirrors `adjudicate`/`suggest-relations`'s wiring exactly:
`config.require_workspace` gate -> `config.read_config` -> a real
`OllamaClient(model=cfg.model)` built from the workspace's configured model
-> `resolution.contradiction.find_contradictions` (which owns the internal
`build_graph` read). It is read-only: no writes, no `--auto`, no
confirmation gate.

Every test that needs a specific verdict OUTCOME patches
`openkos.cli.main.find_contradictions` directly (mirrors how
`test_suggest_relations.py` patches `openkos.cli.main.suggest_relations`) --
zero network, zero real Ollama process.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.llm.ollama import OllamaClient, OllamaModelNotFound, OllamaUnavailable
from openkos.resolution.contradiction import ContradictionVerdict, Verdict

runner = CliRunner()


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


def _write_relation_doc(
    path: Path,
    *,
    title: str,
    status: str | None = None,
    relations: list[tuple[str, str]] | None = None,
) -> None:
    """Write a minimal concept `.md` file with optional lifecycle `status`
    and typed `relations` (mirrors `test_contradiction.py`'s
    `_write_lifecycle_doc` helper, status-aware-retrieval Phase 4)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", f"title: {title}"]
    if status is not None:
        lines.append(f"status: {status}")
    if relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    lines.append("---")
    lines.append(f"# {title}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _FakeOllamaClient:
    """Structural `LLMBackend` stand-in substituted for the real
    `OllamaClient` -- so the default-exclude/`--include-deprecated` behavior
    tests run the REAL (unmocked) `find_contradictions` with zero network,
    zero real Ollama process (status-aware-retrieval Phase 4)."""

    def __init__(self, *, model: str, **kwargs: object) -> None:
        self.model = model

    def chat(self, messages: list[object]) -> str:
        return (
            '{"verdict": "contradicts", "confidence": 0.9, '
            '"rationale": "fake reply", "conflicting_claims": ["x"]}'
        )


def _verdict(
    *,
    source: str = "concepts/a",
    target: str = "concepts/b",
    verdict: Verdict = Verdict.CONTRADICTS,
    confidence: float = 0.9,
    rationale: str = "stub rationale",
    conflicting_claims: tuple[str, ...] = ("claim one", "claim two"),
) -> ContradictionVerdict:
    return ContradictionVerdict(
        pair_ids=(source, target),
        verdict=verdict,
        confidence=confidence,
        rationale=rationale,
        conflicting_claims=conflicting_claims,
    )


def test_contradictions_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `contradictions` refuses (exit 1), prints the
    shared `require_workspace` reason under a `contradictions`-specific
    prefix, and never calls the library function (spec: mirrors
    `adjudicate`/`suggest-relations`)."""
    monkeypatch.chdir(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.find_contradictions",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos contradictions: refusing to run -- no OpenKOS workspace "
        "found in this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_contradictions_malformed_config_maps_to_exit_one_before_calling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard) is caught,
    printed as a friendly stderr message, exits 1 with no raw traceback, and
    the library function is never reached."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.find_contradictions",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos contradictions: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert calls == []


# ---------------------------------------------------------------------------
# 3-tier ordered OllamaError handler (mirrors `adjudicate`/`suggest-relations`)
# ---------------------------------------------------------------------------


def test_contradictions_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _raise_unavailable(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _raise_unavailable)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos contradictions: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stderr.rstrip("\n").endswith(
        "Or run `openkos doctor` to diagnose the environment."
    )
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_contradictions_model_not_found_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    before = _snapshot(tmp_path)

    def _raise_model_not_found(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _raise_model_not_found)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos contradictions: failed -- ")
    assert "is not installed" in result.stderr
    assert f"ollama pull {configured_model}" in result.stderr
    assert "openkos doctor" not in result.stderr
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_contradictions_generic_ollama_error_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generic `OllamaError` (neither `OllamaUnavailable` nor
    `OllamaModelNotFound`) is caught by the 3rd-tier fallback handler."""
    from openkos.llm.ollama import OllamaError

    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _raise_generic(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        raise OllamaError("something else went wrong")

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _raise_generic)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos contradictions: failed -- something else went wrong.\n"
    )
    assert "Traceback" not in result.stderr
    assert _snapshot(tmp_path) == before


def test_contradictions_handler_order_specific_before_generic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OllamaUnavailable` and `OllamaModelNotFound` both subclass
    `OllamaError` -- the specific handlers MUST fire, not the generic
    fallback (proves handler ORDER, not just presence)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_unavailable(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        raise OllamaUnavailable("not reachable")

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _raise_unavailable)

    result = runner.invoke(app, ["contradictions"])

    assert "ollama serve" in result.stderr
    assert "is not installed" not in result.stderr


# ---------------------------------------------------------------------------
# Default view (high-confidence CONTRADICTS only) vs `--all`
# ---------------------------------------------------------------------------


def test_contradictions_default_view_shows_only_high_confidence_contradicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default view shows only `CONTRADICTS` with confidence >= 0.7; hides
    `CONSISTENT`/`UNCERTAIN` and low-confidence `CONTRADICTS` (spec: Default
    view hides CONSISTENT/UNCERTAIN, zero writes)."""
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        return (
            [
                _verdict(
                    source="concepts/a",
                    target="concepts/b",
                    verdict=Verdict.CONTRADICTS,
                    confidence=0.9,
                    rationale="high-confidence conflict",
                ),
                _verdict(
                    source="concepts/c",
                    target="concepts/d",
                    verdict=Verdict.CONTRADICTS,
                    confidence=0.4,
                    rationale="low-confidence conflict",
                ),
                _verdict(
                    source="concepts/e",
                    target="concepts/f",
                    verdict=Verdict.CONSISTENT,
                    confidence=0.95,
                    rationale="aligned",
                    conflicting_claims=(),
                ),
                _verdict(
                    source="concepts/g",
                    target="concepts/h",
                    verdict=Verdict.UNCERTAIN,
                    confidence=0.0,
                    rationale="unsure",
                    conflicting_claims=(),
                ),
            ],
            4,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "high-confidence conflict" in result.stdout
    assert "low-confidence conflict" not in result.stdout
    assert "aligned" not in result.stdout
    assert "unsure" not in result.stdout


def test_contradictions_all_flag_shows_every_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--all` reveals CONSISTENT/UNCERTAIN and low-confidence CONTRADICTS
    too (spec: `--all` shows CONSISTENT and UNCERTAIN too)."""
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        return (
            [
                _verdict(
                    source="concepts/a",
                    target="concepts/b",
                    verdict=Verdict.CONTRADICTS,
                    confidence=0.4,
                    rationale="low-confidence conflict",
                ),
                _verdict(
                    source="concepts/c",
                    target="concepts/d",
                    verdict=Verdict.CONSISTENT,
                    confidence=0.95,
                    rationale="aligned",
                    conflicting_claims=(),
                ),
                _verdict(
                    source="concepts/e",
                    target="concepts/f",
                    verdict=Verdict.UNCERTAIN,
                    confidence=0.0,
                    rationale="unsure",
                    conflicting_claims=(),
                ),
            ],
            3,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions", "--all"])

    assert result.exit_code == 0
    assert "low-confidence conflict" in result.stdout
    assert "aligned" in result.stdout
    assert "unsure" in result.stdout


def test_contradictions_all_flag_does_not_affect_find_contradictions_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--all` is a DISPLAY-only filter: `find_contradictions` is called
    identically regardless of the flag (spec: `--all` MUST NOT affect
    `find_contradictions`, which always judges every pair)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: list[Path] = []

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        captured.append(bundle_dir)
        return [_verdict(confidence=0.9)], 1

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    runner.invoke(app, ["contradictions"])
    runner.invoke(app, ["contradictions", "--all"])

    assert len(captured) == 2
    assert captured[0] == captured[1]


# ---------------------------------------------------------------------------
# Empty graph / no candidate pairs
# ---------------------------------------------------------------------------


def test_contradictions_fresh_bundle_reports_no_candidate_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly initialized, empty bundle has zero candidate pairs, so the
    real `find_contradictions` never calls `llm.chat` -- a real
    `OllamaClient` is safe to construct here. Prints a clear "no candidate
    pairs" line and exits 0 (spec: Empty graph yields clear message, no
    crash)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "No candidate pairs found." in result.stdout


def test_contradictions_no_candidate_pairs_never_calls_llm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    calls: list[object] = []

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        calls.append((bundle_dir, kwargs))
        return [], 0

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "No candidate pairs found." in result.stdout
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Cap-reached truncation notice
# ---------------------------------------------------------------------------


def test_contradictions_cap_reached_line_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cap-reached -> "N of M pairs shown (cap reached)" line present (spec:
    Cap truncation is reported)."""
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        return [_verdict(confidence=0.9)], 250

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "1 of 250 pairs shown (cap reached)" in result.stdout


def test_contradictions_no_cap_reached_line_when_under_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        return [_verdict(confidence=0.9)], 1

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "cap reached" not in result.stdout


# ---------------------------------------------------------------------------
# Zero writes
# ---------------------------------------------------------------------------


def test_contradictions_never_writes_to_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No file under the workspace is created, modified, or deleted on any
    run -- byte contents AND `st_mtime_ns` both unchanged (spec: zero
    writes)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    before = _snapshot(tmp_path)

    result = runner.invoke(app, ["contradictions"], catch_exceptions=True)

    # Regardless of exit code (a real Ollama may or may not be reachable in
    # this environment), the workspace bytes/mtimes must be identical --
    # `contradictions` never writes, whether it succeeds or degrades.
    assert _snapshot(tmp_path) == before
    if result.exit_code == 0:
        assert "openkos contradictions: workspace at" in result.stdout


def test_contradictions_never_writes_across_all_verdict_mix_scenarios(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero bundle writes across every rendered scenario -- default view,
    `--all`, cap-reached, and empty (spec: Verb performs zero writes)."""
    _init_workspace(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        return (
            [
                _verdict(verdict=Verdict.CONTRADICTS, confidence=0.9),
                _verdict(verdict=Verdict.CONSISTENT, confidence=0.5),
            ],
            250,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    runner.invoke(app, ["contradictions"])
    runner.invoke(app, ["contradictions", "--all"])

    assert _snapshot(tmp_path) == before


# ---------------------------------------------------------------------------
# Rendering + wiring
# ---------------------------------------------------------------------------


def test_contradictions_renders_pair_verdict_confidence_and_cited_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        return (
            [
                _verdict(
                    source="concepts/a",
                    target="concepts/b",
                    confidence=0.85,
                    rationale="dates conflict",
                    conflicting_claims=("meeting is Tuesday", "meeting is Wednesday"),
                )
            ],
            1,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "0.85" in result.stdout
    assert "dates conflict" in result.stdout
    assert "meeting is Tuesday" in result.stdout
    assert "meeting is Wednesday" in result.stdout


def test_contradictions_builds_ollama_client_from_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`contradictions` builds the `OllamaClient` from the model configured
    in `openkos.yaml`, not a hardcoded value (spec: mirrors `adjudicate`'s
    wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def _recording_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        captured["kwargs"] = kwargs
        return [], 0

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _recording_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    llm = kwargs["llm"]
    assert isinstance(llm, OllamaClient)
    assert llm._model == configured_model


def test_contradictions_no_auto_flag_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`contradictions` is read-only: no `--auto` or confirmation flag
    exists (spec: zero writes)."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["contradictions", "--auto"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)


# ---------------------------------------------------------------------------
# `--include-deprecated` (status-aware-retrieval Phase 4)
# ---------------------------------------------------------------------------


def test_contradictions_include_deprecated_flag_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-deprecated` is forwarded unchanged as
    `find_contradictions(..., include_deprecated=True)` (spec:
    `--include-deprecated` Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        captured["kwargs"] = kwargs
        return [], 0

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _recording_find)

    result = runner.invoke(app, ["contradictions", "--include-deprecated"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is True


def test_contradictions_omitted_include_deprecated_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--include-deprecated` forwards the safe default
    `include_deprecated=False` (spec: Deprecated Concepts Excluded By
    Default)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[list[ContradictionVerdict], int]:
        captured["kwargs"] = kwargs
        return [], 0

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _recording_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is False


def test_contradictions_default_excludes_a_pair_touching_a_superseded_concept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real (unmocked) `find_contradictions`, with a real `OllamaClient`
    substituted for a fake `LLMBackend`: a supersedes edge alone forms a
    candidate pair whose target is deprecated -- by default it is dropped
    before judgment, so `contradictions` renders "No candidate pairs
    found." (spec: Deprecated Concepts Excluded By Default -- contradiction
    candidates)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_relation_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "supersedes")],
    )
    _write_relation_doc(bundle_dir / "concepts" / "b.md", title="B")
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeOllamaClient)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "No candidate pairs found." in result.stdout


def test_contradictions_include_deprecated_restores_the_superseded_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same real bundle with `--include-deprecated` restores the pair,
    so `find_contradictions` actually judges it and `contradictions` renders
    the resulting verdict (spec: `--include-deprecated` Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    _write_relation_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "supersedes")],
    )
    _write_relation_doc(bundle_dir / "concepts" / "b.md", title="B")
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeOllamaClient)

    result = runner.invoke(app, ["contradictions", "--include-deprecated"])

    assert result.exit_code == 0
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "fake reply" in result.stdout


# --- integration proof (real bundle: examples/good-life-demo) ---------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOOD_LIFE_ROOT = _REPO_ROOT / "examples" / "good-life-demo"


def test_contradictions_over_good_life_demo_is_read_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running `contradictions` against the real `examples/good-life-demo`
    workspace writes nothing under the bundle regardless of outcome -- the
    real `OllamaClient` may or may not reach a live Ollama in this
    environment, but the zero-writes contract must hold either way."""
    assert _GOOD_LIFE_ROOT.is_dir(), f"missing example workspace: {_GOOD_LIFE_ROOT}"
    monkeypatch.chdir(_GOOD_LIFE_ROOT)
    bundle_dir = _GOOD_LIFE_ROOT / "bundle"
    before = _snapshot(bundle_dir)

    result = runner.invoke(app, ["contradictions"], catch_exceptions=True)

    assert _snapshot(bundle_dir) == before
    if result.exit_code == 0:
        assert "openkos contradictions: workspace at" in result.stdout
