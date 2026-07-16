---
type: Reference
title: OpenKOS User Journey
description: The end-to-end user experience — from capturing a source to getting trustworthy, cited knowledge back — and the UX principles behind it.
tags:
  - openkos
  - user-experience
  - ux
  - reference
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-15T00:00:00Z
sensitivity: public
---

# User Journey

This document describes the experience of using OpenKOS, so that the product is designed around the person, not the pipeline. It centers on the MVP 1 experience and notes where later-MVP capabilities extend the journey.

## The core idea: the journey is a loop, not a line

A user does not "finish" with OpenKOS. Each source they add makes the base more useful, and each answer they get can become new knowledge. The trip looks like this:

```
        capture ──▶ ingest ──▶ compile ──▶ review / confirm ──▶ commit
           ▲                                                      │
           │                                                      ▼
        maintain ◀────────────── query (cited answers) ◀──── use the base
           │                                                      │
           └──────────────── good answers filed back ────────────┘
```

The **value moment** — the reason the whole thing exists — is the *query* step: getting a trustworthy, cited answer from knowledge the user never had to organize by hand.

## UX principles

Every interface decision serves these:

- **Least friction to capture.** Getting a source in should be one short command. If capture is annoying, the base never grows.
- **Human-in-the-loop by default, but optional.** By default the user reviews what the engine proposes before it is saved. But a user who trusts the engine can hand it full control and just capture — the review step is a choice, not a wall.
- **Local and private.** Everything runs on the user's machine. Sensitivity is respected at every step (a confidential source never reaches a cloud model).
- **Transparency.** The user can always see what changed and where a fact came from. Provenance and a readable change log are part of the experience, not a debugging afterthought.
- **Plain files, no lock-in.** The result is an OKF bundle the user can open in Obsidian, VS Code, or GitHub — and take elsewhere at any time.

## First run (one-time setup)

```bash
openkos init
```

Creates the bundle structure (`raw/`, the concept folders, `index.md`, `log.md`), a config file (`openkos.yaml`), and an `AGENTS.md` operating manual that tells any AI agent how to work with the bundle, and helps the user pick a local model (via Ollama; e.g. `qwen2.5`). After this, the user never thinks about setup again.

`openkos.yaml` records the defaults that shape the journey, for example:

```
model: qwen2.5
review: true            # show changes and confirm before saving (default)
default_sensitivity: private
```

## The primary journey: capturing a new source

The motivating case: *"I have a meeting transcript I want to capture."*

### Step 1 — Ingest by path

The user points OpenKOS at the file. The engine copies it into `raw/` (immutable) and begins.

```bash
openkos ingest ./standup-2026-07-14.txt
```

- **By path.** `ingest <path>` copies the source into `raw/` for the user — they never have to organize folders by hand.
- **One at a time by default.** Ingesting a single source keeps the user involved and the results reviewable.
- **Batch is optional.** A folder or glob ingests many at once for users who want throughput: `openkos ingest ./inbox/` or `openkos ingest ./inbox/*.txt`.
- **Sensitivity at capture.** The source (and everything derived from it) can be labeled: `--sensitivity confidential`. Unlabeled defaults to `private`.

### Step 2 — Compile

The engine reads the immutable source with the local model, drafts a summary concept, creates or updates related concept pages, records provenance back to the source, applies freshness stamps, and updates `index.md` and `log.md`. The user waits a moment; nothing has been saved yet.

### Step 3 — Review and confirm (default) — or hand it off

**Default (interactive):** the engine shows what it *proposes* to do and asks before saving.

```
$ openkos ingest ./standup-2026-07-14.txt
→ Copied to raw/standup-2026-07-14.txt (immutable)
→ Reading with local model (qwen2.5)…

Proposed changes:
  +  sources/standup-2026-07-14.md   (new summary)
  +  concepts/sarah-chen.md          (new)
  ~  concepts/auth-refactor.md       (updated: + blocker on API contract)
  ~  index.md, log.md

Apply? [Y]es / [e]dit / [n]o:
```

The user can accept, edit before saving, or reject. This keeps consequential changes under human control.

**Optional (unattended):** a user who trusts the engine skips the review entirely — they just capture, and the engine does the rest.

```bash
openkos ingest ./standup-2026-07-14.txt --auto
```

```
✓ Ingested standup-2026-07-14.txt → 4 objects touched, committed.
```

`--auto` (per command) overrides the default; setting `review: false` in the config makes unattended the standing behavior. Either way, nothing is lost — every change is a git commit and can be inspected or reverted afterward. Review is a preference, not a requirement.

### Step 4 — Commit

Accepted changes are committed to git. The knowledge is now part of the base, with full history preserved. One capture cycle is complete.

### Step 5 — Use (the value moment)

Later, the user asks a question and gets an answer with citations back to the source:

```bash
openkos query "what is blocking the auth refactor?"
```

```
The auth refactor is blocked on the API contract, raised by Sarah Chen
in the 2026-07-14 standup [1].

Sources:
  [1] concepts/auth-refactor.md → raw/standup-2026-07-14.txt
```

A good answer can be filed back as a new concept, so exploration compounds — feeding the loop again.

## Secondary journeys

- **Ask:** `openkos query "…"` — cited answers assembled from the bundle.
- **Keep it honest:** `openkos lint` — flags unstamped volatile facts, stale claims, and orphan pages.
- **Orient:** `openkos status` — what the base contains, recent activity, anything needing attention.
- **Browse:** open the folder in any editor — the bundle is just markdown.

## Editing by hand

The bundle is your files, so you can edit any concept document directly — in Obsidian, VS Code, or any editor — without asking the engine. This is not a workaround; it is the point. The canonical files are the source of truth, and the engine's indexes are derived from them.

When you edit a concept by hand, the engine reconciles the next time you run a command: it notices the file changed (by content hash), re-indexes what's affected, and notes the external edit in `log.md`. Because every index is rebuildable from the files, your edit is never lost — the engine adapts to it. If the edit introduced a problem (invalid frontmatter, a broken link, an unstamped volatile fact), `openkos lint` surfaces it; the engine flags, it does not overrule you.

When you later ingest a source that touches a concept you edited, the engine reads the current file first and builds on your version, and review mode shows the merged change before saving — so the compiler adds to your edit rather than overwriting it. Git keeps every version, so any change stays diffable and reversible.

One exception: `raw/` sources are read-only by convention. Editing an original by hand breaks its provenance hash; to correct a source, add a new one rather than rewriting the original.

## Removing knowledge (rare by design)

OpenKOS accumulates knowledge, so removal is a last resort — and the experience is built to steer the user toward the gentlest option that fits. Most "I want to delete this" moments are really something else: undo a wrong ingest, archive a dead topic, retire a stale fact into a snapshot, or merge a duplicate. A true delete is reserved for genuine mistakes and, above all, **privacy** ("I need this gone").

The user reaches for one verb, `forget`, which shows the consequences before acting and asks for scope and depth:

```
$ openkos forget concepts/client-acme.md
This object is referenced by 3 others and was derived from:
  raw/meeting-acme-2026-05-10.txt   (sensitivity: confidential)

Scope:   [1] just this object   [2] the source and everything derived from it
Depth:   [a]rchive (keep history)   [d]elete (keep git history)   [p]urge (erase everything, irreversible)
>
```

It defaults to the least destructive choice, surfaces what links to the target so nothing is silently orphaned, requires explicit confirmation for a **purge** (the right-to-be-forgotten path that also rewrites git history and clears derived indexes), and stays human-in-the-loop even under `--auto`. Everything except a privacy purge is logged. In MVP 1, `forget` covers undo, archive, and simple delete; the reference-aware scope/depth panel and the privacy purge shown above arrive in MVP 2.

## Two ways to work

| | Interactive (default) | Unattended |
| --- | --- | --- |
| Command | `openkos ingest <path>` | `openkos ingest <path> --auto` (or `review: false`) |
| Before saving | Shows proposed changes, asks to confirm | Saves and commits directly |
| Best for | Staying involved, important sources | Bulk capture, trusted flows |
| Safety net | Review, plus git history | git history (inspect / revert anytime) |

## MVP 1 scope

For MVP 1 the journey is **text only**: `ingest` accepts plain-text sources (`.txt`, `.md`). A transcript that is already text fits perfectly. Other formats (PDF, web, audio, images) arrive as producers in later MVPs and extend this same journey without changing its shape.

## Deferred / open questions

To revisit as the product matures: batch review granularity (confirm per source vs per batch), an explicit `undo` beyond `git revert`, and how a "watched inbox" folder would fit for users who prefer drop-in capture.
