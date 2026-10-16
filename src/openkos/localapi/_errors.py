"""The error envelope and the named constants the local API shares.

Every non-2xx body is exactly `{code, retryable, message}`. This module holds the
envelope and the guard's codes; the endpoint rows join it with the routes.
"""

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


class ErrorBody(BaseModel):
    """The one body shape of every non-2xx response."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    retryable: bool
    message: str
