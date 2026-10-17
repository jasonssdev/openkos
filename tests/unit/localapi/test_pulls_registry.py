"""The model pull registry: admission, polling, stop and shutdown.

Tested with no HTTP. `api.pull_model` is replaced by a stub gated on events, so
every interleaving is forced by an event and no test sleeps to win a race.
"""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from openkos import api
from openkos.localapi import _pulls
from openkos.localapi._errors import ApiProblem
from openkos.localapi._models import PullStatus
from openkos.localapi._pulls import PullRegistry

WAIT = 10.0
"""A bound on every wait, so a defect fails the test instead of hanging it."""

MODEL = "bge-m3"


class Stub:
    """A replacement for `api.pull_model` that a test steers with events."""

    def __init__(self) -> None:
        self.calls = 0
        self.stop: api.StopToken | None = None
        self.observer: api.PullObserver | None = None
        self.admit = threading.Event()
        self.release = threading.Event()
        self.before_admission: Callable[[], Any] | None = None
        self.after_admission: Callable[[], Any] | None = None

    def __call__(
        self,
        workspace: api.Workspace,
        model: str,
        *,
        stop: api.StopToken | None = None,
        observer: api.PullObserver | None = None,
    ) -> Any:
        self.calls += 1
        self.stop = stop
        self.observer = observer
        if self.before_admission is not None:
            return self.before_admission()
        assert stop is not None
        stop.is_set()  # the service's first stop check: admission
        self.admit.set()
        assert self.release.wait(WAIT)
        if self.after_admission is not None:
            return self.after_admission()
        if stop.is_set():
            return api.PullOutcome(model, "stopped", 10, 10)
        return api.PullOutcome(model, "completed", 10, 10)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stub]:
    fake = Stub()
    monkeypatch.setattr(api, "pull_model", fake)
    yield fake
    fake.release.set()  # never leave a pull thread parked


@pytest.fixture
def registry() -> PullRegistry:
    return PullRegistry()


def settle(stub: Stub, registry: PullRegistry, pull_id: str) -> PullStatus:
    stub.release.set()
    deadline = time.monotonic() + WAIT
    while registry.status(pull_id).state in ("running", "stopping"):
        assert time.monotonic() < deadline
        threading.Event().wait(0.005)
    return registry.status(pull_id)


def started(registry: PullRegistry, stub: Stub, workspace: api.Workspace) -> PullStatus:
    status = registry.start(workspace, MODEL)
    assert stub.admit.is_set()
    return status


def test_start_returns_a_running_status_once_admitted(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)

    assert status.state == "running"
    assert status.model == MODEL
    assert (status.phase, status.completed_bytes, status.total_bytes) == (None, 0, None)
    assert status.outcome is None
    assert status.error is None
    assert isinstance(stub.stop, _pulls._AdmissionStop)
    assert len(status.pull_id) == 32


def test_progress_is_visible_to_a_poll(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    assert stub.observer is not None

    stub.observer.progress(api.PullProgress(MODEL, "downloading", 40, 100))

    polled = registry.status(status.pull_id)
    assert (polled.phase, polled.completed_bytes, polled.total_bytes) == (
        "downloading",
        40,
        100,
    )


def test_a_poll_after_completion_carries_the_outcome(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)

    done = settle(stub, registry, status.pull_id)

    assert done.state == "finished"
    assert done.outcome == api.PullOutcome(MODEL, "completed", 10, 10)
    assert (done.completed_bytes, done.total_bytes) == (10, 10)


def test_a_stop_is_passed_to_the_pull(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)

    stopping = registry.stop(status.pull_id)
    done = settle(stub, registry, status.pull_id)

    assert stopping.state == "stopping"
    assert done.outcome is not None
    assert done.outcome.status == "stopped"


def test_stopping_a_finished_pull_returns_it_unchanged(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    done = settle(stub, registry, status.pull_id)

    assert registry.stop(status.pull_id) == done


def test_a_second_pull_while_one_is_active_is_409(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    started(registry, stub, workspace)

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, MODEL)

    assert caught.value.status == 409
    assert caught.value.body.code == "pull_active"
    assert caught.value.body.retryable is True
    assert stub.calls == 1


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("not_configured", "model_not_configured"),
        ("backend_cannot_pull", "pull_not_supported"),
    ],
)
def test_a_refusal_answers_the_start_and_leaves_no_pull(
    stub: Stub,
    registry: PullRegistry,
    workspace: api.Workspace,
    reason: str,
    code: str,
) -> None:
    stub.before_admission = lambda: api.PullRefused(model=MODEL, reason=reason)  # type: ignore[arg-type]

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, MODEL)

    assert caught.value.status == 422
    assert caught.value.body.code == code
    stub.before_admission = None
    assert started(registry, stub, workspace).state == "running"


def test_an_unreachable_runtime_answers_the_start(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    def refuse() -> None:
        raise api.BackendUnavailable("secret host detail")

    stub.before_admission = refuse

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, MODEL)

    assert caught.value.status == 503
    assert caught.value.body.code == "backend_unavailable"
    assert "secret" not in caught.value.body.message


def test_an_installed_model_finishes_before_admission(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    stub.before_admission = lambda: api.PullOutcome(MODEL, "already_installed", 0, None)

    status = registry.start(workspace, MODEL)

    assert status.state == "finished"
    assert status.outcome == api.PullOutcome(MODEL, "already_installed", 0, None)


def test_a_failure_after_admission_is_a_failed_pull_with_a_fixed_message(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    def fail() -> None:
        raise api.BackendError("disk full at /secret/path")

    stub.after_admission = fail
    status = started(registry, stub, workspace)

    done = settle(stub, registry, status.pull_id)

    assert done.state == "failed"
    assert done.outcome is None
    assert done.error is not None
    assert done.error.code == "backend_error"
    assert "secret" not in done.error.message


def test_an_unknown_pull_is_404(registry: PullRegistry) -> None:
    with pytest.raises(ApiProblem) as caught:
        registry.status("nope")
    assert caught.value.status == 404
    with pytest.raises(ApiProblem):
        registry.stop("nope")


def test_only_the_newest_finished_pulls_are_kept(
    stub: Stub,
    registry: PullRegistry,
    workspace: api.Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_pulls, "FINISHED_PULLS_KEPT", 2)
    stub.before_admission = lambda: api.PullOutcome(MODEL, "already_installed", 0, None)
    ids = [registry.start(workspace, MODEL).pull_id for _ in range(3)]

    with pytest.raises(ApiProblem):
        registry.status(ids[0])
    assert registry.status(ids[2]).state == "finished"


def test_shutdown_stops_the_active_pull_and_returns(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    assert stub.stop is not None

    stub.release.set()
    registry.shutdown(WAIT)

    assert stub.stop.is_set()
    assert registry.status(status.pull_id).state == "finished"


def test_shutdown_returns_within_the_grace_when_a_pull_is_stuck(
    stub: Stub, registry: PullRegistry, workspace: api.Workspace
) -> None:
    started(registry, stub, workspace)

    began = time.monotonic()
    registry.shutdown(0.05)

    assert time.monotonic() - began < WAIT
