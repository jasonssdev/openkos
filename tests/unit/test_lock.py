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

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from openkos import lock

# A child that acquires the lock, announces it, and then holds it until its
# stdin closes -- so the parent controls the window with no sleeps and no
# timing assumptions.
_HOLDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from openkos import lock

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
    from openkos import lock

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
        [sys.executable, "-c", script, str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )


def _run_contender(root: Path) -> str:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CONTENDER, str(root)],
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
    """
    with lock.workspace_lock(tmp_path) as path:
        assert path.read_bytes() == b""

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
    from openkos import lock

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
            [sys.executable, "-c", _FD_LEAK_PROBE, str(tmp_path)],
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
