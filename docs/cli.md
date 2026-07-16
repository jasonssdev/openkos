---
type: Reference
title: OpenKOS CLI Reference (MVP 1)
description: The authoritative command surface for the OpenKOS command-line interface in MVP 1.
tags:
  - openkos
  - cli
  - reference
  - mvp1
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-14T23:00:00Z
sensitivity: public
---

# CLI Reference (MVP 1)

This is the single source of truth for the OpenKOS command line as scoped for **MVP 1**. It is a design reference — **nothing runs yet** (the project is pre-alpha). Later MVPs extend this surface; anything beyond MVP 1 is marked as such.

## Conventions

- **Local-first.** Every command runs on your machine and works offline.
- **Color is a layer, not a requirement.** Output uses color to encode meaning, but respects `NO_COLOR` and a `--no-color` flag, and auto-disables when output is not a TTY (e.g. piped to a file). The symbols (`+`, `~`, `✔`, `→`) carry the meaning without color.
- **Config lives in `openkos.yaml`** at the workspace root, beside `raw/` and `bundle/`; the agent operating manual lives in `AGENTS.md`, next to it.

## Install and first run

**Prerequisites:** Python 3.13+, and [Ollama](https://ollama.com) with a model pulled (for example `ollama pull qwen3`). No accounts or API keys.

Install the engine once (after the first release):

```bash
uv tool install openkos   # or: pipx install openkos — or: pip install openkos
```

Create a bundle per knowledge base:

```bash
mkdir ~/knowledge && cd ~/knowledge
openkos init
```

You install the engine once and run `openkos init` in each knowledge base — like installing git once and running `git init` per repository. One machine can hold several independent bundles, each with its own `openkos.yaml`, model, and default sensitivity.

## Commands

### `openkos init`

Creates a new workspace in the current directory: `raw/` for immutable sources, `bundle/` for the compiled OKF bundle (concept folders, `index.md`, `log.md`), a config file (`openkos.yaml`), and an `AGENTS.md` operating manual. Helps you pick a local model (via Ollama). Run once per workspace.

### `openkos ingest <path>`

Copies the source at `<path>` into `raw/` (immutable) and compiles it into OKF concept documents, recording provenance and updating `index.md` and `log.md`.

Sources are stored under their own names and extensions — `notes.md` lands as `raw/notes.md` — because `raw/` sits beside the OKF bundle rather than inside it. A markdown source therefore needs no special handling and still renders as markdown in any editor. Compiled output goes into `bundle/`, which stays a pure OKF bundle.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the review step; compile, apply, and commit directly (unattended). Overrides `review` in config. |
| `--sensitivity <level>` | Label the source and everything derived from it: `public`, `private`, or `confidential`. Defaults to the config's `default_sensitivity` (itself `private`). |
| `--batch` | Ingest many sources at once when `<path>` is a folder or glob (default is one at a time). |

By default, ingest shows the proposed changes and asks to confirm before saving.

### `openkos query "<question>"`

Answers a natural-language question from the compiled bundle, with citations back to the concepts and their sources. A good answer can be filed back as a new concept (the two-output rule).

### `openkos lint`

Health-checks the bundle. In MVP 1 (freshness v0) the checks are deliberately mechanical:

- **Stale stamps** — flags any fact whose `as of` stamp is older than the configured `freshness_window` (default `7d`). MVP 1 performs no volatility classification; volatility-aware windows (per-type, LLM-suggested) arrive in **MVP 2**.
- **Orphan pages** — flags any concept file not referenced by a markdown link from `index.md` or from another concept. This is computed by scanning markdown links; no graph is needed (graph-based analysis is **MVP 2**).

Reports errors and warnings; does not modify anything without confirmation.

**Lint is not a conformance checker.** It reports OpenKOS's opinion about knowledge *health*, not OKF's verdict about *validity*. OKF explicitly tolerates broken links and missing index entries (§5.3, §9), so a bundle can fail every check here and still be perfectly conformant. Conformance is verified separately, against the three rules of §9.

### `openkos status`

Shows what the bundle contains (counts of sources and concepts), recent activity, and anything needing attention (for example, lint findings).

### `openkos forget <concept-id>`

Removes knowledge. In **MVP 1** this is deliberately one thing: **a simple delete.** It removes the concept document, drops its entry from `index.md`, and updates the operational state. The target is named by its concept ID — the path with `.md` removed, which is what OKF already defines identity to be (`openkos forget people/maria-salazar`).

Undo is **plain git** (`git revert`, `git checkout <file>`) — there is no wrapper command for it in MVP 1. Every change is already a commit, so the safety net exists without new surface.

MVP 1 does **not** check inbound references before deleting. Removing a concept others link to leaves dangling links, which `lint` reports as it would any orphan — OKF tolerates broken links by design (§5.3), so this is a quality signal, not corruption. Archiving (`status: deprecated`), tombstones in `log.md`, the reference-aware scope/depth flow, and the privacy **purge** (git-history rewrite + index cleanup) all arrive in **MVP 2**, alongside the rest of the lifecycle.

You can also just delete the file by hand — the bundle is your files. `forget` is the ergonomic version that cleans up the index and state in one step.

## `openkos.yaml` (workspace config)

Structured settings for the workspace, read by the engine. It lives at the workspace root, beside `raw/` and `bundle/` — not inside the bundle, which holds concept documents and nothing else.

```yaml
name: good-life-demo
model: qwen3:8b           # local model served via Ollama; see tech_stack.md
review: true              # show proposed changes and confirm before saving
default_sensitivity: private
freshness_window: 7d      # age after which a stamp is flagged for re-observation

# Layout — where the engine keeps things, relative to this file.
raw: raw/                 # immutable sources; any extension, never rewritten
bundle: bundle/           # the OKF bundle root

# type_registry is maintained by the engine (canonical + emergent types)
```

## Not in MVP 1

For orientation, these land later and are **not** part of the MVP 1 CLI: semantic/graph query (MVP 2), volatility-aware freshness windows (MVP 2), archive, tombstones, the reference-aware `forget` and purge (MVP 2), the MCP server and local REST API (MVP 3), and OKF import/export (MVP 3).
