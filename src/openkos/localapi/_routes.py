"""The versioned router. Every operation calls `openkos.api` through the module
attribute, so a test that patches `openkos.api.<fn>` intercepts the call."""

import dataclasses
from importlib import metadata

from fastapi import APIRouter, Depends, Request
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
    RefreshRequest,
)

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


def _plain_mappings(s: api.WorkspaceStatus) -> api.WorkspaceStatus:
    return dataclasses.replace(
        s,
        concepts_by_type=dict(s.concepts_by_type),
        open_decisions_by_kind=dict(s.open_decisions_by_kind),
    )


def _wait(requested: float) -> float:
    """The lock wait: what the client asked for, never above the ceiling."""
    return min(requested, _errors.WRITE_WAIT_SECONDS)


def make_router() -> APIRouter:
    router = APIRouter(
        prefix=prefix(),
        dependencies=[Depends(_BEARER)],
        responses={
            401: {"model": ErrorBody},
            403: {"model": ErrorBody},
            422: {"model": ErrorBody},
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

    @router.get("/pending")
    def pending(workspace: api.Workspace = Depends(_workspace)) -> api.PendingReport:  # noqa: B008
        return invoke(lambda: api.pending_report(workspace))

    @router.get("/decisions")
    def decisions(workspace: api.Workspace = Depends(_workspace)) -> DecisionList:  # noqa: B008
        return DecisionList(
            decisions=list(invoke(lambda: api.list_decisions(workspace)))
        )

    @router.post("/decisions/{decision_key}/preview")
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

    @router.post("/decisions/{decision_key}/apply")
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

    @router.post("/queue/refresh")
    def refresh(
        body: RefreshRequest | None = None,
        workspace: api.Workspace = Depends(_workspace),  # noqa: B008
    ) -> api.QueueRefresh:
        wait = _wait(body.wait_seconds if body else _errors.WRITE_WAIT_SECONDS)
        return invoke(lambda: api.refresh_decision_queue(workspace, wait_seconds=wait))

    return router
