"""The error envelope and the named constants the local API shares.

Every non-2xx body is exactly `{code, retryable, message}`. This module holds the
envelope and the guard's codes; the endpoint rows join it with the routes.
"""

import logging
from collections.abc import Callable, Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict

# A write waits at most this long for the workspace lock (seconds).
WRITE_WAIT_SECONDS: Final = 5.0
# Sent as `Retry-After` on a busy 503; an unmeasured placeholder.
RETRY_AFTER_SECONDS: Final = 2

# Guard rows: (status, code, message).
UNAUTHORIZED: Final = "unauthorized"
FORBIDDEN: Final = "forbidden"
HOST_NOT_ALLOWED: Final = "host_not_allowed"
ORIGIN_NOT_ALLOWED: Final = "origin_not_allowed"
PREFLIGHT_REJECTED: Final = "preflight_rejected"


# Framework rows.
INVALID_REQUEST: Final = "invalid_request"
NOT_FOUND: Final = "not_found"
METHOD_NOT_ALLOWED: Final = "method_not_allowed"
INTERNAL_ERROR: Final = "internal_error"

_LOG = logging.getLogger(__name__.rsplit(".", 1)[0])


class ErrorBody(BaseModel):
    """The one body shape of every non-2xx response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    retryable: bool
    message: str


class ApiProblem(Exception):
    """A mapped failure: the status, the uniform body and any headers to send."""

    def __init__(
        self,
        status: int,
        body: ErrorBody,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(body.code)
        self.status = status
        self.body = body
        self.headers = dict(headers or {})


def error_body(exc: BaseException) -> tuple[int, ErrorBody, dict[str, str]]:
    """The status, body and headers an exception is answered with.

    Only the generic row exists so far; later slices add rows ahead of it,
    subclass first. The message is fixed text: nothing echoes `str(exc)`."""
    _LOG.error("local api request failed", exc_info=exc)
    return (
        500,
        ErrorBody(code=INTERNAL_ERROR, retryable=False, message="internal error"),
        {},
    )


def invoke[T](fn: Callable[[], T]) -> T:
    """Call one `openkos.api` operation, turning any failure into an `ApiProblem`."""
    try:
        return fn()
    except ApiProblem:
        raise
    except Exception as exc:
        status, body, headers = error_body(exc)
        raise ApiProblem(status, body, headers) from exc
