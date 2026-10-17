// Draws the status screen from its state with the design system's components
// (ADR-0066): the engine first, then the workspace's counts, with anything
// secondary behind a disclosure. It decides nothing: every word comes from
// view.ts or is fixed here, and every value is written as text.
import type { Schemas } from "../client/index.ts";
import { el, redraw } from "./components.ts";
import { callout, disclosure, facts, pageHeader, skeleton, uiCard } from "./system/index.ts";
import { type EngineWording, workspaceSummary } from "./view.ts";

export type WorkspaceView =
  | { kind: "waiting" }
  | { kind: "read"; status: Schemas["WorkspaceStatus"] }
  | { kind: "failed"; message: string };

export interface StatusScreen {
  engine: EngineWording;
  workspace: WorkspaceView;
}

export interface StatusOptions {
  /** The disclosures the person opened; they stay open across redraws. */
  expanded: ReadonlySet<string>;
  onToggle: (key: string, open: boolean) => void;
}

function more(key: string, summary: string, options: StatusOptions, ...children: HTMLElement[]): HTMLDetailsElement {
  return disclosure(
    { summary, open: options.expanded.has(key), onToggle: (open) => options.onToggle(key, open), focusKey: key },
    ...children,
  );
}

function engineCard(engine: EngineWording, options: StatusOptions): HTMLElement {
  const detail = engine.detail?.trim();
  return uiCard(
    { title: "Engine", headingLevel: 2 },
    el("p", { attrs: { id: "engine-status", role: "status" } }, engine.text),
    detail ? more("engine-detail", "Show details", options, el("pre", { class: "ui-pre" }, detail)) : null,
  );
}

function workspaceCard(workspace: WorkspaceView, options: StatusOptions): HTMLElement {
  switch (workspace.kind) {
    case "waiting":
      return uiCard({ title: "Workspace", headingLevel: 2 }, skeleton(3, "Waiting for the engine."));
    case "failed":
      return uiCard({ title: "Workspace", headingLevel: 2 }, callout("error", workspace.message));
    case "read": {
      const summary = workspaceSummary(workspace.status);
      return uiCard(
        { title: "Workspace", headingLevel: 2, meta: "Read from the engine just now." },
        facts(summary.shown),
        more("workspace-folder", "Show the folder", options, facts(summary.more)),
      );
    }
  }
}

/** Redraws the status screen into `root`, keeping focus on the same control. */
export function renderStatus(root: HTMLElement, screen: StatusScreen, options: StatusOptions): void {
  redraw(root, "status", () =>
    root.replaceChildren(pageHeader({ title: "Status" }), engineCard(screen.engine, options), workspaceCard(screen.workspace, options)),
  );
}
