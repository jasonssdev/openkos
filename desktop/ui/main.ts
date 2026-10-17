// The app's shell: the shell process supervises the engine, and this page reads
// its state, draws the app shell (sidebar and main), the status page, the
// review screen, the welcome (no library yet), guided setup and settings, and
// creates one client, one review model and one setup model per engine launch
// (a restarted engine has a new port and token). Settings outlives a launch:
// saving a key restarts the engine, and each launch is handed to it. Every value
// is written with textContent, never as HTML, and nothing is logged.
import {
  createApiKeys,
  createClient,
  createWorkspaces,
  openRuntimeDownload,
  type EngineState,
  type Invoke,
} from "../client/index.ts";
import { ReviewModel } from "./review/model.ts";
import { renderReview } from "./review/render.ts";
import { SettingsModel, cloudOn } from "./settings/model.ts";
import { renderSettings } from "./settings/render.ts";
import { WORDS as SETTINGS_WORDS } from "./settings/wording.ts";
import { SetupModel, WelcomeModel } from "./setup/model.ts";
import { renderSetup, renderWelcome } from "./setup/render.ts";
import { renderStatus, type StatusScreen } from "./status.ts";
import { appShell, badge, el, navItem, redraw, spinner, uiButton } from "./system/index.ts";
import { describeEngine, engineBadge, navEntries, openDecisions, reviewCount, statusFailure } from "./view.ts";

declare global {
  interface Window {
    // `app.withGlobalTauri` in tauri.conf.json.
    __TAURI__: { core: { invoke: Invoke } };
  }
}

const POLL_MS = 1000;

function element<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`the page has no #${id}`);
  return found as T;
}

const invoke: Invoke = (command, args) => window.__TAURI__.core.invoke(command, args);

type View = "status" | "review" | "setup" | "welcome" | "settings";
let view: View = "status";

const live = { status: element("review-live-status"), alert: element("review-live-alert") };
const engineFooter = el("div");
const cloudFooter = el("div");
const settingsFooter = el("div");
const footer = el("div", { class: "ui-sidebar__foot" }, cloudFooter, settingsFooter, engineFooter);
const statusRoot = el("div", { class: "ui-view", attrs: { "data-screen": "status" } });
// To review fills the main region with its list and detail panes.
const reviewWaiting = el("div", { class: "ui-view" }, spinner("Waiting for OpenKOS to start…"));
const reviewRoot = el("div", { class: "ui-fill", attrs: { id: "review" } });
const reviewView = el("div", { class: "ui-fill", attrs: { "data-screen": "review" } }, reviewWaiting, reviewRoot);
reviewView.hidden = true;
const setupRoot = el("div", { class: "ui-view", attrs: { "data-screen": "setup" } });
setupRoot.hidden = true;
const welcomeRoot = el("div", { class: "ui-view", attrs: { "data-screen": "welcome" } });
welcomeRoot.hidden = true;
const settingsRoot = el("div", { class: "ui-view", attrs: { "data-screen": "settings" } });
settingsRoot.hidden = true;

const shell = appShell({ nav: [], current: view, onNavigate: (id) => navigate(id), footer });
shell.main.append(statusRoot, reviewView, setupRoot, welcomeRoot, settingsRoot);
element("app").replaceChildren(shell.root);

// One launch: its client's model, and whether its status was read.
let launch: string | null = null;
let statusRead = false;
let review: ReviewModel | null = null;
let unsubscribe: (() => void) | null = null;
let setup: SetupModel | null = null;
let welcome: WelcomeModel | null = null;
// Setup opens on its own once per session, not again after each restart.
let setupOffered = false;
// One for the whole session: the shell's commands work with no engine running.
const settings = new SettingsModel({ keys: createApiKeys(invoke), workspaces: createWorkspaces(invoke) });
settings.subscribe((next) => {
  renderSettings(settingsRoot, settings, next);
  paintSettingsEntry();
});
renderSettings(settingsRoot, settings, settings.state);

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
    onCheckSetup: () => show("setup"),
  });
}

function paintNav(): void {
  const count = reviewCount(review?.state ?? null, statusOpen);
  redraw(shell.nav, "nav", () =>
    shell.nav.replaceChildren(...navEntries(count).map((entry) => navItem(entry, entry.id === view, navigate))),
  );
}

/** The quiet Settings entry, and while a cloud AI model is on, a calm line saying so. */
function paintSettingsEntry(): void {
  const on = cloudOn(settings.state);
  redraw(cloudFooter, "cloud", () =>
    cloudFooter.replaceChildren(
      ...(on ? [uiButton(SETTINGS_WORDS.cloudOn, () => show("settings"), { variant: "quiet", size: "sm", focusKey: "nav:cloud" })] : []),
    ),
  );
  redraw(settingsFooter, "settings", () =>
    settingsFooter.replaceChildren(
      navItem({ id: "settings", label: SETTINGS_WORDS.navLabel }, view === "settings", () => show("settings")),
    ),
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
  setupRoot.hidden = next !== "setup";
  welcomeRoot.hidden = next !== "welcome";
  settingsRoot.hidden = next !== "settings";
  // With no library there is nothing to navigate to yet.
  shell.nav.hidden = next === "welcome";
  settingsFooter.hidden = next === "welcome";
  setup?.watch(next === "setup");
  paintNav();
  paintSettingsEntry();
  if (next === "review") {
    // Everything is read again on each visit: To review is never stale.
    if (review !== null) void review.visit();
  } else {
    if (next === "settings") void settings.load();
    const root = { status: statusRoot, setup: setupRoot, welcome: welcomeRoot, settings: settingsRoot }[next];
    root.querySelector<HTMLElement>('[data-view="heading"]')?.focus();
  }
}

function enterWelcome(): void {
  if (welcome === null) {
    const model = new WelcomeModel(createWorkspaces(invoke));
    welcome = model;
    model.subscribe((next) => renderWelcome(welcomeRoot, model, next));
    renderWelcome(welcomeRoot, model, model.state);
    void model.load();
  }
  if (view !== "welcome") show("welcome");
}

function leaveWelcome(): void {
  welcome = null;
  if (view === "welcome") show("status");
}

function startSetup(state: Extract<EngineState, { state: "ready" }>): void {
  setup?.dispose();
  const client = createClient(state);
  const model = new SetupModel({
    setupReadiness: () => client.setupReadiness(),
    startModelPull: (body) => client.startModelPull(body),
    modelPull: (id) => client.modelPull(id),
    stopModelPull: (id) => client.stopModelPull(id),
    openDownload: () => openRuntimeDownload(invoke),
  });
  setup = model;
  model.subscribe((next) => {
    renderSetup(setupRoot, model, next, { onDone: () => show("status") });
    const needed = next.readiness !== null && !next.readiness.ready;
    if (needed !== Boolean(screen.setupNeeded)) {
      screen = { ...screen, setupNeeded: needed };
      paintStatus();
    }
    if (needed && !setupOffered) {
      setupOffered = true;
      show("setup");
    }
  });
  model.watch(view === "setup");
  void model.load();
}

function stopSetup(): void {
  setup?.dispose();
  setup = null;
  screen = { ...screen, setupNeeded: false };
  if (view === "setup") show("status");
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
  if (view === "review") void model.visit();
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

  if (state.state === "no_workspace") enterWelcome();
  else if (welcome !== null) leaveWelcome();

  if (state.state !== "ready") {
    if (launch !== null) {
      stopReview();
      stopSetup();
      void settings.connect(null);
    }
    launch = null;
    statusRead = false;
    return;
  }
  const current = `${state.pid}:${state.base_url}`;
  if (launch !== current) {
    launch = current;
    statusRead = false;
    startReview(state);
    startSetup(state);
    void settings.connect(createClient(state));
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
paintSettingsEntry();
void tick();
setInterval(() => void tick(), POLL_MS);
