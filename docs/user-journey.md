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

Creates the workspace: `raw/` for immutable sources, `bundle/` for the compiled OKF bundle (the concept folders, `index.md`, `log.md`), a config file (`openkos.yaml`), and an `AGENTS.md` operating manual that tells any AI agent how to work with it, and helps the user pick a local model (via Ollama; e.g. `qwen3`). After this, the user never thinks about setup again.

`openkos.yaml` records the defaults that shape the journey, for example:

```
model: qwen3:8b
review: true            # show changes and confirm before saving (default)
default_sensitivity: private
```

## The primary journey: capturing a new source

The motivating case: *"I just had a conversation I don't want to lose."* The scenario below is the one in [`examples/good-life-demo/`](../examples/good-life-demo/): the user is reading philosophy to write an essay. Nine days ago they took notes on Epictetus's *Enchiridion* and compiled them; today a friend who studies the subject corrected one of their readings on a call.

### Step 1 — Ingest by path

The user points OpenKOS at the file. The engine copies it into `raw/` (immutable) and begins.

```bash
openkos ingest ./call-with-maria-2026-07-14.txt --sensitivity confidential
```

- **By path.** `ingest <path>` copies the source into `raw/` for the user — they never have to organize folders by hand. Sources keep their own names and extensions, markdown included, and the compiled knowledge lands in `bundle/`.
- **One at a time by default.** Ingesting a single source keeps the user involved and the results reviewable.
- **Batch is optional.** A folder or glob ingests many at once for users who want throughput: `openkos ingest ./inbox/` or `openkos ingest ./inbox/*.txt`.
- **Sensitivity at capture.** The source (and everything derived from it) can be labeled: `--sensitivity confidential`. Unlabeled defaults to `private`.

### Step 2 — Compile

The engine reads the immutable source with the local model, drafts a summary concept, creates or updates related concept pages, records provenance back to the source, applies freshness stamps, and updates `index.md` and `log.md`. Here it finds that the call corrects something already in the base — the reading of *apatheia* recorded nine days ago — so rather than write a new page it proposes revising the existing one. The user waits a moment; nothing has been saved yet.

### Step 3 — Review and confirm (default) — or hand it off

**Default (interactive):** the engine shows what it *proposes* to do and asks before saving.

```
$ openkos ingest ./call-with-maria-2026-07-14.txt --sensitivity confidential
→ Copied to raw/call-with-maria-2026-07-14.txt (immutable)
→ Reading with local model (qwen3:8b)…

Proposed changes:
  +  bundle/sources/call-with-maria-2026-07-14.md   (new summary)
  +  bundle/people/maria-salazar.md                 (new)
  ~  bundle/concepts/stoicism.md                    (v1→v2: apatheia corrected;
                                                     sensitivity private → confidential)
  +  bundle/decisions/frame-the-essay-on-the-dichotomy-of-control.md   (new)
  ~  bundle/index.md, bundle/log.md

Apply? [Y]es / [e]dit / [n]o:
```

The user can accept, edit before saving, or reject. This keeps consequential changes under human control — and this panel is exactly where that matters. It is not only proposing to rewrite a page the user wrote themselves; it is proposing to *reclassify* it, because a confidential source now feeds it (the high-water-mark rule). Both are consequential, so both are shown before anything is saved.

**Optional (unattended):** a user who trusts the engine skips the review entirely — they just capture, and the engine does the rest.

```bash
openkos ingest ./call-with-maria-2026-07-14.txt --sensitivity confidential --auto
```

```
✓ Ingested call-with-maria-2026-07-14.txt → 4 objects touched, committed.
```

`--auto` (per command) overrides the default; setting `review: false` in the config makes unattended the standing behavior. Either way, nothing is lost — every change is a git commit and can be inspected or reverted afterward. Review is a preference, not a requirement.

### Step 4 — Commit

Accepted changes are committed to git. The knowledge is now part of the base, with full history preserved. One capture cycle is complete.

### Step 5 — Use (the value moment)

Later, the user asks a question and gets an answer with citations back to the source:

```bash
openkos query "what does apatheia actually mean?"
```

```
Apatheia is freedom from the pathē — the destructive passions — not the
absence of feeling: the Stoics kept the eupatheiai, the "good feelings" [1].
It is commonly misread as "indifference to emotion" by analogy with the
English cognate apathy [1].

Sources:
  [1] bundle/concepts/stoicism.md
      → bundle/sources/call-with-maria-2026-07-14.md
      → raw/call-with-maria-2026-07-14.txt
```

Two things are worth noticing in that answer. It is the *corrected* understanding, not the one the user first wrote down — the base learned. And the citation chain runs all the way back to the immutable original, through the Source concept that represents it: the user can always ask *how do I know this?* and get a file path rather than a shrug.

A good answer can be filed back as a new concept, so exploration compounds — feeding the loop again.

## Secondary journeys

- **Ask:** `openkos query "…"` — cited answers assembled from the bundle.
- **Keep it honest:** `openkos lint` — in MVP 1, flags stale `as of` stamps (older than the configured freshness window) and orphan pages (concepts no markdown link reaches from `index.md` or another concept); volatility-aware and contradiction checks arrive in MVP 2. The lint is OpenKOS's opinion about knowledge health, not a verdict on OKF validity — a bundle it complains about is still a perfectly conformant bundle.
- **Orient:** `openkos status` — what the base contains, recent activity, anything needing attention.
- **Browse:** open the folder in any editor — the bundle is just markdown.

## Editing by hand

The bundle is your files, so you can edit any concept document directly — in Obsidian, VS Code, or any editor — without asking the engine. This is not a workaround; it is the point. The canonical files are the source of truth, and the engine's indexes are derived from them.

When you edit a concept by hand, the engine reconciles the next time you run a command: it notices the file changed (by content hash), re-indexes what's affected, and notes the external edit in `log.md`. Because every index is rebuildable from the files, your edit is never lost — the engine adapts to it. If the edit introduced a problem (invalid frontmatter, a broken link, a stale `as of` stamp), `openkos lint` surfaces it; the engine flags, it does not overrule you.

When you later ingest a source that touches a concept you edited, the engine reads the current file first and builds on your version, and review mode shows the merged change before saving — so the compiler adds to your edit rather than overwriting it. Git keeps every version, so any change stays diffable and reversible.

One exception: `raw/` sources are read-only by convention. Editing an original by hand breaks its provenance hash; to correct a source, add a new one rather than rewriting the original.

## Removing knowledge (rare by design)

OpenKOS accumulates knowledge, so removal is a last resort — and the experience is built to steer the user toward the gentlest option that fits. Most "I want to delete this" moments are really something else: undo a wrong ingest, archive a dead topic, retire a stale fact into a snapshot, or merge a duplicate. A true delete is reserved for genuine mistakes and, above all, **privacy** ("I need this gone").

The user reaches for one verb, `forget`, which shows the consequences before acting and asks for scope and depth. Continuing the running example: Maria mentioned a move she is not making public, and later asks that it not be kept anywhere. That is the privacy case, and it is the one the design exists for.

```
$ openkos forget people/maria-salazar
This object is referenced by 2 others and was derived from:
  raw/call-with-maria-2026-07-14.txt   (sensitivity: confidential)

Scope:   [1] just this object   [2] the source and everything derived from it
Depth:   [a]rchive (keep history)   [d]elete (keep git history)   [p]urge (erase everything, irreversible)
>
```

The target is named by its concept ID — the path with `.md` removed, which is what OKF already defines identity to be.

It defaults to the least destructive choice, surfaces what links to the target so nothing is silently orphaned, requires explicit confirmation for a **purge** (the right-to-be-forgotten path that also rewrites git history and clears derived indexes), and stays human-in-the-loop even under `--auto`. Everything except a privacy purge is logged.

That is the mature shape. **In MVP 1, `forget` is only the simple delete** — remove the concept, its index entry, and its state; undo is plain git. The scope/depth panel above, archiving, tombstones, and the purge arrive in MVP 2 with the rest of the lifecycle. The thin version is enough to keep a first knowledge base tidy while the model is still producing rough drafts, which is the job MVP 1 actually has.

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
