"""Unit tests for the `reindex` CLI command: thin wiring over
`state/reindex.py`.

`reindex` mirrors `query`'s D1 gate shape (bare `require_workspace`, no
Phase B, no confirm gate, no `--auto`) and its ordered Ollama error ladder,
substituting `VecUnavailable` for `FtsUnavailable`. Every test patches
`openkos.cli.main.reindex_module.reindex` (the orchestrator call) or
`openkos.cli.main.open_vector_store` -- zero network, zero real Ollama
process, zero real embedding calls. `open_vector_store` itself runs for
real against a `tmp_path` workspace where the successful-run/error-ladder
scenarios don't specifically fake it, since it only touches local SQLite
(no network).
"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos import config
from openkos.cli.main import app
from openkos.llm.base import EMBED_DIM
from openkos.llm.ollama import (
    OllamaEmbeddingDimensionMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaUnavailable,
)
from openkos.state.fts import FtsUnavailable
from openkos.state.reindex import ReindexReport
from openkos.state.vectorstore import VecUnavailable, open_vector_store
from tests.unit.conftest import (
    LOCAL_BACKEND_LOCALITY,
    make_locked_error,
    make_non_lock_operational_error,
)

runner = CliRunner()


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def test_reindex_refuses_when_not_a_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a workspace, `reindex` refuses (exit 1), prints the shared
    `require_workspace` reason under a `reindex`-specific prefix, and never
    calls the orchestrator (spec: Run outside a workspace refuses)."""
    monkeypatch.chdir(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == (
        "openkos reindex: refusing to run -- no OpenKOS workspace found in "
        "this directory (run 'openkos init' first).\n"
    )
    assert "Traceback" not in result.stderr
    assert calls == []


def test_reindex_successful_run_prints_summary_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful run prints embedded/cache-hit/pruned/skipped counts and
    exits 0 (spec: Successful run prints a summary and exits 0)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(embedded=3, cache_hits=2, pruned=1, skipped=0)
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "3 embedded" in result.stdout
    assert "2 cache-hit" in result.stdout
    assert "1 pruned" in result.stdout
    assert "0 skipped" in result.stdout
    assert "0 embed-failed" in result.stdout


def test_reindex_summary_notes_when_prune_pass_was_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ReindexReport.prune_skipped=True` (a walk-error-suppressed prune
    pass) is surfaced in the printed summary, distinguishing it from a run
    where zero concepts genuinely qualified for pruning (review carry-over,
    fold-in #3; reindex-command: Summary reports when the prune pass was
    skipped)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=1, cache_hits=0, pruned=0, skipped=0, prune_skipped=True
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "prune pass" in result.stdout.lower()
    assert "skipped" in result.stdout.lower().split("prune pass")[1]


def test_reindex_summary_omits_prune_skip_note_when_prune_ran_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal run (`prune_skipped=False`) prints the standard summary
    line with no walk-error-suppression note."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=1, cache_hits=0, pruned=1, skipped=0, prune_skipped=False
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "prune pass" not in result.stdout.lower()


def test_reindex_summary_notes_when_model_tag_forced_the_reembed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ReindexReport.model_reembedded=True` is surfaced in the printed
    summary, naming the OLD (previously stored) and NEW (configured) model
    tags -- distinguishing a heavy, embedding-model-driven full re-embed
    from an ordinary large content change (review correction, WARNING
    finding: model-tag force observability). #888: the comparison is now
    against `report.effective_model_tag` (the `{model}#{composition}` tag),
    never the bare configured model name."""
    _init_workspace(tmp_path, monkeypatch)
    layout = config.WorkspaceLayout(tmp_path)
    with open_vector_store(layout.vectors_db_path) as db:
        db.write_model_tag("old-model#chunk-v1")
        db.commit()
    fake_report = ReindexReport(
        embedded=2,
        cache_hits=0,
        pruned=0,
        skipped=0,
        model_reembedded=True,
        effective_model_tag="bge-m3#chunk-v1",
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "embedding model" in result.stdout.lower()
    assert "old-model" in result.stdout
    assert "bge-m3" in result.stdout
    # skipped == 0: the summary must read as a COMPLETE re-embed and must NOT
    # borrow the skipped>0 branch's "incomplete" wording (symmetric guard to
    # the unhealed-case tests, so a branch mix-up on the complete path fails).
    assert "re-embedded all vectors" in result.stdout.lower()
    assert "incomplete" not in result.stdout.lower()
    assert "embed_calls" in result.stdout.lower()


def test_reindex_summary_notes_when_model_reembed_left_docs_unhealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model-tag-forced run that STILL has `skipped > 0` surfaces a
    non-contradictory summary: it must NOT claim "re-embedded all vectors"
    (unqualified) while ALSO saying some docs could not be re-embedded --
    instead it says the re-embed is incomplete and will retry next run
    (review correction round 2, WARNING finding: self-contradictory
    wording)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=1, cache_hits=0, pruned=0, skipped=1, model_reembedded=True
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "not" in result.stdout.lower()
    assert "next" in result.stdout.lower()
    assert "re-embedded all vectors" not in result.stdout.lower()
    assert "incomplete" in result.stdout.lower()


def test_reindex_summary_notes_when_model_reembed_left_every_doc_unhealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most extreme unhealed case (the WHOLE bundle was transiently
    unreadable this run: `embedded=0`, `skipped=` every discovered doc)
    must still avoid claiming "re-embedded all vectors" -- the wording
    stays accurate even when literally nothing was re-embedded (review
    correction round 2, WARNING finding)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=0, cache_hits=0, pruned=0, skipped=3, model_reembedded=True
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "re-embedded all vectors" not in result.stdout.lower()
    assert "incomplete" in result.stdout.lower()


def test_reindex_summary_omits_model_tag_note_on_an_ordinary_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary content-change run (`model_reembedded=False`, the
    default) prints no model-tag note at all, even when every doc was
    embedded."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(embedded=5, cache_hits=0, pruned=0, skipped=0)
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "embedding model" not in result.stdout.lower()


def test_reindex_embed_failed_prints_actionable_rerun_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ReindexReport.embed_failed > 0` prints a distinct, actionable stderr
    notice stating the run is incomplete and advising a re-run (spec:
    reindex-command Reindex Surfaces An Actionable Re-Run Notice On
    Embed-Failure Skips)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=9, cache_hits=0, pruned=0, skipped=0, embed_failed=1
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "1 embed-failed" in result.stdout
    assert "incomplete" in result.stderr.lower()
    assert "openkos reindex" in result.stderr.lower()


def test_reindex_ordinary_skip_does_not_print_embed_failure_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permanent `skipped > 0` (unreadable/parse failure) with
    `embed_failed == 0` does NOT print the embed-failure re-run notice --
    the two skip kinds are never conflated (spec: An ordinary unreadable-
    file skip does not print the embed-failure notice)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=2, cache_hits=0, pruned=0, skipped=1, embed_failed=0
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert result.stderr == ""


def test_reindex_model_switch_partial_embed_failure_prints_the_same_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model-switch run with a partial `embed_failed > 0` also fires the
    actionable notice, even though `model_reembedded=True` (spec:
    Model-switch run with a partial embed failure prints the same
    notice)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=1,
        cache_hits=0,
        pruned=0,
        skipped=0,
        embed_failed=1,
        model_reembedded=True,
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "incomplete" in result.stderr.lower()


def test_reindex_model_switch_embed_failed_does_not_claim_re_embedded_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model-switch run with `skipped == 0` but `embed_failed > 0` must NOT
    print the "re-embedded all vectors" success line -- the tag-persist gate
    (`state/reindex.py`) withholds the new tag whenever `skipped > 0 OR
    embed_failed > 0`, so a `skipped == 0`-only success message would claim a
    complete re-embed while the tag was actually withheld (review
    correction, CRITICAL finding: the summary gate did not match the widened
    tag-persist gate). The actionable INCOMPLETE re-run notice on stderr
    must still fire."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=1,
        cache_hits=0,
        pruned=0,
        skipped=0,
        embed_failed=1,
        model_reembedded=True,
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "re-embedded all vectors" not in result.stdout.lower()
    assert "incomplete" in result.stderr.lower()


def test_reindex_builds_ollama_client_from_configured_embedding_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reindex` builds the `Embedder` from the workspace's configured
    `embedding_model`, not a hardcoded value."""
    _init_workspace(tmp_path, monkeypatch)
    configured_embedding_model = "custom-embed:test"
    (tmp_path / "openkos.yaml").write_text(
        f"embedding_model: {configured_embedding_model}\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def _recording_reindex(
        bundle_dir: object, db: object, embedder: object, **kwargs: object
    ) -> ReindexReport:
        captured["embedder"] = embedder
        return ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    embedder = captured["embedder"]
    assert embedder._model == configured_embedding_model  # type: ignore[attr-defined]


def test_reindex_force_flag_is_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--force` is forwarded unchanged to the orchestrator."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_reindex(*args: object, **kwargs: object) -> ReindexReport:
        captured["kwargs"] = kwargs
        return ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex", "--force"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["force"] is True


def test_reindex_omitted_force_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting `--force` forwards `force=False`."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_reindex(*args: object, **kwargs: object) -> ReindexReport:
        captured["kwargs"] = kwargs
        return ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["force"] is False


def test_reindex_passes_configured_embedding_model_as_model_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`reindex` forwards the workspace's configured `embedding_model` as
    `model_tag` into `state.reindex.reindex(...)` (MVP-2 follow-up #5;
    spec: reindex-command `reindex()` Accepts An Explicit Model Tag
    Parameter -- CLI wires `cfg.embedding_model` into `reindex`)."""
    _init_workspace(tmp_path, monkeypatch)
    configured_embedding_model = "custom-embed:test"
    (tmp_path / "openkos.yaml").write_text(
        f"embedding_model: {configured_embedding_model}\n", encoding="utf-8"
    )
    captured: dict[str, object] = {}

    def _recording_reindex(*args: object, **kwargs: object) -> ReindexReport:
        captured["kwargs"] = kwargs
        return ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["model_tag"] == configured_embedding_model


def test_reindex_ollama_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator raising `OllamaUnavailable` is caught, printed as a
    friendly stderr message with `ollama serve` remediation, and exits 1
    with no raw traceback (spec: Ollama unreachable exits 1 with a clear
    message)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_unavailable(*args: object, **kwargs: object) -> ReindexReport:
        raise OllamaUnavailable("Ollama not reachable at http://localhost:11434")

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _raise_unavailable)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert "Ollama not reachable" in result.stderr
    assert "ollama serve" in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_model_not_found_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator raising `OllamaModelNotFound` is caught, printed
    with the configured embedding model tag and an `ollama pull`
    remediation, and exits 1 with no raw traceback."""
    _init_workspace(tmp_path, monkeypatch)
    configured_embedding_model = "custom-embed:test"
    (tmp_path / "openkos.yaml").write_text(
        f"embedding_model: {configured_embedding_model}\n", encoding="utf-8"
    )

    def _raise_model_not_found(*args: object, **kwargs: object) -> ReindexReport:
        raise OllamaModelNotFound("Model not found (404): {}")

    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", _raise_model_not_found
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert "is not installed" in result.stderr
    assert f"ollama pull {configured_embedding_model}" in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_dimension_mismatch_maps_to_exit_one_with_dedicated_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator raising `OllamaEmbeddingDimensionMismatch` is caught
    by a DEDICATED branch -- distinct from the generic `OllamaError` ladder
    entry -- printed with remediation naming `embedding_model` in
    `openkos.yaml`, and exits 1 with no raw traceback. It must never say
    "will retry next run" (spec: Per-Doc Embed Failure Is Isolated, Not
    Fatal -- dimension mismatch propagation).

    The remediation phrase ("restore" + "openkos.yaml") is the genuine RED
    proof: the generic `except (VecUnavailable, FtsUnavailable, OllamaError)`
    branch below only ever echoes `f"openkos reindex: failed -- {exc}."` --
    it never mentions `openkos.yaml` or "restore". If the dedicated
    `except OllamaEmbeddingDimensionMismatch` branch were reordered to AFTER
    that generic tuple, this exception (an `OllamaError` subclass) would be
    swallowed by the generic branch instead, and this assertion would fail
    for real -- not merely by coincidence of matching exception text."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_dimension_mismatch(*args: object, **kwargs: object) -> ReindexReport:
        raise OllamaEmbeddingDimensionMismatch(
            "Ollama returned an embedding row of length 768, expected "
            "exactly 1024 (EMBED_DIM) -- this is a permanent dimension "
            "mismatch caused by the configured embedding model, not a "
            "transient failure; it will not heal by retrying."
        )

    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", _raise_dimension_mismatch
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert "restore" in result.stderr.lower()
    assert "openkos.yaml" in result.stderr
    assert "will retry next run" not in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_reindex_generic_ollama_error_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain `OllamaError` (neither `OllamaUnavailable` nor
    `OllamaModelNotFound`) is caught, printed as a generic friendly message,
    and exits 1 with no raw traceback (spec: Error Ladder Mirrors query)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_generic(*args: object, **kwargs: object) -> ReindexReport:
        raise OllamaError("boom")

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _raise_generic)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr == "openkos reindex: failed -- boom.\n"
    assert "Traceback" not in result.stderr


def test_reindex_vec_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`open_vector_store` raising `VecUnavailable` (`sqlite-vec` cannot be
    loaded) is caught, printed as a friendly stderr message, and exits 1
    with no raw traceback (spec: Vector extension unavailable exits 1 with
    a clear message)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_vec_unavailable(*args: object, **kwargs: object) -> None:
        raise VecUnavailable("the sqlite-vec extension could not be loaded")

    monkeypatch.setattr("openkos.cli.main.open_vector_store", _raise_vec_unavailable)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert "sqlite-vec" in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_fts_unavailable_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The orchestrator raising `FtsUnavailable` (SQLite's `fts5` module not
    compiled in, e.g. from the new FTS-persistence write path) is caught,
    printed as a friendly stderr message, and exits 1 with no raw traceback
    -- `reindex`'s own docstring already claims this ladder mirrors
    `query`'s (`VecUnavailable` substituted for `FtsUnavailable`), and
    `query` already catches `FtsUnavailable`; `reindex` must too (review
    correction, Finding A)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_fts_unavailable(*args: object, **kwargs: object) -> ReindexReport:
        raise FtsUnavailable("SQLite's fts5 module is not available")

    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", _raise_fts_unavailable
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert "fts5" in result.stderr
    assert "Traceback" not in result.stderr


# --- reindex-lock-handling: ladder 1 (vectors/fts) --------------------------

_LOCK_MESSAGE_FRAGMENT = "holding the workspace lock"


def test_reindex_locked_vectors_db_at_open_exits_one_with_the_retry_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock-contention `OperationalError` raised at `open_vector_store`
    (store open) is caught, prints the uniform lock-contention retry
    message, and exits 1 with no raw traceback (spec: Locked vectors.db
    exits 1 with the retry message, no traceback; task 3.1)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_locked(*args: object, **kwargs: object) -> None:
        raise make_locked_error()

    monkeypatch.setattr("openkos.cli.main.open_vector_store", _raise_locked)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert _LOCK_MESSAGE_FRAGMENT in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_locked_vectors_db_at_upsert_or_commit_exits_one_with_the_retry_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock-contention `OperationalError` raised inside
    `state.reindex.reindex` (representing a lock hit at `upsert_many`/the
    end-of-run `commit`) is caught the same way, exits 1, no traceback
    (task 3.2)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_locked(*args: object, **kwargs: object) -> ReindexReport:
        raise make_locked_error()

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _raise_locked)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert _LOCK_MESSAGE_FRAGMENT in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_locked_fts_db_at_begin_immediate_exits_one_with_the_retry_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock-contention `OperationalError` raised inside
    `state.reindex.reindex` (representing a lock hit at `write_fts_index`'s
    `BEGIN IMMEDIATE`, propagated unchanged per `state/fts.py`'s errorcode
    discrimination) is caught the SAME way, exits 1, no traceback (spec:
    Locked fts.db, including at BEGIN IMMEDIATE, exits 1 with the retry
    message; task 3.3)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_locked(*args: object, **kwargs: object) -> ReindexReport:
        raise make_locked_error("database is locked", errorcode=sqlite3.SQLITE_LOCKED)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _raise_locked)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert _LOCK_MESSAGE_FRAGMENT in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_non_lock_operational_error_is_re_raised_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-lock `sqlite3.OperationalError` (e.g. `SQLITE_ERROR`) is NOT
    caught by the lock-contention handler -- it keeps its EXISTING
    (uncaught, pre-this-change) behavior rather than being swallowed into a
    generic clean exit (spec: A non-lock operational error is not
    mislabeled as lock contention; task 3.4)."""
    _init_workspace(tmp_path, monkeypatch)

    def _raise_non_lock(*args: object, **kwargs: object) -> ReindexReport:
        raise make_non_lock_operational_error("disk I/O error")

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _raise_non_lock)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert not isinstance(result.exception, SystemExit)
    assert isinstance(result.exception, sqlite3.OperationalError)
    assert _LOCK_MESSAGE_FRAGMENT not in (result.stderr or "")


class _FakeEmbedder:
    """A hermetic stand-in for `OllamaClient` -- no network, no real Ollama
    process, deterministic vectors of `EMBED_DIM` length."""

    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

    def __init__(self, *, model: str = "fake") -> None:
        self._model = model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(i)] * EMBED_DIM for i, _ in enumerate(texts)]


def test_reindex_persists_fts_db_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real (unmocked) `reindex` run -- through the CLI, `open_vector_store`,
    and `state.reindex.reindex` -- persists `.openkos/fts.db` alongside
    `vectors.db` (reindex-command: Reindex writes all three derived stores;
    here, the FTS one PR1 delivers), proving `WorkspaceLayout.fts_db_path` is
    genuinely threaded through the CLI's thin-wiring call, not just present
    on the dataclass."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\ndescription: ''\n---\ndichotomyzz\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeEmbedder)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    fts_db_path = tmp_path / ".openkos" / "fts.db"
    assert fts_db_path.exists()


def test_reindex_persists_graph_db_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real (unmocked) `reindex` run persists `.openkos/graph.db`
    alongside `vectors.db`/`fts.db` (reindex-command: Reindex writes all
    three derived stores in one run) -- proves
    `WorkspaceLayout.graph_db_path` and `sqlite_graph.reindex_graph` are
    genuinely threaded through the CLI's thin-wiring call (Slice 5, PR2)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\ndescription: ''\n---\ndichotomyzz\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("openkos.cli.main.OllamaClient", _FakeEmbedder)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    vectors_db_path = tmp_path / ".openkos" / "vectors.db"
    fts_db_path = tmp_path / ".openkos" / "fts.db"
    graph_db_path = tmp_path / ".openkos" / "graph.db"
    assert vectors_db_path.exists()
    assert fts_db_path.exists()
    assert graph_db_path.exists()


class _PartiallyFaultyEmbedder:
    """A hermetic stand-in for `OllamaClient` that raises the generic
    transient `OllamaError` for any text containing `poison_marker`,
    embedding everything else normally -- drives a REAL (unmocked)
    `reindex` run through the CLI, `open_vector_store`, and
    `state.reindex.reindex`'s per-doc embed loop end-to-end (integration:
    real `reindex()` over a temp bundle, one doc transiently fails, others
    survive)."""

    locality = LOCAL_BACKEND_LOCALITY
    """Stands in for `OllamaClient.locality` (issue #240): the CLI reads it
    for the embedding-host advisory and the confidential local exemption,
    and a fake without it raises `AttributeError` inside a fail-open
    handler -- a fixture gap that would read as a degrade."""

    def __init__(self, *, model: str = "fake", poison_marker: str = "") -> None:
        self._model = model
        self._poison_marker = poison_marker

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        text = texts[0]
        if self._poison_marker and self._poison_marker in text:
            raise OllamaError("EOF after retries")
        return [[float(i)] * EMBED_DIM for i, _ in enumerate(texts)]


def test_reindex_one_transient_embed_failure_survives_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A REAL (unmocked) `reindex` run -- through the CLI, `open_vector_store`,
    and `state.reindex.reindex`'s per-doc embed loop -- where one doc's embed
    transiently fails: the survivor is committed to `vectors.db`, exit code
    is `0`, and stderr carries the actionable re-run notice naming the
    failure count (spec: reindex-command Per-Doc Embed Failure Is Isolated,
    Not Fatal -- integration)."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "bundle" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "bundle" / "concepts" / "stoicism.md").write_text(
        "---\ntype: Concept\ntitle: Stoicism\ndescription: ''\n---\ndichotomyzz\n",
        encoding="utf-8",
    )
    (tmp_path / "bundle" / "concepts" / "poison.md").write_text(
        "---\ntype: Concept\ntitle: Poison\ndescription: ''\n---\npoison-marker-text\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient",
        lambda **kwargs: _PartiallyFaultyEmbedder(
            poison_marker="poison-marker-text", **kwargs
        ),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "1 embedded" in result.stdout
    assert "0 skipped" in result.stdout
    assert "1" in result.stderr
    assert "incomplete" in result.stderr.lower()

    layout = config.WorkspaceLayout(tmp_path)
    with open_vector_store(layout.vectors_db_path) as db:
        hashes = db.meta_hashes()
    assert set(hashes) == {"concepts/stoicism"}  # survivor committed
    assert "concepts/poison" not in hashes  # poison doc never upserted


def test_reindex_graph_write_failure_after_vectors_and_fts_succeed_maps_to_exit_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sqlite_graph.reindex_graph` raising a `sqlite3.Error` (e.g.
    permission-denied, corrupt `graph.db`, disk-IO failure) AFTER
    `vectors.db`/`fts.db` already succeeded is caught, printed as a clean
    stderr message identifying the graph store, and exits 1 with no raw
    traceback (PR3 carry-over fix, Engram bug #1470: the graph reindex
    ladder gap -- the `reindex_graph` call sat in the try block but no
    `except` clause covered a bare `sqlite3.Error` from it)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0)
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    def _raise_graph_write_failure(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        "openkos.cli.main.sqlite_graph.reindex_graph", _raise_graph_write_failure
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed")
    assert "graph" in result.stderr
    assert "disk I/O error" in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_summary_and_prune_skipped_notice_still_surface_when_graph_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When `sqlite_graph.reindex_graph` fails AFTER `vectors.db`/`fts.db`
    already succeeded, the user still sees the embedded/cache-hit/pruned/
    skipped summary AND the `prune_skipped` follow-up notice for the work
    that DID durably happen -- not just the graph failure message -- and the
    process still exits 1 (review finding R4: the summary/prune_skipped
    print block used to sit AFTER the graph write, so a graph-write failure
    silently swallowed it even though vectors.db/fts.db had already
    committed)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(
        embedded=3, cache_hits=1, pruned=0, skipped=0, prune_skipped=True
    )
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    def _raise_graph_write_failure(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        "openkos.cli.main.sqlite_graph.reindex_graph", _raise_graph_write_failure
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "3 embedded" in result.stdout
    assert "1 cache-hit" in result.stdout
    assert "0 pruned" in result.stdout
    assert "0 skipped" in result.stdout
    assert "0 embed-failed" in result.stdout
    assert "prune pass" in result.stdout.lower()
    assert "skipped" in result.stdout.lower().split("prune pass")[1]
    assert "graph" in result.stderr
    assert "Traceback" not in result.stderr


# --- reindex-lock-handling: ladder 2 (graph) --------------------------------


def test_reindex_locked_graph_db_exits_one_with_the_same_retry_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock-contention `OperationalError` raised by
    `sqlite_graph.reindex_graph` -- a concurrent process holding `graph.db`'s
    write lock past `busy_timeout`, e.g. at its own `BEGIN IMMEDIATE` -- is
    caught and reports the SAME uniform lock-contention message ladder 1
    uses for vectors.db/fts.db, exiting 1 with no raw traceback -- NOT the
    graph-specific "failed while writing the graph index" message a
    non-lock `sqlite3.Error` still gets (spec: Locked graph.db exits 1 with
    the SAME uniform message; task 4.1)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0)
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    def _raise_locked(*args: object, **kwargs: object) -> None:
        raise make_locked_error()

    monkeypatch.setattr("openkos.cli.main.sqlite_graph.reindex_graph", _raise_locked)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith("openkos reindex: failed -- ")
    assert _LOCK_MESSAGE_FRAGMENT in result.stderr
    assert "writing the graph index" not in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_non_lock_graph_error_keeps_the_existing_graph_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-lock `sqlite3.Error` from `sqlite_graph.reindex_graph` (e.g.
    permission-denied, corrupt `graph.db`) keeps its EXISTING, graph-specific
    "failed while writing the graph index" message -- NOT the uniform
    lock-contention message -- proving the two are still discriminated
    after the lock-contention branch was added (task 4.2 regression;
    mirrors `test_reindex_graph_write_failure_after_vectors_and_fts_succeed_maps_to_exit_one`
    but asserts the NEGATIVE: the lock message must NOT appear here)."""
    _init_workspace(tmp_path, monkeypatch)
    fake_report = ReindexReport(embedded=1, cache_hits=0, pruned=0, skipped=0)
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex", lambda *a, **k: fake_report
    )

    def _raise_non_lock(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr("openkos.cli.main.sqlite_graph.reindex_graph", _raise_non_lock)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "failed while writing the graph index" in result.stderr
    assert _LOCK_MESSAGE_FRAGMENT not in result.stderr
    assert "Traceback" not in result.stderr


def test_reindex_malformed_config_maps_to_exit_one_before_calling_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed `openkos.yaml` (Phase-A `read_config` guard) is caught,
    printed as a friendly stderr message, exits 1 with no raw traceback, and
    the orchestrator is never reached."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "openkos.yaml").write_text("model: [unclosed\n", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(
        "openkos.cli.main.reindex_module.reindex",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.startswith(
        "openkos reindex: failed while reading the workspace -- "
    )
    assert "Traceback" not in result.stderr
    assert calls == []


# ---------------------------------------------------------------------------
# Issue #190: TTY-gated per-doc embedding progress wiring
# ---------------------------------------------------------------------------


def test_reindex_wires_tty_gated_progress_callback_into_the_orchestrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a TTY, `reindex` passes `observability.progress_callback`'s hook
    into `state.reindex.reindex` as `on_progress`; each invocation renders
    `openkos reindex: embedding doc <i>/<n> - <elapsed>...` on STDERR
    while STDOUT keeps the clean summary (issue #190, in-place + elapsed
    since #383/#384, mirroring `suggest-relations`' #134 wiring)."""
    _init_workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)

    def _recording_reindex(*args: object, **kwargs: object) -> ReindexReport:
        on_progress = kwargs["on_progress"]
        assert callable(on_progress)
        on_progress(1, 2, "concepts/alpha")
        on_progress(2, 2, "concepts/beta")
        return ReindexReport(embedded=2, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert "openkos reindex: embedding doc 1/2 - " in result.stderr
    assert "openkos reindex: embedding doc 2/2 - " in result.stderr
    assert "embedding doc" not in result.stdout


def test_reindex_passes_no_progress_hook_when_stderr_is_not_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a TTY (`CliRunner`'s default), the factory returns `None`
    and the orchestrator receives `on_progress=None` -- piped output stays
    byte-clean (issue #190)."""
    _init_workspace(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    def _recording_reindex(*args: object, **kwargs: object) -> ReindexReport:
        captured["on_progress"] = kwargs["on_progress"]
        return ReindexReport(embedded=0, cache_hits=0, pruned=0, skipped=0)

    monkeypatch.setattr("openkos.cli.main.reindex_module.reindex", _recording_reindex)

    result = runner.invoke(app, ["reindex"])

    assert result.exit_code == 0
    assert captured["on_progress"] is None


# --- Phase (#888): the corrected three-branch re-embed trigger wording -----


def test_reembed_trigger_wording_names_a_genuine_model_change() -> None:
    """S11: model parts differ -> `embedding model changed (<old> -> <new>)`
    -- design's own worked example."""
    from openkos.cli.main import _reembed_trigger_wording

    wording = _reembed_trigger_wording(
        "bge-m3#chunk-v1", "qwen3-embedding:0.6b#chunk-v1"
    )

    assert wording == "embedding model changed (bge-m3 -> qwen3-embedding:0.6b)"


def test_reembed_trigger_wording_names_a_composition_only_change() -> None:
    """S11: model parts equal, composition differs -> composition wording,
    NEVER "embedding model changed" -- the exact defect this change fixes
    (a composition-only bump, such as this change's own `compose-v1` ->
    `chunk-v1`, previously reported a false model change)."""
    from openkos.cli.main import _reembed_trigger_wording

    wording = _reembed_trigger_wording("bge-m3#compose-v1", "bge-m3#chunk-v1")

    assert wording == (
        "embed text composition changed (compose-v1 -> chunk-v1); "
        "your embedding model is unchanged (bge-m3)"
    )
    assert "embedding model changed" not in wording


def test_reembed_trigger_wording_names_an_absent_previous_tag() -> None:
    """S11: no previous tag stored (fresh store, or one `purge` dropped) ->
    the fresh-or-dropped-store wording, naming neither a model nor a
    composition change."""
    from openkos.cli.main import _reembed_trigger_wording

    wording = _reembed_trigger_wording(None, "bge-m3#chunk-v1")

    assert wording == "no embedding-model tag stored (fresh or dropped store)"
    assert "changed" not in wording


def test_reembed_trigger_wording_handles_a_pre_composition_bare_tag() -> None:
    """A previously stored tag with no `#` at all (a store predating the
    composition-tag suffix entirely) is treated as `(model, "")` -- the
    model part compares correctly against a genuine model bump."""
    from openkos.cli.main import _reembed_trigger_wording

    wording = _reembed_trigger_wording("bge-m3", "qwen3-embedding:0.6b#chunk-v1")

    assert wording == "embedding model changed (bge-m3 -> qwen3-embedding:0.6b)"
