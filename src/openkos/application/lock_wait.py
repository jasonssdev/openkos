"""Bounded-wait acquisition of the workspace lock, and the commit-section port.

`lock.workspace_lock` is deliberately non-blocking (ADR-0020 Decision Five): it
refuses at once on contention. A caller that is willing to wait supplies the
policy itself, and this module is that policy -- a retry loop with jittered
exponential backoff around the primitive, bounded by a deadline the caller
chose. The interactive CLI passes `--wait`; the unattended runner passes its job
deadline (ADR-0036 Decision Six).

`CommitSection` is the port a service enters around its commit phase. The CLI
hands it a section that takes the lock under the `--wait` policy, the runner one
with its own policy, and tests `contextlib.nullcontext`.
"""

import contextlib
import random
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path

from openkos import lock

CommitSection = Callable[[], AbstractContextManager[None]]
"""Entered around a service's commit phase; holds the workspace lock inside."""

MAX_WAIT_SECONDS = 3600
"""The documented ceiling for `--wait`; a larger value is a usage error."""

INITIAL_BACKOFF_SECONDS = 0.05
MAX_BACKOFF_SECONDS = 1.0


@contextlib.contextmanager
def acquire_with_backoff(
    root: Path,
    *,
    wait_seconds: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
    on_wait: Callable[[], None] | None = None,
) -> Iterator[Path]:
    """Hold the workspace lock, retrying contention for up to `wait_seconds`.

    Only `WorkspaceBusyError` is retried: an unusable lock directory refuses
    again until it is fixed, so it propagates at once. When the bound expires
    the last `WorkspaceBusyError` propagates, unchanged, so the caller refuses
    exactly as it would without a wait. `wait_seconds=0` is a single attempt.
    `on_wait` fires once, when the first attempt finds the lock busy and a
    retry is about to begin. An error raised by the BODY is never retried: only
    acquisition sits inside the retry loop.
    """
    deadline = clock() + wait_seconds
    delay = INITIAL_BACKOFF_SECONDS
    announced = False
    with contextlib.ExitStack() as stack:
        while True:
            try:
                held = stack.enter_context(lock.workspace_lock(root))
            except lock.WorkspaceBusyError:
                remaining = deadline - clock()
                if remaining <= 0:
                    raise
                if not announced and on_wait is not None:
                    on_wait()
                announced = True
                sleep(min(jitter(delay / 2, delay), remaining))
                delay = min(delay * 2, MAX_BACKOFF_SECONDS)
            else:
                yield held
                return


def locked_commit_section(
    root: Path,
    *,
    wait_seconds: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
    on_wait: Callable[[], None] | None = None,
) -> CommitSection:
    """Build the `CommitSection` that takes the lock with a bounded wait."""

    @contextlib.contextmanager
    def section() -> Iterator[None]:
        with acquire_with_backoff(
            root,
            wait_seconds=wait_seconds,
            clock=clock,
            sleep=sleep,
            jitter=jitter,
            on_wait=on_wait,
        ):
            yield

    return section
