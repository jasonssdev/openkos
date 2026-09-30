"""Unit tests for the `openkos revisions` CLI command (#1014 piece (a),
Phase B re-plan, Slice P7b): wires `application.revisions`'
`load_decisions` -> `plan_revisions` -> the one exact cost gate ->
`judge_revisions`, then renders `application.revisions_report.revisions_report`.

Mirrors `test_contradictions.py`'s posture -- `runner.invoke(app, [...])`
over a real `tmp_path` workspace, `openkos.cli.main.OllamaClient` replaced
by a module-local fake so every test is zero-network, zero-real-Ollama.
`_ScriptedLLM`/`_RaisingLLM` are byte-identical in shape to
`test_decision_revision.py`'s/`test_revisions_service.py`'s own doubles.

Candidate blocking is by embedding similarity (design.md Decision B1): a
fixture Decision's document vector is seeded directly into
`.openkos/vectors.db` via `vectorstore.open_vector_store`, mirroring
`test_revisions_service.py::_seed_decision_and_vector`'s shape -- two
Decisions sharing `_embed(dim_index)`'s `dim_index` score cosine similarity
`1.0` (well above `EMBEDDING_SIMILARITY_THRESHOLD`, 0.65); two different
indices score `0.0`.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config
from openkos.application import revisions as revisions_service
from openkos.cli.main import app
from openkos.llm.base import EMBED_DIM, Message
from openkos.llm.ollama import OllamaError
from openkos.model import okf
from openkos.resolution import decision_revision
from openkos.state import derived, reindex, revision_findings, vectorstore
from openkos.state.vectorstore import content_hash
from tests.unit.cli.conftest import disable_local_exemption
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

runner = CliRunner()

_MODEL = "bge-m3"
"""`config.DEFAULT_EMBEDDING_MODEL` -- the tag `openkos init` writes into a
fresh `openkos.yaml`, so every fixture vector's stored tag matches the
workspace's configured `embedding_model` by construction, unless a test
deliberately writes a different one (the model-mismatch case)."""


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke`
    call -- see `test_ingest.py::_simulate_tty` for why the CLASS method
    must be patched rather than the current `sys.stdin` instance."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _write_doc(
    path: Path,
    *,
    type_: str = "Decision",
    title: str | None = "Stub",
    sensitivity: str | None = "private",
    relations: list[tuple[str, str]] | None = None,
    provenance: list[str] | None = None,
    event_date: str | None = None,
    body: str = "Body.",
) -> None:
    """Write a minimal concept `.md` file. Byte-identical shape to
    `test_revisions_service.py::_write_doc`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"type: {type_}"]
    if title is not None:
        lines.append(f"title: {title}")
    if sensitivity is not None:
        lines.append(f"sensitivity: {sensitivity}")
    if relations is not None:
        lines.append("relations:")
        for target, rel_type in relations:
            lines.append(f"  - target: {target}")
            lines.append(f"    type: {rel_type}")
    if provenance is not None:
        lines.append("provenance:")
        lines.extend(f"  - {entry}" for entry in provenance)
    if event_date is not None:
        lines.append(f"event_date: {event_date}")
    lines.append("---")
    path.write_text("\n".join(lines) + f"\n{body}\n", encoding="utf-8")


def _embed(dim_index: int) -> list[float]:
    vector = [0.0] * EMBED_DIM
    vector[dim_index] = 1.0
    return vector


def _seed_decision_and_vector(
    tmp_path: Path,
    concept_id: str,
    dim_index: int,
    *,
    model: str = _MODEL,
    **doc_kwargs: object,
) -> Path:
    """Write one Decision `.md` file under `bundle/` and seed its CURRENT
    document vector in `.openkos/vectors.db`, as if `openkos reindex` had
    just run over it -- byte-identical shape to
    `test_revisions_service.py::_seed_decision_and_vector`, adapted to a
    real `bundle/` rooted at `tmp_path` (an `init`-ed workspace) instead of
    a hand-built `WorkspaceLayout`."""
    layout = config.WorkspaceLayout(tmp_path)
    path = layout.bundle_dir / f"{concept_id}.md"
    _write_doc(path, **doc_kwargs)  # type: ignore[arg-type]
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        store.upsert(concept_id, _embed(dim_index), content_hash(path.read_bytes()))
        store.write_model_tag(reindex.embedding_tag(model))
        store.commit()
    return path


def _bundle_snapshot(tmp_path: Path) -> dict[str, str]:
    layout = config.WorkspaceLayout(tmp_path)
    files: dict[str, str] = {}
    for path in okf.iter_bundle_markdown(layout.bundle_dir):
        if path.name in okf.RESERVED_FILENAMES:
            continue
        files[path.relative_to(layout.bundle_dir).as_posix()] = path.read_text(
            encoding="utf-8"
        )
    return files


def _record_current_finding(tmp_path: Path, pair_ids: tuple[str, str]) -> None:
    """Persist a finding for `pair_ids` whose stored digests are the REAL
    current ones -- the "already judged, nothing changed since" fixture
    shape the gate-never-fires test starts from."""
    layout = config.WorkspaceLayout(tmp_path)
    files = _bundle_snapshot(tmp_path)
    digests = revisions_service.revision_input_digests(layout, files, pair_ids)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        revision_findings.record_revision_findings(
            conn,
            [
                revision_findings.RevisionFinding(
                    pair_ids=pair_ids,
                    verdict="reaffirms",
                    confidence=0.9,
                    rationale="stub rationale",
                    quotes=(None, None),
                    dates=(None, None),
                    date_states=("none-reached", "none-reached"),
                    include_confidential=False,
                    prompt_version=decision_revision.JUDGE_PROMPT_VERSION,
                    input_digests=digests,
                )
            ],
        )
    finally:
        conn.close()


class _ScriptedLLM:
    """A structural `LLMBackend`: returns queued replies in call order,
    recording every call's messages. Byte-identical shape to
    `test_decision_revision.py`'s/`test_revisions_service.py`'s doubles,
    plus `locality` (`_resolve_local_exemption` reads it) and an `embed`
    method that raises -- `revisions` must never call it (design.md
    Decision B1: "revisions makes zero embedding calls")."""

    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self, replies: Sequence[str] = ()) -> None:
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def chat(self, messages: Sequence[Message]) -> str:
        self.calls.append(list(messages))
        if self._replies:
            return self._replies.pop(0)
        return '{"verdict": "unrelated", "confidence": 0.0}'

    def embed(self, *args: object, **kwargs: object) -> object:
        raise AssertionError(
            "openkos revisions must never call an embedder (design.md Decision B1)"
        )


class _RaisingLLM(_ScriptedLLM):
    """Raises `error` on its `error_at`-th (1-based) call, otherwise
    returns the next queued reply."""

    def __init__(
        self, replies: Sequence[str], *, error: BaseException, error_at: int
    ) -> None:
        super().__init__(replies)
        self.error = error
        self.error_at = error_at

    def chat(self, messages: Sequence[Message]) -> str:
        if len(self.calls) + 1 == self.error_at:
            self.calls.append(list(messages))
            raise self.error
        return super().chat(messages)


def _patch_llm(monkeypatch: pytest.MonkeyPatch, fake: _ScriptedLLM) -> _ScriptedLLM:
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: fake)
    return fake


def _verdict_reply(verdict: str = "unrelated", confidence: float = 0.5) -> str:
    return (
        f'{{"verdict": "{verdict}", "confidence": {confidence}, '
        '"rationale": "stub", "quote_first": null, "quote_second": null}}'
    )


# ---------------------------------------------------------------------------
# P7b.1 / P7b.2: experimental label + stderr notice
# ---------------------------------------------------------------------------


def test_help_labels_the_verb_experimental() -> None:
    result = runner.invoke(app, ["revisions", "--help"])

    assert result.exit_code == 0
    assert "[experimental]" in result.stdout


def test_every_run_prints_the_unmeasured_quality_notice_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _patch_llm(monkeypatch, _ScriptedLLM())

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0
    assert (
        "openkos revisions: experimental -- detection quality is "
        "unmeasured on real bundles; review every finding before applying "
        "it with 'openkos reconcile --from-findings'."
    ) in result.stderr


# ---------------------------------------------------------------------------
# P7b.3: no Decisions
# ---------------------------------------------------------------------------


def test_no_decisions_found_exits_zero_with_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    fake = _patch_llm(monkeypatch, _ScriptedLLM())

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0
    assert "No Decision objects found." in result.stdout
    assert fake.calls == []


# ---------------------------------------------------------------------------
# P7b.4: vector store absent / model-tag mismatch
# ---------------------------------------------------------------------------


def test_vector_store_absent_exits_zero_with_remedy_and_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    layout = config.WorkspaceLayout(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "a.md")
    assert not layout.vectors_db_path.exists()
    fake = _patch_llm(monkeypatch, _ScriptedLLM())

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0
    assert "no document embeddings found" in result.stderr
    assert "openkos reindex" in result.stderr
    assert fake.calls == []


def test_revisions_passes_backend_through_to_plan_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`revisions` passes `backend=cfg.backend` to
    `revisions_service.plan_revisions` (issue #1057 Phase 11, tasks
    11.18-11.21). **RED today**: the CLI call site omits `backend=`
    entirely."""
    _init_workspace(tmp_path, monkeypatch)
    layout = config.WorkspaceLayout(tmp_path)
    _write_doc(layout.bundle_dir / "decisions" / "a.md")
    _patch_llm(monkeypatch, _ScriptedLLM())

    calls: list[str] = []
    original_plan_revisions = revisions_service.plan_revisions

    def _spy(*args: object, backend: str, **kwargs: object) -> object:
        # `backend` has NO default here on purpose: a caller that omits
        # `backend=` entirely (the pre-Phase-11 CLI site) raises
        # `TypeError` before this test's own assertion ever runs -- the
        # test can then never pass vacuously on the default `ollama` path,
        # unlike a spy with `backend: str = "ollama"` would (which cannot
        # tell "wired and defaulted to ollama" apart from "never wired at
        # all", since this workspace's own `cfg.backend` is ollama either
        # way).
        calls.append(backend)
        return original_plan_revisions(*args, backend=backend, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(revisions_service, "plan_revisions", _spy)

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0, result.stdout
    assert calls == ["ollama"]


def test_vector_store_model_mismatch_exits_zero_with_remedy_and_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(
        tmp_path, "decisions/a", 0, model="a-different-model#chunk-v1"
    )
    fake = _patch_llm(monkeypatch, _ScriptedLLM())

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0
    assert "different embedding model" in result.stderr
    assert "openkos reindex" in result.stderr
    assert fake.calls == []


# ---------------------------------------------------------------------------
# P7b.5: the one gate's count matches the stub LLM's call count
# ---------------------------------------------------------------------------


def test_the_one_gate_count_matches_the_stub_llm_call_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3 Decisions sharing one embedding dimension form 3 candidate pairs
    (`a`/`b`, `a`/`c`, `b`/`c`) -- `a`/`b` is already served by a fresh
    persisted finding, so `to_judge` (2) is strictly SMALLER than
    `candidate_plan.candidates` (3). The printed gate count must be the
    POST-SERVING `to_judge` count, never the pre-exclusion candidate
    count -- kills a gate printing a pre-exclusion count."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 0)
    _seed_decision_and_vector(tmp_path, "decisions/c", 0)
    _record_current_finding(tmp_path, ("decisions/a", "decisions/b"))
    fake = _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply(), _verdict_reply()]))

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0
    assert len(fake.calls) == 2
    assert "3 candidate pair(s), 1 served -> 2 LLM call(s) to judge" in result.stderr


def test_truncation_notice_prints_before_the_gate_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression pin for the ordering rule -- a truncation notice, when
    present, is printed BEFORE the gate line (#378 precedent). Forces the
    notice with a monkeypatch rather than a 200+-pair fixture: this is a
    pure ordering check, not a re-test of `revision_truncation_notice`
    itself (already covered at the leaf level, Slice 3)."""
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 0)
    _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))
    monkeypatch.setattr(
        "openkos.cli.main.revision_truncation_notice",
        lambda plan: "FAKE TRUNCATION NOTICE",
    )

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0
    notice_at = result.stderr.index("FAKE TRUNCATION NOTICE")
    gate_at = result.stderr.index("LLM call(s) to judge")
    assert notice_at < gate_at


# ---------------------------------------------------------------------------
# P7b.6: the gate never fires at zero
# ---------------------------------------------------------------------------


def test_gate_never_fires_when_to_judge_is_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`effective_confidential` (`include_confidential OR local_exemption`)
    is one of `is_fresh`'s four strict-equality conditions -- disabling the
    local exemption pins it to `False`, matching the persisted fixture
    finding's `include_confidential=False`, so this pair is genuinely
    fresh and the gate has nothing left to ask about."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 0)
    _record_current_finding(tmp_path, ("decisions/a", "decisions/b"))
    fake = _patch_llm(monkeypatch, _ScriptedLLM())

    result = runner.invoke(app, ["revisions"])

    assert result.exit_code == 0
    assert "LLM call(s) to judge" not in result.stderr
    assert "Proceed?" not in result.stdout
    assert fake.calls == []


# ---------------------------------------------------------------------------
# P7b.7 / P7b.8 / P7b.9: the gate's three branches
# ---------------------------------------------------------------------------


def test_non_tty_without_auto_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 0)
    fake = _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))

    result = runner.invoke(app, ["revisions"])

    assert result.exit_code == 1
    assert (
        "refusing to spend model calls without confirmation -- stdin is "
        "not a TTY; re-run with --auto" in result.stderr
    )
    assert fake.calls == []


def test_tty_decline_exits_zero_with_no_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 0)
    fake = _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["revisions"], input="n\n")

    assert result.exit_code == 0
    assert "Aborted -- no revisions judged." in result.stdout
    assert fake.calls == []
    layout = config.WorkspaceLayout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        persisted = revision_findings.open_revision_findings(conn)
    finally:
        conn.close()
    assert persisted == ()


def test_auto_runs_unattended_on_non_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 0)
    fake = _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0
    assert len(fake.calls) == 1
    assert "Proceed?" not in result.stdout


# ---------------------------------------------------------------------------
# P7b.10: writes only .openkos/findings.db
# ---------------------------------------------------------------------------


def test_full_run_writes_no_bundle_file_and_no_other_derived_store_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 0)
    _patch_llm(
        monkeypatch, _ScriptedLLM([_verdict_reply(verdict="reverses", confidence=0.9)])
    )
    layout = config.WorkspaceLayout(tmp_path)

    bundle_before = _bundle_snapshot(tmp_path)
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        vectors_before = store.document_vectors(["decisions/a", "decisions/b"])
        tag_before = store.read_model_tag()

    result = runner.invoke(app, ["revisions", "--auto"])

    assert result.exit_code == 0
    bundle_after = _bundle_snapshot(tmp_path)
    assert bundle_after == bundle_before
    with vectorstore.open_vector_store(layout.vectors_db_path) as store:
        vectors_after = store.document_vectors(["decisions/a", "decisions/b"])
        tag_after = store.read_model_tag()
    assert vectors_after == vectors_before
    assert tag_after == tag_before

    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        persisted = revision_findings.open_revision_findings(conn)
    finally:
        conn.close()
    assert len(persisted) == 1
    assert persisted[0].pair_ids == ("decisions/a", "decisions/b")


# ---------------------------------------------------------------------------
# P7b.11: a partial batch renders completed then exits 1
# ---------------------------------------------------------------------------


def test_partial_batch_renders_completed_then_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _seed_decision_and_vector(tmp_path, "decisions/a", 0)
    _seed_decision_and_vector(tmp_path, "decisions/b", 0)
    _seed_decision_and_vector(tmp_path, "decisions/c", 1)
    _seed_decision_and_vector(tmp_path, "decisions/d", 1)
    fake = _patch_llm(
        monkeypatch,
        _RaisingLLM(
            [_verdict_reply(verdict="reverses", confidence=0.9)],
            error=OllamaError("boom"),
            error_at=2,
        ),
    )

    result = runner.invoke(app, ["revisions", "--auto"], catch_exceptions=True)

    assert result.exit_code == 1
    assert len(fake.calls) == 2
    assert "failed after judging 1 of 2 planned pair(s)" in result.stderr
    assert "boom" in result.stderr


# ---------------------------------------------------------------------------
# P7b.12: --include-confidential releases the judge send
# ---------------------------------------------------------------------------


def test_include_confidential_reduces_excluded_count_and_releases_the_judge_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    confidential_body = "The confidential rollout plan mentions Project Nightjar."
    _seed_decision_and_vector(
        tmp_path,
        "decisions/confidential",
        0,
        sensitivity="confidential",
        body=confidential_body,
    )
    _seed_decision_and_vector(tmp_path, "decisions/open", 0)

    fake = _patch_llm(monkeypatch, _ScriptedLLM())
    result = runner.invoke(app, ["revisions", "--auto"])
    assert result.exit_code == 0
    assert "No candidate Decision pairs found" in result.stdout
    assert fake.calls == []

    fake_with_flag = _patch_llm(monkeypatch, _ScriptedLLM([_verdict_reply()]))
    result = runner.invoke(app, ["revisions", "--auto", "--include-confidential"])
    assert result.exit_code == 0
    assert len(fake_with_flag.calls) == 1
    sent_bodies = " ".join(
        message["content"] for call in fake_with_flag.calls for message in call
    )
    assert "Project Nightjar" in sent_bodies
