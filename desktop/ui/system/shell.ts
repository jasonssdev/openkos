// The app shell of the design system (desktop/DESIGN.md): a sidebar with the
// lockup and the navigation, and one main region that can split into list/detail.
import { type Child, el } from "../components.ts";

const SVG = "http://www.w3.org/2000/svg";

function svg<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Record<string, string>): SVGElementTagNameMap[K] {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

/** The two-node isotype of docs/brand.md, colored by the brand tokens. */
export function isotype(): SVGSVGElement {
  const mark = svg("svg", { viewBox: "0 0 48 48", class: "ui-isotype", "aria-hidden": "true", focusable: "false" });
  mark.append(
    svg("line", { x1: "18", y1: "30", x2: "32", y2: "18", class: "ui-isotype__link", "stroke-width": "6", "stroke-linecap": "round" }),
    svg("rect", { x: "7", y: "19", width: "22", height: "22", rx: "6", class: "ui-isotype__core" }),
    svg("rect", { x: "25.5", y: "11.5", width: "13", height: "13", rx: "4", class: "ui-isotype__satellite" }),
  );
  return mark;
}

/** The lockup: the isotype and the lowercase monospace wordmark. */
export function lockup(): HTMLElement {
  return el("div", { class: "ui-lockup" }, isotype(), el("span", { class: "ui-wordmark" }, "openkos"));
}

export interface NavEntry {
  id: string;
  label: string;
  /** Things waiting; shown only when above zero. */
  count?: number;
  /** Listed but not offered yet: focusable, announced as unavailable, inert. */
  disabled?: boolean;
  /** A short word beside the label, e.g. why it is unavailable. */
  hint?: string;
}

/** One item of the sidebar's navigation. */
export function navItem(entry: NavEntry, current: boolean, onSelect: (id: string) => void): HTMLButtonElement {
  const node = el(
    "button",
    { class: "ui-nav-item", attrs: { type: "button" } },
    el("span", { class: "ui-nav-item__label" }, entry.label),
    entry.hint ? el("span", { class: "ui-nav-item__hint" }, entry.hint) : null,
    entry.count ? el("span", { class: "ui-count" }, String(entry.count)) : null,
  );
  node.dataset["focus"] = `nav:${entry.id}`;
  if (entry.disabled) {
    // aria-disabled, not disabled: it stays in the tab order, so it can be found and its hint read.
    node.setAttribute("aria-disabled", "true");
    return node;
  }
  if (current) node.setAttribute("aria-current", "page");
  node.addEventListener("click", () => onSelect(entry.id));
  return node;
}

export interface AppShellOptions {
  nav: NavEntry[];
  current: string;
  onNavigate: (id: string) => void;
  /** The sidebar's foot, e.g. the engine's status. */
  footer?: Child;
}

/** The whole window: sidebar and main. Fill `main` with a view or a splitView; redraw `nav` as the current page or a count changes. */
export function appShell(options: AppShellOptions): { root: HTMLElement; main: HTMLElement; nav: HTMLElement } {
  const main = el("main", { class: "ui-main" });
  const nav = el(
    "nav",
    { class: "ui-nav", attrs: { "aria-label": "Main" } },
    ...options.nav.map((entry) => navItem(entry, entry.id === options.current, options.onNavigate)),
  );
  const root = el(
    "div",
    { class: "ui-shell" },
    el(
      "aside",
      { class: "ui-sidebar" },
      lockup(),
      nav,
      options.footer ? el("div", { class: "ui-sidebar__footer" }, options.footer) : null,
    ),
    main,
  );
  return { root, main, nav };
}

/** A list pane and a detail pane, each scrolling on its own. */
export function splitView(list: HTMLElement, detail: HTMLElement, label: { list: string; detail: string }): HTMLElement {
  return el(
    "div",
    { class: "ui-split" },
    el("section", { class: "ui-split__list", attrs: { "aria-label": label.list } }, list),
    el("section", { class: "ui-split__detail", attrs: { "aria-label": label.detail } }, detail),
  );
}
