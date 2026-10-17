"""The model pull registry: one pollable download, held in memory.

A pull runs on its own daemon thread, never on the request pool, so a download
of minutes holds no pool thread. `start` registers the pull, starts the thread
and waits for admission: the setup service's first stop check, which comes after
its refusal and installed checks and before the stream starts. A refusal or an
unreachable runtime therefore answers the start request itself, and the pull is
removed. A model already installed finishes before admission and is returned as
finished.

The registry is lost on restart by design: Ollama keeps the partial download
and resumes it on the next pull. Nothing here imports the framework, so the
registry is tested with no HTTP. It mirrors the judging run registry
(`_runs.py`) rather than sharing it, so the shipped judging module is untouched.
"""

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Final

from openkos import api
from openkos.localapi._errors import (
    NOT_FOUND,
    ApiProblem,
    ErrorBody,
    error_body,
    outcome_problem,
    pull_active,
)
from openkos.localapi._models import PullStatus

# How long a start waits for admission (seconds); an unmeasured placeholder.
ADMISSION_TIMEOUT_SECONDS: Final = 10.0
# Finished and failed pulls kept for polling; older ones are evicted.
FINISHED_PULLS_KEPT: Final = 16

_THREAD_NAME: Final = "openkos-model-pull"


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
class _Pull:
    pull_id: str
    model: str
    stop: _AdmissionStop
    ready: threading.Event
    thread: threading.Thread | None = None
    state: str = "running"
    phase: api.PullPhase | None = None
    completed_bytes: int = 0
    total_bytes: int | None = None
    outcome: api.PullOutcome | None = None
    error: ErrorBody | None = None
    problem: ApiProblem | None = None
    """What a start request answers if the pull ends before admission."""

    settled: threading.Event = field(default_factory=threading.Event)


class _Observer(api.PullObserver):
    def __init__(self, registry: PullRegistry, pull: _Pull) -> None:
        self._registry = registry
        self._pull = pull

    def progress(self, event: api.PullProgress) -> None:
        with self._registry._lock:
            self._pull.phase = event.phase
            self._pull.completed_bytes = event.completed_bytes
            self._pull.total_bytes = event.total_bytes


class PullRegistry:
    """At most one active pull, and the newest finished ones for polling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pulls: dict[str, _Pull] = {}
        self._finished: list[str] = []

    # -- public ---------------------------------------------------------------- #

    def start(self, workspace: api.Workspace, model: str) -> PullStatus:
        """Start a pull and return once it is admitted, finished, or the
        timeout elapsed.

        Raises `ApiProblem`: 409 `pull_active` while another pull is active, and
        the pull's own refusal or mapped exception when it ends before
        admission."""
        ready = threading.Event()
        pull = _Pull(
            pull_id=uuid.uuid4().hex,
            model=model,
            stop=_AdmissionStop(ready),
            ready=ready,
        )
        with self._lock:
            if any(p.state in ("running", "stopping") for p in self._pulls.values()):
                raise pull_active()
            self._pulls[pull.pull_id] = pull
            thread = threading.Thread(
                target=self._execute,
                args=(pull, workspace),
                daemon=True,
                name=_THREAD_NAME,
            )
            pull.thread = thread
            thread.start()
        ready.wait(ADMISSION_TIMEOUT_SECONDS)
        with self._lock:
            if pull.problem is not None and not pull.stop.admitted:
                self._pulls.pop(pull.pull_id, None)
                if pull.pull_id in self._finished:
                    self._finished.remove(pull.pull_id)
                raise pull.problem
            return self._snapshot(pull)

    def status(self, pull_id: str) -> PullStatus:
        """The pull as a poll sees it. Raises 404 `not_found` for an unknown id."""
        with self._lock:
            return self._snapshot(self._get(pull_id))

    def stop(self, pull_id: str) -> PullStatus:
        """Ask a pull to stop after its next progress line.

        A finished or failed pull is returned unchanged."""
        with self._lock:
            pull = self._get(pull_id)
            if pull.state == "running":
                pull.state = "stopping"
                pull.stop.set()
            return self._snapshot(pull)

    def shutdown(self, grace: float) -> None:
        """Set every stop token and wait at most `grace` for the active threads.

        Returns either way: a download blocked on a read is abandoned with its
        daemon thread, and Ollama resumes it on the next pull."""
        with self._lock:
            active = [
                p for p in self._pulls.values() if p.state in ("running", "stopping")
            ]
            for pull in active:
                pull.state = "stopping"
                pull.stop.set()
        deadline = time.monotonic() + grace
        for pull in active:
            if pull.thread is not None:
                pull.thread.join(max(0.0, deadline - time.monotonic()))

    # -- internals ------------------------------------------------------------- #

    def _get(self, pull_id: str) -> _Pull:
        pull = self._pulls.get(pull_id)
        if pull is None:
            raise ApiProblem(
                404,
                ErrorBody(code=NOT_FOUND, retryable=False, message="no such pull"),
            )
        return pull

    def _snapshot(self, pull: _Pull) -> PullStatus:
        return PullStatus(
            pull_id=pull.pull_id,
            model=pull.model,
            state=pull.state,  # type: ignore[arg-type]
            phase=pull.phase,
            completed_bytes=pull.completed_bytes,
            total_bytes=pull.total_bytes,
            outcome=pull.outcome,
            error=pull.error,
        )

    def _execute(self, pull: _Pull, workspace: api.Workspace) -> None:
        problem: ApiProblem | None = None
        outcome: api.PullOutcome | None = None
        try:
            result = api.pull_model(
                workspace,
                pull.model,
                stop=pull.stop,
                observer=_Observer(self, pull),
            )
            if isinstance(result, api.PullRefused):
                problem = outcome_problem(result)
            else:
                outcome = result
        except Exception as exc:  # noqa: BLE001 -- mapped to a fixed-message body
            status, body, headers = error_body(exc)
            problem = ApiProblem(status, body, headers)
        with self._lock:
            if problem is not None:
                pull.problem = problem
                pull.error = problem.body
                pull.state = "failed"
            else:
                pull.outcome = outcome
                if outcome is not None:
                    pull.completed_bytes = outcome.completed_bytes
                    pull.total_bytes = outcome.total_bytes
                pull.state = "finished"
            self._finished.append(pull.pull_id)
            while len(self._finished) > FINISHED_PULLS_KEPT:
                self._pulls.pop(self._finished.pop(0), None)
        pull.settled.set()
        pull.ready.set()
