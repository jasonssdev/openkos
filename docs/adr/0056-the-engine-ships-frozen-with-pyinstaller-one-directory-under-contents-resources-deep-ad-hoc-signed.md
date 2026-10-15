---
type: Decision
title: "ADR-0056: The engine ships frozen with PyInstaller one-directory under Contents/Resources, deep ad-hoc signed"
description: The engine is frozen with PyInstaller in one-directory mode, shipped under Contents/Resources and never Contents/MacOS, and the whole bundle is deep ad-hoc re-signed after the build.
status: Proposed
date: 2026-10-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-14T00:00:00Z
sensitivity: public
---

# ADR-0056: The engine ships frozen with PyInstaller one-directory under Contents/Resources, deep ad-hoc signed

- **Status:** Proposed
- **Date:** 2026-10-14

## Context

The desktop app must run the engine with no Python installed by the user, offline after setup ([roadmap](../roadmap.md)). The roadmap judges a freeze on native extensions, notarization, antivirus false positives and cold start, and excludes a first-run download of the engine because it breaks the offline promise.

The project ships without an Apple Developer ID ([ADR-0055](0055-the-desktop-shell-is-tauri-2-with-the-engine-as-a-supervised-sidecar.md)), so the bundle is at best ad-hoc signed and meets Gatekeeper's warning.

Evidence comes from a throwaway packaging spike run on the project's development machine (Apple Silicon, macOS 27.0), using PyInstaller 6.22.3 with no special flags or entitlements: `--onedir --collect-all sqlite_vec --collect-all openkos` sufficed. All numbers below are from that spike, on Python 3.12.7 and on Python 3.14.6 (the interpreter [ADR-0054](0054-python-314-is-the-only-supported-interpreter.md) adopts).

- **Native extension.** `sqlite_vec` v0.1.9 loads in the frozen build, and `application.query.run_query` answers through the frozen engine and cites the expected concept, on both interpreters. The 3.14 freeze had zero build warnings beyond platform noise.
- **Where the freeze goes decides whether the app opens.** The first build put the engine's `_internal` directory inside `Contents/MacOS`. `codesign` then treated its dist-info and `python3.12` directories as subcomponents, the bundle seal was absent, `spctl --assess` errored and `codesign --verify` failed. A quarantined launch showed "is damaged and can't be opened" with only Move to Trash and Cancel. After shipping the one-directory freeze as `Contents/Resources/engine` and running `codesign --force --deep -s -`, verification was valid, `spctl` said "rejected" (expected for an ad-hoc signature), and the user saw "Not Opened: Apple could not verify..." with Move to Trash and Done. That is the recoverable path, through Privacy & Security and Open Anyway.
- **Startup.** Spawn to first successful request through the app: median 0.072 s (max 0.074 s) on 3.12, median 0.078 s (max 0.102 s) on 3.14, bar 3 s. Warm page cache; a true post-reboot cold start was not measured.
- **Sizes.**

| | Python 3.12.7 | Python 3.14.6 |
|---|---|---|
| Engine one-directory, standalone | 35 MB | 34 MB |
| Engine inside the `.app` | 35 MB | 44 MB |
| `.app` | 48 MB | 57 MB |
| `.dmg` | 24 MB | 30 MB |
| Updater archive | 21 MB | 25 MB |

- **Tauri's resource copy dereferences symlinks.** The 3.14 freeze ships `Python.framework` with symlinks (the standalone one-directory has 4); the copy inside the `.app` has 0, so the 5.4 MB interpreter binary is stored more than once. That duplication is the growth above. `codesign` still verifies and the app runs.
- **A hardcoded path silently shipped the wrong interpreter.** The shell's resource mapping pointed at the 3.12 freeze, and the first 3.14 build bundled 3.12 without any error. It was caught by listing the bundled engine. The build script now rewrites the path and fails if `Python.framework` is missing from the bundled engine.
- A quarantined copy executed directly was killed by SIGKILL (exit -9); the engine must be launched from inside the signed app.

## Decision

The engine is frozen with PyInstaller `--onedir`. The directory ships as a bundle resource under `Contents/Resources/engine`, never under `Contents/MacOS`.

After the build, the whole app bundle is **deep ad-hoc re-signed** (`codesign --force --deep -s -`) and the result is verified (`codesign --verify --deep --strict`). This step is mandatory, not an optimization: the re-sign also repairs signatures stripped by the update tarball ([ADR-0055](0055-the-desktop-shell-is-tauri-2-with-the-engine-as-a-supervised-sidecar.md)).

The build must verify that the bundled interpreter is the intended one (3.14) and fail otherwise, and must verify that the framework is present, so the silent wrong-interpreter ship cannot recur.

## Consequences

**What becomes easier:**

- One tested artifact: the engine that runs in the app is the same frozen tree the build verified, with native extensions loading.
- No network at first launch.

**What becomes harder, and what we accept:**

- **Product consequences for the install guide.** These are user-facing and belong on the download page, not in this decision's mechanism:
  - **App Translocation.** A quarantined app that was not moved by the user runs from a randomized read-only copy (the engine path was `.../AppTranslocation/<uuid>/d/...` in the spike). That can break in-place updates and any path next to the app. The user must drag the app to Applications before the first open.
  - **One Open Anyway per signature.** The approval applies to one ad-hoc signature (cdhash). A rebuilt app has a new cdhash and asks again, so every release asks once. The project chose to ship unsigned (roadmap constraint). After approval, re-quarantining the same app launched without a dialog.
- Bundle size grows by the symlink duplication until it is avoided (for example by shipping the framework in a form the resource copy does not flatten). This was not attempted.
- The freeze is macOS arm64 only; PyInstaller is not a cross-compiler, so each platform needs its own build.
- **Not measured:** antivirus false positives (no scanner was run, and there was no Windows build); notarization (out of scope, no Developer ID); the Gatekeeper click-through on the 3.14 build (a rebuilt app has a new cdhash and the dialog was not re-run); a true cold start after a reboot; the 3.14 updater archive being applied.

## Alternatives considered

- **A python-build-standalone tree, with the engine installed into it.** A relocatable interpreter, nothing frozen. Not built or measured in the spike. Rejected for now: it ships source and many loose files that `codesign` must seal one by one, and it does not remove the Contents/Resources placement or the re-sign. Reopen if freezing breaks on a future dependency.
- **Nuitka.** Compiles to native code, which could shrink and speed the engine, but adds a C toolchain to the build and changes how `sqlite_vec` and package data are collected. Not tried. Rejected: PyInstaller met every check with no special flags.
- **A first-run download with `uv`.** Smallest installer, but the first launch needs the network and a package index, which breaks the offline-after-setup promise. Excluded by the roadmap and rejected.
- **PyInstaller one-file.** Not tried. Rejected on principle: it unpacks to a temporary directory on every start, and the unpacked files would sit outside the signed bundle.
- **Leaving the freeze in `Contents/MacOS`.** Measured and rejected: the bundle seal was absent and the quarantined app was reported as damaged.
