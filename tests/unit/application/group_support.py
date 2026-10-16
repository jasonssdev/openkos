"""Shared builders for the direct tests of the group decision
(`identity-groups-decision`, ADR-0063): a real workspace of typed concepts, a
candidate group, a selection and a populated queue row.

Nothing here mocks the function under test; the effects the service does not
own arrive through recording ports in each test module.
"""

import contextlib
import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from openkos import config
from openkos.application import pending as application_pending
from openkos.application.curate_decisions import GroupSelection
from openkos.bundle import bundle
from openkos.model import okf
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.state import derived
from openkos.state import pending_queue as pq

A, B, C, D = (f"concepts/{name}" for name in "abcd")


def write_concept(
    layout: config.WorkspaceLayout,
    concept_id: str,
    okf_type: str,
    *,
    extra: dict[str, object] | None = None,
    body: str = "",
) -> None:
    path = layout.bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, object] = {
        "type": okf_type,
        "title": concept_id,
        **(extra or {}),
    }
    path.write_text(
        okf.dump_frontmatter(metadata, f"# {concept_id}\n\n{body or concept_id}\n"),
        encoding="utf-8",
    )


def make_workspace(
    tmp_path: Path, types: dict[str, str] | None = None
) -> tuple[Path, config.WorkspaceLayout]:
    """A workspace holding `types` (concept id to OKF type); by default A, B
    and C, all of one type."""
    root = tmp_path / "ws"
    root.mkdir()
    config.write_config(root)
    layout = config.WorkspaceLayout(root)
    bundle.create(layout.bundle_dir, date(2026, 1, 1))
    for concept_id, okf_type in (
        types or {A: "Concept", B: "Concept", C: "Concept"}
    ).items():
        write_concept(layout, concept_id, okf_type)
    return root, layout


def group_of(*ids: str) -> CandidateGroup:
    return CandidateGroup(
        okf_type="Concept", member_ids=tuple(sorted(ids)), tier=Tier.HIGH, trigger="t"
    )


def selection_of(survivor: str, *marked: str) -> GroupSelection:
    return GroupSelection(survivor_id=survivor, member_ids=marked)


def snapshot(root: Path) -> dict[Path, bytes]:
    """Every file under `root` and its bytes."""
    return {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _digests(
    layout: config.WorkspaceLayout, *concept_ids: str
) -> tuple[pq.InputDigest, ...]:
    current = application_pending.current_finding_digest(layout.bundle_dir)
    rows = [(concept_id, current(concept_id)) for concept_id in concept_ids]
    return tuple(pq.InputDigest(ref, digest) for ref, digest in rows if digest)


def identity_row(
    layout: config.WorkspaceLayout,
    members: tuple[str, ...],
    *,
    verdict: str | None = "same",
    member_types: list[str] | None = None,
) -> pq.Proposal:
    """An identity proposal over `members`, as a producer would enqueue it."""
    adjudication = (
        None
        if verdict is None
        else {"verdict": verdict, "confidence": 0.9, "rationale": "r"}
    )
    return pq.Proposal(
        kind="identity",
        key_body=pq.identity_key(members),
        producer="duplicates/1",
        payload=json.dumps(
            {
                "member_ids": list(members),
                "member_types": member_types or ["Concept"] * len(members),
                "okf_type": "Concept",
                "tier": "high",
                "trigger": "stub",
                "adjudication": adjudication,
            }
        ),
        targets=members,
        input_digests=_digests(layout, *members),
    )


@contextlib.contextmanager
def _unlocked() -> Iterator[None]:
    yield


def enqueue(layout: config.WorkspaceLayout, proposal: pq.Proposal) -> None:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn, proposal, commit_section=_unlocked, bundle_dir=layout.bundle_dir
        )
    finally:
        conn.close()
