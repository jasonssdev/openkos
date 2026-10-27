// Files dropped on the window (#1626). The shell sees the operating system's
// drop (the web view gets paths only from the shell) and tells the page with two
// plain DOM events, so the page needs no event or window permission. A drop only
// ever reaches the same quote step the chooser does; nothing is added from here.

export const DROP_EVENT = "openkos:files-dropped";
export const DRAG_EVENT = "openkos:files-over";
/** On the page's body while files are held over the window. */
export const DRAG_CLASS = "is-dragging-files";

function paths(detail: unknown): string[] | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  return detail.every((item) => typeof item === "string" && item !== "") ? (detail as string[]) : null;
}

/** Listen for drops on `target`; returns the function that stops listening. */
export function listenForDrops(target: EventTarget, body: HTMLElement, onDrop: (paths: string[]) => void): () => void {
  const over = (event: Event) => body.classList.toggle(DRAG_CLASS, (event as CustomEvent).detail === true);
  const drop = (event: Event) => {
    body.classList.remove(DRAG_CLASS);
    const dropped = paths((event as CustomEvent).detail);
    if (dropped !== null) onDrop(dropped);
  };
  target.addEventListener(DRAG_EVENT, over);
  target.addEventListener(DROP_EVENT, drop);
  return () => {
    target.removeEventListener(DRAG_EVENT, over);
    target.removeEventListener(DROP_EVENT, drop);
  };
}
