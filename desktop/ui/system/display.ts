// Display components of the design system (desktop/DESIGN.md). Color never
// stands alone: every tone carries a symbol and a word.
import { type Child, el } from "../components.ts";

export type BadgeTone = "neutral" | "info" | "warning" | "success" | "error";

const TONES: Record<BadgeTone, [symbol: string, word: string]> = {
  neutral: ["•", "Status"],
  info: ["i", "Note"],
  warning: ["!", "Changed"],
  success: ["✓", "Done"],
  error: ["✕", "Problem"],
};

/** A small pill: the tone's symbol, then a word (the tone's own when none is given). */
export function badge(tone: BadgeTone, word?: string): HTMLElement {
  const [symbol, fallback] = TONES[tone];
  return el(
    "span",
    { class: `ui-badge ui-badge--${tone}` },
    el("span", { class: "ui-badge__symbol", attrs: { "aria-hidden": "true" } }, symbol),
    el("span", { class: "ui-badge__word" }, word?.trim() ? word.trim() : fallback),
  );
}

export interface CalloutOptions {
  title?: string;
  actions?: HTMLElement[];
  /** Announce it: "status" politely, "alert" at once. */
  live?: "status" | "alert";
}

/** A notice in the flow of the page: symbol, tone word and title, then text and actions. */
export function callout(tone: Exclude<BadgeTone, "neutral">, text: string, options: CalloutOptions = {}): HTMLElement {
  const [symbol, word] = TONES[tone];
  const node = el(
    "div",
    { class: `ui-callout ui-callout--${tone}` },
    el("span", { class: "ui-callout__symbol", attrs: { "aria-hidden": "true" } }, symbol),
    el(
      "div",
      { class: "ui-callout__body" },
      el("p", { class: "ui-callout__title" }, options.title ? `${word}: ${options.title}` : `${word}:`),
      el("p", { class: "ui-callout__text" }, text),
      options.actions?.length ? el("div", { class: "ui-actions" }, ...options.actions) : null,
    ),
  );
  if (options.live) node.setAttribute("role", options.live);
  return node;
}

export interface CardOptions {
  title?: string;
  /** A short line under the title, e.g. the page's type. */
  meta?: string;
  /** Shown at the end of the header, e.g. a badge. */
  aside?: HTMLElement;
  footer?: Child;
  headingLevel?: 2 | 3;
}

/** A quiet raised surface with a hairline edge. */
export function uiCard(options: CardOptions, ...children: Child[]): HTMLElement {
  const title = options.title ? el(`h${options.headingLevel ?? 3}`, { class: "ui-card__title" }, options.title) : null;
  return el(
    "article",
    { class: "ui-card" },
    title || options.meta || options.aside
      ? el(
          "header",
          { class: "ui-card__header" },
          el("div", { class: "ui-card__heading" }, title, options.meta ? el("p", { class: "ui-card__meta" }, options.meta) : null),
          options.aside ?? null,
        )
      : null,
    el("div", { class: "ui-card__body" }, ...children),
    options.footer ? el("footer", { class: "ui-card__footer" }, options.footer) : null,
  );
}

export interface ListRowOptions {
  title: string;
  meta?: string;
  trailing?: HTMLElement;
  selected?: boolean;
  onSelect: () => unknown;
  focusKey?: string;
}

/** One row of a list: a button with a title, a meta line and a trailing badge. */
export function listRow(options: ListRowOptions): HTMLLIElement {
  const open = el(
    "button",
    { class: "ui-row", attrs: { type: "button" } },
    el(
      "span",
      { class: "ui-row__text" },
      el("span", { class: "ui-row__title" }, options.title),
      options.meta ? el("span", { class: "ui-row__meta" }, options.meta) : null,
    ),
    options.trailing ? el("span", { class: "ui-row__trailing" }, options.trailing) : null,
  );
  if (options.selected) open.setAttribute("aria-current", "true");
  if (options.focusKey) open.dataset["focus"] = options.focusKey;
  open.addEventListener("click", () => void options.onSelect());
  return el("li", { class: "ui-list__item" }, open);
}

/** A labelled list of rows. */
export function uiList(label: string, rows: HTMLLIElement[]): HTMLUListElement {
  return el("ul", { class: "ui-list", attrs: { "aria-label": label } }, ...rows);
}

export interface PageHeaderOptions {
  title: string;
  description?: string;
  actions?: HTMLElement[];
  id?: string;
}

/** The title of a view, which takes focus when the view opens, with its actions. */
export function pageHeader(options: PageHeaderOptions): HTMLElement {
  const title = el("h1", { class: "ui-page-header__title", attrs: { tabindex: "-1" } }, options.title);
  title.dataset["view"] = "heading";
  if (options.id) title.id = options.id;
  return el(
    "header",
    { class: "ui-page-header" },
    el(
      "div",
      { class: "ui-page-header__text" },
      title,
      options.description ? el("p", { class: "ui-page-header__description" }, options.description) : null,
    ),
    options.actions?.length ? el("div", { class: "ui-actions" }, ...options.actions) : null,
  );
}

/** Two things side by side for comparison; stacks when the window is narrow. */
export function compare(label: string, left: HTMLElement, right: HTMLElement): HTMLElement {
  return el("div", { class: "ui-compare", attrs: { role: "group", "aria-label": label } }, left, right);
}

export interface EmptyStateOptions {
  title: string;
  text: string;
  action?: HTMLElement;
}

/** What is empty, why, and the one thing to do about it. */
export function emptyState(options: EmptyStateOptions): HTMLElement {
  return el(
    "div",
    { class: "ui-empty" },
    el("h2", { class: "ui-empty__title" }, options.title),
    el("p", { class: "ui-empty__text" }, options.text),
    options.action ?? null,
  );
}

/** A spinner with its label, announced politely. */
export function spinner(label: string): HTMLElement {
  return el(
    "span",
    { class: "ui-loading", attrs: { role: "status" } },
    el("span", { class: "ui-spinner", attrs: { "aria-hidden": "true" } }),
    el("span", { class: "ui-loading__label" }, label),
  );
}

/** Placeholder lines for content on its way; named for assistive technology only. */
export function skeleton(lines: number, label: string): HTMLElement {
  return el(
    "div",
    { class: "ui-skeleton", attrs: { role: "status", "aria-busy": "true" } },
    el("span", { class: "ui-visually-hidden" }, label),
    ...Array.from({ length: lines }, () => el("span", { class: "ui-skeleton__line", attrs: { "aria-hidden": "true" } })),
  );
}

/** Name/value pairs, e.g. the engine's facts; "code" sets the value in monospace. */
export function facts(items: [name: string, value: string, kind?: "code"][]): HTMLDListElement {
  return el(
    "dl",
    { class: "ui-facts" },
    ...items.flatMap(([name, value, kind]) => [
      el("dt", {}, name),
      el("dd", {}, kind === "code" ? el("code", {}, value) : value),
    ]),
  );
}

export interface DisclosureOptions {
  /** What the hidden part holds, e.g. "Show details". */
  summary: string;
  open?: boolean;
  onToggle?: (open: boolean) => void;
  focusKey?: string;
}

/** Secondary content behind a native details/summary, closed unless asked. */
export function disclosure(options: DisclosureOptions, ...children: Child[]): HTMLDetailsElement {
  const summary = el("summary", { class: "ui-disclosure__summary" }, options.summary);
  if (options.focusKey) summary.dataset["focus"] = options.focusKey;
  const node = el("details", { class: "ui-disclosure" }, summary, el("div", { class: "ui-disclosure__body" }, ...children));
  node.open = options.open ?? false;
  if (options.onToggle) {
    const onToggle = options.onToggle;
    node.addEventListener("toggle", () => onToggle(node.open));
  }
  return node;
}
