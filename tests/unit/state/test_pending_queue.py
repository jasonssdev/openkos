"""`state.pending_queue`: the pending-work queue, a fifth tenant of
`.openkos/findings.db` (#1141, ADR-0037)."""

import contextlib
import json
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from openkos.bundle import decisions as bundle_decisions
from openkos.state import derived
from openkos.state import pending_queue as pq

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _Section:
    """A CommitSection that records how often the commit phase was entered."""

    def __init__(self) -> None:
        self.entered = 0

    def __call__(self) -> contextlib.AbstractContextManager[None]:
        @contextlib.contextmanager
        def cm() -> Iterator[None]:
            self.entered += 1
            yield

        return cm()


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bundle"
    path.mkdir()
    return path


@pytest.fixture
def section() -> _Section:
    return _Section()


def _proposal(
    *,
    kind: str = "relation_type",
    key_body: str | None = None,
    payload: str = '{"type":"works_at"}',
    targets: tuple[str, ...] = ("concepts/a", "concepts/b"),
    digests: tuple[tuple[str, str], ...] = (("concepts/a", "sha-a"),),
) -> pq.Proposal:
    return pq.Proposal(
        kind=kind,
        key_body=key_body or pq.relation_type_key(*targets[:2]),
        producer="suggest-relations/1",
        payload=payload,
        targets=targets,
        input_digests=tuple(pq.InputDigest(r, d) for r, d in digests),
    )


def _upsert(
    conn: sqlite3.Connection,
    proposal: pq.Proposal,
    section: _Section,
    bundle_dir: Path,
    minutes: int = 0,
) -> pq.UpsertOutcome:
    return pq.upsert_proposal(
        conn,
        proposal,
        commit_section=section,
        bundle_dir=bundle_dir,
        clock=lambda: T0 + timedelta(minutes=minutes),
    )


def _rows(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT decision_key, status, resolution, last_seen_at, resolved_at, payload"
        " FROM pending_items ORDER BY id"
    ).fetchall()


def test_insert_creates_one_pending_row_with_targets_and_digests(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    outcome = _upsert(conn, _proposal(), section, bundle_dir)

    assert outcome is pq.UpsertOutcome.INSERTED
    (row,) = _rows(conn)
    assert row[0] == "relation_type:" + pq.relation_type_key("concepts/a", "concepts/b")
    assert tuple(row[1:3]) == ("pending", None)
    assert row[3] == T0.isoformat()
    targets = conn.execute(
        "SELECT concept_id FROM pending_item_targets ORDER BY ordinal"
    ).fetchall()
    assert [t[0] for t in targets] == ["concepts/a", "concepts/b"]
    assert conn.execute(
        "SELECT input_ref, digest FROM pending_item_input_digests"
    ).fetchall() == [("concepts/a", "sha-a")]


def test_unchanged_digest_refreshes_last_seen_and_adds_no_row(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    outcome = _upsert(conn, _proposal(), section, bundle_dir, minutes=5)

    assert outcome is pq.UpsertOutcome.UNCHANGED
    (row,) = _rows(conn)
    assert row[1] == "pending"
    assert row[3] == (T0 + timedelta(minutes=5)).isoformat()


def test_changed_digest_retires_the_old_row_and_inserts_a_new_pending_one(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    outcome = _upsert(
        conn, _proposal(digests=(("concepts/a", "sha-a2"),)), section, bundle_dir, 7
    )

    assert outcome is pq.UpsertOutcome.REPLACED
    old, new = _rows(conn)
    assert tuple(old[1:3]) == ("stale", "stale")
    assert old[4] == (T0 + timedelta(minutes=7)).isoformat()
    assert tuple(new[1:3]) == ("pending", None)


def test_payload_change_alone_changes_the_digest(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    outcome = _upsert(conn, _proposal(payload='{"type":"owns"}'), section, bundle_dir)

    assert outcome is pq.UpsertOutcome.REPLACED
    assert [r[5] for r in _rows(conn)] == ['{"type":"works_at"}', '{"type":"owns"}']


def test_a_stale_row_does_not_block_reinsertion_of_the_same_digest(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    _upsert(conn, _proposal(payload="x"), section, bundle_dir)
    outcome = _upsert(conn, _proposal(), section, bundle_dir)

    assert outcome is pq.UpsertOutcome.REPLACED
    assert [r[1] for r in _rows(conn)] == ["stale", "stale", "pending"]


def test_one_open_row_per_key_is_enforced_by_the_schema(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO pending_items (decision_key, kind, producer, payload,"
            " payload_digest, status, created_at, last_seen_at)"
            " SELECT decision_key, kind, producer, payload, 'other', 'claimed',"
            " created_at, last_seen_at FROM pending_items"
        )


def test_unknown_kind_is_refused_before_any_write(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    bad = pq.Proposal("nonsense", "k", "p", "{}", ("a",))
    with pytest.raises(ValueError, match="nonsense"):
        _upsert(conn, bad, section, bundle_dir)
    assert not pq.queue_exists(conn)


def test_writes_enter_the_commit_phase(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    assert section.entered == 1


def test_a_failure_inside_the_transaction_rolls_back_the_retirement(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    bad = _proposal(payload="changed")
    # A NULL digest violates NOT NULL on the digests insert, AFTER the old row
    # was retired and the new item row was inserted in the same transaction.
    null_digest = pq.InputDigest("concepts/a", "x")
    object.__setattr__(null_digest, "digest", None)
    object.__setattr__(bad, "input_digests", (null_digest,))
    with pytest.raises(sqlite3.IntegrityError):
        _upsert(conn, bad, section, bundle_dir, minutes=1)

    assert [r[1] for r in _rows(conn)] == ["pending"]


def test_typed_edge_and_merged_body_contradictions_stay_distinct(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    pair = ("people/ana", "people/bo")
    typed = pq.contradiction_key(pair, None)
    merged = pq.contradiction_key(pair, "people/bo")
    assert typed != merged

    for body in (typed, merged):
        out = _upsert(
            conn,
            _proposal(kind="contradiction", key_body=body, targets=pair),
            section,
            bundle_dir,
        )
        assert out is pq.UpsertOutcome.INSERTED

    assert [r[0] for r in _rows(conn)] == [
        "contradiction:" + typed,
        "contradiction:" + merged,
    ]
    assert all(r[1] == "pending" for r in _rows(conn))


def test_keys_of_different_kinds_never_collide_over_the_same_concepts(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    pair = ("people/ana", "people/bo")
    contradiction = _proposal(
        kind="contradiction", key_body=pq.contradiction_key(pair, None), targets=pair
    )
    identity = _proposal(
        kind="identity", key_body=pq.identity_key(list(pair)), targets=pair
    )
    assert contradiction.decision_key != identity.decision_key
    _upsert(conn, contradiction, section, bundle_dir)
    assert _upsert(conn, identity, section, bundle_dir) is pq.UpsertOutcome.INSERTED


def test_identity_key_is_the_kept_distinct_key_regardless_of_member_order() -> None:
    assert pq.identity_key(["b", "a", "c"]) == (
        bundle_decisions.identity_decision_key_for(["a", "b", "c"])
    )


def test_contradiction_decline_in_force_suppresses_the_insert(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    pair = ("people/ana", "people/bo")
    body = pq.contradiction_key(pair, "people/bo")
    bundle_decisions.write_decisions(
        pair[0],
        bundle_dir,
        records=[
            bundle_decisions.DecisionRecord(
                decision_key=body,
                pair_ids=pair,
                merged_absorbed_id="people/bo",
                state="declined",
                decided_at="2026-01-01T00:00:00+00:00",
            )
        ],
    )
    proposal = _proposal(kind="contradiction", key_body=body, targets=pair)

    assert _upsert(conn, proposal, section, bundle_dir) is pq.UpsertOutcome.SUPPRESSED
    assert _rows(conn) == []

    # The typed-edge proposal over the same pair is a different key.
    other = _proposal(
        kind="contradiction", key_body=pq.contradiction_key(pair, None), targets=pair
    )
    assert _upsert(conn, other, section, bundle_dir) is pq.UpsertOutcome.INSERTED


def test_a_reopened_decision_no_longer_suppresses(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    pair = ("people/ana", "people/bo")
    body = pq.contradiction_key(pair, None)
    bundle_decisions.write_decisions(
        pair[0],
        bundle_dir,
        records=[
            bundle_decisions.DecisionRecord(body, pair, None, "open", "2026-01-01")
        ],
    )
    proposal = _proposal(kind="contradiction", key_body=body, targets=pair)
    assert _upsert(conn, proposal, section, bundle_dir) is pq.UpsertOutcome.INSERTED


def test_identity_keep_distinct_in_force_suppresses_the_insert(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    members = ("people/ana", "people/bo", "people/cy")
    body = pq.identity_key(members)
    bundle_decisions.write_identity_decisions(
        members[0],
        bundle_dir,
        records=[
            bundle_decisions.IdentityDecisionRecord(
                body, members, "declined", "2026-01-01"
            )
        ],
    )
    proposal = _proposal(kind="identity", key_body=body, targets=members)

    assert _upsert(conn, proposal, section, bundle_dir) is pq.UpsertOutcome.SUPPRESSED
    assert _rows(conn) == []


def test_retire_unseen_on_a_complete_run_retires_only_unseen_rows_of_that_kind(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    keep = _proposal(targets=("c/a", "c/b"))
    drop = _proposal(targets=("c/c", "c/d"))
    other_kind = _proposal(
        kind="volatility", key_body=pq.volatility_key("Person"), targets=("Person",)
    )
    for p in (keep, drop, other_kind):
        _upsert(conn, p, section, bundle_dir)

    retired = pq.retire_unseen(
        conn,
        "relation_type",
        {keep.decision_key},
        complete=True,
        commit_section=section,
        clock=lambda: T0 + timedelta(minutes=9),
    )

    assert retired == 1
    by_key = {r[0]: r for r in _rows(conn)}
    assert tuple(by_key[drop.decision_key][1:3]) == ("stale", "stale")
    assert by_key[keep.decision_key][1] == "pending"
    assert by_key[other_kind.decision_key][1] == "pending"


def test_retire_unseen_on_a_truncated_run_retires_nothing(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    entered = section.entered

    retired = pq.retire_unseen(
        conn, "relation_type", set(), complete=False, commit_section=section
    )

    assert retired == 0
    assert [r[1] for r in _rows(conn)] == ["pending"]
    assert section.entered == entered


def test_dead_claimant_reads_as_pending(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    dead = f"{child.pid}@{pq.boot_id()}"
    assert pq.claim_item(conn, 1, claimant=dead, commit_section=section)

    (item,) = pq.open_items(conn)

    assert item.status == "pending"
    assert item.claimed_by is None
    # The stored row is untouched: the lapse is a read-time judgement.
    assert conn.execute("SELECT status FROM pending_items").fetchone() == ("claimed",)


def test_live_claimant_reads_as_claimed_and_blocks_another_claim(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    me = pq.current_claimant()
    assert pq.claim_item(conn, 1, claimant=me, commit_section=section)

    (item,) = pq.open_items(conn)
    assert (item.status, item.claimed_by) == ("claimed", me)
    assert not pq.claim_item(
        conn, 1, claimant="1@" + pq.boot_id(), commit_section=section
    )


def test_a_dead_claim_can_be_taken_over(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    pq.claim_item(
        conn, 1, claimant="999999@x", commit_section=section, alive=lambda _: False
    )
    assert pq.claim_item(
        conn,
        1,
        claimant=pq.current_claimant(),
        commit_section=section,
        alive=lambda _: False,
    )


def test_unparseable_or_foreign_boot_claimants_are_dead() -> None:
    assert not pq.claimant_alive("garbage")
    assert not pq.claimant_alive("0@")
    if pq.boot_id():
        pid = pq.current_claimant().split("@")[0]
        assert not pq.claimant_alive(f"{pid}@not-this-boot")


def test_a_claimed_row_is_refreshed_and_retired_like_a_pending_one(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    pq.claim_item(conn, 1, claimant=pq.current_claimant(), commit_section=section)

    assert _upsert(conn, _proposal(), section, bundle_dir) is pq.UpsertOutcome.UNCHANGED
    assert (
        _upsert(conn, _proposal(payload="changed"), section, bundle_dir)
        is pq.UpsertOutcome.REPLACED
    )
    old, new = _rows(conn)
    assert old[1] == "stale"
    assert new[1] == "pending"


def test_open_items_filters_by_kind_and_hides_resolved_rows(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    _upsert(
        conn,
        _proposal(
            kind="volatility", key_body=pq.volatility_key("Person"), targets=("Person",)
        ),
        section,
        bundle_dir,
    )
    _upsert(conn, _proposal(payload="v2"), section, bundle_dir)  # first goes stale

    assert [i.kind for i in pq.open_items(conn)] == ["volatility", "relation_type"]
    assert [i.kind for i in pq.open_items(conn, kind="volatility")] == ["volatility"]


def test_an_absent_queue_is_distinguishable_from_an_empty_one(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    assert not pq.queue_exists(conn)
    assert pq.open_items(conn) == []
    pq.retire_unseen(conn, "identity", set(), complete=True, commit_section=section)
    assert pq.queue_exists(conn)


def test_sweep_lookup_finds_rows_by_target_input_ref_sources_of_and_digest(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(
        conn,
        _proposal(
            targets=("c/a", "c/b"),
            digests=(("sources-of:src/s1", "dig-s1"), ("c/z", "dig-z")),
        ),
        section,
        bundle_dir,
    )
    _upsert(
        conn,
        _proposal(targets=("c/x", "c/y"), digests=(("c/x", "dig-x"),)),
        section,
        bundle_dir,
    )

    assert pq.find_item_ids_referencing(conn, ["c/b"]) == {1}
    assert pq.find_item_ids_referencing(conn, ["c/z"]) == {1}
    assert pq.find_item_ids_referencing(conn, ["src/s1"]) == {1}
    assert pq.find_item_ids_referencing(conn, [], digests=["dig-x"]) == {2}
    assert pq.find_item_ids_referencing(conn, ["c/nope"]) == set()


def test_sweep_lookup_sees_stale_rows_too(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    _upsert(conn, _proposal(), section, bundle_dir)
    _upsert(conn, _proposal(payload="v2"), section, bundle_dir)
    assert pq.find_item_ids_referencing(conn, ["concepts/a"]) == {1, 2}


def test_queue_file_is_owner_only_when_opened_through_the_derived_opener(
    tmp_path: Path, section: _Section, bundle_dir: Path
) -> None:
    path = tmp_path / ".openkos" / "findings.db"
    connection = derived.open_derived_connection(path)
    try:
        _upsert(connection, _proposal(), section, bundle_dir)
    finally:
        connection.close()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_payload_round_trips_unmodified(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    payload = json.dumps({"a": 1, "claims": ["x"]}, sort_keys=True)
    _upsert(conn, _proposal(payload=payload), section, bundle_dir)
    (item,) = pq.open_items(conn)
    assert item.payload == payload


# -- resolution (unit 4.3) --------------------------------------------------


def test_resolving_an_absent_queue_moves_nothing_and_creates_nothing(
    conn: sqlite3.Connection, section: _Section
) -> None:
    moved = pq.resolve_open_by_key(
        conn,
        "relation_type:x",
        resolution=lambda _item: "as_proposed",
        resolved_by="relate",
        commit_section=section,
    )
    by_digest = pq.resolve_open_by_input_digest(
        conn,
        "watch_refusal",
        "sha-a",
        resolution="as_proposed",
        resolved_by="ingest",
        commit_section=section,
    )

    assert (moved, by_digest) == (0, 0)
    assert not pq.queue_exists(conn)
    assert section.entered == 0


def test_resolution_records_the_verb_and_time_and_never_touches_a_closed_row(
    conn: sqlite3.Connection, section: _Section, bundle_dir: Path
) -> None:
    proposal = _proposal()
    _upsert(conn, proposal, section, bundle_dir)

    first = pq.resolve_open_by_key(
        conn,
        proposal.decision_key,
        resolution=lambda _item: "modified",
        resolved_by="relate",
        commit_section=section,
        clock=lambda: T0,
    )
    again = pq.resolve_open_by_key(
        conn,
        proposal.decision_key,
        resolution=lambda _item: "declined",
        resolved_by="decline",
        commit_section=section,
        clock=lambda: T0,
    )

    assert (first, again) == (1, 0)
    row = conn.execute(
        "SELECT status, resolution, resolved_by, resolved_at, claimed_by"
        " FROM pending_items"
    ).fetchone()
    assert row == ("applied", "modified", "relate", T0.isoformat(), None)
