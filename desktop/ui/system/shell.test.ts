// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { appShell, navItem } from "./shell.ts";

describe("navItem", () => {
  it("marks the current page and shows a count only when there is one", () => {
    const onSelect = vi.fn();
    const item = navItem({ id: "review", label: "To review", count: 3 }, true, onSelect);
    item.click();
    expect(item.getAttribute("aria-current")).toBe("page");
    expect(item.querySelector(".ui-count")?.textContent).toBe("3");
    expect(onSelect).toHaveBeenCalledWith("review");
    const quiet = navItem({ id: "review", label: "To review", count: 0 }, false, onSelect);
    expect(quiet.hasAttribute("aria-current")).toBe(false);
    expect(quiet.querySelector(".ui-count")).toBeNull();
  });
});

describe("appShell", () => {
  it("has a labelled navigation and one main region", () => {
    const shell = appShell({ nav: [{ id: "ask", label: "Ask" }, { id: "status", label: "Status" }], current: "status", onNavigate: () => {} });
    expect(shell.root.querySelector('nav[aria-label="Main"]')?.querySelectorAll("button")).toHaveLength(2);
    expect(shell.root.querySelectorAll("main")).toHaveLength(1);
    expect(shell.main.tagName).toBe("MAIN");
    expect(shell.root.querySelector(".ui-wordmark")?.textContent).toBe("openkos");
  });
});
