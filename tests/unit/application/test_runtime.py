"""Unit tests for `openkos.application.runtime` (MVP 4 unit 3.3): the stop
flag, the per-job deadline and the policy object prompts become. All time is
injected; nothing here installs a signal handler or touches a workspace.
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

from openkos.application import runtime
from openkos.application.consent import (
    BooleanConfirmation,
    ConfirmationRequest,
    TypedChallengeConfirmation,
)


class _Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# --- StopToken ---------------------------------------------------------


def test_stop_token_starts_clear_and_latches() -> None:
    token = runtime.StopToken()
    assert token.is_set() is False
    token.set()
    assert token.is_set() is True
    token.set()  # a second signal is harmless
    assert token.is_set() is True


def test_stop_token_set_from_another_thread_is_seen() -> None:
    token = runtime.StopToken()
    t = threading.Thread(target=token.set)
    t.start()
    t.join()
    assert token.is_set() is True


def test_stop_token_has_no_clear() -> None:
    """A stop is a latch: nothing re-arms it mid-run."""
    assert not hasattr(runtime.StopToken(), "clear")


def test_stop_token_set_acquires_no_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """`set()` runs in a signal handler on the main thread, possibly while
    that thread already holds a lock; any lock acquisition could deadlock.
    `threading.Event.set()` takes its Condition's non-reentrant lock, so
    make every Event/Condition entry explode and require `set()` to work."""

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("StopToken.set() touched a threading lock")

    token = runtime.StopToken()
    monkeypatch.setattr(threading.Condition, "__enter__", boom)
    monkeypatch.setattr(threading.Event, "set", boom)
    monkeypatch.setattr(threading.Event, "is_set", boom)
    token.set()
    assert token.is_set() is True


def test_stop_token_wait_returns_true_as_soon_as_set() -> None:
    token = runtime.StopToken()
    token.set()
    assert token.wait(10.0, sleep=lambda _s: pytest.fail("slept")) is True


def test_stop_token_wait_polls_until_timeout_on_injected_clock() -> None:
    clock = _Clock(0.0)
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    token = runtime.StopToken()
    assert token.wait(1.0, clock=clock, sleep=sleep, poll=0.4) is False
    assert sleeps == [0.4, 0.4, pytest.approx(0.2)]
    assert clock.now == pytest.approx(1.0)


def test_stop_token_wait_sees_a_set_made_during_the_wait() -> None:
    clock = _Clock(0.0)
    token = runtime.StopToken()

    def sleep(seconds: float) -> None:
        clock.now += seconds
        if clock.now >= 0.5:
            token.set()

    assert token.wait(60.0, clock=clock, sleep=sleep, poll=0.25) is True
    assert clock.now == pytest.approx(0.5)


# --- Deadline ----------------------------------------------------------


def test_deadline_counts_from_construction_by_injected_clock() -> None:
    clock = _Clock(100.0)
    deadline = runtime.Deadline(seconds=60, clock=clock)
    assert deadline.remaining() == 60.0
    assert deadline.expired() is False
    clock.now = 130.0
    assert deadline.remaining() == 30.0
    assert deadline.expired() is False


def test_deadline_expires_exactly_at_the_boundary() -> None:
    clock = _Clock(0.0)
    deadline = runtime.Deadline(seconds=60, clock=clock)
    clock.now = 59.999
    assert deadline.expired() is False
    clock.now = 60.0
    assert deadline.expired() is True


def test_deadline_remaining_never_negative() -> None:
    clock = _Clock(0.0)
    deadline = runtime.Deadline(seconds=10, clock=clock)
    clock.now = 500.0
    assert deadline.remaining() == 0.0


@pytest.mark.parametrize("seconds", [0, -1, float("nan")])
def test_deadline_rejects_non_positive_or_nan(seconds: float) -> None:
    with pytest.raises(ValueError, match="seconds"):
        runtime.Deadline(seconds=seconds)


def test_deadline_default_clock_is_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 7.0)
    deadline = runtime.Deadline(seconds=5)
    monkeypatch.setattr(time, "monotonic", lambda: 9.0)
    assert deadline.remaining() == 3.0


# --- check_halt --------------------------------------------------------


def test_check_halt_reports_nothing_when_clear() -> None:
    halt = runtime.check_halt(runtime.StopToken(), runtime.Deadline(60, clock=_Clock()))
    assert halt is None


def test_check_halt_reports_stopped() -> None:
    token = runtime.StopToken()
    token.set()
    assert runtime.check_halt(token, runtime.Deadline(60, clock=_Clock())) == "stopped"


def test_check_halt_reports_timed_out() -> None:
    clock = _Clock(0.0)
    deadline = runtime.Deadline(5, clock=clock)
    clock.now = 5.0
    assert runtime.check_halt(runtime.StopToken(), deadline) == "timed_out"


def test_check_halt_prefers_stopped_over_timed_out() -> None:
    clock = _Clock(0.0)
    deadline = runtime.Deadline(5, clock=clock)
    clock.now = 99.0
    token = runtime.StopToken()
    token.set()
    assert runtime.check_halt(token, deadline) == "stopped"


# --- UnattendedPolicy --------------------------------------------------

_WRITE_REQUESTS: list[ConfirmationRequest] = [
    BooleanConfirmation(prompt="Proceed?", bypass_flag=None, non_tty_refusal=None),
    BooleanConfirmation(prompt="Proceed?", bypass_flag="--auto", non_tty_refusal="x"),
    TypedChallengeConfirmation(
        prompt="Type it",
        expected="merge-all",
        supplying_flag="--confirm",
        non_tty_refusal="x",
        mismatch_abort="y",
    ),
]


@pytest.mark.parametrize("request_", _WRITE_REQUESTS)
def test_policy_declines_every_write_confirmation(
    request_: ConfirmationRequest,
) -> None:
    # Even a policy with unlimited spend must not approve a write.
    policy = runtime.UnattendedPolicy(calls_remaining=lambda: 10**9)
    assert policy.answer(request_) == "declined"


def test_policy_answers_spend_from_the_budget() -> None:
    remaining = {"n": 5}
    policy = runtime.UnattendedPolicy(calls_remaining=lambda: remaining["n"])
    assert policy.answer(runtime.SpendQuestion(estimated_calls=5)) == "approved"
    assert policy.answer(runtime.SpendQuestion(estimated_calls=6)) == "declined"
    remaining["n"] = 0
    assert policy.answer(runtime.SpendQuestion(estimated_calls=1)) == "declined"


def test_policy_spend_reads_the_budget_at_ask_time() -> None:
    """The answer follows the live remainder, not a construction snapshot."""
    seen: list[int] = []

    def remaining() -> int:
        seen.append(1)
        return 3

    policy = runtime.UnattendedPolicy(calls_remaining=remaining)
    assert seen == []
    policy.answer(runtime.SpendQuestion(estimated_calls=1))
    policy.answer(runtime.SpendQuestion(estimated_calls=1))
    assert len(seen) == 2


def test_policy_without_a_budget_treats_spend_as_unavailable() -> None:
    policy = runtime.UnattendedPolicy()
    assert policy.answer(runtime.SpendQuestion(estimated_calls=1)) == "unavailable"


def test_policy_unknown_question_is_unavailable_never_yes() -> None:
    policy = runtime.UnattendedPolicy(calls_remaining=lambda: 10**9)
    assert policy.answer("Proceed?") == "unavailable"
    assert policy.answer(None) == "unavailable"
    assert policy.answer(object()) == "unavailable"


def test_spend_question_rejects_negative_estimate() -> None:
    with pytest.raises(ValueError, match="estimated_calls"):
        runtime.SpendQuestion(estimated_calls=-1)


def test_unreadable_budget_is_unavailable_not_approved() -> None:
    def boom() -> int:
        raise RuntimeError("budget store unreadable")

    policy = runtime.UnattendedPolicy(calls_remaining=boom)
    assert policy.answer(runtime.SpendQuestion(estimated_calls=1)) == "unavailable"


# --- Layering ----------------------------------------------------------


def test_runtime_module_is_synchronous_and_cli_free() -> None:
    tree = ast.parse(Path(runtime.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    roots = {name.split(".")[0] for name in imported}
    assert not roots & {"typer", "rich", "asyncio", "signal"}
    assert not any(name.startswith("openkos.cli") for name in imported)
    assert not any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(tree))


def test_stop_token_wait_returns_early_when_woken_without_a_stop() -> None:
    clock = _Clock(0.0)

    def sleep(seconds: float) -> None:
        clock.now += seconds

    token = runtime.StopToken()
    woken = token.wait(
        60.0, clock=clock, sleep=sleep, poll=0.25, wake=lambda: clock.now >= 0.5
    )

    assert woken is False
    assert clock.now == pytest.approx(0.5)
    assert not token.is_set()
