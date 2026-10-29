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
REQUEST_TOO_LARGE: Final = "request_too_large"


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
INVALID_SELECTION: Final = "invalid_selection"
WRONG_PREVIEW: Final = "wrong_preview"
ALREADY_RECONCILED: Final = "already_reconciled"
WORKSPACE_BUSY: Final = "workspace_busy"
WORKSPACE_LOCK_UNAVAILABLE: Final = "workspace_lock_unavailable"
WRITE_DRIFT: Final = "write_drift"
WRITE_REFUSED: Final = "write_refused"
WRITE_FAILED: Final = "write_failed"
MERGE_GROUP_INTERRUPTED: Final = "merge_group_interrupted"
UNMERGE_INTERRUPTED: Final = "unmerge_interrupted"
SURVIVOR_MISSING: Final = "survivor_missing"
NOT_MERGED: Final = "not_merged"
SURVIVOR_EDITED: Final = "survivor_edited"
QUEUE_REFRESH_REFUSED: Final = "queue_refresh_refused"
NOT_KEPT_DISTINCT: Final = "not_kept_distinct"
NOT_RELATED: Final = "not_related"
TIER_UNCHANGED: Final = "tier_unchanged"
UNKNOWN_TYPE: Final = "unknown_type"
NOT_DECLINED: Final = "not_declined"
NOT_RECONCILED: Final = "not_reconciled"

# Judging rows.
QUOTE_STALE: Final = "quote_stale"
RUN_REFUSED: Final = "run_refused"
RUN_ACTIVE: Final = "run_active"
ASK_ACTIVE: Final = "ask_active"
INGEST_ACTIVE: Final = "ingest_active"
INGEST_REFUSED: Final = "ingest_refused"
JUDGING_REFUSED: Final = "judging_refused"
BACKEND_UNAVAILABLE: Final = "backend_unavailable"
BACKEND_MODEL_NOT_FOUND: Final = "backend_model_not_found"
GENERATION_CAPPED: Final = "generation_capped"
BACKEND_ERROR: Final = "backend_error"

# Settings rows.
SETTINGS_UNCHANGED: Final = "settings_unchanged"
INVALID_BASE_URL: Final = "invalid_base_url"
INVALID_EMBEDDING_BASE_URL: Final = "invalid_embedding_base_url"
INVALID_MODEL: Final = "invalid_model"
INVALID_EMBEDDING_MODEL: Final = "invalid_embedding_model"
BASE_URL_REQUIRED: Final = "base_url_required"

# Inbox watch folder rows.
INBOX_UNCHANGED: Final = "inbox_unchanged"
INVALID_INBOX: Final = "invalid_inbox"
INBOX_INSIDE_WORKSPACE: Final = "inbox_inside_workspace"
INBOX_NOT_A_DIRECTORY: Final = "inbox_not_a_directory"

# Sensitivity rows.
PAGE_NOT_FOUND: Final = "page_not_found"
SENSITIVITY_UNCHANGED: Final = "sensitivity_unchanged"
SENSITIVITY_NO_RECORD: Final = "sensitivity_no_record"
SENSITIVITY_CHANGED_SINCE: Final = "sensitivity_changed_since"

# Search rows.
REBUILD_ACTIVE: Final = "rebuild_active"
REMOTE_NOT_ALLOWED: Final = "remote_not_allowed"
SEARCH_UNAVAILABLE: Final = "search_unavailable"

# Setup rows.
PULL_ACTIVE: Final = "pull_active"
MODEL_NOT_CONFIGURED: Final = "model_not_configured"
PULL_NOT_SUPPORTED: Final = "pull_not_supported"

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

# Subclass-first: the first isinstance match wins. `MergeGroupInterrupted` and
# `UnmergeInterrupted` come before `WriteFailed`, and `WriteFailed` and `DriftDetected` before
# `WriteRefused`, which they extend, and the three
# specific backend errors come before the `BackendError` they extend.
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
    # Before `WriteFailed`, which it extends. Not retryable: re-posting the
    # version is `Stale("row_resolved")`, because the first merge resolved the
    # row. The body names no id; the client lists the decisions again.
    _Row(
        api.MergeGroupInterrupted,
        500,
        MERGE_GROUP_INTERRUPTED,
        False,
        "the group was combined only in part; the completed merges are kept",
    ),
    # Before `WriteFailed`, which it extends. Not retryable: the completed
    # steps are committed, so re-posting the version is stale. The body names no
    # id; the client reads the survivor's merge history again.
    _Row(
        api.UnmergeInterrupted,
        500,
        UNMERGE_INTERRUPTED,
        False,
        "the unmerge stopped part way; the completed steps are kept",
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
    _Row(
        api.JudgingRefused,
        503,
        JUDGING_REFUSED,
        False,
        "judging cannot be run on this workspace",
    ),
    _Row(
        api.IngestRefused,
        503,
        INGEST_REFUSED,
        False,
        "files cannot be added to this workspace",
    ),
    _Row(
        api.BackendUnavailable,
        503,
        BACKEND_UNAVAILABLE,
        True,
        "the configured model backend is unreachable",
    ),
    _Row(
        api.BackendModelNotFound,
        424,
        BACKEND_MODEL_NOT_FOUND,
        False,
        "the configured model is not installed",
    ),
    _Row(
        api.BackendGenerationCapped,
        502,
        GENERATION_CAPPED,
        False,
        "the model's reply was cut off before it finished",
    ),
    _Row(api.BackendError, 502, BACKEND_ERROR, False, "the model backend failed"),
    _Row(
        api.SearchUnavailable,
        500,
        SEARCH_UNAVAILABLE,
        False,
        "a search store cannot be used",
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
    "invalid_selection": (
        422,
        INVALID_SELECTION,
        False,
        "the selection does not name a survivor and the members to combine with it",
    ),
    "wrong_preview": (
        422,
        WRONG_PREVIEW,
        False,
        "this action is previewed by the other preview route",
    ),
    "already_reconciled": (
        409,
        ALREADY_RECONCILED,
        False,
        "these concepts are already reconciled another way",
    ),
}

# Pull refusals returned by the pull operation, keyed by reason.
_PULL_REFUSED: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "not_configured": (
        422,
        MODEL_NOT_CONFIGURED,
        False,
        "that model is not one this workspace uses",
    ),
    "backend_cannot_pull": (
        422,
        PULL_NOT_SUPPORTED,
        False,
        "the configured model backend cannot download models",
    ),
}

# Unmerge refusals returned by the unmerge operations, keyed by reason.
_NOT_UNMERGEABLE: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "survivor_missing": (
        409,
        SURVIVOR_MISSING,
        False,
        "the concept was itself merged into another; undo that merge first",
    ),
    "not_merged": (
        409,
        NOT_MERGED,
        False,
        "that concept is not recorded as merged into this one",
    ),
    "survivor_edited": (
        409,
        SURVIVOR_EDITED,
        False,
        "the concept was edited since the merge; undoing it would discard the edit",
    ),
}

# Undo refusals returned by the undo operations, keyed by reason.
_NOT_UNDOABLE: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "not_kept_distinct": (
        409,
        NOT_KEPT_DISTINCT,
        False,
        "these concepts are not recorded as kept distinct",
    ),
    "not_related": (
        409,
        NOT_RELATED,
        False,
        "the source holds no such typed relation, or a concept no longer exists",
    ),
    "tier_unchanged": (
        409,
        TIER_UNCHANGED,
        False,
        "the type's tier is already set that way",
    ),
    "unknown_type": (
        422,
        UNKNOWN_TYPE,
        False,
        "that is not a concept type the engine knows",
    ),
    "not_declined": (
        409,
        NOT_DECLINED,
        False,
        "this contradiction is not recorded as declined",
    ),
    "not_reconciled": (
        409,
        NOT_RECONCILED,
        False,
        "these concepts hold no reconcile resolution, or a concept no longer exists",
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


def run_active() -> ApiProblem:
    """A run was requested while another is active; trying again later can work."""
    return ApiProblem(
        409,
        ErrorBody(
            code=RUN_ACTIVE,
            retryable=True,
            message="a judging run is already active",
        ),
    )


def ask_active() -> ApiProblem:
    """An ask was started while another is running; trying again later can work."""
    return ApiProblem(
        409,
        ErrorBody(
            code=ASK_ACTIVE,
            retryable=True,
            message="an ask is already running",
        ),
    )


def ingest_active() -> ApiProblem:
    """An ingest run was started while another is active; trying again later can
    work."""
    return ApiProblem(
        409,
        ErrorBody(
            code=INGEST_ACTIVE,
            retryable=True,
            message="files are already being added",
        ),
    )


def rebuild_active() -> ApiProblem:
    """A rebuild was started while another is active; trying again later can
    work."""
    return ApiProblem(
        409,
        ErrorBody(
            code=REBUILD_ACTIVE,
            retryable=True,
            message="a search rebuild is already running",
        ),
    )


def pull_active() -> ApiProblem:
    """A pull was requested while another is active; trying again later can work."""
    return ApiProblem(
        409,
        ErrorBody(
            code=PULL_ACTIVE,
            retryable=True,
            message="a model download is already active",
        ),
    )


_SENSITIVITY_REFUSED: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "not_found": (404, PAGE_NOT_FOUND, False, "no such concept"),
    "unchanged": (
        409,
        SENSITIVITY_UNCHANGED,
        False,
        "the concept already has that sensitivity",
    ),
}

_SENSITIVITY_NOT_UNDOABLE: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "not_found": (404, PAGE_NOT_FOUND, False, "no such concept"),
    "no_record": (
        409,
        SENSITIVITY_NO_RECORD,
        False,
        "this change did not record the pages raised with it, so it cannot be "
        "undone as a whole",
    ),
    "changed_since": (
        409,
        SENSITIVITY_CHANGED_SINCE,
        False,
        "the page, or a page raised with it, has changed since; undoing now "
        "would overwrite that",
    ),
}

_REBUILD_REFUSED: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "remote_not_allowed": (
        422,
        REMOTE_NOT_ALLOWED,
        False,
        "the embedding model is not on this machine; start again with "
        "allow_remote to send page text to it",
    ),
}

_SETTINGS_REFUSED: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "invalid_base_url": (
        422,
        INVALID_BASE_URL,
        False,
        "the address must be a full http:// or https:// URL with no password, "
        "query or fragment",
    ),
    "invalid_embedding_base_url": (
        422,
        INVALID_EMBEDDING_BASE_URL,
        False,
        "the embedding address must be a full http:// or https:// URL with no "
        "password, query or fragment",
    ),
    "invalid_model": (422, INVALID_MODEL, False, "the model name is not valid"),
    "invalid_embedding_model": (
        422,
        INVALID_EMBEDDING_MODEL,
        False,
        "the embedding model name is not valid",
    ),
    "base_url_required": (
        422,
        BASE_URL_REQUIRED,
        False,
        "an OpenAI-compatible backend needs an address",
    ),
    "unchanged": (
        409,
        SETTINGS_UNCHANGED,
        False,
        "the model settings are already set that way",
    ),
}


_INBOX_REFUSED: Final[Mapping[str, tuple[int, str, bool, str]]] = {
    "invalid": (422, INVALID_INBOX, False, "the folder is not a usable path"),
    "inside_workspace": (
        422,
        INBOX_INSIDE_WORKSPACE,
        False,
        "the folder cannot be the workspace or inside it: choose a folder "
        "outside the knowledge base",
    ),
    "not_a_directory": (
        422,
        INBOX_NOT_A_DIRECTORY,
        False,
        "the folder does not exist",
    ),
    "unchanged": (
        409,
        INBOX_UNCHANGED,
        False,
        "the watch folder is already set that way",
    ),
}


def outcome_problem(
    outcome: (
        api.NotApplied
        | api.Stale
        | api.QuoteStale
        | api.IngestQuoteStale
        | api.RunRefused
        | api.NotUnmergeable
        | api.UnmergeStale
        | api.NotUndoable
        | api.UndoStale
        | api.PullRefused
        | api.ModelSettingsRefused
        | api.ModelSettingsStale
        | api.InboxSettingsRefused
        | api.InboxSettingsStale
        | api.SensitivityRefused
        | api.SensitivityNotUndoable
        | api.SensitivityStale
        | api.RebuildRefused
    ),
) -> ApiProblem:
    """The failure a returned (not raised) outcome is answered with."""
    if isinstance(
        outcome,
        api.UnmergeStale
        | api.UndoStale
        | api.ModelSettingsStale
        | api.InboxSettingsStale
        | api.SensitivityStale,
    ):
        message = _STALE_MESSAGES["content_changed"]
        return ApiProblem(409, ErrorBody(code=STALE, retryable=False, message=message))
    if isinstance(outcome, api.QuoteStale | api.IngestQuoteStale):
        # Only the `ChangedField` literals, never anything a client supplied.
        changed = ", ".join(outcome.changed)
        message = f"the quote is stale (changed: {changed}); quote again"
        return ApiProblem(
            409, ErrorBody(code=QUOTE_STALE, retryable=False, message=message)
        )
    if isinstance(outcome, api.RunRefused):
        return ApiProblem(
            422,
            ErrorBody(
                code=RUN_REFUSED,
                retryable=False,
                message="a run needs the token of a quote",
            ),
        )
    if isinstance(outcome, api.Stale):
        message = _STALE_MESSAGES.get(outcome.reason, _STALE_FALLBACK)
        return ApiProblem(409, ErrorBody(code=STALE, retryable=False, message=message))
    if isinstance(outcome, api.NotUnmergeable):
        row = _NOT_UNMERGEABLE.get(outcome.reason)
    elif isinstance(outcome, api.NotUndoable):
        row = _NOT_UNDOABLE.get(outcome.reason)
    elif isinstance(outcome, api.PullRefused):
        row = _PULL_REFUSED.get(outcome.reason)
    elif isinstance(outcome, api.ModelSettingsRefused):
        row = _SETTINGS_REFUSED.get(outcome.reason)
    elif isinstance(outcome, api.InboxSettingsRefused):
        row = _INBOX_REFUSED.get(outcome.reason)
    elif isinstance(outcome, api.SensitivityRefused):
        row = _SENSITIVITY_REFUSED.get(outcome.reason)
    elif isinstance(outcome, api.SensitivityNotUndoable):
        row = _SENSITIVITY_NOT_UNDOABLE.get(outcome.reason)
    elif isinstance(outcome, api.RebuildRefused):
        row = _REBUILD_REFUSED.get(outcome.reason)
    else:
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
