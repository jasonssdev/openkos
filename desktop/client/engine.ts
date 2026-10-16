// What the shell's `engine_state` command returns: the Rust `EngineState`
// (desktop/src-tauri/src/supervisor.rs), serialised with a `state` tag.

export type EngineState =
  | { state: "starting"; attempt: number }
  | { state: "ready"; attempt: number; base_url: string; token: string; pid: number }
  | { state: "restarting"; attempt: number; delay_ms: number; reason: string; detail: string }
  | { state: "failed"; reason: string; detail: string }
  | { state: "stopped" };

export type ReadyEngine = Extract<EngineState, { state: "ready" }>;
