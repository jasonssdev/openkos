"""Unit tests for the read-only row lookups of `openkos.application.curate_queue`.

`open_row_by_key` is the lookup the curation decision surface re-runs INSIDE
its commit section to check that the row a version token was issued for is
still the open one. It reads; it never writes or creates a store.
"""

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

from openkos import config
from openkos.application import curate_queue
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
