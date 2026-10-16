"""The versioned router. Every operation calls `openkos.api` through the module
attribute, so a test that patches `openkos.api.<fn>` intercepts the call."""

from importlib import metadata

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBearer

from openkos import api
from openkos.localapi._errors import ErrorBody
from openkos.localapi._models import HealthBody

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

    return router
