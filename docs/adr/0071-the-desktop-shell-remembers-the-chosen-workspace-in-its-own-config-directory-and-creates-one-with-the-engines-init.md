---
type: Decision
title: "ADR-0071: The desktop shell remembers the chosen workspace in its own config directory and creates one with the engine's init"
description: When OPENKOS_WORKSPACE is unset, the desktop shell serves the workspace the user chose in the app, remembered as one absolute path in the shell's application config directory; a new workspace is created by running the located engine's own init, non-interactively, in an empty folder the user picked with the system dialog, which only the shell may open.
status: Accepted
date: 2026-10-17
tags:
  - openkos
  - adr
  - desktop
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-17T00:00:00Z
sensitivity: public
---

# ADR-0071: The desktop shell remembers the chosen workspace in its own config directory and creates one with the engine's init

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

[MVP 6](../roadmap.md) is for a person who never opens a terminal. Until now the shell served the folder named by `OPENKOS_WORKSPACE`, or else its own working directory, which for an app opened from the Finder is `/`. That person could not start the app at all.

`openkos serve` opens an existing workspace before it binds, so no route of the local API can create the workspace the server needs in order to run. #1487 weighed two ways out: the shell runs the engine's `init` in the folder the user chose and then `serve`, or a new workspace-less setup mode of `serve` with an `openkos.api.create_workspace`. It chose the first: it works today, since the frozen engine accepts every `openkos` command, and it adds no API surface frozen into `API_VERSION` 1 without a client.

That leaves three things to decide: where the choice is kept, which folders `init` may be run in, and how the folder is picked.

- **The engine has nowhere to keep it.** The choice is which workspace to open, so it cannot live in a workspace. The engine keeps no per-user state outside one.
- **The shell already owns the engine's start.** It locates the engine, starts it and restarts it on request without spending the crash budget ([ADR-0055](0055-the-desktop-shell-is-tauri-2-with-the-engine-as-a-supervised-sidecar.md), [ADR-0070](0070-the-desktop-shell-keeps-the-remote-backends-api-key-in-the-os-keychain-and-hands-it-to-the-engine-as-its-environment.md)).
- **`init` is safe in a workspace's terms, not a folder's.** It refuses an existing workspace and a non-empty `raw/` or `bundle/`, but in any other folder it writes the workspace beside whatever is there, runs `git init` unless the folder is already inside a repository, and commits what it wrote. Picked by mistake, a user's Documents folder becomes a git repository.
- **A web view cannot name a folder.** An HTML file input does not reveal an absolute path, and typing one is the terminal skill the user does not have.

## Decision

The shell serves `OPENKOS_WORKSPACE` when it is set, for development, and otherwise the workspace the user chose in the app. With neither, it starts nothing and reports `no_workspace`, which the page shows instead of an error.

- **One file in the shell's own config directory.** The choice is one absolute, symlink-free path in `workspace.json` under Tauri's application config directory (on macOS, `~/Library/Application Support/io.github.jasonssdev.openkos/`), never inside the workspace. It is written to a temporary file and renamed. A missing, malformed or relative choice is no choice, and a remembered folder that no longer holds `openkos.yaml` is served as `no_workspace`, so the page asks again.
- **Creating runs the engine's own `init`.** The same engine the locator would serve with, run as `init` with the folder as its working directory, its stdin empty and not a terminal so it writes the recommended models without prompting, killed after a bounded time. The shell never writes a workspace itself. Only when `init` succeeds and `openkos.yaml` exists is the folder remembered and the engine restarted on it.
- **Only an empty folder is created in.** A missing folder whose parent exists is created first. A folder holding anything but Finder's `.DS_Store` is refused, as is an existing workspace, which the page opens instead.
- **The system picker, opened by the shell.** A `pick_workspace_folder` command opens the operating system's folder dialog through `tauri-plugin-dialog` (Apache-2.0 or MIT) from Rust and answers the path. No capability grants the page the plugin's own commands.
- **The variable wins.** While `OPENKOS_WORKSPACE` is set, opening and creating refuse before writing anything, so the app never claims a choice it cannot serve.

## Consequences

**Easier.**

- A first-run user can pick an empty folder and get a working workspace without a terminal, and the app opens it again at the next launch.
- No engine, API or format change: the engine's `init` stays the only creator, and its behaviour is exercised by the shell's live test against the real engine.

**Harder, and accepted.**

- **The shell holds a second piece of state.** After the key, it now keeps the chosen path. It is not a secret and not product knowledge, and losing it costs one re-choice, but it is state the shell must keep consistent.
- **One workspace at a time.** The file holds one path. A list of recent workspaces is a later change to the same file.
- **Folders with content are refused.** A user who wants a workspace in a folder that already holds their notes must make a new folder. `init` would accept many such folders, but the shell cannot tell a safe one from Documents.
- **A new dependency in the shell.** The dialog plugin brings its native dialog crate. Opening the picker has been compiled and wired but not exercised in an automated test, since it needs a person at the screen.

## Alternatives considered

- **`openkos.api.create_workspace` and a workspace-less setup mode of `serve`** (#1487, option 2). Deferred: one protocol for the whole setup, but a server mode whose routes run with no workspace widens the local API's shape ([ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md), [ADR-0061](0061-the-local-api-is-a-fastapi-adapter-over-the-python-api-shipped-as-an-optional-extra.md)) and needs its own decision. Revisit if setup must probe Ollama before a folder is chosen.
- **The choice in the workspace, or in a dotfile in the home directory.** Rejected: the first is circular, and the second is a file of the shell's outside the place the platform gives every app for one.
- **Passing any folder to `init`.** Rejected for the picker: `init`'s guard protects a workspace, not the user's other files, and its `git init` is hard to undo for a person who does not use git.
- **The web view typing or pasting a path.** Rejected as the only way: it asks the user for the skill the arc removes. The commands still take a path, so a screen may offer it as an advanced entry.
- **The working directory as a fallback.** Removed: for an app opened from the Finder it is `/`, never a workspace.
