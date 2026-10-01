"""`next`'s queue-backed tiers 9 and 11 and the queue/job-backed tier 12
(next-action-pointer: "Queue-Backed Tiers Read The Queue Before Recomputing").

A queue that EXISTS is authoritative for tiers 9, 11 and 12: they read open
rows and do not recompute their advisor. A queue that is absent or unreadable
leaves tiers 9 and 11 exactly as they were and tier 12 silent. Reading never
creates a file, never constructs a backend, never writes.
"""

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos import config
from openkos.application import next_action
from openkos.bundle import decisions as bundle_decisions
from openkos.cli.main import app
from openkos.state import derived, findings, jobs
from openkos.state import pending_queue as pq

runner = CliRunner()


@pytest.fixture(autouse=True)
def _fts_index_present_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openkos.application.next_action.fts_index_present", lambda _path: True
    )


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> config.WorkspaceLayout:
    """A workspace whose every tier above 9 is clean: two documents, vectors
    present (the public seam reports a populated index)."""
    for key, value in (
        ("GIT_CONFIG_COUNT", "2"),
        ("GIT_CONFIG_KEY_0", "user.name"),
        ("GIT_CONFIG_VALUE_0", "openkos tests"),
        ("GIT_CONFIG_KEY_1", "user.email"),
        ("GIT_CONFIG_VALUE_1", "tests@openkos.invalid"),
    ):
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0
    monkeypatch.setattr(
        "openkos.application.next_action.vector_store_is_empty", lambda _path: False
    )
    for name in ("alpha", "beta"):
        path = tmp_path / "bundle" / "concepts" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: Concept\ntitle: {name.title()}\n---\nBody.\n",
            encoding="utf-8",
        )
    return config.WorkspaceLayout(tmp_path)


@contextlib.contextmanager
def _section() -> Iterator[None]:
    yield


def _queue(layout: config.WorkspaceLayout) -> sqlite3.Connection:
    conn = derived.open_derived_connection(layout.findings_db_path)
    pq.ensure_schema(conn)
    return conn


def _enqueue(
    layout: config.WorkspaceLayout,
    kind: str,
    targets: tuple[str, ...],
    payload: dict[str, object] | str | None = None,
) -> None:
    key = {
        "identity": lambda: pq.identity_key(targets),
        "relation_type": lambda: pq.relation_type_key(*targets[:2]),
        "contradiction": lambda: pq.contradiction_key((targets[0], targets[1]), None),
        "volatility": lambda: pq.volatility_key(targets[0]),
        "revision": lambda: pq.revision_key((targets[0], targets[1])),
        "watch_refusal": lambda: pq.watch_refusal_key(targets[0]),
    }[kind]()
    body = payload if isinstance(payload, str) else json.dumps(payload or {})
    conn = _queue(layout)
    try:
        pq.upsert_proposal(
            conn,
            pq.Proposal(
                kind=kind,
                key_body=key,
                producer="test/1",
                payload=body,
                targets=targets,
            ),
            commit_section=_section,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()


def _persist_finding(conn: sqlite3.Connection) -> None:
    findings.record_findings(
        conn,
        [
            findings.Finding(
                pair_ids=("concepts/alpha", "concepts/beta"),
                merged_absorbed_id=None,
                verdict="contradicts",
                confidence=0.91,
                rationale="r",
                input_digests=(),
            )
        ],
    )


def _job(
    layout: config.WorkspaceLayout,
    kind: str,
    outcome: str,
    *,
    started: str,
    deferred: int = 0,
) -> None:
    conn = jobs.open_jobs(layout.openkos_dir / "jobs.db")
    try:
        job_id = jobs.start_job(conn, kind, started)
        jobs.finish_job(
            conn,
            job_id,
            outcome=outcome,
            ended_at=started,
            chat_calls=0,
            units_done=0,
            units_deferred=deferred,
        )
    finally:
        conn.close()


def _exact_title_payload(members: tuple[str, ...]) -> dict[str, object]:
    return {
        "member_ids": list(members),
        "tier": "high",
        "okf_type": "Concept",
        "trigger": "alpha",
    }


# -- no queue: tiers 9 and 11 behave as before the queue existed -----------------


def test_no_queue_keeps_the_recompute_contradiction_tier(
    layout: config.WorkspaceLayout,
) -> None:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        _persist_finding(conn)
    finally:
        conn.close()
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos contradictions"


def test_no_queue_and_no_jobs_fires_nothing_and_creates_nothing(
    layout: config.WorkspaceLayout,
) -> None:
    result = next_action.next_action(layout)
    assert result.action is None
    assert not layout.findings_db_path.exists()
    assert not (layout.openkos_dir / "jobs.db").exists()


def test_no_queue_keeps_the_recompute_duplicate_tier(
    layout: config.WorkspaceLayout,
) -> None:
    (layout.bundle_dir / "concepts" / "alpha-two.md").write_text(
        "---\ntype: Concept\ntitle: Alpha\n---\nBody.\n", encoding="utf-8"
    )
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos curate"


# -- empty queue: authoritative, nothing is recomputed ------------------------


def test_an_empty_queue_is_authoritative_over_stale_findings(
    layout: config.WorkspaceLayout,
) -> None:
    conn = _queue(layout)
    _persist_finding(conn)
    conn.close()
    assert next_action.next_action(layout).action is None


def test_an_empty_queue_does_not_walk_for_duplicates(
    layout: config.WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    _queue(layout).close()

    def _boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("the duplicate walk must not run over a live queue")

    monkeypatch.setattr(next_action, "find_exact_title_groups", _boom)
    assert next_action.next_action(layout).action is None


# -- tier 9 --------------------------------------------------------------------


def test_an_open_identity_row_is_ranked_without_a_candidate_walk(
    layout: config.WorkspaceLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    members = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "identity", members, _exact_title_payload(members))

    def _boom(*_a: object, **_k: object) -> list[object]:
        raise AssertionError("the duplicate walk must not run over a live queue")

    monkeypatch.setattr(next_action, "find_exact_title_groups", _boom)
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos curate"
    assert "1 candidate group with identical titles is pending review" in (
        result.action.reason
    )


def test_two_identity_rows_are_counted(layout: config.WorkspaceLayout) -> None:
    for members in (
        ("concepts/alpha", "concepts/beta"),
        ("concepts/gamma", "concepts/delta"),
    ):
        _enqueue(layout, "identity", members, _exact_title_payload(members))
    result = next_action.next_action(layout)
    assert result.action is not None
    assert "2 candidate groups with identical titles are pending review" in (
        result.action.reason
    )


def test_a_resolved_identity_row_does_not_fire_tier_9(
    layout: config.WorkspaceLayout,
) -> None:
    members = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "identity", members, _exact_title_payload(members))
    conn = _queue(layout)
    conn.execute("UPDATE pending_items SET status='applied', resolution='as_proposed'")
    conn.commit()
    conn.close()
    assert next_action.next_action(layout).action is None


def test_a_kept_distinct_identity_row_is_excluded(
    layout: config.WorkspaceLayout,
) -> None:
    members = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "identity", members, _exact_title_payload(members))
    bundle_decisions.write_identity_decisions(
        members[0],
        layout.bundle_dir,
        records=[
            bundle_decisions.IdentityDecisionRecord(
                decision_key=bundle_decisions.identity_decision_key_for(members),
                member_ids=members,
                state="declined",
                decided_at="2026-01-01T00:00:00+00:00",
            )
        ],
    )
    assert next_action.next_action(layout).action is None


def test_a_non_exact_title_identity_row_is_left_to_tier_12(
    layout: config.WorkspaceLayout,
) -> None:
    members = ("concepts/alpha", "concepts/beta")
    _enqueue(
        layout,
        "identity",
        members,
        {"member_ids": list(members), "tier": "low", "trigger": "0.812"},
    )
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos pending"
    assert "1 identity" in result.action.reason


# -- tier 11 -------------------------------------------------------------------


def _contradiction_payload(confidence: float) -> dict[str, object]:
    return {
        "pair_ids": ["concepts/alpha", "concepts/beta"],
        "merged_absorbed_id": None,
        "confidence": confidence,
        "rationale": "SENTINEL-RATIONALE",
        "conflicting_claims": [],
    }


def test_an_open_contradiction_row_is_ranked_from_the_queue(
    layout: config.WorkspaceLayout,
) -> None:
    pair = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "contradiction", pair, _contradiction_payload(0.91))
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos contradictions"
    assert result.action.reason == (
        "concepts/alpha <-> concepts/beta: an open contradiction finding "
        "is pending review (confidence: 0.91)."
    )
    assert result.action.subjects == pair
    assert "SENTINEL-RATIONALE" not in result.action.reason


def test_a_low_confidence_contradiction_row_is_not_ranked(
    layout: config.WorkspaceLayout,
) -> None:
    pair = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "contradiction", pair, _contradiction_payload(0.1))
    assert next_action.next_action(layout).action is None


def test_a_declined_contradiction_row_is_not_ranked(
    layout: config.WorkspaceLayout,
) -> None:
    pair = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "contradiction", pair, _contradiction_payload(0.91))
    bundle_decisions.write_decisions(
        pair[0],
        layout.bundle_dir,
        records=[
            bundle_decisions.DecisionRecord(
                decision_key=bundle_decisions.decision_key_for(pair, None),
                pair_ids=pair,
                merged_absorbed_id=None,
                state="declined",
                decided_at="2026-01-01T00:00:00+00:00",
            )
        ],
    )
    assert next_action.next_action(layout).action is None


def test_a_contradiction_row_with_a_malformed_payload_is_skipped_not_fatal(
    layout: config.WorkspaceLayout,
) -> None:
    pair = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "contradiction", pair, "not json {")
    assert next_action.next_action(layout).action is None


# -- tier 12: rows -------------------------------------------------------------


def test_a_watch_refusal_row_points_at_pending_and_names_the_source(
    layout: config.WorkspaceLayout,
) -> None:
    _enqueue(layout, "watch_refusal", ("sources/refused-note",))
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos pending"
    assert "sources/refused-note" in result.action.reason
    assert result.action.subjects == ("sources/refused-note",)


@pytest.mark.parametrize(
    ("kind", "targets"),
    [
        ("relation_type", ("concepts/alpha", "concepts/beta")),
        ("volatility", ("Person",)),
        ("revision", ("concepts/alpha", "concepts/beta")),
    ],
)
def test_each_unranked_row_kind_points_at_pending(
    layout: config.WorkspaceLayout, kind: str, targets: tuple[str, ...]
) -> None:
    _enqueue(layout, kind, targets)
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos pending"
    assert f"1 {kind}" in result.action.reason
    assert result.action.subjects == ()


def test_tier_12_counts_rows_by_kind(layout: config.WorkspaceLayout) -> None:
    _enqueue(layout, "revision", ("concepts/alpha", "concepts/beta"))
    _enqueue(layout, "volatility", ("Person",))
    _enqueue(layout, "volatility", ("Event",))
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.reason.startswith(
        "3 open pending-work rows (2 volatility, 1 revision)"
    )


def test_tier_11_outranks_tier_12(layout: config.WorkspaceLayout) -> None:
    pair = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "revision", pair)
    _enqueue(layout, "contradiction", pair, _contradiction_payload(0.91))
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos contradictions"


def test_tier_9_outranks_tier_12(layout: config.WorkspaceLayout) -> None:
    members = ("concepts/alpha", "concepts/beta")
    _enqueue(layout, "revision", members)
    _enqueue(layout, "identity", members, _exact_title_payload(members))
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos curate"


# -- tier 12: unattended job outcomes -----------------------------------------


@pytest.mark.parametrize(
    "outcome", ["budget_exhausted", "timed_out", "commit_failed", "failed"]
)
def test_an_attention_outcome_points_at_pending(
    layout: config.WorkspaceLayout, outcome: str
) -> None:
    _job(layout, "watch", outcome, started="2026-01-02T00:00:00+00:00", deferred=3)
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos pending"
    assert "watch" in result.action.reason
    assert outcome in result.action.reason
    assert "3" in result.action.reason
    assert result.action.subjects == ()


@pytest.mark.parametrize("outcome", ["completed", "stopped", "busy", "refused"])
def test_a_benign_outcome_does_not_fire(
    layout: config.WorkspaceLayout, outcome: str
) -> None:
    _job(layout, "maintenance", outcome, started="2026-01-02T00:00:00+00:00")
    assert next_action.next_action(layout).action is None


def test_only_the_most_recent_job_counts(layout: config.WorkspaceLayout) -> None:
    _job(layout, "watch", "failed", started="2026-01-01T00:00:00+00:00")
    _job(layout, "maintenance", "completed", started="2026-01-02T00:00:00+00:00")
    assert next_action.next_action(layout).action is None


def test_a_job_outcome_works_without_a_queue(layout: config.WorkspaceLayout) -> None:
    _job(layout, "watch", "budget_exhausted", started="2026-01-02T00:00:00+00:00")
    assert not layout.findings_db_path.exists()
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos pending"


# -- degradation ----------------------------------------------------------------


def test_an_unreadable_queue_falls_back_to_recompute(
    layout: config.WorkspaceLayout,
) -> None:
    conn = derived.open_derived_connection(layout.findings_db_path)
    _persist_finding(conn)
    conn.execute("CREATE TABLE pending_items (unrelated TEXT)")
    conn.commit()
    conn.close()
    result = next_action.next_action(layout)
    assert result.action is not None
    assert result.action.command == "openkos contradictions"


def test_an_unreadable_job_record_fires_nothing_and_does_not_raise(
    layout: config.WorkspaceLayout,
) -> None:
    layout.openkos_dir.mkdir(exist_ok=True)
    (layout.openkos_dir / "jobs.db").write_bytes(b"not a database" * 100)
    assert next_action.next_action(layout).action is None


# -- read-only ------------------------------------------------------------------


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and ".git/" not in p.as_posix()
        and not p.name.endswith(("-wal", "-shm"))
    }


def test_reading_the_queue_and_jobs_writes_nothing(
    layout: config.WorkspaceLayout,
) -> None:
    _enqueue(layout, "revision", ("concepts/alpha", "concepts/beta"))
    _job(layout, "watch", "failed", started="2026-01-02T00:00:00+00:00")
    before = _tree_digest(layout.root)
    next_action.next_action(layout)
    assert _tree_digest(layout.root) == before


def test_rendering_appends_the_status_pointer_after_a_queue_action(
    layout: config.WorkspaceLayout,
) -> None:
    _enqueue(layout, "watch_refusal", ("sources/refused-note",))
    lines = next_action.render_lines(next_action.next_action(layout))
    assert lines[0] == "Run: openkos pending"
    assert lines[-1] == "For everything else, run `openkos status`."
