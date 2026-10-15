---
type: Decision
title: "ADR-0055: The desktop shell is Tauri 2, with the engine as a supervised sidecar"
description: The MVP 6 desktop app is a Tauri 2 shell that starts, supervises and stops the engine as a sidecar process, updated through Tauri's own signed updater.
status: Proposed
date: 2026-10-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-14T00:00:00Z
sensitivity: public
---

# ADR-0055: The desktop shell is Tauri 2, with the engine as a supervised sidecar

- **Status:** Proposed
- **Date:** 2026-10-14

## Context

[MVP 6](../roadmap.md) ships OpenKOS as an application a person installs without a terminal. The engine is Python ([ADR-0054](0054-python-314-is-the-only-supported-interpreter.md)), so whatever the shell is, it runs the engine as a separate process and talks to it over a local API ([ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md)). The roadmap names the tradeoff: installer size and a built-in signed updater against the maturity of the signing path for a bundled binary.

The project ships without an Apple Developer ID, so updates cannot lean on Apple's certificates. Whatever signs the updates must be a key the project owns.

Evidence comes from a throwaway packaging spike run on the project's development machine (Apple Silicon, macOS 27.0), which built a Tauri 2 shell (CLI 2.12.2) around a frozen engine ([ADR-0056](0056-the-engine-ships-frozen-with-pyinstaller-one-directory-under-contents-resources-deep-ad-hoc-signed.md)) and ran it unsigned. All numbers below are from that spike.

- **Size.** The whole app is small: with the engine frozen on Python 3.12 the `.app` was 48 MB, the `.dmg` 24 MB and the updater archive 21 MB; on Python 3.14, the interpreter ADR-0054 adopts, the `.app` was 57 MB, the `.dmg` 30 MB and the updater archive 25 MB. The engine dominates; the shell is not the main cost.
- **Updater (S3).** The Tauri updater took an installed v1 (0.1.0, engine 1) to v2 (0.2.0, engine 2) from a local manifest, verified against a minisign key generated for the spike. The engine's sha256 changed from `1295...d142` to `3f01...81df`, and the running engine reported version 2. The updater key is the project's own and free, independent of any Apple certificate.
- **Supervision (S4).** With no guard, both SIGTERM and SIGKILL of the shell left an orphaned engine. With the engine watching stdin for EOF and polling its parent PID, both left 0 engines, on 3.12 and on 3.14. A graceful exit (the shell killing the sidecar on `RunEvent::Exit`) left none even without the guard.
- **Startup (S2).** Spawn to first successful `/health` through the app took a median of 0.072 s (max 0.074 s) on 3.12 and 0.078 s (max 0.102 s) on 3.14, against a bar of 3 s. Both runs had a warm page cache; a true post-reboot cold start was not measured.

## Decision

The desktop shell is Tauri 2. The shell starts, supervises and stops the engine; the engine is a sidecar process, never a library loaded into the shell.

The engine's launch goes through a small executable wrapper declared as the Tauri `externalBin`, and the frozen one-directory engine ships as a bundle resource beside it (see ADR-0056 for why). The engine carries the stdin-EOF plus parent-PID guard, so it exits when the shell dies by any route, and the shell also kills it on a graceful exit. The shell hands the engine its per-launch token over stdin ([ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md)).

Updates use Tauri's updater, signed with a project-owned minisign key.

## Consequences

**What becomes easier:**

- A small installer and a signed update path that needs no paid certificate.
- A shell that is a thin supervisor: all behaviour stays in the synchronous engine behind its local API, so the shell is replaceable.

**What becomes harder, and what we accept:**

- **`externalBin` does not fit a PyInstaller one-directory freeze.** It expects a single binary, while the freeze is a directory of files. The wrapper is the externalBin and the directory is a resource. The spike used a shell script as the wrapper.
- **The updater needs https.** The spike's local manifest over plain `http` only worked with `dangerousInsecureTransportProtocol`, which is for local testing and must never ship. Production updates are served over https.
- **A script wrapper loses its signature in the update tarball.** macOS update tarballs must be built with `COPYFILE_DISABLE=1`, because otherwise AppleDouble `._` files broke the unpack. That setting strips the extended-attribute signature of a script-wrapper externalBin, so the updated app failed `codesign` verification until it was re-signed. The re-sign fixed it. A compiled stub as the wrapper avoids the problem and is the intended direction; this was not built or measured.
- The shell is Rust and web-view based. Contributors to the shell need that toolchain.
- **Not measured:** the installer size of any alternative shell, so the size advantage over Electron is the expected order of magnitude, not a measured ratio; Windows and Linux behaviour; the updater on a rebuilt-and-re-signed release pipeline.

## Alternatives considered

- **Electron.** A mature updater and a large ecosystem, but it ships a full Chromium and a Node runtime, so the installer is larger, and its update signing path leans on platform code-signing certificates the project does not have. The shell would also add a second language runtime next to the engine's Python. Not measured in the spike. Rejected on size and on the signing dependency.
- **pywebview.** The engine's own language and no second toolchain, but it provides no signed updater and no sidecar supervision, so both would be built here. Rejected: it moves the hardest part of the shell onto the project.
- **Briefcase.** Python-native packaging that produces an app bundle, but it ties the shell to the interpreter and offers no updater of its own. Rejected for the same reason as pywebview.
