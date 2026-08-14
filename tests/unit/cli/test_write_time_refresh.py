"""Unit tests for write-time derived-index refresh (issue #640).

Nearly every mutating verb invalidates a derived store (`.openkos/fts.db`,
`.openkos/graph.db`, `.openkos/vectors.db`), and before #640 the user had to
remember `openkos reindex` -- the stale-index warnings fired only AFTER a
degraded answer. #640's contract: the operation that invalidates an index
refreshes it, at END of run, once per invocation, FAIL-OPEN (an index
refresh must never cost the user the write that already committed).

Test seams follow the module's sanctioned pattern (patching public
attributes on `openkos.cli.main`): the per-verb wiring tests replace
`main._refresh_derived_after_write` with a recorder, exactly how
`test_ingest.py` patches `state_reindex._reindex_fts`; the degrade tests
patch `reindex_module.reindex` (the ONE Ollama-needing stage) and assert
the cheap stores were still refreshed -- the cheap-first ordering that is
the helper's central promise.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner, _NamedTextIOWrapper

from openkos.cli import curate as curate_module
from openkos.cli import main
from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.llm.ollama import OllamaUnavailable
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch
from openkos.retrieval.answer import AnswerResult, Citation
from openkos.state import reindex as state_reindex
from openkos.state.derived import stale_derived_stores
from tests.unit.vcs.conftest import isolate_git_identity

runner = CliRunner()


def _simulate_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `sys.stdin.isatty()` report `True` inside a `CliRunner.invoke`
    call (mirrors `test_ingest.py::_simulate_tty`)."""
    monkeypatch.setattr(_NamedTextIOWrapper, "isatty", lambda self: True)


def _init_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _init_workspace_git(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_init_workspace` plus an isolated git identity, for the verbs whose
    success path auto-commits per item (mirrors `test_adjudicate.py`)."""
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path_factory.mktemp("git-identity-config")
    isolate_git_identity(
        monkeypatch, config_dir, name="Isolated Tester", email="tester@example.invalid"
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0


def _write_doc(path: Path, *, doc_type: str = "Concept", title: str = "Stub") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {doc_type}\ntitle: {title}\n---\n# {title}\n",
        encoding="utf-8",
    )


def _ingest_source(tmp_path: Path, name: str) -> str:
    """Ingest one Source concept via `ingest --auto`, returning its id."""
    source = tmp_path / name
    source.write_text("content", encoding="utf-8")
    result = runner.invoke(app, ["ingest", name, "--auto"])
    assert result.exit_code == 0
    return f"sources/{Path(name).stem}"


def _patch_refresh_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace `main._refresh_derived_after_write` with a recorder returning
    True (a complete refresh), collecting each call's `verb`."""
    calls: list[str] = []

    def _recorder(layout: object, cfg: object, *, verb: str) -> bool:
        calls.append(verb)
        return True

    monkeypatch.setattr(main, "_refresh_derived_after_write", _recorder)
    return calls


def _fake_matched_answer() -> AnswerResult:
    return AnswerResult(
        answer="Stoicism teaches the dichotomy of control.",
        citations=[Citation(concept_id="concepts/stoicism", title="Stoicism")],
        fts_hit_count=1,
        llm_invoked=True,
        no_match_cause="none",
        skip_notices=[],
    )


def _derived_stores(tmp_path: Path) -> list[tuple[str, Path]]:
    openkos_dir = tmp_path / ".openkos"
    return [
        ("fts", openkos_dir / "fts.db"),
        ("graph", openkos_dir / "graph.db"),
    ]


# --- per-verb wiring: exactly one refresh per successful run -----------------


def test_set_sensitivity_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path, "notes.txt")
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(
        app, ["set-sensitivity", concept_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert calls == ["set-sensitivity"]


def test_merge_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "survivor.md", title="Survivor")
    _write_doc(tmp_path / "bundle" / "concepts" / "absorbed.md", title="Absorbed")
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0
    assert calls == ["merge"]


def test_merge_declined_does_not_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declined confirm gate wrote nothing, so nothing was invalidated --
    the refresh must not run (fail-open covers failures AFTER the write,
    never a run that refused to write)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "survivor.md", title="Survivor")
    _write_doc(tmp_path / "bundle" / "concepts" / "absorbed.md", title="Absorbed")
    calls = _patch_refresh_recorder(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed"], input="n\n"
    )

    assert calls == []
    assert result.exit_code != 0 or "merged" not in result.stdout


def test_forget_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path, "notes.txt")
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["forget", concept_id, "--auto"])

    assert result.exit_code == 0
    assert calls == ["forget"]


def test_query_save_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "stoicism.md", title="Stoicism")
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _fake_matched_answer()
    )
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert calls == ["query"]


def test_query_without_save_never_refreshes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain read-only `query` writes nothing and must not refresh."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "stoicism.md", title="Stoicism")
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _fake_matched_answer()
    )
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["query", "what is stoicism?"])

    assert result.exit_code == 0
    assert calls == []


def test_curate_refreshes_once_when_a_stage_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`curate` refreshes ONCE at end of run when any stage applied a write
    -- never per stage, never per item (its Identity merges already
    auto-commit per item through `_commit_one_merge`, which must stay
    refresh-free)."""
    _init_workspace(tmp_path, monkeypatch)
    outcomes = [curate_module.StageOutcome(status="applied", applied=2)] + [
        curate_module.StageOutcome(status="empty") for _ in range(4)
    ]
    monkeypatch.setattr(curate_module, "run_curate", lambda ctx: outcomes)
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert calls == ["curate"]


def test_curate_with_no_applied_write_skips_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    outcomes = [curate_module.StageOutcome(status="empty") for _ in range(5)]
    monkeypatch.setattr(curate_module, "run_curate", lambda ctx: outcomes)
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0
    assert calls == []


def test_ingest_single_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #553 end-of-ingest hook now refreshes graph+vectors too, keeping
    its placement semantics: after the pipeline returned, once per run."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert calls == ["ingest"]


def test_ingest_batch_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text("Alpha notes.", encoding="utf-8")
    (inbox / "b.txt").write_text("Beta notes.", encoding="utf-8")
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["ingest", "inbox", "--auto"])

    assert result.exit_code == 0
    assert calls == ["ingest"]


def test_ingest_refused_does_not_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-TTY single-file ingest without `--auto` refuses before any
    write -- the #553 placement semantics (skipped on refusal) carry over."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Some raw notes.", encoding="utf-8")
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["ingest", "notes.txt"])

    assert result.exit_code != 0
    assert calls == []


def test_ingest_graph_index_is_fresh_after_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end (no recorder): a single `ingest --auto` leaves BOTH cheap
    manifest-gated stores on disk and fresh -- graph.db was the #553 gap."""
    _init_workspace(tmp_path, monkeypatch)
    (tmp_path / "notes.txt").write_text("Zorbification notes.", encoding="utf-8")

    result = runner.invoke(app, ["ingest", "notes.txt", "--auto"])

    assert result.exit_code == 0
    assert (tmp_path / ".openkos" / "graph.db").is_file()
    assert (tmp_path / ".openkos" / "fts.db").is_file()
    assert stale_derived_stores(tmp_path / "bundle", _derived_stores(tmp_path)) == ()


# --- optional verbs (same one-line call, same contract) ----------------------


def test_relate_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(
        app, ["relate", "concepts/a", "references", "concepts/b", "--auto"]
    )

    assert result.exit_code == 0
    assert calls == ["relate"]


def test_unmerge_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "survivor.md", title="Survivor")
    _write_doc(tmp_path / "bundle" / "concepts" / "absorbed.md", title="Absorbed")
    merged = runner.invoke(
        app, ["merge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )
    assert merged.exit_code == 0
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(
        app, ["unmerge", "concepts/survivor", "concepts/absorbed", "--auto"]
    )

    assert result.exit_code == 0
    assert calls == ["unmerge"]


def test_normalize_names_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`normalize-names` with an NFD-named on-disk file renames it (a bundle
    write), so the run must refresh."""
    _init_workspace(tmp_path, monkeypatch)
    # An NFD-encoded filename ("é" as "e" + combining acute) that
    # normalize-names rewrites to NFC.
    _write_doc(tmp_path / "bundle" / "concepts" / "café.md", title="Café")
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["normalize-names", "--auto"])

    assert result.exit_code == 0
    assert calls == ["normalize-names"]


def test_suggest_relations_apply_refreshes_derived_once(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    monkeypatch.setattr(
        "openkos.cli.main.candidate_edges",
        lambda bundle_dir, **kwargs: [
            Edge(source_id="concepts/a", target_id="concepts/b")
        ],
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=Edge(source_id="concepts/a", target_id="concepts/b"),
                    suggested_type="references",
                    rationale="stub rationale",
                )
            ]
        ),
    )
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"], input="y\n")

    assert result.exit_code == 0
    assert calls == ["suggest-relations"]


def test_suggest_relations_apply_with_zero_applied_skips_refresh(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Beta")
    monkeypatch.setattr(
        "openkos.cli.main.candidate_edges",
        lambda bundle_dir, **kwargs: [
            Edge(source_id="concepts/a", target_id="concepts/b")
        ],
    )
    monkeypatch.setattr(
        "openkos.cli.main.suggest_edge_types",
        lambda edges, **kwargs: EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(
                    edge=Edge(source_id="concepts/a", target_id="concepts/b"),
                    suggested_type="references",
                    rationale="stub rationale",
                )
            ]
        ),
    )
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["suggest-relations", "--auto", "--apply"], input="n\n")

    assert result.exit_code == 0
    assert calls == []


def test_adjudicate_apply_refreshes_derived_once(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report",
        lambda bundle_dir, **kwargs: CandidateGroupReport(
            groups=(group,), produced=1, retained=1
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda candidates, **kwargs: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        ),
    )
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="y\n")

    assert result.exit_code == 0
    assert calls == ["adjudicate"]


def test_adjudicate_apply_all_declined_skips_refresh(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_workspace_git(tmp_path, tmp_path_factory, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "a.md", title="Concept A")
    _write_doc(tmp_path / "bundle" / "concepts" / "b.md", title="Concept B")
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr(
        "openkos.cli.main.find_candidates_report",
        lambda bundle_dir, **kwargs: CandidateGroupReport(
            groups=(group,), produced=1, retained=1
        ),
    )
    monkeypatch.setattr(
        "openkos.cli.main.adjudicate_candidates",
        lambda candidates, **kwargs: AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=group,
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        ),
    )
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(app, ["adjudicate", "--apply"], input="n\n")

    assert result.exit_code == 0
    assert calls == []


# --- the helper itself: cheap-first, fail-open, one advisory -----------------


def test_refresh_success_leaves_every_gated_store_fresh_and_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through a real verb (no recorder): after `set-sensitivity`
    the manifest-gated stores match the bundle again, so the stale-index
    nags (`status`/`next`/query's warning) disappear naturally, and a clean
    refresh says nothing at all."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path, "notes.txt")

    result = runner.invoke(
        app, ["set-sensitivity", concept_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert stale_derived_stores(tmp_path / "bundle", _derived_stores(tmp_path)) == ()
    assert "refresh incomplete" not in result.stderr


def test_embedder_failure_degrades_to_one_advisory_and_cheap_stores_stay_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-open core of #640: an unreachable embedder costs ONE stderr
    advisory naming the manual fallback -- never the exit code, and never
    the Ollama-free stores. FTS and graph are refreshed BEFORE the vector
    attempt (cheap-first), so they are fresh even though the embed died."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path, "notes.txt")

    def _boom(*args: object, **kwargs: object) -> object:
        raise OllamaUnavailable("connection refused")

    monkeypatch.setattr(state_reindex, "reindex", _boom)

    result = runner.invoke(
        app, ["set-sensitivity", concept_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert result.stderr.count("derived-index refresh incomplete") == 1
    assert "openkos reindex" in result.stderr
    assert stale_derived_stores(tmp_path / "bundle", _derived_stores(tmp_path)) == ()


def test_interrupt_during_the_embed_still_leaves_cheap_stores_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The observable proof of CHEAP-FIRST ordering, which per-stage
    isolation alone cannot pin: a Ctrl-C landing inside the (potentially
    long) embed propagates -- `KeyboardInterrupt` is `BaseException`, never
    absorbed by fail-open -- but FTS and graph were already refreshed
    BEFORE the vector attempt began, so the interrupt costs only the
    embeddings."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path, "notes.txt")

    def _interrupt(*args: object, **kwargs: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(state_reindex, "reindex", _interrupt)

    result = runner.invoke(
        app,
        ["set-sensitivity", concept_id, "confidential", "--auto"],
        catch_exceptions=False,
    )

    # Click maps an uncaught KeyboardInterrupt to `SystemExit(130)` -- the
    # interrupt genuinely aborted the command (fail-open did NOT absorb it),
    # yet the cheap stores were already refreshed before the embed began.
    assert result.exit_code == 130
    assert stale_derived_stores(tmp_path / "bundle", _derived_stores(tmp_path)) == ()


def test_one_dead_cheap_stage_does_not_cost_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage isolation: a dead FTS5 module must not cost the graph its
    refresh -- each stage degrades independently, all failures fold into
    the single advisory."""
    _init_workspace(tmp_path, monkeypatch)
    concept_id = _ingest_source(tmp_path, "notes.txt")

    def _boom(*args: object, **kwargs: object) -> None:
        raise OllamaUnavailable("fts stand-in failure")

    monkeypatch.setattr(state_reindex, "_reindex_fts", _boom)

    result = runner.invoke(
        app, ["set-sensitivity", concept_id, "confidential", "--auto"]
    )

    assert result.exit_code == 0
    assert result.stderr.count("derived-index refresh incomplete") == 1
    graph_only = [("graph", tmp_path / ".openkos" / "graph.db")]
    assert stale_derived_stores(tmp_path / "bundle", graph_only) == ()


# --- query --save message accuracy (#640, item 3) ----------------------------


def test_query_save_success_message_no_longer_instructs_manual_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the write-time refresh succeeded, "Run `openkos reindex` to make
    it searchable." is false -- the filed insight is already indexed, and
    the success line must say so."""
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "stoicism.md", title="Stoicism")
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _fake_matched_answer()
    )

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "Run `openkos reindex` to make it searchable." not in result.stdout
    assert "searchable" in result.stdout


def test_query_save_degrade_path_still_points_at_manual_reindex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_doc(tmp_path / "bundle" / "concepts" / "stoicism.md", title="Stoicism")
    monkeypatch.setattr(
        "openkos.cli.main.answer", lambda *args, **kwargs: _fake_matched_answer()
    )

    def _boom(*args: object, **kwargs: object) -> object:
        raise OllamaUnavailable("connection refused")

    monkeypatch.setattr(state_reindex, "reindex", _boom)

    result = runner.invoke(app, ["query", "what is stoicism?", "--save", "--auto"])

    assert result.exit_code == 0
    assert "openkos reindex" in result.stderr
    # The success line must not CLAIM searchability the degrade disproved.
    assert "searchable" not in result.stdout


# --- reconcile (#655): the last write verb joins the #640 contract ----------


def _write_reconcile_pair(tmp_path: Path) -> None:
    _write_doc(tmp_path / "bundle" / "concepts" / "alpha.md", title="Alpha")
    _write_doc(tmp_path / "bundle" / "concepts" / "beta.md", title="Beta")


def test_reconcile_two_id_refreshes_derived_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#655: a successful two-id `reconcile` writes relation edges and body
    notes (graph-invalidating), so it refreshes end-of-run exactly like
    `merge`/`relate` -- once, with its own verb tag."""
    _init_workspace(tmp_path, monkeypatch)
    _write_reconcile_pair(tmp_path)
    calls = _patch_refresh_recorder(monkeypatch)

    result = runner.invoke(
        app, ["reconcile", "concepts/alpha", "concepts/beta", "--auto"]
    )

    assert result.exit_code == 0
    assert calls == ["reconcile"]


def test_reconcile_idempotent_rerun_does_not_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean re-run changes no edge and appends no note -- nothing was
    invalidated, so the refresh must not run (the same no-write rule the
    declined paths follow)."""
    _init_workspace(tmp_path, monkeypatch)
    _write_reconcile_pair(tmp_path)
    first = runner.invoke(
        app, ["reconcile", "concepts/alpha", "concepts/beta", "--auto"]
    )
    assert first.exit_code == 0
    calls = _patch_refresh_recorder(monkeypatch)

    rerun = runner.invoke(
        app, ["reconcile", "concepts/alpha", "concepts/beta", "--auto"]
    )

    assert rerun.exit_code == 0
    assert calls == []


def test_reconcile_declined_does_not_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_workspace(tmp_path, monkeypatch)
    _write_reconcile_pair(tmp_path)
    calls = _patch_refresh_recorder(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(
        app, ["reconcile", "concepts/alpha", "concepts/beta"], input="n\n"
    )

    assert calls == []
    assert result.exit_code != 0


def test_reconcile_from_findings_refreshes_once_when_a_pair_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#655: the `--from-findings` walk shares `_reconcile_pair` with the
    two-id form; one accepted pair -> ONE end-of-run refresh, not one per
    item."""
    from openkos.state import derived as derived_module
    from openkos.state import findings as findings_module
    from openkos.state.vectorstore import content_hash

    _init_workspace(tmp_path, monkeypatch)
    _write_reconcile_pair(tmp_path)
    digests = tuple(
        findings_module.InputDigest(
            concept_id,
            content_hash((tmp_path / "bundle" / f"{concept_id}.md").read_bytes()),
        )
        for concept_id in ("concepts/alpha", "concepts/beta")
    )
    conn = derived_module.open_derived_connection(tmp_path / ".openkos" / "findings.db")
    try:
        findings_module.record_findings(
            conn,
            [
                findings_module.Finding(
                    pair_ids=("concepts/alpha", "concepts/beta"),
                    merged_absorbed_id=None,
                    verdict="contradicts",
                    confidence=0.9,
                    rationale="dates conflict",
                    input_digests=digests,
                )
            ],
        )
    finally:
        conn.close()
    calls = _patch_refresh_recorder(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["reconcile", "--from-findings"], input="y\n")

    assert result.exit_code == 0
    assert calls == ["reconcile"]


def test_reconcile_from_findings_all_declined_skips_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openkos.state import derived as derived_module
    from openkos.state import findings as findings_module
    from openkos.state.vectorstore import content_hash

    _init_workspace(tmp_path, monkeypatch)
    _write_reconcile_pair(tmp_path)
    digests = tuple(
        findings_module.InputDigest(
            concept_id,
            content_hash((tmp_path / "bundle" / f"{concept_id}.md").read_bytes()),
        )
        for concept_id in ("concepts/alpha", "concepts/beta")
    )
    conn = derived_module.open_derived_connection(tmp_path / ".openkos" / "findings.db")
    try:
        findings_module.record_findings(
            conn,
            [
                findings_module.Finding(
                    pair_ids=("concepts/alpha", "concepts/beta"),
                    merged_absorbed_id=None,
                    verdict="contradicts",
                    confidence=0.9,
                    rationale="dates conflict",
                    input_digests=digests,
                )
            ],
        )
    finally:
        conn.close()
    calls = _patch_refresh_recorder(monkeypatch)
    _simulate_tty(monkeypatch)

    result = runner.invoke(app, ["reconcile", "--from-findings"], input="n\n")

    assert result.exit_code == 0
    assert calls == []
