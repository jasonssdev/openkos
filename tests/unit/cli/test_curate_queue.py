"""`curate` reads and resolves the pending-work queue (mvp4 unit 4.6, curate-
command "Curate Reads And Resolves The Pending Queue").

When the queue exists, each stage serves the open rows of its kind (a fresh
row answers what the model would be asked, at no spend), enqueues what it
computed that holds no open row, and leaves a row it presented but did not
write or decline `pending`. Resolution itself happens in the shared write cores
(`application.queue_resolution`), so these tests pin that curate reaches them
and does not resolve twice. Absent queue: byte-identical to before.

The last group pins the catalog: `index.md`/`log.md` are re-composed over their
current bytes at commit time, so a concurrent append is kept, not refused.
"""

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import config as okf_config
from openkos.application import lifecycle as application_lifecycle
from openkos.application import pending as application_pending
from openkos.cli.main import app
from openkos.graph.base import Edge
from openkos.model import types
from openkos.resolution.adjudication import (
    AdjudicationBatch,
)
from openkos.resolution.candidates import CandidateGroup
from openkos.resolution.edge_typing import EdgeSuggestionBatch
from openkos.resolution.volatility_typing import TierSuggestionBatch
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.cli.commit_phase_support import runner
from tests.unit.cli.test_curate_lock import (
    _answer,
    _git_identity,  # noqa: F401 -- autouse fixture, re-exported for this module
    _one_edge,
    _one_finding,
    _one_tier,
    _same_group,
    _workspace,
    _write,
)
from tests.unit.conftest import LOCAL_BACKEND_LOCALITY

_SENTINEL_RATIONALE = "SENTINEL-ROW-RATIONALE-4c1e"


@contextlib.contextmanager
def _section() -> Iterator[None]:
    yield


def _layout(root: Path) -> okf_config.WorkspaceLayout:
    return okf_config.WorkspaceLayout(root)


def _enqueue(root: Path, proposal: pq.Proposal) -> None:
    layout = _layout(root)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn, proposal, commit_section=_section, bundle_dir=layout.bundle_dir
        )
    finally:
        conn.close()


def _digests(root: Path, *concept_ids: str) -> tuple[pq.InputDigest, ...]:
    digest_of = application_pending.current_finding_digest(_layout(root).bundle_dir)
    rows = []
    for concept_id in concept_ids:
        digest = digest_of(concept_id)
        assert digest is not None
        rows.append(pq.InputDigest(concept_id, digest))
    return tuple(rows)


def _relation_row(
    root: Path, *, rationale: str = "row rationale", suggested: str = "references"
) -> None:
    _enqueue(
        root,
        pq.Proposal(
            kind="relation_type",
            key_body=pq.relation_type_key("concepts/a", "concepts/b"),
            producer="suggest-relations/1",
            payload=json.dumps(
                {
                    "source_id": "concepts/a",
                    "target_id": "concepts/b",
                    "effective_source_id": "concepts/a",
                    "effective_target_id": "concepts/b",
                    "suggested_type": suggested,
                    "rationale": rationale,
                }
            ),
            targets=("concepts/a", "concepts/b"),
            input_digests=_digests(root, "concepts/a", "concepts/b"),
        ),
    )


def _identity_row(root: Path, *, verdict: str | None = "same") -> None:
    _enqueue(
        root,
        pq.Proposal(
            kind="identity",
            key_body=pq.identity_key(("concepts/a", "concepts/b")),
            producer="duplicates/1",
            payload=json.dumps(
                {
                    "member_ids": ["concepts/a", "concepts/b"],
                    "member_types": ["Concept", "Concept"],
                    "okf_type": "Concept",
                    "tier": "high",
                    "trigger": "stub",
                    "adjudication": None
                    if verdict is None
                    else {"verdict": verdict, "confidence": 0.9, "rationale": "r"},
                }
            ),
            targets=("concepts/a", "concepts/b"),
            input_digests=_digests(root, "concepts/a", "concepts/b"),
        ),
    )


def _volatility_row(root: Path) -> None:
    _enqueue(
        root,
        pq.Proposal(
            kind="volatility",
            key_body=pq.volatility_key("Concept"),
            producer="suggest-volatility/1",
            payload=json.dumps(
                {
                    "type_name": "Concept",
                    "current_default": types.TYPE_TO_DEFAULT_VOLATILITY.get(
                        "Concept", ""
                    ),
                    "suggested_tier": "volatile",
                    "rationale": "row rationale",
                }
            ),
            targets=(),
        ),
    )


def _rows(root: Path, kind: str) -> list[tuple[str, str | None]]:
    conn = sqlite3.connect(_layout(root).findings_db_path)
    try:
        return [
            (status, resolution)
            for status, resolution in conn.execute(
                "SELECT status, resolution FROM pending_items WHERE kind = ?"
                " ORDER BY id",
                (kind,),
            )
        ]
    finally:
        conn.close()


def _payloads(root: Path, kind: str) -> list[dict[str, object]]:
    conn = sqlite3.connect(_layout(root).findings_db_path)
    try:
        return [
            json.loads(payload)
            for (payload,) in conn.execute(
                "SELECT payload FROM pending_items WHERE kind = ? ORDER BY id",
                (kind,),
            )
        ]
    finally:
        conn.close()


def _no_model_for_edges(monkeypatch: pytest.MonkeyPatch) -> list[list[Edge]]:
    """Replace the edge typer with one that records what it was asked to type."""
    asked: list[list[Edge]] = []

    def _suggest(edges: list[Edge], **kwargs: object) -> EdgeSuggestionBatch:
        asked.append(list(edges))
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.curate.suggest_edge_types", _suggest)
    return asked


# --- absent queue: today's behaviour ----------------------------------------


def test_an_absent_queue_changes_nothing_and_is_not_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _one_edge(monkeypatch)
    _answer(monkeypatch)

    result = runner.invoke(app, ["curate", "--structure", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Structure: applied 1, skipped 0." in result.stdout.splitlines()
    db = _layout(tmp_path).findings_db_path
    if db.exists():
        conn = sqlite3.connect(db)
        try:
            assert not pq.queue_exists(conn)
        finally:
            conn.close()


# --- Structure --------------------------------------------------------------


def test_an_open_relation_row_is_served_without_a_model_call_and_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _relation_row(tmp_path)
    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])
    asked = _no_model_for_edges(monkeypatch)
    _answer(monkeypatch)

    result = runner.invoke(app, ["curate", "--structure", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Structure: applied 1, skipped 0." in result.stdout.splitlines()
    assert "[references] concepts/a -> concepts/b" in result.stdout
    assert "rationale: row rationale" in result.stdout
    assert asked == [[]]
    assert "references" in (tmp_path / "bundle/concepts/a.md").read_text("utf-8")
    assert _rows(tmp_path, "relation_type") == [("applied", "as_proposed")]


def test_a_row_naming_an_edited_endpoint_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _relation_row(tmp_path, rationale=_SENTINEL_RATIONALE)
    _write(tmp_path, "concepts/b", body="Edited after the row was computed.")
    _one_edge(monkeypatch)
    asked: list[list[Edge]] = []
    real = "openkos.cli.curate.suggest_edge_types"

    def _spy(edges: list[Edge], **kwargs: object) -> EdgeSuggestionBatch:
        asked.append(list(edges))
        return EdgeSuggestionBatch(results=[])

    monkeypatch.setattr(real, _spy)
    _answer(monkeypatch)

    result = runner.invoke(app, ["curate", "--structure", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert [len(batch) for batch in asked] == [1]
    assert _SENTINEL_RATIONALE not in result.stdout + result.stderr


def test_a_row_for_an_edge_that_is_not_a_candidate_is_never_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows serve only for the candidates this run computed under its own
    sensitivity filter, so a row naming an endpoint the run excludes is
    neither presented nor written."""
    _workspace(tmp_path, monkeypatch)
    _relation_row(tmp_path, rationale=_SENTINEL_RATIONALE)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [])
    _no_model_for_edges(monkeypatch)
    _answer(monkeypatch)
    before = (tmp_path / "bundle/concepts/a.md").read_bytes()

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert _SENTINEL_RATIONALE not in result.stdout + result.stderr
    assert (tmp_path / "bundle/concepts/a.md").read_bytes() == before
    assert _rows(tmp_path, "relation_type") == [("pending", None)]


def test_a_presented_and_declined_relation_returns_to_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _relation_row(tmp_path)
    edge = Edge(source_id="concepts/a", target_id="concepts/b", relation_type=None)
    monkeypatch.setattr("openkos.cli.curate.candidate_edges", lambda *a, **k: [edge])
    _no_model_for_edges(monkeypatch)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "n")

    result = runner.invoke(app, ["curate", "--structure", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Structure: applied 0, skipped 1." in result.stdout.splitlines()
    assert _rows(tmp_path, "relation_type") == [("pending", None)]


def test_a_relation_computed_with_no_open_row_is_enqueued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _volatility_row(tmp_path)  # the queue exists, with no relation_type row
    _one_edge(monkeypatch)
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "n")

    result = runner.invoke(app, ["curate", "--structure", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert _rows(tmp_path, "relation_type") == [("pending", None)]
    (payload,) = _payloads(tmp_path, "relation_type")
    assert payload["suggested_type"] == "references"
    assert payload["source_id"] == "concepts/a"
    assert payload["target_id"] == "concepts/b"


def test_a_suggestion_about_content_edited_while_typing_is_not_enqueued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typing holds no lock: a row digested after an endpoint changed would
    carry a verdict about content nobody typed."""
    _workspace(tmp_path, monkeypatch)
    _volatility_row(tmp_path)
    _one_edge(
        monkeypatch,
        on_type=lambda: _write(tmp_path, "concepts/b", body="Rewritten while typing."),
    )
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "n")

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert _rows(tmp_path, "relation_type") == []


# --- Identity ---------------------------------------------------------------


def test_an_adjudicated_identity_row_is_served_and_a_merge_resolves_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _identity_row(tmp_path, verdict="same")
    group = _same_group(monkeypatch)
    judged: list[list[CandidateGroup]] = []

    def _judge(candidates: list[CandidateGroup], **kwargs: object) -> AdjudicationBatch:
        judged.append(list(candidates))
        return AdjudicationBatch(results=[])

    monkeypatch.setattr("openkos.cli.curate.adjudicate_candidates", _judge)
    _answer(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Identity: applied 1, skipped 0." in result.stdout.splitlines()
    assert judged == [[]], group
    assert _rows(tmp_path, "identity") == [("applied", "as_proposed")]


def test_a_keep_distinct_answer_resolves_the_identity_row_declined_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _identity_row(tmp_path, verdict="same")
    _same_group(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(results=[]),
    )
    monkeypatch.setattr(
        "typer.prompt",
        lambda text, *a, **k: "d" if str(text).startswith("Merge") else "n",
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert _rows(tmp_path, "identity") == [("declined", "declined")]


def test_a_skipped_identity_group_leaves_its_row_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1264: `s` (and `n`, Enter) is "not now" -- no ruling, so the row the
    stage presented returns to `pending` and the next run offers it again."""
    _workspace(tmp_path, monkeypatch)
    _identity_row(tmp_path, verdict="same")
    _same_group(monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.curate.adjudicate_candidates",
        lambda *a, **k: AdjudicationBatch(results=[]),
    )
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "s")

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert [status for status, _ in _rows(tmp_path, "identity")] == ["pending"]


def test_an_identity_group_judged_with_no_open_row_is_enqueued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _volatility_row(tmp_path)
    _same_group(monkeypatch)
    # Refusing the merge keeps the group in place; the keep-distinct ruling then
    # resolves the row the stage enqueued, so the row proves the enqueue ran.
    monkeypatch.setattr(
        "typer.prompt",
        lambda text, *a, **k: "d" if str(text).startswith("Merge") else "n",
    )

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert _rows(tmp_path, "identity") == [("declined", "declined")]
    (payload,) = _payloads(tmp_path, "identity")
    assert payload["member_ids"] == ["concepts/a", "concepts/b"]
    assert payload["adjudication"] == {
        "verdict": "same",
        "confidence": 0.9,
        "rationale": "same",
    }


# --- Metadata ---------------------------------------------------------------


def test_an_open_volatility_row_is_served_and_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _volatility_row(tmp_path)
    _one_tier(monkeypatch)
    asked: list[object] = []

    def _suggest(bundle_dir: Path, **kwargs: object) -> TierSuggestionBatch:
        asked.append(kwargs.get("skip_types"))
        return TierSuggestionBatch(results=[])

    monkeypatch.setattr("openkos.cli.curate.suggest_volatility", _suggest)
    _answer(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert "Metadata: applied 1, skipped 0." in result.stdout.splitlines()
    assert "[volatile] Concept" in result.stdout
    assert "rationale: row rationale" in result.stdout
    assert asked == [frozenset({"Concept"})]
    assert _rows(tmp_path, "volatility") == [("applied", "as_proposed")]


# --- Contradictions ---------------------------------------------------------


def test_a_contradiction_is_enqueued_and_never_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    _volatility_row(tmp_path)
    _one_finding(monkeypatch)
    before = {
        p: p.read_bytes() for p in (tmp_path / "bundle").rglob("*.md") if p.is_file()
    }

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert _rows(tmp_path, "contradiction") == [("pending", None)]
    (payload,) = _payloads(tmp_path, "contradiction")
    assert payload["pair_ids"] == ["concepts/a", "concepts/b"]
    assert payload["conflicting_claims"] == ["claim one"]
    after = {
        p: p.read_bytes() for p in (tmp_path / "bundle").rglob("*.md") if p.is_file()
    }
    assert after == before


# --- the catalog is re-composed, not guarded --------------------------------


def test_a_log_append_between_the_relate_plan_and_its_commit_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Superseded pin: the relate write used to refuse (exit 3) when `log.md`
    moved after the plan was composed; its entry is now re-applied over the
    current log."""
    _workspace(tmp_path, monkeypatch)
    _one_edge(monkeypatch)
    _answer(monkeypatch)
    log = tmp_path / "bundle" / "log.md"
    concurrent = "\n## [2099-01-01] concurrent | another verb's entry\n"
    real = application_lifecycle.prepare_relate

    def _prepare_then_a_concurrent_append(*args: object, **kwargs: object) -> object:
        prepared = real(*args, **kwargs)  # type: ignore[arg-type]
        log.write_text(log.read_text("utf-8") + concurrent, encoding="utf-8")
        return prepared

    monkeypatch.setattr(
        application_lifecycle, "prepare_relate", _prepare_then_a_concurrent_append
    )

    result = runner.invoke(app, ["curate", "--structure", "--auto"])

    assert result.exit_code == 0, result.stderr
    text = log.read_text("utf-8")
    assert concurrent.strip() in text
    assert "relate" in text.lower()
    assert "Structure: applied 1, skipped 0." in result.stdout.splitlines()


def test_an_index_and_log_append_while_the_merge_prompt_waits_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Superseded pin: the merge used to refuse (exit 3) when `index.md` or
    `log.md` moved during the prompt."""
    _workspace(tmp_path, monkeypatch)
    _same_group(monkeypatch)
    log = tmp_path / "bundle" / "log.md"
    index = tmp_path / "bundle" / "index.md"
    concurrent_log = "\n## [2099-01-01] concurrent | another verb's entry\n"

    def _concurrent_append() -> None:
        log.write_text(log.read_text("utf-8") + concurrent_log, encoding="utf-8")
        index.write_text(
            index.read_text("utf-8") + "\n- concurrent index line\n", encoding="utf-8"
        )

    _answer(monkeypatch, on_prompt=_concurrent_append)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert concurrent_log.strip() in log.read_text("utf-8")
    assert "concurrent index line" in index.read_text("utf-8")
    assert "Identity: applied 1, skipped 0." in result.stdout.splitlines()
    assert not (tmp_path / "bundle" / "concepts" / "a.md").exists()


def test_a_curate_run_tolerates_an_unreadable_queue_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The queue is a derived cache: a corrupt `findings.db` costs the serve,
    never the run."""
    _workspace(tmp_path, monkeypatch)
    _one_edge(monkeypatch)
    _answer(monkeypatch)
    db = _layout(tmp_path).findings_db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"not a sqlite database" * 50)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code in (0, 1), result.stderr
    assert "Traceback" not in result.stderr


# --- Metadata: the answered-question cache (#1332) --------------------------


class _TierModel:
    """Replies with the type's own default tier, so nothing earns a queue row:
    only the cache can stop the next run asking."""

    locality = LOCAL_BACKEND_LOCALITY

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: object) -> str:
        self.calls += 1
        default = types.TYPE_TO_DEFAULT_VOLATILITY["Concept"]
        return f'{{"tier": "{default}", "rationale": "keep it"}}'

    def embed(self, texts: "list[str]") -> "list[list[float]]":
        return [[1.0] + [0.0] * 7 for _ in texts]


def test_a_no_change_answer_is_not_asked_again_by_the_next_curate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    model = _TierModel()
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: model)
    _answer(monkeypatch)

    first = runner.invoke(app, ["curate", "--auto"])
    asked_first = model.calls
    second = runner.invoke(app, ["curate", "--auto"])

    assert first.exit_code == 0, first.stderr
    assert second.exit_code == 0, second.stderr
    assert asked_first == 1
    assert model.calls == 1


class _VolatileModel(_TierModel):
    def chat(self, messages: object) -> str:
        self.calls += 1
        return '{"tier": "volatile", "rationale": "churns"}'


def test_curate_does_not_propose_the_tier_the_workspace_already_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concept's registry default is `slow`; the workspace maps it to
    `volatile`, and the model agrees. That is settled: no proposal."""
    _workspace(tmp_path, monkeypatch)
    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\ntype_tiers:\n  Concept: volatile\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "openkos.cli.curate._concept_type_names", lambda *a, **k: ["Concept"]
    )
    model = _VolatileModel()
    monkeypatch.setattr("openkos.cli.main.OllamaClient", lambda *a, **k: model)
    _answer(monkeypatch)

    result = runner.invoke(app, ["curate", "--auto"])

    assert result.exit_code == 0, result.stderr
    assert model.calls == 1
    assert "[volatile] Concept" not in result.stdout
    assert "Metadata: applied 0, skipped 0." in result.stdout.splitlines()


def test_a_volatility_row_is_stale_once_the_effective_tier_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row was computed against Concept's registry default; once the
    workspace maps Concept elsewhere the row answers a question nobody asks."""
    from openkos.application import curate_queue

    _workspace(tmp_path, monkeypatch)
    _volatility_row(tmp_path)
    layout = _layout(tmp_path)
    assert set(curate_queue.volatility_suggestions(layout)) == {"Concept"}

    config_path = tmp_path / "openkos.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\ntype_tiers:\n  Concept: static\n",
        encoding="utf-8",
    )

    assert curate_queue.volatility_suggestions(layout) == {}
