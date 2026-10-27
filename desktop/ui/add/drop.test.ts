// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { DRAG_CLASS, DRAG_EVENT, DROP_EVENT, listenForDrops } from "./drop.ts";

const fire = (target: EventTarget, type: string, detail: unknown) => target.dispatchEvent(new CustomEvent(type, { detail }));

afterEach(() => document.body.classList.remove(DRAG_CLASS));

describe("listenForDrops", () => {
  it("hands the dropped paths on", () => {
    const target = new EventTarget();
    const onDrop = vi.fn();
    listenForDrops(target, document.body, onDrop);
    fire(target, DROP_EVENT, ["/Users/me/a.md", "/Users/me/b.md"]);
    expect(onDrop).toHaveBeenCalledWith(["/Users/me/a.md", "/Users/me/b.md"]);
  });

  it("ignores anything that is not a list of text paths", () => {
    const target = new EventTarget();
    const onDrop = vi.fn();
    listenForDrops(target, document.body, onDrop);
    for (const bad of [null, "/a.md", [], [1], ["/a.md", 2], [""], { 0: "/a.md" }]) fire(target, DROP_EVENT, bad);
    expect(onDrop).not.toHaveBeenCalled();
  });

  it("marks the window while files are over it, and clears the mark on leave and on drop", () => {
    const target = new EventTarget();
    listenForDrops(target, document.body, () => {});
    fire(target, DRAG_EVENT, true);
    expect(document.body.classList.contains(DRAG_CLASS)).toBe(true);
    fire(target, DRAG_EVENT, false);
    expect(document.body.classList.contains(DRAG_CLASS)).toBe(false);
    fire(target, DRAG_EVENT, true);
    fire(target, DROP_EVENT, ["/a.md"]);
    expect(document.body.classList.contains(DRAG_CLASS)).toBe(false);
  });

  it("stops listening when asked", () => {
    const target = new EventTarget();
    const onDrop = vi.fn();
    const stop = listenForDrops(target, document.body, onDrop);
    stop();
    fire(target, DROP_EVENT, ["/a.md"]);
    expect(onDrop).not.toHaveBeenCalled();
  });
});
