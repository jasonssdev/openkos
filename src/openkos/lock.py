"""Advisory interprocess workspace lock (#925).

A leaf module: it imports only other leaf modules (`userstate`), mirroring
`fsio.py`, so it can be used from `config`, `cli`, or a future application
service without creating a layering dependency.

**The gap this closes.** The drift guards (#313/#319/#322/#334/#335) protect a
single process's read -> confirm -> write window by re-reading every target
immediately before Phase B. They are blind to a SECOND process: two concurrent
mutators each read the same `index.md`, each pass their own drift comparison
against their own snapshot, and both write. Whichever lands second wins, and
the first process's committed entries are gone with no warning from either
side -- a classic lost update. The drift guards remain the second line of
defense; this is the first.

**Advisory, not mandatory.** Nothing stops a text editor, `git`, or a
hand-written script from writing into the bundle. This serializes OpenKOS
against OpenKOS, which is the race the CLI actually creates.

**Why a kernel lock and not a pidfile.** `fcntl.flock` (POSIX) and
`msvcrt.locking` (Windows) are both released by the kernel when the holding
process exits, however it exits -- including `SIGKILL` and a power loss, since
the lock lives in kernel state and not on disk. A pidfile would need staleness
heuristics, and every such heuristic is wrong at least once: a reused pid makes
it refuse a free workspace, and a too-eager cleanup makes it grant a busy one.
The lock file here carries NO content for exactly that reason -- there is no
recorded state that can go stale, so a leftover `workspace.lock` after a crash
is inert, and removing it by hand is never part of recovery.

**Fail fast, do not queue.** Contention raises `WorkspaceBusyError` rather than
blocking. A blocking acquire would sit behind another process's interactive
confirmation prompt for an unbounded time with no output, which reads as a
hang. The refusal is retry-safe by construction: it fires before the verb has
read anything, so nothing was written and a later re-run is exactly equivalent.

**The lock lives OUTSIDE the workspace, deliberately.** The obvious home is
`<root>/.openkos/workspace.lock`, and it was the first design. It is wrong for
a reason worth recording, because it looks right: this repo's refusal tests
assert that a refusing command leaves the workspace byte- and structure-
identical, and `tests/unit/cli/conftest.py`'s snapshot records DIRECTORIES too,
explicitly so a refusal that created a stray `.git` is caught. Creating
`.openkos/` and a lock file to then refuse breaks that guarantee literally --
96 tests went red -- and the honest reading is that they were right: a run that
refuses really should write nothing at all.

**The home is the per-user state directory** (`userstate.locks_dir()`,
ADR-0036), resolved from the account database and never from the environment,
because a temp reaper that deletes a held lock lets a second process lock a
fresh inode -- harmless for a CLI's short holds, not for a long-lived runner.
The earlier temp-directory lock is no longer taken (#1217): an `openkos` of
0.3.0 or older locks only that path, so it does not exclude this one.
"""

import contextlib
import hashlib
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

from openkos import userstate


class WorkspaceBusyError(RuntimeError):
    """Another OpenKOS process holds this workspace's mutation lock.

    Carries the operator-facing sentence directly, so every call site reports
    the same wording without formatting one of its own.
    """


class WorkspaceLockUnavailableError(RuntimeError):
    """The lock directory or file cannot be trusted or opened (#1134).

    Distinct from `WorkspaceBusyError` on purpose: busy is transient and
    retry-safe, while this persists until the operator fixes the directory, so
    the CLI reports it as a plain refusal rather than the retryable exit 3.
    Carries the operator-facing sentence, including the fix.
    """


BUSY_REASON = (
    "another OpenKOS process is modifying this workspace right now; nothing "
    "was read or written by this run -- wait for that command to finish and "
    "try again"
)


# `sys.platform`, not a `try: import fcntl` probe, because this is the
# discriminator mypy narrows on: the checker runs against ONE platform's stubs,
# so the branch for the other must be invisible to it or every `msvcrt`
# attribute reads as an error on POSIX (and every `fcntl` one on Windows).
if sys.platform == "win32":  # pragma: no cover - not exercised by Linux CI
    import msvcrt

    def _acquire_exclusive_nonblocking(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _acquire_exclusive_nonblocking(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _ensure_private_dir(directory: Path, *, create_parent: bool = False) -> None:
    """Create `directory` owner-only if it is missing.

    `Path.mkdir(mode=..., parents=True)` applies `mode` to the LAST component
    only, so the immediate parent is created explicitly when asked: the OpenKOS
    state directory above `locks` must be owner-only too.
    """
    try:
        if create_parent:
            directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise WorkspaceLockUnavailableError(
            f"cannot create the lock directory {directory} ({exc.strerror or exc}); "
            f"nothing was read or written by this run -- remove or fix "
            f"{directory} and try again"
        ) from exc
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None:
        _verify_lock_dir(directory, geteuid())


def _lock_dir() -> Path:
    """The state-directory `locks` directory, created on demand owner-only and
    verified to be ours (#1134)."""
    directory = Path(userstate.locks_dir())
    _ensure_private_dir(directory, create_parent=True)
    return directory


def _verify_lock_dir(directory: Path, euid: int) -> None:
    """Refuse a lock directory that is not ours, not a real directory, or open
    to group/other (#1134). `mkdir(exist_ok=True)` accepts whatever already sits
    at the path, and the name is guessable from the uid, so another local user
    can pre-create it. POSIX-only: Windows has no uid and its temp directory is
    already per-user."""
    fix = (
        f"nothing was read or written by this run -- remove {directory} "
        f"(or, if it is yours, `chmod 700 {directory}`) and try again"
    )
    try:
        info = os.lstat(directory)
    except OSError as exc:
        raise WorkspaceLockUnavailableError(
            f"cannot inspect the lock directory {directory} "
            f"({exc.strerror or exc}); {fix}"
        ) from exc
    if stat.S_ISLNK(info.st_mode):
        raise WorkspaceLockUnavailableError(
            f"the lock directory {directory} is a symlink; {fix}"
        )
    if not stat.S_ISDIR(info.st_mode):
        raise WorkspaceLockUnavailableError(
            f"the lock directory {directory} is not a directory; {fix}"
        )
    if info.st_uid != euid:
        raise WorkspaceLockUnavailableError(
            f"the lock directory {directory} is not owned by you; {fix}"
        )
    if info.st_mode & 0o077:
        raise WorkspaceLockUnavailableError(
            f"the lock directory {directory} is accessible to other users "
            f"(mode {stat.S_IMODE(info.st_mode):o}); {fix}"
        )


def workspace_digest(root: Path) -> str:
    return hashlib.sha256(
        os.path.realpath(root).encode("utf-8", "surrogateescape")
    ).hexdigest()


def lock_path_for(root: Path) -> Path:
    """The lock file for the workspace at `root`. Creates the containing
    directory, never the file.

    Keyed by the sha256 of the workspace's REAL path, so the same workspace
    reached by two spellings -- notably through a symlinked root, which #926
    deliberately still allows -- resolves to one lock rather than two that
    cannot see each other.
    """
    return _lock_dir() / f"{workspace_digest(root)}.lock"


@contextlib.contextmanager
def _hold(path: Path) -> Iterator[Path]:
    """Hold the exclusive non-blocking lock on one lock file for the block.

    Raises `WorkspaceBusyError` immediately on contention.

    Exactly ONE byte (offset 0) is locked, because that is the intersection of
    what `fcntl.flock` and `msvcrt.locking` both express portably; the file's
    contents are irrelevant and stay empty.
    """
    try:
        fd = os.open(
            path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise WorkspaceLockUnavailableError(
            f"cannot open the lock file {path} ({exc.strerror or exc}); nothing "
            f"was read or written by this run -- remove {path} and try again"
        ) from exc
    try:
        try:
            _acquire_exclusive_nonblocking(fd)
        except OSError as exc:
            raise WorkspaceBusyError(BUSY_REASON) from exc
        try:
            yield path
        finally:
            # PROVABLY REDUNDANT ON POSIX, and kept anyway. Closing the
            # descriptor below already drops the `flock`, so a mutation that
            # deletes this line survives the whole suite -- do not go looking
            # for the test that should have caught it, and do not delete the
            # line on that evidence either. It is here for Windows: the CRT
            # does not document `msvcrt.locking` as released on close, this
            # repo has no Windows CI to settle it (#929), and an unreleased
            # lock there would wedge a workspace until reboot. An untestable
            # line guarding an unverifiable platform is the trade being made
            # deliberately; `os.close` is the release that POSIX actually uses.
            _release(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def workspace_lock(root: Path) -> Iterator[Path]:
    """Hold this workspace's exclusive mutation lock for the whole block.

    Takes the state-directory lock (non-blocking) and yields its path. Raises
    `WorkspaceBusyError` immediately if another process holds it. It is
    released on the way out of the block -- on success, on an exception, and on
    process death, the last of which is the kernel's doing rather than this
    code's.
    """
    with _hold(lock_path_for(root)) as path:
        yield path
