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

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.bundle import decisions as bundle_decisions
from openkos.cli import main as contradiction_main
from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.resolution.contradiction import (
    CandidatePlan,
    ContradictionBatch,
    ContradictionVerdict,
    Verdict,
    _CandidateSpec,
)
from openkos.state import derived, findings
from openkos.state.vectorstore import content_hash
from openkos.vcs import git as vcs_git
from tests.unit.cli.conftest import disable_local_exemption
from tests.unit.cli.conftest import snapshot_with_mtime as _snapshot
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY
from tests.unit.vcs.conftest import isolate_git_identity

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


def _write_doc(
    path: Path,
    *,
    doc_type: str = "Concept",
    title: str = "Stub",
    sensitivity_value: str | None = "private",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {doc_type}", f"title: {title}"]
    if sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
    lines.append("---")
    lines.append(f"# {title}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_relation_doc(
    path: Path,
    *,
    title: str,
    status: str | None = None,
    relations: list[tuple[str, str]] | None = None,
    sensitivity_value: str | None = "private",
) -> None:
    """Write a minimal concept `.md` file with optional lifecycle `status`
    and typed `relations` (mirrors `test_contradiction.py`'s
    `_write_lifecycle_doc` helper, status-aware-retrieval Phase 4).
    `sensitivity_value` defaults to `"private"` (`config.DEFAULT_SENSITIVITY`)
    so fixtures unrelated to the sensitivity-fail-closed-filter feature are
    never collaterally blocked by the fail-closed default; pass `None`
    explicitly for the absent-field case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", f"title: {title}"]
    if status is not None:
        lines.append(f"status: {status}")
    if sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
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

    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

    def __init__(self, *, model: str, **kwargs: object) -> None:
        self.model = model

    def chat(self, messages: list[object]) -> str:
        return (
            '{"verdict": "contradicts", "confidence": 0.9, '
            '"rationale": "fake reply", "conflicting_claims": ["x"]}'
        )


def _truncated_plan() -> CandidatePlan:
    """A plan the shared `_MAX_PAIRS` budget truncated in BOTH kinds: 250
    candidates produced (250 typed-edge + 50 merged-body), 1 judged.

    Built from the real `CandidatePlan`/`_CandidateSpec` so it cannot drift
    from what `contradiction_truncation_notice` reads."""
    return CandidatePlan(
        specs=(
            _CandidateSpec(
                pair_ids=("concepts/a", "concepts/b"), relation_type="related_to"
            ),
        ),
        edge_total=201,
        merged_total=50,
    )


def _verdict(
    *,
    source: str = "concepts/a",
    target: str = "concepts/b",
    verdict: Verdict = Verdict.CONTRADICTS,
    confidence: float = 0.9,
    rationale: str = "stub rationale",
    conflicting_claims: tuple[str, ...] = ("claim one", "claim two"),
    merged_absorbed_id: str | None = None,
) -> ContradictionVerdict:
    return ContradictionVerdict(
        pair_ids=(source, target),
        verdict=verdict,
        confidence=confidence,
        rationale=rationale,
        conflicting_claims=conflicting_claims,
        merged_absorbed_id=merged_absorbed_id,
    )


def _found(
    verdicts: list[ContradictionVerdict], total: int
) -> tuple[ContradictionBatch, int]:
    """Wrap a complete-run fake's verdict list in the `(batch, total)` shape
    `find_contradictions` returns since #441 (`failure=None`)."""
    return ContradictionBatch(results=verdicts), total


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
    ) -> tuple[ContradictionBatch, int]:
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
    ) -> tuple[ContradictionBatch, int]:
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
    ) -> tuple[ContradictionBatch, int]:
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
    ) -> tuple[ContradictionBatch, int]:
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
    ) -> tuple[ContradictionBatch, int]:
        return _found(
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
    ) -> tuple[ContradictionBatch, int]:
        return _found(
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
    ) -> tuple[ContradictionBatch, int]:
        captured.append(bundle_dir)
        return _found([_verdict(confidence=0.9)], 1)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    runner.invoke(app, ["contradictions"])
    runner.invoke(app, ["contradictions", "--all"])

    assert len(captured) == 2
    assert captured[0] == captured[1]


# ---------------------------------------------------------------------------
# Empty graph / no candidate pairs
# ---------------------------------------------------------------------------


def test_contradictions_fresh_bundle_reports_no_typed_edges(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A freshly initialized, empty bundle has zero candidate pairs, so the
    real `find_contradictions` never calls `llm.chat` -- a real
    `OllamaClient` is safe to construct here. With `vectors.db` present
    (state 1's precondition), prints the no-typed-edges message and exits 0
    (spec: Empty graph yields clear message, no crash; "No typed edges at
    all")."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "The graph has no typed edges yet." in result.stdout


def test_contradictions_missing_vectors_db_reports_not_computable_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent `vectors.db` (state 3) reports candidates as not computable
    yet, distinct from the no-typed-edges state (spec: "Missing embeddings
    reports not-computable-yet")."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "Candidate relations unavailable" in result.stdout
    assert "vectors.db missing" in result.stdout
    assert "No concept relationships in the graph yet." not in result.stdout


def test_contradictions_no_candidate_pairs_never_calls_llm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    calls: list[object] = []

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        calls.append((bundle_dir, kwargs))
        return _found([], 0)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "The graph has no typed edges yet." in result.stdout
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Cap-reached truncation notice
# ---------------------------------------------------------------------------


def test_contradictions_cap_reached_line_names_which_kind_was_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cap-reached -> a truncation line naming WHICH KIND went unjudged
    (spec: Cap truncation is reported; issue #444).

    The line is driven by the `CandidatePlan` the verb builds and hands to
    `find_contradictions`, not by that function's return -- so it describes
    the exact list that was judged, and can break the total down per kind."""
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return _found([_verdict(confidence=0.9)], 250)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)
    monkeypatch.setattr(
        "openkos.cli.main.plan_candidates",
        lambda *a, **k: _truncated_plan(),
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "1 of 251 candidate(s) shown (cap reached)" in result.stdout
    assert "dropped: 200 typed-edge, 50 merged-body" in result.stdout


def test_contradictions_no_cap_reached_line_when_under_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return _found([_verdict(confidence=0.9)], 1)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "cap reached" not in result.stdout


# ---------------------------------------------------------------------------
# Candidate-edge cap truncation notice (#378 slice 2)
# ---------------------------------------------------------------------------


def test_contradictions_reports_candidate_truncation_when_the_cap_is_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When pass 3's candidate cap truncates the set, the truncation notice
    ("{retained} of {produced} candidate edge(s) shown (cap reached)") must
    be rendered -- distinct from the contradiction pair-cap notice above,
    and never silent."""
    from openkos.graph.proximity import ProximityPair

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
        def pairs(self, concept_ids: object) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        contradiction_main, "_open_proximity_or_degrade", lambda p: _StubSource()
    )
    monkeypatch.setattr(
        contradiction_main, "find_contradictions", lambda *a, **k: _found([], 0)
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "50 of 60 candidate edge(s) shown (cap reached)" in result.stdout


def test_contradictions_no_candidate_truncation_notice_under_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the candidate cap, `produced == retained` -- the candidate
    truncation notice must NOT appear."""
    from openkos.graph.proximity import ProximityPair

    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "a.md").write_text(
        "---\ntype: Concept\ntitle: A\n---\nBody.\n", encoding="utf-8"
    )
    (tmp_path / "bundle" / "concepts" / "b.md").write_text(
        "---\ntype: Concept\ntitle: B\n---\nBody.\n", encoding="utf-8"
    )

    class _StubSource:
        def pairs(self, concept_ids: object) -> list[ProximityPair]:
            return [
                ProximityPair(
                    source_id="concepts/a", target_id="concepts/b", distance=0.1
                )
            ]

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        contradiction_main, "_open_proximity_or_degrade", lambda p: _StubSource()
    )
    monkeypatch.setattr(
        contradiction_main, "find_contradictions", lambda *a, **k: _found([], 0)
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "candidate edge(s) shown" not in result.stdout


def test_contradictions_suppresses_candidate_notice_when_every_dropped_pair_is_confidential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#378 post-review correction: a truncated candidate set whose dropped
    pairs are ALL confidential must print NO notice by default -- mirrors
    `suggest-relations`'s parallel fixture."""
    from openkos.graph.proximity import ProximityPair

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
        if index > 50:
            (tmp_path / "bundle" / "concepts" / f"{leaf_id}.md").write_text(
                f"---\ntype: Concept\ntitle: {leaf_id}\n"
                "sensitivity: confidential\n---\nBody.\n",
                encoding="utf-8",
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
        def pairs(self, concept_ids: object) -> list[ProximityPair]:
            return pairs

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        contradiction_main, "_open_proximity_or_degrade", lambda p: _StubSource()
    )
    monkeypatch.setattr(
        contradiction_main, "find_contradictions", lambda *a, **k: _found([], 0)
    )

    default_run = runner.invoke(app, ["contradictions"])
    flagged_run = runner.invoke(app, ["contradictions", "--include-confidential"])

    assert default_run.exit_code == 0
    assert "candidate edge(s) shown" not in default_run.stdout
    assert flagged_run.exit_code == 0
    assert "50 of 60 candidate edge(s) shown (cap reached)" in flagged_run.stdout


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
    ) -> tuple[ContradictionBatch, int]:
        return _found(
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
    ) -> tuple[ContradictionBatch, int]:
        return _found(
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


def test_contradictions_renders_merged_body_verdict_distinctly_from_pair_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """surface-merged-body-contradictions (#409): a merged-body verdict
    (`merged_absorbed_id is not None`) is rendered distinctly from an
    ordinary pair verdict -- the survivor id once, with an explicit "merged
    content, absorbed <id>" annotation, never the `a <-> b` pair shape."""
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return _found(
            [
                _verdict(
                    source="concepts/apatheia",
                    target="concepts/apatheia",
                    confidence=0.9,
                    rationale="the two readings disagree",
                    merged_absorbed_id="concepts/apatheia-2",
                )
            ],
            1,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "concepts/apatheia" in result.stdout
    assert "concepts/apatheia-2" in result.stdout
    assert "merged content" in result.stdout
    assert "concepts/apatheia <-> concepts/apatheia" not in result.stdout


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
    ) -> tuple[ContradictionBatch, int]:
        captured["kwargs"] = kwargs
        return _found([], 0)

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
    ) -> tuple[ContradictionBatch, int]:
        captured["kwargs"] = kwargs
        return _found([], 0)

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
    ) -> tuple[ContradictionBatch, int]:
        captured["kwargs"] = kwargs
        return _found([], 0)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _recording_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is False


def test_contradictions_default_excludes_a_pair_touching_a_superseded_concept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """Real (unmocked) `find_contradictions`, with a real `OllamaClient`
    substituted for a fake `LLMBackend`: a supersedes edge alone forms a
    candidate pair whose target is deprecated -- by default it is dropped
    before judgment, so `contradictions` renders the state-2 "typed edges
    exist but none survive filtering" message (spec: Deprecated Concepts
    Excluded By Default -- contradiction candidates). `vectors.db` is
    touched so the outcome is state 2, not state 3."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
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
    assert "typed relation(s); none are contradiction candidates." in result.stdout


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


# ---------------------------------------------------------------------------
# `--include-confidential` (sensitivity-fail-closed-filter S3a)
# ---------------------------------------------------------------------------


def test_contradictions_include_confidential_flag_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` is forwarded unchanged as
    `find_contradictions(..., include_confidential=True)` (spec:
    `--include-confidential` Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        captured["kwargs"] = kwargs
        return _found([], 0)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _recording_find)

    result = runner.invoke(app, ["contradictions", "--include-confidential"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is True


def test_contradictions_omitted_include_confidential_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--include-confidential` forwards the safe default
    `include_confidential=False` (spec: Confidential Excluded By Default)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        captured["kwargs"] = kwargs
        return _found([], 0)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _recording_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is False


def test_contradictions_include_confidential_restores_the_confidential_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """A real bundle with a confidential concept: by default the pair is
    excluded (state 2's "none are contradiction candidates" message), and
    `--include-confidential` restores it, judging normally (spec:
    `--include-confidential` Escape Flag). `vectors.db` is touched so the
    outcome is state 2, not state 3."""
    _init_workspace(tmp_path, monkeypatch)
    seed_vectors_db(tmp_path)
    bundle_dir = tmp_path / "bundle"
    _write_relation_doc(
        bundle_dir / "concepts" / "a.md",
        title="A",
        relations=[("concepts/b", "references")],
        sensitivity_value="confidential",
    )
    _write_relation_doc(bundle_dir / "concepts" / "b.md", title="B")
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeOllamaClient)
    # The stand-in reports a LOCAL backend, where #240 grants the
    # confidential local exemption and the pair is legitimately included.
    # This test is about the gate itself, so opt out through the same
    # workspace switch a user would use.
    disable_local_exemption(tmp_path)

    default_result = runner.invoke(app, ["contradictions"])
    assert default_result.exit_code == 0
    assert (
        "typed relation(s); none are contradiction candidates." in default_result.stdout
    )

    included_result = runner.invoke(app, ["contradictions", "--include-confidential"])
    assert included_result.exit_code == 0
    assert "concepts/a" in included_result.stdout
    assert "concepts/b" in included_result.stdout
    assert "fake reply" in included_result.stdout


# ---------------------------------------------------------------------------
# directory-walk-observability follow-up: walk-incompleteness signal
# ---------------------------------------------------------------------------


def test_contradictions_warns_stderr_on_incomplete_walk_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete directory walk (`okf._walk_errors` non-empty) prints a
    self-explaining warning to STDERR and the command still exits 0 -- WARN,
    not refuse (spec: Incomplete walk warns and still exits 0). A freshly
    initialized, empty bundle has zero candidate pairs, so the real
    `find_contradictions` never calls `llm.chat` -- a real `OllamaClient` is
    safe to construct here.

    The default workspace grants the confidential local exemption (#240),
    which is the OTHER hatch that suppresses the FILTER-SCOPED half of this
    signal -- see
    `test_contradictions_local_exemption_keeps_the_general_advisory` below.
    This test is about the run where NEITHER hatch is set, so it opts out of
    the exemption through the same workspace switch a user would use, and
    both advisories are then true at once.

    Asserted through text unique to each: since #356 both lines open with
    `bundle scan was incomplete`, so that prefix no longer tells them
    apart."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" in result.stderr


def test_contradictions_no_warning_on_clean_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully readable bundle produces NEITHER advisory (spec: Clean bundle
    produces no warning) -- the walk coming back empty is the only thing
    that silences the #356 one, which no hatch suppresses."""
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" not in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_contradictions_include_confidential_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` suppresses the FILTER-SCOPED advisory only:
    the filter is deliberately off, so its message would be a claim about a
    filter that never ran. The #356 incomplete-inputs advisory still prints,
    and the command still exits 0 -- `contradictions` builds its graph
    projection from the same `okf._iter_docs` walk, so an unreadable subtree
    means fewer related pairs and fewer verdicts either way.

    The default workspace ALSO grants the confidential local exemption
    (#240), which independently suppresses the same filter-scoped message.
    Without opting out of that here, this test would keep passing even if
    `--include-confidential` were silently dropped from the
    `warn_if_walk_incomplete` call site, so it disables the exemption to
    make the FLAG the thing that discriminates."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["contradictions", "--include-confidential"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_contradictions_local_exemption_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confidential local exemption (#240) suppresses the FILTER-SCOPED
    advisory the SAME way `--include-confidential` does, and leaves the #356
    incomplete-inputs advisory printing.

    This is the STOCK workspace -- default local backend, exemption active
    -- which is exactly the path that emitted nothing at all before #356:
    the graph projection shrank silently, so did the candidate pairs, and
    the run still exited 0 reporting fewer contradictions than the bundle
    actually holds."""
    _init_workspace(tmp_path, monkeypatch)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


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


# --- Phase 3.2 (#183): the proximity seam ---------------------------------


class _ClosableSource:
    def __init__(self) -> None:
        self.closed = False

    def pairs(self, concept_ids: object) -> list[object]:
        return []

    def close(self) -> None:
        self.closed = True


def test_contradictions_closes_the_proximity_source_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam hands back a real SQLite connection; `contradictions` owns
    closing it."""
    _init_workspace(tmp_path, monkeypatch)
    source = _ClosableSource()
    monkeypatch.setattr(
        contradiction_main, "_open_proximity_or_degrade", lambda p: source
    )
    monkeypatch.setattr(
        contradiction_main, "find_contradictions", lambda *a, **k: _found([], 0)
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert source.closed is True


def test_contradictions_closes_the_proximity_source_when_the_llm_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error paths raise `typer.Exit` mid-command, so a close placed
    after the call would never run. A user whose Ollama is down must not
    also leak a file handle."""
    _init_workspace(tmp_path, monkeypatch)
    source = _ClosableSource()

    def _boom(*args: object, **kwargs: object) -> tuple[list[object], int]:
        raise OllamaUnavailable("connection refused")

    monkeypatch.setattr(
        contradiction_main, "_open_proximity_or_degrade", lambda p: source
    )
    monkeypatch.setattr(contradiction_main, "find_contradictions", _boom)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 1
    assert source.closed is True


# --- graph-projection-reuse (#196): one build per invocation ---------------


def test_contradictions_builds_the_graph_once_on_the_zero_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed_vectors_db: Callable[[Path], None],
) -> None:
    """#196: the zero-candidate path must not rebuild the projection for its
    "no typed edges" message."""
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
        "openkos.resolution.contradiction.build_graph", _counting_build_graph
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "The graph has no typed edges yet." in result.stdout


# ---------------------------------------------------------------------------
# Issue #190: TTY-gated per-pair progress wiring
# ---------------------------------------------------------------------------


def test_contradictions_wires_tty_gated_progress_callback_into_the_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, `contradictions` passes
    `observability.progress_callback`'s hook into `find_contradictions` as
    `on_progress`; each invocation renders `openkos contradictions:
    checking pair <i>/<n> - <elapsed>...` on STDERR while STDOUT keeps
    the clean report (issue #190, in-place + elapsed since #383/#384,
    mirroring `suggest-relations`' #134 wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        on_progress = kwargs["on_progress"]
        assert callable(on_progress)
        verdicts = [
            _verdict(
                source="concepts/a",
                target="concepts/b",
                verdict=Verdict.CONSISTENT,
                confidence=0.9,
                rationale="aligned",
                conflicting_claims=(),
            )
        ]
        for index, verdict in enumerate(verdicts, start=1):
            on_progress(index, len(verdicts), verdict)
        return _found(verdicts, 1)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "openkos contradictions: checking pair 1/1 - " in result.stderr
    assert "checking pair" not in result.stdout


def test_contradictions_passes_no_progress_hook_when_stderr_is_not_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a TTY (`CliRunner`'s default), the factory returns `None`
    and `find_contradictions` receives `on_progress=None` -- piped output
    stays byte-clean (issue #190)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        captured["on_progress"] = kwargs["on_progress"]
        return _found([], 0)

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert captured["on_progress"] is None


# ---------------------------------------------------------------------------
# issue #441: a partial batch keeps its completed verdicts
# ---------------------------------------------------------------------------


def _two_candidate_partial_batch(
    monkeypatch: pytest.MonkeyPatch, failure: OllamaError
) -> None:
    """Wire `plan_candidates` to two typed-edge candidates and
    `find_contradictions` to a batch whose first candidate completed and
    whose second candidate's chat raised `failure` -- the #441 mid-batch
    shape."""
    plan = CandidatePlan(
        specs=(
            _CandidateSpec(
                pair_ids=("concepts/a", "concepts/b"), relation_type="related_to"
            ),
            _CandidateSpec(
                pair_ids=("concepts/c", "concepts/d"), relation_type="related_to"
            ),
        ),
        edge_total=2,
        merged_total=0,
    )
    monkeypatch.setattr("openkos.cli.main.plan_candidates", lambda *a, **k: plan)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return (
            ContradictionBatch(
                results=[
                    _verdict(
                        source="concepts/a",
                        target="concepts/b",
                        confidence=0.9,
                        rationale="kept work",
                    )
                ],
                failure=failure,
                failed_index=2,
            ),
            2,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)


def test_contradictions_partial_batch_reports_completed_then_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-batch `OllamaError` no longer discards completed verdicts
    (#441): the report renders them exactly as a complete run over that list
    would, THEN one stderr line reports the failure with completed-of-total
    counts, and the exit code stays the OllamaError-family 1."""
    _init_workspace(tmp_path, monkeypatch)
    _two_candidate_partial_batch(monkeypatch, OllamaError("boom"))

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "[CONTRADICTS] concepts/a <-> concepts/b" in result.stdout
    assert "kept work" in result.stdout
    assert result.stderr == (
        "openkos contradictions: failed after judging 1 of 2 candidate(s) -- boom.\n"
    )
    assert "Traceback" not in result.stderr


def test_contradictions_partial_batch_unavailable_keeps_remediation_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OllamaUnavailable` batch failure keeps its cause-specific
    remediation (`ollama serve` + doctor hint), gains the completed-of-total
    counts, and still exits 1 (#441)."""
    _init_workspace(tmp_path, monkeypatch)
    _two_candidate_partial_batch(
        monkeypatch, OllamaUnavailable("Ollama not reachable at http://localhost:11434")
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 1
    assert "1 of 2" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stderr.rstrip("\n").endswith(
        "Or run `openkos doctor` to diagnose the environment."
    )
    assert "kept work" in result.stdout


def test_contradictions_partial_batch_model_not_found_keeps_pull_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An `OllamaModelNotFound` batch failure keeps the configured model's
    `ollama pull` remediation alongside the completed-of-total counts
    (#441)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    _two_candidate_partial_batch(
        monkeypatch, OllamaModelNotFound("Model not found (404)")
    )

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 1
    assert "1 of 2" in result.stderr
    assert f"ollama pull {configured_model}" in result.stderr
    assert "kept work" in result.stdout


def test_contradictions_first_candidate_failure_keeps_failure_over_zero_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch whose FIRST candidate failed carries zero completed verdicts
    -- the verb must NOT print the zero-candidates state message (there WERE
    candidates; the failure, not the graph, emptied the results) and must
    still exit 1 with the counted failure line (#441)."""
    plan = CandidatePlan(
        specs=(
            _CandidateSpec(
                pair_ids=("concepts/a", "concepts/b"), relation_type="related_to"
            ),
        ),
        edge_total=1,
        merged_total=0,
    )

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return (
            ContradictionBatch(results=[], failure=OllamaError("boom"), failed_index=1),
            1,
        )

    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.plan_candidates", lambda *a, **k: plan)
    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 1
    assert "The graph has no typed edges yet." not in result.stdout
    assert "Candidate relations unavailable" not in result.stdout
    assert result.stderr == (
        "openkos contradictions: failed after judging 0 of 1 candidate(s) -- boom.\n"
    )


def test_contradictions_merged_body_verdict_names_unmerge_as_the_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merged-content verdict names `unmerge` with the two ids it takes
    (#445).

    This matters more here than for an ordinary pair verdict: with two
    nodes an operator can open both files, but a merged-content verdict has
    only ONE node -- the second body lives in the ledger, where no ordinary
    read will show it. `unmerge <survivor> <absorbed>` is the one verb that
    separates them, and the output used to name the condition without
    naming the verb that resolves it."""
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return _found(
            [
                _verdict(
                    source="concepts/apatheia",
                    target="concepts/apatheia",
                    confidence=0.9,
                    rationale="the two readings disagree",
                    merged_absorbed_id="concepts/apatheia-2",
                )
            ],
            1,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    # Pinned as ONE rendered line, not three independent substrings: the
    # verb is LIFO-enforced and this command raises one candidate per ledger
    # entry, not just the newest, so the hint must state the precondition
    # rather than promise success -- and asserting the parts separately let
    # a stray double space at the concatenation boundary through unnoticed
    # (#486, cosmetic finding carried over from #445's review).
    assert (
        "  next: openkos unmerge concepts/apatheia concepts/apatheia-2 "
        "(LIFO-enforced: refuses unless this is the survivor's most recent "
        "unreversed merge)" in result.stdout
    )


def test_contradictions_pair_verdict_does_not_name_unmerge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary two-node pair verdict does NOT suggest `unmerge` (#445):
    nothing was merged, both bodies are readable on disk, and pointing at a
    reversal verb would be wrong."""
    _init_workspace(tmp_path, monkeypatch)

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return _found(
            [
                _verdict(
                    source="concepts/a",
                    target="concepts/b",
                    confidence=0.9,
                    rationale="dates conflict",
                )
            ],
            1,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    result = runner.invoke(app, ["contradictions"])

    assert result.exit_code == 0
    assert "unmerge" not in result.stdout


# ---------------------------------------------------------------------------
# PR #3 -- Slice B2: `--decline` / `--reopen` / `--declined` (pending-work
# design, Decisions 3, 5, 7; tasks B2.1-B2.13)
# ---------------------------------------------------------------------------


def _init_git_workspace(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors `test_main_autocommit.py`'s own `_init_workspace`: a real,
    git-backed workspace with an isolated, SET git identity, so
    `--decline`'s `_autocommit` call actually commits (needed by the B2.11/
    B2.13 scoped-staging tests, which the plain `_init_workspace` above
    does not set up)."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("contradictions-git-identity")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _last_commit_files(root: Path) -> set[str]:
    result = vcs_git._run(
        ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"], cwd=root
    )
    assert result.returncode == 0, result.stderr
    return {line for line in result.stdout.splitlines() if line}


def _status_porcelain(root: Path) -> str:
    result = vcs_git._run(["git", "status", "--porcelain"], cwd=root)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_decline_writes_a_decision_and_hides_the_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2.1 (pending-work spec: "Declining Is A Non-Interactive Verb...";
    "A declined finding stays out of ordinary output"): `--decline` writes
    a `state: declined` record under `bundle/.state/decisions/**`, and a
    subsequent ordinary `contradictions` run no longer shows that finding."""
    _init_workspace(tmp_path, monkeypatch)

    decline_result = runner.invoke(
        app, ["contradictions", "--decline", "concepts/a", "concepts/b"]
    )
    assert decline_result.exit_code == 0, decline_result.stderr

    decisions_path = (
        tmp_path / "bundle" / ".state" / "decisions" / "concepts" / "a.decisions.okf"
    )
    assert decisions_path.is_file()
    records = bundle_decisions.read_decisions("concepts/a", tmp_path / "bundle")
    assert len(records) == 1
    assert records[0].state == "declined"
    assert records[0].pair_ids == ("concepts/a", "concepts/b")

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return _found(
            [
                _verdict(
                    source="concepts/a",
                    target="concepts/b",
                    confidence=0.95,
                    rationale="should stay hidden",
                )
            ],
            1,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)

    ordinary_result = runner.invoke(app, ["contradictions"])

    assert ordinary_result.exit_code == 0
    assert "should stay hidden" not in ordinary_result.stdout


def test_decline_with_no_matching_findings_row_still_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2.3 (design Decision 7 corollary): `--decline` never reads
    `.openkos/findings.db` as a precondition -- it succeeds even though no
    finding was ever persisted for this pair (the row may have been
    purged), and never even creates the store as a side effect."""
    _init_workspace(tmp_path, monkeypatch)
    assert not (tmp_path / ".openkos" / "findings.db").exists()

    result = runner.invoke(
        app, ["contradictions", "--decline", "concepts/x", "concepts/y"]
    )

    assert result.exit_code == 0, result.stderr
    assert not (tmp_path / ".openkos" / "findings.db").exists()


def test_decline_typed_edge_and_merged_body_over_same_pair_stay_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2.4 (pending-work spec, Scenario "A typed-edge and a merged-body
    candidate over the same pair stay distinct"): declining one does not
    affect the other, and reopening one does not affect the other."""
    _init_workspace(tmp_path, monkeypatch)

    typed_result = runner.invoke(
        app, ["contradictions", "--decline", "concepts/a", "concepts/a"]
    )
    assert typed_result.exit_code == 0, typed_result.stderr

    merged_result = runner.invoke(
        app,
        [
            "contradictions",
            "--decline",
            "concepts/a",
            "concepts/a",
            "--merged-absorbed-id",
            "concepts/absorbed",
        ],
    )
    assert merged_result.exit_code == 0, merged_result.stderr

    records = bundle_decisions.read_decisions("concepts/a", tmp_path / "bundle")
    assert len(records) == 2
    states = {(record.merged_absorbed_id, record.state) for record in records}
    assert states == {(None, "declined"), ("concepts/absorbed", "declined")}

    reopen_result = runner.invoke(
        app, ["contradictions", "--reopen", "concepts/a", "concepts/a"]
    )
    assert reopen_result.exit_code == 0, reopen_result.stderr

    records = bundle_decisions.read_decisions("concepts/a", tmp_path / "bundle")
    by_merged = {record.merged_absorbed_id: record.state for record in records}
    assert by_merged[None] == "open"
    assert by_merged["concepts/absorbed"] == "declined"


def test_reopen_reinstates_a_declined_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2.5 (pending-work spec: "Re-Opening A Declined Finding Requires
    Explicit Operator Action", Scenario "Explicit re-open reinstates it")."""
    _init_workspace(tmp_path, monkeypatch)
    decline_result = runner.invoke(
        app, ["contradictions", "--decline", "concepts/a", "concepts/b"]
    )
    assert decline_result.exit_code == 0, decline_result.stderr

    reopen_result = runner.invoke(
        app, ["contradictions", "--reopen", "concepts/a", "concepts/b"]
    )
    assert reopen_result.exit_code == 0, reopen_result.stderr

    records = bundle_decisions.read_decisions("concepts/a", tmp_path / "bundle")
    assert len(records) == 1
    assert records[0].state == "open"


def test_content_change_does_not_reopen_a_declined_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2.7 (pending-work spec, Scenario "Content change does not silently
    reopen a decline"): a declined finding whose input concept is edited is
    marked stale on recompute, NOT reopened, and stays hidden from ordinary
    output."""
    _init_workspace(tmp_path, monkeypatch)
    concept_path = tmp_path / "bundle" / "concepts" / "a.md"
    concept_path.parent.mkdir(parents=True, exist_ok=True)
    concept_path.write_text(
        "---\ntype: Concept\ntitle: A\n---\n\n# A\n\nOriginal body.\n",
        encoding="utf-8",
    )

    decline_result = runner.invoke(
        app, ["contradictions", "--decline", "concepts/a", "concepts/b"]
    )
    assert decline_result.exit_code == 0, decline_result.stderr

    conn = derived.open_derived_connection(tmp_path / ".openkos" / "findings.db")
    try:
        findings.record_findings(
            conn,
            [
                findings.Finding(
                    pair_ids=("concepts/a", "concepts/b"),
                    merged_absorbed_id=None,
                    verdict="contradicts",
                    confidence=0.9,
                    rationale="original rationale",
                    input_digests=(
                        findings.InputDigest(
                            "concepts/a",
                            content_hash(concept_path.read_bytes()),
                        ),
                    ),
                )
            ],
        )
    finally:
        conn.close()

    concept_path.write_text(
        "---\ntype: Concept\ntitle: A\n---\n\n# A\n\nEdited body.\n",
        encoding="utf-8",
    )

    declined_view = runner.invoke(app, ["contradictions", "--declined"])
    assert declined_view.exit_code == 0, declined_view.stderr
    assert "concepts/a" in declined_view.stdout
    assert "stale" in declined_view.stdout.lower()

    records = bundle_decisions.read_decisions("concepts/a", tmp_path / "bundle")
    assert len(records) == 1
    assert records[0].state == "declined"  # NOT reopened by the edit

    def _fake_find(
        bundle_dir: Path, **kwargs: object
    ) -> tuple[ContradictionBatch, int]:
        return _found(
            [
                _verdict(
                    source="concepts/a",
                    target="concepts/b",
                    confidence=0.95,
                    rationale="should still stay hidden",
                )
            ],
            1,
        )

    monkeypatch.setattr("openkos.cli.main.find_contradictions", _fake_find)
    ordinary_result = runner.invoke(app, ["contradictions"])
    assert ordinary_result.exit_code == 0
    assert "should still stay hidden" not in ordinary_result.stdout


def test_declined_view_lists_declined_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B2.8 (pending-work spec: "Declined Findings Are Hidden By Default,
    With An Explicit Listing View", Scenario "The declined-listing view
    surfaces it")."""
    _init_workspace(tmp_path, monkeypatch)
    decline_result = runner.invoke(
        app, ["contradictions", "--decline", "concepts/a", "concepts/b"]
    )
    assert decline_result.exit_code == 0, decline_result.stderr

    result = runner.invoke(app, ["contradictions", "--declined"])

    assert result.exit_code == 0, result.stderr
    assert "concepts/a" in result.stdout
    assert "concepts/b" in result.stdout
    assert "declined" in result.stdout.lower()


def test_declined_view_is_empty_when_nothing_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)

    result = runner.invoke(app, ["contradictions", "--declined"])

    assert result.exit_code == 0, result.stderr
    assert "no declined" in result.stdout.lower()


def test_decline_stages_only_the_decision_path(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2.11 (workspace-autocommit spec: "Scoped Staging Only" delta):
    `--decline`'s auto-commit contains ONLY the written
    `bundle/.state/decisions/**` path -- `_autocommit`'s scoped `git add --
    <paths>`, never `-A`."""
    _init_git_workspace(tmp_path, tmp_path_factory, monkeypatch)

    result = runner.invoke(
        app, ["contradictions", "--decline", "concepts/a", "concepts/b"]
    )

    assert result.exit_code == 0, result.stderr
    committed = _last_commit_files(tmp_path)
    assert committed == {"bundle/.state/decisions/concepts/a.decisions.okf"}


def test_decline_leaves_unrelated_dirty_file_untouched(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B2.13 (workspace-autocommit spec, Scenario "Unrelated dirty file is
    left untouched"): a pre-existing dirty file elsewhere in the workspace
    is never swept into `--decline`'s auto-commit."""
    _init_git_workspace(tmp_path, tmp_path_factory, monkeypatch)
    (tmp_path / "unrelated.txt").write_text(
        "pre-existing dirty content", encoding="utf-8"
    )

    result = runner.invoke(
        app, ["contradictions", "--decline", "concepts/a", "concepts/b"]
    )

    assert result.exit_code == 0, result.stderr
    assert "unrelated.txt" not in _last_commit_files(tmp_path)
    assert "unrelated.txt" in _status_porcelain(tmp_path)
    assert (tmp_path / "unrelated.txt").read_text(encoding="utf-8") == (
        "pre-existing dirty content"
    )
