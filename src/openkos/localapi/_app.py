"""The application factory.

`create_app` wires the routes, the uniform error handlers and the guard, which
is the only middleware: no route can opt out of it. `openapi_document` builds
the same routes with no settings, so the contract needs no workspace or socket.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from openkos import api
from openkos.localapi import _asks, _errors, _pulls, _routes, _runs
from openkos.localapi._guard import ALLOWED_ORIGINS, LocalApiGuard, _GuardConfig

DEFAULT_ORIGINS: Final = tuple(sorted(ALLOWED_ORIGINS))


@dataclass(frozen=True)
class ServerSettings:
    """What one launch needs. Only the digest of the token is ever kept."""

    workspace: api.Workspace
    port: int
    token_digest: bytes = field(repr=False)


def _problem(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = _errors.ErrorBody(code=code, retryable=False, message=message)
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


async def _on_api_problem(_request: Request, exc: Exception) -> JSONResponse:
    problem = cast(_errors.ApiProblem, exc)  # registered for this type only
    return JSONResponse(
        problem.body.model_dump(),
        status_code=problem.status,
        headers=problem.headers,
    )


async def _on_invalid(_request: Request, _exc: Exception) -> JSONResponse:
    # Pydantic's errors echo the submitted values, which could be an echoed token.
    return _problem(422, _errors.INVALID_REQUEST, "the request is not valid")


async def _on_not_found(_request: Request, _exc: Exception) -> JSONResponse:
    return _problem(404, _errors.NOT_FOUND, "no such route")


async def _on_method(_request: Request, exc: Exception) -> JSONResponse:
    allow = getattr(exc, "headers", None) or {}
    return _problem(405, _errors.METHOD_NOT_ALLOWED, "method not allowed", dict(allow))


async def _on_unexpected(_request: Request, exc: Exception) -> JSONResponse:
    status, body, headers = _errors.error_body(exc)
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Set every stop token and wait at most the grace; the join blocks, so it
    # leaves the event loop. A call in flight is abandoned with its daemon thread.
    registry: _runs.RunRegistry = app.state.registry
    pulls: _pulls.PullRegistry = app.state.pulls
    await asyncio.to_thread(registry.shutdown, _runs.SHUTDOWN_GRACE_SECONDS)
    await asyncio.to_thread(pulls.shutdown, _runs.SHUTDOWN_GRACE_SECONDS)


def _build() -> FastAPI:
    app = FastAPI(
        title="OpenKOS local API",
        version=str(api.API_VERSION),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.registry = _runs.RunRegistry()
    app.state.asks = _asks.AskRegistry()
    app.state.pulls = _pulls.PullRegistry()
    app.include_router(_routes.make_router())
    handlers: dict[Any, Any] = {
        _errors.ApiProblem: _on_api_problem,
        RequestValidationError: _on_invalid,
        404: _on_not_found,
        405: _on_method,
        Exception: _on_unexpected,
    }
    for key, handler in handlers.items():
        app.add_exception_handler(key, handler)
    return app


def create_app(settings: ServerSettings) -> FastAPI:
    """The guarded application for one launch."""
    app = _build()
    app.state.workspace = settings.workspace
    app.add_middleware(
        LocalApiGuard,
        config=_GuardConfig(port=settings.port, token_digest=settings.token_digest),
    )
    return app


def openapi_document() -> dict[str, object]:
    """The contract: the same routes and handlers, built without settings."""
    return _build().openapi()
