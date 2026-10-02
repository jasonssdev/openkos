"""The resolving command a supersession row names (#1212, #1224, ADR-0041)."""

import json

from openkos.application import pending_queue_report as report
from openkos.state import pending_queue as pq


def _row(
    kind: str, payload: dict[str, object], targets: tuple[str, ...]
) -> pq.PendingItem:
    return pq.PendingItem(
        id=1,
        decision_key=f"{kind}:k",
        kind=kind,
        producer="p",
        payload=json.dumps(payload),
        payload_digest="d",
        status="pending",
        claimed_by=None,
        created_at="2026-01-01T00:00:00+00:00",
        last_seen_at="2026-01-01T00:00:00+00:00",
        targets=targets,
    )


def test_a_supersession_row_names_the_exact_relate_command() -> None:
    row = _row(
        "relation_type",
        {
            "suggested_type": "supersedes",
            "effective_source_id": "sources/a-2",
            "effective_target_id": "sources/a",
        },
        ("sources/a-2", "sources/a"),
    )

    assert (
        report.row_resolving_command(row)
        == "openkos relate sources/a-2 supersedes sources/a"
    )


def test_another_relation_row_keeps_the_curate_command() -> None:
    row = _row(
        "relation_type",
        {
            "suggested_type": "works_at",
            "effective_source_id": "a",
            "effective_target_id": "b",
        },
        ("a", "b"),
    )

    assert report.row_resolving_command(row) == "openkos curate"
