"""Unit tests for `lock.py`: the advisory interprocess workspace lock (#925).

The drift guards protect ONE process's read -> confirm -> write window. They
are blind to a second process: two concurrent mutators each pass their own
drift comparison against their own snapshot, and the later write silently
discards the earlier one. These tests pin the lock that serializes them.

Contention is exercised with a real second PROCESS, never a second handle in
this one. `fcntl.flock` is associated with the open file description, so a
same-process second `open()` does contend -- but that is an implementation
detail of the primitive, not the property #925 is about, and a test that
proved only the same-process case would pass just as happily against a
threading lock that fixes nothing.
"""

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from openkos import lock, userstate

pytestmark = pytest.mark.cross_platform_smoke
"""Selects this whole module into the reduced macOS/Windows CI job (#929).

Module-level because EVERY test here is exactly the property that job
exists for: `lock.py` branches on `sys.platform` (`fcntl.flock` vs
`msvcrt.locking`), and `_release`'s own comment names this repo's Windows
gap directly -- "this repo has no Windows CI to settle it (#929)". Linux CI
has run only the `fcntl` branch since #925 landed; the `msvcrt` branch has
never executed anywhere but a contributor's own machine.
"""

# A child that acquires the lock, announces it, and then holds it until its
# stdin closes -- so the parent controls the window with no sleeps and no
# timing assumptions.
_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from openkos import lock, userstate

    userstate.locks_dir = lambda *a, **k: Path(sys.argv[2])

    with lock.workspace_lock(Path(sys.argv[1])):
        print("ACQUIRED", flush=True)
        sys.stdin.read()
    print("RELEASED", flush=True)
    """
)

# A child that tries to acquire and reports which way it went, so the parent
# reads an explicit verdict rather than inferring one from an exit code.
_CONTENDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from openkos import lock, userstate

    userstate.locks_dir = lambda *a, **k: Path(sys.argv[2])

    try:
        with lock.workspace_lock(Path(sys.argv[1])):
            print("ACQUIRED", flush=True)
    except lock.WorkspaceBusyError:
        print("BUSY", flush=True)
    """
)


# `S603` is suppressed on both spawns below. The argv is a fixed list built
# here -- `sys.executable`, `-c`, a module-level literal script, and pytest's
# own `tmp_path` -- with no shell and no caller-supplied input. A real second
# PROCESS is not incidental to these tests; it is the property #925 is about,
# and it cannot be faked in-process.
def _spawn(script: str, root: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script, str(root), str(userstate.locks_dir())],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def _run_contender(root: Path) -> str:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CONTENDER, str(root), str(userstate.locks_dir())],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_lock_never_touches_the_workspace_tree(tmp_path: Path) -> None:
    """Nothing is written inside the workspace -- not even `.openkos/`.

    This is the property that sent the lock out of the tree. A refusing command
    must leave the workspace byte- and structure-identical, and this repo's
    refusal snapshots record directories too; a lock file created on the way to
    a refusal broke 96 of them, and they were right to break.
    """
    (tmp_path / "marker").write_text("x", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())

    with lock.workspace_lock(tmp_path):
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not (tmp_path / ".openkos").exists()


def test_two_spellings_of_one_workspace_share_a_lock(tmp_path: Path) -> None:
    """A symlinked ROOT is still one workspace (#926 deliberately allows it), so
    both spellings must map to the SAME lock -- two would not see each other,
    which is the exact lost update #925 is about."""
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real)

    assert lock.lock_path_for(real) == lock.lock_path_for(linked)


def test_distinct_workspaces_get_distinct_locks(tmp_path: Path) -> None:
    """Without this, `test_two_spellings...` would pass against a single global
    lock that serializes every workspace on the machine."""
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()

    assert lock.lock_path_for(a) != lock.lock_path_for(b)


def test_acquiring_creates_the_lock_file(tmp_path: Path) -> None:
    """The rendezvous file itself is created on demand, outside the workspace."""
    with lock.workspace_lock(tmp_path) as path:
        assert path.is_file()
        assert tmp_path not in path.parents

    assert lock.lock_path_for(tmp_path).is_file()


def test_the_lock_file_stays_empty(tmp_path: Path) -> None:
    """No pid, no timestamp, no owner -- deliberately.

    Recorded state is what goes stale: a reused pid makes a staleness check
    refuse a free workspace, and a too-eager cleanup makes it grant a busy one.
    With no content, a `workspace.lock` left behind by a crash is inert, and
    deleting it by hand is never part of recovery.

    `Path.stat().st_size` checks emptiness WHILE the lock is held, rather
    than `Path.read_bytes()` (#929). What was OBSERVED, in the first Windows
    CI run this repository ever had, is the traceback below -- note which
    line raised:

        with self.open(mode='rb') as f:
    >       return f.read()
        E   PermissionError: [Errno 13] Permission denied

    The `open()` SUCCEEDED; the read of the locked bytes is what failed.
    That is consistent with `lock.py`'s Windows branch taking
    `msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)` -- a one-byte mandatory range
    lock at offset 0, which Windows honours even against the holding
    process, and which applies past EOF on an empty file. POSIX's
    `fcntl.flock` is advisory by contrast, so the owner reads freely and
    `read_bytes()` here was never testing anything platform-specific.

    Stated as observation plus its most likely cause, deliberately: the
    traceback is evidence, the `msvcrt` range-lock explanation is inference,
    and `lock.py`'s locking backend is not part of this candidate. If that
    inference is ever falsified the choice below still stands on the
    traceback alone.

    `stat()` reads directory metadata and never reads file content, so it
    observes size on both platforms without touching the locked range,
    while still proving what this test is about: nothing is ever written to
    the file. The stronger byte-exact `read_bytes() == b""` check runs AFTER
    release below, where reading is unlocked everywhere.
    """
    with lock.workspace_lock(tmp_path) as path:
        assert path.stat().st_size == 0

    assert lock.lock_path_for(tmp_path).read_bytes() == b""


def test_a_second_process_is_refused_while_the_lock_is_held(tmp_path: Path) -> None:
    """The property #925 is about: concurrent mutators are serialized."""
    holder = _spawn(_HOLDER, tmp_path)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"

        assert _run_contender(tmp_path) == "BUSY"
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_a_second_process_succeeds_once_the_lock_is_released(tmp_path: Path) -> None:
    """The refusal is transient, not a latch: nothing has to be cleaned up.

    Without this, `test_a_second_process_is_refused...` would pass just as well
    against a lock that is never released -- which would wedge the workspace
    permanently after the first command.
    """
    holder = _spawn(_HOLDER, tmp_path)
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ACQUIRED"
    assert holder.stdin is not None
    holder.stdin.close()
    assert holder.wait(timeout=60) == 0

    assert _run_contender(tmp_path) == "ACQUIRED"


def test_the_lock_survives_nothing_when_the_holder_is_killed(tmp_path: Path) -> None:
    """A `SIGKILL`ed holder leaves the workspace usable.

    This is the whole reason the lock is a kernel lock rather than a pidfile:
    the kernel drops it when the process dies, however it dies, so there is no
    stale-lock recovery path to get wrong. A pidfile implementation passes
    every other test in this module and fails this one.
    """
    holder = _spawn(_HOLDER, tmp_path)
    assert holder.stdout is not None
    assert holder.stdout.readline().strip() == "ACQUIRED"

    holder.kill()
    holder.wait(timeout=60)

    assert _run_contender(tmp_path) == "ACQUIRED"
    assert lock.lock_path_for(tmp_path).exists(), (
        "the file is expected to remain -- it is inert, and its presence must "
        "not be what a later acquire keys on"
    )


def test_the_lock_is_released_when_the_block_raises(tmp_path: Path) -> None:
    """An exception inside a mutating verb must not wedge the workspace."""
    with pytest.raises(ValueError, match="boom"), lock.workspace_lock(tmp_path):
        raise ValueError("boom")

    assert _run_contender(tmp_path) == "ACQUIRED"


def test_busy_error_carries_the_operator_facing_reason(tmp_path: Path) -> None:
    """One wording, owned here, so no call site formats its own."""
    holder = _spawn(_HOLDER, tmp_path)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"

        with (
            pytest.raises(lock.WorkspaceBusyError) as excinfo,
            lock.workspace_lock(tmp_path),
        ):
            pass

        message = str(excinfo.value)
        assert message == lock.BUSY_REASON
        assert "another OpenKOS process" in message
        assert "nothing was read or written" in message
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


_FD_LEAK_PROBE = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from openkos import lock, userstate

    userstate.locks_dir = lambda *a, **k: Path(sys.argv[2])

    def count():
        for c in (Path(f"/proc/{os.getpid()}/fd"), Path("/dev/fd")):
            if c.is_dir():
                return len(list(c.iterdir()))
        return None

    root = Path(sys.argv[1])
    before = count()
    if before is None:
        print("UNOBSERVABLE", flush=True)
        raise SystemExit(0)
    for _ in range(20):
        try:
            with lock.workspace_lock(root):
                pass
        except lock.WorkspaceBusyError:
            pass
        else:
            print("NOT_CONTENDED", flush=True)
            raise SystemExit(0)
    print(f"{before} {count()}", flush=True)
    """
)


def test_no_file_descriptor_is_leaked_across_acquires(tmp_path: Path) -> None:
    """Every acquire closes its descriptor, including the REFUSED ones.

    The refused path is the one that matters: it raises out of the middle of
    the function, so only the outer `finally` closes it. `openkos` is a CLI, so
    one leak per run is invisible -- but the same primitive is what a
    long-lived adapter (MVP 3's API/MCP server) would call per request, where a
    leak per refused request exhausts the process.

    The count is taken in a DEDICATED subprocess, not here. Counting this
    process's descriptors made the test pass alone and fail in the full suite:
    the suite leaves SQLite connections unclosed (#927) and their garbage
    collection closes descriptors at unpredictable moments, so `before` and
    `after` were measuring the whole suite's churn rather than this lock's.
    A fresh interpreter has a quiet descriptor table and no such noise.
    """
    holder = _spawn(_HOLDER, tmp_path)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"

        probe = subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-c",
                _FD_LEAK_PROBE,
                str(tmp_path),
                str(userstate.locks_dir()),
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[2],
            timeout=60,
        )
        assert probe.returncode == 0, probe.stderr
        verdict = probe.stdout.strip()
        if verdict == "UNOBSERVABLE":
            pytest.skip("open descriptors are not observable on this platform")
        assert verdict != "NOT_CONTENDED", (
            "the probe acquired the lock, so it never exercised the REFUSED "
            "path this test is about"
        )

        before, after = (int(part) for part in verdict.split())
        assert after == before
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


# --- the lock directory is trusted only when it is ours and owner-only (#1134) ---

_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="owner/mode/symlink checks on the lock directory are POSIX-only",
)


def _isolated_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return the state-directory `locks` path the lock will use (the suite's
    autouse fixture already points it at a private per-test location), and
    move the LEGACY temp-dir lookup somewhere private too."""
    legacy_tmp = tmp_path.parent / f"{tmp_path.name}-legacy-tmp"
    legacy_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(legacy_tmp))
    return userstate.locks_dir()


@_POSIX_ONLY
def test_a_fresh_lock_directory_is_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = _isolated_tmp(tmp_path, monkeypatch)
    old = os.umask(0o000)  # a permissive umask must not widen the directory
    try:
        with lock.workspace_lock(tmp_path):
            pass
    finally:
        os.umask(old)

    assert stat.S_IMODE(expected.stat().st_mode) == 0o700


@_POSIX_ONLY
@pytest.mark.parametrize("mode", [0o755, 0o750, 0o707])
def test_a_group_or_world_accessible_lock_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    directory = _isolated_tmp(tmp_path, monkeypatch)
    directory.mkdir()
    directory.chmod(mode)

    with (
        pytest.raises(lock.WorkspaceLockUnavailableError) as excinfo,
        lock.workspace_lock(tmp_path),
    ):
        pytest.fail("the lock must not be taken in an untrusted directory")

    message = str(excinfo.value)
    assert str(directory) in message
    assert f"{mode:o}" in message
    assert "chmod 700" in message


@_POSIX_ONLY
def test_a_lock_directory_owned_by_someone_else_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cannot chown in a test, so the process claims to be a different user:
    # the directory the lock computes is then named for, but not owned by, it.
    directory = _isolated_tmp(tmp_path, monkeypatch)
    directory.mkdir(mode=0o700)
    other_uid = os.geteuid() + 1
    monkeypatch.setattr(os, "geteuid", lambda: other_uid)

    with (
        pytest.raises(lock.WorkspaceLockUnavailableError) as excinfo,
        lock.workspace_lock(tmp_path),
    ):
        pytest.fail("the lock must not be taken in an untrusted directory")

    assert "not owned by you" in str(excinfo.value)
    assert str(directory) in str(excinfo.value)


@_POSIX_ONLY
def test_a_symlinked_lock_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _isolated_tmp(tmp_path, monkeypatch)
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    directory.symlink_to(target)

    with (
        pytest.raises(lock.WorkspaceLockUnavailableError) as excinfo,
        lock.workspace_lock(tmp_path),
    ):
        pytest.fail("the lock must not be taken through a symlink")

    assert "is a symlink" in str(excinfo.value)
    assert list(target.iterdir()) == []  # nothing was created through the link


@_POSIX_ONLY
def test_a_lock_file_that_is_a_symlink_is_refused_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _isolated_tmp(tmp_path, monkeypatch)
    directory.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep")
    lock_file = lock.lock_path_for(tmp_path)
    lock_file.symlink_to(victim)

    with (
        pytest.raises(lock.WorkspaceLockUnavailableError),
        lock.workspace_lock(tmp_path),
    ):
        pytest.fail("the lock must not follow a planted symlink")

    assert victim.read_bytes() == b"keep"


@_POSIX_ONLY
def test_an_unopenable_lock_file_is_a_clean_refusal_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _isolated_tmp(tmp_path, monkeypatch)
    directory.mkdir(mode=0o700)
    lock_file = lock.lock_path_for(tmp_path)
    lock_file.write_bytes(b"")
    lock_file.chmod(0o000)
    if os.access(lock_file, os.R_OK):  # running as root: mode bits do not bind
        pytest.skip("mode 000 does not stop this user from opening the file")

    with (
        pytest.raises(lock.WorkspaceLockUnavailableError) as excinfo,
        lock.workspace_lock(tmp_path),
    ):
        pytest.fail("unreachable")

    assert str(lock_file) in str(excinfo.value)


# --- the lock lives in the per-user state directory, not the OS temp dir (ADR-0036) ---

# An "older openkos": holds ONLY the legacy temp-directory lock, the way a
# release before the relocation does.
_LEGACY_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from openkos import lock

    with lock._hold(lock.legacy_lock_path_for(Path(sys.argv[1]))):
        print("ACQUIRED", flush=True)
        sys.stdin.read()
    """
)


def test_the_lock_file_lives_in_the_state_directory_locks_dir(
    tmp_path: Path,
) -> None:
    """Named by the sha256 of the real path, inside `userstate.locks_dir()`,
    and nowhere under the OS temp directory."""
    import hashlib

    digest = hashlib.sha256(os.path.realpath(tmp_path).encode()).hexdigest()
    with lock.workspace_lock(tmp_path) as path:
        assert path == userstate.locks_dir() / f"{digest}.lock"
        assert path.is_file()
        assert Path(tempfile.gettempdir()) not in path.parents


@_POSIX_ONLY
def test_missing_state_directories_are_created_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `locks` directory AND the OpenKOS state directory above it are
    created owner-only, even under a permissive umask."""
    state = tmp_path.parent / f"{tmp_path.name}-state" / "openkos"
    monkeypatch.setattr(userstate, "locks_dir", lambda *a, **k: state / "locks")
    old = os.umask(0o000)
    try:
        with lock.workspace_lock(tmp_path):
            pass
    finally:
        os.umask(old)

    assert stat.S_IMODE((state / "locks").stat().st_mode) == 0o700
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


def test_a_symlinked_workspace_root_shares_one_lock_across_processes(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real)
    holder = _spawn(_HOLDER, real)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"

        assert _run_contender(linked) == "BUSY"
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_an_older_openkos_holding_only_the_legacy_lock_still_excludes(
    tmp_path: Path,
) -> None:
    holder = _spawn(_LEGACY_HOLDER, tmp_path)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"
        # Precondition: the legacy holder never touched the state-directory lock.
        assert not lock.lock_path_for(tmp_path).exists()

        with pytest.raises(lock.WorkspaceBusyError), lock.workspace_lock(tmp_path):
            pytest.fail("the legacy lock was held; this must refuse")
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_a_refusal_for_the_legacy_lock_leaves_the_state_lock_free(
    tmp_path: Path,
) -> None:
    """Contention on the second lock must release the first, or one refused run
    would wedge the workspace for every newer process."""
    holder = _spawn(_LEGACY_HOLDER, tmp_path)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "ACQUIRED"
        with pytest.raises(lock.WorkspaceBusyError), lock.workspace_lock(tmp_path):
            pass

        with lock._hold(lock.lock_path_for(tmp_path)):
            pass  # the state-directory lock was released by the refused run
    finally:
        assert holder.stdin is not None
        holder.stdin.close()
        holder.wait(timeout=60)


def test_a_held_acquisition_holds_both_locks_and_releases_both(
    tmp_path: Path,
) -> None:
    with lock.workspace_lock(tmp_path):
        for path in (
            lock.lock_path_for(tmp_path),
            lock.legacy_lock_path_for(tmp_path),
        ):
            with pytest.raises(lock.WorkspaceBusyError), lock._hold(path):
                pass

    for path in (lock.lock_path_for(tmp_path), lock.legacy_lock_path_for(tmp_path)):
        with lock._hold(path):
            pass


@_POSIX_ONLY
def test_an_untrusted_legacy_lock_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_tmp = tmp_path.parent / f"{tmp_path.name}-legacy"
    legacy_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(legacy_tmp))
    directory = legacy_tmp / f"{lock.LEGACY_LOCK_DIR_PREFIX}-{os.geteuid()}"
    directory.mkdir()
    directory.chmod(0o755)

    with (
        pytest.raises(lock.WorkspaceLockUnavailableError) as excinfo,
        lock.workspace_lock(tmp_path),
    ):
        pytest.fail("unreachable")

    assert str(directory) in str(excinfo.value)
