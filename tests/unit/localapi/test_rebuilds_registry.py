"""The search rebuild registry: admission, polling, stop and shutdown.

Tested with no HTTP. `api.rebuild_search` is replaced by a stub gated on
events, so every interleaving is forced by an event and no test sleeps to win a
race."""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from openkos import api
from openkos.localapi import _rebuilds
from openkos.localapi._errors import ApiProblem
from openkos.localapi._models import RebuildStatus
from openkos.localapi._rebuilds import RebuildRegistry

WAIT = 10.0
"""A bound on every wait, so a defect fails the test instead of hanging it."""

OUTCOME = api.RebuildOutcome(
    embedded=2, unchanged=1, removed=0, skipped=0, failed=0, withheld=0, complete=True
)


class Stub:
    """A replacement for `api.rebuild_search` that a test steers with events."""

    def __init__(self) -> None:
        self.calls = 0
        self.allow_remote: bool | None = None
        self.stop: api.StopToken | None = None
        self.observer: api.RebuildObserver | None = None
        self.admit = threading.Event()
        self.release = threading.Event()
        self.before_admission: Callable[[], Any] | None = None
        self.after_admission: Callable[[], Any] | None = None

    def __call__(
        self,
        workspace: api.Workspace,
        *,
        allow_remote: bool = False,
        stop: api.StopToken | None = None,
        observer: api.RebuildObserver | None = None,
    ) -> Any:
        self.calls += 1
        self.allow_remote = allow_remote
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
            return api.RebuildStopped()
        return OUTCOME


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stub]:
    fake = Stub()
    monkeypatch.setattr(api, "rebuild_search", fake)
    yield fake
    fake.release.set()  # never leave a rebuild thread parked


@pytest.fixture
def registry() -> RebuildRegistry:
    return RebuildRegistry()


def settle(stub: Stub, registry: RebuildRegistry, rebuild_id: str) -> RebuildStatus:
    stub.release.set()
    deadline = time.monotonic() + WAIT
    while registry.status(rebuild_id).state in ("running", "stopping"):
        assert time.monotonic() < deadline
        threading.Event().wait(0.005)
    return registry.status(rebuild_id)


def started(
    registry: RebuildRegistry,
    stub: Stub,
    workspace: api.Workspace,
    *,
    allow_remote: bool = False,
) -> RebuildStatus:
    status = registry.start(workspace, allow_remote=allow_remote)
    assert stub.admit.is_set()
    return status


def test_start_returns_a_running_status_once_admitted(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace, allow_remote=True)

    assert status.state == "running"
    assert (status.phase, status.done, status.total) == (None, 0, 0)
    assert status.outcome is None
    assert status.error is None
    assert stub.allow_remote is True
    assert isinstance(stub.stop, _rebuilds._AdmissionStop)
    assert len(status.rebuild_id) == 32


def test_progress_is_visible_to_a_poll(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    assert stub.observer is not None

    stub.observer.progress(api.RebuildProgress("embedding", 3, 7))
    polled = registry.status(status.rebuild_id)
    assert (polled.phase, polled.done, polled.total) == ("embedding", 3, 7)

    stub.observer.progress(api.RebuildProgress("finishing", 0, 0))
    polled = registry.status(status.rebuild_id)
    assert (polled.phase, polled.done, polled.total) == ("finishing", 3, 7)


def test_a_poll_after_completion_carries_the_outcome(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)

    done = settle(stub, registry, status.rebuild_id)

    assert done.state == "finished"
    assert done.outcome == OUTCOME


def test_a_stop_is_passed_to_the_rebuild_and_settles_as_stopped(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)

    stopping = registry.stop(status.rebuild_id)
    done = settle(stub, registry, status.rebuild_id)

    assert stopping.state == "stopping"
    assert done.state == "stopped"
    assert done.outcome is None


def test_stopping_a_finished_rebuild_returns_it_unchanged(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    done = settle(stub, registry, status.rebuild_id)

    assert registry.stop(status.rebuild_id) == done


def test_a_second_rebuild_while_one_is_active_is_409(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    started(registry, stub, workspace)

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, allow_remote=False)

    assert caught.value.status == 409
    assert caught.value.body.code == "rebuild_active"
    assert caught.value.body.retryable is True
    assert stub.calls == 1


def test_a_refusal_answers_the_start_and_leaves_no_rebuild(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    stub.before_admission = lambda: api.RebuildRefused("remote_not_allowed")

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, allow_remote=False)

    assert caught.value.status == 422
    assert caught.value.body.code == "remote_not_allowed"
    stub.before_admission = None
    assert started(registry, stub, workspace).state == "running"


def test_a_failure_after_admission_is_reported_on_the_poll(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    def fail() -> None:
        raise api.BackendUnavailable("secret host detail")

    stub.after_admission = fail
    status = started(registry, stub, workspace)

    done = settle(stub, registry, status.rebuild_id)

    assert done.state == "failed"
    assert done.error is not None
    assert done.error.code == "backend_unavailable"
    assert "secret" not in done.error.message


def test_an_unknown_id_is_404(registry: RebuildRegistry) -> None:
    for call in (registry.status, registry.stop):
        with pytest.raises(ApiProblem) as caught:
            call("nope")
        assert caught.value.status == 404


def test_shutdown_stops_an_active_rebuild(
    stub: Stub, registry: RebuildRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    stub.release.set()

    registry.shutdown(WAIT)

    assert registry.status(status.rebuild_id).state == "stopped"


def test_settled_rebuilds_beyond_the_kept_number_are_evicted(
    stub: Stub,
    registry: RebuildRegistry,
    workspace: api.Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_rebuilds, "FINISHED_REBUILDS_KEPT", 1)
    stub.release.set()
    first = started(registry, stub, workspace)
    settle(stub, registry, first.rebuild_id)
    stub.admit.clear()
    second = started(registry, stub, workspace)
    settle(stub, registry, second.rebuild_id)

    with pytest.raises(ApiProblem):
        registry.status(first.rebuild_id)
    assert registry.status(second.rebuild_id).state == "finished"
