"""The error envelope and the named constants the local API shares.

Every non-2xx body is exactly `{code, retryable, message}`. This module holds the
envelope and the guard's codes; the endpoint rows join it with the routes.
"""

import logging
from collections.abc import Callable, Mapping
from typing import Final, NamedTuple

from pydantic import BaseModel, ConfigDict

from openkos import api

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

# Read-path rows.
QUEUE_UNAVAILABLE: Final = "queue_unavailable"
WORKSPACE_NOT_FOUND: Final = "workspace_not_found"
WORKSPACE_UNREADABLE: Final = "workspace_unreadable"

# Write-path rows.
KEY_MISMATCH: Final = "key_mismatch"
STALE: Final = "stale"
ACTION_NOT_OFFERED: Final = "action_not_offered"
SKIP_NOT_APPLICABLE: Final = "skip_not_applicable"
NOT_MERGEABLE: Final = "not_mergeable"
MEMBER_MISSING: Final = "member_missing"
RECORD_FAILED: Final = "record_failed"
WORKSPACE_BUSY: Final = "workspace_busy"
WORKSPACE_LOCK_UNAVAILABLE: Final = "workspace_lock_unavailable"
WRITE_DRIFT: Final = "write_drift"
WRITE_REFUSED: Final = "write_refused"
WRITE_FAILED: Final = "write_failed"
QUEUE_REFRESH_REFUSED: Final = "queue_refresh_refused"

_REFUSAL_FALLBACK: Final = "the write was refused"


class _Row(NamedTuple):
    kind: type[BaseException]
    status: int
    code: str
    retryable: bool
    # `None` passes the exception's own user-facing `message` through.
    message: str | None
    headers: Mapping[str, str] = {}


_BUSY_HEADERS: Final = {"Retry-After": str(RETRY_AFTER_SECONDS)}

# Subclass-first: the first isinstance match wins. `WriteFailed` and
# `DriftDetected` come before `WriteRefused`, which they extend.
_ROWS: Final[tuple[_Row, ...]] = (
    _Row(
        api.QueueUnavailableError,
        503,
        QUEUE_UNAVAILABLE,
        False,
        "the pending queue is unreadable",
    ),
    _Row(
        api.WorkspaceNotFound,
        500,
        WORKSPACE_NOT_FOUND,
        False,
        "the workspace was not found",
    ),
    _Row(
        api.WorkspaceUnreadable,
        500,
        WORKSPACE_UNREADABLE,
        False,
        "the workspace is unreadable",
    ),
    _Row(
        api.WorkspaceBusyError,
        503,
        WORKSPACE_BUSY,
        True,
        "the workspace is busy; try again",
        _BUSY_HEADERS,
    ),
    _Row(
        api.WorkspaceLockUnavailableError,
        500,
        WORKSPACE_LOCK_UNAVAILABLE,
        False,
        "the workspace lock cannot be used",
    ),
    _Row(
        api.DriftDetected,
        409,
        WRITE_DRIFT,
        True,
        "the files changed while the write was prepared; try again",
    ),
    _Row(api.WriteFailed, 500, WRITE_FAILED, False, "the write failed"),
    _Row(api.WriteRefused, 422, WRITE_REFUSED, False, None),
    _Row(
        api.QueueRefreshRefused,
        503,
        QUEUE_REFRESH_REFUSED,
        False,
        "the decision queue cannot be refreshed",
    ),
)

# Outcomes returned (not raised) by the write operations. (status, code,
# retryable, message), keyed by the `NotApplied` reason.
_NOT_APPLIED: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "not_offered": (
        422,
        ACTION_NOT_OFFERED,
        False,
        "this decision does not offer that action",
    ),
    "skipped": (
        422,
        SKIP_NOT_APPLICABLE,
        False,
        "skip writes nothing; the client moves on without a request",
    ),
    "not_mergeable": (
        409,
        NOT_MERGEABLE,
        False,
        "these concepts cannot be merged as they stand",
    ),
    "member_missing": (
        409,
        MEMBER_MISSING,
        False,
        "a concept this decision names no longer exists",
    ),
    "record_failed": (
        409,
        RECORD_FAILED,
        True,
        "the decision could not be recorded; try again",
    ),
}

_STALE_MESSAGES: Final[Mapping[str, str]] = {
    "content_changed": "the concepts changed since the preview; preview it again",
    "row_resolved": "the decision was already resolved; preview it again",
    "row_superseded": "a newer proposal replaced this decision; preview it again",
    "row_inputs_changed": "the inputs behind this decision changed; preview it again",
    "unknown_key": "no open decision has that key; preview it again",
}
_STALE_FALLBACK: Final = "the decision changed; preview it again"

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

    Rows are matched subclass first and the generic 500 closes the table. The
    message is fixed text: nothing echoes `str(exc)`, except the documented
    user-facing `message` of a `WriteRefused`."""
    _LOG.error("local api request failed", exc_info=exc)
    if _is_unreachable_refusal(exc):
        return _internal()
    for row in _ROWS:
        if isinstance(exc, row.kind):
            message = row.message
            if message is None:
                message = getattr(exc, "message", "") or _REFUSAL_FALLBACK
            body = ErrorBody(code=row.code, retryable=row.retryable, message=message)
            return row.status, body, dict(row.headers)
    return _internal()


def _is_unreachable_refusal(exc: BaseException) -> bool:
    """A `WriteRefused` this API never raises (the confirmation refusals).

    Only the base, `Refused` and `DriftDetected` are reachable; any other
    subclass is a defect to report as `internal_error`."""
    return (
        isinstance(exc, api.WriteRefused)
        and type(exc) is not api.WriteRefused
        and not isinstance(exc, api.Refused | api.DriftDetected)
    )


def _internal() -> tuple[int, ErrorBody, dict[str, str]]:
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


def key_mismatch() -> ApiProblem:
    """The path names one decision and the echoed version another."""
    return ApiProblem(
        422,
        ErrorBody(
            code=KEY_MISMATCH,
            retryable=False,
            message="the path key differs from the version's decision key",
        ),
    )


def outcome_problem(outcome: api.NotApplied | api.Stale) -> ApiProblem:
    """The failure a returned (not raised) write outcome is answered with."""
    if isinstance(outcome, api.Stale):
        message = _STALE_MESSAGES.get(outcome.reason, _STALE_FALLBACK)
        return ApiProblem(409, ErrorBody(code=STALE, retryable=False, message=message))
    row = _NOT_APPLIED.get(outcome.reason)
    if row is None:  # a reason added after this table: report it, do not guess
        return ApiProblem(
            500,
            ErrorBody(code=INTERNAL_ERROR, retryable=False, message="internal error"),
        )
    status, code, retryable, message = row
    return ApiProblem(
        status, ErrorBody(code=code, retryable=retryable, message=message)
    )
