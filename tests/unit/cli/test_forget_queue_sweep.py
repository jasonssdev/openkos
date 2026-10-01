"""`forget` sweeps the pending-work queue (#1141; forget-command spec:
"Deletion Sweep Includes The Pending-Work Queue").

The queue is a fifth tenant of `.openkos/findings.db`; its payloads can quote
a forgotten concept's text, so a row naming that concept must be ERASED --
rows deleted, then `VACUUM` plus an inspected `wal_checkpoint` -- not merely
unlinked from the table."""

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openkos.cli.main import app
from openkos.model import okf
from openkos.state import derived
from openkos.state import pending_queue as pq
from tests.unit.cli.conftest import commit_pending_fixture_docs

runner = CliRunner()

SENTINEL = "ZQX-SENTINEL-4471-quoted-from-the-forgotten-body"
OTHER_SENTINEL = "KEEP-SENTINEL-9902-from-a-survivor"


def _section() -> contextlib.AbstractContextManager[None]:
    return contextlib.nullcontext()


def _init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init"]).exit_code == 0


def _concept(tmp_path: Path, concept_id: str) -> None:
    path = tmp_path / "bundle" / f"{concept_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        okf.dump_frontmatter({"type": "Concept", "title": concept_id}, "Body.\n"),
        encoding="utf-8",
    )
    commit_pending_fixture_docs()


def _proposal(
    kind: str,
    key_body: str,
    payload: str,
    targets: tuple[str, ...],
    digests: tuple[tuple[str, str], ...] = (),
) -> pq.Proposal:
    return pq.Proposal(
        kind=kind,
        key_body=key_body,
        producer="test/1",
        payload=payload,
        targets=targets,
        input_digests=tuple(pq.InputDigest(r, d) for r, d in digests),
    )


@contextlib.contextmanager
def _queue(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    (tmp_path / ".openkos").mkdir(exist_ok=True)
    conn = derived.open_derived_connection(tmp_path / ".openkos" / "findings.db")
    try:
        yield conn
    finally:
        conn.close()


def _seed(tmp_path: Path, proposals: list[pq.Proposal]) -> None:
    with _queue(tmp_path) as conn:
        for proposal in proposals:
            pq.upsert_proposal(
                conn,
                proposal,
                commit_section=_section,
                bundle_dir=tmp_path / "bundle",
            )


def _store_bytes(tmp_path: Path) -> bytes:
    base = tmp_path / ".openkos" / "findings.db"
    data = b""
    for suffix in ("", "-wal"):
        path = base.with_name(base.name + suffix)
        if path.exists():
            data += path.read_bytes()
    return data


def _keys(tmp_path: Path) -> set[str]:
    with _queue(tmp_path) as conn:
        return {r[0] for r in conn.execute("SELECT decision_key FROM pending_items")}


def _forget(concept_id: str) -> str:
    result = runner.invoke(app, ["forget", concept_id, "--auto"])
    assert result.exit_code == 0, result.output
    return result.output


def test_forget_erases_rows_naming_the_concept_with_no_recoverable_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/gone")
    _concept(tmp_path, "concepts/stays")
    _concept(tmp_path, "concepts/other")
    _seed(
        tmp_path,
        [
            # An identity row whose member set includes the forgotten id.
            _proposal(
                "identity",
                pq.identity_key(["concepts/gone", "concepts/other"]),
                f'{{"quote":"{SENTINEL}"}}',
                ("concepts/gone", "concepts/other"),
            ),
            # A watch_refusal keyed by the forgotten id.
            _proposal(
                "watch_refusal",
                pq.watch_refusal_key("concepts/gone"),
                f'{{"note":"{SENTINEL}-2"}}',
                ("concepts/gone",),
            ),
            # Unrelated row: must survive untouched.
            _proposal(
                "volatility",
                pq.volatility_key("Concept"),
                f'{{"note":"{OTHER_SENTINEL}"}}',
                ("concepts/stays",),
            ),
        ],
    )
    before = _store_bytes(tmp_path)
    assert SENTINEL.encode() in before  # precondition: it was on disk
    assert len(_keys(tmp_path)) == 3

    _forget("concepts/gone")

    after = _store_bytes(tmp_path)
    assert SENTINEL.encode() not in after
    assert OTHER_SENTINEL.encode() in after
    assert _keys(tmp_path) == {f"volatility:{pq.volatility_key('Concept')}"}
    with _queue(tmp_path) as conn:
        targets = conn.execute("SELECT COUNT(*) FROM pending_item_targets")
        assert targets.fetchone()[0] == 1
    wal = tmp_path / ".openkos" / "findings.db-wal"
    assert not wal.exists() or wal.stat().st_size == 0


def test_forget_matches_on_input_ref_and_sources_of_not_only_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/gone")
    _concept(tmp_path, "concepts/stays")
    _seed(
        tmp_path,
        [
            _proposal(
                "revision",
                pq.revision_key(("concepts/stays", "concepts/stays2")),
                f'{{"q":"{SENTINEL}"}}',
                ("concepts/stays", "concepts/stays2"),
                digests=(("sources-of:concepts/gone", "d1"),),
            ),
            _proposal(
                "relation_type",
                pq.relation_type_key("concepts/stays", "concepts/stays3"),
                f'{{"q":"{SENTINEL}-b"}}',
                ("concepts/stays", "concepts/stays3"),
                digests=(("concepts/gone", "d2"),),
            ),
        ],
    )
    assert SENTINEL.encode() in _store_bytes(tmp_path)
    assert len(_keys(tmp_path)) == 2

    _forget("concepts/gone")

    assert _keys(tmp_path) == set()
    assert SENTINEL.encode() not in _store_bytes(tmp_path)
    with _queue(tmp_path) as conn:
        digests = conn.execute("SELECT COUNT(*) FROM pending_item_input_digests")
        assert digests.fetchone()[0] == 0


def test_forget_without_a_queue_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/gone")

    _forget("concepts/gone")

    assert not (tmp_path / ".openkos" / "findings.db").exists()


def test_forget_with_a_store_but_no_queue_table_does_not_create_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/gone")
    with _queue(tmp_path) as conn:
        conn.execute("CREATE TABLE marker (x)")
        conn.commit()

    _forget("concepts/gone")

    with _queue(tmp_path) as conn:
        assert not pq.queue_exists(conn)


def test_forget_with_an_unreadable_store_warns_naming_the_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init(tmp_path, monkeypatch)
    _concept(tmp_path, "concepts/gone")
    (tmp_path / ".openkos").mkdir(exist_ok=True)
    (tmp_path / ".openkos" / "findings.db").write_bytes(b"not a database" * 100)

    result = runner.invoke(app, ["forget", "concepts/gone", "--auto"])

    assert result.exit_code == 0, result.output
    assert "findings.db" in result.output
    assert "may still quote" in result.output


def test_a_pinned_wal_is_reported_not_silently_left_holding_the_payload(
    tmp_path: Path,
) -> None:
    """A blocked `wal_checkpoint` is reported through the returned row's `busy`
    column, never as an exception. A reader pinning the WAL leaves the deleted
    payload in un-truncated frames, so the sweep must raise (and `forget`
    degrade that to its residue warning) instead of reporting success."""
    _seed(
        tmp_path,
        [
            _proposal(
                "watch_refusal",
                pq.watch_refusal_key("concepts/gone"),
                f'{{"note":"{SENTINEL}"}}',
                ("concepts/gone",),
            )
        ],
    )
    reader = sqlite3.connect(tmp_path / ".openkos" / "findings.db")
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM pending_items").fetchall()
        with _queue(tmp_path) as writer:
            writer.execute("PRAGMA busy_timeout = 0")
            with pytest.raises(sqlite3.OperationalError, match="checkpoint busy"):
                pq.delete_items_referencing(writer, {"concepts/gone"})
    finally:
        reader.close()
