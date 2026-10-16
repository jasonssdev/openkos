"""The judging run registry: admission, polling, stop and shutdown.

Tested with no HTTP. `api.run_judging` is replaced by a stub gated on events, so
every interleaving is forced by an event and no test sleeps to win a race.
"""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from openkos import api
from openkos.localapi import _runs
from openkos.localapi._errors import ApiProblem, ErrorBody, error_body
from openkos.localapi._models import RunStatus
from openkos.localapi._runs import RunRegistry

WAIT = 10.0
"""A bound on every wait, so a defect fails the test instead of hanging it."""


def make_token() -> api.QuoteToken:
    binding = api.QuoteBinding(
        kind="identity",
        model="m",
        backend="ollama",
        is_local=True,
        effective_confidential=False,
        rubric_digest=None,
    )
    return api.QuoteToken(
        binding=binding,
        include_confidential=False,
        include_uncertain=False,
        work=(),
        calls=0,
    )


def make_outcome(**changes: Any) -> api.JudgingOutcome:
    fields: dict[str, Any] = {
        "kind": "identity",
        "status": "completed",
        "judged": 2,
        "calls": 2,
        "persisted": 2,
        "unpersisted": 0,
        "unpersisted_cause": None,
        "dropped": 0,
        "deferred": 0,
        "not_in_quote": (),
        "settled_since_quote": 0,
    }
    fields.update(changes)
    return api.JudgingOutcome(**fields)


class Stub:
    """A replacement for `api.run_judging` that a test steers with events."""

    def __init__(self) -> None:
        self.calls = 0
        self.stop: api.StopToken | None = None
        self.observer: api.JudgingObserver | None = None
        self.wait_seconds: float | None = None
        self.entered = threading.Event()
        self.admit = threading.Event()
        self.release = threading.Event()
        self.before_admission: Callable[[], Any] | None = None
        self.after_admission: Callable[[], Any] | None = None
        self.observed_stop: list[bool] = []

    def __call__(
        self,
        workspace: api.Workspace,
        token: api.QuoteToken | None,
        *,
        stop: api.StopToken | None = None,
        observer: api.JudgingObserver | None = None,
        chat_client: object = None,
        wait_seconds: float = 0.0,
    ) -> Any:
        self.calls += 1
        self.stop = stop
        self.observer = observer
        self.wait_seconds = wait_seconds
        self.entered.set()
        if self.before_admission is not None:
            result = self.before_admission()
            if result is not None:
                return result
        assert stop is not None
        stop.is_set()  # the service's first stop check: admission
        self.admit.set()
        assert self.release.wait(WAIT)
        self.observed_stop.append(stop.is_set())
        if self.after_admission is not None:
            return self.after_admission()
        return make_outcome(status="stopped" if stop.is_set() else "completed")


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[Stub]:
    fake = Stub()
    monkeypatch.setattr(api, "run_judging", fake)
    yield fake
    fake.release.set()  # never leave a run thread parked


@pytest.fixture
def registry() -> RunRegistry:
    return RunRegistry()


def finish(stub: Stub, registry: RunRegistry, run_id: str) -> api.JudgingKind:
    """Let the stub return and wait until the registry has recorded it."""
    stub.release.set()
    deadline = time.monotonic() + WAIT
    while registry.status(run_id).state in ("running", "stopping"):
        assert time.monotonic() < deadline
        threading.Event().wait(0.005)
    return registry.status(run_id).kind


def started(registry: RunRegistry, stub: Stub, workspace: api.Workspace) -> RunStatus:
    status = registry.start(workspace, make_token())
    assert stub.admit.is_set()
    return status


# -- start ----------------------------------------------------------------------- #


def test_start_returns_a_running_status_once_the_service_is_admitted(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)

    assert status.state == "running"
    assert status.kind == "identity"
    assert (status.index, status.total, status.persisted) == (None, None, 0)
    assert status.outcome is None
    assert status.error is None
    assert stub.calls == 1
    assert stub.wait_seconds == 5.0


def test_start_hands_the_service_the_admission_stop_and_an_observer(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    started(registry, stub, workspace)

    assert isinstance(stub.stop, _runs._AdmissionStop)
    assert isinstance(stub.observer, api.JudgingObserver)


def test_run_ids_are_distinct_hex_strings(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    first = started(registry, stub, workspace)
    finish(stub, registry, first.run_id)
    stub.admit.clear()
    stub.release.clear()
    second = started(registry, stub, workspace)

    assert first.run_id != second.run_id
    assert len(first.run_id) == 32
    assert int(first.run_id, 16) >= 0


def test_a_stale_quote_before_admission_is_409_and_removes_the_run(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    stub.before_admission = lambda: api.QuoteStale(
        kind="identity", changed=("model", "locality")
    )

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, make_token())

    assert caught.value.status == 409
    assert caught.value.body.code == "quote_stale"
    assert caught.value.body.retryable is False
    assert "model, locality" in caught.value.body.message
    # the run is gone: a new start is not blocked by it
    stub.before_admission = None
    assert started(registry, stub, workspace).state == "running"


def test_a_refused_run_is_422_run_refused_and_removes_the_run(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    stub.before_admission = lambda: api.RunRefused(reason="no_quote_token")

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, make_token())

    assert caught.value.status == 422
    assert caught.value.body.code == "run_refused"
    stub.before_admission = None
    assert started(registry, stub, workspace).state == "running"


def test_an_exception_before_admission_maps_through_the_table(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    exc = api.QueueUnavailableError("boom")

    def raise_it() -> None:
        raise exc

    stub.before_admission = raise_it

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, make_token())

    expected_status, expected_body, _ = error_body(exc)
    assert caught.value.status == expected_status == 503
    assert caught.value.body == expected_body
    stub.before_admission = None
    assert started(registry, stub, workspace).state == "running"


def test_an_exception_after_admission_leaves_the_run_failed_with_an_error(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    def raise_it() -> None:
        raise api.QueueUnavailableError("boom")

    stub.after_admission = raise_it
    status = started(registry, stub, workspace)
    finish(stub, registry, status.run_id)

    final = registry.status(status.run_id)
    assert final.state == "failed"
    assert final.outcome is None
    assert final.error == error_body(api.QueueUnavailableError("boom"))[1]


def test_a_returned_outcome_makes_the_run_finished_with_a_body(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    failure = api.BackendUnavailable("down")
    stub.after_admission = lambda: make_outcome(
        status="failed",
        judged=1,
        persisted=1,
        deferred=3,
        not_in_quote=("a", "b"),
        failure=failure,
        failed_at=2,
    )
    status = started(registry, stub, workspace)
    finish(stub, registry, status.run_id)

    final = registry.status(status.run_id)
    assert final.state == "finished"
    assert final.error is None
    body = final.outcome
    assert body is not None
    assert (body.kind, body.status) == ("identity", "failed")
    assert (body.judged, body.persisted, body.deferred) == (1, 1, 3)
    assert body.not_in_quote == ["a", "b"]
    assert body.failed_at == 2
    assert body.failure == error_body(failure)[1]
    assert body.observer_failed is False


def test_a_successful_outcome_carries_no_failure(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    finish(stub, registry, status.run_id)

    body = registry.status(status.run_id).outcome
    assert body is not None
    assert body.status == "completed"
    assert body.failure is None


def test_an_outcome_returned_before_admission_is_still_a_started_run(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    stub.before_admission = lambda: make_outcome(judged=0, calls=0, persisted=0)

    status = registry.start(workspace, make_token())

    assert status.state == "finished"
    assert status.outcome is not None
    assert status.outcome.judged == 0


# -- one active run -------------------------------------------------------------- #


def test_a_second_start_while_running_is_409_run_active_and_retryable(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    started(registry, stub, workspace)

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, make_token())

    assert caught.value.status == 409
    assert caught.value.body.code == "run_active"
    assert caught.value.body.retryable is True
    assert stub.calls == 1


def test_a_second_start_while_stopping_is_409_run_active(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    registry.stop(status.run_id)

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, make_token())

    assert caught.value.body.code == "run_active"


def test_a_second_start_during_admission_is_409_run_active(
    stub: Stub,
    registry: RunRegistry,
    workspace: api.Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = threading.Event()
    stub.before_admission = lambda: gate.wait(WAIT) and None
    first: list[Any] = []
    worker = threading.Thread(
        target=lambda: first.append(registry.start(workspace, make_token())),
        daemon=True,
    )
    worker.start()
    assert stub.entered.wait(WAIT)  # the first start is inside admission

    with pytest.raises(ApiProblem) as caught:
        registry.start(workspace, make_token())
    gate.set()
    worker.join(WAIT)

    assert caught.value.body.code == "run_active"
    assert first
    assert first[0].state == "running"


def test_after_finishing_another_start_is_accepted(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    first = started(registry, stub, workspace)
    finish(stub, registry, first.run_id)
    stub.admit.clear()
    stub.release.clear()

    second = started(registry, stub, workspace)

    assert second.state == "running"
    assert second.run_id != first.run_id
    assert registry.status(first.run_id).state == "finished"


# -- admission timeout ----------------------------------------------------------- #


def test_the_admission_timeout_returns_running_and_a_late_stale_lands_in_error(
    stub: Stub,
    registry: RunRegistry,
    workspace: api.Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runs, "ADMISSION_TIMEOUT_SECONDS", 0.05)
    gate = threading.Event()
    stub.before_admission = lambda: (
        gate.wait(WAIT) and api.QuoteStale(kind="identity", changed=("rubric",))
    )

    status = registry.start(workspace, make_token())
    assert status.state == "running"
    gate.set()
    deadline = time.monotonic() + WAIT
    while registry.status(status.run_id).state == "running":
        assert time.monotonic() < deadline
        threading.Event().wait(0.005)

    final = registry.status(status.run_id)
    assert final.state == "failed"
    assert final.error is not None
    assert final.error.code == "quote_stale"
    assert "rubric" in final.error.message


# -- poll and stop --------------------------------------------------------------- #


def test_status_reflects_the_latest_progress_and_persisted_events(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    assert stub.observer is not None

    stub.observer.progress(
        api.JudgingProgress(index=1, total=4, work_key="k1", verdict="same")
    )
    stub.observer.chunk_persisted(1)
    stub.observer.progress(
        api.JudgingProgress(index=3, total=4, work_key="k3", verdict="different")
    )
    stub.observer.chunk_persisted(3)

    polled = registry.status(status.run_id)
    assert (polled.index, polled.total, polled.persisted) == (3, 4, 3)
    assert polled.state == "running"


def test_stop_sets_the_token_and_the_run_ends_finished_with_a_stopped_outcome(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    assert stub.stop is not None
    assert not stub.stop.is_set()

    stopping = registry.stop(status.run_id)

    assert stopping.state == "stopping"
    assert stub.stop.is_set()
    finish(stub, registry, status.run_id)
    final = registry.status(status.run_id)
    assert stub.observed_stop == [True]
    assert final.state == "finished"
    assert final.outcome is not None
    assert final.outcome.status == "stopped"


def test_stopping_twice_stays_stopping(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    registry.stop(status.run_id)

    assert registry.stop(status.run_id).state == "stopping"


def test_stopping_a_finished_run_returns_it_unchanged(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    finish(stub, registry, status.run_id)
    before = registry.status(status.run_id)

    assert registry.stop(status.run_id) == before
    assert before.state == "finished"


def test_stopping_a_failed_run_returns_it_unchanged(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    def raise_it() -> None:
        raise api.QueueUnavailableError("boom")

    stub.after_admission = raise_it
    status = started(registry, stub, workspace)
    finish(stub, registry, status.run_id)
    before = registry.status(status.run_id)

    assert registry.stop(status.run_id) == before
    assert before.state == "failed"


@pytest.mark.parametrize("operation", ["status", "stop"])
def test_an_unknown_run_id_is_404_not_found(
    registry: RunRegistry, operation: str
) -> None:
    with pytest.raises(ApiProblem) as caught:
        getattr(registry, operation)("0" * 32)

    assert caught.value.status == 404
    assert caught.value.body == ErrorBody(
        code="not_found", retryable=False, message="no such run"
    )


def test_eviction_keeps_the_newest_sixteen_finished_runs(
    stub: Stub,
    registry: RunRegistry,
    workspace: api.Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids: list[str] = []
    for _ in range(_runs.FINISHED_RUNS_KEPT + 2):
        stub.admit.clear()
        stub.release.clear()
        status = started(registry, stub, workspace)
        finish(stub, registry, status.run_id)
        ids.append(status.run_id)

    for evicted in ids[:2]:
        with pytest.raises(ApiProblem) as caught:
            registry.status(evicted)
        assert caught.value.status == 404
    for kept in ids[2:]:
        assert registry.status(kept).state == "finished"


def test_eviction_never_drops_the_active_run(
    stub: Stub,
    registry: RunRegistry,
    workspace: api.Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_runs, "FINISHED_RUNS_KEPT", 1)
    done: list[str] = []
    for _ in range(3):
        stub.admit.clear()
        stub.release.clear()
        status = started(registry, stub, workspace)
        finish(stub, registry, status.run_id)
        done.append(status.run_id)
    stub.admit.clear()
    stub.release.clear()
    active = started(registry, stub, workspace)

    assert registry.status(active.run_id).state == "running"
    assert registry.status(done[-1]).state == "finished"


def test_a_poll_does_not_reflect_the_runs_result_in_its_own_status(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    def raise_it() -> None:
        raise api.QueueUnavailableError("boom")

    stub.after_admission = raise_it
    status = started(registry, stub, workspace)
    finish(stub, registry, status.run_id)

    polled = registry.status(status.run_id)  # returns, never raises
    assert polled.state == "failed"
    assert polled.error is not None
    assert polled.error.code == "queue_unavailable"


# -- shutdown -------------------------------------------------------------------- #


def test_shutdown_with_no_active_run_returns_at_once(registry: RunRegistry) -> None:
    started_at = time.monotonic()

    registry.shutdown(WAIT)

    assert time.monotonic() - started_at < WAIT / 2


def test_shutdown_returns_within_the_grace_while_a_stub_stays_blocked(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    assert stub.observer is not None
    stub.observer.chunk_persisted(2)
    thread = registry._runs[status.run_id].thread
    assert thread is not None

    started_at = time.monotonic()
    registry.shutdown(0.2)
    elapsed = time.monotonic() - started_at

    assert elapsed < WAIT / 2  # the blocked stub did not hold it
    assert stub.stop is not None
    assert stub.stop.is_set()
    assert thread.is_alive()
    assert thread.daemon
    assert registry.status(status.run_id).persisted == 2
    assert registry.status(status.run_id).state == "stopping"


def test_shutdown_waits_for_a_run_that_finishes_inside_the_grace(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)
    timer = threading.Timer(0.05, stub.release.set)
    timer.start()

    registry.shutdown(WAIT)
    timer.join(WAIT)

    assert registry.status(status.run_id).state == "finished"


def test_the_run_thread_is_a_named_daemon(
    stub: Stub, registry: RunRegistry, workspace: api.Workspace
) -> None:
    status = started(registry, stub, workspace)

    thread = registry._runs[status.run_id].thread
    assert thread is not None
    assert thread.daemon
    assert thread.name == "openkos-judging-run"


def test_the_named_constants() -> None:
    assert _runs.ADMISSION_TIMEOUT_SECONDS == 10.0
    assert _runs.SHUTDOWN_GRACE_SECONDS == 5.0
    assert _runs.FINISHED_RUNS_KEPT == 16
