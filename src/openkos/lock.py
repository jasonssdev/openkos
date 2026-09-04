"""Advisory interprocess workspace lock (#925).

A leaf module: it imports nothing from `openkos`, mirroring `fsio.py`, so it
can be used from `config`, `cli`, or a future application service without
creating a layering dependency.

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
"""

import contextlib
import hashlib
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

LOCK_DIR_PREFIX = "openkos-locks"
"""Per-user directory under the OS temp dir holding one lock file per workspace.

**The lock lives OUTSIDE the workspace, deliberately.** The obvious home is
`<root>/.openkos/workspace.lock`, and it was the first design. It is wrong for
a reason worth recording, because it looks right: this repo's refusal tests
assert that a refusing command leaves the workspace byte- and structure-
identical, and `tests/unit/cli/conftest.py`'s snapshot records DIRECTORIES too,
explicitly so a refusal that created a stray `.git` is caught. Creating
`.openkos/` and a lock file to then refuse breaks that guarantee literally --
96 tests went red -- and the honest reading is that they were right: a run that
refuses really should write nothing at all. Excluding the lock from the snapshot
would have traded a real product guarantee for a convenient one.

A lock is process rendezvous state, not workspace data. Keeping it out of the
tree also means a read-only or network-mounted workspace can still be locked,
and nothing new needs to be gitignored.

**The tradeoff being accepted:** if the OS clears its temp directory while a
lock is held, a second process creates a fresh file and does not see the first
one's lock. Temp reapers act on boot or on files idle for days; a lock here is
held for the duration of ONE command. `openkos` is a CLI, not a daemon, so that
window is not reachable in practice -- but it is the reason this is not simply
"the obviously correct place", and a long-lived MVP 3 adapter should revisit it.
"""


class WorkspaceBusyError(RuntimeError):
    """Another OpenKOS process holds this workspace's mutation lock.

    Carries the operator-facing sentence directly, so every call site reports
    the same wording without formatting one of its own.
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


def _lock_dir() -> Path:
    """The per-user lock directory, created on demand with owner-only access.

    `tempfile.gettempdir()` is already per-user on macOS (`/var/folders/...`)
    and Windows (`%LOCALAPPDATA%\\Temp`), but NOT on Linux, where `/tmp` is
    shared -- so the uid goes in the name there and the mode is `0o700`. Without
    that, one user could create the path first and either squat the name or make
    it unopenable for everyone else.
    """
    geteuid = getattr(os, "geteuid", None)
    suffix = "" if geteuid is None else f"-{geteuid()}"
    directory = Path(tempfile.gettempdir()) / f"{LOCK_DIR_PREFIX}{suffix}"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def lock_path_for(root: Path) -> Path:
    """The lock file for the workspace at `root`. Creates the containing
    directory, never the file.

    Keyed by the sha256 of the workspace's REAL path, so the same workspace
    reached by two spellings -- notably through a symlinked root, which #926
    deliberately still allows -- resolves to one lock rather than two that
    cannot see each other.
    """
    digest = hashlib.sha256(
        os.path.realpath(root).encode("utf-8", "surrogateescape")
    ).hexdigest()
    return _lock_dir() / f"{digest}.lock"


@contextlib.contextmanager
def workspace_lock(root: Path) -> Iterator[Path]:
    """Hold this workspace's exclusive mutation lock for the whole block.

    Raises `WorkspaceBusyError` immediately if another process holds it. The
    lock is released on the way out of the block -- on success, on an
    exception, and on process death, the last of which is the kernel's doing
    rather than this code's.

    Exactly ONE byte (offset 0) is locked, because that is the intersection of
    what `fcntl.flock` and `msvcrt.locking` both express portably; the file's
    contents are irrelevant and stay empty.
    """
    path = lock_path_for(root)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
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
