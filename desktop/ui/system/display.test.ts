// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { badge, callout, emptyState, facts, listRow, skeleton, spinner } from "./display.ts";

describe("badge", () => {
  it.each([
    ["neutral", "•"],
    ["info", "i"],
    ["warning", "!"],
    ["success", "✓"],
    ["error", "✕"],
  ] as const)("shows the %s tone as its symbol and a word, never color alone", (tone, symbol) => {
    const node = badge(tone, "Checked");
    expect(node.classList).toContain(`ui-badge--${tone}`);
    expect(node.querySelector(".ui-badge__symbol")?.textContent).toBe(symbol);
    expect(node.querySelector(".ui-badge__symbol")?.getAttribute("aria-hidden")).toBe("true");
    expect(node.querySelector(".ui-badge__word")?.textContent).toBe("Checked");
  });

  it.each(["", "   "])("falls back to the tone's own word when given %j", (word) => {
    const node = badge("warning", word);
    expect(node.querySelector(".ui-badge__symbol")?.textContent).toBe("!");
    expect(node.querySelector(".ui-badge__word")?.textContent).toBe("Changed");
  });
});

describe("callout", () => {
  it("names its tone in words before its text", () => {
    const node = callout("error", "The engine stopped.", { title: "Can't reach OpenKOS" });
    expect(node.querySelector(".ui-callout__symbol")?.textContent).toBe("✕");
    expect(node.querySelector(".ui-callout__title")?.textContent).toBe("Problem: Can't reach OpenKOS");
    expect(node.querySelector(".ui-callout__text")?.textContent).toBe("The engine stopped.");
  });
});

describe("listRow", () => {
  it("is a button that marks the selected row as current", () => {
    const onSelect = vi.fn();
    const row = listRow({ title: "Are these the same?", meta: "Marguerite Osei · M. Osei", selected: true, onSelect });
    const open = row.querySelector("button") as HTMLButtonElement;
    open.click();
    expect(row.tagName).toBe("LI");
    expect(open.getAttribute("aria-current")).toBe("true");
    expect(onSelect).toHaveBeenCalledOnce();
    expect(listRow({ title: "t", onSelect }).querySelector("button")?.hasAttribute("aria-current")).toBe(false);
  });
});

describe("loading and empty", () => {
  it("gives the spinner and the skeleton a name for assistive technology", () => {
    expect(spinner("Checking pages…").textContent).toBe("Checking pages…");
    expect(spinner("Checking pages…").getAttribute("role")).toBe("status");
    const loading = skeleton(3, "Loading decisions…");
    expect(loading.getAttribute("aria-busy")).toBe("true");
    expect(loading.querySelectorAll(".ui-skeleton__line")).toHaveLength(3);
    expect(loading.textContent).toBe("Loading decisions…");
  });

  it("says what is empty and what to do", () => {
    const node = emptyState({ title: "Nothing to review", text: "New decisions appear here." });
    expect(node.querySelector("h2")?.textContent).toBe("Nothing to review");
  });
});

describe("facts", () => {
  it("pairs each name with its value, marking machine values as code", () => {
    const node = facts([
      ["Pages", "128"],
      ["Folder", "~/Notes/good-life", "code"],
    ]);
    expect(Array.from(node.querySelectorAll("dt, dd"), (n) => n.textContent)).toEqual(["Pages", "128", "Folder", "~/Notes/good-life"]);
    expect(node.querySelectorAll("dd code")).toHaveLength(1);
  });
});
