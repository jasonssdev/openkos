"""The judging run registry: one pollable job, held in memory.

A run goes on its own daemon thread, never on the request pool, so a run of
minutes holds no pool thread. `start` registers the run, starts the thread and
waits for admission: the judging service's first stop check, which comes after
its stale-quote and refusal checks and before any model call. A stale quote or a
refusal therefore answers the start request itself, and the run is removed.

The registry is lost on restart by design: per-chunk persistence keeps the paid
work. Nothing here imports the framework, so the registry is tested with no HTTP.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Final

from openkos import api
from openkos.localapi._errors import (
    NOT_FOUND,
    WRITE_WAIT_SECONDS,
    ApiProblem,
    ErrorBody,
    error_body,
    outcome_problem,
    run_active,
)
from openkos.localapi._models import JudgingOutcomeBody, RunStatus

# How long a start waits for admission (seconds); an unmeasured placeholder.
ADMISSION_TIMEOUT_SECONDS: Final = 10.0
# How long shutdown waits for the active run's thread (seconds).
SHUTDOWN_GRACE_SECONDS: Final = 5.0
# Finished and failed runs kept for polling; older ones are evicted.
FINISHED_RUNS_KEPT: Final = 16

_THREAD_NAME: Final = "openkos-judging-run"


class _AdmissionStop(api.StopToken):
    """A stop token whose first check also signals admission.

    The service checks it before each chunk, so the first check proves the quote
    was current and the run is about to spend."""

    def __init__(self, on_admitted: threading.Event) -> None:
        super().__init__()
        self._on_admitted = on_admitted
        self.admitted = False

    def is_set(self) -> bool:
        self.admitted = True
        self._on_admitted.set()
        return super().is_set()


@dataclass
class _Run:
    run_id: str
    kind: api.JudgingKind
    stop: _AdmissionStop
    ready: threading.Event
    thread: threading.Thread | None = None
    state: str = "running"
    index: int | None = None
    total: int | None = None
    persisted: int = 0
    outcome: JudgingOutcomeBody | None = None
    error: ErrorBody | None = None
    problem: ApiProblem | None = None
    """What a start request answers if the run ends before admission."""

    settled: threading.Event = field(default_factory=threading.Event)


class _Observer(api.JudgingObserver):
    def __init__(self, registry: RunRegistry, run: _Run) -> None:
        self._registry = registry
        self._run = run

    def progress(self, event: api.JudgingProgress) -> None:
        with self._registry._lock:
            self._run.index = event.index
            self._run.total = event.total

    def chunk_persisted(self, persisted: int) -> None:
        with self._registry._lock:
            self._run.persisted = persisted


def _outcome_body(outcome: api.JudgingOutcome) -> JudgingOutcomeBody:
    failure = outcome.failure
    return JudgingOutcomeBody(
        kind=outcome.kind,
        status=outcome.status,
        judged=outcome.judged,
        calls=outcome.calls,
        persisted=outcome.persisted,
        unpersisted=outcome.unpersisted,
        unpersisted_cause=outcome.unpersisted_cause,
        dropped=outcome.dropped,
        deferred=outcome.deferred,
        not_in_quote=list(outcome.not_in_quote),
        settled_since_quote=outcome.settled_since_quote,
        failure=error_body(failure)[1] if failure is not None else None,
        failed_at=outcome.failed_at,
        observer_failed=outcome.observer_failed,
    )


class RunRegistry:
    """At most one active run, and the newest finished ones for polling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, _Run] = {}
        self._finished: list[str] = []

    # -- public ---------------------------------------------------------------- #

    def start(self, workspace: api.Workspace, token: api.QuoteToken) -> RunStatus:
        """Start a run and return once it is admitted or the timeout elapsed.

        Raises `ApiProblem`: 409 `run_active` while another run is active, and the
        run's own refusal (409 `quote_stale`, 422 `run_refused`, or the mapped
        exception) when it ends before admission."""
        ready = threading.Event()
        run = _Run(
            run_id=uuid.uuid4().hex,
            kind=token.binding.kind,
            stop=_AdmissionStop(ready),
            ready=ready,
        )
        with self._lock:
            if any(r.state in ("running", "stopping") for r in self._runs.values()):
                raise run_active()
            self._runs[run.run_id] = run
            thread = threading.Thread(
                target=self._execute,
                args=(run, workspace, token),
                daemon=True,
                name=_THREAD_NAME,
            )
            run.thread = thread
            thread.start()
        ready.wait(ADMISSION_TIMEOUT_SECONDS)
        with self._lock:
            if run.problem is not None and not run.stop.admitted:
                self._runs.pop(run.run_id, None)
                if run.run_id in self._finished:
                    self._finished.remove(run.run_id)
                raise run.problem
            return self._snapshot(run)

    def status(self, run_id: str) -> RunStatus:
        """The run as a poll sees it. Raises 404 `not_found` for an unknown id."""
        with self._lock:
            return self._snapshot(self._get(run_id))

    def stop(self, run_id: str) -> RunStatus:
        """Ask a run to stop at its next chunk boundary.

        A finished or failed run is returned unchanged."""
        with self._lock:
            run = self._get(run_id)
            if run.state == "running":
                run.state = "stopping"
                run.stop.set()
            return self._snapshot(run)

    def shutdown(self, grace: float) -> None:
        """Set every stop token and wait at most `grace` for the active threads.

        Returns either way: a model call in flight is abandoned with its daemon
        thread, and no lock is held across a model call."""
        with self._lock:
            active = [
                r for r in self._runs.values() if r.state in ("running", "stopping")
            ]
            for run in active:
                run.state = "stopping"
                run.stop.set()
        deadline = time.monotonic() + grace
        for run in active:
            if run.thread is not None:
                run.thread.join(max(0.0, deadline - time.monotonic()))

    # -- internals ------------------------------------------------------------- #

    def _get(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            raise ApiProblem(
                404,
                ErrorBody(code=NOT_FOUND, retryable=False, message="no such run"),
            )
        return run

    def _snapshot(self, run: _Run) -> RunStatus:
        return RunStatus(
            run_id=run.run_id,
            kind=run.kind,
            state=run.state,  # type: ignore[arg-type]
            index=run.index,
            total=run.total,
            persisted=run.persisted,
            outcome=run.outcome,
            error=run.error,
        )

    def _execute(
        self, run: _Run, workspace: api.Workspace, token: api.QuoteToken
    ) -> None:
        problem: ApiProblem | None = None
        outcome: JudgingOutcomeBody | None = None
        try:
            result = api.run_judging(
                workspace,
                token,
                stop=run.stop,
                observer=_Observer(self, run),
                wait_seconds=WRITE_WAIT_SECONDS,
            )
            if isinstance(result, api.QuoteStale | api.RunRefused):
                problem = outcome_problem(result)
            else:
                outcome = _outcome_body(result)
        except Exception as exc:  # noqa: BLE001 -- mapped to a fixed-message body
            status, body, headers = error_body(exc)
            problem = ApiProblem(status, body, headers)
        with self._lock:
            if problem is not None:
                run.problem = problem
                run.error = problem.body
                run.state = "failed"
            else:
                run.outcome = outcome
                run.state = "finished"
            self._finished.append(run.run_id)
            while len(self._finished) > FINISHED_RUNS_KEPT:
                self._runs.pop(self._finished.pop(0), None)
        run.settled.set()
        run.ready.set()
