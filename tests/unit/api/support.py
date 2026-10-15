"""Fixtures shared by the facade tests: a workspace with queue rows written
through the real queue API, and whole-tree byte snapshots."""

import contextlib
import json
from pathlib import Path

from openkos import config
from openkos.application import pending as application_pending
from openkos.application import queue_producers
from openkos.state import derived
from openkos.state import jobs as jobs_store
from openkos.state import pending_queue as pq

_SQLITE_SIDECARS = ("-wal", "-shm")


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under the workspace, derived stores and `.git` included.

    SQLite's `-wal` and `-shm` sidecars are housekeeping a read-only open of a
    WAL database may create; they hold no content, so a `-wal` file must be
    empty and neither is part of the comparison."""
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith(_SQLITE_SIDECARS):
            assert path.name.endswith("-shm") or path.stat().st_size == 0, (
                f"a read left content in {path.name}"
            )
            continue
        files[str(path.relative_to(root))] = path.read_bytes()
    return files


def enqueue(
    root: Path,
    *,
    kind: str,
    key_body: str,
    payload: dict[str, object],
    targets: tuple[str, ...],
    producer: str,
    fresh: bool = False,
) -> None:
    """Write one proposal through the real queue API. `fresh` records the
    targets' current bytes as the inputs the proposal was computed from, as a
    producer does, so the row is not reported as `row_inputs_changed`."""
    layout = config.WorkspaceLayout(root)
    digests: tuple[pq.InputDigest, ...] = ()
    if fresh:
        current = application_pending.current_finding_digest(layout.bundle_dir)
        digests = tuple(
            pq.InputDigest(ref, digest) for ref in targets if (digest := current(ref))
        )
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn,
            pq.Proposal(
                kind=kind,
                key_body=key_body,
                producer=producer,
                payload=json.dumps(payload, sort_keys=True),
                targets=targets,
                input_digests=digests,
            ),
            commit_section=contextlib.nullcontext,
            bundle_dir=layout.bundle_dir,
        )
    finally:
        conn.close()


def enqueue_identity(
    root: Path,
    members: tuple[str, str],
    *,
    verdict: str | None = None,
    rationale: str = "Same entity.",
    tier: str = "high",
    fresh: bool = False,
) -> None:
    adjudication = (
        None
        if verdict is None
        else {"verdict": verdict, "confidence": 0.91, "rationale": rationale}
    )
    enqueue(
        root,
        kind="identity",
        key_body=pq.identity_key(members),
        payload={
            "member_ids": list(members),
            "member_types": ["Concept", "Concept"],
            "okf_type": "Concept",
            "tier": tier,
            "trigger": "alpha",
            "adjudication": adjudication,
        },
        targets=members,
        producer=queue_producers.PRODUCER_IDENTITY,
        fresh=fresh,
    )


def enqueue_volatility(
    root: Path,
    type_name: str = "Person",
    suggested: str = "static",
    *,
    current_default: str = "slow",
) -> None:
    enqueue(
        root,
        kind="volatility",
        key_body=pq.volatility_key(type_name),
        payload={
            "type_name": type_name,
            "current_default": current_default,
            "suggested_tier": suggested,
            "rationale": "Names do not change.",
        },
        targets=(),
        producer=queue_producers.PRODUCER_VOLATILITY,
    )


def record_failed_job(root: Path, kind: str = "maintenance") -> None:
    layout = config.WorkspaceLayout(root)
    conn = jobs_store.open_jobs(layout.openkos_dir / "jobs.db")
    try:
        job_id = jobs_store.start_job(conn, kind, "2026-10-14T10:00:00Z")
        jobs_store.finish_job(
            conn,
            job_id,
            outcome="failed",
            ended_at="2026-10-14T10:01:00Z",
            chat_calls=0,
            units_done=0,
            units_deferred=3,
            detail_code="boom",
        )
    finally:
        conn.close()


def enqueue_relation(
    root: Path,
    source: str,
    target: str,
    *,
    suggested: str = "member_of",
    rationale: str = "A reason.",
    fresh: bool = False,
) -> None:
    enqueue(
        root,
        kind="relation_type",
        key_body=pq.relation_type_key(source, target),
        payload={
            "source_id": source,
            "target_id": target,
            "effective_source_id": source,
            "effective_target_id": target,
            "suggested_type": suggested,
            "rationale": rationale,
            "confidence": 0.8,
        },
        targets=(source, target),
        producer=queue_producers.PRODUCER_RELATIONS,
        fresh=fresh,
    )
