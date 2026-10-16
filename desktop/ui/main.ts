// The app's shell: the shell process supervises the engine, and this page reads
// its state, shows the status page and the review screen, and creates one
// client and one review model per engine launch (a restarted engine has a new
// port and token). Every value is written with textContent, never as HTML,
// and nothing is logged.
import { createClient, type EngineState } from "../client/index.ts";
import { ReviewModel } from "./review/model.ts";
import { renderReview } from "./review/render.ts";
import { describeEngine, statusFacts, statusFailure } from "./view.ts";

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

const engineStatus = element("engine-status");
const engineDetail = element<HTMLPreElement>("engine-detail");
const workspaceMessage = element("workspace-message");
const workspaceFacts = element<HTMLDListElement>("workspace-facts");
const statusView = element("status-view");
const reviewView = element("review-view");
const reviewWaiting = element("review-waiting");
const reviewRoot = element("review");
const live = { status: element("review-live-status"), alert: element("review-live-alert") };
const navStatus = element<HTMLButtonElement>("nav-status");
const navReview = element<HTMLButtonElement>("nav-review");

type View = "status" | "review";
let view: View = "status";

// One launch: its client's model, and whether its status was read.
let launch: string | null = null;
let statusRead = false;
let review: ReviewModel | null = null;
let unsubscribe: (() => void) | null = null;

function show(next: View): void {
  view = next;
  statusView.hidden = next !== "status";
  reviewView.hidden = next !== "review";
  const [current, other] = next === "status" ? [navStatus, navReview] : [navReview, navStatus];
  current.setAttribute("aria-current", "page");
  other.removeAttribute("aria-current");
  if (next === "review") {
    // Everything is read again on each visit: To review is never stale.
    if (review !== null) void review.load();
  } else {
    statusView.querySelector<HTMLElement>("h1")?.focus();
  }
}

navStatus.addEventListener("click", () => show("status"));
navReview.addEventListener("click", () => show("review"));

function showDetail(detail: string | undefined): void {
  engineDetail.hidden = !detail;
  engineDetail.textContent = detail ? detail.trim() : "";
}

function showFacts(facts: [string, string][]): void {
  workspaceFacts.replaceChildren(
    ...facts.flatMap(([term, value]) => {
      const dt = document.createElement("dt");
      dt.textContent = term;
      const dd = document.createElement("dd");
      dd.textContent = value;
      return [dt, dd];
    }),
  );
  workspaceMessage.textContent = "Read from the engine just now.";
  workspaceFacts.hidden = false;
}

function startReview(state: Extract<EngineState, { state: "ready" }>): void {
  unsubscribe?.();
  review = new ReviewModel(createClient(state));
  const model = review;
  unsubscribe = model.subscribe((next) => renderReview(reviewRoot, live, model, next));
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
}

async function tick(): Promise<void> {
  let state: EngineState;
  try {
    state = await window.__TAURI__.core.invoke<EngineState>("engine_state");
  } catch (error) {
    engineStatus.textContent = "The shell could not report the engine's state.";
    showDetail(String(error));
    return;
  }
  const { text, detail } = describeEngine(state);
  engineStatus.textContent = text;
  showDetail(detail);

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
    showFacts(statusFacts(await createClient(state).status()));
  } catch (error) {
    statusRead = false;
    workspaceFacts.hidden = true;
    workspaceMessage.textContent = statusFailure(error);
  }
}

void tick();
setInterval(() => void tick(), POLL_MS);
