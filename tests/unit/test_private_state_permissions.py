"""Engine-owned state is created owner-only (#1135).

`.openkos/` holds the full text and embeddings of every document (including
`confidential` ones) and `bundle/.state/` holds decisions, so on a machine
whose home directory is world-readable a default umask would expose them to
every local account. `bundle/` and `raw/` are the user's own files and are
deliberately NOT tightened.
"""

import contextlib
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from openkos import fsio
from openkos.bundle import decisions, ledger
from openkos.llm.base import EMBED_DIM
from openkos.state import derived, vectorstore

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits are not meaningful on Windows",
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def permissive_umask() -> Iterator[None]:
    """A umask of 0 is the worst case: nothing masks a wide default away."""
    old = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(old)


# --- fsio helpers ---------------------------------------------------------


@pytest.mark.usefixtures("permissive_umask")
def test_mkdir_private_creates_every_new_component_owner_only(
    tmp_path: Path,
) -> None:
    leaf = tmp_path / "a" / "b" / "c"

    fsio.mkdir_private(leaf, top=tmp_path / "a")

    assert _mode(tmp_path / "a") == 0o700
    assert _mode(tmp_path / "a" / "b") == 0o700
    assert _mode(leaf) == 0o700


@pytest.mark.usefixtures("permissive_umask")
def test_mkdir_private_leaves_directories_above_top_alone(tmp_path: Path) -> None:
    user_dir = tmp_path / "bundle"
    user_dir.mkdir(mode=0o755)

    fsio.mkdir_private(user_dir / ".state" / "ledger", top=user_dir / ".state")

    assert _mode(user_dir) == 0o755
    assert _mode(user_dir / ".state") == 0o700
    assert _mode(user_dir / ".state" / "ledger") == 0o700


def test_mkdir_private_does_not_touch_an_existing_directory(tmp_path: Path) -> None:
    existing = tmp_path / ".openkos"
    existing.mkdir(mode=0o755)
    existing.chmod(0o755)

    fsio.mkdir_private(existing)

    assert _mode(existing) == 0o755


@pytest.mark.usefixtures("permissive_umask")
def test_touch_private_creates_owner_only_and_keeps_existing_content(
    tmp_path: Path,
) -> None:
    fresh = tmp_path / "new.db"
    fsio.touch_private(fresh)
    assert _mode(fresh) == 0o600
    assert fresh.read_bytes() == b""

    existing = tmp_path / "old.db"
    existing.write_bytes(b"keep")
    existing.chmod(0o644)
    fsio.touch_private(existing)
    assert existing.read_bytes() == b"keep"
    assert _mode(existing) == 0o644


# --- the SQLite store openers ---------------------------------------------


@pytest.mark.usefixtures("permissive_umask")
def test_derived_store_and_its_wal_sidecars_are_owner_only(tmp_path: Path) -> None:
    db_path = tmp_path / ".openkos" / "fts.db"

    conn = derived.open_derived_connection(db_path)
    try:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()

        assert _mode(db_path.parent) == 0o700
        assert _mode(db_path) == 0o600
        sidecars = [
            db_path.with_name(db_path.name + suffix) for suffix in ("-wal", "-shm")
        ]
        assert any(p.exists() for p in sidecars), "expected WAL sidecars while open"
        for sidecar in sidecars:
            if sidecar.exists():
                assert _mode(sidecar) == 0o600, sidecar.name
    finally:
        conn.close()


@pytest.mark.usefixtures("permissive_umask")
@pytest.mark.skipif(
    not vectorstore.probe_vec_loadable(), reason="sqlite-vec extension not loadable"
)
def test_vector_store_is_owner_only(tmp_path: Path) -> None:
    db_path = tmp_path / ".openkos" / "vectors.db"

    with contextlib.closing(vectorstore.open_vector_store(db_path)) as store:
        store.upsert("concepts/a", [0.1] * EMBED_DIM, "hash")
        assert _mode(db_path.parent) == 0o700
        assert _mode(db_path) == 0o600
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                assert _mode(sidecar) == 0o600, sidecar.name


def test_an_existing_store_is_not_re_moded_by_a_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / ".openkos" / "fts.db"
    derived.open_derived_connection(db_path).close()
    db_path.chmod(0o644)

    derived.open_derived_connection(db_path).close()

    assert _mode(db_path) == 0o644  # `doctor` reports it; the engine never guesses


# --- bundle/.state --------------------------------------------------------


@pytest.mark.usefixtures("permissive_umask")
def test_ledger_and_decision_writers_create_state_dir_owner_only(
    tmp_path: Path,
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(mode=0o755)

    ledger.write_pending(
        "concepts/a",
        bundle_dir,
        survivor_id="concepts/a",
        entries=[],
        expected_survivor_sha256="0" * 64,
    )
    record = decisions.DecisionRecord(
        decision_key=decisions.decision_key_for(("concepts/a", "concepts/b"), None),
        pair_ids=("concepts/a", "concepts/b"),
        merged_absorbed_id=None,
        state="declined",
        decided_at="2026-08-12T00:00:00Z",
    )
    decisions.write_decisions("concepts/a", bundle_dir, records=[record])

    state_dir = bundle_dir / ".state"
    assert _mode(state_dir) == 0o700
    assert _mode(ledger.ledger_root(bundle_dir)) == 0o700
    assert _mode(decisions.decisions_root(bundle_dir)) == 0o700
    assert _mode(bundle_dir) == 0o755  # the user's directory is never tightened
