// Test fixtures for the Add files screen: a quote, a job and an outcome in the
// API's own shapes. Only tests import this.
import type { Schemas } from "../../client/index.ts";

type Quote = Schemas["IngestQuote"];
type FileQuote = Schemas["IngestFileQuote"];
type Job = Schemas["IngestJob"];
type Outcome = Schemas["IngestRunOutcome"];
type FileResult = Schemas["IngestFileResult"];

export function fileQuote(name: string, patch: Partial<FileQuote> = {}): FileQuote {
  return { calls: 6, name, path: `/Users/me/${name}`, size: 100, status: "extracts", ...patch };
}

export function quote(files: FileQuote[], patch: Partial<Quote> = {}): Quote {
  const addable = files.filter((f) => f.status === "extracts" || f.status === "source_only");
  const calls = addable.reduce((sum, f) => sum + f.calls, 0);
  return {
    backend: "ollama",
    calls,
    files,
    host: "this Mac",
    include_confidential: false,
    is_local: true,
    model: "qwen3",
    token: {
      binding: { backend: "ollama", include_confidential: false, is_local: true, model: "qwen3", settings_digest: "d" },
      calls,
      files: addable.map((f) => ({ calls: f.calls, digest: `sha-${f.name}`, path: f.path })),
    },
    ...patch,
  };
}

export function job(patch: Partial<Job> = {}): Job {
  return { calls: 0, error: null, index: null, outcome: null, phase: null, run_id: "r1", state: "running", total: null, warnings: [], ...patch };
}

export function result(name: string, patch: Partial<FileResult> = {}): FileResult {
  return {
    calls: 3,
    created: 2,
    extraction_degraded: false,
    name,
    path: `/Users/me/${name}`,
    revised: 0,
    source_id: "s1",
    status: "added",
    ...patch,
  };
}

export function outcome(files: FileResult[], patch: Partial<Outcome> = {}): Outcome {
  return { calls: 3, files, indexes: "refreshed", status: "completed", ...patch };
}
