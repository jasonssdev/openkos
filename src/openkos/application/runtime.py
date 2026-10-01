"""Cooperative-runtime primitives for unattended jobs (MVP 4, job-runtime).

Three small, synchronous building blocks the job runner composes:

- `StopToken` -- the cooperative stop flag a signal handler sets.
- `Deadline` -- a per-job wall-clock deadline on an injected monotonic clock.
- `UnattendedPolicy` -- the object every question a verb could ask a human
  becomes when no human is attached. It answers by data, never by prompting.

Everything here is cooperative: nothing preempts a unit of work (ADR-0021
Decision Three). Callers check `check_halt` between units and immediately
before a commit phase's write burst, never inside it. Installing signal
handlers belongs to the daemon, not to this module. No `typer`, no `rich`,
no `openkos.cli`, no `asyncio`.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from openkos.application.consent import (
    BooleanConfirmation,
    ConfirmationRequest,
    TypedChallengeConfirmation,
)

Halt = Literal["stopped", "timed_out"]
"""Why a job must start no new unit of work. `stopped` wins over
`timed_out` when both hold: an operator's explicit stop is the more
informative outcome to record."""

PolicyAnswer = Literal["approved", "declined", "unavailable"]
"""`unavailable` means the policy has no answer; it is never read as yes."""


def _monotonic() -> float:
    return time.monotonic()


class StopToken:
    """A latching cooperative stop flag, settable from a signal handler.

    The setter is deliberately lock-free. A Python signal handler runs on
    the main thread between bytecodes, possibly while that same thread is
    inside a lock-guarded section. `threading.Event.set()` takes its
    Condition's non-reentrant lock, so a handler calling it while the
    interrupted thread holds that lock (for example inside `Event.wait()`)
    would block on a lock its own thread owns and deadlock at shutdown.
    A single attribute store is atomic under the GIL and takes no lock, so
    `set()` is one such store and `is_set()` one load. There is no
    `clear()`: a stop is never re-armed mid-run, so a second signal is
    idempotent.

    `wait()` is a short poll on the flag rather than a blocking primitive,
    so an idle loop stays interruptible without any lock a handler could
    collide with.
    """

    def __init__(self) -> None:
        self._stopped = False

    def set(self) -> None:
        """Request a stop. Idempotent; takes no lock."""
        self._stopped = True

    def is_set(self) -> bool:
        return self._stopped

    def wait(
        self,
        timeout: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        poll: float = 0.1,
    ) -> bool:
        """Block up to `timeout` seconds; `True` as soon as the flag is set,
        `False` if the timeout elapses first. Clock and sleep are injectable."""
        now = clock if clock is not None else _monotonic
        nap = sleep if sleep is not None else time.sleep
        end = now() + timeout
        while not self._stopped:
            left = end - now()
            if left <= 0:
                return False
            nap(min(poll, left))
        return True


class Deadline:
    """A wall-clock budget that starts when the object is built.

    Uses a monotonic clock so a system clock change cannot fire or defer it.
    `clock` is injectable for tests. Checking it never interrupts anything;
    a unit already running is allowed to finish.
    """

    def __init__(
        self,
        seconds: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if math.isnan(seconds) or seconds <= 0:
            raise ValueError(f"seconds must be a positive number, got {seconds!r}")
        self._clock = clock if clock is not None else _monotonic
        self._seconds = float(seconds)
        self._expires_at = self._clock() + self._seconds

    def remaining(self) -> float:
        """Seconds left, never negative."""
        return max(0.0, self._expires_at - self._clock())

    def expired(self) -> bool:
        return self._clock() >= self._expires_at


def check_halt(stop: StopToken, deadline: Deadline) -> Halt | None:
    """The one check the runner makes between units and before a write
    burst: `"stopped"`, `"timed_out"`, or `None` to proceed."""
    if stop.is_set():
        return "stopped"
    if deadline.expired():
        return "timed_out"
    return None


@dataclass(frozen=True)
class SpendQuestion:
    """ "May this unit make `estimated_calls` model calls?" -- the spend
    confirmation as data. The budget, not a person, answers it."""

    estimated_calls: int

    def __post_init__(self) -> None:
        if self.estimated_calls < 0:
            raise ValueError(
                f"estimated_calls must be >= 0, got {self.estimated_calls}"
            )


class UnattendedPolicy:
    """Answers every question for a caller with no human attached.

    - Any write confirmation (`BooleanConfirmation`,
      `TypedChallengeConfirmation`) is `declined`, unconditionally: the
      runner never performs a consequential write (ADR-0037).
    - A `SpendQuestion` is answered from the live budget remainder read
      through `calls_remaining` at ask time; with no budget wired, or one
      that cannot be read, it is `unavailable`.
    - Anything else is `unavailable`. A question with no answer is never yes.
    """

    def __init__(self, *, calls_remaining: Callable[[], int] | None = None) -> None:
        self._calls_remaining = calls_remaining

    def answer(
        self, question: ConfirmationRequest | SpendQuestion | object
    ) -> PolicyAnswer:
        if isinstance(question, BooleanConfirmation | TypedChallengeConfirmation):
            return "declined"
        if isinstance(question, SpendQuestion):
            return self._answer_spend(question)
        return "unavailable"

    def _answer_spend(self, question: SpendQuestion) -> PolicyAnswer:
        if self._calls_remaining is None:
            return "unavailable"
        try:
            remaining = self._calls_remaining()
        except Exception:  # noqa: BLE001 -- an unreadable budget must never approve
            return "unavailable"
        return "approved" if question.estimated_calls <= remaining else "declined"
