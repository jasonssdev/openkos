import { describe, expect, it } from "vitest";
import { ApiError, EngineUnreachable, WORKSPACE_ERROR_REASONS, WorkspaceError, type Schemas } from "../../client/index.ts";
import {
  downloadSize,
  memoryNote,
  memorySize,
  movedLibrary,
  pullFailureFor,
  pullFailure,
  pullProgress,
  runtimeStep,
  stoppedNote,
  welcomeRefusal,
} from "./wording.ts";

const GiB = 1024 ** 3;

function apiError(code: string): ApiError {
  return new ApiError({ status: 503, code, retryable: true, message: "fixed", retryAfterSeconds: null });
}

function runtime(over: Partial<Schemas["RuntimeReadiness"]> = {}): Schemas["RuntimeReadiness"] {
  return {
    backend: "ollama",
    host: "localhost:11434",
    is_local: true,
    reachable: false,
    version: null,
    download_url: "https://ollama.com/download",
    ...over,
  };
}

// Rule 1 of docs/decision-wording.md: no engine words on screen.
const ENGINE_WORDS = /\b(workspace|engine|bundle|concept|ingest|okf|pull|backend|runtime|token)\b/i;

describe("downloadSize", () => {
  it.each([
    [0, "0 MB"],
    [400_000, "0 MB"],
    [397_000_000, "397 MB"],
    [999_400_000, "999 MB"],
    [1_000_000_000, "1.0 GB"],
    [5_234_000_000, "5.2 GB"],
  ])("words %d bytes as %s", (bytes, words) => {
    expect(downloadSize(bytes)).toBe(words);
  });
});

describe("memorySize", () => {
  it("words memory in whole gigabytes as the Mac does", () => {
    expect(memorySize(16 * GiB)).toBe("16 GB");
    expect(memorySize(32 * GiB)).toBe("32 GB");
  });
});

describe("pullProgress", () => {
  it("says the bytes and the percent while downloading", () => {
    expect(pullProgress({ state: "running", phase: "downloading", completed: 1_300_000_000, total: 5_200_000_000 })).toBe(
      "1.3 GB of 5.2 GB (25%)",
    );
  });

  it("never claims a full percent before the end, nor more than 100", () => {
    expect(pullProgress({ state: "running", phase: "downloading", completed: 999, total: 1000 })).toBe("0 MB of 0 MB (99%)");
    expect(pullProgress({ state: "running", phase: "downloading", completed: 2000, total: 1000 })).toBe("0 MB of 0 MB (100%)");
  });

  it("says only the bytes when the size is not known yet", () => {
    expect(pullProgress({ state: "running", phase: "downloading", completed: 300_000_000, total: null })).toBe("300 MB so far");
    expect(pullProgress({ state: "running", phase: "downloading", completed: 0, total: 0 })).toBe("0 MB so far");
  });

  it.each([
    [null, "Getting ready…"],
    ["preparing", "Getting ready…"],
    ["verifying", "Checking the download…"],
    ["finishing", "Finishing…"],
  ] as const)("words the %s phase", (phase, words) => {
    expect(pullProgress({ state: "running", phase, completed: 0, total: null })).toBe(words);
  });

  it("says it is stopping once asked", () => {
    expect(pullProgress({ state: "stopping", phase: "downloading", completed: 1, total: 2 })).toBe("Stopping…");
  });
});

describe("stoppedNote", () => {
  it("says where it stopped and that it continues from there", () => {
    expect(stoppedNote(1_300_000_000, 5_200_000_000)).toBe(
      "Stopped at 1.3 GB of 5.2 GB. Downloading again continues from there.",
    );
    expect(stoppedNote(300_000_000, null)).toBe("Stopped at 300 MB. Downloading again continues from there.");
    expect(stoppedNote(0, null)).toBe("Stopped.");
  });
});

describe("pullFailure", () => {
  it.each([
    ["backend_unavailable", "OpenKOS lost touch with Ollama. Try again: the download continues from where it stopped."],
    ["pull_active", "Another download is already running. Try again in a moment."],
    ["backend_model_not_found", "Ollama couldn't find this AI model to download."],
    ["model_not_configured", "Your library doesn't use this AI model any more."],
    ["pull_not_supported", "This AI model can't be downloaded from here."],
    ["internal_error", "The download didn't finish. Try again: it continues from where it stopped."],
  ])("words %s", (code, words) => {
    expect(pullFailure(apiError(code))).toBe(words);
  });

  it("words an engine that stopped answering, and anything else", () => {
    expect(pullFailure(new EngineUnreachable({ path: "/setup/pulls" }))).toBe("OpenKOS isn't responding. Try again in a moment.");
    expect(pullFailure(new Error("secret"))).toBe("The download didn't finish. Try again: it continues from where it stopped.");
  });
});

describe("runtimeStep", () => {
  it("sends a local Ollama user to the download", () => {
    const step = runtimeStep(runtime());
    expect(step).toEqual({
      title: "Install Ollama",
      text: [
        "OpenKOS uses Ollama, a free app, to run AI models on this Mac. It isn't running yet.",
        "Get it from Ollama's website, install it and open it. We'll notice when it's running.",
      ],
      download: "https://ollama.com/download",
    });
  });

  it("offers no download for a runtime elsewhere, and names where it is", () => {
    for (const over of [{ is_local: false }, { backend: "openai-compatible" as const, download_url: null }]) {
      expect(runtimeStep(runtime({ host: "models.example:8080", ...over }))).toEqual({
        title: "Reach the AI model",
        text: ["OpenKOS can't reach the AI model at models.example:8080. Check that it's running, then check again."],
        download: null,
      });
    }
  });
});

describe("memoryNote", () => {
  it("is honest below the floor and silent otherwise", () => {
    expect(memoryNote({ total_bytes: 16 * GiB, floor_bytes: 32 * GiB, meets_floor: false })).toBe(
      "This Mac has 16 GB of memory. OpenKOS is made for 32 GB or more, so answers may be slow and some steps may not finish. You can still continue.",
    );
    expect(memoryNote({ total_bytes: 32 * GiB, floor_bytes: 32 * GiB, meets_floor: true })).toBeNull();
    expect(memoryNote({ total_bytes: null, floor_bytes: 32 * GiB, meets_floor: null })).toBeNull();
  });
});

describe("welcomeRefusal", () => {
  const refused = (reason: string, detail = "the detail") => new WorkspaceError(reason as never, detail);

  it("words every reason the shell answers, in plain words", () => {
    for (const reason of [...WORKSPACE_ERROR_REASONS, "unknown"]) {
      for (const action of ["create", "open"] as const) {
        const words = welcomeRefusal(action, refused(reason), "/Users/me/kb");
        expect(words.text.length).toBeGreaterThan(0);
        expect(words.text).not.toMatch(ENGINE_WORDS);
        expect(words.text).not.toContain("the detail");
      }
    }
  });

  it.each([
    ["folder_not_empty", "That folder already has files in it. Choose an empty folder, or make a new one, so nothing of yours is changed."],
    ["not_a_workspace", "That folder doesn't hold an OpenKOS library. Choose another, or create a new library."],
    ["set_by_environment", "This copy of OpenKOS is set to use a library chosen outside the app, so it can't be changed here."],
    ["init_timed_out", "Setting up the library took too long, so OpenKOS stopped. Please try again."],
    ["engine_not_located", "Part of OpenKOS is missing, so it can't set up a library. Reinstalling OpenKOS may help."],
    ["invalid_path", "OpenKOS can't use that folder. Please choose another."],
    ["not_a_folder", "OpenKOS can't use that folder. Please choose another."],
    ["unknown", "Something went wrong. Please try again."],
  ])("words %s", (reason, text) => {
    expect(welcomeRefusal("create", refused(reason), "/x")).toEqual({ text });
  });

  it("offers to open a folder that already holds a library", () => {
    expect(welcomeRefusal("create", refused("already_a_workspace"), "/Users/me/kb")).toEqual({
      text: "That folder already holds an OpenKOS library.",
      openPath: "/Users/me/kb",
    });
  });

  it("keeps a failed set-up's own report behind a disclosure", () => {
    expect(welcomeRefusal("create", refused("init_failed", "the engine exited (1).\nboom"), "/x")).toEqual({
      text: "OpenKOS couldn't set up a library in that folder.",
      detail: "the engine exited (1).\nboom",
    });
  });

  it("says a created library was not remembered, and offers to open it", () => {
    expect(welcomeRefusal("create", refused("store_failed"), "/Users/me/kb")).toEqual({
      text: "Your library was set up, but OpenKOS couldn't remember it. Open it to continue.",
      openPath: "/Users/me/kb",
    });
    expect(welcomeRefusal("open", refused("store_failed"), "/Users/me/kb")).toEqual({
      text: "OpenKOS couldn't remember your choice. Please try again.",
    });
  });

  it("words an error that is not the shell's as unknown", () => {
    expect(welcomeRefusal("open", new Error("x"), "/x")).toEqual({ text: "Something went wrong. Please try again." });
  });
});

describe("pullFailureFor", () => {
  it("words a failed download's code as a refused start's", () => {
    expect(pullFailureFor("backend_unavailable")).toBe(pullFailure(apiError("backend_unavailable")));
    expect(pullFailureFor("something_new")).toBe("The download didn't finish. Try again: it continues from where it stopped.");
  });
});

describe("movedLibrary", () => {
  it("says the library chosen before is gone from where it was", () => {
    expect(movedLibrary("/Users/me/kb")).toBe(
      "The library you used before isn't at /Users/me/kb any more. Open it from where it is now, or create a new one.",
    );
  });
});
