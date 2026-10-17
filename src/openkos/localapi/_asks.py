"""The ask registry: one pollable ask job, held in memory.

An ask goes on its own daemon thread, never on the request pool, so a model
call of minutes holds no pool thread. `start` registers the ask, starts the
thread and returns at once; every failure, a workspace that cannot be read
included, is reported by the job's poll. An ask has no stop: the query service
has no point at which to observe one, and an abandoned ask writes nothing.

The answer is the one `openkos.api.ask` returned, already gated (ADR-0068);
nothing here adds to it. The registry is lost on restart by design. Nothing
here imports the framework, so the registry is tested with no HTTP.
"""

import threading
import uuid
from dataclasses import dataclass, field
from typing import Final

from openkos import api
from openkos.localapi._errors import (
    NOT_FOUND,
    ApiProblem,
    ErrorBody,
    ask_active,
    error_body,
)
from openkos.localapi._models import AskJob, AskRequest

# Finished and failed asks kept for polling; older ones are evicted.
FINISHED_ASKS_KEPT: Final = 16

_THREAD_NAME: Final = "openkos-ask"


@dataclass
class _Ask:
    ask_id: str
    state: str = "running"
    phase: api.AskProgress | None = None
    answer: api.Answer | None = None
    error: ErrorBody | None = None
    settled: threading.Event = field(default_factory=threading.Event)


class AskRegistry:
    """At most one active ask, and the newest finished ones for polling."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._asks: dict[str, _Ask] = {}
        self._finished: list[str] = []

    def start(self, workspace: api.Workspace, request: AskRequest) -> AskJob:
        """Start an ask and return its job at once.

        Raises `ApiProblem` 409 `ask_active` while another ask is running."""
        ask = _Ask(ask_id=uuid.uuid4().hex)
        with self._lock:
            if any(a.state == "running" for a in self._asks.values()):
                raise ask_active()
            self._asks[ask.ask_id] = ask
            threading.Thread(
                target=self._execute,
                args=(ask, workspace, request),
                daemon=True,
                name=_THREAD_NAME,
            ).start()
            return self._snapshot(ask)

    def status(self, ask_id: str) -> AskJob:
        """The ask as a poll sees it. Raises 404 `not_found` for an unknown id."""
        with self._lock:
            ask = self._asks.get(ask_id)
            if ask is None:
                raise ApiProblem(
                    404,
                    ErrorBody(code=NOT_FOUND, retryable=False, message="no such ask"),
                )
            return self._snapshot(ask)

    def _snapshot(self, ask: _Ask) -> AskJob:
        progress = ask.phase
        return AskJob(
            ask_id=ask.ask_id,
            state=ask.state,  # type: ignore[arg-type]
            phase=progress.phase if progress is not None else None,
            completed=progress.completed if progress is not None else None,
            total=progress.total if progress is not None else None,
            answer=ask.answer,
            error=ask.error,
        )

    def _progress(self, ask: _Ask, event: api.AskProgress) -> None:
        with self._lock:
            ask.phase = event

    def _ask(
        self, ask: _Ask, workspace: api.Workspace, request: AskRequest
    ) -> api.Answer:
        """`api.ask` through the module attribute, so a patch intercepts it; an
        omitted limit is left to the API's own default."""

        def progress(event: api.AskProgress) -> None:
            self._progress(ask, event)

        if request.limit is None:
            return api.ask(
                workspace,
                request.question,
                include_deprecated=request.include_deprecated,
                include_confidential=request.include_confidential,
                progress=progress,
            )
        return api.ask(
            workspace,
            request.question,
            limit=request.limit,
            include_deprecated=request.include_deprecated,
            include_confidential=request.include_confidential,
            progress=progress,
        )

    def _execute(
        self, ask: _Ask, workspace: api.Workspace, request: AskRequest
    ) -> None:
        answer: api.Answer | None = None
        error: ErrorBody | None = None
        try:
            answer = self._ask(ask, workspace, request)
        except Exception as exc:  # noqa: BLE001 -- mapped to a fixed-message body
            error = error_body(exc)[1]
        with self._lock:
            if error is not None:
                ask.error = error
                ask.state = "failed"
            else:
                ask.answer = answer
                ask.state = "finished"
            self._finished.append(ask.ask_id)
            while len(self._finished) > FINISHED_ASKS_KEPT:
                self._asks.pop(self._finished.pop(0), None)
        ask.settled.set()
