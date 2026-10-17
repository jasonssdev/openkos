// The errors the client raises. Every non-2xx answer of the local API carries
// one envelope, `{code, retryable, message}`; the engine's message is fixed text
// and never echoes a request, so it is safe to show. No error here is built from
// a request or its headers, so none can carry the token.

/**
 * Every code the engine's error table defines (`src/openkos/localapi/_errors.py`).
 * A test holds this list equal to that table in both directions.
 */
export const ERROR_CODES = [
  // The guard.
  "unauthorized",
  "forbidden",
  "host_not_allowed",
  "origin_not_allowed",
  "preflight_rejected",
  "request_too_large",
  // The framework.
  "invalid_request",
  "not_found",
  "method_not_allowed",
  "internal_error",
  // Reads.
  "queue_unavailable",
  "workspace_not_found",
  "workspace_unreadable",
  // Writes.
  "key_mismatch",
  "stale",
  "action_not_offered",
  "skip_not_applicable",
  "not_mergeable",
  "member_missing",
  "record_failed",
  "invalid_selection",
  "wrong_preview",
  "already_reconciled",
  "workspace_busy",
  "workspace_lock_unavailable",
  "write_drift",
  "write_refused",
  "write_failed",
  "merge_group_interrupted",
  "unmerge_interrupted",
  "survivor_missing",
  "not_merged",
  "survivor_edited",
  "queue_refresh_refused",
  "not_kept_distinct",
  "not_related",
  "tier_unchanged",
  "unknown_type",
  "not_declined",
  "not_reconciled",
  // Judging.
  "quote_stale",
  "run_refused",
  "run_active",
  "judging_refused",
  "backend_unavailable",
  "backend_model_not_found",
  "generation_capped",
  "backend_error",
  // Asking.
  "ask_active",
  // Adding files.
  "ingest_active",
  "ingest_refused",
  // Setup.
  "pull_active",
  "model_not_configured",
  "pull_not_supported",
  // Settings.
  "settings_unchanged",
  "invalid_base_url",
  "invalid_embedding_base_url",
  "invalid_model",
  "invalid_embedding_model",
  "base_url_required",
  // Who can see a page.
  "page_not_found",
  "sensitivity_unchanged",
  // Rebuilding search.
  "rebuild_active",
  "remote_not_allowed",
  "search_unavailable",
] as const;

export type KnownErrorCode = (typeof ERROR_CODES)[number];
/** A known code, or one a newer engine sends, kept rather than lost. */
export type ErrorCode = KnownErrorCode | (string & {});

/** An answer that carried the error envelope. */
export class ApiError extends Error {
  override readonly name = "ApiError";
  readonly status: number;
  readonly code: ErrorCode;
  readonly retryable: boolean;
  /** The `Retry-After` header in whole seconds, or null when absent or not seconds. */
  readonly retryAfterSeconds: number | null;

  constructor(init: {
    status: number;
    code: ErrorCode;
    retryable: boolean;
    message: string;
    retryAfterSeconds: number | null;
  }) {
    super(init.message);
    this.status = init.status;
    this.code = init.code;
    this.retryable = init.retryable;
    this.retryAfterSeconds = init.retryAfterSeconds;
  }
}

/** An answer the client cannot read: no envelope on a failure, no JSON on a success. */
export class ProtocolError extends Error {
  override readonly name = "ProtocolError";
  readonly status: number;
  readonly path: string;

  constructor(init: { status: number; path: string }) {
    super(`the engine answered ${init.status} in a form the client cannot read`);
    this.status = init.status;
    this.path = init.path;
  }
}

/** No answer at all: the request never reached the engine. */
export class EngineUnreachable extends Error {
  override readonly name = "EngineUnreachable";
  readonly path: string;

  constructor(init: { path: string }) {
    super("the engine could not be reached");
    this.path = init.path;
  }
}

function hasCode(error: unknown, codes: readonly string[]): error is ApiError {
  return error instanceof ApiError && codes.includes(error.code);
}

/** The workspace lock was held: nothing was written, and the same call can be repeated. */
export function isBusy(error: unknown): error is ApiError {
  return hasCode(error, ["workspace_busy"]);
}

/** The engine says trying again later can work. Not every such case is safe to repeat blindly. */
export function isTryAgain(error: unknown): error is ApiError {
  return error instanceof ApiError && error.retryable;
}

/** The files or the queue changed since the preview: preview again. */
export function isStale(error: unknown): error is ApiError {
  return hasCode(error, ["stale", "write_drift"]);
}

/** An unmerge the ledger or the files refuse, decided before anything is written. */
export function isUnmergeRefusal(error: unknown): error is ApiError {
  return hasCode(error, ["survivor_missing", "not_merged", "survivor_edited"]);
}
