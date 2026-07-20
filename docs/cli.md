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

**Prerequisites:** Python 3.13+, and [Ollama](https://ollama.com) with a model pulled (for example `ollama pull qwen3:8b`, `openkos init`'s packaged default). No accounts or API keys.

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

Creates a new workspace in the current directory: `raw/` for immutable sources, `bundle/` for the compiled OKF bundle (`index.md` and `log.md`; concept folders are not pre-created, `ingest` adds them on first write), a config file (`openkos.yaml`), and an `AGENTS.md` operating manual. Run once per workspace. On success, `init` unconditionally prints a next-step hint pointing at `openkos ingest <path>` — there is no TTY/quiet gate on it.

The model written into `openkos.yaml` resolves in this order: the `--model <tag>` flag, if given; otherwise, when stdin is a TTY, an interactive prompt offering the default `qwen3:8b`; otherwise the default `qwen3:8b` is used silently, no prompt shown. A blank value, or one containing whitespace, a quote (`'`/`"`), or `#`, refuses (exit 1) before anything is written; a colon is allowed, since Ollama `name:tag` tags (including the default) contain one.

| Flag | Meaning |
| --- | --- |
| `--model <tag>` | Ollama model tag to write into `openkos.yaml`. Skips the prompt even on a TTY. Defaults to `qwen3:8b`. |

### `openkos ingest <path>`

Copies the source at `<path>` into `raw/` (immutable, as `raw/<name>` — only the basename is used, so directory components in `<path>`, including traversal segments, are always stripped), generates exactly **one** OKF Source concept in `bundle/sources/<slug>.md`, and attempts LLM-driven extraction of a **bounded list** of derived objects from that source's text — zero up to a hard cap of **5** (`_MAX_OBJECTS_PER_SOURCE`), each written as its own document under its type's folder. When the source decodes as UTF-8 text, its verbatim content is embedded in the Source's body under a `## Source content` heading — making it queryable via `openkos query` through the same generic body-indexing `query` already uses for every other concept. A source that is not valid UTF-8 text (binary or otherwise undecodable) still copies to `raw/`, but its content cannot be embedded as text: the body instead carries an honest fallback note, with no false claim of embedded content. A zero-length source renders a distinct "the source file is empty" note. In every case, the Source's `description` states plainly whether the content was embedded or could not be embedded. Provenance is recorded OKF-natively as each document's `provenance:` frontmatter field, with no separate provenance store. `index.md` and `log.md` are updated to reflect every new entry.

Sources are stored under their own names and extensions — `notes.md` lands as `raw/notes.md` — because `raw/` sits beside the OKF bundle rather than inside it. A markdown source therefore needs no special handling and still renders as markdown in any editor.

#### Extraction: zero to five derived objects, or a graceful degrade

Using the model configured in `openkos.yaml`, `ingest` prompts the model to propose the distinct derived objects the source is genuinely about — zero, one, or several — each classified as one of the classifiable types: `Concept`, `Entity`, `Place`, `Event`, `Procedure`, `Decision`, `Project`, `Person`, or `Organization`. The prompt is deliberately anti-enumeration — it asks for the objects the source is *about*, preferring fewer, richer objects, not every named entity mentioned in passing (a meeting transcript is about the meeting and any decisions reached, not one `Person` stub per attendee). `Entity` is used only as a fallback when no more specific type fits; every other classifiable type is preferred over `Entity` whenever the source content clearly matches that type's definition. `Source` remains the only in-registry type that is never a classification target.

`Decision` classification is scoped to a single-source, self-narrating decision — a source that itself narrates a choice made, with rationale, alternatives considered, and current status. There is no cross-document synthesis step in this slice, so a decision whose evidence is inferred from patterns spread across several sources (the KOM's canonical multi-source case) is not reproduced here; that synthesis is deliberate future work, not an oversight.

Each surviving, validated candidate writes its OWN document alongside the Source, under the type's own bundle subdirectory (e.g. `bundle/concepts/<slug>.md` for a `Concept`, `bundle/events/<slug>.md` for an `Event`, `bundle/procedures/<slug>.md` for a `Procedure`). Every such document's `provenance` points back at the Source, and its `sensitivity` is inherited verbatim from the Source's own `sensitivity`. Extraction always runs, even under `--auto` or `review: false` — those flags only skip the confirmation PROMPT, never the extraction attempt itself.

The list is bounded and deduplicated in Phase A, before any write. A **hard cap of 5** (`_MAX_OBJECTS_PER_SOURCE`) truncates a pathological reply to the first five validated objects in reply order — a safety ceiling, not a target; the anti-enumeration prompt is the real lever. Each candidate is then staged independently, and any single one can be dropped without affecting the rest (never the whole batch), each drop printing a short note to stderr: an **empty slug** (a title made only of characters the slugifier strips) skips just that candidate; an **in-batch slug collision** — two candidates in the SAME reply that slugify identically — keeps the first and drops the later one; a slug that **already exists on disk** is skipped create-only (see re-ingest below); and a candidate whose fields fail the stricter single-line concept-build gate is skipped. A slug is reserved only once its candidate survives every check.

Extraction degrades to Source-only — the exact MVP-1 result, nothing more — in every one of these cases, none of which fail the command (`ingest` still exits `0` and writes the Source concept normally):

- the source has no decodable text to extract from (binary or empty);
- the model declines to extract anything, or its reply fails validation (not parseable structured output, a `type` outside `{Concept, Entity, Place, Event, Procedure, Decision, Project, Person, Organization}`, or a missing/empty `title`/`description`);
- the local Ollama server is unreachable, times out, or errors.

Each degrade prints a short, distinguishing note to stderr — e.g. `source has no extractable text; keeping the Source only` for a binary/empty source, `no concept extracted from this source; keeping the Source only` for a decline/invalid reply, or `concept extraction skipped -- <reason>; keeping the Source only` for an LLM-availability failure — so the miss is always visible without interrupting the run.

Each derived document is **create-only**, like the Source: on a re-ingest, any candidate whose slug already exists on disk (e.g. `bundle/concepts/<slug>.md`, or any of the nine type folders) is left completely untouched — no overwrite, no re-typing, no merge — the same way `raw/<name>` is never rewritten. This preserves any hand edits. (Extraction itself still re-runs on every re-ingest; only the reconciliation of each candidate against what is already on disk is create-only — see below.)

Re-ingest reconciles **per slug**, not all-or-nothing. Extraction re-runs on every re-ingest — the LLM is called again — and each proposed candidate is reconciled against what is already on disk: a candidate whose slug already exists is skipped create-only (the existing file left byte-untouched), while a genuinely NEW slug IS inserted. So a re-ingest can add an object it did not produce the first time (e.g. the LLM was unreachable on the first attempt) without disturbing what already landed. This replaces the earlier provenance-keyed all-or-nothing gate — which skipped extraction entirely for a re-ingest if any existing derived object already cited the source — with the finer per-slug create-only check. The accepted cost is that a nondeterministic LLM title can slugify differently across re-ingests and produce a duplicate object; entity resolution/merge to reconcile that is MVP-2.

Two guards keep a single run honest: the in-batch slug-collision guard (two candidates in one reply that slugify identically — keep the first, drop the later one) and the on-disk `exists()` create-only skip, which also covers the case of two different sources colliding on the same slug.

`ingest` computes the raw copy, the Source concept, every staged derived object (zero or more), and the `index.md`/`log.md` changes in memory first, shows a preview of the proposed changes — listing the Source and every staged derived object — and only writes after confirmation. When `raw/<name>` already exists, the incoming source's bytes are compared against it before any write: if the bytes are **byte-identical**, `ingest` treats this as an idempotent re-ingest — `raw/<name>` is reused untouched (never re-copied or rewritten) and the Source concept plus `index.md`/`log.md` are regenerated, exiting `0`, regardless of whether the concept already exists. This closes the "forget, then re-ingest" trap: after `openkos forget`, re-ingesting the same source no longer requires deleting `raw/<name>` by hand. Extraction is still attempted on every re-ingest, independently of the Source's own regenerate/fresh status, and reconciled per slug — it can add a NEW derived object it did not produce the first time (e.g. the LLM was unreachable on the first attempt) while leaving any already-existing slug untouched. If the bytes **differ**, `ingest` still refuses (raw sources are immutable) with a message that distinguishes "differs" from the identical case. A source whose raw copy is absent but whose concept (`bundle/sources/<slug>.md`) already exists is refused as an inconsistent workspace state.

Writes are **not transactional**: each individual write is create-only or atomic (never half-written), and content is always written before the catalog (the raw copy, the Source concept, and any derived document all land before `index.md`/`log.md`), so the catalog never references a file that does not exist — but there is no rollback across the sequence. A failure partway through a write can leave the workspace holding a partial result, for example a raw file or concept document not yet reflected in `index.md`/`log.md`. Because the OKF bundle is version-controlled, recovery is `git status` to see the partial result and `git checkout`/`git clean` to restore it — not a manual unlink. This mirrors `init`'s own no-cleanup-path position.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. Extraction still runs either way — only the prompt is skipped. |

`review: true` in config plus a non-TTY stdin (and no `--auto`) refuses to write rather than defaulting silently — re-run with `--auto` for unattended use.

**Not in this slice / planned:** a per-workspace configurable cap (the cap is fixed at 5 for now), cross-document synthesis (e.g. a `Decision` inferred from patterns spread across several sources), entity resolution/merge/reclassification on re-ingest, a typed relationship graph, `--sensitivity <level>` (the generated Source's `sensitivity` always equals config's `default_sensitivity`, currently no per-invocation override), and `--batch` (folder/glob ingestion — one source per invocation only, for now). Both flags are documented here for forward reference but are not implemented yet.

### `openkos query "<question>"`

**Read-only.** Answers a natural-language question from the compiled bundle, with citations back to the concepts and their sources. It shares the same shape as `status`/`lint`: no writes, no confirmation, no `--auto`. Requires a local Ollama server running the model configured in `openkos.yaml` (see `openkos init`'s `--model`) — `query` never calls Ollama outside a workspace.

Refuses (exit 1) outside an initialized workspace, using the same shared workspace check `ingest`/`status`/`lint` use, before any LLM or index work happens. Retrieval is lexical only (FTS5, inherited from the `retrieval.answer` seam) — there is no semantic/vector search in MVP 1 (that lands later; see "Not in MVP 1").

| Flag | Meaning |
| --- | --- |
| `--limit <n>` | Max concepts to retrieve as context. Defaults to `5`. |

Output is answer-first and banner-free: the answer text, then (only when at least one citation exists) a blank line, `Citations:`, and one `  → <concept_id> (<title>)` line per citation, in the same order the answer cited them. On every completed run — successful answer or no-match — a one-line `retrieval: <n> FTS hit(s) → LLM invoked|skipped → <m> source(s) cited` summary prints to **stderr**, so a silent short-circuit (e.g. zero hits, so the LLM never ran) is always visible even though stdout stays pipe-clean. When the bundle build skipped any unreadable/unparseable files, an `index:` skip-notice block follows the summary on stderr, worded as a whole-bundle build diagnostic — never implying the skipped files were candidates for the current question.

When nothing in the bundle matches, `query` prints a cause-specific stdout message instead of the answer, and still exits `0` — a valid "no answer found" response is not an error: zero FTS hits states nothing matched and suggests trying different wording or `openkos status`; hits found but all unreadable points at possible bundle corruption and suggests `openkos lint`; an empty or whitespace-only question prompts the user to provide one. A malformed or unreadable `openkos.yaml` (caught the same way `lint` handles an unreadable workspace), a failure to reach Ollama, or a missing/unusable FTS5 index is caught and reported on stderr (exit 1), never a raw traceback — an unreachable Ollama and a not-installed configured model print actionable guidance (`ollama serve` / `ollama pull <model>`).

A good answer can be filed back as a new concept (the two-output rule) — that re-filing step is not automated in this slice.

### `openkos lint`

**Read-only.** Health-checks the bundle for two freshness signals, mirroring `status`'s Phase-A-only shape: no writes, no confirmation, no `--auto`. In MVP 1 (freshness v0) the checks are deliberately mechanical:

- **Stale stamps** — flags any inline `(as of YYYY-MM-DD)` stamp in a concept body older than the configured `freshness_window` (default `7d`). The scan reads only inline body text, never the `freshness` field, so a `freshness: snapshot` Source produced by `ingest` (no `as of` stamp by design) never produces a stale-stamp finding. MVP 1 performs no volatility classification; volatility-aware windows (per-type, LLM-suggested) arrive in **MVP 2**.
- **Orphan pages** — flags any concept or Source file not referenced by a markdown link from `index.md` or from another concept's body. This is a flat link scan, no dependency graph (graph-based analysis is **MVP 2**), and treats every doc type uniformly — a Source is orphan-able exactly like a concept.

Refuses (exit 1) outside an initialized workspace, using the same shared workspace check `ingest`/`status` use, and also on the rare case where `bundle/index.md` exists but cannot be read. Both are the ONLY non-zero exit paths: `lint` is **not a CI gate** in MVP 1 — a bundle with findings, or a clean bundle, both exit `0`. An invalid or out-of-range `freshness_window` in `openkos.yaml` never crashes `lint`; it degrades to the packaged default (`7d`) and prints a one-line fallback notice instead. Findings are flat warning-level (no error/warning tiers) and rendered as plain text; no `--json` or other structured output mode is offered, and no file under the workspace is ever created, modified, or deleted.

**Lint is not a conformance checker.** It reports OpenKOS's opinion about knowledge *health*, not OKF's verdict about *validity*. OKF explicitly tolerates broken links and missing index entries (§5.3, §9), so a bundle can fail every check here and still be perfectly conformant. Conformance is verified separately, against the three rules of §9.

### `openkos status`

**Read-only.** Reports what the bundle currently contains, in three sections: **Bundle contents** (source/concept counts from a fresh scan of `bundle/**/*.md`, never from `index.md` alone, so it stays accurate even after an interrupted `ingest`), **Recent activity** (the most recent 5 entries from `log.md`, newest-first), and **Needs attention** (OKF §9 conformance findings — unparseable frontmatter, missing/empty `type` — reused from the same check `ingest`'s generated concepts must pass). It never writes, modifies, or deletes any bundle file.

Refuses (exit 1) outside an initialized workspace, using the same shared workspace check `ingest` uses. A malformed or unreadable `log.md` degrades "Recent activity" to a notice rather than failing the whole command; counts and findings still come from the disk scan. Findings are informational only — their presence never causes a non-zero exit.

**Not in this slice:** `--json` or any other structured output mode; a non-zero exit for findings or CI-gate behavior. Freshness and orphan-link checks are `lint`'s job, not `status`'s.

### `openkos forget <concept-id>`

Removes knowledge. In **MVP 1** this is deliberately one thing: **a simple delete.** It removes the concept document, drops its entry from `index.md`, and records the removal as a dated line in `log.md`. The target is named by its concept ID — the path with `.md` removed, which is what OKF already defines identity to be (`openkos forget people/maria-salazar`).

`forget` is the mirror-image of `ingest`, sharing the same Phase A (validate + preview) / confirm gate / Phase B (write) shape. Index removal is **generic across every section** — Sources, Concepts, People, Decisions — not just Sources: whichever section's bullet links to the concept ID is the one dropped. A concept ID with no matching `index.md` entry is not an error; the file is still deleted. `forget` computes the proposed changes in memory and shows a preview (`~ index.md`, `~ log.md`, `- bundle/<concept-id>.md`) before writing, using the same confirm-gate precedence as `ingest` (`--auto` > config `review: false` > TTY prompt > non-TTY refusal).

Writes are, like `ingest`'s, **not transactional** — but ordered in reverse: `index.md` and `log.md` are updated FIRST, and the concept file is deleted LAST, so the catalog never references a file that no longer exists. A failure partway through (for example, the file delete itself failing) can leave the concept file present as a benign, git-recoverable orphan while the catalog has already moved on — never the other way around.

Undo is **plain git** (`git revert`, `git checkout <file>`) — there is no wrapper command for it in MVP 1. Every change is already a commit, so the safety net exists without new surface.

MVP 1 does **not** check inbound references before deleting. Removing a concept others link to leaves those inbound links dangling. MVP 1 `lint` does **not** detect this — its orphan check flags concepts that nothing links *to*, not links whose *target* is missing — so a dangling inbound link is neither reported nor rewritten in this slice. OKF tolerates broken links by design (§5.3), so this is a quality signal, not corruption. Broken-link detection, archiving (`status: deprecated`), tombstones in `log.md`, the reference-aware scope/depth flow, and the privacy **purge** (git-history rewrite + index cleanup) all arrive in **MVP 2**, alongside the rest of the lifecycle.

You can also just delete the file by hand — the bundle is your files. `forget` is the ergonomic version that cleans up the index and log in one step.

### `openkos doctor`

**Read-only.** A fixed environment health scan: five checks against the local workspace and the local Ollama server, each printed as one `[PASS]`, `[FAIL]`, or `[SKIP]` line. Every `[FAIL]` line is immediately followed by an indented `  -> <fix command>` line naming the user's own next command (`ollama serve`, `ollama pull <model>`, `openkos init`) — `doctor` never runs these commands itself.

Unlike `status`/`lint`/`query`, `doctor` never stops at the first failure: it runs and prints **all** applicable checks, then exits once. The checks, in order:

1. **Workspace initialized** — informational.
2. **Config valid** — critical, workspace-only (`[SKIP]` outside a workspace).
3. **Ollama reachable** — critical, always runs.
4. **Model `<tag>` installed** — critical, always runs; `[SKIP]` (not `[FAIL]`) when Ollama is unreachable, since the two share one root cause. A configured tag counts as installed if it matches an installed tag exactly, or matches that tag's `<name>:latest` form.
5. **Bundle readable** — informational, workspace-only (`[SKIP]` outside a workspace).

Exit code reflects **critical** failures only: `doctor` exits `1` if config-valid, Ollama-reachable, or model-installed failed, and `0` otherwise — the two informational checks (workspace-initialized, bundle-readable) never affect the exit code on their own.

`doctor` also works **outside an initialized workspace**, as a pure Ollama preflight: the workspace-initialized check reports an informational `[FAIL]` with `openkos init` remediation, config-valid and bundle-readable are skipped as not applicable, and Ollama-reachable/model-installed still run — checked against the packaged default model — and still determine the exit code.

`doctor` never creates, modifies, or deletes any file.

## `openkos.yaml` (workspace config)

Structured settings for the workspace, read by the engine. It lives at the workspace root, beside `raw/` and `bundle/` — not inside the bundle, which holds concept documents and nothing else.

```yaml
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
