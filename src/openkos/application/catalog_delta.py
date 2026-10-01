"""Catalog re-composition for a verb's commit phase (ADR-0036, issue #1137).

Every verb appends to `index.md` and `log.md`, so under a lock held only for
the commit phase a change to either since the plan was composed is the
ordinary case, not drift. A plan therefore carries its catalog *delta* -- a
pure function from the current catalog text to the text the verb wants -- as
well as the finished bytes. At commit time:

* when the catalog still holds the bytes the plan was composed from, the
  finished text is used as planned;
* otherwise the delta is re-applied to the current bytes, so a concurrent
  append is kept beside the verb's own entry.

Only a catalog that cannot be re-read, or whose current text cannot take the
delta, refuses (`CatalogRecomposeError`, which every adapter maps to its exit
`3`). Nothing here locks, prints or imports `typer`; the caller enters the
commit section and decides what the refusal looks like.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from openkos import fsio

CatalogDelta = Callable[[str, str], tuple[str, str]]
"""`(current_index_text, current_log_text)` -> `(new_index_text, new_log_text)`."""

LogDelta = Callable[[str], str]
"""`current_text` -> `new_text` for one catalog file, for a verb that edits only
`log.md` (or only `index.md`)."""

SnapshotRead = Callable[[Path], tuple[bytes, str]]


class CatalogRecomposeError(ValueError):
    """The catalog cannot take the plan's delta; nothing was written. The
    message is the whole refusal, ready to print."""


def _read(path: Path, verb: str, read: SnapshotRead) -> tuple[bytes, str]:
    try:
        return read(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogRecomposeError(
            f"openkos {verb}: refusing to write -- the catalog ({path.name}) "
            f"could not be re-read for the commit phase: {exc}. "
            "Nothing was written."
        ) from exc


def recompose_catalog(
    *,
    verb: str,
    index_path: Path,
    log_path: Path,
    index_baseline: bytes,
    log_baseline: bytes,
    planned: tuple[str, str],
    delta: CatalogDelta,
    read: SnapshotRead = fsio.snapshot_read,
    subject: str = "this run's",
) -> tuple[str, str]:
    """The `(index_text, log_text)` a verb that writes both files commits.

    `planned` is the finished text the plan was composed with from
    `index_baseline`/`log_baseline`; `delta` re-derives it from the current
    bytes when either file moved. Raises `CatalogRecomposeError` when a file
    cannot be re-read or `delta` rejects the current text."""
    current_index_bytes, current_index = _read(index_path, verb, read)
    current_log_bytes, current_log = _read(log_path, verb, read)
    if current_index_bytes == index_baseline and current_log_bytes == log_baseline:
        return planned
    try:
        return delta(current_index, current_log)
    except ValueError as exc:
        raise CatalogRecomposeError(
            f"openkos {verb}: refusing to write -- index.md or log.md changed "
            f"and {subject} entries cannot be re-applied to it: {exc}. "
            "Nothing was written."
        ) from exc


def recompose_file(
    *,
    verb: str,
    path: Path,
    baseline: bytes,
    planned: str,
    delta: LogDelta,
    read: SnapshotRead = fsio.snapshot_read,
    subject: str = "this run's",
) -> str:
    """`recompose_catalog` for a verb that writes ONE catalog file (`log.md`, or
    the `index.md` front matter) and not the other."""
    current_bytes, current = _read(path, verb, read)
    if current_bytes == baseline:
        return planned
    try:
        return delta(current)
    except ValueError as exc:
        raise CatalogRecomposeError(
            f"openkos {verb}: refusing to write -- {path.name} changed and "
            f"{subject} edit cannot be re-applied to it: {exc}. "
            "Nothing was written."
        ) from exc
