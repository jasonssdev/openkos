"""The versioned router. Every operation calls `openkos.api` through the module
attribute, so a test that patches `openkos.api.<fn>` intercepts the call."""

import dataclasses
from importlib import metadata

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPBearer

from openkos import api
from openkos.localapi._errors import ErrorBody, invoke
from openkos.localapi._models import DecisionList, HealthBody

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

    return router
