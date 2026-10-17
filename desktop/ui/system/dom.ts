// The two DOM primitives the design system and the screens are built on
// (ADR-0066): an element whose children are written as text, never as markup,
// and a redraw that keeps focus where it was.

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
