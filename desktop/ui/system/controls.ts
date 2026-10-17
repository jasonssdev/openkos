// Controls of the design system (desktop/DESIGN.md): plain functions returning
// native elements, styled by the ui- classes of system.css. No decision lives here.
import { el } from "./dom.ts";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "destructive";

export interface UiButtonOptions {
  /** One primary per view; secondary by default. */
  variant?: ButtonVariant;
  size?: "sm" | "md";
  disabled?: boolean;
  /** The action is running: focus stays, clicks are ignored, a spinner shows. */
  loading?: boolean;
  /** What the button says while loading, e.g. "Combining…". */
  loadingLabel?: string;
  /** The id of the visible text that says why the button is unavailable. */
  describedBy?: string;
  /** Restores focus to this button after a redraw. */
  focusKey?: string;
}

/** A small spinner mark for inside a control; decorative, the label says the rest. */
export function spinnerMark(): HTMLElement {
  return el("span", { class: "ui-spinner ui-spinner--inline", attrs: { "aria-hidden": "true" } });
}

/** A native button, labelled by its own text. */
export function uiButton(label: string, onClick: () => unknown, options: UiButtonOptions = {}): HTMLButtonElement {
  const variant = options.variant ?? "secondary";
  const node = el("button", {
    class: `ui-button ui-button--${variant} ui-button--${options.size ?? "md"}`,
    attrs: { type: "button" },
  });
  if (options.loading) {
    node.classList.add("ui-button--loading");
    node.setAttribute("aria-busy", "true");
    node.setAttribute("aria-disabled", "true");
    node.append(spinnerMark(), options.loadingLabel ?? label);
  } else {
    node.append(label);
  }
  if (options.focusKey) node.dataset["focus"] = options.focusKey;
  if (options.describedBy) node.setAttribute("aria-describedby", options.describedBy);
  node.disabled = options.disabled ?? false;
  node.addEventListener("click", () => {
    if (!options.loading) void onClick();
  });
  return node;
}

export interface SegmentOption {
  value: string;
  label: string;
}

const NEXT: Record<string, (index: number, count: number) => number> = {
  ArrowRight: (i, n) => (i + 1) % n,
  ArrowDown: (i, n) => (i + 1) % n,
  ArrowLeft: (i, n) => (i - 1 + n) % n,
  ArrowUp: (i, n) => (i - 1 + n) % n,
  Home: () => 0,
  End: (_i, n) => n - 1,
};

/**
 * A segmented choice: a radio group of buttons with one tab stop. Arrows,
 * Home and End move focus and selection; a click selects.
 */
export function segmented(
  label: string,
  options: SegmentOption[],
  selected: string | undefined,
  onChange: (value: string) => void,
): HTMLElement {
  const group = el("div", { class: "ui-segmented", attrs: { role: "radiogroup", "aria-label": label } });
  const radios = options.map((option) =>
    el("button", { class: "ui-segmented__option", attrs: { type: "button", role: "radio" } }, option.label),
  );
  let current = options.findIndex((option) => option.value === selected);

  const paint = () =>
    radios.forEach((radio, index) => {
      radio.setAttribute("aria-checked", String(index === current));
      radio.tabIndex = index === (current === -1 ? 0 : current) ? 0 : -1;
    });

  const choose = (index: number) => {
    if (index === current) return;
    current = index;
    paint();
    onChange((options[index] as SegmentOption).value);
  };

  radios.forEach((radio, index) => {
    radio.addEventListener("click", () => choose(index));
    radio.addEventListener("keydown", (event) => {
      const move = NEXT[event.key];
      if (!move) return;
      event.preventDefault();
      const target = move(index, radios.length);
      choose(target);
      radios[target]?.focus();
    });
  });
  paint();
  group.append(...radios);
  return group;
}

export interface TextFieldOptions {
  id: string;
  label: string;
  /** "password" masks a secret and keeps it out of autofill and spell checking. */
  type?: "text" | "search" | "password";
  value?: string;
  placeholder?: string;
  hint?: string;
  error?: string;
  /** Keeps the label for assistive technology only, e.g. a search in a toolbar. */
  hideLabel?: boolean;
  onInput?: (value: string) => void;
}

/** A labelled text, search or password input, with an optional hint and error. */
export function textField(options: TextFieldOptions): HTMLElement {
  const input = el("input", { class: "ui-input", attrs: { id: options.id, type: options.type ?? "text" } });
  input.value = options.value ?? "";
  if (options.type === "password") {
    input.autocomplete = "off";
    input.spellcheck = false;
  }
  if (options.placeholder) input.placeholder = options.placeholder;
  const described: string[] = [];
  const hint = options.hint ? el("p", { class: "ui-field__hint", attrs: { id: `${options.id}-hint` } }, options.hint) : null;
  if (hint) described.push(hint.id);
  const error = options.error ? el("p", { class: "ui-field__error", attrs: { id: `${options.id}-error` } }, `✕ ${options.error}`) : null;
  if (error) {
    described.push(error.id);
    input.setAttribute("aria-invalid", "true");
  }
  if (described.length > 0) input.setAttribute("aria-describedby", described.join(" "));
  if (options.onInput) {
    const onInput = options.onInput;
    input.addEventListener("input", () => onInput(input.value));
  }
  const label = el("label", { class: options.hideLabel ? "ui-visually-hidden" : "ui-field__label" }, options.label);
  label.htmlFor = options.id;
  return el("div", { class: `ui-field${options.type === "search" ? " ui-field--search" : ""}` }, label, input, hint, error);
}

export interface ChoiceItem {
  value: string;
  label: string;
  checked: boolean;
}

export interface ChoiceListOptions {
  legend: string;
  type: "checkbox" | "radio";
  name: string;
  items: ChoiceItem[];
  onChange: (value: string) => void;
}

/** A labelled group of native checkboxes or radios; each keeps focus across a redraw. */
export function choiceList(options: ChoiceListOptions): HTMLFieldSetElement {
  const group = el("fieldset", { class: "ui-choices" }, el("legend", { class: "ui-choices__legend" }, options.legend));
  for (const item of options.items) {
    const input = el("input", { class: "ui-choice__input", attrs: { type: options.type, name: options.name, value: item.value } });
    input.checked = item.checked;
    input.dataset["focus"] = `${options.name}:${item.value}`;
    input.addEventListener("change", () => options.onChange(item.value));
    group.append(el("label", { class: "ui-choice" }, input, item.label));
  }
  return group;
}

export interface AnswerOption {
  label: string;
  /** What choosing it does, one line, shown under it. */
  does: string;
  onChoose: () => unknown;
  /** Answers to a question share one weight; "Decide later" is quiet. */
  variant?: ButtonVariant;
  focusKey?: string;
  disabled?: boolean;
  /** Why it is unavailable, shown and tied to the button. */
  reason?: string | null;
}

let answers = 0;

/** The answers to a question: each a button with what it does under it. */
export function answerList(label: string, options: AnswerOption[]): HTMLUListElement {
  const list = el("ul", { class: "ui-answers", attrs: { "aria-label": label } });
  for (const option of options) {
    const reasonId = option.reason ? `ui-answer-reason-${++answers}` : undefined;
    list.append(
      el(
        "li",
        { class: "ui-answer" },
        uiButton(option.label, option.onChoose, {
          variant: option.variant ?? "secondary",
          ...(option.focusKey ? { focusKey: option.focusKey } : {}),
          disabled: option.disabled ?? false,
          ...(reasonId ? { describedBy: reasonId } : {}),
        }),
        el("p", { class: "ui-answer__does" }, option.does),
        reasonId ? el("p", { class: "ui-answer__reason", attrs: { id: reasonId } }, option.reason as string) : null,
      ),
    );
  }
  return list;
}
