"""CLI-test-specific fixtures.

The offline-Ollama default that used to live here (#183) moved up to
`tests/unit/conftest.py` in #217. Two reasons, both discovered by measuring
rather than reading: it was INCOMPLETE (it stubbed `chat` and `embed` but not
`list_models`, so every test running `openkos init` still made a real HTTP
request), and it was SCOPED TOO NARROWLY (`tests/unit/vcs/conftest.py`'s
`tmp_git_repo` fixture and `tests/unit/retrieval/test_answer.py` also drive
`ingest --auto`, and sat outside this package's reach).

Nothing here shadows it any more. A same-named fixture in this file would
override the parent's for every test under `tests/unit/cli/**` -- which is
exactly how the incomplete stub kept winning -- so the seam default is
deliberately defined once, at the unit-suite root.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def seed_vectors_db() -> Callable[[Path], None]:
    """Return a callable that writes a `.openkos/vectors.db` holding one
    `vector_meta` row, so the bundle counts as embeddings PRESENT.

    Issue #183's state 3 keys on "absent OR empty", not merely absent -- a
    zero-byte file must NOT read as present. `init` never creates this file,
    so embeddings-missing is the default for a bare `_init_workspace` call
    unless a test opts out via this fixture.

    A factory rather than a plain fixture: seeding is opt-in per test (most
    CLI tests want the embeddings-missing default), and the callable form
    keeps the existing `seed_vectors_db(tmp_path)` call shape from the three
    module-level helpers it replaces (#197).
    """

    def _seed(workspace_root: Path) -> None:
        openkos_dir = workspace_root / ".openkos"
        openkos_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(openkos_dir / "vectors.db"))
        try:
            conn.execute(
                "CREATE TABLE vector_meta (concept_id TEXT PRIMARY KEY, "
                "content_hash TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO vector_meta (concept_id, content_hash) "
                "VALUES ('stub', 'hash')"
            )
            conn.commit()
        finally:
            conn.close()

    return _seed
