"""Shared filesystem primitives with no dependency on any other `openkos` module.

A leaf module: `bundle/bundle.py` and `config.py` both import from here, but
this never imports from either, avoiding a `bundle` -> `config` (or reverse)
layering dependency.
"""

import os
import uuid
from pathlib import Path


def write_exclusive(path: Path, content: str) -> None:
    """Write `content` to `path`, refusing to overwrite an existing file.

    Exclusive-create mode ("x"): a colliding file raises `FileExistsError`
    instead of being overwritten (D2). `newline=""` and `encoding="utf-8"`
    are preserved so callers keep byte-identical output.
    """
    with path.open("x", encoding="utf-8", newline="") as f:
        f.write(content)


def write_atomic(path: Path, content: str) -> None:
    """Write `content` to `path`, replacing any existing file atomically (D1).

    Separate from `write_exclusive` (which stays create-only, D2) so callers
    that need to overwrite an existing file -- `bundle/index.md`,
    `bundle/log.md` -- never lose that guarantee by accident. Writes to a
    uniquely-named temp file in `path.parent` (same filesystem, so the
    rename is atomic; the name includes the pid and a random token so two
    concurrent calls for the same `path` never share, and cannot clobber,
    each other's in-flight temp file), flushes and `os.fsync`s it for
    content durability, then renames it onto `path` via `Path.replace`
    (which calls `os.replace` under the hood). If anything before the
    replace fails, the temp file is unlinked and `path`'s original content
    is left untouched.

    Visibility atomicity comes from the rename itself: any reader sees
    either the whole old file or the whole new one, never a splice. Content
    is fsynced before the rename, but a directory fsync is deliberately
    deferred (D1) -- so this does NOT guarantee the rename survives a crash;
    it only guarantees `path` is never left half-written.
    """
    tmp_path = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def copy_exclusive(src: Path, dst: Path) -> None:
    """Copy `src`'s bytes to `dst`, refusing to overwrite an existing file.

    `write_exclusive`'s binary counterpart: raw sources may carry any
    extension (not just UTF-8 text), so this reads and writes bytes, not
    str. Exclusive-create mode ("xb"): a colliding `dst` raises
    `FileExistsError` instead of being overwritten (D2). Mirrors
    `write_atomic`'s cleanup: if the write fails after `dst` was already
    created, `dst` is unlinked before re-raising -- `dst` is create-only, so
    a leftover partial file would make every retry raise `FileExistsError`
    and block recovery.
    """
    content = src.read_bytes()
    with dst.open("xb") as f:
        try:
            f.write(content)
        except BaseException:
            dst.unlink(missing_ok=True)
            raise
