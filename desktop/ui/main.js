// Proves the round trip: the shell supervises the engine, and this page reads
// the workspace status from the engine's local API with the per-launch token.
// Every value is written with textContent, never as HTML.

const POLL_MS = 1000;

const engineStatus = document.getElementById("engine-status");
const engineDetail = document.getElementById("engine-detail");
const workspaceMessage = document.getElementById("workspace-message");
const workspaceFacts = document.getElementById("workspace-facts");

// The status is read once per engine launch; a restarted engine has a new port
// and token, so its status is read again.
let statusReadFor = null;

function describe(state) {
  switch (state.state) {
    case "starting":
      return { text: "Starting the engine…" };
    case "ready":
      return { text: "The engine is running." };
    case "restarting":
      return {
        text: `The engine stopped unexpectedly. Restarting it (attempt ${state.attempt + 1})…`,
        detail: state.detail,
      };
    case "failed":
      return { text: "The engine could not be started.", detail: state.detail };
    case "stopped":
      return { text: "The engine has stopped." };
    default:
      return { text: "The engine's state is unknown." };
  }
}

function showDetail(detail) {
  engineDetail.hidden = !detail;
  engineDetail.textContent = detail ? detail.trim() : "";
}

function fact(term, value) {
  const dt = document.createElement("dt");
  dt.textContent = term;
  const dd = document.createElement("dd");
  dd.textContent = String(value);
  workspaceFacts.append(dt, dd);
}

function showStatus(status) {
  const open = Object.values(status.open_decisions_by_kind ?? {}).reduce(
    (sum, n) => sum + n,
    0,
  );
  workspaceFacts.replaceChildren();
  fact("Folder", status.root);
  fact("Sources", status.sources);
  fact("Concepts", status.concepts);
  fact("Decisions waiting for you", open);
  workspaceMessage.textContent = "Read from the engine just now.";
  workspaceFacts.hidden = false;
}

async function readStatus(state) {
  const response = await fetch(`${state.base_url}/status`, {
    headers: { Authorization: `Bearer ${state.token}` },
  });
  if (!response.ok) {
    throw new Error(`the engine answered ${response.status}`);
  }
  return response.json();
}

async function tick() {
  let state;
  try {
    state = await window.__TAURI__.core.invoke("engine_state");
  } catch (error) {
    engineStatus.textContent = "The shell could not report the engine's state.";
    showDetail(String(error));
    return;
  }
  const { text, detail } = describe(state);
  engineStatus.textContent = text;
  showDetail(detail);

  if (state.state !== "ready") {
    statusReadFor = null;
    return;
  }
  const launch = `${state.pid}:${state.base_url}`;
  if (statusReadFor === launch) {
    return;
  }
  statusReadFor = launch;
  try {
    showStatus(await readStatus(state));
  } catch (error) {
    statusReadFor = null;
    workspaceFacts.hidden = true;
    workspaceMessage.textContent = `The workspace status could not be read: ${error.message}.`;
  }
}

tick();
setInterval(tick, POLL_MS);
