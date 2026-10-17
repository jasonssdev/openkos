// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { segmented, textField, uiButton } from "./controls.ts";

afterEach(() => document.body.replaceChildren());

function key(target: HTMLElement, name: string, shiftKey = false): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key: name, shiftKey, bubbles: true, cancelable: true });
  target.dispatchEvent(event);
  return event;
}

describe("uiButton", () => {
  it("is a native button labelled by its text that runs its action", () => {
    const onClick = vi.fn();
    const node = uiButton("Same thing", onClick, { variant: "primary" });
    node.click();
    expect(node.tagName).toBe("BUTTON");
    expect(node.type).toBe("button");
    expect(node.textContent).toBe("Same thing");
    expect(node.classList).toContain("ui-button--primary");
    expect(node.classList).toContain("ui-button--md");
    expect(onClick).toHaveBeenCalledOnce();
  });

  it.each(["primary", "secondary", "quiet", "destructive"] as const)("takes the %s variant at either size", (variant) => {
    const node = uiButton("Go", () => {}, { variant, size: "sm" });
    expect(node.classList).toContain(`ui-button--${variant}`);
    expect(node.classList).toContain("ui-button--sm");
  });

  it("does nothing when disabled, and says why through describedBy", () => {
    const onClick = vi.fn();
    const node = uiButton("Combine", onClick, { disabled: true, describedBy: "why" });
    node.click();
    expect(node.disabled).toBe(true);
    expect(node.getAttribute("aria-describedby")).toBe("why");
    expect(onClick).not.toHaveBeenCalled();
  });

  it("keeps focus but ignores clicks while loading, showing a spinner and its loading label", () => {
    const onClick = vi.fn();
    const node = uiButton("Combine", onClick, { loading: true, loadingLabel: "Combining…" });
    document.body.append(node);
    node.focus();
    node.click();
    expect(onClick).not.toHaveBeenCalled();
    expect(node.disabled).toBe(false);
    expect(document.activeElement).toBe(node);
    expect(node.getAttribute("aria-busy")).toBe("true");
    expect(node.getAttribute("aria-disabled")).toBe("true");
    expect(node.textContent).toBe("Combining…");
    expect(node.querySelector(".ui-spinner")?.getAttribute("aria-hidden")).toBe("true");
  });
});

describe("segmented", () => {
  const OPTIONS = [
    { value: "same", label: "Same thing" },
    { value: "different", label: "Different things" },
    { value: "unsure", label: "Not sure" },
  ];

  function radios(group: HTMLElement): HTMLElement[] {
    return Array.from(group.querySelectorAll<HTMLElement>('[role="radio"]'));
  }

  it("is a labelled radio group with one tab stop on the selected option", () => {
    const group = segmented("Are these the same?", OPTIONS, "different", () => {});
    expect(group.getAttribute("role")).toBe("radiogroup");
    expect(group.getAttribute("aria-label")).toBe("Are these the same?");
    expect(radios(group).map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
    expect(radios(group).map((r) => r.tabIndex)).toEqual([-1, 0, -1]);
  });

  it("puts the tab stop on the first option when nothing is selected yet", () => {
    const group = segmented("Are these the same?", OPTIONS, undefined, () => {});
    expect(radios(group).map((r) => r.getAttribute("aria-checked"))).toEqual(["false", "false", "false"]);
    expect(radios(group).map((r) => r.tabIndex)).toEqual([0, -1, -1]);
  });

  it("selects the clicked option and reports it once", () => {
    const onChange = vi.fn();
    const group = segmented("Are these the same?", OPTIONS, undefined, onChange);
    radios(group)[0]?.click();
    radios(group)[0]?.click();
    expect(radios(group).map((r) => r.getAttribute("aria-checked"))).toEqual(["true", "false", "false"]);
    expect(radios(group).map((r) => r.tabIndex)).toEqual([0, -1, -1]);
    expect(onChange).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledWith("same");
  });

  it.each([
    ["ArrowRight", 1, 2],
    ["ArrowDown", 1, 2],
    ["ArrowLeft", 1, 0],
    ["ArrowUp", 1, 0],
    ["ArrowRight", 2, 0],
    ["ArrowLeft", 0, 2],
    ["Home", 2, 0],
    ["End", 0, 2],
  ] as const)("moves focus and selection with %s from option %d to %d", (name, from, to) => {
    const onChange = vi.fn();
    const group = segmented("Are these the same?", OPTIONS, OPTIONS[from]?.value, onChange);
    document.body.append(group);
    const start = radios(group)[from] as HTMLElement;
    start.focus();
    const event = key(start, name);
    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(radios(group)[to]);
    expect(radios(group)[to]?.getAttribute("aria-checked")).toBe("true");
    expect(onChange).toHaveBeenCalledWith(OPTIONS[to]?.value);
  });

  it("leaves other keys alone", () => {
    const group = segmented("Are these the same?", OPTIONS, "same", () => {});
    expect(key(radios(group)[0] as HTMLElement, "a").defaultPrevented).toBe(false);
  });
});

describe("textField", () => {
  it("labels its input and reports what is typed", () => {
    const onInput = vi.fn();
    const field = textField({ id: "find", label: "Find a page", type: "search", onInput });
    const input = field.querySelector("input") as HTMLInputElement;
    expect(field.querySelector("label")?.htmlFor).toBe("find");
    expect(input.type).toBe("search");
    input.value = "osei";
    input.dispatchEvent(new Event("input"));
    expect(onInput).toHaveBeenCalledWith("osei");
  });

  it("ties a hint and an error to the input, and marks it invalid", () => {
    const field = textField({ id: "name", label: "Name", hint: "As it appears on the page.", error: "A name is needed." });
    const input = field.querySelector("input") as HTMLInputElement;
    expect(input.getAttribute("aria-invalid")).toBe("true");
    const described = (input.getAttribute("aria-describedby") ?? "").split(" ").map((id) => field.querySelector(`#${id}`)?.textContent);
    expect(described).toEqual(["As it appears on the page.", "✕ A name is needed."]);
  });
});
