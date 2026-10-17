"""Whole-tree byte snapshots that compare a workspace's content, not git's
packing.

A git commit may spawn a detached `git maintenance run --auto`, which packs
loose objects and refs at an arbitrary later moment, so a snapshot that walks
`.git/objects` byte for byte flakes with nothing changed. Every test that
snapshots a workspace holding a repository shares this one helper."""

import subprocess
from pathlib import Path

_SQLITE_SIDECARS = ("-wal", "-shm")

# How git STORES history, not what history says: gc, `maintenance --auto` (a
# commit may spawn it detached, so it lands at an arbitrary later moment),
# `repack` and `pack-refs` rewrite these without changing a single object,
# ref or file of the repository.
_GIT_STORAGE = (
    ".git/objects/",
    ".git/refs/",
    ".git/packed-refs",
    ".git/info/refs",
    ".git/gc.log",
)


def _git_content(root: Path) -> dict[str, bytes]:
    """What the repository SAYS, independent of how git packs it: HEAD, every
    ref with its target, and every object (id and type) in the object store."""
    if not (root / ".git").exists():
        return {}

    def git(*args: str) -> bytes:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(root), *args],  # noqa: S607
            check=True,
            capture_output=True,
        ).stdout

    return {
        "git:head": git("symbolic-ref", "-q", "HEAD"),
        "git:refs": git("for-each-ref", "--format=%(refname) %(objectname)"),
        "git:objects": git(
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ),
    }


def snapshot(root: Path, *, skip_parts: tuple[str, ...] = ()) -> dict[str, bytes]:
    """Every file under the workspace, derived stores and `.git` included.

    SQLite's `-wal` and `-shm` sidecars are housekeeping a read-only open of a
    WAL database may create; they hold no content, so a `-wal` file must be
    empty and neither is part of the comparison.

    `.git` is compared by content, not by storage: the index, logs and the
    other loose files are bytes, while objects and refs are read through git
    (`_git_content`) so a background repack cannot show as a change.

    `skip_parts` drops any file with one of those path components."""
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        if set(skip_parts) & set(path.relative_to(root).parts):
            continue
        if rel.startswith(_GIT_STORAGE):
            continue
        if path.name.endswith(_SQLITE_SIDECARS):
            assert path.name.endswith("-shm") or path.stat().st_size == 0, (
                f"a read left content in {path.name}"
            )
            continue
        files[rel] = path.read_bytes()
    return files | _git_content(root)
