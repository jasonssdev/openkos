import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ApiError, ApiKeyError, EngineUnreachable, WatchError, type Schemas } from "../../client/index.ts";
import {
  WATCH,
  WORDS,
  cloudWarning,
  keyAfterSaveFailure,
  keyFailure,
  saveFailure,
  savedNote,
  watchCommandFailure,
  watchFailure,
  whereItRuns,
} from "./wording.ts";

type Settings = Schemas["ModelSettings"];

const LOCAL: Settings = {
  backend: "ollama",
  base_url: null,
  embedding_base_url: null,
  model: "qwen3:8b",
  embedding_model: "bge-m3",
  remote_hosts: [],
  is_local: true,
  takes_api_key: false,
};

const CLOUD: Settings = {
  ...LOCAL,
  backend: "openai-compatible",
  base_url: "https://api.example.com/v1",
  model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  remote_hosts: ["api.example.com"],
  is_local: false,
  takes_api_key: true,
};

function apiError(code: string): ApiError {
  return new ApiError({ status: 422, code, retryable: false, message: "engine text", retryAfterSeconds: null });
}

// The brief's rule 1, plus "key" words that must never stand for the secret itself.
const ENGINE_WORDS = /\b(merge|concept|bundle|okf|tier|volatility|provenance|ingest|adjudicat|judge|canonical|workspace|queue|pending|token|backend|embedding|yaml|sensitivity|confidential|ollama_host|base_url)/i;

describe("the settings wording", () => {
  it("uses no engine word", () => {
    const sentences = [
      ...Object.values(WORDS),
      whereItRuns(LOCAL),
      whereItRuns(CLOUD),
      ...cloudWarning(CLOUD),
      savedNote(CLOUD, true),
      savedNote(LOCAL, false),
    ];
    for (const sentence of sentences) expect(sentence).not.toMatch(ENGINE_WORDS);
  });

  it("uses no engine word to ask about a folder, and names the daemon's other jobs", () => {
    const strings = Object.values(WATCH).flatMap((v) => (typeof v === "function" ? [] : Array.isArray(v) ? v : [v]));
    const sentences = [
      ...strings,
      WATCH.confirmTitle("/Users/ana/Inbox"),
      WATCH.startedNote("/Users/ana/Inbox"),
      WATCH.notStarted(new WatchError("watch_choice_unkept", "d")),
      WATCH.notStarted(new WatchError("no_workspace", "d")),
      WATCH.notStarted(null),
      watchCommandFailure(new WatchError("watch_choice_unkept", "d")),
      watchCommandFailure(null),
      ...["inbox_inside_workspace", "inbox_not_a_directory", "invalid_inbox", "stale"].map((c) => watchFailure(apiError(c))),
    ];
    for (const sentence of sentences) expect(sentence).not.toMatch(ENGINE_WORDS);
    // ADR-0078, "Three" and "Four": what is added, what is never done, and the other jobs.
    expect(WATCH.what.join(" ")).toMatch(/AI model/);
    expect(WATCH.what.join(" ")).toMatch(/never changed/);
    expect(WATCH.what.join(" ")).toMatch(/To review/);
    expect(WATCH.also).toMatch(/tries again to save/);
    expect(WATCH.also).toMatch(/AI model too, within the same daily limit/);
  });

  it("words a refused folder from the engine's code alone", () => {
    expect(watchFailure(apiError("inbox_inside_workspace"))).toBe(WATCH.insideLibrary);
    expect(watchFailure(apiError("inbox_not_a_directory"))).toBe(WATCH.notAFolder);
    expect(watchFailure(apiError("invalid_inbox"))).toBe(WATCH.cannotUse);
    expect(watchFailure(apiError("workspace_busy"))).toBe(saveFailure(apiError("workspace_busy")));
    expect(watchFailure(apiError("inbox_inside_workspace"))).not.toContain("engine text");
  });

  it("says where the AI model runs, naming the host of a cloud one", () => {
    expect(whereItRuns(LOCAL)).toBe("On this Mac.");
    expect(whereItRuns(CLOUD)).toBe("In the cloud, at api.example.com.");
    expect(whereItRuns({ ...CLOUD, remote_hosts: ["a.example", "b.example"] })).toBe(
      "In the cloud, at a.example and b.example.",
    );
  });

  it("warns who receives what, what never leaves, that private is sent, and that it may cost", () => {
    const [who, sent, never, cost] = cloudWarning(CLOUD);
    expect(who).toContain("to api.example.com");
    expect(sent).toContain("Shareable or Private are sent");
    expect(sent).toContain("New pages start as Private");
    expect(never).toBe("Pages marked Only on this Mac are never sent.");
    expect(cost).toBe("api.example.com may charge you for this. Check its prices before you continue.");
  });

  it("confirms the save, and says when search must be rebuilt", () => {
    expect(savedNote(LOCAL, false)).toBe("Saved. OpenKOS now uses the AI model on this Mac.");
    expect(savedNote(CLOUD, true)).toBe(`Saved. OpenKOS now uses the AI model at api.example.com. ${WORDS.searchRebuild}`);
  });

  it("words a refused save from its code alone, never the engine's text", () => {
    const codes = [
      "invalid_base_url",
      "invalid_embedding_base_url",
      "base_url_required",
      "invalid_model",
      "invalid_embedding_model",
      "stale",
      "workspace_busy",
      "write_refused",
      "something_new",
    ];
    const messages = codes.map((code) => saveFailure(apiError(code)));
    for (const message of messages) expect(message).not.toContain("engine text");
    expect(new Set(messages.slice(2)).size).toBe(messages.length - 2);
    expect(saveFailure(apiError("invalid_base_url"))).toContain("https://");
    expect(saveFailure(new EngineUnreachable({ path: "/v1/settings/model" }))).toBe(
      "OpenKOS isn't responding. Nothing was changed.",
    );
    expect(saveFailure(apiError("something_new"))).toBe("Something went wrong. Nothing was changed.");
  });

  it("words a refused key from the shell's reason alone", () => {
    const unknown = new ApiKeyError("unknown", "sk-secret-in-a-message");
    expect(keyFailure(unknown)).not.toContain("sk-secret");
    expect(keyFailure(new ApiKeyError("invalid_key", "x"))).toContain("copied all of it");
    expect(keyFailure(new ApiKeyError("store_failed", "x"))).toContain("keychain");
    expect(keyFailure(new ApiKeyError("not_supported", "x"))).toBe(WORDS.keyNotSupported);
    expect(keyAfterSaveFailure(new ApiKeyError("store_failed", "x"))).toMatch(/^Your AI model settings were saved, but the key wasn't\./);
  });
});

describe("docs/decision-wording.md", () => {
  const brief = readFileSync(new URL("../../../docs/decision-wording.md", import.meta.url), "utf8");
  const settings = brief.slice(brief.indexOf("## Settings"), brief.indexOf("## The sidebar and Status"));

  it("states the warning word for word", () => {
    const host = "{host}";
    for (const line of cloudWarning({ remote_hosts: [host] })) expect(settings).toContain(line);
  });

  it("states every refusal word for word", () => {
    const codes = ["invalid_base_url", "base_url_required", "invalid_model", "invalid_embedding_model", "stale", "workspace_busy", "write_refused", "other"];
    for (const code of codes) expect(settings).toContain(saveFailure(apiError(code)));
    for (const reason of ["invalid_key", "store_failed", "unknown"] as const) {
      expect(settings).toContain(keyFailure(new ApiKeyError(reason, "x")));
    }
    expect(settings).toContain(WORDS.searchRebuild);
    expect(settings).toContain(WORDS.keyNotSupported);
    expect(settings).toContain(WORDS.libraryFromOutside);
  });
});
