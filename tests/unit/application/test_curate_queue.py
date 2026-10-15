"""Unit tests for the read-only row lookups of `openkos.application.curate_queue`.

`open_row_by_key` is the lookup the curation decision surface re-runs INSIDE
its commit section to check that the row a version token was issued for is
still the open one. It reads; it never writes or creates a store.
"""

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import config
from openkos.application import curate_queue, queue_producers
from openkos.application import pending as application_pending
from openkos.model import okf
from openkos.resolution.adjudication import AdjudicatedCandidate, Verdict
from openkos.resolution.candidates import CandidateGroup, Tier
from openkos.state import derived
from openkos.state import pending_queue as pq


@contextlib.contextmanager
def _section() -> Iterator[None]:
    yield


def _layout(tmp_path: Path) -> config.WorkspaceLayout:
    (tmp_path / "bundle").mkdir(parents=True, exist_ok=True)
    return config.WorkspaceLayout(tmp_path)


def _identity_proposal(*members: str, note: str = "first") -> pq.Proposal:
    return pq.Proposal(
        kind="identity",
        key_body=pq.identity_key(members),
        producer="duplicates/1",
        payload=json.dumps({"member_ids": list(members), "note": note}),
        targets=tuple(members),
    )


def _enqueue(layout: config.WorkspaceLayout, proposal: pq.Proposal) -> None:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.ensure_schema(conn)
        pq.upsert_proposal(
            conn, proposal, commit_section=_section, bundle_dir=layout.bundle_dir
        )
    finally:
        conn.close()


def test_the_open_row_is_returned_by_its_decision_key(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    proposal = _identity_proposal("concepts/a", "concepts/b")
    _enqueue(layout, proposal)

    row = curate_queue.open_row_by_key(layout, proposal.decision_key)

    assert row is not None
    assert row.item.decision_key == proposal.decision_key
    assert row.payload["member_ids"] == ["concepts/a", "concepts/b"]
    assert row.item.payload_digest == pq.payload_digest_for(proposal)


def test_another_key_finds_nothing_even_when_a_row_of_the_kind_is_open(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    _enqueue(layout, _identity_proposal("concepts/a", "concepts/b"))
    other = _identity_proposal("concepts/c", "concepts/d")

    assert curate_queue.open_row_by_key(layout, other.decision_key) is None


def test_a_resolved_row_is_ignored(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    proposal = _identity_proposal("concepts/a", "concepts/b")
    _enqueue(layout, proposal)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        conn.execute(
            "UPDATE pending_items SET status = 'applied', resolution = 'as_proposed'"
        )
        conn.commit()
    finally:
        conn.close()

    assert curate_queue.open_row_by_key(layout, proposal.decision_key) is None


def test_the_lookup_reads_the_new_row_after_a_superseding_proposal(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    first = _identity_proposal("concepts/a", "concepts/b", note="first")
    second = _identity_proposal("concepts/a", "concepts/b", note="second")
    _enqueue(layout, first)
    _enqueue(layout, second)

    row = curate_queue.open_row_by_key(layout, first.decision_key)

    assert row is not None
    assert row.item.payload_digest == pq.payload_digest_for(second)
    assert row.item.payload_digest != pq.payload_digest_for(first)


def test_an_absent_queue_reads_as_no_row_and_creates_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    assert curate_queue.open_row_by_key(layout, "identity:whatever") is None
    assert not layout.findings_db_path.exists()


def test_a_key_without_a_kind_prefix_finds_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _enqueue(layout, _identity_proposal("concepts/a", "concepts/b"))

    assert curate_queue.open_row_by_key(layout, "no-colon-here") is None


# -- row_is_fresh: the public freshness predicate ----------------------------


def _open_row(
    layout: config.WorkspaceLayout, proposal: pq.Proposal
) -> curate_queue.OpenRow:
    _enqueue(layout, proposal)
    row = curate_queue.open_row_by_key(layout, proposal.decision_key)
    assert row is not None
    return row


def _with_digests(proposal: pq.Proposal, *digests: tuple[str, str]) -> pq.Proposal:
    return pq.Proposal(
        kind=proposal.kind,
        key_body=proposal.key_body,
        producer=proposal.producer,
        payload=proposal.payload,
        targets=proposal.targets,
        input_digests=tuple(pq.InputDigest(ref, digest) for ref, digest in digests),
    )


def _volatility_proposal() -> pq.Proposal:
    return pq.Proposal(
        kind="volatility",
        key_body=pq.volatility_key("Person"),
        producer="suggest-volatility/1",
        payload=json.dumps({"type_name": "Person", "current_default": "slow"}),
        targets=(),
    )


def _relation_proposal() -> pq.Proposal:
    return pq.Proposal(
        kind="relation_type",
        key_body=pq.relation_type_key("concepts/a", "concepts/b"),
        producer="suggest-relations/1",
        payload=json.dumps({"suggested_type": "related_to"}),
        targets=("concepts/a", "concepts/b"),
    )


def test_row_is_fresh_is_public_and_the_old_name_is_an_alias() -> None:
    assert curate_queue.row_is_fresh is curate_queue._fresh


def test_row_is_fresh_and_its_alias_agree_for_every_kind_the_list_reads(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    identity = _open_row(
        layout,
        _with_digests(
            _identity_proposal("concepts/a", "concepts/b"),
            ("concepts/a", "da"),
            ("concepts/b", "db"),
        ),
    )
    relation = _open_row(
        layout,
        _with_digests(_relation_proposal(), ("concepts/a", "da"), ("concepts/b", "db")),
    )
    volatility = _open_row(layout, _volatility_proposal())
    current = {"concepts/a": "da", "concepts/b": "db"}
    moved = {"concepts/a": "da", "concepts/b": "other"}

    cases = [
        (identity, current, identity.item.targets),
        (identity, moved, identity.item.targets),
        (identity, current, ("concepts/a", "concepts/never-recorded")),
        (relation, current, relation.item.targets),
        (relation, moved, relation.item.targets),
        (volatility, current, ()),
    ]
    for row, digests, required in cases:
        assert curate_queue.row_is_fresh(
            row, digests.get, required
        ) == curate_queue._fresh(row, digests.get, required)

    assert curate_queue.row_is_fresh(identity, current.get, identity.item.targets)
    assert not curate_queue.row_is_fresh(identity, moved.get, identity.item.targets)
    assert not curate_queue.row_is_fresh(
        identity, current.get, ("concepts/a", "concepts/never-recorded")
    )
    assert curate_queue.row_is_fresh(volatility, current.get, ())


# -- set_identity_verdicts: the amend-only write-through (judging, S2a) -------


def _write_concept(layout: config.WorkspaceLayout, concept_id: str, body: str) -> None:
    path = layout.bundle_dir / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": concept_id}, body),
        encoding="utf-8",
    )


_MEMBERS = ("concepts/a", "concepts/b")


def _group() -> CandidateGroup:
    return CandidateGroup(
        okf_type="Concept", member_ids=_MEMBERS, tier=Tier.HIGH, trigger="alpha"
    )


def _judged(verdict: Verdict) -> AdjudicatedCandidate:
    return AdjudicatedCandidate(_group(), verdict, 0.8, "Because.")


def _identity_layout(tmp_path: Path, *, seed_open_row: bool = True):  # type: ignore[no-untyped-def]
    layout = _layout(tmp_path)
    for member in _MEMBERS:
        _write_concept(layout, member, f"# {member}\n")
    if seed_open_row:
        proposal = queue_producers.identity_group_proposal(
            _group(),
            current_digest=application_pending.current_finding_digest(
                layout.bundle_dir
            ),
        )
        assert proposal is not None
        _enqueue(layout, proposal)
    return layout


def _identity_items(layout: config.WorkspaceLayout) -> list[pq.PendingItem]:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        return pq.all_items(conn)
    finally:
        conn.close()


def _stats(layout: config.WorkspaceLayout) -> list[pq.KindStats]:
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        return pq.kind_stats(conn)
    finally:
        conn.close()


def _set(
    layout: config.WorkspaceLayout,
    results: list[AdjudicatedCandidate],
    pinned: dict[str, str | None] | None = None,
) -> None:
    curate_queue.set_identity_verdicts(
        layout, results, pinned=pinned or {}, commit_section=_section
    )


@pytest.mark.parametrize("verdict", [Verdict.SAME, Verdict.UNCERTAIN])
def test_a_same_or_uncertain_verdict_amends_the_open_row_in_place(
    tmp_path: Path, verdict: Verdict
) -> None:
    layout = _identity_layout(tmp_path)
    (before,) = _identity_items(layout)
    stats = _stats(layout)

    _set(layout, [_judged(verdict)])

    (after,) = _identity_items(layout)
    assert (after.id, after.status, after.created_at) == (
        before.id,
        before.status,
        before.created_at,
    )
    assert json.loads(after.payload)["adjudication"] == {
        "verdict": verdict.value,
        "confidence": 0.8,
        "rationale": "Because.",
    }
    assert _stats(layout) == stats


def test_a_group_with_no_open_row_writes_nothing(tmp_path: Path) -> None:
    layout = _identity_layout(tmp_path)
    conn = derived.open_derived_connection(layout.findings_db_path)
    try:
        pq.resolve_open_by_key(
            conn,
            f"identity:{pq.identity_key(_MEMBERS)}",
            resolution=lambda _item: "declined",
            resolved_by="curate",
            commit_section=_section,
        )
    finally:
        conn.close()
    before = _identity_items(layout)

    _set(layout, [_judged(Verdict.SAME)])

    assert _identity_items(layout) == before


def test_an_absent_queue_is_never_created(tmp_path: Path) -> None:
    layout = _identity_layout(tmp_path, seed_open_row=False)

    _set(layout, [_judged(Verdict.SAME)])

    assert not layout.findings_db_path.exists()


def test_a_result_whose_member_moved_since_it_was_pinned_is_not_written(
    tmp_path: Path,
) -> None:
    layout = _identity_layout(tmp_path)
    before = _identity_items(layout)
    pinned: dict[str, str | None] = {"concepts/a": "digest-from-before-the-model-call"}

    _set(layout, [_judged(Verdict.SAME)], pinned)

    assert _identity_items(layout) == before


def test_a_different_verdict_never_touches_the_row(tmp_path: Path) -> None:
    layout = _identity_layout(tmp_path)
    before = _identity_items(layout)

    _set(layout, [_judged(Verdict.DIFFERENT)])

    assert _identity_items(layout) == before


def test_an_unreadable_queue_is_left_alone_without_raising(tmp_path: Path) -> None:
    layout = _identity_layout(tmp_path, seed_open_row=False)
    layout.findings_db_path.parent.mkdir(parents=True, exist_ok=True)
    layout.findings_db_path.write_bytes(b"not a database" * 100)

    _set(layout, [_judged(Verdict.SAME)])
