// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import type { Schemas } from "../client/index.ts";
import { ENGINE_WORDS } from "./review/wording.ts";
import { renderStatus, type StatusScreen } from "./status.ts";

const STATUS = {
  root: "/Users/me/knowledge",
  sources: 4,
  concepts: 21,
  open_decisions_by_kind: { identity: 2 },
} as unknown as Schemas["WorkspaceStatus"];

function draw(screen: StatusScreen, expanded: ReadonlySet<string> = new Set(), onToggle = vi.fn()): HTMLElement {
  const root = document.createElement("div");
  document.body.replaceChildren(root);
  renderStatus(root, screen, { expanded, onToggle });
  return root;
}

const terms = (root: Element) => Array.from(root.querySelectorAll("dt"), (n) => n.textContent);

describe("the status screen", () => {
  it("opens on its focusable title and words the engine", () => {
    const root = draw({ engine: { text: "The engine is running." }, workspace: { kind: "waiting" } });
    const title = root.querySelector("h1") as HTMLElement;
    expect(title.textContent).toBe("Status");
    expect(title.dataset["view"]).toBe("heading");
    expect(root.querySelector("#engine-status")?.textContent).toBe("The engine is running.");
    expect(root.querySelector("#engine-status")?.getAttribute("role")).toBe("status");
    expect(root.querySelector("details")).toBeNull();
  });

  it("keeps the engine's detail behind Show details, trimmed", () => {
    const screen: StatusScreen = {
      engine: { text: "The engine could not be started.", detail: "  trace\n" },
      workspace: { kind: "waiting" },
    };
    const closed = draw(screen);
    const details = closed.querySelector("details") as HTMLDetailsElement;
    expect(details.querySelector("summary")?.textContent).toBe("Show details");
    expect(details.open).toBe(false);
    expect(details.querySelector("pre")?.textContent).toBe("trace");
    expect((draw(screen, new Set(["engine-detail"])).querySelector("details") as HTMLDetailsElement).open).toBe(true);
  });

  it("reports which disclosure was toggled", () => {
    const onToggle = vi.fn();
    const root = draw({ engine: { text: "x", detail: "trace" }, workspace: { kind: "waiting" } }, new Set(), onToggle);
    const details = root.querySelector("details") as HTMLDetailsElement;
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
    expect(onToggle).toHaveBeenCalledWith("engine-detail", true);
  });

  it("shows a placeholder while it waits for the engine", () => {
    const root = draw({ engine: { text: "Starting the engine…" }, workspace: { kind: "waiting" } });
    const placeholder = root.querySelector(".ui-skeleton") as HTMLElement;
    expect(placeholder.getAttribute("role")).toBe("status");
    expect(placeholder.textContent).toBe("Waiting for the engine.");
    expect(root.querySelector("dl")).toBeNull();
  });

  it("shows the counts and keeps the folder behind a disclosure", () => {
    const root = draw({ engine: { text: "The engine is running." }, workspace: { kind: "read", status: STATUS } });
    const [shown, more] = Array.from(root.querySelectorAll("dl"));
    expect(terms(shown as Element)).toEqual(["To review", "Pages", "Files you added"]);
    expect(more?.closest("details")?.querySelector("summary")?.textContent).toBe("Show the folder");
    expect(more?.querySelector("dd code")?.textContent).toBe("/Users/me/knowledge");
    expect(root.querySelector(".ui-card__meta")?.textContent).not.toContain("just now");
  });

  it("states when the counts were read, never that they are current", () => {
    const readAt = new Date(2026, 9, 29, 14, 5);
    const root = draw({ engine: { text: "The engine is running." }, workspace: { kind: "read", status: STATUS, readAt } });
    const meta = root.querySelector(".ui-card__meta")?.textContent ?? "";
    expect(meta).toMatch(/^Read at /);
    expect(meta).toContain(readAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
    expect(meta).not.toContain("just now");
  });

  it("says why the status could not be read, in an error notice", () => {
    const message = "Your library's status could not be read.";
    const root = draw({ engine: { text: "The engine is running." }, workspace: { kind: "failed", message } });
    const notice = root.querySelector(".ui-callout--error") as HTMLElement;
    expect(notice.querySelector(".ui-callout__text")?.textContent).toBe(message);
    expect(root.querySelector("dl")).toBeNull();
  });

  it("names the library and its counts in plain words, never the engine's", () => {
    const root = draw({ engine: { text: "The engine is running." }, workspace: { kind: "read", status: STATUS } });
    expect(Array.from(root.querySelectorAll("h2"), (n) => n.textContent)).toEqual(["Engine", "Your library"]);
    const words = (root.textContent ?? "").replace(STATUS.root, "").toLowerCase();
    for (const word of ENGINE_WORDS) expect(words, word).not.toContain(word);
  });

  it("keeps focus on a disclosure across a redraw", () => {
    const screen: StatusScreen = { engine: { text: "x", detail: "trace" }, workspace: { kind: "waiting" } };
    const root = draw(screen);
    (root.querySelector("summary") as HTMLElement).focus();
    renderStatus(root, { ...screen, engine: { text: "y", detail: "trace" } }, { expanded: new Set(), onToggle: vi.fn() });
    expect(document.activeElement).toBe(root.querySelector("summary"));
  });

  it("offers Check setup only while setup is not ready", () => {
    const onCheckSetup = vi.fn();
    const screen: StatusScreen = { engine: { text: "x" }, workspace: { kind: "waiting" } };
    const root = document.createElement("div");
    document.body.replaceChildren(root);
    renderStatus(root, screen, { expanded: new Set(), onToggle: vi.fn(), onCheckSetup });
    expect(root.querySelector(".ui-page-header button")).toBeNull();
    renderStatus(root, { ...screen, setupNeeded: true }, { expanded: new Set(), onToggle: vi.fn(), onCheckSetup });
    const header = root.querySelector(".ui-page-header") as HTMLElement;
    expect(header.querySelector(".ui-page-header__description")?.textContent).toBe("Some setup steps are left.");
    const check = header.querySelector("button") as HTMLButtonElement;
    expect(check.textContent).toBe("Check setup");
    check.click();
    expect(onCheckSetup).toHaveBeenCalledOnce();
  });
});

describe("search on the status screen", () => {
  it("shows the screen's search section in the library's card once it is read", () => {
    const search = document.createElement("section");
    search.textContent = "Rebuild search";
    const root = document.createElement("div");
    document.body.replaceChildren(root);
    renderStatus(root, { engine: { text: "x" }, workspace: { kind: "read", status: STATUS } }, { expanded: new Set(), onToggle: vi.fn(), search });
    expect(root.querySelectorAll(".ui-card")[1]?.querySelector("section")?.textContent).toBe("Rebuild search");
    const waiting = document.createElement("div");
    renderStatus(waiting, { engine: { text: "x" }, workspace: { kind: "waiting" } }, { expanded: new Set(), onToggle: vi.fn(), search });
    expect(waiting.querySelector("section")).toBeNull();
  });
});
