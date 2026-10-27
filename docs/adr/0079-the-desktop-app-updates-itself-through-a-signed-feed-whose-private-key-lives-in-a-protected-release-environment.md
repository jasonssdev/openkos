---
type: Decision
title: "ADR-0079: The desktop app updates itself through a signed feed whose private key lives in a protected release environment"
description: The desktop app adopts the Tauri updater plugin with a feed on GitHub releases and an in-app prompt that never installs silently. The updater private key and its password are encrypted secrets of a GitHub `release` environment that needs the owner's approval for every deployment, with an offline backup; the public key is committed in tauri.conf.json.
status: Proposed
date: 2026-10-27
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-27T00:00:00Z
sensitivity: public
---

# ADR-0079: The desktop app updates itself through a signed feed whose private key lives in a protected release environment

- **Status:** Proposed
- **Date:** 2026-10-27

## Context

The roadmap lists "the app's own updates are signed with the project's
updater key" as a product constraint, and the desktop status paragraph says
the updater has not started (#1624). There is no updater plugin in
`desktop/src-tauri/Cargo.toml` or `tauri.conf.json`, and every fix needs a
manual re-download of the `.dmg`.

What is already decided or true:

- **ADR-0055** chose Tauri 2 partly for its built-in signed updater and
  recorded a spike in which the updater took an installed 0.1.0 to 0.2.0 from
  a local manifest, verified against a minisign key generated for the spike.
  Its stated gap: "the updater on a rebuilt-and-re-signed release pipeline"
  was not measured. It also says production updates are served over https and
  `dangerousInsecureTransportProtocol` must never ship.
- **No Apple Developer ID.** The app is re-signed ad hoc and not notarized
  (`docs/install-desktop.md`), so the update signature is the only
  cryptographic proof of who published an update. The signing key must be one
  the project owns.
- **The release pipeline.** `.github/workflows/release.yml`, job
  `desktop-release`, runs on a `v*.*.*` tag with `contents: write`, builds the
  `.dmg` with `desktop/scripts/build-app.sh` and attaches it to the GitHub
  release. It deliberately uses no caches: it holds `contents: write` and its
  output is the binary users download.
- **How the Tauri 2 updater works** (official docs, `plugin/updater`).
  `tauri signer generate -w <path>` writes a minisign key pair and asks for a
  password. With `bundle.createUpdaterArtifacts: true` the build signs the
  update archive using the environment variables `TAURI_SIGNING_PRIVATE_KEY`
  (path or content) and optionally `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`; `.env`
  files are not read. `plugins.updater.pubkey` holds the public key content
  (it cannot be a file path) and `plugins.updater.endpoints` lists https
  URLs; a static `latest.json` on a GitHub release
  (`https://github.com/<owner>/<repo>/releases/latest/download/latest.json`)
  is a documented endpoint. The app verifies each downloaded archive's
  signature against the compiled-in public key before installing.

The forces: the updater key decides what code runs on every user's Mac, so it
is the highest-stakes secret the project will hold; the release job is the only
job with write access to the repository; and the owner is the only maintainer.

## Decision

**One. We adopt the Tauri updater plugin.** `bundle.createUpdaterArtifacts` is
turned on, the plugin is added to the shell, and `plugins.updater` carries the
public key and one https endpoint.

**Two. The update feed is served from GitHub releases.** The release job
uploads the signed update archive and a `latest.json` to the tag's release, and
the endpoint is that release's `latest.json`. No server, account or third-party
host is added.

**Three. The app never updates silently.** It checks for an update and shows an
in-app prompt with the new version and its notes. Nothing downloads or
installs until the person confirms. The check can be turned off, and the app
works fully with it off.

**Four. The private key lives only in a protected GitHub `release`
environment.** The key and its password are two encrypted secrets of an
environment named `release` (`TAURI_SIGNING_PRIVATE_KEY` and
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`). The environment requires the owner's
approval for every deployment and accepts only `v*.*.*` tags. The key is
generated with `tauri signer generate` with a non-empty password. An offline
backup of the key and its password is kept in the owner's password manager. The
key is never committed and never stored as a plain repository secret.

**Five. The public key is committed in `tauri.conf.json`.** It is public by
design and is what binds every installed app to the key.

**Six. The `desktop-release` job gains `environment: release`.** Only that job,
so the secrets are invisible to every other job and to pull-request builds. The
job keeps its no-cache rule.

This ADR changes no code or workflow; the follow-on work under #1624 does.

## Consequences

- **Updates become one confirmation and stay verifiable without an Apple
  certificate.** First install still meets the Gatekeeper approval
  (`docs/install-desktop.md`). Whether an updated, ad-hoc re-signed app asks
  the user again is not measured here; it belongs in the follow-on's
  acceptance test.
- **Every release waits for the owner.** A tag push alone cannot sign
  anything, and a release does not finish unattended. The owner is the only
  reviewer, so the environment's "prevent self-review" option must be off, or
  the owner could never approve their own release. The gate therefore guards
  against a stolen workflow trigger or a hostile pull request, not against the
  owner's own account.
- **The update archive and the build script must agree.** `build-app.sh` deep
  ad-hoc re-signs the `.app` after `cargo tauri build`. The update archive must
  be made from the re-signed, smoke-checked `.app` and then signed, or users
  receive a different app from the one that was checked. ADR-0055 records the
  related tarball hazard (`COPYFILE_DISABLE=1` strips a script wrapper's
  signature). The follow-on settles this order.
- **The feed depends on GitHub.** `releases/latest` skips prereleases, so a
  prerelease is never offered. If GitHub is unreachable the check fails quietly
  and the app keeps working.
- **Local-first is unaffected.** There is no account and no telemetry. The check
  is one https request for a static file, and the person can turn it off.
  Workspace, engine and models never leave the machine. It is a new outbound
  connection from the shell, so the app's CSP (`connect-src` today allows only
  IPC and loopback) and any doc that says the app makes no network calls must
  name it.
- **Key loss.** The public key cannot regenerate the private key. If both the
  environment secret and the offline backup are lost, no installed app can
  accept another update, because each trusts only the old public key. Recovery
  is a manual re-download of a build carrying a new public key, which is
  today's situation. The offline backup makes this unlikely.
- **Rotation works only while the old key is still trusted.** Ship a release
  signed with the old key whose `tauri.conf.json` carries the new public key.
  Apps that install it trust the new key from then on, and later releases are
  signed with it. Apps that never install that release must be re-downloaded.
- **Key compromise.** The key alone does not push code to users: an attacker
  also needs to publish to the feed (write access to the repository's
  releases) or to intercept it. Response: delete any malicious release and
  tag, replace the environment secrets with a new key pair, and ship the
  rotation release at once. That release is still signed by the old,
  compromised key, so it races the attacker, and it cannot protect an app that
  has already installed an attacker's update. State in the release notes that
  anyone unsure of their build must re-download and check the published
  sha256. This residual risk of a self-held key is accepted.
- **Both secrets share one environment**, so one approved run exposes both to
  that run. The required approval and the tag restriction are the control, and
  the workflow must never print either value.
- **Forks never sign.** The existing `github.repository` guard stays, and a
  fork has no `release` environment secrets.

## Alternatives considered

- **The key only on the owner's Mac.** Strongest against a CI compromise and
  no secret in GitHub. Rejected: releases would need the owner's machine for a
  manual build and upload, the release job is the repeatable record of what was
  shipped, and one lost laptop loses the only copy of the key.
- **A plain repository secret.** Simplest. Rejected: any workflow run with
  secret access can read it, and nothing waits for the owner. The environment
  adds the approval gate and the tag restriction for the same storage.
- **No updater, manual re-download only.** Acceptable for the exit session, and
  no key risk. Rejected for a real release: it contradicts the roadmap
  constraint, and a user on a vulnerable build gets no prompt to move.
