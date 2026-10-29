"""The ingest run registry: one pollable job that adds the files a quote covered.

A run goes on its own daemon thread, never on the request pool, so a run of
minutes holds no pool thread. `start` registers the run, starts the thread and
waits for admission: the API's first stop check, which comes after its stale
and refusal checks and before any model call. A stale quote, a missing token or
a refusal therefore answers the start request itself, and the run is removed.

The registry reads no file: every path check is the API's. It is lost on
restart by design; what a run committed stays committed. Nothing here imports
the framework, so the registry is tested with no HTTP.
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
    ingest_active,
    outcome_problem,
)
from openkos.localapi._models import IngestJob

# How long a start waits for admission (seconds); the judging run's placeholder.
ADMISSION_TIMEOUT_SECONDS: Final = 10.0
# Finished and failed runs kept for polling; older ones are evicted.
FINISHED_RUNS_KEPT: Final = 16

_THREAD_NAME: Final = "openkos-ingest-run"


class _AdmissionStop(api.StopToken):
    """A stop token whose first check also signals admission."""

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
    stop: _AdmissionStop
    ready: threading.Event
    thread: threading.Thread | None = None
    state: str = "running"
    progress: api.IngestProgress | None = None
    outcome: api.IngestRunOutcome | None = None
    error: ErrorBody | None = None
    problem: ApiProblem | None = None
    """What a start request answers if the run ends before admission."""
    warnings: list[str] = field(default_factory=list)
    settled: threading.Event = field(default_factory=threading.Event)


class IngestRegistry:
    """At most one active ingest run, and the newest finished ones for polling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, _Run] = {}
        self._finished: list[str] = []

    def start(self, workspace: api.Workspace, token: api.IngestQuoteToken) -> IngestJob:
        """Start a run and return once it is admitted or has finished.

        Raises `ApiProblem`: 409 `ingest_active` while another run is active, and
        the run's own refusal (409 `quote_stale`, 422 `run_refused`, or the
        mapped exception) when it ends before admission."""
        ready = threading.Event()
        run = _Run(run_id=uuid.uuid4().hex, stop=_AdmissionStop(ready), ready=ready)
        with self._lock:
            if any(r.state in ("running", "stopping") for r in self._runs.values()):
                raise ingest_active()
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

    def status(self, run_id: str) -> IngestJob:
        """The run as a poll sees it. Raises 404 `not_found` for an unknown id."""
        with self._lock:
            return self._snapshot(self._get(run_id))

    def stop(self, run_id: str) -> IngestJob:
        """Ask a run to stop before its next file. A finished or failed run is
        returned unchanged."""
        with self._lock:
            run = self._get(run_id)
            if run.state == "running":
                run.state = "stopping"
                run.stop.set()
            return self._snapshot(run)

    def shutdown(self, grace: float) -> None:
        """Set every stop token and wait at most `grace` for the active threads."""
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

    def _get(self, run_id: str) -> _Run:
        run = self._runs.get(run_id)
        if run is None:
            raise ApiProblem(
                404,
                ErrorBody(code=NOT_FOUND, retryable=False, message="no such run"),
            )
        return run

    def _snapshot(self, run: _Run) -> IngestJob:
        progress = run.progress
        return IngestJob(
            run_id=run.run_id,
            state=run.state,  # type: ignore[arg-type]
            index=progress.index if progress is not None else None,
            total=progress.total if progress is not None else None,
            phase=progress.phase if progress is not None else None,
            calls=progress.calls if progress is not None else 0,
            parts_done=progress.parts_done if progress is not None else None,
            parts_total=progress.parts_total if progress is not None else None,
            outcome=run.outcome,
            warnings=list(run.warnings),
            error=run.error,
        )

    def _execute(
        self, run: _Run, workspace: api.Workspace, token: api.IngestQuoteToken
    ) -> None:
        def progress(event: api.IngestProgress) -> None:
            with self._lock:
                run.progress = event

        def warn(message: str) -> None:
            with self._lock:
                run.warnings.append(message)

        problem: ApiProblem | None = None
        outcome: api.IngestRunOutcome | None = None
        try:
            result = api.run_ingest(
                workspace,
                token,
                stop=run.stop,
                progress=progress,
                wait_seconds=WRITE_WAIT_SECONDS,
                on_warning=warn,
            )
            if isinstance(result, api.IngestQuoteStale | api.RunRefused):
                problem = outcome_problem(result)
            else:
                outcome = result
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
