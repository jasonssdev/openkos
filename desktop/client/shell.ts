// The shell's own commands for the remote backend's API key (ADR-0069). The
// shell keeps the key in the operating system's keychain and hands it to the
// engine as `OPENKOS_OPENAI_API_KEY`; the local API never sees it. The page can
// set the key, clear it and ask whether one is set. There is deliberately no
// way to read it back. Setting or clearing it restarts the engine, so a client
// created from the old ready state stops working: read `engine_state` again.

/** Tauri's `invoke` (`window.__TAURI__.core.invoke`), injected so tests need no shell. */
export type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

/** Every reason the shell's key commands refuse with (`src-tauri/src/apikey.rs`). */
export const API_KEY_ERROR_REASONS = ["invalid_key", "not_supported", "store_failed"] as const;

export type ApiKeyErrorReason = (typeof API_KEY_ERROR_REASONS)[number] | "unknown";

/**
 * A key command the shell refused. `reason` is one of the shell's reasons, or
 * `unknown` when the answer was not one: an unexpected rejection is never
 * echoed, because nothing guarantees it does not carry the key.
 */
export class ApiKeyError extends Error {
  readonly reason: ApiKeyErrorReason;

  constructor(reason: ApiKeyErrorReason, message: string) {
    super(message);
    this.name = "ApiKeyError";
    this.reason = reason;
  }
}

const UNKNOWN = "the shell did not answer the API key command as expected";

function isReason(value: unknown): value is (typeof API_KEY_ERROR_REASONS)[number] {
  return (API_KEY_ERROR_REASONS as readonly unknown[]).includes(value);
}

function refusal(error: unknown): ApiKeyError {
  if (typeof error === "object" && error !== null) {
    const { reason, detail } = error as Record<string, unknown>;
    if (isReason(reason) && typeof detail === "string") return new ApiKeyError(reason, detail);
  }
  return new ApiKeyError("unknown", UNKNOWN);
}

async function run<T>(invoke: Invoke, command: string, args?: Record<string, unknown>): Promise<T> {
  try {
    return await invoke<T>(command, args);
  } catch (error) {
    throw refusal(error);
  }
}

export type ApiKeys = ReturnType<typeof createApiKeys>;

/** The three key commands over the shell's `invoke`. */
export function createApiKeys(invoke: Invoke) {
  return {
    /** Keep `key` (trimmed by the shell) and restart the engine with it. */
    async set(key: string): Promise<void> {
      await run<null>(invoke, "set_api_key", { key });
    },
    /** Remove the key and restart the engine without it. */
    async clear(): Promise<void> {
      await run<null>(invoke, "clear_api_key");
    },
    /** Whether a key is kept; the only thing the page may learn about it. */
    async isSet(): Promise<boolean> {
      const answer = await run<unknown>(invoke, "api_key_status");
      if (typeof answer !== "boolean") throw new ApiKeyError("unknown", UNKNOWN);
      return answer;
    },
  };
}
