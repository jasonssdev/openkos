// The components the screens are built from (ADR-0066): plain functions that
// return elements, styled by class on the custom properties of tokens.css.
// Every value is written as text, never as markup. No decision lives here.

export type Child = Node | string | null | undefined | false;

/** An element with classes, attributes and text or element children. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  options: { class?: string; attrs?: Record<string, string> } = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (options.class) node.className = options.class;
  for (const [name, value] of Object.entries(options.attrs ?? {})) node.setAttribute(name, value);
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export interface ButtonOptions {
  variant?: "primary" | "secondary" | "quiet";
  /** Restores focus to this button after a redraw. */
  focusKey?: string;
  disabled?: boolean;
  /** The id of the visible text that says why the button is unavailable. */
  describedBy?: string;
}

/** A native button: keyboard operable, labelled by its own text. */
export function button(label: string, onClick: () => unknown, options: ButtonOptions = {}): HTMLButtonElement {
  const node = el("button", { class: `button button--${options.variant ?? "secondary"}`, attrs: { type: "button" } }, label);
  if (options.focusKey) node.dataset["focus"] = options.focusKey;
  if (options.describedBy) node.setAttribute("aria-describedby", options.describedBy);
  node.disabled = options.disabled ?? false;
  node.addEventListener("click", () => void onClick());
  return node;
}

export type Tone = "info" | "done" | "warning" | "error";

// Color never stands alone: each tone carries a symbol and a word.
const TONES: Record<Tone, [symbol: string, word: string]> = {
  info: ["i", "Note"],
  done: ["✓", "Done"],
  warning: ["!", "Changed"],
  error: ["✕", "Problem"],
};

/** A message with its tone shown as a symbol, a word and a color. */
export function notice(tone: Tone, text: string, ...actions: HTMLElement[]): HTMLElement {
  const [symbol, word] = TONES[tone];
  return el(
    "div",
    { class: `notice notice--${tone}` },
    el("span", { class: "notice__symbol", attrs: { "aria-hidden": "true" } }, symbol),
    el("span", { class: "notice__word" }, `${word}:`),
    el("span", { class: "notice__text" }, text),
    actions.length > 0 ? el("div", { class: "actions" }, ...actions) : null,
  );
}

/** A heading that can take focus when its view opens. */
export function heading(level: 1 | 2 | 3, text: string, id?: string): HTMLHeadingElement {
  const node = el(`h${level}`, { attrs: { tabindex: "-1" } }, text);
  if (id) node.id = id;
  node.dataset["view"] = "heading";
  return node;
}

/** A bordered card. */
export function card(...children: Child[]): HTMLElement {
  return el("div", { class: "card" }, ...children);
}

/** A labelled group of checkboxes or radios. */
export function choiceGroup(
  legend: string,
  type: "checkbox" | "radio",
  name: string,
  items: { value: string; label: string; checked: boolean }[],
  onChange: (value: string) => void,
): HTMLFieldSetElement {
  const fieldset = el("fieldset", { class: "choices" }, el("legend", {}, legend));
  for (const item of items) {
    const input = el("input", { attrs: { type, name, value: item.value } });
    input.checked = item.checked;
    input.dataset["focus"] = `${name}:${item.value}`;
    input.addEventListener("change", () => onChange(item.value));
    fieldset.append(el("label", { class: "choice" }, input, item.label));
  }
  return fieldset;
}

/**
 * Replaces the content of `root` with `draw` and puts focus back: on the
 * control with the same `data-focus` key when the view is the same, else on the
 * view's heading when focus was inside `root` or the view changed.
 */
export function redraw(root: HTMLElement, viewKey: string, draw: () => void): void {
  const active = document.activeElement;
  const inside = active instanceof HTMLElement && root.contains(active);
  const key = inside ? active.dataset["focus"] : undefined;
  const sameView = root.dataset["view"] === viewKey;
  draw();
  root.dataset["view"] = viewKey;
  if (sameView && key) {
    const again = Array.from(root.querySelectorAll<HTMLElement>("[data-focus]")).find((n) => n.dataset["focus"] === key);
    if (again && !(again instanceof HTMLButtonElement && again.disabled)) {
      again.focus();
      return;
    }
  }
  if (!sameView || inside) root.querySelector<HTMLElement>('[data-view="heading"]')?.focus();
}
