"""The commit phases of `curate` (#1137, ADR-0036; tasks.md 1.7).

Each stage computes with no workspace lock -- the model calls, the per-item
prompts and the reconciliation pass. Every item's write (a merge, a relation,
a volatility tier) and every persist of paid-for results (adjudication
verdicts, edge suggestions, contradiction findings) is its own commit phase:
it takes the lock, re-validates what it rests on, and DROPS a row whose input
vanished or changed while no lock was held.
"""

import contextlib
from collections.abc import Callable
from pathlib import Path

import pytest

from openkos import config as okf_config
from openkos.application import lifecycle as application_lifecycle
from openkos.application import merge_service
from openkos.cli import main as main_module
from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.llm.base import EMBED_DIM
from openkos.resolution.adjudication import (
    AdjudicatedCandidate,
    AdjudicationBatch,
    Verdict,
)
from openkos.resolution.candidates import CandidateGroup, CandidateGroupReport, Tier
from openkos.resolution.contradiction import (
    CandidatePlan,
    ContradictionBatch,
    ContradictionVerdict,
    _CandidateSpec,
)
from openkos.resolution.contradiction import Verdict as ContradictionKind
from openkos.resolution.edge_typing import EdgeSuggestion, EdgeSuggestionBatch
from openkos.resolution.volatility_typing import TierSuggestion, TierSuggestionBatch
from openkos.state import adjudications as adjudications_store
from openkos.state import derived, findings
from openkos.state import edge_suggestions as edge_suggestions_store
from tests.unit.cli.commit_phase_support import (
    hold_the_lock_from,
    init_workspace,
    lock_is_free,
    runner,
    simulate_tty,
    wrap,
)
from tests.unit.cli.conftest import seed_workspace_docs
from tests.unit.cli.conftest import snapshot_bytes as _snapshot
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY
from tests.unit.vcs.conftest import isolate_git_identity

_SENTINEL = "SENTINEL-CONFIDENTIAL-BODY"
_ALPHA = "Alpha states the shared subject at length. " * 8
_BETA = "Beta restates the same subject in a second voice. " * 8


@pytest.fixture(autouse=True)
def _git_identity(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolate_git_identity(
        monkeypatch,
        tmp_path_factory.mktemp("git-identity-config"),
        name="Isolated Tester",
        email="tester@example.invalid",
    )


class _OfflineOllama:
    locality = LOCAL_BACKEND_LOCALITY

    def chat(self, messages: object) -> str:
        return '{"extract": false}'

    def embed(self, texts: "list[str]") -> "list[list[float]]":
        return [[1.0] + [0.0] * (EMBED_DIM - 1) for _ in texts]


def _write(root: Path, concept_id: str, *, body: str = _ALPHA, extra: str = "") -> Path:
    path = root / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    title = concept_id.rsplit("/", 1)[-1]
    path.write_text(
        f"---\ntype: Concept\ntitle: {title}\n{extra}---\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )
    return path


def _workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *concept_ids: str
) -> None:
    """An initialised, committed and reindexed workspace (Preconditions needs
    a non-empty `vectors.db`) holding `concept_ids`, and every stage but the
    one under test stubbed to an empty queue."""
    init_workspace(tmp_path, monkeypatch)
    for concept_id in concept_ids or ("concepts/a", "concepts/b"):
        _write(tmp_path, concept_id, body=_BETA if concept_id.endswith("b") else _ALPHA)
    seed_workspace_docs(tmp_path)
    monkeypatch.setattr(
        "openkos.cli.main.OllamaClient", lambda *a, **k: _OfflineOllama()
    )
    assert runner.invoke(app, ["reindex"]).exit_code == 0
    simulate_tty(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(),
    )
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    monkeypatch.setattr("openkos.cli.curate._concept_type_names", lambda *a, **k: [])
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: CandidatePlan(specs=(), edge_total=0, merged_total=0),
    )


def _answer(
    monkeypatch: pytest.MonkeyPatch, on_prompt: Callable[[], object] | None = None
) -> None:
    """Answer every per-item prompt `y`, running `on_prompt` first -- the
    moment a human would be reading the preview."""

    def _prompt(*args: object, **kwargs: object) -> str:
        if on_prompt is not None:
            on_prompt()
        return "y"

    monkeypatch.setattr("typer.prompt", _prompt)


# --- Identity ---------------------------------------------------------------


def _same_group(
    monkeypatch: pytest.MonkeyPatch, on_judge: Callable[[], object] | None = None
) -> CandidateGroup:
    group = CandidateGroup(
        okf_type="Concept",
        member_ids=("concepts/a", "concepts/b"),
        tier=Tier.HIGH,
        trigger="stub",
    )
    monkeypatch.setattr(
        "openkos.cli.curate.find_candidates_report",
        lambda *a, **k: CandidateGroupReport(groups=(group,), produced=1, retained=1),
    )

    def _judge(candidates: list[CandidateGroup], **kwargs: object) -> AdjudicationBatch:
        if on_judge is not None:
            on_judge()
        return AdjudicationBatch(
            results=[
                AdjudicatedCandidate(
                    candidate=candidates[0],
                    verdict=Verdict.SAME,
                    confidence=0.9,
                    rationale="same",
                )
            ]
        )

    monkeypatch.setattr("openkos.cli.curate.adjudicate_candidates", _judge)
    return group


def _stored_adjudications(root: Path) -> tuple[adjudications_store.Adjudication, ...]:
    conn = derived.open_derived_connection(
        okf_config.WorkspaceLayout(root).findings_db_path
    )
    try:
        return adjudications_store.open_adjudications(conn)
    finally:
        conn.close()


def test_identity_judges_asks_and_reconciles_without_the_lock_then_merges_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    at_judge: list[bool] = []
    at_prompt: list[bool] = []
    at_reconcile: list[bool] = []
    at_commit: list[bool] = []
    _same_group(monkeypatch, on_judge=lambda: at_judge.append(lock_is_free(tmp_path)))
    _answer(monkeypatch, on_prompt=lambda: at_prompt.append(lock_is_free(tmp_path)))

    def _reconcile(root: Path, prepared: object) -> tuple[object, None]:
        at_reconcile.append(lock_is_free(tmp_path))
        return prepared, None

    monkeypatch.setattr("openkos.cli.main._reconcile_merged_survivor", _reconcile)
    wrap(
        monkeypatch,
        merge_service,
        "commit_merge",
        before=lambda: at_commit.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Identity: applied 1, skipped 0." in result.stdout.splitlines()
    assert at_judge == [True]
    assert at_prompt == [True]
    assert at_reconcile == [True]
    assert at_commit == [False]


def test_identity_refuses_with_exit_3_when_the_lock_is_busy_at_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _same_group(monkeypatch)
    before = _snapshot(tmp_path)
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        _answer(monkeypatch, on_prompt=hold_the_lock_from(stack, tmp_path, acquired))

        result = runner.invoke(app, ["curate", "--auto"])

    assert acquired == [True]
    assert result.exit_code == 3, result.stderr
    assert "refusing to run" in result.stderr
    assert (tmp_path / "bundle" / "concepts" / "b.md").exists()
    assert (tmp_path / "bundle" / "concepts" / "a.md").exists()
    after = _snapshot(tmp_path)
    assert {k: v for k, v in after.items() if str(k).startswith("bundle")} == {
        k: v for k, v in before.items() if str(k).startswith("bundle")
    }


@pytest.mark.parametrize("scenario", ["unchanged", "edited", "forgotten"])
def test_a_verdict_is_persisted_only_for_the_content_it_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """Judging holds no lock: a member edited or forgotten meanwhile must not
    have the verdict stored against content nobody judged -- and a forgotten
    one must not be NAMED by a row at all."""
    _workspace(tmp_path, monkeypatch)
    at_persist: list[bool] = []

    def _concurrent_writer() -> None:
        if scenario == "edited":
            _write(tmp_path, "concepts/a", body="Rewritten while judging.")
        elif scenario == "forgotten":
            forgotten = runner.invoke(app, ["forget", "concepts/b", "--auto"])
            assert forgotten.exit_code == 0, forgotten.stderr

    _same_group(monkeypatch, on_judge=_concurrent_writer)
    _answer(monkeypatch)
    wrap(
        monkeypatch,
        adjudications_store,
        "record_adjudications",
        before=lambda: at_persist.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    stored = _stored_adjudications(tmp_path)
    if scenario == "unchanged":
        assert at_persist == [False]
        assert [row.member_ids for row in stored] == [("concepts/a", "concepts/b")]
    else:
        assert stored == ()


def test_a_member_forgotten_during_the_prompt_is_dropped_not_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _same_group(monkeypatch)
    survivor = tmp_path / "bundle" / "concepts" / "b.md"
    survivor_before = survivor.read_bytes()

    def _forget_absorbed() -> None:
        forgotten = runner.invoke(app, ["forget", "concepts/a", "--auto"])
        assert forgotten.exit_code == 0, forgotten.stderr

    _answer(monkeypatch, on_prompt=_forget_absorbed)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Identity: applied 0, skipped 1." in result.stdout.splitlines()
    assert "a member no longer exists" in result.stderr
    assert not (tmp_path / "bundle" / "concepts" / "a.md").exists()
    assert survivor.read_bytes() == survivor_before


def test_a_bystander_edited_while_the_prompt_waits_refuses_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sentinel for the read dependencies of curate's merge path. The merge
    plan scanned the whole bundle (which documents reference the absorbed
    concept, what the survivor's sensitivity high-water must cover); a
    document it only READ changing afterwards -- here raised to confidential
    with a sentinel body -- must refuse the merge, writing nothing."""
    _workspace(tmp_path, monkeypatch, "concepts/a", "concepts/b", "concepts/c")
    _same_group(monkeypatch)
    survivor = tmp_path / "bundle" / "concepts" / "b.md"
    absorbed = tmp_path / "bundle" / "concepts" / "a.md"
    before = survivor.read_bytes(), absorbed.read_bytes()

    def _raise_bystander() -> None:
        _write(
            tmp_path,
            "concepts/c",
            body=f"{_SENTINEL} links to [a](a.md).",
            extra="sensitivity: confidential\n",
        )

    _answer(monkeypatch, on_prompt=_raise_bystander)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 3, result.stderr
    assert "read dependency(ies) changed on disk" in result.stderr
    assert "concepts/c.md" in result.stderr
    assert (survivor.read_bytes(), absorbed.read_bytes()) == before
    assert _SENTINEL not in result.stdout


def test_a_document_appearing_while_the_prompt_waits_refuses_the_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Additive drift has no baseline: a NEW document linking to the absorbed
    concept would otherwise be left dangling by the merge."""
    _workspace(tmp_path, monkeypatch)
    _same_group(monkeypatch)
    survivor = tmp_path / "bundle" / "concepts" / "b.md"
    absorbed = tmp_path / "bundle" / "concepts" / "a.md"
    before = survivor.read_bytes(), absorbed.read_bytes()
    _answer(
        monkeypatch,
        on_prompt=lambda: _write(tmp_path, "concepts/late", body="See [a](a.md)."),
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 3, result.stderr
    assert "new document(s) appeared" in result.stderr
    assert (survivor.read_bytes(), absorbed.read_bytes()) == before


def test_a_keep_distinct_ruling_is_recorded_in_a_commit_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _same_group(monkeypatch)
    at_record: list[bool] = []
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "n")
    wrap(
        monkeypatch,
        main_module,
        "_apply_identity_decision",
        before=lambda: at_record.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert at_record == [False]


# --- Structure --------------------------------------------------------------


def _one_edge(
    monkeypatch: pytest.MonkeyPatch, on_type: Callable[[], object] | None = None
) -> Edge:
    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])

    def _suggest(edges: object, **kwargs: object) -> EdgeSuggestionBatch:
        if on_type is not None:
            on_type()
        return EdgeSuggestionBatch(
            results=[
                EdgeSuggestion(edge=edge, suggested_type="references", rationale="r")
            ]
        )

    monkeypatch.setattr("openkos.cli.curate.suggest_edge_types", _suggest)
    return edge


def _stored_edge_suggestions(
    root: Path,
) -> tuple[edge_suggestions_store.PersistedEdgeSuggestion, ...]:
    conn = derived.open_derived_connection(
        okf_config.WorkspaceLayout(root).findings_db_path
    )
    try:
        return edge_suggestions_store.open_edge_suggestions(conn)
    finally:
        conn.close()


def test_structure_types_and_asks_without_the_lock_then_relates_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    at_type: list[bool] = []
    at_prompt: list[bool] = []
    at_write: list[bool] = []
    _one_edge(monkeypatch, on_type=lambda: at_type.append(lock_is_free(tmp_path)))
    _answer(monkeypatch, on_prompt=lambda: at_prompt.append(lock_is_free(tmp_path)))
    wrap(
        monkeypatch,
        application_lifecycle,
        "relate_core",
        before=lambda: at_write.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Structure: applied 1, skipped 0." in result.stdout.splitlines()
    assert at_type == [True]
    assert at_prompt == [True]
    assert at_write == [False]


def test_structure_refuses_with_exit_3_when_the_lock_is_busy_at_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _one_edge(monkeypatch)
    source = tmp_path / "bundle" / "concepts" / "a.md"
    source_before = source.read_bytes()
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        _answer(monkeypatch, on_prompt=hold_the_lock_from(stack, tmp_path, acquired))

        result = runner.invoke(app, ["curate", "--auto"])

    assert acquired == [True]
    assert result.exit_code == 3, result.stderr
    assert source.read_bytes() == source_before


@pytest.mark.parametrize("scenario", ["unchanged", "edited", "forgotten"])
def test_an_edge_suggestion_is_persisted_only_for_the_content_it_typed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    _workspace(tmp_path, monkeypatch)
    at_persist: list[bool] = []

    def _concurrent_writer() -> None:
        if scenario == "edited":
            _write(tmp_path, "concepts/b", body="Rewritten while typing.")
        elif scenario == "forgotten":
            forgotten = runner.invoke(app, ["forget", "concepts/b", "--auto"])
            assert forgotten.exit_code == 0, forgotten.stderr

    _one_edge(monkeypatch, on_type=_concurrent_writer)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "n")
    wrap(
        monkeypatch,
        edge_suggestions_store,
        "record_edge_suggestions",
        before=lambda: at_persist.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    stored = _stored_edge_suggestions(tmp_path)
    if scenario == "unchanged":
        assert at_persist == [False]
        assert [(row.source_id, row.target_id) for row in stored] == [
            ("concepts/a", "concepts/b")
        ]
    else:
        assert stored == ()


def test_an_endpoint_forgotten_during_the_prompt_drops_the_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _one_edge(monkeypatch)
    source = tmp_path / "bundle" / "concepts" / "a.md"

    def _forget_target() -> None:
        forgotten = runner.invoke(app, ["forget", "concepts/b", "--auto"])
        assert forgotten.exit_code == 0, forgotten.stderr

    _answer(monkeypatch, on_prompt=_forget_target)
    source_before = source.read_bytes()

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Structure: applied 0, skipped 1." in result.stdout.splitlines()
    assert source.read_bytes() == source_before
    assert "concepts/b" not in source.read_text(encoding="utf-8")


# --- Metadata ---------------------------------------------------------------


def _one_tier(
    monkeypatch: pytest.MonkeyPatch, on_suggest: Callable[[], object] | None = None
) -> None:
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )

    def _suggest(bundle_dir: Path, **kwargs: object) -> TierSuggestionBatch:
        if on_suggest is not None:
            on_suggest()
        return TierSuggestionBatch(
            results=[
                TierSuggestion(
                    type_name="Concept",
                    current_default="static",
                    suggested_tier="volatile",
                    rationale="r",
                )
            ]
        )

    monkeypatch.setattr("openkos.cli.curate.suggest_volatility", _suggest)


def test_metadata_suggests_and_asks_without_the_lock_then_writes_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    at_suggest: list[bool] = []
    at_prompt: list[bool] = []
    at_write: list[bool] = []
    _one_tier(monkeypatch, on_suggest=lambda: at_suggest.append(lock_is_free(tmp_path)))
    _answer(monkeypatch, on_prompt=lambda: at_prompt.append(lock_is_free(tmp_path)))
    wrap(
        monkeypatch,
        application_lifecycle,
        "set_volatility_core",
        before=lambda: at_write.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Metadata: applied 1, skipped 0." in result.stdout.splitlines()
    assert at_suggest == [True]
    assert at_prompt == [True]
    assert at_write == [False]


def test_metadata_refuses_with_exit_3_when_the_lock_is_busy_at_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _one_tier(monkeypatch)
    config_before = (tmp_path / "openkos.yaml").read_bytes()
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        _answer(monkeypatch, on_prompt=hold_the_lock_from(stack, tmp_path, acquired))

        result = runner.invoke(app, ["curate", "--auto"])

    assert acquired == [True]
    assert result.exit_code == 3, result.stderr
    assert (tmp_path / "openkos.yaml").read_bytes() == config_before


# --- Contradictions ---------------------------------------------------------


def _one_finding(
    monkeypatch: pytest.MonkeyPatch, on_judge: Callable[[], object] | None = None
) -> None:
    pair = ("concepts/a", "concepts/b")
    monkeypatch.setattr(
        "openkos.cli.curate._contradiction_plan",
        lambda *a, **k: CandidatePlan(
            specs=(_CandidateSpec(pair_ids=pair, relation_type="related_to"),),
            edge_total=1,
            merged_total=0,
        ),
    )
    verdict = ContradictionVerdict(
        pair_ids=pair,
        verdict=ContradictionKind.CONTRADICTS,
        confidence=0.9,
        rationale="stated conflicting claims",
        conflicting_claims=("claim one",),
    )

    def _judge(*args: object, **kwargs: object) -> tuple[ContradictionBatch, int]:
        if on_judge is not None:
            on_judge()
        return ContradictionBatch(results=[verdict]), 1

    monkeypatch.setattr("openkos.cli.curate.find_contradictions", _judge)


def _stored_findings(root: Path) -> tuple[findings.PersistedFinding, ...]:
    conn = derived.open_derived_connection(
        okf_config.WorkspaceLayout(root).findings_db_path
    )
    try:
        return tuple(findings.open_findings(conn))
    finally:
        conn.close()


@pytest.mark.parametrize("scenario", ["unchanged", "edited", "forgotten"])
def test_a_finding_is_persisted_only_for_the_content_it_judged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """The headline #1137 property: a persist after a concurrent `forget` of
    an input writes no row naming it, and one after an edit stores no digest
    of content nobody judged."""
    _workspace(tmp_path, monkeypatch)
    at_judge: list[bool] = []
    at_persist: list[bool] = []

    def _concurrent_writer() -> None:
        at_judge.append(lock_is_free(tmp_path))
        if scenario == "edited":
            _write(tmp_path, "concepts/b", body="Rewritten while judging.")
        elif scenario == "forgotten":
            forgotten = runner.invoke(app, ["forget", "concepts/b", "--auto"])
            assert forgotten.exit_code == 0, forgotten.stderr

    _one_finding(monkeypatch, on_judge=_concurrent_writer)
    wrap(
        monkeypatch,
        findings,
        "record_findings",
        before=lambda: at_persist.append(lock_is_free(tmp_path)),
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert at_judge == [True]
    stored = _stored_findings(tmp_path)
    if scenario == "unchanged":
        assert at_persist == [False]
        assert [row.pair_ids for row in stored] == [("concepts/a", "concepts/b")]
    else:
        assert stored == ()


def test_a_busy_workspace_costs_the_findings_persist_an_advisory_not_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    acquired: list[bool] = []
    with contextlib.ExitStack() as stack:
        take = hold_the_lock_from(stack, tmp_path, acquired)
        _one_finding(monkeypatch, on_judge=take)

        result = runner.invoke(app, ["curate", "--auto"])

    assert acquired == [True]
    assert result.exit_code == 0, result.stderr
    assert "findings were not persisted" in result.stderr
    assert _stored_findings(tmp_path) == ()


# --- the end-of-run derived refresh and the verb's surface -----------------


def test_the_derived_refresh_runs_inside_a_commit_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _one_tier(monkeypatch)
    _answer(monkeypatch)
    at_refresh: list[bool] = []

    def _refresh(layout: object, cfg: object, **kwargs: object) -> bool:
        section = kwargs["commit_section"]
        assert callable(section)
        with section():
            at_refresh.append(lock_is_free(tmp_path))
        return True

    monkeypatch.setattr("openkos.cli.main._refresh_derived_after_write", _refresh)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert at_refresh == [False]


def test_curate_takes_wait_like_every_split_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plain_rich_output: None
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["curate", "--help"])

    assert result.exit_code == 0
    assert "--wait" in result.stdout
