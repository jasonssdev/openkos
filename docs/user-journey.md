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

**In MVP 1:** `compile` is a null compiler — the source is embedded verbatim into exactly one `Source` concept, with no LLM synthesis. **Later MVPs** grow `compile` into the richer step this loop depicts: an LLM drafting a summary, updating related concept pages, and reconciling corrections across the base.

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

Creates the workspace: `raw/` for immutable sources, `bundle/` for the compiled OKF bundle (the concept folders, `index.md`, `log.md`), a config file (`openkos.yaml`) that ships with a working local-model default (via Ollama), and an `AGENTS.md` operating manual that tells any AI agent how to work with it. Picking a different model is a one-line edit to `openkos.yaml`; an interactive model picker during `init` is deferred — see `add-model-selection`. After this, the user never thinks about setup again.

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
openkos ingest ./call-with-maria-2026-07-14.txt
```

- **By path.** `ingest <path>` copies the source into `raw/` for the user — they never have to organize folders by hand. Sources keep their own names and extensions, markdown included, and the compiled knowledge lands in `bundle/`.
- **One at a time.** **In MVP 1**, `ingest` takes a single `<path>` argument; each source is captured and reviewed on its own. **Later MVPs** add batch/glob ingest (`openkos ingest ./inbox/` or `openkos ingest ./inbox/*.txt`) for users who want throughput.
- **Sensitivity at capture.** **In MVP 1**, sensitivity is not a per-command flag — it comes from `default_sensitivity` in `openkos.yaml` and applies to everything ingested. **Later MVPs** may add a per-source `--sensitivity` flag for one-off overrides.

### Step 2 — Compile

**In MVP 1**, `compile` is the "null compiler": the engine copies the source into `raw/`, embeds its full text verbatim into exactly one `Source` concept (or a binary-fallback note if the content is not text), and updates `index.md` and `log.md`. There is no LLM extraction, no drafted summary, and no separate person/decision/topic pages — one source in, one honest `Source` concept out.

**Later MVPs** grow this into real compilation: the engine reads the source with the local model, drafts a summary concept, creates or updates related concept pages, records provenance back to the source, applies freshness stamps, and reconciles corrections against what is already in the base — for example, recognizing that today's call corrects the reading of *apatheia* recorded nine days ago and revising that page instead of writing a new one.

### Step 3 — Review and confirm (default) — or hand it off

**In MVP 1**, "review" is a preview of the exact files touched, followed by a plain yes/no confirm — not an editable panel.

```
$ openkos ingest ./call-with-maria-2026-07-14.txt
openkos ingest: proposed changes:
  + raw/call-with-maria-2026-07-14.txt
  + bundle/sources/call-with-maria-2026-07-14.md
  ~ index.md (new Source entry)
  ~ log.md (new dated entry)
Proceed with these changes? [y/N]:
```

The user accepts or declines; there is no `[e]dit` option to change the content in place. Declining aborts and nothing is written. This confirm step only appears when stdin is a TTY and review is not disabled; otherwise `--auto` (or `review: false` in `openkos.yaml`) is required to write unattended.

**Optional (unattended):** a user who trusts the engine skips the review entirely — they just capture, and the engine does the rest.

```bash
openkos ingest ./call-with-maria-2026-07-14.txt --auto
```

```
openkos ingest: imported 'call-with-maria-2026-07-14.txt' -> raw/call-with-maria-2026-07-14.txt, bundle/sources/call-with-maria-2026-07-14.md (index.md, log.md updated).
```

`--auto` (per command) overrides the default; setting `review: false` in the config makes unattended the standing behavior. Either way the proposed-changes preview and this same success line are printed — review is a preference, not a requirement.

**Later MVPs** grow this into a richer review panel: multiple proposed concepts (summary, person, decision pages) shown together, an `[e]dit` option to revise content before saving, and a *reclassification* notice when a more sensitive source raises the sensitivity of a concept it feeds (the high-water-mark rule) — for example:

```
Proposed changes:
  +  bundle/sources/call-with-maria-2026-07-14.md   (new summary)
  +  bundle/people/maria-salazar.md                 (new)
  ~  bundle/concepts/stoicism.md                    (v1→v2: apatheia corrected;
                                                     sensitivity private → confidential)
  +  bundle/decisions/frame-the-essay-on-the-dichotomy-of-control.md   (new)
  ~  bundle/index.md, bundle/log.md

Apply? [Y]es / [e]dit / [n]o:
```

### Step 4 — Commit

**In MVP 1**, accepted changes are written to disk (`raw/`, the new `Source` concept, `index.md`, `log.md`); committing them to git is a manual, optional step the user takes themselves. **Later MVPs** may make that commit automatic as part of `ingest`. Either way the workspace is a normal git repository, so `git log`/`git diff` always show what changed.

### Step 5 — Use (the value moment)

Later, the user asks a question and gets an answer with citations back to the source:

```bash
openkos query "what does apatheia actually mean?"
```

```
Apatheia is freedom from the pathē — the destructive passions — not the
absence of feeling: the Stoics kept the eupatheiai, the "good feelings"
(sources/call-with-maria-2026-07-14). It is commonly misread as
"indifference to emotion" by analogy with the English cognate apathy.

Citations:
  → sources/call-with-maria-2026-07-14 (call with maria 2026 07 14)
```

**In MVP 1**, `query` cites the `Source` concept it drew on directly (`bundle/sources/<slug>.md`), which itself embeds the raw text and points back to `raw/<name>`. The citation chain still lets the user ask *how do I know this?* and get a file path, even though there is no separate extracted topic page yet. **Later MVPs**, once `compile` produces topic pages (like a `concepts/stoicism.md`), the citation chain will run from the answer through that topic page, back through the Source concept, to the immutable original — and the answer above will reflect the *corrected* understanding the base learned from later sources, not just the first one it saw.

A good answer can be filed back as a new concept, so exploration compounds — feeding the loop again.

## Secondary journeys

- **Ask:** `openkos query "…"` — cited answers assembled from the bundle.
- **Keep it honest:** `openkos lint` — in MVP 1, flags stale `as of` stamps (older than the configured freshness window) and orphan pages (concepts no markdown link reaches from `index.md` or another concept); volatility-aware and contradiction checks arrive in MVP 2. The lint is OpenKOS's opinion about knowledge health, not a verdict on OKF validity — a bundle it complains about is still a perfectly conformant bundle.
- **Orient:** `openkos status` — what the base contains, recent activity, anything needing attention.
- **Browse:** open the folder in any editor — the bundle is just markdown.

## Editing by hand

The bundle is your files, so you can edit any concept document directly — in Obsidian, VS Code, or any editor — without asking the engine. This is not a workaround; it is the point. The canonical files are the source of truth, and the engine's indexes are derived from them.

**In MVP 1**, a hand edit simply stays as you left it — the engine does not scan for or automatically reconcile out-of-band edits. `openkos status`/`openkos lint` read the bundle fresh each time, so they always reflect your latest edit; if the edit introduced a problem (invalid frontmatter, a broken link, a stale `as of` stamp), `lint` surfaces it. **Later MVPs** may add automatic reconciliation: detecting a changed file by content hash, re-indexing what's affected, and logging the external edit in `log.md`.

**Later MVPs**: when you later ingest a source that touches a concept you edited, the engine would read the current file first and build on your version, with review mode showing the merged change before saving — so the compiler adds to your edit rather than overwriting it. **In MVP 1**, `ingest` produces one `Source` concept per source and does not merge into concepts you have hand-edited. Git keeps every version either way, so any change stays diffable and reversible if you use it.

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
| Before saving | Shows proposed changes, asks to confirm | Saves directly to disk (git commit stays manual/optional, same as interactive) |
| Best for | Staying involved, important sources | Bulk capture, trusted flows |
| Safety net | Review, plus git history | git history (inspect / revert anytime) |

## MVP 1 scope

MVP 1's intended use case is **text**: `ingest` is built and tested for plain-text sources (`.txt`, `.md`), and a transcript that is already text fits perfectly. `ingest` does not enforce a file-extension allowlist — any readable file is accepted and copied into `raw/`; text content is embedded verbatim into the `Source` concept, and non-text content gets a binary-fallback note in the concept body instead. Dedicated format producers for PDF, web, audio, and images arrive in later MVPs and extend this same journey without changing its shape.

## Deferred / open questions

To revisit as the product matures: batch review granularity (confirm per source vs per batch), an explicit `undo` beyond `git revert`, and how a "watched inbox" folder would fit for users who prefer drop-in capture.
