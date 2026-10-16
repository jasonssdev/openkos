// What the status page says, kept apart from the DOM so it can be tested. No
// function here reads the token, and a failure is worded from its class and the
// engine's fixed message only, never from an unknown error's text.
import {
  ApiError,
  EngineUnreachable,
  ProtocolError,
  type EngineState,
  type Schemas,
} from "../client/index.ts";

export interface EngineWording {
  text: string;
  detail?: string;
}

export function describeEngine(state: EngineState): EngineWording {
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

export function statusFacts(status: Schemas["WorkspaceStatus"]): [string, string][] {
  const open = Object.values(status.open_decisions_by_kind).reduce((sum, n) => sum + n, 0);
  return [
    ["Folder", status.root],
    ["Sources", String(status.sources)],
    ["Concepts", String(status.concepts)],
    ["Decisions waiting for you", String(open)],
  ];
}

const UNREADABLE = "The workspace status could not be read";

export function statusFailure(error: unknown): string {
  if (error instanceof ApiError) return `${UNREADABLE}: ${error.message}.`;
  if (error instanceof ProtocolError) {
    return `${UNREADABLE}: the engine answered in a form this app cannot read.`;
  }
  if (error instanceof EngineUnreachable) return `${UNREADABLE}: ${error.message}.`;
  return `${UNREADABLE}.`;
}
