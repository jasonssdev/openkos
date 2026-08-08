"""Unit tests for the `query` CLI command: the MVP-1 query chain entry point.

`query` is the read-only counterpart to `status`/`lint` (D1: bare
`require_workspace` gate, no Phase B, no confirm gate, no `--auto`), followed
by a Phase-A `read_config` guard (`except (OSError, ValueError)`, lint
parity) and a Phase-B `answer()` call guarded by four ORDERED handlers --
`OllamaUnavailable`, then `OllamaModelNotFound`, then
`OllamaEmbeddingDimensionMismatch` (issue #209), then the generic
`(FtsUnavailable, OllamaError)` fallback -- the first three carry their own
cause-specific actionable remediation, the fourth a deliberately generic,
non-actionable-specific message. Most tests patch `openkos.cli.main.answer`
(D4) -- zero network, zero real Ollama process; the few end-to-end pins
substitute `openkos.cli.main.OllamaClient` and drive the REAL `answer()`.
"""

import ast
import os
import urllib.error
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.llm.base import EMBED_DIM
from openkos.llm.ollama import (
    OllamaClient,
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.retrieval.answer import NO_MATCH, AnswerResult, Citation
from openkos.state import fts, vectorstore
from openkos.state.fts import FtsUnavailable
from openkos.state.vectorstore import VecUnavailable
from tests.unit.cli.conftest import disable_local_exemption
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

_REPO_ROOT = Path(__file__).resolve().parents[3]

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


def _fake_no_match_answer(question: str, **kwargs: object) -> AnswerResult:
    return AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="zero_hits",
        skip_notices=[],
    )


class _FakeOllamaClient:
    """Structural `LLMBackend`/`Embedder` stand-in substituted for the real
    `OllamaClient` -- so the R3-pin end-to-end test below runs the REAL
    `answer()` (not mocked) through the CLI with zero network, zero real
    Ollama process (status-aware-retrieval Phase 4)."""

    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

    def __init__(self, *, model: str, **kwargs: object) -> None:
        self.model = model

    def chat(self, messages: list[object]) -> str:
        return "a fake answer"


class _FakeHealthyEmbedOllamaClient(_FakeOllamaClient):
    """Control half of the unconditional-refusal pin: a HEALTHY `embed`."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]


class _FakeWrongWidthEmbedOllamaClient(_FakeOllamaClient):
    """`embed` raises the mismatch with production's exact message, surfacing
    it from the EMBEDDER seam inside the real `answer()` after `_fts_search`
    returned hits. Raised directly: replacing this class hides the validator."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise OllamaEmbeddingDimensionMismatch(
            f"Ollama returned an embedding row of length 768, expected "
            f"exactly {EMBED_DIM} (EMBED_DIM) -- this is a permanent "
            "dimension mismatch caused by the configured embedding model, "
            "not a transient failure; it will not heal by retrying."
        )


def _write_query_doc(
    path: Path,
    *,
    title: str,
    status: str | None = None,
    sensitivity_value: str | None = "private",
) -> None:
    """Write a minimal concept `.md` file whose body is indexable by the real
    FTS index, with an optional lifecycle `status` (status-aware-retrieval
    Phase 4). `sensitivity_value` defaults to `"private"`
    (`config.DEFAULT_SENSITIVITY`) so fixtures unrelated to the
    sensitivity-fail-closed-filter feature are never collaterally blocked by
    the fail-closed default; pass `None` explicitly for the absent-field
    case."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: Concept", f"title: {title}", "description: ''"]
    if status is not None:
        lines.append(f"status: {status}")
    if sensitivity_value is not None:
        lines.append(f"sensitivity: {sensitivity_value}")
    lines.append("---")
    lines.append("dichotomyzz")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Initialize a workspace AND backfill an (empty) `vectors.db`, `fts.db`,
    and `graph.db`, so `query`'s two retrieval seams are healthy by default
    -- most tests here exercise the answer-rendering/stderr-format contract,
    not the absent/corrupt-index-degrade/hint behavior, which has its own
    dedicated tests below using a workspace missing one of the stores (or
    one that fails to open).

    `graph.db` is still written even though `query` no longer reads it
    (issue #434): `reindex` maintains it, `status`/`next` still name it in
    the shared staleness advisory (`query` itself declares only `fts`
    since #436), and contradiction detection still derives its candidate
    pairs from the typed edges it projects."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db").close()
    bundle_dir = tmp_path / "bundle"
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", bundle_dir)
    sqlite_graph.write_graph_store(tmp_path / ".openkos" / "graph.db", bundle_dir)


def test_query_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `query` refuses (exit 1), prints the shared
    `require_workspace` reason under a `query`-specific prefix, and never
    calls `answer()` (spec: Run outside a workspace)."""
    monkeypatch.chdir(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos query: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_query_matching_answer_renders_citations_in_fused_rank_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace whose bundle answers the question renders the answer text
    followed by one `  → {concept_id} ({title})` line per citation, in the
    exact order `AnswerResult.citations` returns them, and exits 0 (spec:
    Matching answer with citations; Citation order matches the answer; Run
    inside a workspace)."""
    _init_workspace(tmp_path, monkeypatch)
    citation_one = Citation(concept_id="concepts/stoicism", title="Stoicism")
    citation_two = Citation(concept_id="concepts/epictetus", title="Epictetus")
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[citation_one, citation_two],
        fts_hit_count=3,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=2,
        fused_count=2,
        dense_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Stoicism teaches the dichotomy of control.\n"
        "\n"
        "Citations:\n"
        "  → concepts/stoicism (Stoicism)\n"
        "  → concepts/epictetus (Epictetus)\n"
    )
    assert result.stderr == (
        "retrieval: 3 FTS + 2 dense → 2 fused → LLM invoked → 2 cited\n"
    )


def test_query_zero_hits_renders_zero_hits_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `no_match_cause="zero_hits"` `AnswerResult` renders the zero-hits
    stdout message, no `Citations:` section, and exits 0 -- not an error
    (spec: Zero matching concepts). Stderr reports the retrieval summary."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="zero_hits",
        skip_notices=[],
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is nothing?"])

    assert result.exit_code == 0
    assert result.stdout == (
        "No matching concepts were found in the compiled bundle for this "
        "question. Try different wording, or run `openkos status` to see "
        "what the bundle contains.\n"
    )
    assert "Citations:" not in result.stdout
    assert result.stderr == (
        "retrieval: 0 FTS + 0 dense → 0 fused → LLM skipped → 0 cited\n"
    )


def test_query_all_unreadable_renders_corruption_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `no_match_cause="all_unreadable"` `AnswerResult` renders the
    unreadable-hits stdout message pointing at `openkos lint`, no
    `Citations:` section, and exits 0."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=2,
        llm_invoked=False,
        no_match_cause="all_unreadable",
        skip_notices=[],
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is nothing?"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Found 2 matching concepts, but none could be read from the "
        "compiled bundle — it may be corrupted. Run `openkos lint` to "
        "check bundle health.\n"
    )
    assert "Citations:" not in result.stdout
    assert result.stderr == (
        "retrieval: 2 FTS + 0 dense → 0 fused → LLM skipped → 0 cited\n"
    )


def test_query_empty_question_renders_prompt_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `no_match_cause="empty_query"` `AnswerResult` renders a prompt to
    provide a question, no `Citations:` section, and exits 0."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer=NO_MATCH,
        citations=[],
        fts_hit_count=0,
        llm_invoked=False,
        no_match_cause="empty_query",
        skip_notices=[],
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "   "])

    assert result.exit_code == 0
    assert result.stdout == (
        "No question was provided. Pass a question to answer, e.g. "
        'openkos query "what is stoicism?".\n'
    )
    assert "Citations:" not in result.stdout
    assert result.stderr == (
        "retrieval: 0 FTS + 0 dense → 0 fused → LLM skipped → 0 cited\n"
    )


def test_query_skip_notices_surfaced_on_stderr_alongside_successful_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-empty `skip_notices` on a successful run print to stderr, worded
    as a whole-bundle build diagnostic, after the retrieval summary; stdout
    is unaffected (spec: Skip notices present alongside a successful
    answer)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=["concepts/corrupt.md: skipped (unreadable)"],
        dense_hit_count=0,
        fused_count=1,
        dense_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Stoicism teaches the dichotomy of control.\n"
        "\n"
        "Citations:\n"
        "  → concepts/stoicism (Stoicism)\n"
    )
    assert result.stderr == (
        "retrieval: 1 FTS + 0 dense → 1 fused → LLM invoked → 1 cited\n"
        "index: 1 doc skipped while building the search index (whole-bundle, "
        "not this query's hits):\n"
        "  concepts/corrupt.md: skipped (unreadable)\n"
    )


def test_query_no_skip_notices_omits_skip_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty `skip_notices` prints only the retrieval summary line to
    stderr, no skip-notice text (spec: No skip notices)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=0,
        fused_count=1,
        dense_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert result.stderr == (
        "retrieval: 1 FTS + 0 dense → 1 fused → LLM invoked → 1 cited\n"
    )


def test_query_limit_flag_is_forwarded_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--limit 3` is forwarded unchanged as `answer(..., limit=3)` (spec:
    Caller overrides the default limit)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["question"] = question
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?", "--limit", "3"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["bundle_dir"] == tmp_path / "bundle"
    assert kwargs["limit"] == 3
    assert captured["question"] == "what is stoicism?"
    assert isinstance(kwargs["embedder"], OllamaClient)
    assert kwargs["vector_store"] is not None


def test_query_omitted_limit_defaults_to_five(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--limit` forwards the default `limit=5` (spec: Caller omits
    `--limit`)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["limit"] == 5


def test_query_builds_and_injects_embedder_and_vector_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`query` builds an `Embedder` (`OllamaClient(cfg.embedding_model)`) and
    opens the vector store via `open_vector_store(layout.vectors_db_path)`,
    injecting both into `answer()` (spec: Happy-Path Answer Rendering --
    Matching answer with citations)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    embedder = kwargs["embedder"]
    assert isinstance(embedder, OllamaClient)
    assert embedder._model == "bge-m3"  # DEFAULT_EMBEDDING_MODEL
    assert kwargs["vector_store"] is not None


def test_query_injects_the_fts_index_and_no_graph_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`query` opens the persisted FTS derived index read-only and injects it
    into `answer()` alongside the embedder/vector store -- and passes NO
    `graph_index`, because retrieval has no graph channel to feed (issue
    #434; spec: Happy-Path Answer Rendering -- Matching answer with
    citations)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\ndescription: ''\n---\ndichotomyzz\n",
        encoding="utf-8",
    )
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", bundle_dir)
    sqlite_graph.write_graph_store(tmp_path / ".openkos" / "graph.db", bundle_dir)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["fts_index"] is not None
    assert "graph_index" not in kwargs


def test_query_never_creates_vectors_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`query` is read-only over the vector store: a fresh workspace with no
    `vectors.db` never gets one created by `query` (spec: Query never
    creates vectors.db)."""
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "openkos.cli.main.answer",
        lambda *args, **kwargs: AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        ),
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert not (tmp_path / ".openkos" / "vectors.db").exists()


def test_query_never_creates_fts_db_or_graph_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`query` is read-only over the FTS/graph derived indexes too: a fresh
    workspace with no `fts.db`/`graph.db` never gets either created by
    `query` (Slice 5, PR3; reindex-command: Query never writes to a derived
    store)."""
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    monkeypatch.setattr(
        "openkos.cli.main.answer",
        lambda *args, **kwargs: AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        ),
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert not (tmp_path / ".openkos" / "fts.db").exists()
    assert not (tmp_path / ".openkos" / "graph.db").exists()


def test_query_performs_zero_writes_to_an_existing_fts_db_or_graph_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace with ALREADY-persisted `fts.db`/`graph.db` is left
    byte-for-byte unmodified by `query` (Slice 5, PR3; query-command:
    Happy-Path Answer Rendering; reindex-command: Query never writes to a
    derived store)."""
    _init_workspace(tmp_path, monkeypatch)
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "concepts").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\ndescription: ''\n---\ndichotomyzz\n",
        encoding="utf-8",
    )
    fts_db_path = tmp_path / ".openkos" / "fts.db"
    graph_db_path = tmp_path / ".openkos" / "graph.db"
    fts.write_fts_index(fts_db_path, bundle_dir)
    sqlite_graph.write_graph_store(graph_db_path, bundle_dir)
    fts_bytes_before = fts_db_path.read_bytes()
    graph_bytes_before = graph_db_path.read_bytes()
    monkeypatch.setattr(
        "openkos.cli.main.answer",
        lambda *args, **kwargs: AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        ),
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert fts_db_path.read_bytes() == fts_bytes_before
    assert graph_db_path.read_bytes() == graph_bytes_before


def test_query_cold_store_hints_at_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace with no `vectors.db` (never reindexed) still completes on
    the FTS-only fused result, exits 0, and prints a reindex hint on stderr
    -- even when the injected `AnswerResult.dense_degraded` is `False`, since
    the CLI itself detected the absent store (spec: Cold store hints at
    reindex)."""
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=0,
        fused_count=1,
        dense_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "openkos reindex" in result.stderr


def test_query_vec_unavailable_at_open_degrades_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `vectors.db` that exists but fails to open (`VecUnavailable`)
    degrades to `vector_store=None`, exits 0 on the FTS-only fused result,
    and prints the same reindex hint (spec: Locked or corrupt vectors.db
    degrades with the same hint)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.open_vector_store",
        lambda path: (_ for _ in ()).throw(VecUnavailable("boom")),
    )
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["vector_store"] is None
    assert "openkos reindex" in result.stderr


def test_query_corrupt_vectors_db_degrades_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EXISTING `vectors.db` that is corrupt at the filesystem level (not
    a valid SQLite file, or exclusively locked) raises a raw `sqlite3.Error`
    from `open_vector_store`'s CREATE TABLE step -- NOT `VecUnavailable` --
    but `query` must still degrade to FTS-only fusion, printing the answer
    and citations to STDOUT and the reindex hint on stderr, rather than
    crashing with a raw traceback (spec: Locked or corrupt vectors.db
    degrades with the same hint)."""
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    openkos_dir = tmp_path / ".openkos"
    openkos_dir.mkdir(exist_ok=True)
    (openkos_dir / "vectors.db").write_bytes(b"not a database")
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=0,
        fused_count=1,
        dense_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "Traceback" not in result.stderr
    assert result.stdout == (
        "Stoicism teaches the dichotomy of control.\n"
        "\n"
        "Citations:\n"
        "  → concepts/stoicism (Stoicism)\n"
    )
    assert "openkos reindex" in result.stderr


def test_query_cold_fts_store_hints_at_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace with no `fts.db` (never reindexed) still completes on
    whatever retrieval lists remain available, exits 0, and prints the
    reindex hint -- mirroring the existing dense-cold-store behavior (Slice
    5, PR3; query-command: FTS-Unavailable Runs Degrade And Hint At Reindex
    -- Never-reindexed workspace hints at reindex for FTS too)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / ".openkos" / "fts.db").unlink()
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=0,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=1,
        fused_count=1,
        dense_degraded=False,
    )
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return fake_result

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["fts_index"] is None
    assert "graph_index" not in kwargs
    assert "openkos reindex" in result.stderr


def test_query_corrupt_fts_db_degrades_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EXISTING `fts.db` that is corrupt at the filesystem level raises a
    raw `sqlite3.Error` from `open_fts_index_readonly`'s validating read --
    `query` must still degrade to dense-only fusion, printing the answer and
    citations to STDOUT and the reindex hint on stderr, rather than crashing
    with a raw traceback (Slice 5, PR3; query-command: Corrupt or unopenable
    FTS index degrades with the same hint)."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    (openkos_dir / "fts.db").write_bytes(b"not a database")
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=0,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=1,
        fused_count=1,
        dense_degraded=False,
    )
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return fake_result

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "Traceback" not in result.stderr
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["fts_index"] is None
    assert "openkos reindex" in result.stderr


def test_query_ignores_a_corrupt_graph_db_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt `graph.db` no longer degrades RETRIEVAL (issue #434): the
    graph channel is gone, so `query` never opens the store as an index,
    never passes a `graph_index`, and never prints the
    derived-index-unavailable hint or the graph-degrade note on its account.

    This is the load-bearing difference from the removed
    `test_query_corrupt_graph_db_degrades_with_hint`, which treated a broken
    graph store as a partial retrieval failure worth reporting.

    Since #436 the shared `_stale_index_names` advisory is scoped to the
    stores each verb declares it reads -- `query` declares only `fts` -- so
    a corrupt (hence unreadable, hence "stale") `graph.db` no longer
    produces even the staleness warning here; `status` and `next` still
    name `graph` (`reindex` still maintains `graph.db`, and contradiction
    detection still reads the projection). The assertion here stays scoped
    to the retrieval seam, not to the whole of stderr."""
    _init_workspace(tmp_path, monkeypatch)
    openkos_dir = tmp_path / ".openkos"
    (openkos_dir / "graph.db").write_bytes(b"not a database")
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=1,
        fused_count=1,
        dense_degraded=False,
    )
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return fake_result

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "Traceback" not in result.stderr
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert "graph_index" not in kwargs
    assert "derived indexes are unavailable this run" not in result.stderr
    assert "graph retrieval degraded" not in result.stderr
    assert "graph-added" not in result.stderr


def test_query_dense_degraded_hints_at_reindex_even_with_store_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`AnswerResult.dense_degraded=True` (a read-path failure caught inside
    `answer()`) prints the reindex hint even though the CLI's OWN store-open
    succeeded (spec: Dense-Unavailable Runs Degrade And Hint At Reindex)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=0,
        fused_count=1,
        dense_degraded=True,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "openkos reindex" in result.stderr


def test_query_no_hint_when_dense_healthy_and_store_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy run (store present, `dense_degraded=False`) prints no
    reindex hint at all."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=1,
        fused_count=1,
        dense_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "reindex" not in result.stderr


def test_query_retrieval_summary_has_no_graph_term(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `retrieval:` stderr summary is `FTS + dense -> fused -> LLM ->
    cited` and nothing else (issue #434).

    The graph term is gone because the channel is gone. It reported
    `graph_contributed_count`, the number of reserved slots the graph filled
    -- an honest number for a channel that should not have been filling
    slots at all: seeded personalized PageRank ranks by global centrality,
    and every slot it claimed cost a real FTS/dense hit."""
    _init_workspace(tmp_path, monkeypatch)
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=3,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=2,
        fused_count=2,
        dense_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert result.stderr == (
        "retrieval: 3 FTS + 2 dense \u2192 2 fused \u2192 LLM invoked \u2192 1 cited\n"
    )
    assert "graph" not in result.stderr


def test_query_never_prints_a_graph_degrade_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No run prints a graph-degrade note any more (issue #434) -- there is
    no graph stage left to degrade, so an absent or unopenable `graph.db`
    changes nothing a reader of `query` would ever see."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / ".openkos" / "graph.db").unlink()
    fake_result = AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=1,
        fused_count=1,
        dense_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "graph retrieval degraded" not in result.stderr
    assert "graph" not in result.stderr


def test_query_builds_ollama_client_from_configured_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`query` builds the `OllamaClient` from the model configured in
    `openkos.yaml`, not a hardcoded or wrong-field value: a distinctive
    non-default model tag written to config is the model tag on the `llm`
    passed to `answer()` (spec: Model comes from workspace config)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_model}\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    llm = kwargs["llm"]
    assert isinstance(llm, OllamaClient)
    assert llm._model == configured_model


def test_query_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`answer()` raising `OllamaUnavailable` (an `OllamaError` subclass) is
    caught, printed as a friendly stderr message with `ollama serve`
    remediation, and exits 1 with no raw traceback (spec: Ollama backend
    unreachable)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_unavailable(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.answer", _raise_unavailable)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos query: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert result.stderr.rstrip("\n").endswith(
        "Or run `openkos doctor` to diagnose the environment."
    )
    assert "Traceback" not in result.stderr


def test_query_model_not_found_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`answer()` raising `OllamaModelNotFound` is caught and printed with
    the REAL failing model name taken from the exception text (`{exc}`) --
    NOT a hardcoded `cfg.model`, which would name the wrong one of the two
    Ollama-backed models `query` now builds (chat vs. embedding) whenever
    the embedding model is the one that actually 404'd -- plus `ollama pull`
    remediation, and exits 1 with no raw traceback (spec: Configured model
    not installed)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_chat_model = "llama3.2:1b-openkos-test"
    (tmp_path / "openkos.yaml").write_text(
        f"model: {configured_chat_model}\n", encoding="utf-8"
    )
    failing_embedding_model = "qwen3-embedding:0.6b"

    def _raise_model_not_found(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaModelNotFound(
            f'Model not found (404): model "{failing_embedding_model}" not '
            "found, try pulling it first"
        )

    monkeypatch.setattr("openkos.cli.main.answer", _raise_model_not_found)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos query: failed -- ")
    assert failing_embedding_model in result.stderr
    assert configured_chat_model not in result.stderr
    assert "ollama pull" in result.stderr
    assert "openkos doctor" not in result.stderr
    assert "Traceback" not in result.stderr


def test_query_dimension_mismatch_maps_to_exit_one_with_dedicated_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`answer()` raising `OllamaEmbeddingDimensionMismatch` is caught by a
    DEDICATED branch -- distinct from the generic `(FtsUnavailable,
    OllamaError)` ladder entry -- printed with remediation naming
    `embedding_model` in `openkos.yaml`, and exits 1 with nothing on stdout
    (issue #209: this used to degrade to a silent FTS-only answer at exit
    0).

    The `"permanent dimension mismatch" in result.stderr` assertion below
    proves ONLY that the exception text is interpolated at all: this test
    constructs the exception with that phrase itself, and BOTH the dedicated
    handler and the generic `(FtsUnavailable, OllamaError)` fallback
    interpolate `{exc}`, so it cannot discriminate which branch ran. The
    DISCRIMINATING assertions are "restore", `embedding_model`, and
    `openkos.yaml`: the generic branch only echoes
    `f"openkos query: failed -- {exc}."` and never mentions any of them, so
    reordering the dedicated branch AFTER that generic tuple would make those
    three fail for real, not by coincidence of matching exception text.

    Stderr must ALSO stay free of the `openkos reindex` hint: reindex fails
    with this very same error until `openkos.yaml` is fixed, so pointing at
    it would be actively misleading. The pre-existing
    "one or more derived indexes are unavailable" hint only fires off
    `dense_degraded`, which this exception now bypasses entirely by
    propagating."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_dimension_mismatch(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaEmbeddingDimensionMismatch(
            "Ollama returned an embedding row of length 768, expected "
            "exactly 1024 (EMBED_DIM) -- this is a permanent dimension "
            "mismatch caused by the configured embedding model, not a "
            "transient failure; it will not heal by retrying."
        )

    monkeypatch.setattr("openkos.cli.main.answer", _raise_dimension_mismatch)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos query: failed -- ")
    assert "permanent dimension mismatch" in result.stderr
    assert "restore" in result.stderr.lower()
    assert "embedding_model" in result.stderr
    assert "openkos.yaml" in result.stderr
    assert "will retry next run" not in result.stderr.lower()
    # The misleading hint MUST NOT fire: `reindex` fails with this same
    # error until the config is fixed, so it is not the remedy here.
    assert "reindex" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_query_dimension_mismatch_is_fatal_even_when_fts_already_had_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exit-1 refusal is UNCONDITIONAL: it stands even when lexical
    retrieval ALREADY succeeded and would have grounded a cited answer (spec:
    Refusal stands even when FTS retrieval already succeeded).

    Driven end-to-end through the REAL (unmocked) `answer()`: `_fts_search`
    runs BEFORE `_dense_search`, so the mismatch surfaces only AFTER the FTS
    hits are in hand, which a wholesale `answer` patch cannot express. The
    control pins that genuine FTS material exists -- only the embedder differs
    -- so the second run's exit 1 with EMPTY stdout is deliberate disposal of
    already-successful retrieval work, not a zero-hit coincidence. A refusal
    conditional on FTS hits, or an `--fts-only` hatch, would turn it back
    into the control's exit-0 cited answer."""
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    bundle_dir = tmp_path / "bundle"
    _write_query_doc(bundle_dir / "concepts" / "live.md", title="Live")
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", bundle_dir)
    # A schema-initialized (empty) vectors.db: a `None` store would make
    # `_dense_search` return early and the mismatch could never surface.
    vectorstore.open_vector_store(tmp_path / ".openkos" / "vectors.db").close()

    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeHealthyEmbedOllamaClient)
    control = runner.invoke(app, ["query", "dichotomyzz"])

    assert control.exit_code == 0
    assert control.stderr.startswith(
        "retrieval: 1 FTS + 0 dense → 1 fused → LLM invoked → 1 cited\n"
    )
    assert "a fake answer" in control.stdout

    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", _FakeWrongWidthEmbedOllamaClient
    )
    result = runner.invoke(app, ["query", "dichotomyzz"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    # The FTS hit the control just cited is thrown away.
    assert result.stdout == ""
    assert "retrieval:" not in result.stderr
    assert "openkos.yaml" in result.stderr
    assert "Traceback" not in result.stderr


def test_query_generic_ollama_error_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`answer()` raising a plain `OllamaError` (neither `OllamaUnavailable`
    nor `OllamaModelNotFound`) is caught, printed as the unchanged generic
    friendly message with no cause-specific remediation, and exits 1 with no
    raw traceback (spec: Other Ollama error)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_generic(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaError("boom")

    monkeypatch.setattr("openkos.cli.main.answer", _raise_generic)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == "openkos query: failed -- boom.\n"
    assert "ollama serve" not in result.stderr
    assert "ollama pull" not in result.stderr
    assert "Traceback" not in result.stderr


def test_query_specific_ollama_subclasses_do_not_fall_through_to_generic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All three of `OllamaUnavailable`, `OllamaModelNotFound`, and
    `OllamaEmbeddingDimensionMismatch` must reach their OWN handler, not the
    generic `(FtsUnavailable, OllamaError)` fallback -- the direct RED test
    for D1's specific-before-general handler ordering."""
    _init_workspace(tmp_path, monkeypatch)

    unavailable_message = "Ollama not reachable at http://localhost:11434"

    def _raise_unavailable(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaUnavailable(unavailable_message)

    monkeypatch.setattr("openkos.cli.main.answer", _raise_unavailable)
    result = runner.invoke(app, ["query", "what is stoicism?"])
    # The generic tuple fallback would print exactly this bare shape with no
    # remediation -- proves `OllamaUnavailable` reached its OWN handler.
    assert result.stderr != f"openkos query: failed -- {unavailable_message}.\n"
    assert "ollama serve" in result.stderr

    def _raise_model_not_found(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaModelNotFound("Model not found (404): model not found")

    monkeypatch.setattr("openkos.cli.main.answer", _raise_model_not_found)
    result = runner.invoke(app, ["query", "what is stoicism?"])
    # The generic tuple fallback would print exactly the bare
    # "openkos query: failed -- {exc}.\n" shape with no remediation -- the
    # `ollama pull` remediation text proves `OllamaModelNotFound` reached its
    # OWN handler instead.
    assert "ollama pull" in result.stderr

    mismatch_message = "wrong embedding width."

    def _raise_dimension_mismatch(*args: object, **kwargs: object) -> AnswerResult:
        raise OllamaEmbeddingDimensionMismatch(mismatch_message)

    monkeypatch.setattr("openkos.cli.main.answer", _raise_dimension_mismatch)
    result = runner.invoke(app, ["query", "what is stoicism?"])
    # The generic fallback prints exactly this bare shape; the `openkos.yaml`
    # remediation proves the mismatch reached its OWN handler (issue #209).
    assert result.stderr != f"openkos query: failed -- {mismatch_message}.\n"
    assert "openkos.yaml" in result.stderr


def test_query_fts_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`answer()` raising `FtsUnavailable` is caught, printed as a friendly
    stderr message, and exits 1 with no raw traceback (spec: FTS index
    unavailable)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_fts_unavailable(*args: object, **kwargs: object) -> AnswerResult:
        raise FtsUnavailable("fts5 not compiled in")

    monkeypatch.setattr("openkos.cli.main.answer", _raise_fts_unavailable)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos query: failed -- ")
    assert "fts5 not compiled in" in result.stderr
    assert "Traceback" not in result.stderr


def test_query_malformed_config_maps_to_exit_one_before_calling_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard, D2 lint
    parity) is caught, printed as a friendly stderr message, exits 1 with no
    raw traceback, and `answer()` is never reached."""
    _init_workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text("model: [unclosed\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos query: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert calls == []


# ---------------------------------------------------------------------------
# `--include-deprecated` (status-aware-retrieval Phase 4)
# ---------------------------------------------------------------------------


def test_query_include_deprecated_flag_forwarded_as_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-deprecated` is forwarded unchanged as
    `answer(..., include_deprecated=True)` (spec: `--include-deprecated`
    Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?", "--include-deprecated"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is True


def test_query_omitted_include_deprecated_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--include-deprecated` forwards the safe default
    `include_deprecated=False` (spec: Deprecated Concepts Excluded By
    Default)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_deprecated"] is False


def test_query_retrieval_stderr_default_reports_post_filter_fts_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3 CLI pin, deferred from PR2: the `retrieval:` stderr summary reports
    POST-filter counts end-to-end through the REAL (unmocked) `answer()` --
    a deprecated concept sharing the matched term is excluded from the FTS
    hit count and the fused count by default (design R3; status-aware-
    retrieval Phase 4 closes the PR2 deferral now that `--include-deprecated`
    is wired all the way through the CLI)."""
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    bundle_dir = tmp_path / "bundle"
    _write_query_doc(bundle_dir / "concepts" / "live.md", title="Live")
    _write_query_doc(
        bundle_dir / "concepts" / "old.md", title="Old", status="deprecated"
    )
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", bundle_dir)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeOllamaClient)

    result = runner.invoke(app, ["query", "dichotomyzz"])

    assert result.exit_code == 0
    assert result.stderr.startswith(
        "retrieval: 1 FTS + 0 dense → 1 fused → LLM invoked → 1 cited\n"
    )


def test_query_retrieval_stderr_include_deprecated_reports_post_filter_fts_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same end-to-end run with `--include-deprecated` restores the
    deprecated concept into the FTS hit count, fused count, and citation
    count reported on the `retrieval:` stderr line (design R3; spec:
    `--include-deprecated` Escape Flag)."""
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init"])
    assert init_result.exit_code == 0
    bundle_dir = tmp_path / "bundle"
    _write_query_doc(bundle_dir / "concepts" / "live.md", title="Live")
    _write_query_doc(
        bundle_dir / "concepts" / "old.md", title="Old", status="deprecated"
    )
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", bundle_dir)
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeOllamaClient)

    result = runner.invoke(app, ["query", "dichotomyzz", "--include-deprecated"])

    assert result.exit_code == 0
    assert result.stderr.startswith(
        "retrieval: 2 FTS + 0 dense → 2 fused → LLM invoked → 2 cited\n"
    )


# ---------------------------------------------------------------------------
# `--include-confidential` (sensitivity-fail-closed-filter S3a)
# ---------------------------------------------------------------------------


def test_query_include_confidential_flag_forwarded_as_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` is forwarded unchanged as
    `answer(..., include_confidential=True)` (spec: `--include-confidential`
    Escape Flag)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(
        app, ["query", "what is stoicism?", "--include-confidential"]
    )

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is True


def test_query_omitted_include_confidential_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--include-confidential` forwards the safe default
    `include_confidential=False` (spec: Confidential Excluded By Default)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_answer(question: str, **kwargs: object) -> AnswerResult:
        captured["kwargs"] = kwargs
        return AnswerResult(
            answer=NO_MATCH,
            citations=[],
            fts_hit_count=0,
            llm_invoked=False,
            no_match_cause="zero_hits",
            skip_notices=[],
        )

    monkeypatch.setattr("openkos.cli.main.answer", _recording_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["include_confidential"] is False


# ---------------------------------------------------------------------------
# directory-walk-observability follow-up: walk-incompleteness signal
# ---------------------------------------------------------------------------


def test_query_warns_stderr_on_incomplete_walk_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An incomplete directory walk (`okf._walk_errors` non-empty) prints a
    self-explaining warning to STDERR and the command still exits 0 -- WARN,
    not refuse (spec: Incomplete walk warns and still exits 0). `answer()` is
    faked (D4 pattern) so this test never depends on a real Ollama process.

    The default workspace grants the confidential local exemption (#240),
    which is the OTHER hatch that suppresses the FILTER-SCOPED half of this
    signal -- see `test_query_local_exemption_keeps_the_general_advisory`
    below. This test is about the run where NEITHER hatch is set, so it opts
    out of the exemption through the same workspace switch a user would use,
    and both advisories are then true at once.

    Asserted through text unique to each: since #356 both lines open with
    `bundle scan was incomplete`, so that prefix no longer tells them
    apart."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    monkeypatch.setattr("openkos.cli.main.answer", _fake_no_match_answer)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" in result.stderr


def test_query_no_warning_on_clean_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully readable bundle produces NEITHER advisory (spec: Clean bundle
    produces no warning) -- the walk coming back empty is the only thing
    that silences the #356 one, which no hatch suppresses."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.answer", _fake_no_match_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" not in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_query_include_confidential_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--include-confidential` suppresses the FILTER-SCOPED advisory only:
    the filter is deliberately off, so its message would be a claim about a
    filter that never ran. The #356 incomplete-inputs advisory still prints,
    because `query`'s retrieval reads the same truncated bundle either way,
    and the command still exits 0.

    The default workspace ALSO grants the confidential local exemption
    (#240), which independently suppresses the same filter-scoped message.
    Without opting out of that here, this test would keep passing even if
    `--include-confidential` were silently dropped from the
    `warn_if_walk_incomplete` call site, so it disables the exemption to
    make the FLAG the thing that discriminates."""
    _init_workspace(tmp_path, monkeypatch)
    disable_local_exemption(tmp_path)
    monkeypatch.setattr("openkos.cli.main.answer", _fake_no_match_answer)
    _break_os_walk(monkeypatch)

    result = runner.invoke(
        app, ["query", "what is stoicism?", "--include-confidential"]
    )

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_query_local_exemption_keeps_the_general_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confidential local exemption (#240) suppresses the FILTER-SCOPED
    advisory the SAME way `--include-confidential` does, and leaves the #356
    incomplete-inputs advisory printing.

    This is the STOCK workspace -- default local backend, exemption active
    -- which is exactly the path that emitted nothing at all before #356:
    an unreadable subtree shrank the fused retrieval pool and the answer was
    computed from it, with no signal anywhere."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.answer", _fake_no_match_answer)
    _break_os_walk(monkeypatch)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "this command's inputs are incomplete" in result.stderr
    assert "confidential-content filter" not in result.stderr


def test_query_docstring_no_longer_claims_no_persisted_state() -> None:
    """`query`'s docstring no longer states that retrieval carries "no
    persisted state, no CLI-level graph command" -- it now describes the FTS
    and dense channels as reading persisted, `reindex`-written on-disk
    indexes (Slice 5, PR3; query-command: Docstring reflects persisted-index
    contract)."""
    cli_main = _REPO_ROOT / "src" / "openkos" / "cli" / "main.py"
    tree = ast.parse(cli_main.read_text(encoding="utf-8"))
    query_docstring = next(
        ast.get_docstring(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "query"
    )
    assert query_docstring is not None
    assert "no persisted state" not in query_docstring
    assert "no CLI-level graph command" not in query_docstring
    assert "persisted" in query_docstring.lower()
    assert "reindex" in query_docstring


# ---------------------------------------------------------------------------
# Issue #190: TTY-gated stage notice before the long answer call
# ---------------------------------------------------------------------------


def test_query_prints_tty_gated_stage_notice_before_the_answer_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, `query` prints ONE `openkos query: answering (waiting on
    the LLM)...` stage notice to STDERR immediately before its single long
    `answer()` call -- the single-call sibling of the per-item progress
    hooks (issue #190). STDOUT keeps the clean answer."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)
    monkeypatch.setattr("openkos.cli.main.answer", _fake_no_match_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "openkos query: answering (waiting on the LLM)..." in result.stderr
    assert "waiting on the LLM" not in result.stdout


def test_query_transport_failure_never_prints_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection failure against a credentialed host prints no password
    on stderr, and still names the redacted host (issue #355).

    This is the issue's own reproduction, at the surface it was reported on:
    the message is built by the REAL `OllamaClient` transport path (a real
    `URLError` raised out of `urlopen`, mapped by the real `_unavailable`)
    and then formatted by the REAL `OllamaUnavailable` handler. The
    message-level pins live in `tests/unit/llm/test_ollama.py`; this one
    proves the composition, because it is the handler's output -- not the
    exception -- that a user actually sees."""
    _init_workspace(tmp_path, monkeypatch)

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.URLError("Connection refused")

    def _answer_through_a_real_client(*args: object, **kwargs: object) -> AnswerResult:
        client = OllamaClient(
            "qwen3",
            host="http://user:s3cret@remote.example:11434",
            urlopen=_refuse,
        )
        return client.chat([{"role": "user", "content": "hi"}])  # type: ignore[return-value]

    monkeypatch.setattr("openkos.cli.main.answer", _answer_through_a_real_client)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 1
    assert "s3cret" not in result.stderr
    assert "s3cret" not in result.stdout
    # Redaction must not cost diagnosability: the user still learns where the
    # attempt went, and still gets the handler's own remediation.
    assert "remote.example:11434" in result.stderr
    assert "ollama serve" in result.stderr


def test_query_stage_notice_is_silent_without_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a TTY (`CliRunner`'s default), the stage notice never
    appears -- piped output stays byte-clean (issue #190)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr("openkos.cli.main.answer", _fake_no_match_answer)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "waiting on the LLM" not in result.stderr
    assert "waiting on the LLM" not in result.stdout


# --- stale derived indexes (#381) ---------------------------------------


def _stale_answer_result() -> AnswerResult:
    return AnswerResult(
        answer="An answer.",
        citations=[],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
        dense_hit_count=1,
        fused_count=1,
        dense_degraded=False,
    )


def test_query_warns_when_the_derived_indexes_are_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#381: a bundle edited since the last reindex leaves `fts.db`/`graph.db`
    describing an older document set, and until now the ONLY symptom was a
    quietly worse answer. `query` names the stale stores before answering, so
    the degradation is attributable rather than invisible.

    Updated by #436: `query` never reads `graph.db` (#434), so graph
    staleness cannot degrade its answer -- the advisory names only the
    stores this verb actually reads, which is `fts` alone. Naming `graph`
    here was a true statement about the workspace but a false statement
    about this answer."""
    _init_workspace(tmp_path, monkeypatch)
    # Edit the bundle AFTER the indexes were written -- exactly what `relate`,
    # `reconcile`, `merge` and `curate` do, none of which reindex.
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "new.md").write_text(
        "---\ntype: Concept\ntitle: New\ndescription: ''\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _stale_answer_result()
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert (
        "warning: derived indexes are stale (fts) -- this answer may "
        "be degraded; run `openkos reindex`.\n"
    ) in result.stderr
    assert "graph" not in result.stderr


def test_query_says_nothing_when_only_the_graph_index_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#436: a stale `graph.db` behind a fresh `fts.db` cannot degrade this
    answer -- `query` reads no graph store -- so warning about it here would
    be a false hint. `status` and `next` still report it (they describe the
    workspace, not one answer)."""
    _init_workspace(tmp_path, monkeypatch)
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "new.md").write_text(
        "---\ntype: Concept\ntitle: New\ndescription: ''\n---\nbody\n",
        encoding="utf-8",
    )
    # Refresh only the FTS store over the edited bundle: graph stays stale.
    fts.write_fts_index(tmp_path / ".openkos" / "fts.db", tmp_path / "bundle")
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _stale_answer_result()
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "stale" not in result.stderr


def test_query_stale_warning_precedes_the_retrieval_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The warning is emitted BEFORE the LLM call, not after it: the user is
    told their answer is suspect while they are still waiting for it, rather
    than after having read it."""
    _init_workspace(tmp_path, monkeypatch)
    concepts = tmp_path / "bundle" / "concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    (concepts / "new.md").write_text(
        "---\ntype: Concept\ntitle: New\ndescription: ''\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _stale_answer_result()
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.stderr.index(
        "warning: derived indexes are stale"
    ) < result.stderr.index("retrieval: ")


def test_query_says_nothing_when_the_indexes_are_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A freshly reindexed workspace must stay silent -- an advisory that
    fires on the healthy path is noise, and noise is what gets ignored."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _stale_answer_result()
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "stale" not in result.stderr


def test_query_stale_check_never_breaks_the_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The advisory is strictly additive: if the staleness check itself
    fails, the answer still lands. An advisory must never be what breaks the
    command it advises."""
    _init_workspace(tmp_path, monkeypatch)

    def _boom(*args: object, **kwargs: object) -> tuple[str, ...]:
        raise RuntimeError("manifest walk exploded")

    monkeypatch.setattr("openkos.cli.main.stale_derived_stores", _boom)
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _stale_answer_result()
    )

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "An answer." in result.stdout
