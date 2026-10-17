// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { openDialog } from "./dialog.ts";

let opener: HTMLButtonElement;
let other: HTMLElement;

beforeEach(() => {
  other = document.createElement("main");
  opener = document.createElement("button");
  other.append(opener);
  document.body.append(other);
  opener.focus();
});

afterEach(() => document.body.replaceChildren());

function key(name: string, shiftKey = false): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key: name, shiftKey, bubbles: true, cancelable: true });
  (document.activeElement ?? document.body).dispatchEvent(event);
  return event;
}

function open(overrides: Partial<Parameters<typeof openDialog>[0]> = {}) {
  return openDialog({
    title: "Combine these pages?",
    description: "Marguerite Osei and M. Osei become one page. You can split them again later.",
    confirmLabel: "Combine",
    onConfirm: () => {},
    ...overrides,
  });
}

describe("openDialog", () => {
  it("is a modal dialog labelled by its title and described by its text", () => {
    const { element } = open();
    const dialog = element.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(document.getElementById(dialog.getAttribute("aria-labelledby") ?? "")?.textContent).toBe("Combine these pages?");
    expect(document.getElementById(dialog.getAttribute("aria-describedby") ?? "")?.textContent).toContain("split them again");
  });

  it("is an alert dialog that starts on Cancel when the action is destructive", () => {
    const { element } = open({ destructive: true, confirmLabel: "Forget page" });
    expect(element.querySelector('[role="alertdialog"]')).not.toBeNull();
    expect(document.activeElement?.textContent).toBe("Cancel");
  });

  it("starts on the confirm action otherwise, and hides the rest of the page", () => {
    open();
    expect(document.activeElement?.textContent).toBe("Combine");
    expect(other.hasAttribute("inert")).toBe(true);
  });

  it("keeps Tab inside the dialog in both directions", () => {
    const { element } = open();
    const buttons = Array.from(element.querySelectorAll("button"));
    const [first, last] = [buttons[0] as HTMLButtonElement, buttons.at(-1) as HTMLButtonElement];
    last.focus();
    expect(key("Tab").defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(first);
    expect(key("Tab", true).defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(last);
  });

  it("lets Tab move normally between the dialog's own controls", () => {
    const { element } = open();
    (element.querySelector("button") as HTMLButtonElement).focus();
    expect(key("Tab").defaultPrevented).toBe(false);
  });

  it("brings focus back when it lands outside", () => {
    const { element } = open();
    opener.focus();
    expect(element.contains(document.activeElement)).toBe(true);
  });

  it("cancels on Escape, restores the page and returns focus to the opener", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    const { element } = open({ onCancel, onConfirm });
    expect(key("Escape").defaultPrevented).toBe(true);
    expect(onCancel).toHaveBeenCalledOnce();
    expect(onConfirm).not.toHaveBeenCalled();
    expect(element.isConnected).toBe(false);
    expect(other.hasAttribute("inert")).toBe(false);
    expect(document.activeElement).toBe(opener);
  });

  it("confirms once and closes", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    const { element } = open({ onConfirm, onCancel });
    const confirm = Array.from(element.querySelectorAll("button")).find((b) => b.textContent === "Combine") as HTMLButtonElement;
    confirm.click();
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(onCancel).not.toHaveBeenCalled();
    expect(element.isConnected).toBe(false);
    expect(document.activeElement).toBe(opener);
  });

  it("closes once: a stale close never undoes a later dialog", () => {
    const first = open();
    first.close();
    const second = open({ title: "Forget this page?" });
    first.close();
    expect(second.element.isConnected).toBe(true);
    expect(other.hasAttribute("inert")).toBe(true);
    expect(second.element.contains(document.activeElement)).toBe(true);
  });
});
