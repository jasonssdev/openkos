import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, EngineUnreachable, WorkspaceError, type Schemas, type WorkspaceChoice } from "../../client/index.ts";
import {
  PULL_POLL_MS,
  RUNTIME_POLL_MS,
  SetupModel,
  WelcomeModel,
  modelRows,
  optionalRows,
  setupStep,
  type SetupPorts,
  type WelcomePorts,
} from "./model.ts";

type Readiness = Schemas["SetupReadiness"];
type Pull = Schemas["PullStatus"];

const GiB = 1024 ** 3;

function readiness(over: { reachable?: boolean; installed?: Record<string, boolean | null>; meets?: boolean | null } = {}): Readiness {
  const installed = over.installed ?? { "qwen3:8b": false, "bge-m3": false };
  const models = [
    ...Object.entries(installed).map(([tag, value]) => ({ tag, roles: ["chat"], required: true, installed: value, pullable: true })),
    { tag: "gemma4:26b", roles: ["adjudication"], required: false, installed: false, pullable: true },
  ];
  const reachable = over.reachable ?? true;
  return {
    runtime: {
      backend: "ollama",
      host: "localhost:11434",
      is_local: true,
      reachable,
      version: reachable ? "0.32.9" : null,
      download_url: "https://ollama.com/download",
    },
    models,
    memory: { total_bytes: 64 * GiB, floor_bytes: 32 * GiB, meets_floor: over.meets ?? true },
    ready: reachable && models.filter((m) => m.required).every((m) => m.installed === true),
  };
}

function pull(model: string, over: Partial<Pull> = {}): Pull {
  return {
    pull_id: `p-${model}`,
    model,
    state: "running",
    phase: "downloading",
    completed_bytes: 0,
    total_bytes: null,
    outcome: null,
    error: null,
    ...over,
  };
}

function finished(model: string, status: "completed" | "already_installed" | "stopped", completed = 0, total: number | null = null): Pull {
  return pull(model, {
    state: "finished",
    completed_bytes: completed,
    total_bytes: total,
    outcome: { model, status, completed_bytes: completed, total_bytes: total },
  });
}

function apiError(code: string): ApiError {
  return new ApiError({ status: 503, code, retryable: true, message: "fixed", retryAfterSeconds: null });
}

function ports(over: Partial<SetupPorts> = {}): SetupPorts {
  return {
    setupReadiness: vi.fn(async () => readiness()),
    startModelPull: vi.fn(async (body: { model: string }) => pull(body.model)),
    modelPull: vi.fn(async (id: string) => pull(id.slice(2))),
    stopModelPull: vi.fn(async (id: string) => pull(id.slice(2), { state: "stopping" })),
    openDownload: vi.fn(async () => undefined),
    ...over,
  };
}

async function flush(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("SetupModel: the steps", () => {
  it("is loading, then on the step the readiness calls for", async () => {
    const model = new SetupModel(ports({ setupReadiness: vi.fn(async () => readiness({ reachable: false })) }));
    expect(setupStep(model.state)).toBe("loading");
    await model.load();
    expect(setupStep(model.state)).toBe("runtime");
  });

  it("is on the models step once the runtime answers, and ready when the required models are in", async () => {
    const answers = [readiness(), readiness({ installed: { "qwen3:8b": true, "bge-m3": true } })];
    const model = new SetupModel(ports({ setupReadiness: vi.fn(async () => answers.shift() as Readiness) }));
    await model.load();
    expect(setupStep(model.state)).toBe("models");
    await model.load();
    // The optional model is still missing: setup follows the engine's own `ready` (#1510).
    expect(setupStep(model.state)).toBe("ready");
  });

  it("is failed when the first read fails, and keeps the last readiness when a later one does", async () => {
    const reads = vi.fn<() => Promise<Readiness>>().mockRejectedValueOnce(new EngineUnreachable({ path: "/setup/readiness" }));
    const model = new SetupModel(ports({ setupReadiness: reads }));
    await model.load();
    expect(setupStep(model.state)).toBe("failed");
    reads.mockResolvedValueOnce(readiness()).mockRejectedValueOnce(new Error("x"));
    await model.load();
    await model.load();
    expect(setupStep(model.state)).toBe("models");
    expect(model.state.failed).toBe(true);
  });

  it("lists the required models only, each with what it is doing", async () => {
    const model = new SetupModel(ports({ setupReadiness: vi.fn(async () => readiness({ installed: { "qwen3:8b": true, "bge-m3": false } })) }));
    await model.load();
    expect(modelRows(model.state)).toEqual([
      { tag: "qwen3:8b", status: "installed", pullable: true },
      { tag: "bge-m3", status: "missing", pullable: true },
    ]);
  });

  it("lists the optional models apart, so a first run never pulls one unasked", async () => {
    const model = new SetupModel(ports({ setupReadiness: vi.fn(async () => readiness({ installed: { "qwen3:8b": true, "bge-m3": true } })) }));
    await model.load();
    expect(optionalRows(model.state)).toEqual([{ tag: "gemma4:26b", status: "missing", pullable: true }]);
    expect(modelRows(model.state).map((r) => r.tag)).toEqual(["qwen3:8b", "bge-m3"]);
    expect(setupStep(model.state)).toBe("ready");
  });

  it("downloads an optional model on request, and Download all leaves it out", async () => {
    const p = ports({ setupReadiness: vi.fn(async () => readiness({ installed: { "qwen3:8b": true, "bge-m3": false } })) });
    const model = new SetupModel(p);
    await model.load();
    await model.downloadAll();
    expect(p.startModelPull).toHaveBeenCalledTimes(1);
    expect(p.startModelPull).toHaveBeenCalledWith({ model: "bge-m3" });
  });

  it("does not read twice at once", async () => {
    const reads = vi.fn(async () => readiness());
    const model = new SetupModel(ports({ setupReadiness: reads }));
    await Promise.all([model.load(), model.load()]);
    expect(reads).toHaveBeenCalledTimes(1);
    expect(model.state.checking).toBe(false);
  });

  it("tells subscribers of every change, until they leave", async () => {
    const model = new SetupModel(ports());
    const seen = vi.fn();
    const leave = model.subscribe(seen);
    await model.load();
    expect(seen).toHaveBeenCalled();
    const count = seen.mock.calls.length;
    leave();
    await model.load();
    expect(seen).toHaveBeenCalledTimes(count);
  });
});

describe("SetupModel: waiting for the runtime", () => {
  it("checks again gently while watched, and stops when the runtime answers", async () => {
    const reads = vi
      .fn<() => Promise<Readiness>>()
      .mockResolvedValueOnce(readiness({ reachable: false }))
      .mockResolvedValueOnce(readiness({ reachable: false }))
      .mockResolvedValue(readiness());
    const model = new SetupModel(ports({ setupReadiness: reads }));
    model.watch(true);
    await model.load();
    await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS - 1);
    expect(reads).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(reads).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS);
    expect(reads).toHaveBeenCalledTimes(3);
    expect(setupStep(model.state)).toBe("models");
    await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS * 3);
    expect(reads).toHaveBeenCalledTimes(3);
  });

  it("does not check on its own while not watched, or once disposed", async () => {
    const reads = vi.fn(async () => readiness({ reachable: false }));
    const model = new SetupModel(ports({ setupReadiness: reads }));
    await model.load();
    await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS * 2);
    expect(reads).toHaveBeenCalledTimes(1);
    model.watch(true);
    await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS);
    expect(reads).toHaveBeenCalledTimes(2);
    model.dispose();
    await vi.advanceTimersByTimeAsync(RUNTIME_POLL_MS * 2);
    expect(reads).toHaveBeenCalledTimes(2);
  });

  it("opens the download page, and says so when the browser did not open", async () => {
    const opened = vi.fn<() => Promise<void>>().mockRejectedValueOnce(new Error("no")).mockResolvedValueOnce(undefined);
    const model = new SetupModel(ports({ openDownload: opened }));
    await model.openDownload();
    expect(model.state.browserFailed).toBe(true);
    await model.openDownload();
    expect(model.state.browserFailed).toBe(false);
    expect(opened).toHaveBeenCalledTimes(2);
  });
});

describe("SetupModel: downloading", () => {
  it("follows a download's progress to the end, then reads the readiness again", async () => {
    const polls = [
      pull("bge-m3", { completed_bytes: 600_000_000, total_bytes: 1_200_000_000 }),
      finished("bge-m3", "completed", 1_200_000_000, 1_200_000_000),
    ];
    const reads = vi
      .fn<() => Promise<Readiness>>()
      .mockResolvedValueOnce(readiness({ installed: { "qwen3:8b": true, "bge-m3": false } }))
      .mockResolvedValue(readiness({ installed: { "qwen3:8b": true, "bge-m3": true } }));
    const p = ports({ setupReadiness: reads, modelPull: vi.fn(async () => polls.shift() as Pull) });
    const model = new SetupModel(p);
    await model.load();
    await model.download("bge-m3");
    expect(p.startModelPull).toHaveBeenCalledWith({ model: "bge-m3" });
    expect(modelRows(model.state)[1]?.status).toBe("running");
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    expect(p.modelPull).toHaveBeenCalledWith("p-bge-m3");
    expect(modelRows(model.state)[1]?.progress).toEqual({
      state: "running",
      phase: "downloading",
      completed: 600_000_000,
      total: 1_200_000_000,
    });
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    await flush();
    expect(model.state.pull).toBeNull();
    expect(reads).toHaveBeenCalledTimes(2);
    expect(setupStep(model.state)).toBe("ready");
  });

  it("downloads every missing model one after another, as the engine allows one at a time", async () => {
    const p = ports({ modelPull: vi.fn(async (id: string) => finished(id.slice(2), "completed")) });
    const model = new SetupModel(p);
    await model.load();
    await model.downloadAll();
    expect(p.startModelPull).toHaveBeenCalledTimes(1);
    expect(modelRows(model.state).map((r) => r.status)).toEqual(["running", "waiting"]);
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    await flush();
    expect(p.startModelPull).toHaveBeenNthCalledWith(2, { model: "bge-m3" });
    expect(modelRows(model.state).map((r) => r.status)).toEqual(["missing", "running"]);
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    await flush();
    expect(p.startModelPull).toHaveBeenCalledTimes(2);
    expect(p.setupReadiness).toHaveBeenCalledTimes(2);
  });

  it("queues a second download asked for while one runs, once", async () => {
    const p = ports();
    const model = new SetupModel(p);
    await model.load();
    await model.download("qwen3:8b");
    await model.download("bge-m3");
    await model.download("bge-m3");
    await model.download("qwen3:8b");
    expect(model.state.waiting).toEqual(["bge-m3"]);
    expect(p.startModelPull).toHaveBeenCalledTimes(1);
  });

  it("is already done when the model turned out to be installed", async () => {
    const p = ports({ startModelPull: vi.fn(async () => finished("bge-m3", "already_installed")) });
    const model = new SetupModel(p);
    await model.load();
    await model.download("bge-m3");
    expect(model.state.pull).toBeNull();
    expect(p.modelPull).not.toHaveBeenCalled();
    expect(p.setupReadiness).toHaveBeenCalledTimes(2);
  });

  it("stops the running download and drops the waiting ones, keeping where it stopped", async () => {
    const p = ports({ modelPull: vi.fn(async () => finished("qwen3:8b", "stopped", 1_300_000_000, 5_200_000_000)) });
    const model = new SetupModel(p);
    await model.load();
    await model.downloadAll();
    await model.stop();
    expect(p.stopModelPull).toHaveBeenCalledWith("p-qwen3:8b");
    expect(model.state.waiting).toEqual([]);
    expect(modelRows(model.state)[0]?.progress?.state).toBe("stopping");
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    await flush();
    expect(model.state.pull).toBeNull();
    expect(model.state.notes["qwen3:8b"]).toEqual({
      kind: "stopped",
      text: "Stopped at 1.3 GB of 5.2 GB. Downloading again continues from there.",
    });
    expect(p.startModelPull).toHaveBeenCalledTimes(1);
  });

  it("does nothing on stop when nothing runs, and keeps following when the stop itself fails", async () => {
    const p = ports({ stopModelPull: vi.fn(async () => Promise.reject(new Error("x"))) });
    const model = new SetupModel(p);
    await model.load();
    await model.stop();
    expect(p.stopModelPull).not.toHaveBeenCalled();
    await model.download("bge-m3");
    await model.stop();
    expect(model.state.pull?.model).toBe("bge-m3");
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    expect(p.modelPull).toHaveBeenCalled();
  });

  it("words a refused start on its model, and starts nothing after it", async () => {
    const p = ports({ startModelPull: vi.fn(async () => Promise.reject(apiError("pull_active"))) });
    const model = new SetupModel(p);
    await model.load();
    await model.downloadAll();
    expect(model.state.pull).toBeNull();
    expect(model.state.waiting).toEqual([]);
    expect(model.state.notes["qwen3:8b"]).toEqual({ kind: "failed", text: "Another download is already running. Try again in a moment." });
    expect(p.startModelPull).toHaveBeenCalledTimes(1);
  });

  it("words a download that failed after it started, then reads the readiness again", async () => {
    const failed = (id: string) =>
      pull(id.slice(2), { state: "failed", error: { code: "backend_unavailable", message: "fixed", retryable: true } });
    const p = ports({ modelPull: vi.fn(async (id: string) => failed(id)) });
    const model = new SetupModel(p);
    await model.load();
    await model.downloadAll();
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    await flush();
    expect(model.state.notes["qwen3:8b"]?.text).toBe(
      "OpenKOS lost touch with Ollama. Try again: the download continues from where it stopped.",
    );
    expect(model.state.waiting).toEqual([]);
    expect(p.setupReadiness).toHaveBeenCalledTimes(2);
  });

  it("words a download it lost track of, and clears the note when it is tried again", async () => {
    const p = ports({ modelPull: vi.fn(async () => Promise.reject(new EngineUnreachable({ path: "/setup/pulls/x" }))) });
    const model = new SetupModel(p);
    await model.load();
    await model.download("bge-m3");
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS);
    await flush();
    expect(model.state.notes["bge-m3"]).toEqual({ kind: "failed", text: "OpenKOS isn't responding. Try again in a moment." });
    expect(model.state.pull).toBeNull();
    await model.download("bge-m3");
    expect(model.state.notes["bge-m3"]).toBeUndefined();
  });

  it("stops following a download once disposed", async () => {
    const p = ports();
    const model = new SetupModel(p);
    await model.load();
    await model.download("bge-m3");
    model.dispose();
    await vi.advanceTimersByTimeAsync(PULL_POLL_MS * 3);
    expect(p.modelPull).not.toHaveBeenCalled();
  });
});

function welcomePorts(over: Partial<WelcomePorts> = {}): WelcomePorts {
  const chosen: WorkspaceChoice = { source: "remembered", path: "/Users/me/kb", valid: true };
  return {
    status: vi.fn(async (): Promise<WorkspaceChoice> => ({ source: "none", path: null, valid: false })),
    pickFolder: vi.fn(async () => "/Users/me/kb"),
    create: vi.fn(async () => chosen),
    open: vi.fn(async () => chosen),
    ...over,
  };
}

describe("WelcomeModel", () => {
  it("notices a library chosen before that is no longer where it was", async () => {
    const model = new WelcomeModel(welcomePorts({ status: vi.fn(async () => ({ source: "remembered", path: "/old/kb", valid: false }) as const) }));
    await model.load();
    expect(model.state.moved).toBe("/old/kb");
    const fresh = new WelcomeModel(welcomePorts());
    await fresh.load();
    expect(fresh.state.moved).toBeNull();
    const elsewhere = new WelcomeModel(welcomePorts({ status: vi.fn(async () => ({ source: "environment", path: "/env/kb", valid: false }) as const) }));
    await elsewhere.load();
    expect(elsewhere.state.moved).toBeNull();
    const failing = new WelcomeModel(welcomePorts({ status: vi.fn(async () => Promise.reject(new Error("x"))) }));
    await failing.load();
    expect(failing.state.moved).toBeNull();
  });

  it.each(["create", "open"] as const)("%s: asks for a folder, then hands it to the shell", async (action) => {
    const p = welcomePorts();
    const model = new WelcomeModel(p);
    const seen: (string | null)[] = [];
    model.subscribe((s) => seen.push(s.busy));
    await model[action]();
    expect(p[action]).toHaveBeenCalledWith("/Users/me/kb");
    expect(seen).toContain(action);
    expect(model.state).toEqual({ busy: null, started: true, problem: null, moved: null });
  });

  it("does nothing when the person cancels the folder window", async () => {
    const p = welcomePorts({ pickFolder: vi.fn(async () => null) });
    const model = new WelcomeModel(p);
    await model.create();
    expect(p.create).not.toHaveBeenCalled();
    expect(model.state).toEqual({ busy: null, started: false, problem: null, moved: null });
  });

  it("words a refusal, and clears it on the next try", async () => {
    const refused = new WorkspaceError("folder_not_empty", "detail");
    const create = vi.fn<WelcomePorts["create"]>().mockRejectedValueOnce(refused);
    const model = new WelcomeModel(welcomePorts({ create }));
    await model.create();
    expect(model.state.busy).toBeNull();
    expect(model.state.started).toBe(false);
    expect(model.state.problem?.text).toMatch(/^That folder already has files in it\./);
    await model.create();
    expect(model.state.problem).toBeNull();
    expect(model.state.started).toBe(true);
  });

  it("opens the folder it offered instead", async () => {
    const p = welcomePorts({ create: vi.fn(async () => Promise.reject(new WorkspaceError("already_a_workspace", "d"))) });
    const model = new WelcomeModel(p);
    await model.create();
    expect(model.state.problem?.openPath).toBe("/Users/me/kb");
    await model.openPath("/Users/me/kb");
    expect(p.open).toHaveBeenCalledWith("/Users/me/kb");
    expect(p.pickFolder).toHaveBeenCalledTimes(1);
    expect(model.state.started).toBe(true);
  });

  it("ignores a second choice while one runs, or after a library is chosen", async () => {
    let release: (value: string | null) => void = () => undefined;
    const p = welcomePorts({ pickFolder: vi.fn(() => new Promise<string | null>((resolve) => (release = resolve))) });
    const model = new WelcomeModel(p);
    const first = model.create();
    await model.open();
    release("/Users/me/kb");
    await first;
    await model.open();
    expect(p.pickFolder).toHaveBeenCalledTimes(1);
    expect(p.open).not.toHaveBeenCalled();
  });

  it("words a folder window that failed to open", async () => {
    const model = new WelcomeModel(welcomePorts({ pickFolder: vi.fn(async () => Promise.reject(new Error("x"))) }));
    await model.open();
    expect(model.state.problem).toEqual({ text: "Something went wrong. Please try again." });
  });
});
