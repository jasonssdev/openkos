"""The search rebuild registry: one pollable rebuild, held in memory.

A rebuild runs on its own daemon thread, never on the request pool. `start`
registers it, starts the thread and waits for admission: the rebuild's first
stop check, which comes after its consent check and before any page is sent.
A refusal therefore answers the start request itself, and the rebuild is
removed. A rebuild with nothing to send may finish before admission is
signalled; it is returned as finished.

The registry is lost on restart by design: a rebuild writes nothing until its
end, so a lost one leaves search as it was. It mirrors the pull registry
(`_pulls.py`) rather than sharing it, so the shipped modules are untouched.
Nothing here imports the framework, so the registry is tested with no HTTP.
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
    rebuild_active,
)
from openkos.localapi._models import RebuildStatus

# How long a start waits for admission (seconds); an unmeasured placeholder.
ADMISSION_TIMEOUT_SECONDS: Final = 10.0
# Settled rebuilds kept for polling; older ones are evicted.
FINISHED_REBUILDS_KEPT: Final = 16

_THREAD_NAME: Final = "openkos-search-rebuild"


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
class _Rebuild:
    rebuild_id: str
    stop: _AdmissionStop
    ready: threading.Event
    thread: threading.Thread | None = None
    state: str = "running"
    phase: api.RebuildPhase | None = None
    done: int = 0
    total: int = 0
    outcome: api.RebuildOutcome | None = None
    error: ErrorBody | None = None
    problem: ApiProblem | None = None
    """What a start request answers if the rebuild ends before admission."""

    settled: threading.Event = field(default_factory=threading.Event)


class _Observer:
    def __init__(self, registry: RebuildRegistry, rebuild: _Rebuild) -> None:
        self._registry = registry
        self._rebuild = rebuild

    def progress(self, event: api.RebuildProgress) -> None:
        with self._registry._lock:
            self._rebuild.phase = event.phase
            if event.phase == "embedding":
                self._rebuild.done = event.done
                self._rebuild.total = event.total


class RebuildRegistry:
    """At most one active rebuild, and the newest settled ones for polling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rebuilds: dict[str, _Rebuild] = {}
        self._finished: list[str] = []

    # -- public ---------------------------------------------------------------- #

    def start(self, workspace: api.Workspace, *, allow_remote: bool) -> RebuildStatus:
        """Start a rebuild and return once it is admitted, settled, or the
        timeout elapsed.

        Raises `ApiProblem`: 409 `rebuild_active` while another rebuild is
        active, and the rebuild's own refusal or mapped exception when it ends
        before admission."""
        ready = threading.Event()
        rebuild = _Rebuild(
            rebuild_id=uuid.uuid4().hex, stop=_AdmissionStop(ready), ready=ready
        )
        with self._lock:
            if any(r.state in ("running", "stopping") for r in self._rebuilds.values()):
                raise rebuild_active()
            self._rebuilds[rebuild.rebuild_id] = rebuild
            thread = threading.Thread(
                target=self._execute,
                args=(rebuild, workspace, allow_remote),
                daemon=True,
                name=_THREAD_NAME,
            )
            rebuild.thread = thread
            thread.start()
        ready.wait(ADMISSION_TIMEOUT_SECONDS)
        with self._lock:
            if rebuild.problem is not None and not rebuild.stop.admitted:
                self._rebuilds.pop(rebuild.rebuild_id, None)
                if rebuild.rebuild_id in self._finished:
                    self._finished.remove(rebuild.rebuild_id)
                raise rebuild.problem
            return self._snapshot(rebuild)

    def status(self, rebuild_id: str) -> RebuildStatus:
        """The rebuild as a poll sees it. Raises 404 `not_found` for an unknown
        id."""
        with self._lock:
            return self._snapshot(self._get(rebuild_id))

    def stop(self, rebuild_id: str) -> RebuildStatus:
        """Ask a rebuild to stop after the page in flight. A settled rebuild is
        returned unchanged."""
        with self._lock:
            rebuild = self._get(rebuild_id)
            if rebuild.state == "running":
                rebuild.state = "stopping"
                rebuild.stop.set()
            return self._snapshot(rebuild)

    def shutdown(self, grace: float) -> None:
        """Set every stop token and wait at most `grace` for the active threads.

        Returns either way: a rebuild blocked on a model call is abandoned with
        its daemon thread, and it writes nothing once stopped."""
        with self._lock:
            active = [
                r for r in self._rebuilds.values() if r.state in ("running", "stopping")
            ]
            for rebuild in active:
                rebuild.state = "stopping"
                rebuild.stop.set()
        deadline = time.monotonic() + grace
        for rebuild in active:
            if rebuild.thread is not None:
                rebuild.thread.join(max(0.0, deadline - time.monotonic()))

    # -- internals ------------------------------------------------------------- #

    def _get(self, rebuild_id: str) -> _Rebuild:
        rebuild = self._rebuilds.get(rebuild_id)
        if rebuild is None:
            raise ApiProblem(
                404,
                ErrorBody(code=NOT_FOUND, retryable=False, message="no such rebuild"),
            )
        return rebuild

    def _snapshot(self, rebuild: _Rebuild) -> RebuildStatus:
        return RebuildStatus(
            rebuild_id=rebuild.rebuild_id,
            state=rebuild.state,  # type: ignore[arg-type]
            phase=rebuild.phase,
            done=rebuild.done,
            total=rebuild.total,
            outcome=rebuild.outcome,
            error=rebuild.error,
        )

    def _execute(
        self, rebuild: _Rebuild, workspace: api.Workspace, allow_remote: bool
    ) -> None:
        problem: ApiProblem | None = None
        result: api.RebuildOutcome | api.RebuildStopped | None = None
        try:
            returned = api.rebuild_search(
                workspace,
                allow_remote=allow_remote,
                stop=rebuild.stop,
                observer=_Observer(self, rebuild),
            )
            if isinstance(returned, api.RebuildRefused):
                problem = outcome_problem(returned)
            else:
                result = returned
        except Exception as exc:  # noqa: BLE001 -- mapped to a fixed-message body
            status, body, headers = error_body(exc)
            problem = ApiProblem(status, body, headers)
        with self._lock:
            if problem is not None:
                rebuild.problem = problem
                rebuild.error = problem.body
                rebuild.state = "failed"
            elif isinstance(result, api.RebuildStopped):
                rebuild.state = "stopped"
            else:
                rebuild.outcome = result
                rebuild.state = "finished"
            self._finished.append(rebuild.rebuild_id)
            while len(self._finished) > FINISHED_REBUILDS_KEPT:
                self._rebuilds.pop(self._finished.pop(0), None)
        rebuild.settled.set()
        rebuild.ready.set()
