"""Shared filesystem primitives with no dependency on any other `openkos` module.

A leaf module: `bundle/bundle.py` and `config.py` both import from here, but
this never imports from either, avoiding a `bundle` -> `config` (or reverse)
layering dependency.
"""

import contextlib
import os
import uuid
from pathlib import Path

RENAME_TEMP_PREFIX = "okos-nfc-tmp-"
"""Namespace for `rename_two_step`'s intermediate sibling name (issue #474
part 2, design D3). ASCII -- trivially NFC, so a stranded temp is never
self-flagged by `lint.scan_non_nfc_entries` -- and unique per call
(`uuid4().hex` suffix), so concurrent runs never collide."""


def write_exclusive(path: Path, content: str) -> None:
    """Write `content` to `path`, refusing to overwrite an existing file.

    Exclusive-create mode ("x"): a colliding file raises `FileExistsError`
    instead of being overwritten (D2). `newline=""` and `encoding="utf-8"`
    are preserved so callers keep byte-identical output. Mirrors
    `copy_exclusive`'s cleanup: if the write fails after `path` was already
    created, `path` is unlinked before re-raising -- `path` is create-only,
    so a leftover partial file would make every retry raise
    `FileExistsError` and block recovery.
    """
    with path.open("x", encoding="utf-8", newline="") as f:
        try:
            f.write(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise


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


def remove_file(path: Path) -> None:
    """Delete `path`, refusing to silently no-op on a missing file.

    `path.unlink()` with the default `missing_ok=False` -- symmetry with
    `write_exclusive`/`write_atomic`/`copy_exclusive`: every filesystem
    mutation this module offers goes through an explicit primitive rather
    than an inline `Path` call. A missing `path` raises `FileNotFoundError`
    rather than a silent no-op, since a caller (`forget`) that already
    verified the file exists during Phase A treats its disappearance before
    Phase B as a genuine race worth surfacing, not something to swallow.
    """
    path.unlink()


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


def rename_two_step(src: Path, nfc_name: str) -> Path:
    """Rename `src` to `nfc_name` (its own NFC-normalized name) through a
    unique ASCII temporary sibling, verifying the result by byte-exact
    directory listing (issue #474 part 2, `normalize-names`, design D2).

    `src -> src.parent/f"{RENAME_TEMP_PREFIX}{uuid4().hex}" -> src.parent
    /nfc_name`. A single DIRECT `os.rename(src, nfc_target)` is never
    used: APFS/HFS+ and SMB shares are normalization-*insensitive*, so a
    rename between two canonically equivalent names (the raw NFD spelling
    and its NFC form) can be silently treated as a same-file no-op that
    leaves the on-disk spelling untouched -- a failure mode that reports
    success while `lint` still flags the entry. Neither hop of the
    two-step scheme crosses a canonically equivalent pair -- the ASCII
    temp name shares no characters with either spelling -- so both
    `os.rename` calls are real renames even under a hostile,
    normalization-insensitive primitive.

    Verification is `nfc_name in os.listdir(src.parent)`, a byte-exact
    listing comparison, never `Path.exists()`: the macOS spike (design.md
    S1, Q4) observed `Path(nfc_target).exists()` returning `True` with
    ONLY the NFD name on disk, which is exactly the silent-success result
    this primitive exists to rule out.

    Immediately before the second rename, a byte-exact destination-
    presence guard checks `nfc_name` against `os.listdir(parent)`: a dst
    that appeared since the caller's own drift re-check (the batched
    check in `normalize-names` runs BEFORE its per-entry renames, so a
    later entry's window is real) would otherwise be SILENTLY overwritten
    by `os.rename` -- instead, the original name is restored and
    `OSError` is raised naming the collision (PR #492 risk finding).

    On the collision guard firing, the second rename failing, or the
    post-rename verification failing, a restore of the entry to
    `src.name` (the original on-disk spelling) is ATTEMPTED --
    best-effort, its own `OSError` suppressed so the ORIGINAL failure
    propagates, never the restore's -- and `OSError` is raised loudly,
    per design D2. When the restore succeeds, no
    `RENAME_TEMP_PREFIX`-named entry is left on disk. A stranded temp can
    result from a hard kill strictly between the two `os.rename` calls
    below, OR from a double fault where the failure's suppressed restore
    also fails (PR #492 informational finding) -- in every such case the
    entry remains at its machine-detectable temp name and
    `lint.scan_stranded_rename_temps` reports it at the next run (design
    D3), never this primitive.
    """
    parent = src.parent
    original_name = src.name
    temp_path = parent / f"{RENAME_TEMP_PREFIX}{uuid.uuid4().hex}"
    dst = parent / nfc_name
    # `os.rename` (not `Path.rename`), deliberately: this primitive's own
    # tests monkeypatch `os.rename` IN THIS MODULE'S NAMESPACE to inject a
    # normalization-insensitive rename and prove the two-step scheme
    # survives it (design D2/D3) -- `Path.rename` would not be patchable
    # the same way.
    os.rename(src, temp_path)  # noqa: PTH104
    # Byte-exact destination-presence guard (PR #492): a dst created
    # between the caller's drift re-check and this entry's rename would
    # be silently overwritten by `os.rename` below -- refuse instead.
    if nfc_name in os.listdir(parent):  # noqa: PTH208
        with contextlib.suppress(OSError):
            os.rename(temp_path, parent / original_name)  # noqa: PTH104
        raise OSError(
            f"rename_two_step: refusing to overwrite -- {nfc_name!r} is "
            f"already present byte-exactly in {parent} (collision with "
            f"an entry created after the caller's drift re-check)"
        )
    try:
        os.rename(temp_path, dst)  # noqa: PTH104
    except OSError:
        # Best-effort restore, suppressed (PR #492): a double fault must
        # propagate the ORIGINAL hop-2 OSError, never the restore's; the
        # entry then remains at the machine-detectable temp name.
        with contextlib.suppress(OSError):
            os.rename(temp_path, parent / original_name)  # noqa: PTH104
        raise
    # Byte-exact directory-listing verification, never `Path.exists()`
    # (design D2): the macOS spike observed `exists()` return `True` with
    # only the NFD name on disk (design.md S1, Q4).
    if nfc_name not in os.listdir(parent):  # noqa: PTH208
        with contextlib.suppress(OSError):
            os.rename(dst, parent / original_name)  # noqa: PTH104
        raise OSError(
            f"rename_two_step: verification failed -- {nfc_name!r} not "
            f"found byte-exactly in {parent} after renaming {original_name!r}"
        )
    return dst
