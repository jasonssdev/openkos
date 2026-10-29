// @vitest-environment jsdom
// The label controls drawn from a real model over a fake client.
import { describe, expect, it, vi } from "vitest";
import { LabelModel, type LabelClient, type PageRef } from "./model.ts";
import { keepHereAction, keepHereState, labelChoice } from "./render.ts";

const GARDEN: PageRef = { conceptId: "concepts/garden", title: "Garden", level: "private" };

function model(): { model: LabelModel; client: { previewSensitivity: ReturnType<typeof vi.fn>; applySensitivity: ReturnType<typeof vi.fn> } } {
  const client = {
    previewSensitivity: vi.fn(async (body: { concept_id: string; level: string }) => ({
      concept_id: body.concept_id,
      title: "Garden",
      current: "private",
      level: body.level,
      direction: "raise",
      also_raised: [],
      version: { concept_id: body.concept_id, level: body.level, content_digest: "d" },
    })),
    applySensitivity: vi.fn(async (body: { version: { concept_id: string; level: string } }) => ({
      changed: { concept_id: body.version.concept_id, level: body.version.level, previous: "private", also_raised: [], commit_sha: null, committed_confidential: false },
      warnings: [],
    })),
  };
  const m = new LabelModel({ cloudHost: () => null, confirm: async () => true, sleep: async () => {} });
  m.setClient(client as unknown as LabelClient);
  return { model: m, client };
}

const text = (node: HTMLElement) => node.textContent ?? "";

describe("the label choice", () => {
  it("shows the three labels with the page's own checked, and its meaning", () => {
    const { model: m } = model();
    const node = labelChoice(GARDEN, m, m.state);
    const radios = Array.from(node.querySelectorAll<HTMLElement>('[role="radio"]'));
    expect(radios.map((r) => [text(r), r.getAttribute("aria-checked")])).toEqual([
      ["Shareable", "false"],
      ["Private", "true"],
      ["Only on this Mac", "false"],
    ]);
    expect(node.querySelector('[role="radiogroup"]')?.getAttribute("aria-label")).toBe("Who can see it");
    expect(text(node)).toContain("Stays in your library. Sent to a cloud AI model only if you turn one on.");
  });

  it("changes the label in one click, then confirms with Undo", async () => {
    const { model: m, client } = model();
    const radios = Array.from(labelChoice(GARDEN, m, m.state).querySelectorAll<HTMLElement>('[role="radio"]'));
    radios[2]!.click();
    await vi.waitFor(() => expect(m.state.notice).not.toBeNull());
    expect(client.applySensitivity).toHaveBeenCalledTimes(1);
    const after = labelChoice(GARDEN, m, m.state);
    expect(text(after)).toContain("“Garden” is now Only on this Mac.");
    const undo = Array.from(after.querySelectorAll("button")).find((b) => text(b) === "Undo");
    expect(undo).toBeDefined();
    undo!.click();
    await vi.waitFor(() => expect(m.state.notice?.text).toBe("“Garden” is Private again."));
  });
});

describe("dismissing a confirmation", () => {
  it("offers Dismiss on a confirmation that has no Undo, and closing it removes it", async () => {
    const { model: m } = model();
    m.set(GARDEN, "confidential");
    await vi.waitFor(() => expect(m.state.notice).not.toBeNull());
    const node = labelChoice(GARDEN, m, m.state);
    const dismiss = Array.from(node.querySelectorAll("button")).find((b) => text(b) === "Dismiss");
    expect(dismiss).toBeDefined();
    dismiss!.click();
    expect(m.state.notice).toBeNull();
    expect(text(labelChoice(GARDEN, m, m.state))).not.toContain("is now");
  });
});

describe("the one action from a citation", () => {
  it("is a button that keeps the page only on this Mac, then a quiet line saying so", async () => {
    const { model: m } = model();
    const node = keepHereAction(GARDEN, m, m.state);
    const button = node.querySelector("button")!;
    expect(text(button)).toBe("Keep only on this Mac");
    button.click();
    await vi.waitFor(() => expect(m.levelOf(GARDEN)).toBe("confidential"));
    const after = keepHereAction(GARDEN, m, m.state);
    expect(text(after)).toContain("is now Only on this Mac");
    expect(Array.from(after.querySelectorAll("button")).map(text)).toEqual(["Undo", "Dismiss"]);
    expect(text(keepHereState(GARDEN, m)!)).toContain("Only on this Mac");
  });

  it("keeps the state apart from the action: the badge is never beside a button's label", () => {
    const { model: m } = model();
    expect(keepHereState(GARDEN, m)).toBeNull();
    const kept = { ...GARDEN, level: "confidential" as const };
    expect(text(keepHereState(kept, m)!)).toContain("Only on this Mac");
    expect(keepHereAction(kept, m, m.state).querySelector("button")).toBeNull();
    expect(text(keepHereAction(kept, m, m.state))).toBe("");
  });
});
