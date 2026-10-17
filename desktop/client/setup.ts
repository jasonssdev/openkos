// The shell's one command for guided setup (ADR-0057): open the model runtime's
// official download page in the system browser. The page names no address; the
// shell opens its one fixed URL, so nothing the page says can open another.

import type { Invoke } from "./shell.ts";

/** The browser did not open. The shell's text is never kept: it may name a path. */
export class BrowserNotOpened extends Error {
  constructor() {
    super("the shell could not open the browser");
    this.name = "BrowserNotOpened";
  }
}

/** Open Ollama's download page in the default browser. */
export async function openRuntimeDownload(invoke: Invoke): Promise<void> {
  try {
    await invoke<null>("open_runtime_download");
  } catch {
    throw new BrowserNotOpened();
  }
}
