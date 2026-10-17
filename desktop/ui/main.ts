// The app's shell: the shell process supervises the engine, and this page reads
// its state, draws the app shell (sidebar and main), the status page and the
// review screen, and creates one client and one review model per engine launch
// (a restarted engine has a new port and token). Every value is written with
// textContent, never as HTML, and nothing is logged.
import { createClient, type EngineState } from "../client/index.ts";
import { el, redraw } from "./components.ts";
import { ReviewModel } from "./review/model.ts";
import { renderReview } from "./review/render.ts";
import { renderStatus, type StatusScreen } from "./status.ts";
import { appShell, badge, navItem } from "./system/index.ts";
import { describeEngine, engineBadge, navEntries, openDecisions, reviewCount, statusFailure } from "./view.ts";

declare global {
  interface Window {
    // `app.withGlobalTauri` in tauri.conf.json.
    __TAURI__: { core: { invoke<T>(command: string): Promise<T> } };
  }
}

const POLL_MS = 1000;

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`the page has no #${id}`);
  return found as T;
}

type View = "status" | "review";
let view: View = "status";

const live = { status: element("review-live-status"), alert: element("review-live-alert") };
const engineFooter = el("div");
const statusRoot = el("div", { class: "ui-view" });
// The review screen keeps its own styles (style.css) until it moves onto the
// design system; ui-legacy keeps the system's resets off it.
const reviewWaiting = el("p", {}, "Waiting for OpenKOS to start…");
const reviewRoot = el("div", { attrs: { id: "review" } });
const reviewView = el("div", { class: "ui-view ui-legacy" }, reviewWaiting, reviewRoot);
reviewView.hidden = true;

const shell = appShell({ nav: [], current: view, onNavigate: (id) => navigate(id), footer: engineFooter });
shell.main.append(statusRoot, reviewView);
element("app").replaceChildren(shell.root);

// One launch: its client's model, and whether its status was read.
let launch: string | null = null;
let statusRead = false;
let review: ReviewModel | null = null;
let unsubscribe: (() => void) | null = null;

let statusOpen: number | undefined;
let screen: StatusScreen = { engine: describeEngine({ state: "starting", attempt: 1 }), workspace: { kind: "waiting" } };
let drawnScreen = "";
const expanded = new Set<string>();

function paintStatus(force = false): void {
  // The engine is polled every second; redraw only on a change, so an open
  // disclosure and the focused control are left alone.
  const key = JSON.stringify(screen);
  if (!force && key === drawnScreen) return;
  drawnScreen = key;
  renderStatus(statusRoot, screen, {
    expanded,
    onToggle: (name, open) => void (open ? expanded.add(name) : expanded.delete(name)),
  });
}

function paintNav(): void {
  const count = reviewCount(review?.state ?? null, statusOpen);
  redraw(shell.nav, "nav", () =>
    shell.nav.replaceChildren(...navEntries(count).map((entry) => navItem(entry, entry.id === view, navigate))),
  );
}

function paintEngine(state: EngineState | null): void {
  const [tone, word] = engineBadge(state);
  engineFooter.replaceChildren(badge(tone, word));
}

function navigate(id: string): void {
  if (id === "status" || id === "review") show(id);
}

function show(next: View): void {
  view = next;
  statusRoot.hidden = next !== "status";
  reviewView.hidden = next !== "review";
  paintNav();
  if (next === "review") {
    // Everything is read again on each visit: To review is never stale.
    if (review !== null) void review.load();
  } else {
    statusRoot.querySelector<HTMLElement>('[data-view="heading"]')?.focus();
  }
}

function startReview(state: Extract<EngineState, { state: "ready" }>): void {
  unsubscribe?.();
  review = new ReviewModel(createClient(state));
  const model = review;
  const stopDrawing = model.subscribe((next) => renderReview(reviewRoot, live, model, next));
  const stopCounting = model.subscribe(() => paintNav());
  unsubscribe = () => {
    stopDrawing();
    stopCounting();
  };
  reviewWaiting.hidden = true;
  renderReview(reviewRoot, live, model, model.state);
  if (view === "review") void model.load();
}

function stopReview(): void {
  unsubscribe?.();
  unsubscribe = null;
  review = null;
  reviewWaiting.hidden = false;
  reviewRoot.replaceChildren();
  paintNav();
}

async function tick(): Promise<void> {
  let state: EngineState;
  try {
    state = await window.__TAURI__.core.invoke<EngineState>("engine_state");
  } catch (error) {
    paintEngine(null);
    screen = { ...screen, engine: { text: "The shell could not report the engine's state.", detail: String(error) } };
    paintStatus();
    return;
  }
  paintEngine(state);
  screen = { ...screen, engine: describeEngine(state) };
  paintStatus();

  if (state.state !== "ready") {
    if (launch !== null) stopReview();
    launch = null;
    statusRead = false;
    return;
  }
  const current = `${state.pid}:${state.base_url}`;
  if (launch !== current) {
    launch = current;
    statusRead = false;
    startReview(state);
  }
  if (statusRead) return;
  statusRead = true;
  try {
    const status = await createClient(state).status();
    statusOpen = openDecisions(status);
    screen = { ...screen, workspace: { kind: "read", status } };
  } catch (error) {
    statusRead = false;
    screen = { ...screen, workspace: { kind: "failed", message: statusFailure(error) } };
  }
  paintStatus();
  paintNav();
}

paintEngine({ state: "starting", attempt: 1 });
paintStatus(true);
paintNav();
void tick();
setInterval(() => void tick(), POLL_MS);
