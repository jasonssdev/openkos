import { describe, expect, it, vi } from "vitest";
import { ApiError, type Schemas } from "../../client/index.ts";
import { LabelModel, type LabelClient, type PageRef } from "./model.ts";
import { changedLine, lowerWarning, undoneLine, type Level } from "./wording.ts";

type Preview = Schemas["SensitivityPreview"];

const GARDEN: PageRef = { conceptId: "concepts/garden", title: "Garden", level: "private" };

function apiError(code: string, status = 409, retryAfterSeconds: number | null = null): ApiError {
  return new ApiError({ status, code, retryable: code === "workspace_busy", message: "engine text", retryAfterSeconds });
}

function previewOf(page: PageRef, level: Level, alsoRaised: string[] = []): Preview {
  return {
    concept_id: page.conceptId,
    title: page.title,
    current: page.level,
    level,
    direction: "raise",
    also_raised: alsoRaised.map((id) => ({ concept_id: id, title: id, before: "private", after: level })),
    version: { concept_id: page.conceptId, level, content_digest: "d" },
  };
}

function setup(over: { cloud?: string | null; answer?: boolean; alsoRaised?: string[] } = {}) {
  const client = {
    previewSensitivity: vi.fn(async (body: { concept_id: string; level: Level }) =>
      previewOf({ ...GARDEN, conceptId: body.concept_id }, body.level, over.alsoRaised ?? []),
    ),
    applySensitivity: vi.fn(async (body: { version: Preview["version"] }) => ({
      changed: {
        concept_id: body.version.concept_id,
        level: body.version.level,
        previous: "private",
        also_raised: over.alsoRaised ?? [],
        commit_sha: "abc",
        committed_confidential: body.version.level === "confidential",
      },
      warnings: [],
    })),
  };
  const confirm = vi.fn(async () => over.answer ?? true);
  const model = new LabelModel({ cloudHost: () => over.cloud ?? null, confirm, sleep: async () => {} });
  model.setClient(client as unknown as LabelClient);
  return { model, client, confirm };
}

describe("the label model", () => {
  it("marks a page Only on this Mac in one step: preview, then apply the previewed version", async () => {
    const { model, client, confirm } = setup();
    await model.set(GARDEN, "confidential");
    expect(client.previewSensitivity).toHaveBeenCalledWith({ concept_id: GARDEN.conceptId, level: "confidential" });
    expect(client.applySensitivity).toHaveBeenCalledWith({
      version: { concept_id: GARDEN.conceptId, level: "confidential", content_digest: "d" },
    });
    expect(confirm).not.toHaveBeenCalled();
    expect(model.levelOf(GARDEN)).toBe("confidential");
    expect(model.state.notice).toEqual({
      conceptId: GARDEN.conceptId,
      text: changedLine("Garden", "confidential", 0),
      undo: "private",
    });
    expect(model.state.busy).toBeNull();
  });

  it("undoes by putting back the level it replaced", async () => {
    const { model, client } = setup({ cloud: "api.example.com" });
    await model.set(GARDEN, "confidential");
    await model.undo();
    expect(client.applySensitivity).toHaveBeenLastCalledWith({
      version: { concept_id: GARDEN.conceptId, level: "private", content_digest: "d" },
    });
    expect(model.levelOf(GARDEN)).toBe("private");
    expect(model.state.notice).toEqual({ conceptId: GARDEN.conceptId, text: undoneLine("Garden", "private"), undo: null });
  });

  it("undoes to the level the engine read, not the one the screen guessed", async () => {
    const { model, client } = setup();
    client.previewSensitivity.mockImplementationOnce(async (body: { concept_id: string; level: Level }) => ({
      ...previewOf(GARDEN, body.level),
      current: "public",
    }));
    await model.set(GARDEN, "confidential");
    expect(model.state.notice?.undo).toBe("public");
    await model.undo();
    expect(client.previewSensitivity).toHaveBeenLastCalledWith({ concept_id: GARDEN.conceptId, level: "public" });
  });

  it("offers no undo when pages made from it were raised too", async () => {
    const { model } = setup({ alsoRaised: ["concepts/a", "concepts/b"] });
    await model.set(GARDEN, "confidential");
    expect(model.state.notice?.text).toBe(changedLine("Garden", "confidential", 2));
    expect(model.state.notice?.undo).toBeNull();
    expect(model.levelOf({ conceptId: "concepts/a", title: "a", level: "private" })).toBe("confidential");
  });

  it("asks first before letting a page marked Only on this Mac out while a cloud AI model is on", async () => {
    const kept: PageRef = { ...GARDEN, level: "confidential" };
    const declined = setup({ cloud: "api.example.com", answer: false });
    await declined.model.set(kept, "private");
    expect(declined.confirm).toHaveBeenCalledWith(lowerWarning("Garden", "private", "api.example.com"));
    expect(declined.client.previewSensitivity).not.toHaveBeenCalled();
    expect(declined.model.levelOf(kept)).toBe("confidential");

    const agreed = setup({ cloud: "api.example.com", answer: true });
    await agreed.model.set(kept, "private");
    expect(agreed.model.levelOf(kept)).toBe("private");

    const local = setup({ cloud: null });
    await local.model.set(kept, "public");
    expect(local.confirm).not.toHaveBeenCalled();
    expect(local.model.levelOf(kept)).toBe("public");
  });

  it("does nothing for the level a page already has", async () => {
    const { model, client } = setup();
    await model.set(GARDEN, "private");
    expect(client.previewSensitivity).not.toHaveBeenCalled();
  });

  it("retries a busy apply, never a preview", async () => {
    const { model, client } = setup();
    client.applySensitivity.mockRejectedValueOnce(apiError("workspace_busy", 503, 1));
    await model.set(GARDEN, "confidential");
    expect(client.applySensitivity).toHaveBeenCalledTimes(2);
    expect(model.levelOf(GARDEN)).toBe("confidential");

    const failed = setup();
    failed.client.previewSensitivity.mockRejectedValueOnce(apiError("workspace_busy", 503, 1));
    await failed.model.set(GARDEN, "confidential");
    expect(failed.client.previewSensitivity).toHaveBeenCalledTimes(1);
    expect(failed.model.state.problem).toEqual({
      conceptId: GARDEN.conceptId,
      text: "OpenKOS is finishing something else. Nothing was changed.",
    });
  });

  it("words a failure after the change was sent without claiming nothing changed", async () => {
    const { model, client } = setup();
    client.applySensitivity.mockRejectedValueOnce(new Error("boom"));
    await model.set(GARDEN, "confidential");
    expect(model.state.problem?.text).toBe("Something went wrong. Look at the page again to see whether it changed.");
    expect(model.levelOf(GARDEN)).toBe("private");
    expect(model.state.busy).toBeNull();
  });

  it("treats a level already set elsewhere as set", async () => {
    const { model, client } = setup();
    client.previewSensitivity.mockRejectedValueOnce(apiError("sensitivity_unchanged"));
    await model.set(GARDEN, "confidential");
    expect(model.levelOf(GARDEN)).toBe("confidential");
    expect(model.state.problem).toBeNull();
  });

  it("does nothing with no engine, and one change at a time", async () => {
    const { model, client } = setup();
    model.setClient(null);
    await model.set(GARDEN, "confidential");
    expect(client.previewSensitivity).not.toHaveBeenCalled();
    model.setClient(client as unknown as LabelClient);
    const first = model.set(GARDEN, "confidential");
    await model.set({ ...GARDEN, conceptId: "concepts/other" }, "confidential");
    await first;
    expect(client.previewSensitivity).toHaveBeenCalledTimes(1);
  });

  it("tells its listeners about every change", async () => {
    const { model } = setup();
    const seen: (string | null)[] = [];
    model.subscribe((state) => seen.push(state.busy));
    await model.set(GARDEN, "confidential");
    expect(seen).toEqual([GARDEN.conceptId, null]);
  });
});
