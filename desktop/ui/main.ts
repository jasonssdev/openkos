// Proves the round trip: the shell supervises the engine, and this page reads
// the workspace status through the client (client/), created from the engine
// state the shell reports. Every value is written with textContent, never as
// HTML, and nothing is logged.
import { createClient, type EngineState } from "../client/index.ts";
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

// The status is read once per engine launch; a restarted engine has a new port
// and token, so its status is read again.
let statusReadFor: string | null = null;

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
    statusReadFor = null;
    return;
  }
  const launch = `${state.pid}:${state.base_url}`;
  if (statusReadFor === launch) return;
  statusReadFor = launch;
  try {
    showFacts(statusFacts(await createClient(state).status()));
  } catch (error) {
    statusReadFor = null;
    workspaceFacts.hidden = true;
    workspaceMessage.textContent = statusFailure(error);
  }
}

void tick();
setInterval(() => void tick(), POLL_MS);
