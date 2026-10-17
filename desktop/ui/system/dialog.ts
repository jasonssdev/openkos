// The design system's dialog (desktop/DESIGN.md): modal, labelled by its
// title, closed by Escape, with focus kept inside and returned on close.
import { type Child, el } from "../components.ts";
import { uiButton } from "./controls.ts";

export interface DialogOptions {
  title: string;
  description?: string;
  body?: Child[];
  confirmLabel: string;
  cancelLabel?: string;
  /** A consequential action: an alert dialog that starts on Cancel. */
  destructive?: boolean;
  onConfirm: () => unknown;
  onCancel?: () => void;
}

const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

let opened = 0;

/** The dialog's panel alone, for showing in place (the gallery). */
export function dialogPanel(options: DialogOptions, onCancel: () => void, onConfirm: () => void): HTMLElement {
  const id = `ui-dialog-${++opened}`;
  const panel = el(
    "div",
    {
      class: "ui-dialog",
      attrs: { role: options.destructive ? "alertdialog" : "dialog", "aria-modal": "true", "aria-labelledby": `${id}-title` },
    },
    el("h2", { class: "ui-dialog__title", attrs: { id: `${id}-title` } }, options.title),
    options.description ? el("p", { class: "ui-dialog__description", attrs: { id: `${id}-description` } }, options.description) : null,
    ...(options.body ?? []),
    el(
      "div",
      { class: "ui-dialog__actions" },
      uiButton(options.cancelLabel ?? "Cancel", onCancel, { variant: "quiet" }),
      uiButton(options.confirmLabel, onConfirm, { variant: options.destructive ? "destructive" : "primary" }),
    ),
  );
  if (options.description) panel.setAttribute("aria-describedby", `${id}-description`);
  return panel;
}

/** Opens a modal confirm dialog over the page; the rest of the page is inert while it is open. */
export function openDialog(options: DialogOptions): { element: HTMLElement; close: () => void } {
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  let open = true;
  const close = () => {
    if (!open) return;
    open = false;
    document.removeEventListener("focusin", keepFocus);
    backdrop.remove();
    for (const node of hidden) node.removeAttribute("inert");
    opener?.focus();
  };
  const panel = dialogPanel(
    options,
    () => {
      close();
      options.onCancel?.();
    },
    () => {
      close();
      void options.onConfirm();
    },
  );
  const backdrop = el("div", { class: "ui-dialog-backdrop" }, panel);
  const focusables = () => Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));

  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      options.onCancel?.();
      return;
    }
    if (event.key !== "Tab") return;
    const all = focusables();
    const [first, last] = [all[0], all.at(-1)];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  });

  function keepFocus(event: FocusEvent): void {
    if (event.target instanceof Node && !panel.contains(event.target)) focusables()[0]?.focus();
  }

  const hidden = Array.from(document.body.children).filter((node) => !node.hasAttribute("inert"));
  for (const node of hidden) node.setAttribute("inert", "");
  document.body.append(backdrop);
  document.addEventListener("focusin", keepFocus);
  const buttons = focusables();
  (options.destructive ? buttons.at(-2) : buttons.at(-1))?.focus();
  return { element: backdrop, close };
}
