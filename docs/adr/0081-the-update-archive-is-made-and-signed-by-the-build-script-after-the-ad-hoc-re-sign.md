---
type: Decision
title: "ADR-0081: The update archive is made and signed by the build script after the ad-hoc re-sign"
description: The desktop update archive is made by build-app.sh from the deep ad-hoc re-signed, smoke-checked .app, unpacked and verified, then signed with `tauri signer sign --app-version`, instead of by Tauri's createUpdaterArtifacts during `cargo tauri build`; the updater requires the signed version, and the update check runs in the shell, so the web view's CSP does not widen.
status: Accepted
date: 2026-10-28
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-28T00:00:00Z
sensitivity: public
---

# ADR-0081: The update archive is made and signed by the build script after the ad-hoc re-sign

- **Status:** Accepted
- **Date:** 2026-10-28

## Context

[ADR-0079](0079-the-desktop-app-updates-itself-through-a-signed-feed-whose-private-key-lives-in-a-protected-release-environment.md) adopts the Tauri updater and says, in its Decision, that `bundle.createUpdaterArtifacts` is turned on. Its Consequences add that the archive "must be made from the re-signed, smoke-checked `.app` and then signed, or users receive a different app from the one that was checked", and leave that order to the follow-on (#1624). Two of its other statements also meet the code for the first time here.

The forces, as the implementation found them:

- **`createUpdaterArtifacts` archives the wrong app.** With it on, `cargo tauri build` makes and signs `OpenKOS.app.tar.gz` while it bundles. `desktop/scripts/build-app.sh` deep ad-hoc re-signs the `.app` only after that step ([ADR-0056](0056-the-engine-ships-frozen-with-pyinstaller-one-directory-under-contents-resources-deep-ad-hoc-signed.md)), then verifies the signature and smoke-checks the engine inside it. An archive made by the bundler would hold the app before the re-sign: not the bytes that were checked.
- **The AppleDouble hazard.** [ADR-0055](0055-the-desktop-shell-is-tauri-2-with-the-engine-as-a-supervised-sidecar.md) found that a macOS tarball needs `COPYFILE_DISABLE=1`, or AppleDouble `._` files break the unpack, and that the setting strips a script wrapper's extended-attribute signature. The shipped app has no script wrapper: the engine is a resource the shell starts directly. Whether anything signed still depends on an extended attribute is a fact to check, not to assume.
- **A downgrade through the feed.** The feed (`latest.json`) is fetched over TLS but is not itself signed; the signature covers only the archive. The updater plugin's `requireSignedVersion` refuses an archive whose signature does not carry the version the feed announces, which closes the pairing of a new version number with an older, genuinely signed archive. It needs every archive to be signed with its version.
- **The app's version.** The updater compares the feed's version with the running app's. `tauri.conf.json` carries `0.1.0` while releases are tagged from the package version, so an app built as `0.1.0` would be offered every release forever.
- **The CSP.** ADR-0079 says the web view's CSP must name the new outbound connection. A Content Security Policy governs requests the web view makes; the update check is made by the shell, in Rust, through the plugin, and the page reaches it only through the shell's own commands. Naming GitHub in `connect-src` would let the page reach a host it never needs to.

Measured on the development machine (Apple Silicon, macOS 27.0) with a throwaway key: `build-app.sh --release 0.0.1` built the app as version 0.0.1, made a 28 MB archive with no `._` entries from the re-signed `.app`, unpacked it to a temporary folder where `codesign --verify --deep --strict` reported "valid on disk" and "satisfies its Designated Requirement", and signed it. The signature verified against the throwaway public key with `minisign-verify` (the crate the plugin uses), its trusted comment carried `version:0.0.1`, and a one-byte change to the archive failed verification. No update was installed end to end: that needs the project's real key and a published release.

## Decision

**The build script makes the archive, not the bundler.** `createUpdaterArtifacts` stays off. `build-app.sh --release VERSION` builds the app as `VERSION`, re-signs, verifies and smoke-checks it as before, writes the `.dmg`, then archives that same `.app` with `COPYFILE_DISABLE=1`, unpacks the archive into a temporary folder and verifies the copy's signature, and only then signs the archive with `cargo tauri signer sign --app-version VERSION`. Any failure stops the build. The release job runs it with the tag's version; this supersedes the line of ADR-0079's Decision One that turns `createUpdaterArtifacts` on, and nothing else in it.

**The updater requires the signed version.** `plugins.updater.requireSignedVersion` is on from the first release, so no unversioned signature ever needs to be honoured.

**Every pull request runs the same path with a throwaway key.** CI's `desktop-bundle` job generates a key for the run and builds with `--release`, so the archive, the unpack check and the signing are exercised on every change without the project's key.

**The check runs in the shell, and the CSP does not widen.** The updater plugin is called only from the shell's commands; no capability gives the page the plugin's own commands, and `connect-src` stays IPC and loopback. This replaces ADR-0079's statement that the CSP must name the connection; the documents that said the app makes no network calls name it instead.

## Consequences

- **The archive holds the app that was checked**, and the build proves it on every pull request, so an archive that would not unpack to a valid signature never reaches a release.
- **A release build needs `--release` and the key.** `build-app.sh` without the flag is unchanged, and refuses the flag without the key, its password or a semver version before it builds anything.
- **The app's version is set by the build, not by `tauri.conf.json`.** A local build without `--release` stays `0.1.0` and is offered the latest release, which is harmless for a developer build. A future change that drops the override must keep the app's version equal to the tag.
- **One more moving part in the build script**, which `tauri build` would otherwise own. If a later Tauri version archives after a custom hook, this can move back.
- **The page cannot reach GitHub.** Anything about updates the page shows comes through the shell's commands, which hold the found update and install only the version the person confirmed.
- **Not measured:** an end-to-end update from one published release to the next on the real key; whether macOS asks for Gatekeeper approval again after the updater replaces an ad-hoc signed app (ADR-0079 leaves this to the follow-on's acceptance test, which needs a real release).

## Alternatives considered

- **`createUpdaterArtifacts` with the re-sign moved before it.** ADR-0056 makes the deep re-sign a mandatory step after the build. Moving it inside `tauri build`, ahead of the archive, would need a custom signing command there and would still leave the smoke check after the archive. Rejected: it inverts the order the build already proves.
- **`createUpdaterArtifacts`, then replace its archive.** Turning it on and overwriting its output after the re-sign keeps a tauri-made archive in the bundle folder that is not the shipped one, and makes the build need the key just to bundle. Rejected as two archives where one is wrong.
- **Leave `requireSignedVersion` off.** Simpler, and the archive would still carry its version, since the build signs with `--app-version`. Rejected: it is free on the first release and impossible to add later without re-signing every release users can still update from.
- **Widen `connect-src` to GitHub's release hosts.** Matches ADR-0079's wording. Rejected: it grants the page reach it does not use; the CSP is not what governs the shell's request.
