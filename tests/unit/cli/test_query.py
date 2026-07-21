"""Unit tests for the `query` CLI command: the MVP-1 query chain entry point.

`query` is the read-only counterpart to `status`/`lint` (D1: bare
`require_workspace` gate, no Phase B, no confirm gate, no `--auto`), followed
by a Phase-A `read_config` guard (`except (OSError, ValueError)`, lint
parity) and a Phase-B `answer()` call guarded by three ORDERED handlers --
`OllamaUnavailable`, then `OllamaModelNotFound`, then the generic
`(FtsUnavailable, OllamaError)` fallback -- each with its own actionable
message. Every test patches `openkos.cli.main.answer` (D4) -- zero
network, zero real Ollama process, zero real FTS5 index.
"""

import ast
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.graph import sqlite_graph
from openkos.llm.ollama import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.retrieval.answer import NO_MATCH, AnswerResult, Citation
from openkos.state import fts, vectorstore
from openkos.state.fts import FtsUnavailable
from openkos.state.vectorstore import VecUnavailable

_REPO_ROOT = Path(__file__).resolve().parents[3]

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Initialize a workspace AND backfill an (empty) `vectors.db`, `fts.db`,
    and `graph.db`, so `query`'s three derived-index seams are ALL healthy
    by default -- most tests here exercise the answer-rendering/stderr-format
    contract, not the absent/corrupt-index-degrade/hint behavior, which has
    its own dedicated tests below using a workspace missing one or more of
    the three stores (or one that fails to open)."""
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
        "retrieval: 3 FTS + 2 dense + 0 graph → 2 fused → LLM invoked → 2 cited\n"
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
        "retrieval: 0 FTS + 0 dense + 0 graph → 0 fused → LLM skipped → 0 cited\n"
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
        "retrieval: 2 FTS + 0 dense + 0 graph → 0 fused → LLM skipped → 0 cited\n"
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
        "retrieval: 0 FTS + 0 dense + 0 graph → 0 fused → LLM skipped → 0 cited\n"
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
        "retrieval: 1 FTS + 0 dense + 0 graph → 1 fused → LLM invoked → 1 cited\n"
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
        "retrieval: 1 FTS + 0 dense + 0 graph → 1 fused → LLM invoked → 1 cited\n"
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
    assert embedder._model == "qwen3-embedding:0.6b"
    assert kwargs["vector_store"] is not None


def test_query_builds_and_injects_fts_index_and_graph_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`query` opens the persisted FTS and graph derived indexes read-only
    and injects both into `answer()` alongside the embedder/vector store
    (Slice 5, PR3; spec: Happy-Path Answer Rendering -- Matching answer with
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
    assert kwargs["graph_index"] is not None


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


def test_query_cold_fts_and_graph_stores_hint_at_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace with no `fts.db`/`graph.db` (never reindexed) still
    completes on whatever retrieval lists remain available, exits 0, and
    prints the reindex hint -- mirroring the existing dense-cold-store
    behavior (Slice 5, PR3; query-command: FTS/Graph-Unavailable Runs
    Degrade And Hint At Reindex -- Never-reindexed workspace hints at
    reindex for FTS/graph too)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / ".openkos" / "fts.db").unlink()
    (tmp_path / ".openkos" / "graph.db").unlink()
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
    assert kwargs["graph_index"] is None
    assert "openkos reindex" in result.stderr


def test_query_corrupt_fts_db_degrades_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EXISTING `fts.db` that is corrupt at the filesystem level raises a
    raw `sqlite3.Error` from `open_fts_index_readonly`'s validating read --
    `query` must still degrade to dense-only fusion, printing the answer and
    citations to STDOUT and the reindex hint on stderr, rather than crashing
    with a raw traceback (Slice 5, PR3; query-command: Corrupt or unopenable
    FTS/graph index degrades with the same hint)."""
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


def test_query_corrupt_graph_db_degrades_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EXISTING `graph.db` that is corrupt at the filesystem level raises
    a raw `sqlite3.Error` from `open_graph_store_readonly`'s validating read
    -- `query` must still degrade cleanly, rather than crashing with a raw
    traceback (Slice 5, PR3; query-command: Corrupt or unopenable FTS/graph
    index degrades with the same hint)."""
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
    assert kwargs["graph_index"] is None
    assert "openkos reindex" in result.stderr


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


def test_query_retrieval_summary_includes_graph_hit_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `retrieval:` stderr summary reports `graph_hit_count` alongside
    the existing FTS/dense/fused counts (spec: Stderr Retrieval Summary On
    Every Run)."""
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
        graph_hit_count=4,
        graph_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert result.stderr == (
        "retrieval: 3 FTS + 2 dense + 4 graph → 2 fused → LLM invoked → 1 cited\n"
    )
    assert result.stdout == (
        "Stoicism teaches the dichotomy of control.\n"
        "\n"
        "Citations:\n"
        "  → concepts/stoicism (Stoicism)\n"
    )


def test_query_graph_degraded_adds_a_stderr_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`graph_degraded=True` prints an additional stderr note, mirroring
    the existing dense-degrade hint shape; stdout is unaffected (spec:
    Graph degrade is noted alongside the summary)."""
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
        graph_hit_count=0,
        graph_degraded=True,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "graph retrieval degraded" in result.stderr
    assert result.stdout == (
        "Stoicism teaches the dichotomy of control.\n"
        "\n"
        "Citations:\n"
        "  → concepts/stoicism (Stoicism)\n"
    )


def test_query_no_graph_degrade_note_when_graph_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy graph run (`graph_degraded=False`) prints no graph-degrade
    note at all."""
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
        graph_hit_count=2,
        graph_degraded=False,
    )
    monkeypatch.setattr("openkos.cli.main.answer", lambda *args, **kwargs: fake_result)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert "graph retrieval degraded" not in result.stderr


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
    """Both `OllamaUnavailable` and `OllamaModelNotFound` must reach their
    OWN handler, not the generic `(FtsUnavailable, OllamaError)` fallback --
    the direct RED test for D1's specific-before-general handler ordering."""
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


def test_query_docstring_no_longer_claims_no_persisted_state() -> None:
    """`query`'s docstring no longer states that graph/FTS retrieval carries
    "no persisted state, no CLI-level graph command" -- it now describes
    both as reading persisted, `reindex`-written on-disk indexes (Slice 5,
    PR3; query-command: Docstring reflects persisted-index contract)."""
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
