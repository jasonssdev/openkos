"""The versioned router. Every operation calls `openkos.api` through the module
attribute, so a test that patches `openkos.api.<fn>` intercepts the call."""

import dataclasses
from importlib import metadata
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPBearer

from openkos import api
from openkos.localapi import _errors
from openkos.localapi._errors import ErrorBody, invoke
from openkos.localapi._models import (
    ApplyRequest,
    ApplyResponse,
    DecisionList,
    HealthBody,
    PreviewRequest,
    QuoteRequest,
    RefreshRequest,
    RunRequest,
    RunStatus,
)
from openkos.localapi._runs import RunRegistry

# Documents the scheme in the OpenAPI schema only: the guard enforces the token
# before any route runs, so this never rejects (`auto_error=False`).
_BEARER = HTTPBearer(scheme_name="BearerToken", auto_error=False)


def prefix() -> str:
    """`/v1` today; read at call time so it always follows `API_VERSION`."""
    return f"/v{api.API_VERSION}"


def _engine_version() -> str:
    try:
        return metadata.version("openkos")
    except metadata.PackageNotFoundError:
        return "unknown"


def _workspace(request: Request) -> api.Workspace:
    """The launch's workspace, kept on the app by `create_app`."""
    workspace: api.Workspace = request.app.state.workspace
    return workspace


def _registry(request: Request) -> RunRegistry:
    """The run registry `_build` keeps on the app."""
    registry: RunRegistry = request.app.state.registry
    return registry


def _plain_mappings(s: api.WorkspaceStatus) -> api.WorkspaceStatus:
    return dataclasses.replace(
        s,
        concepts_by_type=dict(s.concepts_by_type),
        open_decisions_by_kind=dict(s.open_decisions_by_kind),
    )


def _wait(requested: float) -> float:
    """The lock wait: what the client asked for, never above the ceiling."""
    return min(requested, _errors.WRITE_WAIT_SECONDS)


def _errors_of(
    *statuses: int, retry_after: bool = False
) -> dict[int | str, dict[str, Any]]:
    """`responses=` rows for the statuses a route answers with an `ErrorBody`.

    `retry_after` documents the header a busy 503 carries."""
    rows: dict[int | str, dict[str, Any]] = {s: {"model": ErrorBody} for s in statuses}
    if retry_after:
        rows[503]["headers"] = {
            "Retry-After": {
                "description": "Seconds to wait before retrying a busy workspace.",
                "schema": {"type": "integer"},
            }
        }
    return rows


def make_router() -> APIRouter:
    router = APIRouter(
        prefix=prefix(),
        dependencies=[Depends(_BEARER)],
        responses={
            401: {"model": ErrorBody},
            403: {"model": ErrorBody},
            422: {"model": ErrorBody},
            500: {"model": ErrorBody},
        },
    )

    @router.get("/health")
    def health() -> HealthBody:
        return HealthBody(
            status="ok",
            api_version=api.API_VERSION,
            engine_version=_engine_version(),
        )

    @router.get("/status")
    def status(workspace: api.Workspace = Depends(_workspace)) -> api.WorkspaceStatus:  # noqa: B008
        # FastAPI cannot serialise the read-only mappings the API returns.
        return invoke(lambda: _plain_mappings(api.status(workspace)))

    @router.get("/pending", responses=_errors_of(503))
    def pending(workspace: api.Workspace = Depends(_workspace)) -> api.PendingReport:  # noqa: B008
        return invoke(lambda: api.pending_report(workspace))

    @router.get("/decisions")
    def decisions(workspace: api.Workspace = Depends(_workspace)) -> DecisionList:  # noqa: B008
        return DecisionList(
            decisions=list(invoke(lambda: api.list_decisions(workspace)))
        )

    @router.post("/decisions/{decision_key}/preview", responses=_errors_of(409))
    def preview(
        decision_key: str,
        body: PreviewRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.DecisionPreview:
        result = invoke(
            lambda: api.preview_decision(workspace, decision_key, body.action)
        )
        if isinstance(result, api.DecisionPreview):
            return result
        raise _errors.outcome_problem(result)

    @router.post(
        "/decisions/{decision_key}/apply",
        responses=_errors_of(409, 503, retry_after=True),
    )
    def apply(
        decision_key: str,
        body: ApplyRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> ApplyResponse:
        if body.version.decision_key != decision_key:
            raise _errors.key_mismatch()
        warnings: list[str] = []
        result = invoke(
            lambda: api.apply_decision(
                workspace,
                body.version,
                body.action,
                wait_seconds=_wait(body.wait_seconds),
                on_warning=warnings.append,
            )
        )
        if isinstance(result, api.Applied):
            return ApplyResponse(applied=result, warnings=warnings)
        raise _errors.outcome_problem(result)

    @router.post("/queue/refresh", responses=_errors_of(503, retry_after=True))
    def refresh(
        body: RefreshRequest | None = None,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.QueueRefresh:
        wait = _wait(body.wait_seconds if body else _errors.WRITE_WAIT_SECONDS)
        return invoke(lambda: api.refresh_decision_queue(workspace, wait_seconds=wait))

    @router.post("/judging/quote", responses=_errors_of(424, 502, 503))
    def quote(
        body: QuoteRequest,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.JudgingQuote:
        return invoke(
            lambda: api.quote_judging(
                workspace,
                body.kind,
                include_confidential=body.include_confidential,
                include_uncertain=body.include_uncertain,
            )
        )

    @router.post("/judging/runs", status_code=202, responses=_errors_of(409))
    def start_run(
        body: RunRequest,
        response: Response,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
        registry: RunRegistry = Depends(_registry),  # noqa: B008
    ) -> RunStatus:
        if body.token is None:
            raise _errors.outcome_problem(api.RunRefused("no_quote_token"))
        status = registry.start(workspace, body.token)
        response.headers["Location"] = f"{prefix()}/judging/runs/{status.run_id}"
        return status

    @router.get("/judging/runs/{run_id}", responses=_errors_of(404))
    def run_status(
        run_id: str,
        registry: RunRegistry = Depends(_registry),  # noqa: B008
    ) -> RunStatus:
        return registry.status(run_id)

    @router.post("/judging/runs/{run_id}/stop", responses=_errors_of(404))
    def stop_run(
        run_id: str,
        registry: RunRegistry = Depends(_registry),  # noqa: B008
    ) -> RunStatus:
        return registry.stop(run_id)

    return router
