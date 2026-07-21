"""Unit tests for `state/vectorstore.py`: sqlite-vec on-disk scaffolding.

Slice 2a is additive infrastructure only -- a guarded sqlite-vec extension
loader, an injectable `VectorStore` Protocol seam (lifecycle-only: `close()`),
and an idempotent `vectors.db` schema at `<root>/.openkos/vectors.db`. There
is no vec0 upsert/query data flow yet (deferred to Slice 2b): this module
has no CLI surface and no consumer in this slice.
"""

import hashlib
import sqlite3
from pathlib import Path

import pytest
import sqlite_vec

from openkos.llm.base import EMBED_DIM
from openkos.state import vectorstore

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


def test_close_only_fake_satisfies_vector_store_protocol_structurally() -> None:
    """A fake exposing only `close()` is structurally a `VectorStore` --
    mirrors `Embedder`/`LLMBackend` (`llm/base.py`); mypy accepts the
    assignment below (`uv run mypy .` gate)."""

    class _FakeVectorStore:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake: vectorstore.VectorStore = _FakeVectorStore()
    fake.close()

    assert isinstance(fake, _FakeVectorStore)
    assert fake.closed is True


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


# --- Review Correction 2: connect()/schema footprint on real-path failure --


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
