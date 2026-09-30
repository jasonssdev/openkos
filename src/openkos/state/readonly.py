"""The one read-only opener for the derived stores.

A leaf module (imports nothing from `openkos`) so `vectorstore`, `fts`, `derived`
and the graph store can all share it without an import cycle.
"""

import sqlite3
from pathlib import Path
from urllib.parse import quote


def open_read_only(path: Path) -> sqlite3.Connection:
    """Open the existing database at `path` read-only (`mode=ro`).

    The ONE place a read-only `file:` URI is built. The path is
    percent-encoded before it becomes a URI, because SQLite reads an
    unencoded `?`, `#` or `%` in a directory name as query/fragment/escape
    syntax and the open then fails or targets the wrong file. Every
    read-only opener of a derived store routes through here so a new one
    cannot forget the encoding. Never creates the file and never loads an
    extension; a missing file raises `sqlite3.OperationalError`."""
    return sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
