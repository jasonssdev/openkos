import { describe, expect, it, vi } from "vitest";
import { createClient, fillPath, retryWhileBusy, type Client } from "./index.ts";
import type { ReadyEngine } from "./engine.ts";
import { ApiError, EngineUnreachable, ProtocolError, isBusy } from "./errors.ts";

const TOKEN = "a".repeat(32) + "b".repeat(32);
const READY: ReadyEngine = {
  state: "ready",
  attempt: 1,
  base_url: "http://127.0.0.1:4242/v1",
  token: TOKEN,
  pid: 7,
};

function json(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
}

function envelope(status: number, code: string, retryable = false, headers = {}): Response {
  return json({ code, retryable, message: `the ${code} message` }, { status, headers });
}

type Sent = { method: string; url: string; auth: string | null; body: unknown };

function engine(...answers: (Response | Error)[]) {
  const sent: Sent[] = [];
  const fetch = vi.fn(async (input: Request) => {
    const text = await input.text();
    sent.push({
      method: input.method,
      url: input.url,
      auth: input.headers.get("Authorization"),
      body: text ? JSON.parse(text) : null,
    });
    const answer = answers.length > 1 ? answers.shift()! : answers[0]!;
    if (answer instanceof Error) throw answer;
    return answer.clone();
  });
  const client = createClient(READY, { fetch: fetch as unknown as typeof globalThis.fetch });
  return { client, sent, fetch };
}

const VERSION = {
  decision_key: "k1",
  action: "merge",
  content_digest: "c",
  payload_digest: "p",
  selection: null,
} as const;

describe("the base URL", () => {
  it.each([
    "http://example.com:4242/v1",
    "https://127.0.0.1:4242/v1",
    "http://localhost:4242/v1",
    "http://127.0.0.1:4242/v2",
    "http://127.0.0.1:4242/",
    "not a url",
    "http://user:secret@127.0.0.1:4242/v1",
    "http://127.0.0.1:4242/v1?next=x",
    "http://127.0.0.1:4242/v1#x",
  ])("refuses %s before any request", (base_url) => {
    const fetch = vi.fn();
    expect(() =>
      createClient({ ...READY, base_url }, { fetch: fetch as unknown as typeof globalThis.fetch }),
    ).toThrow(TypeError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("accepts a trailing slash", () => {
    expect(() => createClient({ ...READY, base_url: "http://127.0.0.1:4242/v1/" })).not.toThrow();
  });
});

describe("fillPath", () => {
  it("fills and encodes every segment", () => {
    expect(fillPath("/v1/a/{x}/b/{y}", { x: "1/2", y: "z" })).toBe("/v1/a/1%2F2/b/z");
  });

  it("refuses a segment it has no value for", () => {
    expect(() => fillPath("/v1/a/{x}", {})).toThrow(TypeError);
  });
});

describe("the requests", () => {
  const cases: [string, (c: Client) => Promise<unknown>, string, string, unknown][] = [
    ["health", (c) => c.health(), "GET", "/v1/health", null],
    ["status", (c) => c.status(), "GET", "/v1/status", null],
    ["pending", (c) => c.pending(), "GET", "/v1/pending", null],
    ["decisions", (c) => c.decisions(), "GET", "/v1/decisions", null],
    [
      "previewDecision",
      (c) => c.previewDecision("k/1", { action: "merge" }),
      "POST",
      "/v1/decisions/k%2F1/preview",
      { action: "merge" },
    ],
    [
      "applyDecision",
      (c) => c.applyDecision("k1", { action: "merge", version: VERSION }),
      "POST",
      "/v1/decisions/k1/apply",
      { action: "merge", version: VERSION },
    ],
    ["refreshQueue", (c) => c.refreshQueue(), "POST", "/v1/queue/refresh", null],
    [
      "quoteJudging",
      (c) => c.quoteJudging({ kind: "identity" }),
      "POST",
      "/v1/judging/quote",
      { kind: "identity" },
    ],
    ["startJudgingRun", (c) => c.startJudgingRun({ token: null }), "POST", "/v1/judging/runs", { token: null }],
    ["judgingRun", (c) => c.judgingRun("r1"), "GET", "/v1/judging/runs/r1", null],
    ["stopJudgingRun", (c) => c.stopJudgingRun("r1"), "POST", "/v1/judging/runs/r1/stop", null],
    [
      "mergeHistory",
      (c) => c.mergeHistory("concepts/a b"),
      "GET",
      "/v1/merges?concept_id=concepts%2Fa+b",
      null,
    ],
    [
      "previewUnmerge",
      (c) => c.previewUnmerge({ survivor_id: "s", absorbed_id: "a" }),
      "POST",
      "/v1/unmerge/preview",
      { survivor_id: "s", absorbed_id: "a" },
    ],
  ];

  it.each(cases)("%s sends its method, path, body and token", async (_name, call, method, path, body) => {
    const { client, sent } = engine(json({ ok: true }));
    await expect(call(client)).resolves.toEqual({ ok: true });
    expect(sent).toEqual([{ method, url: `http://127.0.0.1:4242${path}`, auth: `Bearer ${TOKEN}`, body }]);
  });

  it("sends a body as JSON and no content type without one", async () => {
    const types: (string | null)[] = [];
    const fetch = async (request: Request) => {
      types.push(request.headers.get("Content-Type"));
      return json({});
    };
    const client = createClient(READY, { fetch: fetch as unknown as typeof globalThis.fetch });
    await client.quoteJudging({ kind: "identity" });
    await client.status();
    expect(types).toEqual(["application/json", null]);
  });

  it("applyUnmerge sends the echoed version", async () => {
    const { client, sent } = engine(json({ ok: true }));
    const version = { survivor_id: "s", absorbed_id: "a", digest: "d" } as never;
    await client.applyUnmerge({ version });
    expect(sent[0]).toMatchObject({ method: "POST", url: "http://127.0.0.1:4242/v1/unmerge/apply" });
    expect(sent[0]!.body).toEqual({ version });
  });

  it("accepts the 202 of a started run", async () => {
    const { client } = engine(json({ run_id: "r1" }, { status: 202 }));
    await expect(client.startJudgingRun({})).resolves.toEqual({ run_id: "r1" });
  });
});

describe("the failures", () => {
  it("turn an envelope into an ApiError", async () => {
    const { client } = engine(envelope(503, "workspace_busy", true, { "Retry-After": "2" }));
    const error = await client.applyDecision("k1", { action: "merge", version: VERSION }).catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect([error.status, error.code, error.retryable, error.retryAfterSeconds]).toEqual([
      503,
      "workspace_busy",
      true,
      2,
    ]);
    expect(error.message).toBe("the workspace_busy message");
    expect(isBusy(error)).toBe(true);
  });

  it.each([
    [413, "request_too_large"],
    [422, "invalid_selection"],
    [500, "merge_group_interrupted"],
    [500, "unmerge_interrupted"],
    [409, "survivor_missing"],
    [409, "not_merged"],
    [409, "survivor_edited"],
    [401, "unauthorized"],
  ])("keep the status %s and code %s", async (status, code) => {
    const { client } = engine(envelope(status, code));
    await expect(client.status()).rejects.toMatchObject({ name: "ApiError", status, code });
  });

  it("read no Retry-After when it is absent or not seconds", async () => {
    for (const headers of [{}, { "Retry-After": "soon" }, { "Retry-After": "-1" }]) {
      const { client } = engine(envelope(503, "workspace_busy", true, headers));
      await expect(client.status()).rejects.toMatchObject({ retryAfterSeconds: null });
    }
  });

  it.each([
    ["an HTML body", new Response("<html>bad gateway</html>", { status: 502 })],
    ["an empty body", new Response(null, { status: 500 })],
    ["a partial envelope", json({ code: "x", message: "m" }, { status: 500 })],
    ["a mistyped envelope", json({ code: "x", retryable: "yes", message: "m" }, { status: 500 })],
    ["a non-JSON success", new Response("ok", { status: 200 })],
  ])("turn %s into a ProtocolError", async (_name, response) => {
    const { client } = engine(response);
    const error = await client.status().catch((e) => e);
    expect(error).toBeInstanceOf(ProtocolError);
    expect([error.status, error.path]).toEqual([response.status, "/v1/status"]);
  });

  it("turn a rejected fetch into EngineUnreachable", async () => {
    const { client } = engine(new TypeError("Failed to fetch http://127.0.0.1:4242/v1/status"));
    const error = await client.status().catch((e) => e);
    expect(error).toBeInstanceOf(EngineUnreachable);
    expect(error.path).toBe("/v1/status");
  });
});

describe("the token", () => {
  it("is in no error and not on the client", async () => {
    const failures = [
      envelope(403, "forbidden"),
      new Response(`<html>${TOKEN}</html>`, { status: 502 }),
      new TypeError(`connect refused ${TOKEN}`),
    ];
    for (const failure of failures) {
      const { client } = engine(failure);
      const error = await client.status().catch((e) => e);
      for (const text of [error.message, String(error), JSON.stringify(error), error.stack ?? ""]) {
        expect(text).not.toContain(TOKEN);
      }
    }
    const { client } = engine(json({}));
    expect(JSON.stringify(client)).not.toContain(TOKEN);
    expect(String(client)).not.toContain(TOKEN);
    expect(Object.values(client).map(String).join()).not.toContain(TOKEN);
  });
});

describe("retryWhileBusy", () => {
  function clock() {
    let now = 0;
    const waits: number[] = [];
    return {
      waits,
      now: () => now,
      sleep: async (ms: number) => {
        waits.push(ms);
        now += ms;
      },
    };
  }
  const busy = (after?: string) =>
    envelope(503, "workspace_busy", true, after === undefined ? {} : { "Retry-After": after });

  it("repeats a busy call until it succeeds, waiting Retry-After", async () => {
    const { client, sent } = engine(busy("2"), json({ ok: true }));
    const time = clock();
    const result = await retryWhileBusy(
      () => client.applyDecision("k1", { action: "merge", version: VERSION }),
      time,
    );
    expect(result).toEqual({ ok: true });
    expect(sent).toHaveLength(2);
    expect(time.waits).toEqual([2000]);
  });

  it("waits one second when Retry-After is absent", async () => {
    const { client } = engine(busy(), json({ ok: true }));
    const time = clock();
    await retryWhileBusy(() => client.status(), time);
    expect(time.waits).toEqual([1000]);
  });

  it("stops before its deadline and rethrows the last busy error", async () => {
    const { client, sent } = engine(busy("2"));
    const time = clock();
    const error = await retryWhileBusy(() => client.status(), { ...time, deadlineMs: 5000 }).catch((e) => e);
    expect(isBusy(error)).toBe(true);
    expect(time.waits).toEqual([2000, 2000]);
    expect(sent).toHaveLength(3);
    expect(time.now()).toBeLessThanOrEqual(5000);
  });

  it("defaults to a five-second deadline", async () => {
    const { client } = engine(busy("1"));
    const time = clock();
    await retryWhileBusy(() => client.status(), time).catch(() => undefined);
    expect(time.waits).toEqual([1000, 1000, 1000, 1000, 1000]);
  });

  it.each([
    ["write_drift", 409, true],
    ["record_failed", 409, true],
    ["invalid_selection", 422, false],
  ])("does not retry %s", async (code, status, retryable) => {
    const { client, sent } = engine(envelope(status, code, retryable), json({ ok: true }));
    const time = clock();
    await expect(retryWhileBusy(() => client.status(), time)).rejects.toMatchObject({ code });
    expect(sent).toHaveLength(1);
    expect(time.waits).toEqual([]);
  });

  it("uses real timers by default", async () => {
    vi.useFakeTimers();
    try {
      const { client, sent } = engine(busy("1"), json({ ok: true }));
      const pending = retryWhileBusy(() => client.status());
      await vi.advanceTimersByTimeAsync(1000);
      await expect(pending).resolves.toEqual({ ok: true });
      expect(sent).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
