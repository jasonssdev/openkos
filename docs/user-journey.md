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

This document describes the experience of using OpenKOS, so that the product is designed around the person, not the pipeline. It centers on the everyday capture-and-query loop and notes where later-MVP (MVP 3) capabilities extend the journey.

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

**In MVP 1:** `compile` embeds the source verbatim into exactly one `Source` concept, then attempts an LLM-driven extraction step that proposes the distinct derived objects the source is genuinely about — zero to as many as the source genuinely supports, a selector judge deciding which candidates survive rather than a small fixed cap, each classified across nine types (`Concept`, `Entity`, `Place`, `Event`, `Procedure`, `Decision`, `Project`, `Person`, `Organization`) — or a graceful degrade back to the Source alone. A meeting-shaped source pays one further model call beyond that general pass, a scoped follow-up asking specifically who took part; Step 2 says why. **Later MVPs** grow `compile` into the richer step this loop depicts: an LLM drafting a full summary, updating related concept pages, and reconciling corrections across the base.

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

Creates the workspace: `raw/` for immutable sources, `bundle/` for the compiled OKF bundle (the concept folders, `index.md`, `log.md`), a config file (`openkos.yaml`) that ships with a working local-model default (via Ollama), and an `AGENTS.md` operating manual that tells any AI agent how to work with it. On a TTY, `init` probes Ollama and offers a numbered picker over the chat models actually installed (`qwen3:8b` listed first and marked recommended); picking a different model later is a one-line edit to `openkos.yaml`. After this, the user never thinks about setup again.

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
- **One file or a batch.** `ingest` takes a `<path>` — a single file, a directory, or a quoted glob (`openkos ingest ./inbox/` or `openkos ingest './inbox/*.txt'`), driving every matched file through the same per-file pipeline with one up-front cost gate for users who want throughput.
- **Sensitivity at capture.** Sensitivity is not a per-command flag — it comes from `default_sensitivity` in `openkos.yaml`, which sets the floor for everything ingested. It need not be flat across types: `type_sensitivity_defaults` raises a type's birth level above that floor. It ships empty — sensitivity is your call — and `Person: 1` is the recommended setting if the workspace holds material about other people, under which a person page compiled from a `private` source is born `confidential` while the concepts beside it stay `private`. **Later MVPs** may add a per-source `--sensitivity` flag for one-off overrides.

### Step 2 — Compile

**In MVP 1**, the engine copies the source into `raw/`, embeds its full text verbatim into exactly one `Source` concept (or a binary-fallback note if the content is not text), and updates `index.md` and `log.md`. It then attempts an LLM-driven extraction step: using the model configured in `openkos.yaml`, it proposes the distinct derived objects the source is genuinely about — zero to as many as the source genuinely supports — each classified across nine types (`Concept`, `Entity`, `Place`, `Event`, `Procedure`, `Decision`, `Project`, `Person`, `Organization`). A source that is meeting-shaped — by its title, or by carrying the recurring speaker turns a transcript cannot hide — pays a second, scoped call that asks only for the people and organizations who took part. A source that names a gathering *anywhere* — in its title, in its speaker turns, or in one of its own markdown headings — additionally has the gathering itself dropped from the extracted objects: a note whose body opens with `## Información de la reunión` describes a meeting, so `Reunión de coordinación` is its framing and not a concept the base should carry ([#903](https://github.com/jasonssdev/openkos/issues/903)). That heading signal deliberately reaches **only** the container drop, not the participant call above or the prompt channel — those two carry a measured recall cost on a false positive, and widening them needs a summarized-note fixture the corpus does not yet have. That call exists because measurement earned it: on meeting sources the general pass reliably proposed no participant at all, so the machinery meant to rescue them had nothing to rescue. Its findings join the same selection pipeline as every other candidate rather than bypassing it. Each surviving candidate writes its own document alongside the Source, with `provenance` pointing back to it and `sensitivity` inherited from it — inherited and *then raised* for a type carrying a birth offset, so a `Person` candidate on a stock workspace lands at `confidential` even when the Source is `private`; a decline, a validation failure, or an unreachable local model all degrade gracefully to the Source alone, with a short note and no failed command. By default the engine runs extraction more than once and hands the union to a selector judge, which decides what is genuinely distinct and worth keeping; a fixed backstop bounds pathological output rather than doing the selecting, and setting `union_judge: false` restores the older single-run path and its hard cap of six. One source in, one `Source` concept plus the surviving derived documents out — so person, decision, place, event, procedure, and project pages all ship now. What is still deferred to later MVPs: a drafted multi-paragraph summary, and relationships created by extraction itself — typed relationships between objects already exist as human-asserted edges (`openkos relate` writes them, `openkos suggest-relations` and `openkos curate` propose candidates); ingest just never invents them automatically.

**Later MVPs** grow this into fuller compilation: a drafted multi-paragraph summary, a typed relationship graph between objects, applied freshness stamps, and reconciliation against what is already in the base — for example, recognizing that today's call corrects the reading of *apatheia* recorded nine days ago and revising that page instead of writing a new one.

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

The example above shows the case where extraction declined (Source-only, MVP-1's baseline result). When extraction succeeds, the preview adds one line per surviving derived object, each under its type's folder (`+ bundle/concepts/<slug>.md`, `+ bundle/people/<slug>.md`, `+ bundle/decisions/<slug>.md`, and so on across the nine type dirs), and the single confirm covers the Source and every proposed document together, still one prompt.

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

Accepted changes are written to disk (`raw/`, the new `Source` concept, any derived documents extraction produced across the nine types, `index.md`, `log.md`) **and committed for the user**: `ingest` auto-commits exactly the paths it wrote, as does every other mutating verb (`query --save` included). This was a manual step in MVP 1; MVP 2 made it automatic, which is what closes the loop on "the human curates, the engine maintains" — the safety net exists without the user having to maintain it. The workspace is still a normal git repository, so `git log`/`git diff` always show what changed, and `git revert` undoes any single step. When git is unavailable or its identity is unset, the write still lands and a stderr `WARNING` says the commit was skipped.

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

On every run, a `retrieval: <n> FTS + <n> dense → <n> fused → LLM invoked|skipped → <n> cited` summary also prints to stderr — separate from the stdout answer above, so scripts piping stdout never see it.

**The prompt is bounded to the model's context window, and says so when that bites.** Until [#882](https://github.com/jasonssdev/openkos/issues/882) the context was assembled from the full body of every retrieved document with no size bound: Ollama does not raise on an oversized prompt, it discards the overflow and answers normally, so an answer could cite documents the model never read — and `--save` filed those citations as permanent provenance. Context is now assembled to fit, and the two ways a document can lose out are reported separately on stderr, each naming the documents and pointing at `context_window` in `openkos.yaml`. An **excerpted** document was clipped with elision markers; it is still cited, and its citation line carries a trailing `[partial]`. An **omitted** document did not fit at all and is **not cited**, because a document the model never saw is not provenance — and its absence may be why an answer reads thin.

`query` cites whichever of the retrieved documents the answer reports actually drawing on — the Source, a derived page, or both, rather than always preferring one over the other. It used to cite everything retrieval matched, which meant the citation count was `--limit` rather than a fact about the answer ([#753](https://github.com/jasonssdev/openkos/issues/753)); a cited `Source` concept (`bundle/sources/<slug>.md`) itself embeds the raw text and points back to `raw/<name>`, so the citation chain lets the user ask *how do I know this?* and get a file path. **MVP 2** made retrieval hybrid — lexical FTS and dense vectors fused by reciprocal rank fusion — so a page can surface on either its wording or its meaning. That dense half reads whole documents only since [#888](https://github.com/jasonssdev/openkos/issues/888): a document's vector used to be the vector of its first chunk, so the second half of every long Source was invisible to semantic search. Vectors are chunk-backed now and a document-level vector averages them, while `query` still collapses chunk hits to one hit per document — so the retrieval unit the citations name did not change, only what it can be found by. MVP 2 also tried a third channel: a PageRank walk over the typed graph, adding into a reserved slot only what those two had missed. Measurement retired it ([#434](https://github.com/jasonssdev/openkos/issues/434)): PageRank ranks by how *central* a page is in the corpus, which is not the same thing as how *relevant* it is to your question, so the reserved slot kept seating the base's most-connected page at the cost of a real hit — once evicting the very document that answered the question. The typed graph itself was not the problem and remains: it is what `openkos contradictions` reads to find pairs worth checking. **Later MVPs** deepen the compile side: the answer above will reflect the *corrected* understanding the base learned from later sources, not just the first one it saw.

A good answer can be filed back as a new concept, so exploration compounds — feeding the loop again.

## Secondary journeys

- **Ask:** `openkos query "…"` — cited answers assembled from the bundle.
- **Keep it honest:** `openkos lint` — flags stale `as of` stamps (older than the configured freshness window) and orphan pages (concepts no markdown link reaches from `index.md` or another concept); volatility-aware windows and contradiction detection shipped in MVP 2 (`openkos contradictions`, `openkos suggest-volatility`). The lint is OpenKOS's opinion about knowledge health, not a verdict on OKF validity — a bundle it complains about is still a perfectly conformant bundle.
- **Orient:** `openkos status` — what the base contains, recent activity, anything needing attention.
- **Check the setup:** `openkos doctor` — an environment health preflight that reports whether the workspace is initialized, `openkos.yaml` is valid, the local Ollama server is reachable, and the configured model is installed, each as a `[PASS]`/`[FAIL]`/`[SKIP]` line with a fix command. It also runs outside a workspace as a pure Ollama preflight.
- **Browse:** open the folder in any editor — the bundle is just markdown.

## Editing by hand

The bundle is your files, so you can edit any concept document directly — in Obsidian, VS Code, or any editor — without asking the engine. This is not a workaround; it is the point. The canonical files are the source of truth, and the engine's indexes are derived from them.

**In MVP 1**, a hand edit simply stays as you left it — the engine does not scan for or automatically reconcile out-of-band edits. `openkos status`/`openkos lint` read the bundle fresh each time, so they always reflect your latest edit; if the edit introduced a problem (invalid frontmatter, a broken link, a stale `as of` stamp), `lint` surfaces it. **Later MVPs** may add automatic reconciliation: detecting a changed file by content hash, re-indexing what's affected, and logging the external edit in `log.md`.

**Later MVPs**: when you later ingest a source that touches a concept you edited, the engine would read the current file first and build on your version, with review mode showing the merged change before saving — so the compiler adds to your edit rather than overwriting it. **In MVP 1**, `ingest` produces one `Source` concept plus the derived documents that survive selection (across the nine types) per source, and does not merge into pages you have hand-edited. Re-ingest re-runs extraction every time and reconciles per slug: a candidate whose slug already exists is skipped create-only, leaving that file exactly as you left it — the same guarantee `raw/` gets — while a genuinely new slug is still inserted. Git keeps every version either way, so any change stays diffable and reversible if you use it.

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

That is the mature shape, and most of it now ships. `forget` is **reference-aware** — it refuses to orphan an inbound link unless you pass `--force` — and `--scope source` cascades a delete to a source plus everything derived from it; each deletion leaves a tombstone in `log.md`, and undo is plain git. Deleting the page is only the visible half of the privacy case, because by the time a user asks for something to be gone, its words can be sitting in three other places the engine wrote them to. So the same `forget` also sweeps the merge-ledger sidecars (a past merge keeps the absorbed object's verbatim bytes, so the entry is dropped and the surviving entries scrubbed of its text), the persisted contradiction findings in `.openkos/findings.db` (which quote claim text out of the bodies they judged), and the live decision records under `bundle/.state/decisions/`. Maria's request in the example is only honored if all four go — the page, and the three copies of it nobody sees. The **purge** path (right-to-be-forgotten) is a distinct verb, `openkos purge`: it rewrites git history, scrubs the catalog and log across all history, and clears the derived indexes, behind a typed-phrase confirmation. What is still deferred to MVP 3 is the single unified interactive scope/depth prompt that would present archive, delete, and purge as one guided panel — today they are separate, explicit commands.

## Two ways to work

| | Interactive (default) | Unattended |
| --- | --- | --- |
| Command | `openkos ingest <path>` | `openkos ingest <path> --auto` (or `review: false`) |
| Before saving | Shows proposed changes, asks to confirm | Saves directly to disk (auto-commits either way, same as interactive) |
| Best for | Staying involved, important sources | Bulk capture, trusted flows |
| Safety net | Review, plus git history | git history (inspect / revert anytime) |

## MVP 1 scope

MVP 1's intended use case is **text**: `ingest` is built and tested for plain-text sources (`.txt`, `.md`), and a transcript that is already text fits perfectly. `ingest` does not enforce a file-extension allowlist — any readable file is accepted and copied into `raw/`; text content is embedded verbatim into the `Source` concept, and non-text content gets a binary-fallback note in the concept body instead. Dedicated format producers for PDF, web, audio, and images arrive in later MVPs and extend this same journey without changing its shape.

## Deferred / open questions

To revisit as the product matures: batch review granularity (confirm per source vs per batch), an explicit `undo` beyond `git revert`, and how a "watched inbox" folder would fit for users who prefer drop-in capture.
