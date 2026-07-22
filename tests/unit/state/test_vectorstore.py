"""Unit tests for `state/vectorstore.py`: sqlite-vec on-disk scaffolding.

Slice 2a is additive infrastructure only -- a guarded sqlite-vec extension
loader, an injectable `VectorStore` Protocol seam (lifecycle-only: `close()`),
and an idempotent `vectors.db` schema at `<root>/.openkos/vectors.db`.
Slice 2b makes the seam real: `upsert`/`query` data flow plus the
`meta_hashes`/`prune` cache accessors the reindex orchestrator needs. This
module still has no CLI surface -- the `reindex` command (`cli/main.py`) is
its first consumer.
"""

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
import sqlite_vec

from openkos.llm.base import EMBED_DIM
from openkos.state import vectorstore


def _serialize(values: list[float]) -> bytes:
    """Build a raw vec0 embedding blob without depending on `upsert` (Slice
    2b's own subject under test) -- delegates to `sqlite_vec.serialize_float32`,
    the same serializer `upsert` itself will use. `sqlite-vec` ships no type
    stubs, so its return is `Any`; `cast` documents the known real type
    without weakening `strict` mode elsewhere (mirrors the `pyproject.toml`
    override's rationale)."""
    return cast(bytes, sqlite_vec.serialize_float32(values))


# --- Phase 1: vec0 semantics spike (gates all Slice 2b data-flow work) -----


@pytest.mark.skipif(
    not vectorstore.probe_vec_loadable(), reason="sqlite-vec extension not loadable"
)
def test_vec0_delete_by_concept_id_then_reinsert_survives_with_one_row(
    tmp_path: Path,
) -> None:
    """Real sqlite-vec 0.1.9 extension: `DELETE FROM vectors WHERE
    concept_id = ?` (metadata-column delete, no rowid lookup) removes the
    prior row for that `concept_id`, and a subsequent `INSERT` leaves exactly
    one row for it -- proves `upsert`'s planned
    delete-by-concept_id-then-insert sequence is valid against the real
    extension (design Open Question: confirm DELETE-by-metadata works in
    0.1.9, else fall back to DELETE-by-rowid)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        conn = db._conn
        conn.execute(
            "INSERT INTO vectors (embedding, concept_id, content_hash) "
            "VALUES (?, ?, ?)",
            (_serialize([1.0] * EMBED_DIM), "concepts/a", "hash-one"),
        )
        conn.commit()

        conn.execute("DELETE FROM vectors WHERE concept_id = ?", ("concepts/a",))
        conn.execute(
            "INSERT INTO vectors (embedding, concept_id, content_hash) "
            "VALUES (?, ?, ?)",
            (_serialize([2.0] * EMBED_DIM), "concepts/a", "hash-two"),
        )
        conn.commit()

        rows = conn.execute(
            "SELECT concept_id, content_hash FROM vectors WHERE concept_id = ?",
            ("concepts/a",),
        ).fetchall()

    assert rows == [("concepts/a", "hash-two")]


@pytest.mark.skipif(
    not vectorstore.probe_vec_loadable(), reason="sqlite-vec extension not loadable"
)
def test_vec0_metadata_filtered_knn_returns_expected_concept_id_ascending(
    tmp_path: Path,
) -> None:
    """Real sqlite-vec 0.1.9 extension: `embedding MATCH ? AND k = ? ORDER BY
    distance` returns `(concept_id, distance)` rows, nearest first -- proves
    `query`'s planned KNN statement is valid against the real extension."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        conn = db._conn
        near = [1.0] + [0.0] * (EMBED_DIM - 1)
        far = [0.0, 1.0] + [0.0] * (EMBED_DIM - 2)
        conn.execute(
            "INSERT INTO vectors (embedding, concept_id, content_hash) "
            "VALUES (?, ?, ?)",
            (_serialize(near), "concepts/near", "hash-near"),
        )
        conn.execute(
            "INSERT INTO vectors (embedding, concept_id, content_hash) "
            "VALUES (?, ?, ?)",
            (_serialize(far), "concepts/far", "hash-far"),
        )
        conn.commit()

        rows = conn.execute(
            "SELECT concept_id, distance FROM vectors "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (_serialize(near), 2),
        ).fetchall()

    assert [row[0] for row in rows] == ["concepts/near", "concepts/far"]
    assert rows[0][1] < rows[1][1]


# --- Phase 3: content_hash --------------------------------------------------


def test_content_hash_is_stable_across_calls() -> None:
    """Hashing the same bytes twice returns the identical digest."""
    data = b"---\ntitle: Stoicism\n---\nBody text.\n"

    assert vectorstore.content_hash(data) == vectorstore.content_hash(data)


def test_content_hash_matches_sha256_hexdigest_of_raw_bytes() -> None:
    """The digest is exactly `hashlib.sha256(data).hexdigest()` -- no
    normalization, no encoding re-interpretation."""
    data = b"raw markdown bytes"

    assert vectorstore.content_hash(data) == hashlib.sha256(data).hexdigest()


def test_content_hash_differs_for_differing_bytes() -> None:
    """Different raw bytes produce different digests."""
    first = vectorstore.content_hash(b"version one")
    second = vectorstore.content_hash(b"version two")

    assert first != second


# --- Phase 4: VecUnavailable + VectorStore Protocol + loader + schema ------


def test_extended_fake_satisfies_vector_store_protocol_structurally() -> None:
    """A fake implementing `close`/`upsert`/`upsert_many`/`query`/
    `meta_hashes`/`prune`/`prune_many`/`commit` satisfies `VectorStore`
    structurally -- mirrors `Embedder`/`LLMBackend` (`llm/base.py`); mypy
    accepts the assignment below (`uv run mypy .` gate). The Protocol
    extension is additive: the SAME lifecycle seam 2a defined, with the
    Slice 2b data-flow methods (`upsert`/`query`/`meta_hashes`/`prune`), and
    now the Slice 5 follow-up #4 batch methods (`upsert_many`/`prune_many`/
    `commit`), all added on top (spec: Protocol Extended Additively --
    Extended fake satisfies the Protocol).

    A fake declaring ONLY `close()` can no longer satisfy the now-larger
    `VectorStore` Protocol -- verified directly against mypy's own Protocol
    structural-typing rules, which require EVERY declared member to be
    present, with no partial/optional subset (Protocol methods are
    implicitly abstract; a subclass missing one is rejected, and a
    non-subclassed structural match is rejected the same way). "Additive"
    therefore means the Protocol's SHAPE grows without dropping `close()`,
    not that a minimal old fake remains valid against the new, larger shape.
    """

    class _FakeVectorStore:
        def __init__(self) -> None:
            self.closed = False
            self.upserts: list[tuple[str, list[float], str]] = []
            self.pruned: list[str] = []
            self.committed = False
            self.model_tag: str | None = None

        def close(self) -> None:
            self.closed = True

        def upsert(
            self, concept_id: str, embedding: Sequence[float], content_hash: str
        ) -> None:
            self.upserts.append((concept_id, list(embedding), content_hash))

        def upsert_many(
            self, items: Sequence[tuple[str, Sequence[float], str]]
        ) -> None:
            for concept_id, embedding, content_hash in items:
                self.upserts.append((concept_id, list(embedding), content_hash))

        def query(self, embedding: Sequence[float], k: int) -> list[vectorstore.VecHit]:
            return []

        def meta_hashes(self) -> dict[str, str]:
            return {}

        def prune(self, concept_id: str) -> None:
            self.pruned.append(concept_id)

        def prune_many(self, concept_ids: Sequence[str]) -> None:
            self.pruned.extend(concept_ids)

        def commit(self) -> None:
            self.committed = True

        def read_model_tag(self) -> str | None:
            return self.model_tag

        def write_model_tag(self, tag: str) -> None:
            self.model_tag = tag

    fake: vectorstore.VectorStore = _FakeVectorStore()
    fake.upsert("concepts/a", [0.0] * EMBED_DIM, "hash")
    fake.prune("concepts/a")
    fake.close()

    assert isinstance(fake, _FakeVectorStore)
    assert fake.closed is True
    assert fake.upserts == [("concepts/a", [0.0] * EMBED_DIM, "hash")]
    assert fake.pruned == ["concepts/a"]


def test_vec_unavailable_is_a_runtime_error() -> None:
    """`VecUnavailable` subclasses `RuntimeError`, mirrors `FtsUnavailable`."""
    assert issubclass(vectorstore.VecUnavailable, RuntimeError)


class _NoLoadExtensionConnection(sqlite3.Connection):
    """Simulates a SQLite build with no `enable_load_extension` support.

    `sqlite3.Connection` is a C-extension type: its methods cannot be
    monkeypatched on the class or an instance directly, so this subclasses
    it instead (mirrors `fts.py`'s `_NoFts5Connection` test pattern)."""

    def enable_load_extension(self, enable: bool) -> None:
        """Raise `AttributeError`, simulating an absent method on this build."""
        raise AttributeError("enable_load_extension not available on this build")


def test_open_vector_store_raises_vec_unavailable_when_load_extension_missing(
    tmp_path: Path,
) -> None:
    """An injected `connect` whose `enable_load_extension` raises
    `AttributeError` maps to `VecUnavailable`, and the connection is closed
    before the error propagates (mirrors `fts.py`'s guard)."""
    captured: dict[str, sqlite3.Connection] = {}

    def fake_connect(database: str) -> sqlite3.Connection:
        conn = sqlite3.connect(database, factory=_NoLoadExtensionConnection)
        captured["conn"] = conn
        return conn

    db_path = tmp_path / ".openkos" / "vectors.db"

    with pytest.raises(vectorstore.VecUnavailable):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")


class _ExtensionLoadingDisabledConnection(sqlite3.Connection):
    """Simulates a SQLite build where extension loading is compiled out."""

    def enable_load_extension(self, enable: bool) -> None:
        """Raise `OperationalError` on enable, simulating a disabled build."""
        if enable:
            raise sqlite3.OperationalError("extension loading is disabled")


def test_open_vector_store_raises_vec_unavailable_when_loading_disabled(
    tmp_path: Path,
) -> None:
    """An injected `connect` whose `enable_load_extension` raises
    `sqlite3.OperationalError` also maps to `VecUnavailable`, connection
    closed first."""
    captured: dict[str, sqlite3.Connection] = {}

    def fake_connect(database: str) -> sqlite3.Connection:
        conn = sqlite3.connect(database, factory=_ExtensionLoadingDisabledConnection)
        captured["conn"] = conn
        return conn

    db_path = tmp_path / ".openkos" / "vectors.db"

    with pytest.raises(vectorstore.VecUnavailable):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")


def test_open_vector_store_lazily_creates_openkos_dir_and_both_tables(
    tmp_path: Path,
) -> None:
    """Real interpreter: `open_vector_store` succeeds, lazily creates the
    `.openkos/` parent directory (which does not exist beforehand), and both
    the `vectors` vec0 virtual table and the `vector_meta` companion table
    exist afterward."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    assert not db_path.parent.exists()

    with vectorstore.open_vector_store(db_path) as db:
        table_names = {
            str(row[0])
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', "
                "'virtual table')"
            ).fetchall()
        }

    assert db_path.parent.is_dir()
    assert db_path.is_file()
    assert "vectors" in table_names
    assert "vector_meta" in table_names


def test_open_vector_store_reopen_is_idempotent(tmp_path: Path) -> None:
    """Re-opening the same `path` after it already exists is a no-op
    migration -- no error, same tables present."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    with vectorstore.open_vector_store(db_path):
        pass

    with vectorstore.open_vector_store(db_path) as db:
        table_names = {
            str(row[0])
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', "
                "'virtual table')"
            ).fetchall()
        }

    assert "vectors" in table_names
    assert "vector_meta" in table_names


def test_vector_meta_companion_supports_hash_keyed_lookup(tmp_path: Path) -> None:
    """The `vector_meta` companion table supports hash-keyed lookup: a row
    inserted with a `content_hash` is retrievable by querying that
    `content_hash`, without touching the `vectors` vec0 table."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    digest = vectorstore.content_hash(b"# Concept\n\nbody\n")

    with vectorstore.open_vector_store(db_path) as db:
        db._conn.execute(
            "INSERT INTO vector_meta (concept_id, content_hash) VALUES (?, ?)",
            ("concept-a.md", digest),
        )
        rows = db._conn.execute(
            "SELECT concept_id FROM vector_meta WHERE content_hash = ?",
            (digest,),
        ).fetchall()

    assert [str(row[0]) for row in rows] == ["concept-a.md"]


def test_vector_store_db_context_manager_closes_connection_after_block(
    tmp_path: Path,
) -> None:
    """`with open_vector_store(...) as db:` closes the connection on block
    exit (mirrors `FtsIndex`)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        conn = db._conn

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_vector_store_db_close_can_be_called_directly(tmp_path: Path) -> None:
    """`close()` works outside a `with` block too."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    db = vectorstore.open_vector_store(db_path)

    db.close()

    with pytest.raises(sqlite3.ProgrammingError):
        db._conn.execute("SELECT 1")


class _FailingVectorMetaConnection(sqlite3.Connection):
    """A real connection (extension loads normally) whose `vector_meta`
    `CREATE TABLE` fails -- simulates a mid-schema failure AFTER the
    extension load and the `vectors` DDL both already succeeded."""

    def execute(self, sql: str, *args: object, **kwargs: object) -> sqlite3.Cursor:
        """Raise `OperationalError` for the `vector_meta` DDL, delegate
        everything else (including the extension load's own SQL, if any)."""
        if "vector_meta" in sql:
            raise sqlite3.OperationalError("simulated mid-schema failure")
        return super().execute(sql, *args, **kwargs)  # type: ignore[arg-type]


def test_open_vector_store_closes_connection_on_mid_schema_failure(
    tmp_path: Path,
) -> None:
    """A DDL failure AFTER a successful extension load (e.g. the
    `vector_meta` table creation) still closes the connection before
    propagating -- it must not leak (mirrors `fts.py`'s build-failure
    guard)."""
    captured: dict[str, sqlite3.Connection] = {}

    def fake_connect(database: str) -> sqlite3.Connection:
        conn = sqlite3.connect(database, factory=_FailingVectorMetaConnection)
        captured["conn"] = conn
        return conn

    db_path = tmp_path / ".openkos" / "vectors.db"

    with pytest.raises(sqlite3.OperationalError):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")


class _UnanticipatedFailureConnection(sqlite3.Connection):
    """Simulates a future `sqlite_vec.load`/`enable_load_extension` failure
    of a type NOT covered by the `AttributeError`/`sqlite3.Error` guard
    (e.g. `MemoryError`, `KeyboardInterrupt`)."""

    def enable_load_extension(self, enable: bool) -> None:
        """Raise `MemoryError`, simulating an unanticipated failure type."""
        raise MemoryError("simulated: unanticipated failure during load")


def test_load_vec_extension_closes_connection_on_unmapped_exception_type() -> None:
    """An exception type OTHER than `AttributeError`/`sqlite3.Error` still
    closes the connection before propagating -- it is NOT swallowed into
    `VecUnavailable`; only `AttributeError`/`sqlite3.Error` map to that. A
    failed load never leaks the connection, regardless of the exception
    type that caused the failure."""
    conn = sqlite3.connect(":memory:", factory=_UnanticipatedFailureConnection)

    with pytest.raises(MemoryError):
        vectorstore._load_vec_extension(conn)

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_open_vector_store_closes_connection_on_unmapped_exception_type(
    tmp_path: Path,
) -> None:
    """The same unmapped-exception guard holds through `open_vector_store`:
    the connection is closed and the original exception type propagates
    (not `VecUnavailable`), and no on-disk footprint is left behind."""
    captured: dict[str, sqlite3.Connection] = {}

    def fake_connect(database: str) -> sqlite3.Connection:
        conn = sqlite3.connect(database, factory=_UnanticipatedFailureConnection)
        captured["conn"] = conn
        return conn

    db_path = tmp_path / ".openkos" / "vectors.db"

    with pytest.raises(MemoryError):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")
    assert not db_path.parent.exists()


def test_open_vector_store_leaves_no_new_openkos_dir_when_load_extension_missing(
    tmp_path: Path,
) -> None:
    """A `VecUnavailable` failure (missing `enable_load_extension`) leaves
    no new `.openkos/` directory or `vectors.db` file on disk when neither
    existed before the call."""

    def fake_connect(database: str) -> sqlite3.Connection:
        return sqlite3.connect(database, factory=_NoLoadExtensionConnection)

    db_path = tmp_path / ".openkos" / "vectors.db"
    assert not db_path.parent.exists()

    with pytest.raises(vectorstore.VecUnavailable):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    assert not db_path.parent.exists()
    assert not db_path.exists()


def test_open_vector_store_leaves_no_new_openkos_dir_when_loading_disabled(
    tmp_path: Path,
) -> None:
    """Same no-new-footprint guarantee for the `sqlite3.OperationalError`
    (extension loading disabled) failure branch."""

    def fake_connect(database: str) -> sqlite3.Connection:
        return sqlite3.connect(database, factory=_ExtensionLoadingDisabledConnection)

    db_path = tmp_path / ".openkos" / "vectors.db"
    assert not db_path.parent.exists()

    with pytest.raises(vectorstore.VecUnavailable):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    assert not db_path.parent.exists()
    assert not db_path.exists()


def test_open_vector_store_preserves_pre_existing_openkos_dir_on_failure(
    tmp_path: Path,
) -> None:
    """A PRE-EXISTING `.openkos/` directory (and a file inside it) is left
    untouched by a `VecUnavailable` failure -- only artifacts THIS call
    would have created may ever be cleaned up, and this call creates
    none."""

    def fake_connect(database: str) -> sqlite3.Connection:
        return sqlite3.connect(database, factory=_NoLoadExtensionConnection)

    db_path = tmp_path / ".openkos" / "vectors.db"
    db_path.parent.mkdir(parents=True)
    sentinel = db_path.parent / "sentinel.txt"
    sentinel.write_text("pre-existing", encoding="utf-8")

    with pytest.raises(vectorstore.VecUnavailable):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    assert db_path.parent.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "pre-existing"
    assert not db_path.exists()


def test_vectors_table_uses_embed_dim_from_llm_base(tmp_path: Path) -> None:
    """The vec0 `embedding` column width is `EMBED_DIM` (`llm/base.py`),
    the single source of truth for vector width -- not a hardcoded literal
    duplicated in this module."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        (create_sql,) = db._conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'vectors'"
        ).fetchone()

    assert f"float[{EMBED_DIM}]" in create_sql


# --- Phase 5: probe_vec_loadable -------------------------------------------


def test_probe_vec_loadable_returns_true_on_this_interpreter() -> None:
    """Real interpreter: `probe_vec_loadable()` returns a `bool` and, on an
    extension-capable build, returns `True`."""
    result = vectorstore.probe_vec_loadable()

    assert isinstance(result, bool)
    assert result is True


def test_probe_vec_loadable_returns_false_never_raises_on_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the guarded load sequence fails, `probe_vec_loadable` degrades
    to `False` instead of propagating the exception."""

    def _raise(conn: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("simulated: extension loading disabled")

    # Patches the SAME `sqlite_vec` module object `vectorstore.py` calls
    # `sqlite_vec.load` against -- module attribute access, not a re-export,
    # so this stays valid under `implicit_reexport = False` (mypy strict).
    monkeypatch.setattr(sqlite_vec, "load", _raise)

    assert vectorstore.probe_vec_loadable() is False


def test_probe_vec_loadable_returns_false_on_unmapped_exception_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception type NOT mapped by the guarded load (e.g. `MemoryError`,
    or any future `sqlite_vec.load` failure) still degrades to `False`
    instead of propagating -- `probe_vec_loadable` must never raise for an
    ordinary failure, since `doctor` calls it with no surrounding
    try/except before rendering any check."""

    def _raise(conn: sqlite3.Connection) -> None:
        raise MemoryError("simulated: unanticipated failure during load")

    monkeypatch.setattr(sqlite_vec, "load", _raise)

    assert vectorstore.probe_vec_loadable() is False


def test_probe_vec_loadable_closes_connection_on_unmapped_exception_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The throwaway `:memory:` connection is still closed even when the
    failure is an unmapped exception type (the `finally: conn.close()`
    covers every failure path, not only `VecUnavailable`)."""
    captured: dict[str, sqlite3.Connection] = {}
    real_connect = sqlite3.connect

    def fake_connect(database: str) -> sqlite3.Connection:
        conn = real_connect(database)
        captured["conn"] = conn
        return conn

    def _raise(conn: sqlite3.Connection) -> None:
        raise MemoryError("simulated: unanticipated failure during load")

    monkeypatch.setattr(sqlite3, "connect", fake_connect)
    monkeypatch.setattr(sqlite_vec, "load", _raise)

    assert vectorstore.probe_vec_loadable() is False
    with pytest.raises(sqlite3.ProgrammingError):
        captured["conn"].execute("SELECT 1")


def test_probe_vec_loadable_does_not_swallow_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`KeyboardInterrupt`/`SystemExit` are not "ordinary failures" -- they
    are `BaseException`, not `Exception`, and must still propagate rather
    than degrade to `False`."""

    def _raise(conn: sqlite3.Connection) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(sqlite_vec, "load", _raise)

    with pytest.raises(KeyboardInterrupt):
        vectorstore.probe_vec_loadable()


# --- Phase 5b: real-path connect()/schema footprint on failure -------------


class _FailingRealConnectFactory:
    """A `connect` factory whose `:memory:` probe succeeds (real connection,
    real extension load) but whose real-path connect raises -- simulates a
    `PermissionError`/`OSError` opening the on-disk database file after the
    probe already confirmed the extension loads."""

    def __call__(self, database: str) -> sqlite3.Connection:
        if database == ":memory:":
            return sqlite3.connect(database)
        raise PermissionError("simulated: cannot open database file")


def test_open_vector_store_leaves_no_new_footprint_when_real_connect_fails(
    tmp_path: Path,
) -> None:
    """When the `:memory:` probe passes but the real-path `connect` raises,
    `open_vector_store` re-raises the original exception and leaves no new
    `.openkos/` directory on disk (the failure happens after the probe, so
    the previous `VecUnavailable`-only footprint guarantee alone would not
    have covered this path)."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    assert not db_path.parent.exists()

    with pytest.raises(PermissionError):
        vectorstore.open_vector_store(db_path, connect=_FailingRealConnectFactory())

    assert not db_path.parent.exists()
    assert not db_path.exists()


def test_open_vector_store_preserves_pre_existing_dir_when_real_connect_fails(
    tmp_path: Path,
) -> None:
    """A PRE-EXISTING `.openkos/` directory (with a sentinel file) is left
    untouched when the real-path `connect` raises after a successful probe
    -- only artifacts THIS call would have created may be cleaned up."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    db_path.parent.mkdir(parents=True)
    sentinel = db_path.parent / "sentinel.txt"
    sentinel.write_text("pre-existing", encoding="utf-8")

    with pytest.raises(PermissionError):
        vectorstore.open_vector_store(db_path, connect=_FailingRealConnectFactory())

    assert db_path.parent.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "pre-existing"
    assert not db_path.exists()


def test_open_vector_store_leaves_no_new_footprint_on_mid_schema_failure(
    tmp_path: Path,
) -> None:
    """A DDL failure AFTER a successful extension load and real-path
    connect (e.g. the `vector_meta` table creation) also leaves no new
    `.openkos/` directory or `vectors.db` file behind -- the connection's
    own on-disk file is cleaned up too, not just closed."""

    def fake_connect(database: str) -> sqlite3.Connection:
        return sqlite3.connect(database, factory=_FailingVectorMetaConnection)

    db_path = tmp_path / ".openkos" / "vectors.db"
    assert not db_path.parent.exists()

    with pytest.raises(sqlite3.OperationalError):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    assert not db_path.parent.exists()
    assert not db_path.exists()


# --- Phase 2: upsert/query data flow ----------------------------------------


def test_upsert_first_insert_creates_one_vectors_row_and_one_meta_row(
    tmp_path: Path,
) -> None:
    """First `upsert` of a new `concept_id` inserts exactly one `vectors` row
    and one `vector_meta` row for it (spec: First upsert of a new concept
    inserts one row)."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    embedding = [0.1] * EMBED_DIM

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", embedding, "hash-1")
        vector_rows = db._conn.execute(
            "SELECT concept_id, content_hash FROM vectors"
        ).fetchall()
        meta_rows = db._conn.execute(
            "SELECT concept_id, content_hash FROM vector_meta"
        ).fetchall()

    assert vector_rows == [("concepts/a", "hash-1")]
    assert meta_rows == [("concepts/a", "hash-1")]


def test_upsert_reupsert_replaces_prior_vector_and_updates_meta_hash(
    tmp_path: Path,
) -> None:
    """Re-`upsert` of an existing `concept_id` removes the old row, leaves
    exactly one row, and `vector_meta` reflects the new `content_hash`
    (spec: Re-upsert replaces the prior vector)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", [0.1] * EMBED_DIM, "hash-1")
        db.upsert("concepts/a", [0.2] * EMBED_DIM, "hash-2")
        vector_rows = db._conn.execute(
            "SELECT concept_id, content_hash FROM vectors"
        ).fetchall()
        meta_rows = db._conn.execute(
            "SELECT concept_id, content_hash FROM vector_meta"
        ).fetchall()

    assert vector_rows == [("concepts/a", "hash-2")]
    assert meta_rows == [("concepts/a", "hash-2")]


def test_upsert_does_not_affect_other_concept_ids(tmp_path: Path) -> None:
    """`upsert` of one `concept_id` never touches another's row (baseline
    isolation, guards the DELETE-by-`concept_id` statement's WHERE clause)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", [0.1] * EMBED_DIM, "hash-a")
        db.upsert("concepts/b", [0.2] * EMBED_DIM, "hash-b")
        db.upsert("concepts/a", [0.3] * EMBED_DIM, "hash-a2")
        rows = dict(
            db._conn.execute(
                "SELECT concept_id, content_hash FROM vector_meta"
            ).fetchall()
        )

    assert rows == {"concepts/a": "hash-a2", "concepts/b": "hash-b"}


def test_query_returns_k_nearest_ordered_by_ascending_distance(
    tmp_path: Path,
) -> None:
    """`query(embedding, k)` returns up to `k` `VecHit`s, nearest first
    (spec: Query returns nearest neighbors ordered by distance)."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    near = [1.0] + [0.0] * (EMBED_DIM - 1)
    far = [0.0, 1.0] + [0.0] * (EMBED_DIM - 2)

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/near", near, "hash-near")
        db.upsert("concepts/far", far, "hash-far")
        hits = db.query(near, k=2)

    assert [hit.concept_id for hit in hits] == ["concepts/near", "concepts/far"]
    assert all(isinstance(hit, vectorstore.VecHit) for hit in hits)
    assert hits[0].distance < hits[1].distance


def test_query_respects_k_limit(tmp_path: Path) -> None:
    """`query` never returns more than `k` hits, even when more rows exist."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        for i in range(5):
            vec = [0.0] * EMBED_DIM
            vec[0] = float(i)
            db.upsert(f"concepts/{i}", vec, f"hash-{i}")
        hits = db.query([0.0] * EMBED_DIM, k=2)

    assert len(hits) == 2


def test_query_against_empty_store_returns_empty_list_not_an_error(
    tmp_path: Path,
) -> None:
    """`query` against a store with no upserted vectors returns `[]`, not an
    error (spec: Query against an empty store returns no results)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        hits = db.query([0.0] * EMBED_DIM, k=5)

    assert hits == []


def test_vec_hit_is_a_frozen_dataclass_with_concept_id_and_distance() -> None:
    """`VecHit` mirrors `FtsHit`'s shape: a frozen dataclass with
    `concept_id`/`distance` fields."""
    hit = vectorstore.VecHit(concept_id="concepts/a", distance=0.5)

    assert hit.concept_id == "concepts/a"
    assert hit.distance == 0.5
    with pytest.raises(AttributeError):
        hit.concept_id = "concepts/b"  # type: ignore[misc]


# --- Phase 3: meta_hashes/prune cache accessors -----------------------------


def test_meta_hashes_returns_concept_id_to_content_hash_mapping(
    tmp_path: Path,
) -> None:
    """`meta_hashes()` returns `{concept_id: content_hash}` for every row."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", [0.1] * EMBED_DIM, "hash-a")
        db.upsert("concepts/b", [0.2] * EMBED_DIM, "hash-b")
        hashes = db.meta_hashes()

    assert hashes == {"concepts/a": "hash-a", "concepts/b": "hash-b"}


def test_meta_hashes_on_empty_store_returns_empty_dict(tmp_path: Path) -> None:
    """`meta_hashes()` against an empty store returns `{}`, not an error."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        hashes = db.meta_hashes()

    assert hashes == {}


def test_prune_removes_rows_from_both_vectors_and_vector_meta(
    tmp_path: Path,
) -> None:
    """`prune(concept_id)` removes matching rows from both `vectors` and
    `vector_meta` (spec: `prune(concept_id)` removes matching rows from both
    tables)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", [0.1] * EMBED_DIM, "hash-a")
        db.upsert("concepts/b", [0.2] * EMBED_DIM, "hash-b")
        db.prune("concepts/a")
        vector_ids = {
            str(row[0])
            for row in db._conn.execute("SELECT concept_id FROM vectors").fetchall()
        }
        meta_ids = set(db.meta_hashes())

    assert vector_ids == {"concepts/b"}
    assert meta_ids == {"concepts/b"}


def test_prune_of_absent_concept_id_is_a_no_op(tmp_path: Path) -> None:
    """Pruning a `concept_id` with no stored row does not raise and leaves
    other rows untouched."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", [0.1] * EMBED_DIM, "hash-a")
        db.prune("concepts/does-not-exist")
        hashes = db.meta_hashes()

    assert hashes == {"concepts/a": "hash-a"}


# --- Phase 6: deferred 2a follow-ups -----------------------------------------


def test_second_close_call_does_not_raise(tmp_path: Path) -> None:
    """(c) `VectorStoreDB.close()` is safe to call more than once; a second
    call does not raise (spec: Idempotent Double-Close)."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    db = vectorstore.open_vector_store(db_path)

    db.close()
    db.close()  # must not raise


def test_workspace_root_and_other_files_survive_a_failed_open(
    tmp_path: Path,
) -> None:
    """(a) A failed `open_vector_store` (after `.openkos/` was created but
    before the real connect/DDL succeeds) removes only the `.openkos/`
    artifacts THIS call created -- the enclosing workspace root and its
    other files are left fully intact (spec: Workspace root survives a
    failed open)."""
    other_file = tmp_path / "AGENTS.md"
    other_file.write_text("workspace marker", encoding="utf-8")
    db_path = tmp_path / ".openkos" / "vectors.db"
    assert not db_path.parent.exists()

    with pytest.raises(PermissionError):
        vectorstore.open_vector_store(db_path, connect=_FailingRealConnectFactory())

    assert tmp_path.is_dir()
    assert other_file.read_text(encoding="utf-8") == "workspace marker"
    assert not db_path.parent.exists()


def test_preexisting_vectors_db_survives_a_failed_reopen(tmp_path: Path) -> None:
    """(d) When `path` already exists (`db_preexisted=True`) and a later
    step in `open_vector_store` fails (schema DDL on the real connection),
    the pre-existing file is left untouched -- its bytes are unchanged
    (spec: Pre-existing vectors.db survives a failed reopen)."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    with vectorstore.open_vector_store(db_path):
        pass
    original_bytes = db_path.read_bytes()

    def fake_connect(database: str) -> sqlite3.Connection:
        return sqlite3.connect(database, factory=_FailingVectorMetaConnection)

    with pytest.raises(sqlite3.OperationalError):
        vectorstore.open_vector_store(db_path, connect=fake_connect)

    assert db_path.exists()
    assert db_path.read_bytes() == original_bytes


# --- Phase 7 (Slice 5, follow-up #4): WAL/busy_timeout + batched writes ------


def test_open_vector_store_sets_wal_journal_mode_and_busy_timeout(
    tmp_path: Path,
) -> None:
    """`open_vector_store` sets `PRAGMA journal_mode=WAL` and a non-zero
    `busy_timeout` on the opened connection, mirroring
    `state/derived.py::open_derived_connection`'s posture for the
    (pre-existing, Slice 2b) `vectors.db` store too (reindex-command: WAL
    mode is active on every derived connection, including vectors.db)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        journal_mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = db._conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) > 0


def test_upsert_many_writes_all_items_in_one_batch(tmp_path: Path) -> None:
    """`upsert_many` writes every item's `vectors`/`vector_meta` rows,
    mirroring `upsert`'s own per-item DELETE-then-INSERT semantics, just for
    many `concept_id`s in one call."""
    db_path = tmp_path / ".openkos" / "vectors.db"
    items = [
        ("concepts/a", [0.1] * EMBED_DIM, "hash-a"),
        ("concepts/b", [0.2] * EMBED_DIM, "hash-b"),
    ]

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert_many(items)
        db.commit()
        hashes = db.meta_hashes()

    assert hashes == {"concepts/a": "hash-a", "concepts/b": "hash-b"}


def test_upsert_many_does_not_commit_until_commit_is_called(tmp_path: Path) -> None:
    """`upsert_many` writes are NOT durable to a SEPARATE reader connection
    until `commit()` is explicitly called -- proves the batching contract:
    the write path defers its single commit to the caller (reindex-command:
    single run performs one commit per store, not once per document)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert_many([("concepts/a", [0.1] * EMBED_DIM, "hash-a")])

        reader = sqlite3.connect(str(db_path))
        rows_before_commit = reader.execute(
            "SELECT concept_id FROM vector_meta"
        ).fetchall()
        reader.close()

        db.commit()

        reader2 = sqlite3.connect(str(db_path))
        rows_after_commit = reader2.execute(
            "SELECT concept_id FROM vector_meta"
        ).fetchall()
        reader2.close()

    assert rows_before_commit == []
    assert rows_after_commit == [("concepts/a",)]


def test_prune_many_removes_all_given_concept_ids_in_one_batch(
    tmp_path: Path,
) -> None:
    """`prune_many` removes matching rows from both `vectors` and
    `vector_meta` for every given `concept_id`, mirroring `prune`'s own
    per-item semantics for many `concept_id`s in one call."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", [0.1] * EMBED_DIM, "hash-a")
        db.upsert("concepts/b", [0.2] * EMBED_DIM, "hash-b")
        db.upsert("concepts/c", [0.3] * EMBED_DIM, "hash-c")

        db.prune_many(["concepts/a", "concepts/b"])
        db.commit()
        hashes = db.meta_hashes()

    assert hashes == {"concepts/c": "hash-c"}


def test_commit_persists_writes_to_a_separate_reader_connection(
    tmp_path: Path,
) -> None:
    """`commit()` makes prior `upsert_many`/`prune_many` writes durable and
    visible to a brand-new connection to the same file."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert_many([("concepts/a", [0.1] * EMBED_DIM, "hash-a")])
        db.commit()

    reader = sqlite3.connect(str(db_path))
    rows = reader.execute("SELECT concept_id FROM vector_meta").fetchall()
    reader.close()

    assert rows == [("concepts/a",)]


# --- Phase 8 (MVP-2 follow-up #5): generic meta table + embedding-model tag -


def test_open_vector_store_creates_meta_table_idempotently_on_reopen(
    tmp_path: Path,
) -> None:
    """`open_vector_store` creates a generic `meta(key, value)` table,
    DISTINCT from `vector_meta` (the per-concept content_hash cache), and
    reopening the SAME database is a no-op migration -- the table still
    exists, no error (spec: vector-store Generic Meta Table -- idempotent
    creation)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        table_names_first = {
            str(row[0])
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    with vectorstore.open_vector_store(db_path) as db:
        table_names_second = {
            str(row[0])
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert "meta" in table_names_first
    assert "vector_meta" in table_names_first
    assert table_names_first == table_names_second


def test_read_model_tag_returns_none_when_absent(tmp_path: Path) -> None:
    """`read_model_tag()` returns `None` on a freshly opened store (or one
    predating this follow-up) -- no `embedding_model` row exists yet (spec:
    vector-store Generic Meta Table -- absent tag reads None)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        tag = db.read_model_tag()

    assert tag is None


def test_write_model_tag_persists_across_reopen(tmp_path: Path) -> None:
    """`write_model_tag` commits and the tag round-trips through a brand-new
    `open_vector_store` call against the same file (spec: vector-store
    Generic Meta Table -- write persists across reopen)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.write_model_tag("qwen3-embedding:0.6b")
        db.commit()

    with vectorstore.open_vector_store(db_path) as db:
        tag = db.read_model_tag()

    assert tag == "qwen3-embedding:0.6b"


def test_write_model_tag_replaces_prior_tag_leaving_one_row(tmp_path: Path) -> None:
    """A second `write_model_tag` call REPLACES the prior tag -- exactly one
    `embedding_model` row survives, not two (spec: vector-store Generic Meta
    Table -- write replaces prior tag)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.write_model_tag("qwen3-embedding:0.6b")
        db.commit()
        db.write_model_tag("nomic-embed-text")
        db.commit()
        tag = db.read_model_tag()
        rows = db._conn.execute(
            "SELECT value FROM meta WHERE key = 'embedding_model'"
        ).fetchall()

    assert tag == "nomic-embed-text"
    assert rows == [("nomic-embed-text",)]


def test_meta_hashes_unaffected_by_the_new_meta_table(tmp_path: Path) -> None:
    """`meta_hashes()` (the content_hash cache reindex reads) is completely
    unaffected by the new generic `meta` table -- writing an
    `embedding_model` tag never appears as a fake `concept_id` in
    `meta_hashes()`'s result, proving the two tables stay genuinely separate
    (spec: vector-store Generic Meta Table; design D1 -- rejected a sentinel
    row inside `vector_meta` precisely to avoid this pollution)."""
    db_path = tmp_path / ".openkos" / "vectors.db"

    with vectorstore.open_vector_store(db_path) as db:
        db.upsert("concepts/a", [0.1] * EMBED_DIM, "hash-a")
        db.write_model_tag("qwen3-embedding:0.6b")
        db.commit()
        hashes = db.meta_hashes()

    assert hashes == {"concepts/a": "hash-a"}
    assert "embedding_model" not in hashes
    assert "qwen3-embedding:0.6b" not in hashes.values()
