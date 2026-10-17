// The app's shell: the shell process supervises the engine, and this page reads
// its state, draws the app shell (sidebar and main), the status page, the Ask
// screen, the review screen, the welcome (no library yet), guided setup and
// settings, and creates one client, one review model and one setup model per
// engine launch (a restarted engine has a new port and token). Settings, Ask and
// Add files outlive a launch: saving a key restarts the engine, and each launch
// is handed to them. Every value is written with textContent, never as HTML,
// and nothing is logged.
import {
  createApiKeys,
  createClient,
  createWorkspaces,
  openRuntimeDownload,
  type EngineState,
  type Invoke,
} from "../client/index.ts";
import { AddModel } from "./add/model.ts";
import { renderAdd } from "./add/render.ts";
import { addCue, nextUnseen } from "./add/wording.ts";
import { AskModel } from "./ask/model.ts";
import { renderAsk } from "./ask/render.ts";
import { LabelModel } from "./label/model.ts";
import { keepHereAction, labelChoice } from "./label/render.ts";
import { ReviewModel } from "./review/model.ts";
import { renderReview } from "./review/render.ts";
import { SettingsModel, cloudOn } from "./settings/model.ts";
import { renderSettings } from "./settings/render.ts";
import { WORDS as SETTINGS_WORDS } from "./settings/wording.ts";
import { SetupModel, WelcomeModel } from "./setup/model.ts";
import { renderSetup, renderWelcome } from "./setup/render.ts";
import { SearchModel } from "./search/model.ts";
import { rebuildSection } from "./search/render.ts";
import { renderStatus, type StatusScreen } from "./status.ts";
import { appShell, badge, el, navItem, openDialog, redraw, spinner, uiButton } from "./system/index.ts";
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

type View = "ask" | "add" | "status" | "review" | "setup" | "welcome" | "settings";
let view: View = "status";

const live = { status: element("review-live-status"), alert: element("review-live-alert") };
const engineFooter = el("div");
const cloudFooter = el("div");
const settingsFooter = el("div");
const footer = el("div", { class: "ui-sidebar__foot" }, cloudFooter, settingsFooter, engineFooter);
const statusRoot = el("div", { class: "ui-view", attrs: { "data-screen": "status" } });
const askRoot = el("div", { class: "ui-view", attrs: { "data-screen": "ask" } });
askRoot.hidden = true;
const addRoot = el("div", { class: "ui-view", attrs: { "data-screen": "add" } });
addRoot.hidden = true;
// To review fills the main region with its list and detail panes.
const reviewWaiting = el("div", { class: "ui-view" }, spinner("Waiting for OpenKOS to start…"));
const reviewRoot = el("div", { class: "ui-fill", attrs: { id: "review" } });
const reviewView = el("div", { class: "ui-fill", attrs: { "data-screen": "review" } }, reviewWaiting, reviewRoot);
reviewView.hidden = true;
const setupRoot = el("div", { class: "ui-view", attrs: { "data-screen": "setup" } });
setupRoot.hidden = true;
const welcomeRoot = el("div", { class: "ui-view", attrs: { "data-screen": "welcome" } });
welcomeRoot.hidden = true;
// Settings, and under it rebuilding search while search is behind.
const settingsScreen = el("div", { class: "ui-view" });
const settingsSearch = el("div", { class: "ui-view" });
const settingsRoot = el("div", { attrs: { "data-screen": "settings" } }, settingsScreen, settingsSearch);
settingsRoot.hidden = true;

const shell = appShell({ nav: [], current: view, onNavigate: (id) => navigate(id), footer });
shell.main.append(askRoot, addRoot, statusRoot, reviewView, setupRoot, welcomeRoot, settingsRoot);
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
let settingsNote: string | null = null;
settings.subscribe((next) => {
  renderSettings(settingsScreen, settings, next);
  paintSettingsEntry();
  // A saved change may have changed the AI model for search: read search again.
  if (next.note !== settingsNote) {
    settingsNote = next.note;
    void search.load();
  }
});
renderSettings(settingsScreen, settings, settings.state);

/** Asks the person in a dialog that starts on its safe answer; resolves `true` to go ahead. */
function askFirst(question: { title: string; description?: string; lines?: string[]; confirm: string; cancel: string }): Promise<boolean> {
  return new Promise((resolve) =>
    openDialog({
      title: question.title,
      ...(question.description ? { description: question.description } : {}),
      body: (question.lines ?? []).map((line) => el("p", {}, line)),
      confirmLabel: question.confirm,
      cancelLabel: question.cancel,
      destructive: true,
      onConfirm: () => resolve(true),
      onCancel: () => resolve(false),
    }),
  );
}

// Who can see a page, and rebuilding search: one model each for the session.
const labels = new LabelModel({
  cloudHost: () => (cloudOn(settings.state) ? (settings.state.settings?.remote_hosts[0] ?? null) : null),
  confirm: askFirst,
});
const search = new SearchModel({ confirm: askFirst });
const searchActions = { rebuild: () => search.rebuild(), stop: () => search.stop() };

let statusOpen: number | undefined;
let screen: StatusScreen = { engine: describeEngine({ state: "starting", attempt: 1 }), workspace: { kind: "waiting" } };
let drawnScreen = "";
const expanded = new Set<string>();

function paintStatus(force = false): void {
  // The engine is polled every second; redraw only on a change, so an open
  // disclosure and the focused control are left alone.
  const key = JSON.stringify([screen, search.state]);
  if (!force && key === drawnScreen) return;
  drawnScreen = key;
  renderStatus(statusRoot, screen, {
    expanded,
    onToggle: (name, open) => void (open ? expanded.add(name) : expanded.delete(name)),
    onCheckSetup: () => show("setup"),
    search: rebuildSection(search.state, searchActions),
  });
}

// Ask: one model for the session, so its history outlives an engine restart.
const ask = new AskModel();
const askExpanded = new Set<string>();
function paintAsk(): void {
  renderAsk(askRoot, ask.state, {
    ask: (question) => ask.ask(question),
    retry: () => ask.retry(),
    show: (id) => ask.show(id),
    expanded: askExpanded,
    onToggle: (name, open) => void (open ? askExpanded.add(name) : askExpanded.delete(name)),
    citationAction: (citation) =>
      // A named page is never marked Only on this Mac unless the person agreed
      // to see such pages; its other level is not known here, and Undo puts
      // back the level the engine read.
      keepHereAction(
        { conceptId: citation.concept_id, title: citation.title, level: citation.confidential ? "confidential" : "private" },
        labels,
        labels.state,
      ),
    // Under an answer whose search may have been behind, the way to bring it up to date.
    afterAnswer: (answer) =>
      answer.stale_indexes.length > 0 || answer.lexical_unavailable || answer.dense_unavailable || search.state.job !== null
        ? rebuildSection(search.state, searchActions)
        : null,
  });
}
ask.subscribe(() => paintAsk());

// Add files: one model for the session, so a run goes on while another screen is open.
// The shell opens the system's file chooser; the page has no dialog permission.
const workspaces = createWorkspaces(invoke);
const add = new AddModel({ pick: () => workspaces.pickFiles() });
function paintAdd(): void {
  renderAdd(addRoot, add.state, {
    choose: () => add.choose(),
    agree: () => add.agree(),
    decline: () => add.decline(),
    stop: () => add.stop(),
    close: () => add.close(),
    resolveProblem: () => add.resolveProblem(),
  });
}
// A run goes on off the screen: the sidebar says so, and how it ended until the person looks.
let addPhase = add.state.phase;
let addUnseen = false;
add.subscribe((next) => {
  addUnseen = nextUnseen(addUnseen, addPhase, next.phase, view === "add");
  addPhase = next.phase;
  paintAdd();
  paintNav();
});

/** A refused file's "Add it": quote it on the Add files screen. */
function addRefusedFile(decisionKey: string): void {
  show("add");
  void add.addRefused(decisionKey);
}

function paintSettingsSearch(): void {
  redraw(settingsSearch, "settings-search", () =>
    settingsSearch.replaceChildren(...[rebuildSection(search.state, searchActions)].filter((n) => n !== null)),
  );
}

function paintReview(): void {
  const model = review;
  if (model !== null) renderReview(reviewRoot, live, model, model.state, reviewExtras);
}

const reviewExtras = {
  addFile: addRefusedFile,
  pageAction: (page: { conceptId: string; title: string; level: "public" | "private" | "confidential" }) =>
    labelChoice(page, labels, labels.state),
};

labels.subscribe(() => {
  paintAsk();
  paintReview();
});
search.subscribe(() => {
  paintAsk();
  paintSettingsSearch();
  paintStatus();
});

function paintNav(): void {
  const count = reviewCount(review?.state ?? null, statusOpen);
  redraw(shell.nav, "nav", () =>
    shell.nav.replaceChildren(...navEntries(count, addCue(add.state, addUnseen)).map((entry) => navItem(entry, entry.id === view, navigate))),
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
  if (id === "ask" || id === "add" || id === "status" || id === "review") show(id);
}

function show(next: View): void {
  view = next;
  if (next === "add") addUnseen = false;
  statusRoot.hidden = next !== "status";
  askRoot.hidden = next !== "ask";
  addRoot.hidden = next !== "add";
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
    if (next === "settings" || next === "status") void search.load();
    const root = { ask: askRoot, add: addRoot, status: statusRoot, setup: setupRoot, welcome: welcomeRoot, settings: settingsRoot }[next];
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
  ask.setClient(createClient(state));
  labels.setClient(createClient(state));
  add.setClient(createClient(state));
  void search.setClient(createClient(state));
  review = new ReviewModel(createClient(state), {
    // The check named an AI model that isn't installed; setup reads again and offers it.
    openSetup: () => {
      void setup?.load();
      show("setup");
    },
  });
  const model = review;
  const stopDrawing = model.subscribe((next) => renderReview(reviewRoot, live, model, next, reviewExtras));
  const stopCounting = model.subscribe(() => paintNav());
  unsubscribe = () => {
    stopDrawing();
    stopCounting();
  };
  reviewWaiting.hidden = true;
  renderReview(reviewRoot, live, model, model.state, reviewExtras);
  if (view === "review") void model.visit();
}

function stopReview(): void {
  unsubscribe?.();
  unsubscribe = null;
  review = null;
  ask.setClient(null);
  labels.setClient(null);
  add.setClient(null);
  void search.setClient(null);
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
paintAsk();
paintAdd();
paintNav();
paintSettingsEntry();
void tick();
setInterval(() => void tick(), POLL_MS);
