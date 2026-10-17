import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { ApiError, ApiKeyError, WorkspaceError, type Schemas, type WorkspaceChoice } from "../../client/index.ts";
import { LOCAL_MODELS, SettingsModel, cloudOn, type SettingsApi, type SettingsPorts } from "./model.ts";
import { WORDS, savedNote } from "./wording.ts";

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

function setup(over: { settings?: Settings; keySet?: boolean | Error; library?: WorkspaceChoice } = {}) {
  let current = over.settings ?? LOCAL;
  const api = {
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

describe("the packaged local models", () => {
  it("match the engine's own defaults", () => {
    const source = readFileSync(new URL("../../../src/openkos/config.py", import.meta.url), "utf8");
    expect(source).toContain(`DEFAULT_MODEL = "${LOCAL_MODELS.model}"`);
    expect(source).toContain(`DEFAULT_EMBEDDING_MODEL = "${LOCAL_MODELS.embeddingModel}"`);
  });
});
