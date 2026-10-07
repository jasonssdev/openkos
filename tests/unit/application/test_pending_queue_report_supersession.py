"""The resolving command a supersession row names (#1212, #1224, ADR-0041)."""

import json
from pathlib import Path

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


def test_another_relation_row_names_the_command_that_reviews_it() -> None:
    row = _row(
        "relation_type",
        {
            "suggested_type": "works_at",
            "effective_source_id": "a",
            "effective_target_id": "b",
        },
        ("a", "b"),
    )

    assert report.row_resolving_command(row) == "openkos curate --structure"


# --- #1334 item 8: a cross-type identity row names a command that can act ---


def _concept(bundle: Path, concept_id: str, type_name: str) -> None:
    path = bundle / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: {type_name}\ntitle: {concept_id}\n---\n\nBody of {concept_id}.\n",
        encoding="utf-8",
    )


def test_a_cross_type_identity_row_names_the_merge_command_that_accepts_it(
    tmp_path: Path,
) -> None:
    """`adjudicate --apply` and `curate` refuse a cross-type SAME pair, so the
    row must not name them as the way to act on it."""
    _concept(tmp_path, "concepts/claude-code", "Concept")
    _concept(tmp_path, "procedures/installing-claude-code", "Procedure")
    row = _row(
        "identity",
        {"adjudication": {"verdict": "same"}},
        ("concepts/claude-code", "procedures/installing-claude-code"),
    )

    command = report.row_resolving_command(row, bundle_dir=tmp_path)

    assert command.startswith("openkos merge --include-cross-type ")
    assert "concepts/claude-code" in command
    assert "procedures/installing-claude-code" in command
    assert "adjudicate" not in command


def test_a_same_type_identity_row_keeps_the_adjudicate_command(
    tmp_path: Path,
) -> None:
    _concept(tmp_path, "concepts/a", "Concept")
    _concept(tmp_path, "concepts/b", "Concept")
    row = _row(
        "identity",
        {"adjudication": {"verdict": "same"}},
        ("concepts/a", "concepts/b"),
    )

    assert (
        report.row_resolving_command(row, bundle_dir=tmp_path)
        == "openkos adjudicate --apply"
    )
