import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  ApiKeyError,
  WatchError,
  WorkspaceError,
  type Schemas,
  type WatchStatus,
  type WorkspaceChoice,
} from "../../client/index.ts";
import {
  LOCAL_MODELS,
  SettingsModel,
  cloudOn,
  watchKind,
  type SettingsApi,
  type SettingsPorts,
  type SettingsState,
} from "./model.ts";
import { WATCH, WORDS, savedNote } from "./wording.ts";

type Settings = Schemas["ModelSettings"];
type Preview = Schemas["ModelSettingsPreview"];

const LOCAL: Settings = {
  backend: "ollama",
  base_url: null,
  embedding_base_url: null,
  model: "llama3:8b",
  embedding_model: "bge-m3",
  remote_hosts: [],
  is_local: true,
  takes_api_key: false,
};

const CLOUD: Settings = {
  backend: "openai-compatible",
  base_url: "https://api.example.com/v1",
  embedding_base_url: null,
  model: "gpt-4o-mini",
  embedding_model: "text-embedding-3-small",
  remote_hosts: ["api.example.com"],
  is_local: false,
  takes_api_key: true,
};

const LIBRARY: WorkspaceChoice = { source: "remembered", path: "/Users/ana/Library", valid: true };

function preview(current: Settings, proposed: Settings, searchRebuild = true): Preview {
  return {
    current,
    proposed,
    changes: [],
    search_rebuild: searchRebuild,
    version: {
      change: {
        backend: proposed.backend,
        base_url: proposed.base_url,
        embedding_base_url: proposed.embedding_base_url,
        model: proposed.model,
        embedding_model: proposed.embedding_model,
      },
      content_digest: "d",
    },
  };
}

function apiError(code: string, status = 422): ApiError {
  return new ApiError({ status, code, retryable: false, message: "engine text", retryAfterSeconds: null });
}

type Pair = Schemas["ReconciledPair"];
type Declined = Schemas["DeclinedContradiction"];

const TITLES: Record<string, string> = { "a/old": "Old plan", "a/new": "New plan", "b/x": "Tea", "b/y": "Coffee", "c/whole": "Whole" };

const SUPERSEDES: Pair = { first_id: "a/old", second_id: "a/new", relation_type: "supersedes", holder_id: "a/new", target_id: "a/old" };
const BOTH_RIGHT: Pair = { first_id: "b/x", second_id: "b/y", relation_type: "reconciled_with", holder_id: null, target_id: null };
const REVISES: Pair = { first_id: "a/old", second_id: "a/new", relation_type: "revises", holder_id: "a/new", target_id: "a/old" };
const LEFT: Declined = { first_id: "b/x", second_id: "b/y", merged_absorbed_id: null, decided_at: "2026-10-01T00:00:00Z" };
const INSIDE: Declined = { first_id: "c/whole", second_id: "b/y", merged_absorbed_id: "b/x", decided_at: "2026-10-02T00:00:00Z" };

type Inbox = Schemas["InboxSettings"];
type InboxPreview = Schemas["InboxSettingsPreview"];

type Activity = Schemas["UnattendedActivity"];

const IDLE: Activity = { last_job: null, budget_exhausted: false, jobs_unreadable: false };
const SPENT: Activity = {
  last_job: {
    kind: "watch",
    started_at: "2026-10-27T10:00:00Z",
    ended_at: "2026-10-27T10:01:00Z",
    outcome: "budget_exhausted",
    detail_code: null,
    units_deferred: 2,
    needs_attention: true,
  },
  budget_exhausted: true,
  jobs_unreadable: false,
};
const FOLDER = "/Users/ana/Inbox";
const OFF: WatchStatus = { state: "off", wanted: false };
const ON: WatchStatus = { state: "on", attempt: 1, pid: 7, wanted: true };

function setup(
  over: {
    settings?: Settings;
    keySet?: boolean | Error;
    library?: WorkspaceChoice;
    pairs?: Pair[];
    declined?: Declined[];
    inbox?: string | null;
    watch?: WatchStatus;
    activity?: Activity;
  } = {},
) {
  const activity: Activity = over.activity ?? IDLE;
  let current = over.settings ?? LOCAL;
  let inbox: Inbox = { inbox: over.inbox ?? null, quiet_seconds: 30, max_calls_per_day: 500 };
  let watch: WatchStatus = over.watch ?? OFF;
  let pairs = over.pairs ?? [];
  let declined = over.declined ?? [];
  const version = (n: string) => ({ n }) as never;
  const api = {
    reconciledPairs: vi.fn(async () => ({ pairs })),
    declinedContradictions: vi.fn(async () => ({ contradictions: declined })),
    readPage: vi.fn(async (id: string) => {
      if (!(id in TITLES)) throw apiError("not_found", 404);
      return { concept_id: id, title: TITLES[id] as string, excerpt: "", okf_type: null, sensitivity: "private", sources: [] } as Schemas["Page"];
    }),
    previewUnreconcile: vi.fn(async (body: Schemas["UnreconcilePreviewRequest"]) => ({
      ...body,
      holder_id: null,
      pages_changed: [],
      relation_type: "supersedes" as const,
      target_id: null,
      version: version("u"),
      withdraws_deprecation: true,
    })),
    applyUnreconcile: vi.fn(async (_body: Schemas["UnreconcileApplyRequest"]) => {
      pairs = [];
      return {} as Schemas["UnreconcileResponse"];
    }),
    previewReopenContradiction: vi.fn(async (body: Schemas["ContradictionReopenPreviewRequest"]) => ({
      ...body,
      merged_absorbed_id: body.merged_absorbed_id ?? null,
      version: version("r"),
    })),
    applyReopenContradiction: vi.fn(async (_body: Schemas["ContradictionReopenApplyRequest"]) => {
      declined = [];
      return {} as Schemas["ContradictionReopenResponse"];
    }),
    unattendedActivity: vi.fn(async () => activity),
    inboxSettings: vi.fn(async () => inbox),
    previewInboxSettings: vi.fn(async (body: Schemas["InboxSettingsPreviewRequest"]): Promise<InboxPreview> => {
      if (body.inbox === inbox.inbox) throw apiError("inbox_unchanged", 409);
      return {
        current: inbox,
        proposed: { ...inbox, inbox: body.inbox === null ? null : `/resolved${body.inbox}` },
        version: { change: { inbox: body.inbox }, content_digest: "i" },
      };
    }),
    applyInboxSettings: vi.fn(async (body: Schemas["InboxSettingsApplyRequest"]) => {
      const to = body.version.change.inbox;
      inbox = { ...inbox, inbox: to === null ? null : `/resolved${to}` };
      return { changed: { settings: inbox, commit_sha: "abc" }, warnings: [] };
    }),
    modelSettings: vi.fn(async () => current),
    previewModelSettings: vi.fn(async (body: Schemas["ModelSettingsPreviewRequest"]): Promise<Preview> => {
      const proposed: Settings =
        body.backend === "openai-compatible"
          ? { ...CLOUD, base_url: body.base_url ?? null, model: body.model, embedding_model: body.embedding_model }
          : { ...LOCAL, model: body.model, embedding_model: body.embedding_model };
      return preview(current, proposed);
    }),
    applyModelSettings: vi.fn(async (body: Schemas["ModelSettingsApplyRequest"]) => {
      const c = body.version.change;
      current = c.backend === "ollama" ? { ...LOCAL, model: c.model, embedding_model: c.embedding_model } : { ...CLOUD, base_url: c.base_url ?? null, model: c.model, embedding_model: c.embedding_model };
      return { changed: { settings: current, commit_sha: "abc" }, warnings: [] };
    }),
  } satisfies SettingsApi;
  const keySet = over.keySet ?? false;
  const ports = {
    watch: {
      state: vi.fn(async () => watch),
      set: vi.fn(async (on: boolean): Promise<WatchStatus> => {
        watch = on ? ON : OFF;
        return watch;
      }),
      pickFolder: vi.fn(async (): Promise<string | null> => FOLDER),
      notifyBudgetStop: vi.fn(async () => {}),
    },
    keys: {
      set: vi.fn(async (_key: string) => {}),
      clear: vi.fn(async () => {}),
      isSet: vi.fn(async () => {
        if (keySet instanceof Error) throw keySet;
        return keySet;
      }),
    },
    workspaces: {
      status: vi.fn(async () => over.library ?? LIBRARY),
      pickFolder: vi.fn(async (): Promise<string | null> => "/Users/ana/Other"),
      open: vi.fn(async (path: string): Promise<WorkspaceChoice> => ({ source: "remembered", path, valid: true })),
      create: vi.fn(async (path: string): Promise<WorkspaceChoice> => ({ source: "remembered", path, valid: true })),
    },
  } satisfies SettingsPorts;
  const model = new SettingsModel(ports);
  return { model, api, ports };
}

async function connected(over: Parameters<typeof setup>[0] = {}) {
  const made = setup(over);
  await made.model.connect(made.api);
  return made;
}

function toCloud(model: SettingsModel): void {
  model.edit();
  model.choose("cloud");
  model.setField("baseUrl", "https://api.example.com/v1");
  model.setField("model", "gpt-4o-mini");
  model.setField("embeddingModel", "text-embedding-3-small");
}

describe("reading the settings", () => {
  it("waits for the engine, then reads the model, the key and the library", async () => {
    const { model, api } = setup({ keySet: true });
    expect(model.state.phase).toBe("waiting");

    await model.connect(api);

    expect(model.state.phase).toBe("ready");
    expect(model.state.settings).toEqual(LOCAL);
    expect(model.state.key).toBe("saved");
    expect(model.state.library).toEqual(LIBRARY);
    expect(cloudOn(model.state)).toBe(false);
  });

  it("knows a cloud model is on", async () => {
    const { model } = await connected({ settings: CLOUD });
    expect(cloudOn(model.state)).toBe(true);
  });

  it("keeps showing the settings while the engine restarts", async () => {
    const { model } = await connected({ settings: CLOUD });

    await model.connect(null);

    expect(model.state.settings).toEqual(CLOUD);
    expect(cloudOn(model.state)).toBe(true);
  });

  it("reports a failed read and reads again", async () => {
    const { model, api } = setup();
    api.modelSettings.mockRejectedValueOnce(apiError("workspace_unreadable", 500));

    await model.connect(api);
    expect(model.state.phase).toBe("failed");

    await model.load();
    expect(model.state.phase).toBe("ready");
  });

  it("drops what an earlier launch answers after a newer one connected", async () => {
    const { model, api } = setup();
    let answer: (value: Settings) => void = () => {};
    const slow = { ...api, modelSettings: vi.fn(() => new Promise<Settings>((resolve) => (answer = resolve))) };
    const first = model.connect(slow);
    await model.connect(api);

    answer(CLOUD);
    await first;

    expect(model.state.settings).toEqual(LOCAL);
  });

  it("marks a Mac that cannot keep a key", async () => {
    const { model } = await connected({ keySet: new ApiKeyError("not_supported", "x") });
    expect(model.state.key).toBe("unsupported");
  });

  it("leaves the key unknown when the shell does not answer", async () => {
    const { model } = await connected({ keySet: new ApiKeyError("unknown", "x") });
    expect(model.state.key).toBeNull();
  });
});

describe("the draft", () => {
  it("starts from the settings in use", async () => {
    const { model } = await connected({ settings: CLOUD });
    model.edit();
    expect(model.state.draft).toEqual({
      where: "cloud",
      baseUrl: "https://api.example.com/v1",
      model: "gpt-4o-mini",
      embeddingModel: "text-embedding-3-small",
      key: "",
    });
  });

  it("offers the packaged local models when coming back from the cloud", async () => {
    const { model } = await connected({ settings: CLOUD });
    model.edit();
    model.choose("local");
    expect(model.state.draft).toMatchObject({ where: "local", ...LOCAL_MODELS });
  });

  it("keeps the local models in use when they are local", async () => {
    const { model } = await connected();
    model.edit();
    model.choose("cloud");
    model.choose("local");
    expect(model.state.draft).toMatchObject({ model: "llama3:8b", embeddingModel: "bge-m3" });
  });

  it("starts a first cloud model blank", async () => {
    const { model } = await connected();
    model.edit();
    model.choose("cloud");
    expect(model.state.draft).toMatchObject({ baseUrl: "", model: "", embeddingModel: "" });
  });

  it("typing does not redraw", async () => {
    const { model } = await connected();
    model.edit();
    const listener = vi.fn();
    model.subscribe(listener);

    model.setField("model", "x");

    expect(listener).not.toHaveBeenCalled();
    expect(model.state.draft.model).toBe("x");
  });

  it("cancelling drops the draft and the key typed", async () => {
    const { model } = await connected();
    toCloud(model);
    model.setField("key", "sk-123");
    model.cancel();
    expect(model.state.editing).toBe(false);
    model.edit();
    expect(model.state.draft.key).toBe("");
  });
});

describe("turning on a cloud model", () => {
  it("shows the warning and changes nothing until it is confirmed", async () => {
    const { model, api, ports } = await connected();
    toCloud(model);
    model.setField("key", "sk-123");

    await model.save();

    expect(model.state.confirming?.lines[0]).toContain("api.example.com");
    expect(api.applyModelSettings).not.toHaveBeenCalled();
    expect(ports.keys.set).not.toHaveBeenCalled();
  });

  it("cancelling the warning keeps the draft and writes nothing", async () => {
    const { model, api } = await connected();
    toCloud(model);
    await model.save();

    model.dismiss();

    expect(model.state.confirming).toBeNull();
    expect(model.state.editing).toBe(true);
    expect(model.state.draft.baseUrl).toBe("https://api.example.com/v1");
    expect(api.applyModelSettings).not.toHaveBeenCalled();
  });

  it("confirming applies the previewed version, then keeps the key", async () => {
    const { model, api, ports } = await connected();
    toCloud(model);
    model.setField("key", "sk-123");
    await model.save();

    await model.confirm();

    expect(api.previewModelSettings).toHaveBeenCalledWith({
      backend: "openai-compatible",
      base_url: "https://api.example.com/v1",
      embedding_base_url: null,
      model: "gpt-4o-mini",
      embedding_model: "text-embedding-3-small",
    });
    expect(api.applyModelSettings.mock.calls[0]?.[0].version.content_digest).toBe("d");
    expect(ports.keys.set).toHaveBeenCalledWith("sk-123");
    expect(api.applyModelSettings.mock.invocationCallOrder[0]).toBeLessThan(ports.keys.set.mock.invocationCallOrder[0] ?? 0);
    expect(model.state.editing).toBe(false);
    expect(model.state.key).toBe("saved");
    expect(model.state.draft.key).toBe("");
    expect(model.state.note).toBe(savedNote(model.state.settings as Settings, true));
    expect(cloudOn(model.state)).toBe(true);
  });

  it("does not warn again for the same service", async () => {
    const { model, api } = await connected({ settings: CLOUD });
    model.edit();
    model.setField("model", "gpt-4o");

    await model.save();

    expect(model.state.confirming).toBeNull();
    expect(api.applyModelSettings).toHaveBeenCalledTimes(1);
  });

  it("warns again for a different service", async () => {
    const { model, api } = await connected({ settings: CLOUD });
    api.previewModelSettings.mockResolvedValueOnce(preview(CLOUD, { ...CLOUD, remote_hosts: ["other.example"] }));
    model.edit();

    await model.save();

    expect(model.state.confirming?.lines[0]).toContain("other.example");
  });

  it("going back to this Mac needs no warning", async () => {
    const { model, api } = await connected({ settings: CLOUD });
    model.edit();
    model.choose("local");

    await model.save();

    expect(model.state.confirming).toBeNull();
    expect(api.applyModelSettings).toHaveBeenCalledTimes(1);
    expect(model.state.note).toBe(savedNote(LOCAL_SETTINGS_AFTER(), true));
  });
});

function LOCAL_SETTINGS_AFTER(): Settings {
  return { ...LOCAL, ...{ model: LOCAL_MODELS.model, embedding_model: LOCAL_MODELS.embeddingModel } };
}

describe("refusals", () => {
  it("words a refused preview and stays in the form", async () => {
    const { model, api } = await connected();
    api.previewModelSettings.mockRejectedValueOnce(apiError("invalid_base_url"));
    toCloud(model);

    await model.save();

    expect(model.state.problem).toContain("https://");
    expect(model.state.editing).toBe(true);
    expect(model.state.saving).toBe(false);
  });

  it("an unchanged setting with a new key keeps only the key", async () => {
    const { model, api, ports } = await connected({ settings: CLOUD });
    api.previewModelSettings.mockRejectedValueOnce(apiError("settings_unchanged", 409));
    model.edit();
    model.setField("key", "sk-new");

    await model.save();

    expect(api.applyModelSettings).not.toHaveBeenCalled();
    expect(ports.keys.set).toHaveBeenCalledWith("sk-new");
    expect(model.state.note).toBe(WORDS.keySavedNote);
  });

  it("nothing changed and no key just closes the form", async () => {
    const { model, api } = await connected();
    api.previewModelSettings.mockRejectedValueOnce(apiError("settings_unchanged", 409));
    model.edit();

    await model.save();

    expect(model.state.editing).toBe(false);
    expect(model.state.note).toBeNull();
    expect(model.state.problem).toBeNull();
  });

  it("a stale apply reads the settings again", async () => {
    const { model, api } = await connected({ settings: CLOUD });
    api.applyModelSettings.mockRejectedValueOnce(apiError("stale", 409));
    model.edit();
    model.setField("model", "gpt-4o");

    await model.save();

    expect(model.state.problem).toContain("changed while you were editing");
    expect(api.modelSettings).toHaveBeenCalledTimes(2);
  });

  it("a key the shell refuses after a save says the settings were saved", async () => {
    const { model, ports } = await connected();
    ports.keys.set.mockRejectedValueOnce(new ApiKeyError("store_failed", "x"));
    toCloud(model);
    model.setField("key", "sk-123");
    await model.save();

    await model.confirm();

    expect(model.state.problem).toMatch(/^Your AI model settings were saved, but the key wasn't\./);
    expect(model.state.settings?.is_local).toBe(false);
  });

  it("a second save while one runs is ignored", async () => {
    const { model, api } = await connected({ settings: CLOUD });
    model.edit();
    model.setField("model", "gpt-4o");

    await Promise.all([model.save(), model.save()]);

    expect(api.previewModelSettings).toHaveBeenCalledTimes(1);
  });
});

describe("the key", () => {
  it("is removed through the shell", async () => {
    const { model, ports } = await connected({ settings: CLOUD, keySet: true });

    await model.removeKey();

    expect(ports.keys.clear).toHaveBeenCalled();
    expect(model.state.key).toBe("none");
    expect(model.state.note).toBe(WORDS.keyRemoved);
  });

  it("is never kept in the state after it is sent", async () => {
    const { model } = await connected();
    toCloud(model);
    model.setField("key", "sk-secret");
    await model.save();
    await model.confirm();

    expect(JSON.stringify(model.state)).not.toContain("sk-secret");
  });
});

describe("the library", () => {
  it("opens another library through the shell", async () => {
    const { model, ports } = await connected();

    await model.openLibrary();

    expect(ports.workspaces.open).toHaveBeenCalledWith("/Users/ana/Other");
    expect(model.state.library?.path).toBe("/Users/ana/Other");
    expect(model.state.librarySwitching).toBe(true);
  });

  it("creates a new library through the shell", async () => {
    const { model, ports } = await connected();

    await model.createLibrary();

    expect(ports.workspaces.create).toHaveBeenCalledWith("/Users/ana/Other");
  });

  it("does nothing when the folder window is closed", async () => {
    const { model, ports } = await connected();
    ports.workspaces.pickFolder.mockResolvedValueOnce(null);

    await model.openLibrary();

    expect(ports.workspaces.open).not.toHaveBeenCalled();
    expect(model.state.libraryBusy).toBeNull();
  });

  it("words a refused folder with the welcome's words", async () => {
    const { model, ports } = await connected();
    ports.workspaces.open.mockRejectedValueOnce(new WorkspaceError("not_a_workspace", "x"));

    await model.openLibrary();

    expect(model.state.libraryProblem?.text).toContain("doesn't hold an OpenKOS library");
  });

  it("stops saying it is switching once the engine is back", async () => {
    const { model, api } = await connected();
    await model.openLibrary();

    await model.connect(api);

    expect(model.state.librarySwitching).toBe(false);
  });
});

describe("decisions you can undo", () => {
  it("lists the pairs you settled and the disagreements you left, named by their pages", async () => {
    const { model } = await connected({ pairs: [SUPERSEDES, BOTH_RIGHT], declined: [LEFT, INSIDE] });

    expect(model.state.settled.map((r) => r.text)).toEqual(["'Old plan' and 'New plan'", "'Tea' and 'Coffee'"]);
    // Found inside a combined page, a disagreement is named by that page alone.
    expect(model.state.declined.map((r) => r.text)).toEqual(["'Tea' and 'Coffee'", "'Whole'"]);
  });

  it("names a page that cannot be read without failing the list", async () => {
    const { model } = await connected({ pairs: [{ ...BOTH_RIGHT, second_id: "gone/page" }] });

    expect(model.state.settled.map((r) => r.text)).toEqual(["'Tea' and 'A page that couldn't be read'"]);
  });

  it("keeps the settings when a list cannot be read, and shows it empty", async () => {
    const { model, api } = setup({ pairs: [BOTH_RIGHT] });
    api.reconciledPairs.mockRejectedValueOnce(new Error("down"));
    await model.connect(api);

    expect(model.state.phase).toBe("ready");
    expect(model.state.settled).toEqual([]);
  });

  it("undoes a settled pair from the preview it was shown, and says what came undone", async () => {
    const { model, api } = await connected({ pairs: [SUPERSEDES] });

    await model.undo("settled", (model.state.settled[0] as { key: string }).key);

    expect(api.previewUnreconcile).toHaveBeenCalledWith({ first_id: "a/old", second_id: "a/new" });
    expect(api.applyUnreconcile).toHaveBeenCalledWith({ version: { n: "u" } });
    expect(model.state.note).toBe(
      "Undone. 'Old plan' is no longer marked out of date. We'll point this out again the next time your pages are checked.",
    );
    expect(model.state.settled).toEqual([]);
    expect(model.state.undoing).toBeNull();
  });

  it("says a page stays out of date when another page still replaces it", async () => {
    const { model, api } = await connected({ pairs: [SUPERSEDES] });
    api.previewUnreconcile.mockResolvedValueOnce({ ...(await api.previewUnreconcile({ first_id: "a/old", second_id: "a/new" })), withdraws_deprecation: false });

    await model.undo("settled", (model.state.settled[0] as { key: string }).key);

    expect(model.state.note).toContain("'Old plan' stays out of date, because another page replaces it.");
  });

  it("asks again about a newer decision when a revision is undone", async () => {
    const { model } = await connected({ pairs: [REVISES] });

    await model.undo("settled", (model.state.settled[0] as { key: string }).key);

    expect(model.state.note).toBe("Undone. We'll ask about these again the next time your pages are checked.");
  });

  it("names no page for a pair that was both right", async () => {
    const { model } = await connected({ pairs: [BOTH_RIGHT] });

    await model.undo("settled", (model.state.settled[0] as { key: string }).key);

    expect(model.state.note).toBe("Undone. We'll point this out again the next time your pages are checked.");
  });

  it("points a disagreement out again, keeping the combined page it was found in", async () => {
    const { model, api } = await connected({ declined: [INSIDE] });

    await model.undo("declined", (model.state.declined[0] as { key: string }).key);

    expect(api.previewReopenContradiction).toHaveBeenCalledWith({ first_id: "c/whole", second_id: "b/y", merged_absorbed_id: "b/x" });
    expect(api.applyReopenContradiction).toHaveBeenCalledWith({ version: { n: "r" } });
    expect(model.state.note).toBe("Undone. We'll point this out again the next time your pages are checked.");
    expect(model.state.declined).toEqual([]);
  });

  it("words an undo that is already done, and reads the lists again", async () => {
    const { model, api } = await connected({ declined: [LEFT] });
    api.previewReopenContradiction.mockRejectedValueOnce(apiError("not_declined", 409));
    api.declinedContradictions.mockClear();

    await model.undo("declined", (model.state.declined[0] as { key: string }).key);

    expect(model.state.problem).toBe("This has already been undone.");
    expect(model.state.note).toBeNull();
    expect(api.applyReopenContradiction).not.toHaveBeenCalled();
    expect(api.declinedContradictions).toHaveBeenCalledTimes(1);
  });

  it("words a pair that is no longer settled that way, and one that changed meanwhile", async () => {
    const { model, api } = await connected({ pairs: [BOTH_RIGHT] });
    const key = (model.state.settled[0] as { key: string }).key;
    api.previewUnreconcile.mockRejectedValueOnce(apiError("not_reconciled", 409));
    await model.undo("settled", key);
    expect(model.state.problem).toBe("These pages aren't settled that way any more.");

    api.applyUnreconcile.mockRejectedValueOnce(apiError("stale", 409));
    await model.undo("settled", key);
    expect(model.state.problem).toBe("These pages changed while you were looking. Here is the current version.");
  });

  it("does not undo twice at once, or an unknown row", async () => {
    const { model, api } = await connected({ declined: [LEFT] });
    const key = (model.state.declined[0] as { key: string }).key;

    await Promise.all([model.undo("declined", key), model.undo("declined", key), model.undo("declined", "nope")]);

    expect(api.previewReopenContradiction).toHaveBeenCalledTimes(1);
  });
});

describe("watching a folder", () => {
  it("reads the folder and the shell's watch beside the model", async () => {
    const { model } = await connected({ inbox: FOLDER, watch: ON });

    expect(model.state.inbox?.inbox).toBe(FOLDER);
    expect(model.state.watch).toEqual(ON);
  });

  it("keeps the settings when the watch cannot be read", async () => {
    const { model, api, ports } = setup();
    ports.watch.state.mockRejectedValue(new WatchError("unknown", "x"));

    await model.connect(api);

    expect(model.state.phase).toBe("ready");
    expect(model.state.watch).toBeNull();
  });

  it("previews the chosen folder and writes nothing until it is confirmed", async () => {
    const { model, api, ports } = await connected();

    await model.chooseWatchFolder();

    expect(api.previewInboxSettings).toHaveBeenCalledWith({ inbox: FOLDER });
    expect(model.state.watchConfirm).toEqual({ folder: `/resolved${FOLDER}` });
    expect(api.applyInboxSettings).not.toHaveBeenCalled();
    expect(ports.watch.set).not.toHaveBeenCalled();
    expect(model.state.watchBusy).toBeNull();
  });

  it("does nothing when the folder window is closed", async () => {
    const { model, api, ports } = await connected();
    ports.watch.pickFolder.mockResolvedValue(null);

    await model.chooseWatchFolder();

    expect(api.previewInboxSettings).not.toHaveBeenCalled();
    expect(model.state.watchConfirm).toBeNull();
    expect(model.state.watchBusy).toBeNull();
  });

  it("saves the folder with the previewed version, then starts the watch, in that order", async () => {
    const { model, api, ports } = await connected();
    const order: string[] = [];
    api.applyInboxSettings.mockImplementationOnce(async () => {
      order.push("apply");
      return {
        changed: { settings: { inbox: `/resolved${FOLDER}`, quiet_seconds: 30, max_calls_per_day: 500 }, commit_sha: "a" },
        warnings: [],
      };
    });
    ports.watch.set.mockImplementationOnce(async () => {
      order.push("start");
      return ON;
    });
    await model.chooseWatchFolder();

    await model.confirmWatch();

    expect(order).toEqual(["apply", "start"]);
    expect(api.applyInboxSettings).toHaveBeenCalledWith({ version: { change: { inbox: FOLDER }, content_digest: "i" } });
    expect(ports.watch.set).toHaveBeenCalledWith(true);
    expect(model.state.inbox?.inbox).toBe(`/resolved${FOLDER}`);
    expect(model.state.watch).toEqual(ON);
    expect(model.state.watchConfirm).toBeNull();
    expect(model.state.note).toBe(WATCH.startedNote(`/resolved${FOLDER}`));
    expect(model.state.problem).toBeNull();
  });

  it("does not write or start anything when the confirmation is cancelled", async () => {
    const { model, api, ports } = await connected();
    await model.chooseWatchFolder();

    model.cancelWatch();
    await model.confirmWatch();

    expect(model.state.watchConfirm).toBeNull();
    expect(api.applyInboxSettings).not.toHaveBeenCalled();
    expect(ports.watch.set).not.toHaveBeenCalled();
  });

  it("says why a folder is refused, in plain words, and asks nothing further", async () => {
    for (const [code, line] of [
      ["inbox_inside_workspace", WATCH.insideLibrary],
      ["inbox_not_a_directory", WATCH.notAFolder],
      ["invalid_inbox", WATCH.cannotUse],
    ] as const) {
      const { model, api, ports } = await connected();
      api.previewInboxSettings.mockRejectedValueOnce(apiError(code));

      await model.chooseWatchFolder();

      expect(model.state.problem).toBe(line);
      expect(model.state.watchConfirm).toBeNull();
      expect(model.state.watchBusy).toBeNull();
      expect(ports.watch.set).not.toHaveBeenCalled();
    }
  });

  it("starts the watch on a folder that is already saved without writing it again", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER });

    await model.chooseWatchFolder();
    expect(model.state.watchConfirm).toEqual({ folder: FOLDER });
    await model.confirmWatch();

    expect(api.applyInboxSettings).not.toHaveBeenCalled();
    expect(ports.watch.set).toHaveBeenCalledWith(true);
    expect(model.state.watch).toEqual(ON);
  });

  it("starts a saved folder that is not being watched", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER });

    await model.startWatch();

    expect(ports.watch.set).toHaveBeenCalledWith(true);
    expect(api.applyInboxSettings).not.toHaveBeenCalled();
    expect(model.state.watch).toEqual(ON);
  });

  it("reads the settings again and does not start when they changed meanwhile", async () => {
    const { model, api, ports } = await connected();
    await model.chooseWatchFolder();
    api.applyInboxSettings.mockRejectedValueOnce(apiError("stale", 409));

    await model.confirmWatch();

    expect(model.state.problem).toBe("Your settings changed while you were editing them. Here they are now.");
    expect(ports.watch.set).not.toHaveBeenCalled();
    expect(api.inboxSettings).toHaveBeenCalledTimes(2);
  });

  it("says the folder was saved when the watch then cannot start", async () => {
    const { model, ports } = await connected();
    const refusal = new WatchError("watch_choice_unkept", "disk");
    ports.watch.set.mockRejectedValueOnce(refusal);
    await model.chooseWatchFolder();

    await model.confirmWatch();

    expect(model.state.problem).toBe(WATCH.notStarted(refusal));
    expect(model.state.problem).toContain("saved");
    expect(model.state.inbox?.inbox).toBe(`/resolved${FOLDER}`);
    expect(model.state.watch).toEqual(OFF);
  });

  it("stops the watch first, then forgets the folder, and remembers the person stopped it", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER, watch: ON });
    const order: string[] = [];
    ports.watch.set.mockImplementationOnce(async () => {
      order.push("stop");
      return OFF;
    });
    api.applyInboxSettings.mockImplementationOnce(async () => {
      order.push("forget");
      return { changed: { settings: { inbox: null, quiet_seconds: 30, max_calls_per_day: 500 }, commit_sha: "a" }, warnings: [] };
    });

    await model.stopWatch();

    expect(order).toEqual(["stop", "forget"]);
    expect(ports.watch.set).toHaveBeenCalledWith(false);
    expect(api.previewInboxSettings).toHaveBeenCalledWith({ inbox: null });
    expect(model.state.inbox?.inbox).toBeNull();
    expect(model.state.watch).toEqual(OFF);
    expect(model.state.stoppedByYou).toBe(true);
    expect(model.state.note).toBe(WATCH.stoppedNote);
  });

  it("reports a folder that stays saved when only the watch could be stopped", async () => {
    const { model, api } = await connected({ inbox: FOLDER, watch: ON });
    api.applyInboxSettings.mockRejectedValueOnce(apiError("write_refused", 409));

    await model.stopWatch();

    expect(model.state.watch).toEqual(OFF);
    expect(model.state.problem).toContain(WATCH.folderKept);
    expect(model.state.watchBusy).toBeNull();
  });

  it("does not start two things at once", async () => {
    const { model, ports } = await connected({ inbox: FOLDER });

    await Promise.all([model.startWatch(), model.startWatch()]);

    expect(ports.watch.set).toHaveBeenCalledTimes(1);
  });
});

describe("the watch status", () => {
  const FAILED: WatchStatus = { state: "failed", reason: "exited", detail: "the daemon exited (1).", wanted: true };
  const kind = (over: Partial<Pick<SettingsState, "watch" | "activity" | "inbox" | "stoppedByYou">>) =>
    watchKind({ watch: OFF, activity: IDLE, inbox: null, stoppedByYou: false, ...over });

  it("is on while the daemon runs and the budget holds", () => {
    expect(kind({ watch: ON, activity: IDLE })).toBe("on");
    expect(kind({ watch: { state: "restarting", attempt: 2, delay_ms: 5, reason: "exited", detail: "", wanted: true } })).toBe("on");
    expect(kind({ watch: { state: "starting", attempt: 1, wanted: true } })).toBe("on");
  });

  it("is stopped on the budget when the daemon runs but the latest job ran out of it", () => {
    expect(kind({ watch: ON, activity: SPENT })).toBe("budget");
  });

  it("is failed when the shell gave up on the daemon, whatever the job record says", () => {
    expect(kind({ watch: FAILED })).toBe("failed");
    expect(kind({ watch: FAILED, activity: SPENT })).toBe("failed");
  });

  it("is stopped when the person turned it off, or a folder is saved and not watched", () => {
    expect(kind({ watch: OFF, stoppedByYou: true })).toBe("stopped");
    expect(kind({ watch: OFF, inbox: { inbox: FOLDER, quiet_seconds: 30, max_calls_per_day: 5 } })).toBe("stopped");
  });

  it("says nothing before anyone turned the watch on, or when the shell cannot say", () => {
    expect(kind({ watch: OFF })).toBeNull();
    expect(kind({ watch: null })).toBeNull();
    expect(kind({ watch: { state: "no_workspace", wanted: true } })).toBeNull();
  });

  it("does not report a spent budget for a watch nobody wants", () => {
    expect(kind({ watch: OFF, activity: SPENT })).toBeNull();
  });

  it("reads the job record with the settings", async () => {
    const { model } = await connected({ watch: ON, activity: SPENT });

    expect(model.state.activity?.budget_exhausted).toBe(true);
    expect(watchKind(model.state)).toBe("budget");
  });

  it("is read again on request, without touching anything else", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER, watch: ON });
    ports.watch.state.mockResolvedValue({ state: "failed", reason: "exited", detail: "x", wanted: true });
    api.unattendedActivity.mockResolvedValue(SPENT);
    const settingsReads = api.modelSettings.mock.calls.length;

    await model.refreshWatch();

    expect(model.state.watch?.state).toBe("failed");
    expect(model.state.activity).toEqual(SPENT);
    expect(api.modelSettings).toHaveBeenCalledTimes(settingsReads);
  });

  it("asks the engine for the job record only while the watch is wanted", async () => {
    const { model, api } = await connected({ inbox: FOLDER, watch: OFF });
    api.unattendedActivity.mockClear();

    await model.refreshWatch();

    expect(api.unattendedActivity).not.toHaveBeenCalled();
  });

  it("keeps the last job record when it cannot be read, and still reads the shell", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER, watch: ON, activity: SPENT });
    api.unattendedActivity.mockRejectedValue(new Error("engine restarting"));
    ports.watch.state.mockResolvedValue(ON);

    await model.refreshWatch();

    expect(model.state.activity).toEqual(SPENT);
    expect(model.state.watch).toEqual(ON);
  });

  it("does not notify for a stop that was already there when the app looked first", async () => {
    const { model, ports } = await connected({ inbox: FOLDER, watch: ON, activity: SPENT });

    await model.refreshWatch();

    expect(watchKind(model.state)).toBe("budget");
    expect(ports.watch.notifyBudgetStop).not.toHaveBeenCalled();
  });

  it("notifies once when the daemon stops on the limit while the app is open", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER, watch: ON });
    api.unattendedActivity.mockResolvedValue(SPENT);

    await model.refreshWatch();
    await model.refreshWatch();

    expect(ports.watch.notifyBudgetStop).toHaveBeenCalledTimes(1);
  });

  it("notifies again when the limit is reached again after a clean pass", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER, watch: ON });
    for (const next of [SPENT, IDLE, SPENT]) {
      api.unattendedActivity.mockResolvedValue(next);
      await model.refreshWatch();
    }

    expect(ports.watch.notifyBudgetStop).toHaveBeenCalledTimes(2);
  });

  it("notices a stop that arrives while Settings reloads, not only on the timer", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER, watch: ON });
    api.unattendedActivity.mockResolvedValue(SPENT);

    await model.load();
    await model.refreshWatch();

    expect(ports.watch.notifyBudgetStop).toHaveBeenCalledTimes(1);
  });

  it("leaves a failure to the shell, which raises its own notification", async () => {
    const { model, ports } = await connected({ inbox: FOLDER, watch: ON });
    ports.watch.state.mockResolvedValue({ state: "failed", reason: "exited", detail: "x", wanted: true });

    await model.refreshWatch();

    expect(watchKind(model.state)).toBe("failed");
    expect(ports.watch.notifyBudgetStop).not.toHaveBeenCalled();
  });

  it("carries on when the notification cannot be shown", async () => {
    const { model, api, ports } = await connected({ inbox: FOLDER, watch: ON });
    ports.watch.notifyBudgetStop.mockRejectedValue(new WatchError("unknown", "x"));
    api.unattendedActivity.mockResolvedValue(SPENT);

    await expect(model.refreshWatch()).resolves.toBeUndefined();

    expect(watchKind(model.state)).toBe("budget");
  });

  it("starts a failed watch over with Start again", async () => {
    const { model, ports } = await connected({ inbox: FOLDER, watch: FAILED });

    await model.startWatch();

    expect(ports.watch.set).toHaveBeenCalledWith(true);
    expect(model.state.watch).toEqual(ON);
  });
});

describe("the packaged local models", () => {
  it("match the engine's own defaults", () => {
    const source = readFileSync(new URL("../../../src/openkos/config.py", import.meta.url), "utf8");
    expect(source).toContain(`DEFAULT_MODEL = "${LOCAL_MODELS.model}"`);
    expect(source).toContain(`DEFAULT_EMBEDDING_MODEL = "${LOCAL_MODELS.embeddingModel}"`);
  });
});
