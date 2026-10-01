"""The bounded-wait lock acquisition and the commit-section port (#1137)."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from openkos import lock
from openkos.application import lock_wait


class _Clock:
    """A fake monotonic clock whose `sleep` advances it, so no test waits."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _busy_for(
    monkeypatch: pytest.MonkeyPatch, attempts_before_free: int | None
) -> list[int]:
    """Make `lock.workspace_lock` busy for N attempts (None = forever)."""
    calls: list[int] = []

    @contextmanager
    def fake(root: Path) -> Iterator[Path]:
        calls.append(1)
        if attempts_before_free is None or len(calls) <= attempts_before_free:
            raise lock.WorkspaceBusyError(lock.BUSY_REASON)
        yield root

    monkeypatch.setattr(lock, "workspace_lock", fake)
    return calls


def test_a_free_lock_is_taken_without_sleeping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    calls = _busy_for(monkeypatch, 0)

    with lock_wait.acquire_with_backoff(
        tmp_path, wait_seconds=5, clock=clock, sleep=clock.sleep
    ) as held:
        assert held == tmp_path

    assert len(calls) == 1
    assert clock.sleeps == []


def test_zero_wait_is_a_single_attempt_that_never_sleeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    calls = _busy_for(monkeypatch, None)

    with (
        pytest.raises(lock.WorkspaceBusyError),
        lock_wait.acquire_with_backoff(
            tmp_path, wait_seconds=0, clock=clock, sleep=clock.sleep
        ),
    ):
        pytest.fail("the body must not run")

    assert len(calls) == 1
    assert clock.sleeps == []


def test_a_wait_retries_until_the_holder_releases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    calls = _busy_for(monkeypatch, 3)
    waiting: list[int] = []

    with lock_wait.acquire_with_backoff(
        tmp_path,
        wait_seconds=30,
        clock=clock,
        sleep=clock.sleep,
        jitter=lambda low, high: high,
        on_wait=lambda: waiting.append(1),
    ):
        pass

    assert len(calls) == 4
    assert len(clock.sleeps) == 3
    # Backoff grows: each pause is at least the previous one.
    assert clock.sleeps == sorted(clock.sleeps)
    assert clock.sleeps[-1] > clock.sleeps[0]
    # The caller is told exactly once, however many retries followed.
    assert waiting == [1]


def test_the_bound_is_respected_and_the_busy_error_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    _busy_for(monkeypatch, None)
    start = clock.now

    with (
        pytest.raises(lock.WorkspaceBusyError),
        lock_wait.acquire_with_backoff(
            tmp_path,
            wait_seconds=3,
            clock=clock,
            sleep=clock.sleep,
            jitter=lambda low, high: high,
        ),
    ):
        pytest.fail("the body must not run")

    # Never sleeps past the deadline: the last pause is clamped to what is left.
    assert clock.now - start == pytest.approx(3.0)
    assert all(s > 0 for s in clock.sleeps)


def test_jitter_is_drawn_within_its_range_and_the_draw_is_what_sleeps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    _busy_for(monkeypatch, 2)
    ranges: list[tuple[float, float]] = []

    def jitter(low: float, high: float) -> float:
        ranges.append((low, high))
        return low  # the carried value, distinguishable from `high`

    with lock_wait.acquire_with_backoff(
        tmp_path, wait_seconds=30, clock=clock, sleep=clock.sleep, jitter=jitter
    ):
        pass

    assert len(ranges) == 2
    for low, high in ranges:
        assert 0 < low < high <= lock_wait.MAX_BACKOFF_SECONDS
    assert clock.sleeps == [low for low, _ in ranges]


def test_an_unavailable_lock_directory_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only contention is worth waiting for; a re-run refuses again otherwise."""
    clock = _Clock()
    calls: list[int] = []

    @contextmanager
    def fake(root: Path) -> Iterator[Path]:
        calls.append(1)
        if calls:
            raise lock.WorkspaceLockUnavailableError("nope")
        yield root

    monkeypatch.setattr(lock, "workspace_lock", fake)

    with (
        pytest.raises(lock.WorkspaceLockUnavailableError),
        lock_wait.acquire_with_backoff(
            tmp_path, wait_seconds=30, clock=clock, sleep=clock.sleep
        ),
    ):
        pytest.fail("the body must not run")

    assert len(calls) == 1
    assert clock.sleeps == []


def test_an_error_raised_by_the_body_is_not_mistaken_for_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    calls = _busy_for(monkeypatch, 0)

    with (
        pytest.raises(lock.WorkspaceBusyError),
        lock_wait.acquire_with_backoff(
            tmp_path, wait_seconds=30, clock=clock, sleep=clock.sleep
        ),
    ):
        raise lock.WorkspaceBusyError("raised by the body")

    assert len(calls) == 1
    assert clock.sleeps == []


def test_the_commit_section_factory_returns_a_reentrant_free_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The port a service enters around its commit phase: each entry takes the
    lock afresh, honouring the wait bound it was built with."""
    clock = _Clock()
    calls = _busy_for(monkeypatch, 1)
    section = lock_wait.locked_commit_section(
        tmp_path, wait_seconds=10, clock=clock, sleep=clock.sleep
    )

    with section():
        pass
    with section():
        pass

    assert len(calls) == 3  # one retry on the first entry, none on the second
