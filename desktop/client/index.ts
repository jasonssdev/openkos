// The desktop app's client for the engine's local API.
//
// The types come from `schema.d.ts`, generated from the committed OpenAPI
// snapshot (`pnpm generate`; `pnpm check:schema` fails when it drifts). Every
// call names its path template and method from the generated `paths`, and its
// body and answer are typed from that operation, so a path, a body or an answer
// the snapshot no longer has fails `tsc`. This layer adds what the schema
// cannot: the address and token of the running engine, and errors a screen can
// act on (`errors.ts`). It has no runtime dependency.
import type { ReadyEngine } from "./engine.ts";
import { ApiError, EngineUnreachable, ProtocolError, isBusy } from "./errors.ts";
import type { components, paths } from "./schema";

export type { EngineState, ReadyEngine } from "./engine.ts";
export * from "./errors.ts";
export * from "./shell.ts";
export * from "./setup.ts";
export * from "./workspace.ts";
export type { components, paths } from "./schema";

/** The schema's named shapes: `Schemas["WorkspaceStatus"]`, `Schemas["DecisionPreview"]`, ... */
export type Schemas = components["schemas"];

type Method = "get" | "post";
type Operation<P extends keyof paths, M extends Method> = NonNullable<paths[P][M]>;
type Json<R> = R extends { content: { "application/json": infer B } } ? B : never;
/** The JSON body of an operation's success (200 or 202). */
type Answer<P extends keyof paths, M extends Method> =
  Operation<P, M> extends { responses: infer R } ? Json<R[Extract<keyof R, 200 | 202>]> : never;
/** The JSON body an operation takes. */
type Body<P extends keyof paths, M extends Method> =
  Operation<P, M> extends { requestBody?: infer B } ? Json<NonNullable<B>> : never;

export interface ClientOptions {
  /** The `fetch` to send requests with; the global one by default. */
  fetch?: typeof globalThis.fetch;
}

export type Client = ReturnType<typeof createClient>;

/**
 * The origin of a ready engine's base URL. Only `http://127.0.0.1:<port>/v1` is
 * accepted, so a state that names anything else cannot send the token there.
 */
function engineOrigin(baseUrl: string): string {
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new TypeError("the engine's base URL is not a URL");
  }
  const loopback = url.protocol === "http:" && url.hostname === "127.0.0.1";
  const plain = !url.username && !url.password && !url.search && !url.hash;
  if (!loopback || !plain || !["/v1", "/v1/"].includes(url.pathname)) {
    throw new TypeError("the engine's base URL must be http://127.0.0.1:<port>/v1");
  }
  return url.origin;
}

function isEnvelope(body: unknown): body is Schemas["ErrorBody"] {
  if (typeof body !== "object" || body === null) return false;
  const { code, retryable, message } = body as Record<string, unknown>;
  return typeof code === "string" && typeof retryable === "boolean" && typeof message === "string";
}

function retryAfter(response: Response): number | null {
  const value = response.headers.get("Retry-After")?.trim() ?? "";
  return /^\d+$/.test(value) ? Number(value) : null;
}

/**
 * Sends one request. The token lives only in this
 * closure: it is set on each request and is never a property of the client,
 * and no error below is built from the request's headers. Every answer is
 * checked here, where its path is known: a success must be JSON and a failure
 * must be the envelope.
 */
function sender(token: string, send: typeof globalThis.fetch) {
  return async (request: Request): Promise<Response> => {
    const path = new URL(request.url).pathname;
    request.headers.set("Authorization", `Bearer ${token}`);
    let response: Response;
    try {
      response = await send(request);
    } catch {
      throw new EngineUnreachable({ path });
    }
    const text = await response.text();
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      throw new ProtocolError({ status: response.status, path });
    }
    if (!response.ok && !isEnvelope(body)) {
      throw new ProtocolError({ status: response.status, path });
    }
    return new Response(text, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  };
}

async function unwrap<T>(response: Response): Promise<T> {
  // `sender` has already checked the body: JSON, and the envelope on a failure.
  const body: unknown = await response.json();
  if (response.ok) return body as T;
  const envelope = body as Schemas["ErrorBody"];
  throw new ApiError({
    status: response.status,
    code: envelope.code,
    retryable: envelope.retryable,
    message: envelope.message,
    retryAfterSeconds: retryAfter(response),
  });
}

/** `/v1/decisions/{decision_key}/apply` with its `{name}` segments filled in and encoded. */
export function fillPath(template: string, params: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, name: string) => {
    const value = params[name];
    if (value === undefined) throw new TypeError(`no value for {${name}}`);
    return encodeURIComponent(value);
  });
}

interface Call<P extends keyof paths, M extends Method> {
  params?: Record<string, string>;
  query?: Record<string, string>;
  body?: Body<P, M>;
}

/** A client for one ready engine. A restarted engine has a new port and token: create a new client. */
export function createClient(engine: ReadyEngine, options: ClientOptions = {}) {
  const origin = engineOrigin(engine.base_url);
  const send = sender(engine.token, options.fetch ?? globalThis.fetch.bind(globalThis));

  async function call<P extends keyof paths, M extends Method>(
    method: M,
    template: P,
    { params = {}, query, body }: Call<P, M> = {},
  ): Promise<Answer<P, M>> {
    const url = new URL(fillPath(template, params), origin);
    for (const [name, value] of Object.entries(query ?? {})) url.searchParams.set(name, value);
    const init: RequestInit = { method: method.toUpperCase() };
    if (body !== undefined) {
      init.body = JSON.stringify(body);
      init.headers = { "Content-Type": "application/json" };
    }
    return unwrap(await send(new Request(url, init)));
  }

  return {
    health: () => call("get", "/v1/health"),
    status: () => call("get", "/v1/status"),
    pending: () => call("get", "/v1/pending"),
    decisions: () => call("get", "/v1/decisions"),
    previewDecision: (decisionKey: string, body: Schemas["PreviewRequest"]) =>
      call("post", "/v1/decisions/{decision_key}/preview", { params: { decision_key: decisionKey }, body }),
    previewFinding: (decisionKey: string, body: Schemas["FindingPreviewRequest"]) =>
      call("post", "/v1/decisions/{decision_key}/preview-finding", { params: { decision_key: decisionKey }, body }),
    applyDecision: (decisionKey: string, body: Schemas["ApplyRequest"]) =>
      call("post", "/v1/decisions/{decision_key}/apply", { params: { decision_key: decisionKey }, body }),
    refreshQueue: (body?: Schemas["RefreshRequest"]) =>
      call("post", "/v1/queue/refresh", body === undefined ? {} : { body }),
    quoteJudging: (body: Schemas["QuoteRequest"]) => call("post", "/v1/judging/quote", { body }),
    startJudgingRun: (body: Schemas["RunRequest"]) => call("post", "/v1/judging/runs", { body }),
    judgingRun: (runId: string) => call("get", "/v1/judging/runs/{run_id}", { params: { run_id: runId } }),
    stopJudgingRun: (runId: string) =>
      call("post", "/v1/judging/runs/{run_id}/stop", { params: { run_id: runId } }),
    ask: (body: Schemas["AskRequest"]) => call("post", "/v1/ask", { body }),
    askJob: (askId: string) => call("get", "/v1/ask/{ask_id}", { params: { ask_id: askId } }),
    setupReadiness: () => call("get", "/v1/setup/readiness"),
    startModelPull: (body: Schemas["PullRequest"]) => call("post", "/v1/setup/pulls", { body }),
    modelPull: (pullId: string) => call("get", "/v1/setup/pulls/{pull_id}", { params: { pull_id: pullId } }),
    stopModelPull: (pullId: string) =>
      call("post", "/v1/setup/pulls/{pull_id}/stop", { params: { pull_id: pullId } }),
    mergeHistory: (conceptId: string) => call("get", "/v1/merges", { query: { concept_id: conceptId } }),
    mergedConcepts: () => call("get", "/v1/merged"),
    readPage: (conceptId: string) => call("get", "/v1/pages", { query: { concept_id: conceptId } }),
    previewUnmerge: (body: Schemas["UnmergePreviewRequest"]) => call("post", "/v1/unmerge/preview", { body }),
    applyUnmerge: (body: Schemas["UnmergeApplyRequest"]) => call("post", "/v1/unmerge/apply", { body }),
    keptDistinct: () => call("get", "/v1/kept-distinct"),
    previewReopen: (body: Schemas["ReopenPreviewRequest"]) => call("post", "/v1/reopen/preview", { body }),
    applyReopen: (body: Schemas["ReopenApplyRequest"]) => call("post", "/v1/reopen/apply", { body }),
    previewUnrelate: (body: Schemas["UnrelatePreviewRequest"]) => call("post", "/v1/unrelate/preview", { body }),
    applyUnrelate: (body: Schemas["UnrelateApplyRequest"]) => call("post", "/v1/unrelate/apply", { body }),
    previewTierChange: (body: Schemas["TierChangePreviewRequest"]) => call("post", "/v1/tiers/preview", { body }),
    applyTierChange: (body: Schemas["TierChangeApplyRequest"]) => call("post", "/v1/tiers/apply", { body }),
  };
}

export interface RetryOptions {
  /** Never wait past this many milliseconds from the first try (5000 by default). */
  deadlineMs?: number;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

const DEFAULT_DEADLINE_MS = 5000;
const DEFAULT_WAIT_SECONDS = 1;

/**
 * Repeat `call` while it fails with `workspace_busy` only: a busy refusal is
 * raised before anything is written, so the same call is safe to repeat. Waits
 * `Retry-After` seconds (one when absent) between tries, never past the
 * deadline, and then rethrows the last busy error. Every other failure,
 * including the retryable `write_drift` and `record_failed`, is rethrown at
 * once: each needs the person, not a blind repeat.
 */
export async function retryWhileBusy<T>(call: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const deadline = options.deadlineMs ?? DEFAULT_DEADLINE_MS;
  const now = options.now ?? (() => Date.now());
  const sleep = options.sleep ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const start = now();
  for (;;) {
    try {
      return await call();
    } catch (error) {
      if (!isBusy(error)) throw error;
      const wait = (error.retryAfterSeconds ?? DEFAULT_WAIT_SECONDS) * 1000;
      if (now() - start + wait > deadline) throw error;
      await sleep(wait);
    }
  }
}
