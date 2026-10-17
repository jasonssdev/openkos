---
type: Decision
title: "ADR-0070: The desktop shell keeps the remote backend's API key in the OS keychain and hands it to the engine as its environment"
description: The desktop shell stores the openai-compatible backend's API key in the operating system's keychain and sets it as OPENKOS_OPENAI_API_KEY on the engine process it supervises; the engine still reads only that variable, the web view can set, clear and test for the key but never read it, and the key never reaches the local API, the workspace or a log.
status: Accepted
date: 2026-10-16
tags:
  - openkos
  - adr
  - desktop
  - llm
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# ADR-0070: The desktop shell keeps the remote backend's API key in the OS keychain and hands it to the engine as its environment

- **Status:** Accepted
- **Date:** 2026-10-16

## Context

The opt-in `openai-compatible` backend may take an API key, and the engine reads it only from the `OPENKOS_OPENAI_API_KEY` environment variable ([ADR-0031](0031-openai-compatible-backend.md)). That rule exists because a secret has no safe home in the workspace: `init` autocommits `openkos.yaml`, so a key written there lands in the user's git history. `AGENTS.md` states it as a principle: a key is taken only from that variable, never from a file in the workspace.

[MVP 6](../roadmap.md) is for a person who never opens a terminal, and that person cannot set an environment variable. The roadmap listed where a remote backend's key is kept as a decision the arc owes, and its product constraints already fix that a cloud model is optional, off by default, and turned on in settings. Without somewhere to keep the key, that setting has nothing to turn on.

The forces:

- **The engine's rule should not move.** The variable is the one door every entry point shares: the CLI, MCP, the daemon and the local API all reach the backend through the resolver in `application/backends.py`. A second door in the engine would have to be specified, tested and kept safe for all of them.
- **The shell already owns the engine's process.** It starts, supervises and stops the engine ([ADR-0055](0055-the-desktop-shell-is-tauri-2-with-the-engine-as-a-supervised-sidecar.md)), so it decides the environment the engine starts with.
- **The local API is reachable from any page the user opens.** It is guarded by a per-launch token and `Host`/`Origin` checks ([ADR-0058](0058-the-local-api-is-loopback-http-with-a-per-launch-bearer-token-and-host-origin-checks.md)), but a credential that never crosses it cannot leak through it.
- **The web view is the least trusted part of the app.** It renders text that comes from the user's sources. Whatever it can read, a flaw in it can exfiltrate.
- **macOS is the first platform,** and it ships a keychain every user already has, encrypted at rest and unlocked with the login session.

## Decision

The desktop shell keeps the key in the operating system's keychain and hands it to the engine only as the `OPENKOS_OPENAI_API_KEY` environment variable of the engine process it supervises. The engine does not change: it still reads only that variable.

- **One keychain item, owned by the shell.** On macOS it is a generic password in the login keychain, service `io.github.jasonssdev.openkos` (the app's identifier) and a fixed account name, written through the maintained `keyring-core` and `apple-native-keyring-store` crates (MIT or Apache-2.0). The store sits behind a trait, so the shell's tests use an in-memory store and never touch a real keychain.
- **Three commands, none of which returns the key.** The web view can set the key, clear it, and ask whether one is set, which answers a boolean. There is no command that reads the key back. A refusal answers a reason (`invalid_key`, `not_supported`, `store_failed`) and an English sentence, and neither ever contains the key.
- **The key is validated before it is stored.** It is trimmed. An empty key, a key with a line break or another control character, and a key longer than any provider's are refused, because each would be a broken environment value.
- **Changing the key restarts the engine.** The engine reads its environment once, at start, so setting or clearing the key restarts it with the new environment. The restart is the user's request, not a crash: it never spends the crash-restart budget, it cuts a pending backoff short, and after a final failure it starts supervision again. A restarted engine has a new token, as after any restart.
- **The key is read once and kept in memory.** The shell reads the keychain the first time an engine starts and keeps the result, so a restart does not ask the keychain again. A failed read is not remembered. When the keychain cannot be read, the engine starts without the key, so a local backend keeps working, and the shell writes the reason, never the key, to its stderr.
- **The keychain key wins; an inherited variable survives.** When a key is kept, the engine's variable is set to it, replacing any value the shell itself inherited. When none is kept, the shell's own environment passes through unchanged, so a developer who exports the variable before `cargo run` keeps working.
- **The key goes nowhere else.** It never travels through the local API, never appears in the engine state the web view reads, never reaches the engine's argv, and is never written to the workspace or a log. The types that carry it print `<redacted>` in debug output and cannot be serialised.
- **Other platforms compile and say so.** Where the shell has no keychain store, the three commands answer `not_supported`, and the engine starts with the shell's own environment.

## Consequences

**Easier.**

- A person who never opens a terminal can use a remote backend once the settings screen exists, and the warning the roadmap requires has a real switch behind it.
- The engine, its tests and its rule are unchanged. Every entry point keeps one way to receive a key, and the CLI, MCP and the daemon gain nothing they must secure.
- The key is encrypted at rest by the operating system and never lands in git, the workspace, the local API or the web view.

**Harder, and accepted.**

- **The rule's letter widens, its intent holds.** Read literally, `AGENTS.md` said the key came only from an environment variable; now the app's user enters it in the app, and it is stored in the keychain. The engine still reads only the variable, and the key is still never a file in the workspace. The principle's wording is updated to say so.
- **The key is in the engine's environment.** Any process that can read another process's environment as the same user can read it, as with any exported variable. That is the exposure the variable already had, not a new one.
- **The shell holds one secret.** It no longer holds no state of its own: a flaw in the shell's command surface is now a path to the keychain item. The commands are kept to three, none returns the key, and each refusal is a fixed reason.
- **A keychain prompt is possible.** An unsigned or re-signed build may make macOS ask the user to allow access to the item. Reading it once per launch keeps that to one prompt; the prompt itself is the operating system's, and has not been exercised in tests.
- **Windows and Linux have no store yet.** They answer `not_supported`, and each needs its own store and its own decision about prompts when the arc reaches them.
- **The key is per user, not per workspace.** One kept key serves every workspace the app opens. A user who needs two keys for two workspaces cannot have them through the app.

## Alternatives considered

- **The environment variable only.** Rejected: the user MVP 6 is for cannot set one, so the cloud model setting the roadmap promises would be unreachable.
- **A file outside the workspace,** for example under the user's configuration directory, read by the engine or the shell. Rejected: the key is plaintext at rest, readable by every process the user runs, and easily swept into a backup or a synced folder. It honours the letter of "never a file in the workspace" while losing the intent.
- **The engine reading the keychain itself.** Rejected: it puts a platform secret store, its prompts and its failure modes into the Python core and the frozen engine, for every entry point, when only the app needs it. It would also make "the engine reads the key only from the environment" false, and a second code path for a credential is a second thing to keep safe.
- **Sending the key to the engine through the local API.** Rejected: it puts the credential on the one surface any page the user opens can reach, and it would need a write endpoint that exists only to carry a secret.
- **Letting the web view read the key back,** for example to show it masked. Rejected: the page renders text from the user's sources, so anything it can read, a flaw in it can send elsewhere. A boolean is enough to show whether a key is set.
- **Counting a key change's restart against the crash budget.** Rejected: three key changes in a minute would leave the engine failed although nothing crashed.
