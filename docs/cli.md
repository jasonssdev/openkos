---
type: Reference
title: OpenKOS CLI Reference
description: The authoritative command surface for the OpenKOS command-line interface.
tags:
  - openkos
  - cli
  - reference
  - mvp
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-14T23:00:00Z
sensitivity: public
---

# CLI Reference

This is the single source of truth for the OpenKOS command line. It covers the complete MVP 1 (Compiler) and MVP 2 (Graph and Memory) surface, which ship today (the project is **alpha**). Anything still deferred to MVP 3 is marked as such.

## Conventions

- **Local-first.** Every command runs on your machine and works offline.
- **Color is a layer, not a requirement.** Output uses color to encode meaning, but respects `NO_COLOR` and a `--no-color` flag, and auto-disables when output is not a TTY (e.g. piped to a file). The symbols (`+`, `~`, `✔`, `→`) carry the meaning without color.
- **Config lives in `openkos.yaml`** at the workspace root, beside `raw/` and `bundle/`; the agent operating manual lives in `AGENTS.md`, next to it.
- **git is handled for you.** `init` creates the repository, the `.gitignore`, and the first commit; every mutating verb (including `query --save`, since #331) then auto-commits its own writes with a scoped `git add -- <paths>` (never `-A`). You never have to run a git command to keep the workspace versioned — `git log`/`git diff`/`git revert` remain available for inspection and undo. When git is unavailable or its identity is unset, commands print a stderr `WARNING` and still leave every write on disk; they never fail because of git.
- **Exit codes.** `0` is success; `2` is a usage error (unknown flag, wrong arguments — from the CLI parser); every other failure exits `1` — validation refusals, environment problems, declined confirmations — with one deliberate exception. After its confirm gate and before its first write, every mutating verb re-reads each file it is about to write **or unlink** and refuses with **exit `3`** if any of them changed (or vanished) since the previewed plan was computed from it — a post-confirm **drift refusal**. Exit 3 is the one failure a script may safely retry: nothing was written and nothing was deleted, and a re-run recomputes the plan over the current bundle (unless the refusal itself says otherwise — a vanished target must be restored first, and `unmerge`'s message tells you to copy the conflicting edit somewhere safe instead). No other non-zero exit carries that guarantee. Retry exactly the drift case with e.g. `openkos ingest notes.txt --auto || { [ $? -eq 3 ] && openkos ingest notes.txt --auto; }`.

### Global options

- **`--version`** — prints exactly `openkos {version}` on one line and exits 0, sourced from the installed distribution's metadata (`importlib.metadata`), never a source-code constant. Works outside any workspace and short-circuits before any subcommand runs (e.g. `openkos --version doctor` prints only the version). Staleness is out of scope: if `pyproject.toml` was bumped without re-running `uv sync`/reinstalling, this reports the still-installed version, not the checked-out one. If the distribution metadata cannot be resolved at all — realistically only a raw `sys.path` run with no install step — it degrades to `openkos unknown` and still exits 0, since `unknown` cannot be misread as a released build.

## Install and first run

**Prerequisites:** Python 3.12+; `git`; `git-filter-repo` (only for `purge`); and [Ollama](https://ollama.com) with the chat model (`ollama pull qwen3:8b`, `openkos init`'s packaged default) and the embedding model (`ollama pull bge-m3`, the default used by `reindex`/`query`) pulled. No accounts or API keys.

Install the engine once (from PyPI):

```bash
uv tool install openkos   # or: pipx install openkos — or: pip install openkos
```

Create a bundle per knowledge base. By convention the first workspace lives at the root of your home directory, named `knowledge`:

```bash
mkdir ~/knowledge && cd ~/knowledge
openkos init
```

You install the engine once and run `openkos init` in each knowledge base — like installing git once and having one repository per project. One machine can hold several independent bundles, each with its own `openkos.yaml`, model, and default sensitivity; `~/knowledge` is simply the default worth starting from. Do not run `git init` yourself: `init` does it for you (see below).

## Commands

### `openkos init`

Creates a new workspace in the current directory: `raw/` for immutable sources, `bundle/` for the compiled OKF bundle (`index.md` and `log.md`; concept folders are not pre-created, `ingest` adds them on first write), a config file (`openkos.yaml`), and an `AGENTS.md` operating manual. Run once per workspace. On success, `init` unconditionally prints a next-step hint pointing at `openkos ingest <path>` — there is no TTY/quiet gate on it.

After those artifacts land, `init` sets the workspace up for git — **best-effort and strictly last**, so a git failure can never leave a half-written workspace. It runs `git init` only if the directory is not already inside a git working tree (it never nests a repository inside a parent one), writes a `.gitignore` from the packaged template (which ignores the derived `.openkos/`) unless one already exists, and commits exactly the paths it just created — a scoped `git add -- <paths>`, never `-A`, so unrelated dirty content in a host repository is never swept in — with the message `chore(openkos): initialize workspace`. If git identity is unset it skips the commit with a stderr `WARNING` rather than inventing a bot identity; any other git failure is likewise reported as a non-fatal `WARNING` pointing at `git status`. Neither case changes `init`'s exit code or the workspace-write guarantee. This is what makes `forget`'s undo and `purge`'s history rewrite work without asking the user to run git themselves.

The model written into `openkos.yaml` resolves in this order: the `--model <tag>` flag, if given; otherwise, when stdin is a TTY, an interactive picker that probes Ollama and lists the installed chat models as a numbered menu (embedding models filtered out, `qwen3:8b` listed first and marked `(recommended)`, Enter takes it; an out-of-range or non-numeric answer reprompts, and after three failed attempts it falls back to the default) — degrading to a plain `Model [qwen3:8b]:` text prompt if the probe fails or no chat model is installed; otherwise the default `qwen3:8b` is used silently, no prompt shown. A blank value, or one containing whitespace, a quote (`'`/`"`), or `#`, refuses (exit 1) before anything is written; a colon is allowed, since Ollama `name:tag` tags (including the default) contain one.

After the workspace is written, `init` runs one non-fatal, bounded-timeout Ollama preflight (reusing the same short timeout as `doctor`): if Ollama is unreachable, the resolved model is not installed, or the probe itself fails unexpectedly, a one-line note pointing at `openkos doctor` is printed to stderr. This is purely observational — it never pulls a model, never starts a server, and never changes `init`'s exit code (always `0` on success); a clean, ready Ollama produces no extra output.

| Flag | Meaning |
| --- | --- |
| `--model <tag>` | Ollama model tag to write into `openkos.yaml`. Skips the prompt even on a TTY. Defaults to `qwen3:8b`. |

### `openkos ingest <path>`

Copies the source at `<path>` into `raw/` (immutable, as `raw/<name>` — only the basename is used, so directory components in `<path>`, including traversal segments, are always stripped), generates exactly **one** OKF Source concept in `bundle/sources/<slug>.md`, and attempts LLM-driven extraction of a **bounded list** of derived objects from that source's text — zero up to a hard cap of **5** (`_MAX_OBJECTS_PER_SOURCE`), each written as its own document under its type's folder. When the source decodes as UTF-8 text, its verbatim content is embedded in the Source's body under a `## Source content` heading — making it queryable via `openkos query` through the same generic body-indexing `query` already uses for every other concept. A source that is not valid UTF-8 text (binary or otherwise undecodable) still copies to `raw/`, but its content cannot be embedded as text: the body instead carries an honest fallback note, with no false claim of embedded content. A zero-length source renders a distinct "the source file is empty" note. In every case, the Source's `description` states plainly whether the content was embedded or could not be embedded. Provenance is recorded OKF-natively as each document's `provenance:` frontmatter field, with no separate provenance store. `index.md` and `log.md` are updated to reflect every new entry.

Sources are stored under their own names and extensions — `notes.md` lands as `raw/notes.md` — because `raw/` sits beside the OKF bundle rather than inside it. A markdown source therefore needs no special handling and still renders as markdown in any editor. `<path>` may also be a directory or a quoted glob, ingesting every matched file in one invocation — see "Batch: a directory or glob in one invocation" below.

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

After the ingest is committed, `ingest` also refreshes the dense vector index for the just-written documents, so candidate relations are available in the same run — fail-open: any embedding failure degrades to one stderr notice (`embeddings not updated -- ...`) with an unchanged exit code. Before that embed, when the configured embedding host is not literally this machine — `OLLAMA_HOST` set to anything other than a loopback literal (`localhost` in any case with an optional trailing dot, a `127.0.0.0/8` address, or `::1`; no DNS lookup is ever made) — a one-line stderr advisory names the host (credentials always redacted, even for an unparseable value) and says document text and embedding vectors will leave this machine. Advisory only: it never blocks the ingest and never changes the exit code, and an unset `OLLAMA_HOST` (Ollama's own local default) stays silent. A directory or glob batch prints this advisory once per invocation, up front before the first file — the same once-per-run consolidation as the batch cost gate — never once per file. `reindex` and `query` print the same advisory under their own prefixes before they embed — deliberately identical wording, since it is one cause, not several (the distinct-causes rule from #234 cuts both ways).

Each derived document is **create-only**, like the Source: on a re-ingest, any candidate whose slug already exists on disk (e.g. `bundle/concepts/<slug>.md`, or any of the nine type folders) is left completely untouched — no overwrite, no re-typing, no merge — the same way `raw/<name>` is never rewritten. This preserves any hand edits. (Extraction itself still re-runs on every re-ingest; only the reconciliation of each candidate against what is already on disk is create-only — see below.)

Re-ingest reconciles **per slug**, not all-or-nothing. Extraction re-runs on every re-ingest — the LLM is called again — and each proposed candidate is reconciled against what is already on disk: a candidate whose slug already exists is skipped create-only (the existing file left byte-untouched), while a genuinely NEW slug IS inserted. So a re-ingest can add an object it did not produce the first time (e.g. the LLM was unreachable on the first attempt) without disturbing what already landed. This replaces the earlier provenance-keyed all-or-nothing gate — which skipped extraction entirely for a re-ingest if any existing derived object already cited the source — with the finer per-slug create-only check. The accepted cost is that a nondeterministic LLM title can slugify differently across re-ingests and produce a duplicate object; entity resolution/merge to reconcile that is MVP-2.

Two guards keep a single run honest: the in-batch slug-collision guard (two candidates in one reply that slugify identically — keep the first, drop the later one) and the on-disk `exists()` create-only skip, which also covers the case of two different sources colliding on the same slug.

`ingest` computes the raw copy, the Source concept, every staged derived object (zero or more), and the `index.md`/`log.md` changes in memory first, shows a preview of the proposed changes — listing the Source and every staged derived object — and only writes after confirmation. When `raw/<name>` already exists, the incoming source's bytes are compared against it before any write: if the bytes are **byte-identical**, `ingest` treats this as an idempotent re-ingest — `raw/<name>` is reused untouched (never re-copied or rewritten) and the Source concept plus `index.md`/`log.md` are regenerated, exiting `0`, regardless of whether the concept already exists. This closes the "forget, then re-ingest" trap: after `openkos forget`, re-ingesting the same source no longer requires deleting `raw/<name>` by hand. Extraction is still attempted on every re-ingest, independently of the Source's own regenerate/fresh status, and reconciled per slug — it can add a NEW derived object it did not produce the first time (e.g. the LLM was unreachable on the first attempt) while leaving any already-existing slug untouched. If the bytes **differ**, `ingest` still refuses (raw sources are immutable) with a message that distinguishes "differs" from the identical case. A source whose raw copy is absent but whose concept (`bundle/sources/<slug>.md`) already exists is refused as an inconsistent workspace state.

Writes are **not transactional**: each individual write is create-only or atomic (never half-written), and content is always written before the catalog (the raw copy, the Source concept, and any derived document all land before `index.md`/`log.md`), so the catalog never references a file that does not exist — but there is no rollback across the sequence. A failure partway through a write can leave the workspace holding a partial result, for example a raw file or concept document not yet reflected in `index.md`/`log.md`. Because the OKF bundle is version-controlled, recovery is `git status` to see the partial result and `git checkout`/`git clean` to restore it — not a manual unlink. This mirrors `init`'s own no-cleanup-path position.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. Extraction still runs either way — only the prompt is skipped. |
| `--include-confidential` | Bypass the workspace `default_sensitivity` floor gate on concept extraction. By default, when the floor is `confidential` (or absent/blank), `ingest` skips extraction entirely — `llm.chat` is never called — and keeps the Source only. |

`review: true` in config plus a non-TTY stdin (and no `--auto`) refuses to write rather than defaulting silently — re-run with `--auto` for unattended use.

#### Batch: a directory or glob in one invocation

`<path>` may also be a **directory** or a **quoted glob** — each matched file is driven through the exact same single-file pipeline described above, once per file, in one invocation:

```
openkos ingest ./notes/           # every readable file directly inside, non-recursive
openkos ingest './notes/**/*.md'  # explicit glob; recursion only via **
```

A directory matches every readable file **directly** inside it — subdirectories are never walked into (recursion is available only via an explicit `**` glob). A quoted glob arrives as a literal string (detected by its magic characters `*`, `?`, `[`) and is expanded relative to the current directory. Matched files are **sorted by path** — never filesystem order — so `log.md` and the per-file commits are reproducible across machines. A plain existing file path keeps the single-file behavior above, byte-identical. An empty directory or a glob matching nothing refuses with a clear message (exit `1`, nothing written).

Because destination names and slugs derive **only from the basename** (the path-traversal defense above, deliberately unweakened), two matched files sharing a basename — e.g. `notes/setup.md` and `notes/archive/setup.md` — would fight over the same `raw/<name>`. The batch detects this **before any write** and refuses the whole run: exit `1`, every colliding path named, nothing written. Rename one, or ingest them separately.

One up-front **cost gate** replaces the per-file prompts: before any LLM contact, `{n} file(s) -> {n} LLM call(s)` is printed and confirmed **once** — that single batch-level consent covers every file, so the per-file confirmation is suppressed the way `--auto` suppresses it today. `--auto` (or config `review: false`) skips the gate; non-TTY stdin without `--auto` refuses to write, mirroring the single-file convention. On a TTY, per-file `i/N` progress goes to stderr; piped output stays clean.

Each file then runs **independently, in order**, with `--include-confidential` forwarded unchanged per file and the existing per-ingest auto-commit reused as-is — commit granularity is **per file**, so an interrupted run leaves every completed file committed (each its own checkpoint) and a re-run is idempotent for the completed ones. A per-file refusal (e.g. differing bytes under an existing `raw/` copy) skips that file with its reason on stderr and **continues** with the rest; a per-file extraction failure stays non-fatal exactly as in a single-file run (Source-only degrade, stderr note). The run closes with per-file outcome lines plus an aggregate summary — ingested / re-ingested / skipped (with reasons) / extraction-degraded — in that order: outcome lines first, the summary as the batch's last word.

The batch's **exit ladder** mirrors the per-file one (see Conventions): exit `0` when every file succeeded (idempotent re-ingests count as success); exit `3` when **every** skip was a per-file drift refusal (exit 3) — nothing those files would have written was written, so the whole batch inherits the retry-safe guarantee and a script may re-run it exactly as it would a single-file exit 3; exit `1` when **any** skip was a hard refusal — a plain re-run would refuse again, so the batch never advertises retryability it cannot deliver.

**Not in this slice / planned:** a per-workspace configurable cap (the cap is fixed at 5 for now), cross-document synthesis (e.g. a `Decision` inferred from patterns spread across several sources), entity resolution/merge/reclassification on re-ingest, a typed relationship graph, and `--sensitivity <level>` (the generated Source's `sensitivity` always equals config's `default_sensitivity`, currently no per-invocation override). The flag is documented here for forward reference but is not implemented yet.

### `openkos query "<question>"`

**Read-only by default.** Answers a natural-language question from the compiled bundle, with citations back to the concepts and their sources. Without `--save` it shares the same shape as `status`/`lint`: no writes, no confirmation. With `--save` the two-output rule is automated — the cited answer is filed back into the bundle as a new derived concept, so `query` can write, gated by the same confirm/`--auto` precedence the other write verbs use (see below). Requires a local Ollama server running the chat model configured in `openkos.yaml` (see `openkos init`'s `--model`) — `query` never calls Ollama outside a workspace.

Refuses (exit 1) outside an initialized workspace, using the same shared workspace check `ingest`/`status`/`lint` use, before any LLM or index work happens. Retrieval fuses **three** ranked lists, ALL THREE now read from **persisted, read-only on-disk indexes** that `reindex` maintains under `.openkos/` — `vectors.db`, `fts.db`, and `graph.db` (performance-caching, MVP 2 Slice 5): lexical FTS5 hits, dense hits (via the embedding model configured as `embedding_model`), and a second-stage seeded graph pool — a personalized PageRank walk over the persisted node/edge projection, seeded from the top concepts of the initial FTS+dense fusion — all combined by reciprocal rank fusion (RRF) into one ranked concept list, which then drives context assembly. `query` is strictly read-only over all three derived stores — it never creates or writes `.openkos/vectors.db`, `.openkos/fts.db`, or `.openkos/graph.db`; run `openkos reindex` first to populate them. Each of the three retrievers degrades independently: a workspace that has never run `reindex`, or whose store is unavailable/corrupt, falls back to whichever of the remaining lists are healthy rather than failing — an empty or unreachable dense/graph list never blocks an answer, and FTS alone is enough to answer. `query` NEVER recomputes or compares the bundle's manifest hash to make this decision: staleness detection is exclusively `reindex`'s job, so an edit made after the last `reindex` run stays invisible to `query` until the NEXT `reindex` run, mirroring how the dense store already behaved before this slice.

| Flag | Meaning |
| --- | --- |
| `--limit <n>` | Max concepts to retrieve as context. Defaults to `5`. Each retriever is queried with a pool of `max(limit, 10)` before fusion truncates to `limit`. |
| `--include-deprecated` | Include deprecated and superseded concepts in retrieval. Excluded by default from every channel (lexical, dense, graph) — the `retrieval:` stderr summary already reports the POST-filter counts. |
| `--include-confidential` | Include confidential concepts in retrieval. Excluded by default from every channel (lexical, dense, graph) when the LLM backend is **not** verifiably on this machine. Against a local backend the exemption already applies and this flag is unnecessary — see [Sensitivity and the local backend](#sensitivity-and-the-local-backend). |
| `--save` | File the cited answer back into the bundle as a new derived concept (the two-output rule). Opt-in; off by default, keeping `query` read-only. Refuses when the answer cited no concepts (nothing to record provenance from). |
| `--title <text>` | With `--save`, the title of the filed concept. Defaults to the question. Its slug is the new concept's id; a collision with an existing file refuses. |
| `--description <text>` | With `--save`, the description of the filed concept. Defaults to the question. |
| `--type <type>` | With `--save`, the type of the filed concept. Defaults to `Concept`. |
| `--auto` | With `--save`, skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. Has no effect without `--save`. |

Output is answer-first and banner-free: the answer text, then (only when at least one citation exists) a blank line, `Citations:`, and one `  → <concept_id> (<title>)` line per citation, in fused-rank order. On every completed run — successful answer or no-match — a one-line `retrieval: <n> FTS + <n> dense + <n> graph → <n> fused → LLM invoked|skipped → <n> cited` summary prints to **stderr**, so a silent short-circuit (e.g. zero hits from all three retrievers, so the LLM never ran) is always visible even though stdout stays pipe-clean. When any of the three derived indexes is absent or unavailable/corrupt this run, an additional stderr hint recommends running `openkos reindex` to enable full retrieval. When graph retrieval degraded this run specifically (absent/unopenable graph index, no seeds from the initial fusion, or the PageRank step itself failed), a separate stderr note says so — graph retrieval never affects the FTS/dense outcome. When the persisted FTS index (built at the last `reindex` run) skipped any unreadable/unparseable files, an `index:` skip-notice block follows the summary on stderr, worded as a whole-bundle build diagnostic — never implying the skipped files were candidates for the current question. When `OLLAMA_HOST` points the embedding host off this machine, the same one-line stderr advisory `ingest` prints (redacted host; document text and embedding vectors will leave this machine) precedes the answer — advisory only, never a refusal.

When nothing in the bundle matches (zero hits from BOTH retrievers), `query` prints a cause-specific stdout message instead of the answer, and still exits `0` — a valid "no answer found" response is not an error: zero hits states nothing matched and suggests trying different wording or `openkos status`; hits found but all unreadable points at possible bundle corruption and suggests `openkos lint`; an empty or whitespace-only question prompts the user to provide one. A malformed or unreadable `openkos.yaml` (caught the same way `lint` handles an unreadable workspace), a failure to reach Ollama, or a missing/unusable FTS5 index is caught and reported on stderr (exit 1), never a raw traceback — an unreachable Ollama and a not-installed model (chat or embedding, named from the actual failure) print actionable guidance (`ollama serve` / `ollama pull <model>`); an unreachable Ollama also points at `openkos doctor` to diagnose further. `adjudicate` and `suggest-relations` degrade the same way on an unreachable/missing-model Ollama.

A good answer can be filed back as a new concept (the two-output rule): pass `--save` and `query` writes it into the bundle as a new derived concept — with `provenance` pointing at the concepts the answer cited — showing a preview and confirming first (or `--auto` to write unattended). `query` stays read-only unless you ask for `--save`.

### `openkos lint`

**Read-only.** Health-checks the bundle for two freshness signals, mirroring `status`'s Phase-A-only shape: no writes, no confirmation, no `--auto`. In MVP 1 (freshness v0) the checks are deliberately mechanical:

- **Stale stamps** — flags any inline `(as of YYYY-MM-DD)` stamp in a concept body older than the configured `freshness_window` (default `7d`). The scan reads only inline body text, never the `freshness` field, so a `freshness: snapshot` Source produced by `ingest` (no `as of` stamp by design) never produces a stale-stamp finding. MVP 1 performs no volatility classification; volatility-aware windows (per-type, LLM-suggested) arrive in **MVP 2**.
- **Orphan pages** — flags any concept or Source file not referenced by a markdown link from `index.md` or from another concept's body. This is a flat link scan, no dependency graph (graph-based analysis is **MVP 2**), and treats every doc type uniformly — a Source is orphan-able exactly like a concept.
- **Dangling references** — flags each outbound `relations:` target or body markdown link that names a concept id absent from the bundle.
- **Dangling provenance** — flags each `provenance:` entry that resolves to no bundle concept, EXCEPT a doc's own raw `resource` entry: every ingested Source cites its own raw file (e.g. `raw/notes.txt`), which never resolves to a bundle id by design, so without that exclusion every Source in every bundle would be reported on every run (issue #257). The finding names the consequence that earned it a dedicated kind: a dangling entry excludes the doc fail-closed from every Source's provenance closure, so `backfill-sensitivity` will never raise it and `set-sensitivity` cannot cascade to it — this finding is the only surface reporting that gap. Same single-pass `collect_docs` walk as the other checks — no extra bundle walk.
- **Unextracted sources** — flags any Source whose frontmatter `extraction_status` is `failed` (issue #187: a compilation attempt started and errored — a retryable state, distinct from a healthy Source, which carries no `extraction_status` key at all). The other three possible values are never reported here: `no-extractable-text` and `no-concepts-found` are non-actionable outcomes of the input itself, and `blocked-by-sensitivity` is a **deliberate policy outcome** — the source's sensitivity floor blocked it from being sent to the LLM — never debt, and it never appears in any retry prompt. Each finding names the literal retry command built from that Source's own `resource` frontmatter value, for example `openkos ingest raw/notes.txt`; a Source missing its `resource` field falls back to a generic re-ingest hint. This check reuses the same single-pass `collect_docs` walk the other three checks share — no extra bundle walk.

Refuses (exit 1) outside an initialized workspace, using the same shared workspace check `ingest`/`status` use, and also on the rare case where `bundle/index.md` exists but cannot be read. Both are the ONLY non-zero exit paths: `lint` is **not a CI gate** in MVP 1 — a bundle with findings, or a clean bundle, both exit `0`, including a bundle with unextracted-source findings. An invalid or out-of-range `freshness_window` in `openkos.yaml` never crashes `lint`; it degrades to the packaged default (`7d`) and prints a one-line fallback notice instead. Findings are flat warning-level (no error/warning tiers) and rendered as plain text; no `--json` or other structured output mode is offered, and no file under the workspace is ever created, modified, or deleted.

**Lint is not a conformance checker.** It reports OpenKOS's opinion about knowledge *health*, not OKF's verdict about *validity*. OKF explicitly tolerates broken links and missing index entries (§5.3, §9), so a bundle can fail every check here and still be perfectly conformant. Conformance is verified separately, against the three rules of §9.

### `openkos duplicates`

**Read-only.** Reports cross-source CANDIDATE duplicates: same-type concepts that MIGHT be the same real-world entity (for example, "Stoicism" and "Stoic Philosophy" living as two separate documents). Mirrors `status`/`lint`'s shape exactly: no Phase B, no confirm gate, no `--auto`. This is a **report only** — `duplicates` never merges, deletes, or otherwise adjudicates a candidate; adjudication belongs to `openkos adjudicate`, and the actual fusion to an explicit `openkos merge` call (or `adjudicate`'s `--apply`/`--apply-same` modes).

One read-only, whole-bundle pass compares titles only within the same declared OKF `type` (a `Concept` is never compared against an `Entity`, even with an identical title) and proposes two deterministic, stdlib-only confidence tiers: **HIGH** — titles that normalize to an identical key (case-folded, punctuation-stripped, diacritics-removed, whitespace-collapsed); and **LOW** — titles that clear a fixed near-match threshold (`difflib`-based token-subset similarity) without being normalized-identical. Neither tier uses an LLM or embeddings in this slice.

Output is grouped by OKF type, then HIGH before LOW, and renders each group's type, tier, member concept_ids, and the trigger (the shared normalized key for HIGH, the similarity score for LOW). An empty result prints a clear "No candidates found." line instead of an empty section.

| Flag | Meaning |
| --- | --- |
| `--include-deprecated` | Include deprecated and superseded concepts in candidate groups. Excluded by default — `duplicates` shares `adjudicate`'s `find_candidates` call and gets the same flag for consistency. |

Refuses (exit 1) outside an initialized workspace, using the same shared workspace check `status`/`lint` use. Every successful read exits 0, whether or not any candidates are found. No file under the workspace is ever created, modified, or deleted, and no `--json` or other structured output mode is offered.

### `openkos adjudicate`

**Read-only by default.** LLM-adjudicates the candidate groups `duplicates` reports, printing a `SAME`/`DIFFERENT`/`UNCERTAIN` verdict and rationale per group for human review (the parsed confidence is deliberately not rendered — a local model returns a flat, uncalibrated value, so a two-decimal number would imply a precision it does not have). Without an apply flag it never merges, writes, or decides — an accepted `SAME` verdict still needs an explicit `openkos merge` call, or one of the two apply modes below, which run that same merge path for you. Degrades the same way `query` does on an unreachable Ollama server or a missing model, with the same actionable stderr guidance.

| Flag | Meaning |
| --- | --- |
| `--same-only` | Display-only filter: print only groups with a `SAME` verdict. `adjudicate_candidates` still judges every candidate group either way. With `--json`, filters the emitted array the same way. |
| `--include-deprecated` | Include deprecated and superseded concepts in candidate groups. Excluded by default — shares `duplicates`'s `find_candidates` call. |
| `--include-confidential` | Include confidential concepts. Excluded by default when the LLM backend is **not** verifiably on this machine — a confidential member is then dropped from a group before its content is ever read. See [Sensitivity and the local backend](#sensitivity-and-the-local-backend). |
| `--json` | Emit every verdict as a single pretty-printed JSON array on stdout (`member_ids`, `okf_type`, `tier`, `verdict`, `rationale` — no confidence), suppressing all human output. Mutually exclusive with `--apply`/`--apply-same` (exit 2). A degraded run (unreachable Ollama, missing model) still exits 1 on stderr with no JSON. |
| `--apply` | Interactive merge walk over the same adjudication results: each `SAME` two-member group is previewed and prompted `[y/N/skip]`; an accepted pair runs the same prepare/merge path `openkos merge` uses, committed per merge and reversible via `unmerge`. Groups with more than two members are skipped (merge those manually), and each pair's member ids are re-verified just before merging, since an earlier merge in the same run may already have absorbed one. A summary line (applied/skipped counts) always prints. Mutually exclusive with `--json`. |
| `--apply-same` | Guarded batch merge of every eligible `SAME` two-member group: prints one aggregate preview plus a `Total: <n>` line, then requires the operator to type that exact count before anything is written (see `--confirm-count`) — a mismatch aborts with zero writes. Merges then commit sequentially, re-resolving each pair immediately before applying it; a mid-batch failure stops the run, reports how many of the previewed merges were applied, and leaves every prior commit intact and reversible via `unmerge`. Mutually exclusive with `--apply` and `--json`. |
| `--confirm-count <n>` | With `--apply-same`, supplies the exact eligible-merge count non-interactively (unattended/non-TTY use). On a TTY, omitting it prompts interactively instead; on a non-TTY without it, the batch is refused. There is no bypass — the count must match exactly. |

### `openkos contradictions`

**Read-only.** LLM-detects contradictions between already-related concepts (candidate pairs drawn from the bundle's typed relation graph), printing a verdict (`CONTRADICTS`/`CONSISTENT`/`UNCERTAIN`), confidence, rationale, and the cited conflicting claims per pair. By default only high-confidence `CONTRADICTS` verdicts are shown. Degrades the same way `adjudicate`/`query` do on an unreachable Ollama server or a missing model.

| Flag | Meaning |
| --- | --- |
| `--all` | Display-only filter: reveal every verdict regardless of type or confidence. `find_contradictions` still judges every candidate pair either way. |
| `--include-deprecated` | Include deprecated and superseded concepts. Excluded by default — a candidate pair with either endpoint deprecated/superseded is never judged. |
| `--include-confidential` | Include confidential concepts. Excluded by default when the LLM backend is **not** verifiably on this machine — a candidate pair with either endpoint confidential is then never judged. See [Sensitivity and the local backend](#sensitivity-and-the-local-backend). |

### `openkos reconcile <id-a> <id-b>`

Records a human's resolution of a contradiction between two concepts — the write counterpart to `contradictions`, which only reports. **No LLM in the write path**: `<id-a>`, `<id-b>`, and `--winner` are plain concept-id arguments; `reconcile` never invokes contradiction detection. Both ids resolve exactly as `relate`'s do, and must be two distinct existing concepts.

There are two shapes, chosen by `--winner`:

- **Omit `--winner`** — a **symmetric** reconciliation: a `reconciled_with` edge is added to **both** concepts' `relations:`, recording that a human judged them reconciled.
- **`--winner <id>`** (must resolve to `id-a` or `id-b`) — a **directional supersede**: a single `supersedes` edge is written on the winner's document, pointing at its counterpart. A `--winner` that resolves to neither id refuses (exit 1).

Reconciliation is idempotent per pair: re-running the exact same request (same mode, same winner) is a no-op; requesting a **different** resolution for a pair already reconciled (a mode switch, or an opposite `--winner`) refuses rather than silently flipping it. Same Phase A / confirm gate / Phase B shape and `--auto` precedence as `relate`.

| Flag | Meaning |
| --- | --- |
| `--winner <id>` | The concept (must resolve to `id-a` or `id-b`) that supersedes its counterpart — writes a directional `supersedes` edge. Omit for a symmetric `reconciled_with` reconciliation. |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. |

### `openkos suggest-relations`

**Read-only.** LLM-suggests a relation `type` for every untyped body-link edge in the bundle, printing a suggested type and rationale per edge (or `[?]` when the suggestion is invalid) for human review. It never writes — applying a suggestion is still a separate, explicit `openkos relate <source> <type> <target>` call. Degrades the same way `adjudicate`/`query` do.

| Flag | Meaning |
| --- | --- |
| `--include-confidential` | Include confidential concepts. Excluded by default when the LLM backend is **not** verifiably on this machine — an untyped edge with a confidential endpoint is then dropped before `llm.chat` is ever called for it. See [Sensitivity and the local backend](#sensitivity-and-the-local-backend). |

### `openkos relate <source-id> <type> <target-id>`

Writes one deterministic typed edge — `{target: <target-id>, type: <type>}` — into `<source-id>`'s `relations:` frontmatter. **No LLM**: the relation type is supplied by you, not inferred, so this is the explicit write path `suggest-relations` forward-references. Both ids are bundle-relative concept ids (the path minus `.md`), resolved exactly as `forget`/`merge` resolve their targets — an absolute id, a `..` segment, a reserved basename, or a nonexistent file refuses (exit 1) before any read, on **both** ends. The two ids must be distinct. `<type>` is validated: empty or whitespace-only refuses; a type outside the seeded relation vocabulary is accepted with an advisory stderr note (the vocabulary is seeded-but-extensible).

The edge is **idempotent**: an identical `(target, type)` pair already present is left as-is, so a repeated `relate` is a no-op. It shares `forget`/`ingest`/`merge`'s Phase A (validate + preview) / confirm gate / Phase B (write) shape — the preview shows the source file, the edge being added, and the `relations:` count before and after; `log.md` gains a `**Relate**` line. There is **no** `index.md` change: a relation edits an existing catalog entry, it does not add one.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. |

### `openkos suggest-volatility`

**Read-only.** LLM-suggests a volatility `tier` for every concept `type` present in the bundle, printing a suggested tier and rationale per type (or `[?]` when the suggestion is invalid) for human review. It never writes — accepting a suggestion is a separate, explicit `openkos set-volatility <Type> <tier>` call (below), which records the tier in `openkos.yaml`'s `type_tiers:`. Degrades the same way `suggest-relations`/`adjudicate`/`query` do.

| Flag | Meaning |
| --- | --- |
| `--include-confidential` | Include confidential concepts. Excluded by default when the LLM backend is **not** verifiably on this machine — a confidential concept is then dropped from its type's sampled bodies, and a type whose docs are all confidential yields no suggestion at all. See [Sensitivity and the local backend](#sensitivity-and-the-local-backend). |

### `openkos set-volatility <Type> <tier>`

Writes `type_tiers[<Type>]: <tier>` into `openkos.yaml` — the write counterpart to `suggest-volatility`, which only reports. **No LLM in the write path**: the type and tier are supplied by you, not inferred, so this is the explicit write path `suggest-volatility` forward-references. Vocabulary validation runs first, before any read or write: `<tier>` must be one of `static`, `slow`, or `volatile`, and `<Type>` must exact-match, case-sensitive, one of the ten PascalCase registry type names — including `Source`, which `suggest-volatility` can suggest a tier for even though it is never a classification target. Either failure refuses (exit 1) with nothing read and nothing written.

The write is **idempotent** against the parsed config: when `type_tiers:` already maps `<Type>` to `<tier>`, the command is a no-op — a message, exit `0`, no write, no commit. An explicit override equal to the type's registry *default* is not present in the parsed map, so it is NOT treated as idempotent; it still proceeds as a real write. The edit itself is comment-safe text surgery on `openkos.yaml`'s raw text — never a YAML round-trip that would reflow the file — so an existing `type_tiers:` shape the editor cannot safely modify (an inline flow mapping, duplicate headers or entries, a non-mapping value, tab or inconsistent indentation) refuses (exit 1) with the file left byte-identical.

A one-line preview (`<Type>: <old-or-default> -> <new>`) prints before the same confirm gate every other mutating verb shares — `--auto` skips it; otherwise config `review: false` skips it the same way; otherwise an interactive TTY prompts and aborts on decline; otherwise (non-TTY, no `--auto`) the command refuses to write. Declining or refusing leaves `openkos.yaml` untouched. A confirmed write is atomic and commits as `openkos: set-volatility <Type> -> <tier>`, mirroring every other mutating verb's commit-message convention.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. |

### `openkos set-sensitivity <concept-id> <level>`

Writes one existing concept's `sensitivity` frontmatter field. This is the guarded path for the field that decides what reaches the LLM — `confidential` content is held back, fail-closed — which until now could only be changed by hand-editing YAML, where a typo silently degrades the guard. **No LLM in the write path.** Vocabulary validation runs first, before any read or write: `<level>` must exact-match `public`, `private`, or `confidential`. The concept-id is resolved the same way `forget` and `relate` resolve theirs, so an absolute path, a `..` segment, a reserved basename, or a nonexistent concept refuses (exit 1) with nothing read and nothing written.

**This verb changes exactly the one concept you name.** It does not touch siblings, and it does not touch objects extracted from that concept. Note the contrast with `merge` below, which recomputes `sensitivity` as a high-water-mark across two objects: that recompute is a merge-time fold, not a propagation, and no equivalent propagation exists here. Marking a source confidential therefore does not reclassify the concepts extracted from it.

The write is **idempotent** against the raw stored value: when the field already reads exactly `<level>`, the command is a no-op — a message, exit `0`, no write, no commit. The comparison is deliberately exact and unstripped, so a dirty value (missing, blank, whitespace-padded, or an unrecognized string) never short-circuits and always reaches the fail-closed ranking described next.

Lowering a level is permitted, because correcting a wrong default is a legitimate downgrade. It is permitted **through review**, so the friction sits exactly where review is absent: when no confirm prompt will actually run — `--auto`, config `review: false`, or a non-interactive stdin — a lowering additionally requires `--allow-downgrade`, and refuses (exit 1) without it, before any preview is printed and before anything is written. Raising is never gated beyond the standard confirm. A dirty current value ranks fail-closed (missing or blank counts as `private`, anything else unrecognized as `confidential`), so assigning below that floor counts as a lowering and is gated too — otherwise the verb would launder bad frontmatter into a lower classification. See ADR-0008 for why an explicit human assignment may lower a value that `merge`'s automatic recompute may not.

A one-line preview naming the direction in words (`sensitivity: lowering 'confidential' -> public`) prints before the same confirm gate every other mutating verb shares. A confirmed write appends a `**Set-sensitivity**` entry to `log.md`, leaves `index.md` untouched, and commits as `openkos: set-sensitivity <concept-id> -> <level>`.

Like `merge`'s Phase B below, the write is **not transactional as a whole**: the concept file is written first, then `log.md`, then the commit. Each individual file write is atomic, but a failure between them leaves the sensitivity already changed on disk while the log entry and the commit do not yet record it. That is a benign, git-recoverable partial result rather than corruption — `git status` shows it, and re-running the same command completes it — but for a field that gates what reaches the LLM it is worth knowing the audit trail can lag the value by one failed step.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. |
| `--allow-downgrade` | Permit lowering the level on a path where no confirm prompt will run. Has no effect when raising, and none when the prompt does run. |

### `openkos backfill-sensitivity`

Dedicated, raise-only, bundle-wide sweep that closes the sensitivity gap left by bundles or descendants created before Source-to-descendant propagation existed (issue #219). Unlike `set-sensitivity` above, it takes **no concept-id argument**: every `type: Source` concept in the bundle is treated as an independent provenance-closure root in one pass, and each descendant's new value is computed the same way — `okf.combine_sensitivity(existing, source_level)` — staging a write only when that computation is a strict raise. There is no per-Source scoping flag; `set-sensitivity` already covers the single-Source case. There is no `--allow-downgrade` equivalent either: the sweep never lowers anything, by construction. There is no `--dry-run` flag: the preview shown before confirmation, or declining the prompt, already serves as the dry run.

A concept that is a member of no single Source's provenance closure is skipped — never written — even if it cites two or more ids, as long as those ids together span more than one Source's closure. A concept citing two or more ids that all fall inside **one** Source's closure is covered and is raised normally. Skipped concepts surface only through `lint`'s `multi-source-uncovered` finding and `status`'s "needs attention" section, never silently.

Before writing, the command prints one bundle-wide preview listing every staged `(concept_id, current -> new_level)` raise across every Source, then the same confirm-gate precedence every other mutating verb shares: `--auto` skips it; otherwise config `review: false` skips it; otherwise an interactive TTY prompts via `typer.confirm` and aborts on decline; otherwise (non-TTY, no `--auto`) the command refuses to write. When the sweep stages zero raises — an already-clean bundle, or an immediate re-run after a prior successful sweep — it prints an explicit "nothing to backfill" message, writes nothing, creates no commit, and exits 0.

A confirmed write lands every staged descendant raise, then appends exactly **one** dated `**Backfill-sensitivity**` entry to `log.md` summarizing the whole sweep, then issues exactly **one** `_autocommit` covering every changed path. No Source's own frontmatter is ever written as its own closure root; a Source that is a genuine provenance descendant of another Source is raised like any other descendant. Like `set-sensitivity`, there is no cross-file rollback: a mid-sweep write failure leaves the bundle over-classified, never under-classified, and the failure message names every path that already landed before the failure (ADR-0012).

`backfill-sensitivity` deliberately does not run the unresolvable-provenance scan `set-sensitivity` runs: every Source cites its raw ingest `resource`, which never resolves to a bundle id, so a bundle-wide run would emit one WARNING per Source on every invocation, including the no-op path. That signal is delivered by `lint`'s `dangling-provenance` finding instead (issue #257), which excludes each doc's own raw `resource` entry for exactly this reason.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. |

### `openkos merge <survivor-id> <absorbed-id>`

Fuses two distinct concept-ids a human has confirmed are the same real-world entity — the first DESTRUCTIVE entity-resolution write. `survivor-id`'s id survives; `absorbed-id`'s file is removed. This is the verb `duplicates` and `adjudicate` forward-reference: a candidate pair still needs an explicit `merge` to actually be fused — invoked directly, or per accepted pair through `adjudicate`'s `--apply`/`--apply-same` modes, which run this same merge path.

`merge` mirrors `forget`'s Phase A (validate + preview) / confirm gate / Phase B (write) shape, doubled for two objects. Both ids are resolved the same way `forget` resolves its target, and MUST be distinct, existing concepts — a same-id or unknown-id argument refuses (exit 1) before any read. The survivor's body gains the absorbed content by **append** (a delimited `## Merged content (<absorbed-id>)` heading, then the absorbed body) — never an overwrite. Frontmatter conflicts resolve deterministically: a scalar field (`type`/`title`/`description`/`status`/`version`/`resource`) keeps the **survivor's** value; a list field (`tags`, `provenance`) is **unioned**, deduped, order-preserving; `freshness`+`timestamp` are taken together from whichever side has the strictly more recent `timestamp`. `sensitivity` is never copied — it is **recomputed** as the high-water-mark of both sides (`public < private < confidential`; a missing value counts as `private`, an unrecognized/malformed one fails closed to `confidential`). Every one of these conflicts is shown in the Phase A preview before you confirm.

Any OTHER concept file with a markdown link to the absorbed id is rewritten to point at the survivor instead (the anchor, if any, is preserved); a link inside a fenced code block is never touched. Any OTHER concept file whose typed `relations:` targets the absorbed id is retargeted the same way. As a **third pass over that same scan**, any OTHER concept file whose `provenance:` list names the absorbed id is retargeted to the survivor too — this scan is **not gated on the absorbed concept's `type`**: `query --save` can file any cited concept id, Source or not, as another object's `provenance`, so absorbing a non-Source concept can still orphan a third party's provenance. All three scans run over the exact same bundle walk — no extra pass is made for the provenance retarget. A list already naming both the survivor and the absorbed id collapses to a single survivor entry, at whichever position was earlier; every other entry keeps its relative order (retarget-then-dedupe). `index.md` drops the absorbed entry; `log.md` gains a `**Merge**` line.

This retarget is why a later `set-sensitivity <survivor> <higher-level>` correctly raises a third party's sensitivity once its `provenance` used to name the (now removed) absorbed Source: the object stays reachable through `find_provenance_descendants` because its `provenance` field was rewritten to point at the survivor, instead of silently dangling.

The survivor also gains a `merged_from` ledger entry — an ordinary frontmatter field, not a new file type — that captures everything needed to reverse this exact merge later: the absorbed file's full verbatim bytes, the survivor's own full verbatim bytes immediately before this write, `index.md`/`log.md`'s prior contents, every inbound-link rewrite performed, and a whole-file pre-merge snapshot for every third-party file whose `relations:` or `provenance:` was retargeted. This is what makes `unmerge` (below) possible. Merging the same survivor more than once is fine — each merge appends its own entry, oldest-first, so sequential merges reverse in last-in-first-out order.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. |

`review: true` in config plus a non-TTY stdin (and no `--auto`) refuses to write rather than defaulting silently — re-run with `--auto` for unattended use. Declining, or refusing, leaves the bundle completely untouched.

Writes are, like `merge`'s Phase B siblings, **not transactional** as a whole: `index.md`/`log.md` are written first, then every rewritten inbound-link file, then the merged survivor (carrying the ledger) — and only then is the absorbed file removed, **last**. A failure at any point leaves a benign, git-recoverable partial result, never silent corruption; a failure while rewriting inbound links, in particular, leaves no trace at all, so simply re-running the same `merge` command completes it.

### `openkos unmerge <survivor-id> <absorbed-id>`

Reverses a prior `merge`, restoring both concept files to **byte parity** with their pre-merge state — the payoff of the `merged_from` ledger `merge` writes. `unmerge` is two-arg and **LIFO-enforced**: it only ever reverses the most recent, not-yet-reversed merge recorded on the survivor (the ledger's tail entry), and the `absorbed-id` you supply must match that tail entry's absorbed id exactly, or the command refuses with a clean error and writes nothing. Reversing anything other than the most recent merge is unsafe — a later merge's snapshots and link rewrites can nest on top of an earlier one's — so it is not offered.

Phase A previews every reversed inbound link, every restored relation/provenance snapshot, the restored `index.md`/`log.md`, the restored survivor, and the recreated absorbed file — the mirror image of `merge`'s own preview. If a file has since appeared at the absorbed concept's path (bundle drift since the merge), or a previously rewritten link, relation, or provenance entry no longer matches what was recorded, `unmerge` refuses (exit 1) in this **pre-prompt ledger check** — Phase A comparing the disk against what the *merge recorded*, before any prompt — rather than risk overwriting or corrupting drifted content. That is distinct from the post-confirm drift refusal (exit 3, see Conventions), which compares the disk against what *this run previewed* and fires only for an edit landing after the confirm gate; only the exit-3 refusal is the retryable one.

A third-party file can be touched by more than one rewrite kind in the same merge (an inbound link, a `relations:` retarget, and a `provenance:` retarget can all land on the same file). Reversal precedence is **provenance > relations > links**: a file recorded in `provenance_rewrites` restores exclusively from that whole-file snapshot; failing that, a file recorded in `relation_rewrites` restores exclusively from that snapshot; a file in neither reverses via the exact-offset link rule. Each kind's snapshot already restores the whole file, so applying a narrower reversal on top would either corrupt the already-restored bytes or fail closed on a now-absent occurrence.

The confirm gate is identical in precedence to `merge`/`forget`: `--auto` skips it outright; otherwise config `review: false` skips it the same way; otherwise an interactive TTY prompts and aborts on decline; otherwise (non-TTY, no `--auto`) `unmerge` refuses to write and tells you to re-run with `--auto`.

| Flag | Meaning |
| --- | --- |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. |

A full `merge` then `unmerge` round trip restores **every** bundle file to its exact pre-merge bytes, with one deliberate exception: `log.md`. Because `log.md` is an append-only audit trail, `unmerge` restores it to its pre-merge contents and then appends one new `**Unmerge**` line documenting the reversal, rather than silently erasing the fact that a merge (and its undo) ever happened. Every other file — the restored survivor, the recreated absorbed file, `index.md`, and any file whose inbound link was rewritten — matches the pre-merge bundle exactly.

**Limitation:** `unmerge` restores `index.md`/`log.md` to their exact pre-merge snapshot, not a merge of that snapshot with whatever is on disk now — if an `ingest`, `forget`, or unrelated `merge` ran in between, `unmerge` discards those changes. Phase A detects this and prints a warning in the preview before the confirm gate, but does not refuse; round-trip parity assumes a prompt unmerge.

**Rollback failure mode (ADR-0011).** The `merged_from` reversibility ledger reached schema `openkos.merge_ledger/v3` to carry provenance-retarget snapshots; the reader still accepts v1 and v2 entries written before this change, so pre-existing merges keep unmerging exactly. What is **not** purely additive is reverting the *code* itself: a v3 entry already written into a survivor's frontmatter survives a `git revert`, and a reverted, older build only knows schemas up to v2 — both `merge` and `unmerge` on that survivor then refuse with `unsupported merged_from schema version: 'openkos.merge_ledger/v3'`, since the ledger reader fails closed on an unrecognized schema rather than silently misreading it. Only `bundle/merge.py`'s `plan_merge`/`plan_unmerge` decode the ledger, so the blast radius is exactly those two paths for the affected survivor; every other verb is unaffected. Recovery, in order of preference: **before reverting**, run `unmerge` on every pair merged under v3 — this removes the v3 entries and reverses the provenance retarget cleanly; **after reverting**, hand-edit the affected survivor's frontmatter to set `schema: openkos.merge_ledger/v2` and delete the `provenance_rewrites` key so `merge`/`unmerge` work again, but the provenance retarget itself stays applied on disk and must be undone by hand (or via git) using the deleted snapshots as reference.

### `openkos status`

**Read-only.** Reports what the bundle currently contains, in three sections: **Bundle contents** (source/concept counts from a fresh scan of `bundle/**/*.md`, never from `index.md` alone, so it stays accurate even after an interrupted `ingest`), **Recent activity** (the most recent 5 entries from `log.md`, newest-first), and **Needs attention** (OKF §9 conformance findings — unparseable frontmatter, missing/empty `type` — reused from the same check `ingest`'s generated concepts must pass, plus `lint`'s dangling-reference, dangling-provenance, and unextracted-source findings, folded in from the SAME in-memory scan `status` already performs — no extra bundle walk). A `failed` extraction is listed here with the same retry command `lint` computes, for example `openkos ingest raw/notes.txt`; a `blocked-by-sensitivity` Source is deliberately never listed (issue #187: not debt, not a retry candidate). It never writes, modifies, or deletes any bundle file.

Refuses (exit 1) outside an initialized workspace, using the same shared workspace check `ingest` uses. A malformed or unreadable `log.md` degrades "Recent activity" to a notice rather than failing the whole command; counts and findings still come from the disk scan. Findings are informational only — their presence never causes a non-zero exit.

**Not in this slice:** `--json` or any other structured output mode; a non-zero exit for findings or CI-gate behavior. Freshness and orphan-link checks are `lint`'s job, not `status`'s.

### `openkos next`

**Read-only.** Answers one narrower question than `status`: not "what is in this bundle", but "which single command should I run next". It ranks four actionable finding kinds by a fixed priority order and prints exactly one runnable command with a one-line reason — or, when none of them fires, one line pointing at `openkos status` for the full report.

Priority order, highest first: (1) missing or empty vector index — `openkos reindex`; (2) an unextracted source (`extraction_status: failed`) — `openkos ingest <resource>`; (3) a descendant below its Source's sensitivity — `openkos backfill-sensitivity`; (4) a pending exact-title duplicate group — `openkos duplicates`. Evaluation stops at the first tier with a finding — a lower-ranked tier's finding is never mentioned while a higher one exists — so cost is capped by how far it needs to look: stopping at tier 1 costs nothing beyond the cheapest possible check, and the worst case (reaching tier 4, or finding nothing) still costs less than `status`'s own scan.

Findings that name no command at all (a §9 conformance violation, a dangling reference, a source cited by more than one closure) are never "the one action" here — they stay visible only through `status`/`lint`. Because `next` stops looking as soon as one tier fires, it can never prove those commandless findings are absent, so its no-action output never claims the bundle is clean — it always points at `openkos status` instead, with no count of anything left unseen.

A document that could not be read or whose frontmatter would not parse is excluded from the scan entirely, so `next` names every such document by path before the `openkos status` pointer — on the no-action path *and* when a tier fires, since an action derived from a knowingly incomplete document set needs the caveat just as much as no action does. Only what the run actually read is reported: stopping at tier 1 costs no bundle walk, so it collects no skip notices and claims none.

Tier 2's `openkos ingest <resource>` is read out of the finding's own retry text and then corroborated against the Source's `resource` frontmatter. A `resource` carrying a backtick would otherwise close that text's code span early and leave behind a real command naming the *wrong* path; when the two do not match exactly, tier 2 declines and evaluation continues.

A declined finding is still a real failed extraction, so it is named rather than dropped — on every path, whether or not a lower tier goes on to fire. Each declination identifies the document and which of the two repairs it needs (the Source records no `resource` at all, or its `resource` cannot be spelled as a runnable argument and the file needs renaming). It never reprints the `resource` value itself: that is precisely the value the declination established cannot be trusted in generated prose.

Refuses (exit 1) outside an initialized workspace, using the same shared workspace check `status` uses. Every other workspace state, including a freshly initialized, empty bundle, exits 0. No `--json` or other structured output mode is offered, no file under the workspace is ever created, modified, or deleted, and no model backend is ever constructed — the answer is a pure function of files already on disk.

### `openkos curate`

**One dependency-ordered decision session.** Walks the five kinds of pending human judgment — identity, structure, metadata, sensitivity, contradictions — in a single sitting, as five stages in one fixed order: **Preconditions → Identity → Structure → Metadata → Contradictions**. That order is a correctness invariant, not a preference (ADR-0005/ADR-0011): identity must resolve before structure work, because a merge changes which concepts every later stage would even be reasoning about — so each stage re-derives its queue from the bundle *as it is when the session reaches it*, after any merges an earlier stage just committed, never from a stale pre-run snapshot. Nothing is checkpointed to disk: interrupting mid-run loses nothing, and re-invoking `curate` resumes from current bundle state without replaying any already-committed decision. `curate` is the session counterpart to `next`: `next` names the one command to run; `curate` sits you down and works the whole queue. It is not a CI gate — pending work never causes a non-zero exit.

All five stages run fully. Structure calls `suggest-relations`' engine (`suggest_edge_types`) over the bundle's untyped-edge candidates, then writes each accepted suggestion through `relate`'s own write core — the same `[y/N/skip]` walk and drift guard as standalone `relate`, so the two can never drift apart. Metadata calls `suggest-volatility`'s engine (`suggest_volatility`, one model call per distinct concept *type*, never per concept) and writes each accepted tier through `set-volatility`'s own write core; in the same pass it also reports any concept with **no `sensitivity` set at all**, naming `openkos set-sensitivity <id> <level>` — that gap is reported only, never written by `curate`. Contradictions runs last and is strictly read-only: it calls `contradictions`' engine (`find_contradictions`) and prints high-confidence verdicts; it never proposes or performs a write, matching the standalone verb.

Preconditions is the one stage that can halt the entire run: it probes `.openkos/vectors.db` through the same `_open_proximity_or_degrade` seam `suggest-relations` and `contradictions` use, and a missing or empty vector index prints the consequence (candidate edges are starved) plus a pointer to `openkos reindex`, then ends the run with **exit 0** — no later stage runs, since every one of them would be reasoning over a bundle whose retrieval is known-broken. Every *other* stage's decline, empty queue, failure, or not-yet-available skip is scoped to that stage alone: **declining one stage never aborts the rest of the session**.

Every LLM-costing stage states its price before contacting the model — a per-stage **cost gate** printing `{n} {noun}(s) -> {n} LLM call(s)` (e.g. `3 candidate group(s) -> 3 LLM call(s)`), then asking for confirmation. Each gate is individually declinable, and no Ollama connection is even opened until the first accepted gate's stage runs — a session where every gate is declined makes zero model calls and zero connection attempts. `--auto` accepts **cost gates only**: it consents to model *spend*, never to a per-item *write* — the per-merge `[y/N/skip]` prompts are never auto-accepted. Without a TTY: no `--auto` means every LLM-costing stage declines before any model call, with no exception for read-only stages (there is no consent channel at all); with `--auto`, a read-only stage runs and reports, while a write stage declines its per-item walk and instead prints the standalone verb built for unattended use (for Identity: `openkos adjudicate --apply-same --confirm-count <n>`). An unreachable Ollama or missing model marks the failing stage unavailable with the same actionable guidance `adjudicate` prints, and later LLM stages are skipped without a second connection attempt; any other model failure fails only its own stage.

Identity reuses `adjudicate --apply`'s exact merge walk rather than reimplementing it: candidate groups are LLM-adjudicated, then each accepted `SAME` two-member pair gets the same preview, `[y/N/skip]` prompt, and prepare/commit path `openkos merge` uses — committed per merge, before the next pair is even considered, and reversible via `unmerge`. A candidate group with more than two members is never auto-merged: `curate` prints the exact pairwise `openkos merge` commands for it and moves on. The post-confirm drift refusal (exit `3`, see Conventions) applies here exactly as in `merge`: a target edited between the preview and the write refuses with nothing written for that pair — and, unlike a decline, it ends the whole session, since drift proves the workspace is racing and no later stage's plan could be trusted either. Merges already committed earlier in the same session stay intact and reversible.

| Flag | Meaning |
| --- | --- |
| `--auto` | Accept every stage's cost gate without prompting (model spend only — per-item write prompts are never auto-accepted). |
| `--include-confidential` | Include confidential concepts, forwarded to every stage's underlying call. Excluded by default when the LLM backend is **not** verifiably on this machine. See [Sensitivity and the local backend](#sensitivity-and-the-local-backend). |
| `--include-deprecated` | Include deprecated and superseded concepts, forwarded the same way. Excluded by default. |

Exit codes: `0` for any completed or declined run — including every declined gate, empty queue, and the Preconditions halt; `1` on a workspace/config failure or a failed mid-walk write; `2` on a usage error; `3` on the post-confirm drift refusal, the one retryable failure (see Conventions).

### `openkos list [TYPE]`

**Read-only.** Enumerates bundle objects with their id, sensitivity, lifecycle status, and title — the discovery counterpart to the id-taking write verbs (`forget`, `relate`, `merge`, `unmerge`, `set-sensitivity`), which all require an id you first have to know. One read-only bundle walk backs every invocation; id, sensitivity, status, and title are all derived in that same pass.

`[TYPE]` is an optional positional filter. It accepts a canonical `link_dir` (`concepts`, `decisions`, `entities`, `events`, `organizations`, `people`, `places`, `procedures`, `projects`, `sources`) or a case-sensitive `REGISTRY.name` alias (e.g. `Person`) resolving to the same type; both forms print identical rows. Help and error text enumerate only the canonical `link_dir` names.

Each row prints exactly `ID  SENSITIVITY  STATUS  TITLE`, `ljust`-aligned over the header labels and the rows actually shown, matching `status`'s bundle-contents alignment. Deprecated and superseded objects are shown by default, marked via `STATUS`, with no flag to hide them; an object removed from disk by `merge` never appears. A confidential concept's title prints **unredacted and in full** — there is no display gate: `sensitivity` here is a column reporting what the document declares, never a filter on what you, the owner, can see on your own terminal (`--include-confidential` is exclusively an LLM-send gate elsewhere and has no effect here). A document that fails to read or parse is still listed, with its id and `(unreadable)` in place of a title; a readable document with no title shows `(untitled)` instead — two distinct markers for two distinct follow-ups. An empty bundle, or a filter matching nothing, prints a friendly "no objects" message and exits 0.

| Flag | Meaning |
| --- | --- |
| `--limit N` | Print at most `N` rows (default 50). Truncation prints a footer reporting how many rows were shown out of the total match count. `0` or a negative value refuses (exit 1) before any workspace or disk access. |
| `--all` | Print every matching row, ignoring `--limit`, with no truncation footer. |

**Exit ladder, in this exact order:** an unrecognized `TYPE` or an out-of-range `--limit` refuses (exit 1) before the workspace is even consulted — mirroring `set-volatility`'s vocabulary-before-workspace precedent, so a typo is reported as itself, not as a missing workspace. Only then does the shared `require_workspace` check run; its failure is the only remaining non-zero path. Once past both refusals, no bundle content — however malformed — can make `list` fail; it always exits 0. No file under the workspace is ever created, modified, or deleted, and no `--json` or other structured output mode is offered (deferred, not banned — issue #240).

### `openkos forget <concept-id>`

Removes knowledge. It removes the concept document, drops its entry from `index.md`, and records the removal as a dated tombstone line in `log.md`. The target is named by its concept ID — the path with `.md` removed, which is what OKF already defines identity to be (`openkos forget people/maria-salazar`). `forget` is **reference-aware**: a surviving inbound link or typed relation to the target refuses (exit 1) unless you pass `--force`, and `--scope source` cascades the delete to the target plus every concept whose *entire* provenance resolves back to it (the orphan-after-delete closure). It is recoverable through plain git; the irreversible, history-scrubbing counterpart is `purge`, below.

| Flag | Meaning |
| --- | --- |
| `--scope {self,source}` | `self` (default) removes only `<concept-id>`. `source` expands the set to `<concept-id>` plus every concept whose ENTIRE `provenance` resolves back to it — the orphan-after-delete closure; a concept with any surviving provenance entry outside the set is preserved untouched. |
| `--force` | Proceed even when inbound references (markdown links or typed relations) — or unverifiable referrers whose frontmatter could not be parsed — were detected; they are left dangling, never retargeted. Independent of `--auto`: it never skips the confirmation prompt. |
| `--auto` | Skip the confirmation prompt and write immediately (unattended). Config `review: false` skips the prompt the same way. |

`forget` is the mirror-image of `ingest`, sharing the same Phase A (validate + preview) / confirm gate / Phase B (write) shape. Index removal is **generic across every section** — Sources, Concepts, People, Decisions — not just Sources: whichever section's bullet links to the concept ID is the one dropped. A concept ID with no matching `index.md` entry is not an error; the file is still deleted. `forget` computes the proposed changes in memory and shows a preview (`~ index.md`, `~ log.md`, `- bundle/<concept-id>.md`) before writing, using the same confirm-gate precedence as `ingest` (`--auto` > config `review: false` > TTY prompt > non-TTY refusal).

Writes are, like `ingest`'s, **not transactional** — but ordered in reverse: `index.md` and `log.md` are updated FIRST, and the concept file is deleted LAST, so the catalog never references a file that no longer exists. A failure partway through (for example, the file delete itself failing) can leave the concept file present as a benign, git-recoverable orphan while the catalog has already moved on — never the other way around.

Undo is **plain git** (`git revert`, `git checkout <file>`) — there is no wrapper command for it. Every change is already a commit, so the safety net exists without new surface.

By default `forget` refuses to orphan an inbound link: a surviving markdown link or typed relation whose *target* is the concept being removed refuses (exit 1) unless `--force` is passed, in which case those links are left dangling (never retargeted). OKF tolerates broken links by design (§5.3), so a forced dangle is a quality signal, not corruption. `forget` deletes from the working tree only, leaving content recoverable in git history; the git-history rewrite + history content-scrub counterpart, which completes right-to-be-forgotten, is `purge`, below.

You can also just delete the file by hand — the bundle is your files. `forget` is the ergonomic version that cleans up the index and log in one step.

### `openkos purge <concept-id>` (MVP 2, right-to-be-forgotten, complete)

The **irreversible, true-erasure** counterpart to `forget`: whole-file-expunges a concept's source `raw/<name>` and bundle file from **ALL git history** (not just the working tree) via `git-filter-repo`. This is the most destructive verb in `openkos` — there is no undo, no reflog, no backup once the rewrite begins. In the SAME single rewrite pass, `purge` also content-scrubs every historical commit's `bundle/index.md` and `bundle/log.md` blobs — removing the purge-set member's catalog bullet, log entries, and any prior `forget` tombstone referencing it, as full-line removals matched by markdown link-identity (never a bare id-substring match) — so no residual is left anywhere in history: this is complete right-to-be-forgotten.

`purge` reuses `forget`'s Phase A **unchanged**: the same `--scope {self,source}` (default `self`; `source` cascades to every concept whose entire `provenance` resolves back to the target, via the same orphan-after-delete closure) and the same reference-aware detection. On top of that, it resolves each purge-set member's raw source path from a Source's `resource: raw/<name>` frontmatter (a derived concept, with no `resource`, contributes only its own bundle file; a Source whose `resource` is absent or malformed is **warned about, not refused**, and simply contributes no raw path).

Six fail-closed safety rails run, **in this exact order, all before any write**, and the first failing rail refuses immediately (exit 1, nothing written, no rewrite):

1. **Reference-aware refusal** — a surviving inbound reference or unverifiable referrer outside the purge set refuses unless `--force` (identical to `forget`'s own gate).
2. **`git`/`git-filter-repo` availability** — refuses with an install remediation if either is missing.
3. **Workspace root == git repository root** — refuses if the workspace is not a git repo, or is nested inside one whose root differs.
4. **Clean working tree** — refuses on any uncommitted change.
5. **No commits published on any remote** — refuses if the local branch already has commits on a configured remote (rewriting published history is unsafe).
6. **Typed confirmation phrase** — prints the preview and the irreversibility warning, then requires typing the **exact** phrase (`purge <concept-id>` for `--scope self`; `purge <concept-id> (<N> concepts)` for `--scope source`) — a bare `y`/`yes` does not satisfy it. `--confirm-phrase <phrase>` supplies it non-interactively; on a TTY without it, `purge` prompts interactively. There is **no `--auto` bypass** for this phrase.

Once all six rails pass, `purge` prints a "beginning the irreversible history rewrite now -- do not interrupt" line (the point-of-no-return warning: `expunge_paths` can run silently for a while, and this line exists so an operator does not mistake it for a hang and Ctrl-C mid-rewrite), then invokes `git-filter-repo`, in a single pass, to (1) remove every purge-set member's `raw/<name>` and `bundle/<id>.md` from every commit and the working tree, AND (2) content-scrub every historical commit's `bundle/index.md`/`bundle/log.md` blobs of that member's catalog bullet, log entries, and any `forget` tombstone referencing it — then finalizes (`git reflog expire` + `git gc --prune=now`) so purged blobs are unreachable **and pruned**. If the rewrite itself fails, nothing changed. If the rewrite succeeds but finalize fails, `purge` reports this distinctly (the data may still be recoverable via the reflog until finalize is completed manually) — this is the one case a purge can end in a state needing manual git-level follow-up.

After a successful rewrite (including the finalize-failed case above), `purge` removes the **live** `index.md` catalog bullet and any **live** `log.md` `forget` tombstone for every purge-set member (reusing `forget`'s own `remove_index_entry`/new `remove_log_entry` write paths) — otherwise the live catalog/log would keep pointing at a concept absent from every commit.

Index cleanup **deletes** (not row-`DELETE`, which SQLite's freelist can retain) `.openkos/{fts,vectors,graph}.db`, then best-effort rebuilds the FTS and graph indexes only (never through the Ollama-dependent `reindex`, which `purge` must never require) — `vectors.db` stays deleted for the next `openkos reindex` to lazily re-embed. A rebuild failure is reported but does **not** fail the purge — the irreversible act already succeeded.

**Irreversibility warning**, printed at preview and echoed again on success:

> WARNING: purge is IRREVERSIBLE. It rewrites ALL git history in place -- there is no git-undo, no reflog, no backup. The raw source file(s) and concept file(s) listed above will be permanently expunged from every commit, the purge-set member(s)' catalog bullet, log entries, and any forget tombstone will be scrubbed from every historical commit of index.md/log.md, and the live index.md catalog bullet(s) and log.md tombstone(s) for the purge-set member(s) will be removed.

No residual is printed or left behind: after a successful purge, the purged concept's id and title do not appear anywhere in `index.md` or `log.md`, in any commit, live or historical — this is complete right-to-be-forgotten.

`git-filter-repo` is a **system tool**, not a runtime dependency — installed separately (`pip install git-filter-repo` or your package manager), verified the same PATH-probe way `doctor` verifies Ollama. Run `openkos doctor` to check both `git` and `git-filter-repo` availability before purging.

### `openkos doctor`

**Read-only.** A fixed environment health scan: eleven checks against the local workspace, the local Ollama server, the local Python/SQLite build, and `git`/`git-filter-repo` availability, each printed as one `[PASS]`, `[FAIL]`, or `[SKIP]` line. Every `[FAIL]` line is immediately followed by an indented `  -> <fix command>` line naming the user's own next command (`ollama serve`, `ollama pull <model>`, `openkos init`, or an install command) — `doctor` never runs these commands itself. Output leads with the same `openkos {version}` banner as `--version`, printed before any check line, informational only and not counted among the eleven checks.

Unlike `status`/`lint`/`query`, `doctor` never stops at the first failure: it runs and prints **all** applicable checks, then exits once. The checks, in order:

1. **Workspace initialized** — informational.
2. **Config valid** — critical, workspace-only (`[SKIP]` outside a workspace).
3. **Ollama reachable** — critical, always runs. If unreachable, the remediation is binary-aware: when `ollama` is found on `PATH`, it stays exactly `ollama serve`; when no `ollama` binary is found at all, it names that ("no `ollama` binary found on PATH — install from https://ollama.com") rather than the over-claim "not installed", since a missing `PATH` entry does not prove Ollama was never installed (e.g. the macOS app).
4. **Model `<tag>` installed** — critical, always runs; `[SKIP]` (not `[FAIL]`) when Ollama is unreachable, since the two share one root cause. A configured tag counts as installed if it matches an installed tag exactly, or matches that tag's `<name>:latest` form.
5. **Embedding model `<tag>` installed** — informational, always runs, reusing the same installed-tag list and `[SKIP]`-when-unreachable behavior as the model-installed check (one root cause, never double-reported). Embeddings ARE consumed — `reindex` and `query`'s dense retrieval both need this model — but this check stays informational, so a failure here never affects the exit code (dense retrieval degrades to the other channels rather than blocking an answer).
6. **Bundle readable** — informational, workspace-only (`[SKIP]` outside a workspace).
7. **Workspace vector index present** — informational, workspace-only (`[SKIP]` outside a workspace), mirroring check 6's shape. Checks only whether *this* workspace's `.openkos/vectors.db` exists on disk (the file `purge` used to delete, #142); remediation is `openkos reindex`. Deliberately absent-only — staleness is out of scope, and this is distinct from check 8, which says nothing about any particular workspace's index file.
8. **Vector extension loadable** — informational, always runs, independent of workspace state and Ollama reachability (no `[SKIP]` branch — unlike check 5, it shares no root cause with any other check). Probes whether the `sqlite-vec` extension loads into a throwaway `:memory:` connection; on failure, the remediation names an extension-capable Python interpreter (e.g. a uv-managed interpreter) rather than the system/Homebrew Python that some platforms build without SQLite extension-loading support. The on-disk vector store this checks backs `query`'s dense retrieval, populated by `reindex`.
9. **`git` available** — informational, always runs, independent of workspace state and Ollama reachability. Required by `purge` (right-to-be-forgotten).
10. **`git-filter-repo` available** — informational, always runs. Required by `purge`; remediation names an install command (`pip install git-filter-repo` or your package manager).
11. **Backend host locality** — informational, always runs, independent of workspace state and Ollama *reachability* (locality is a literal-form check over an already-resolved host, so it answers even when the server is down). Reports the redacted host, whether it is this machine, and whether the confidential local exemption is consequently active — e.g. `[PASS] Backend host locality — this machine (localhost:11434); confidential local exemption active`. Unlike every other check it **always** `[PASS]`es: running against a remote Ollama is a legitimate configuration, not a fault, so the status only says the check ran and the detail carries the finding. It can never change the exit code. See [Sensitivity and the local backend](#sensitivity-and-the-local-backend).

Exit code reflects **critical** failures only: `doctor` exits `1` if config-valid, Ollama-reachable, or model-installed failed, and `0` otherwise — the eight informational checks (workspace-initialized, embedding-model-installed, bundle-readable, workspace-vector-index-present, vector-extension-loadable, `git`-available, `git-filter-repo`-available, backend-host-locality) never affect the exit code on their own.

`doctor` also works **outside an initialized workspace**, as a pure Ollama/vector-extension preflight: the workspace-initialized check reports an informational `[FAIL]` with `openkos init` remediation, the three workspace-only checks (config-valid, bundle-readable, workspace-vector-index-present) are skipped as not applicable, and the seven remaining checks (Ollama-reachable, model-installed, embedding-model-installed, vector-extension-loadable, `git`-available, `git-filter-repo`-available, backend-host-locality) still run — the Ollama-dependent checks against the packaged default model/embedding model, the locality check against the packaged `confidential_local_exemption` default — and Ollama-reachable/model-installed still determine the exit code.

`doctor` never creates, modifies, or deletes any file.

### `openkos reindex` (MVP 2)

**The sole writer of all three on-disk derived stores** — `.openkos/vectors.db`, `.openkos/fts.db`, and `.openkos/graph.db` (embedding-vector-store Slice 2b; performance-caching Slice 5 extended this to FTS and the graph). `query`/`answer()` only ever READ these three stores, read-only; `reindex` is the only command that writes to any of them. Mirrors `query`'s read-only-over-the-bundle shape: no confirmation prompt, no `--auto`. Refuses (exit 1) outside an initialized workspace, using the same shared `require_workspace` check `query`/`ingest` use.

Walks the compiled bundle once (the same walk `query`'s lexical index uses), keys each document by `concept_id` (bundle-relative path minus `.md` — identical to `forget`'s identity), and embeds its raw decoded text through a local Ollama server running the model configured as `embedding_model` in `openkos.yaml` (default `bge-m3`, ADR-0006). Re-embedding is gated by a `content_hash` cache: an unchanged document costs zero Ollama calls. Any stored vector whose source document no longer exists on disk is pruned — unless this run's bundle walk hit a directory-scan error (e.g. a permission-denied subdirectory), in which case the ENTIRE prune pass is skipped for that run (an unreadable subtree could make a still-existing document look absent, and pruning on that false signal would destroy a valid vector); the embed and cache-hit passes still complete normally regardless. `vectors.db` batches its embed/prune writes into ONE commit for the whole run (not once per document), and its connection sets `PRAGMA journal_mode=WAL` plus a `busy_timeout`, matching `fts.db`/`graph.db`'s posture.

The FTS and graph indexes are gated separately, by a **bundle-manifest-hash cache key**: a sha256 digest over the sorted set of every discovered document's `(concept_id, content_hash)` pair, stored in each derived store's own `meta` table. When a run's freshly computed digest matches the PREVIOUSLY stored one, the WHOLE FTS/graph rebuild is skipped for that store; any added, edited, or removed document changes the digest and triggers a full rebuild (no partial/per-document patch — cross-document graph edges make incremental updates unsafe). This manifest comparison happens **only here, in `reindex`** — `query`/`answer()` never compute or compare it, so an edit made after the last `reindex` run stays invisible to `query` until the next `reindex` run, exactly like the dense store already behaved before this slice. Each rebuild is atomic (wrapped in one explicit transaction): a crash mid-rebuild leaves the PRIOR index and PRIOR manifest hash intact rather than a half-written store.

Prints one summary line reporting how many documents were embedded, cache-hit, pruned, and skipped, then exits 0; a second line follows when the prune pass was itself suppressed by a directory-scan error this run, distinguishing that from a run where nothing genuinely qualified for pruning. When `OLLAMA_HOST` points the embedding host off this machine, the same one-line stderr advisory `ingest` and `query` print (redacted host; document text and embedding vectors will leave this machine) precedes the run — advisory only, never a refusal.

| Flag | Meaning |
| --- | --- |
| `--force` | Re-embed every discovered document, and unconditionally rebuild the FTS/graph indexes, ignoring the content-hash/manifest-hash caches. |

An unreachable Ollama, a missing embedding model, an unusable `sqlite-vec` extension, an unusable `fts5` module, or a filesystem error writing the graph index is reported on stderr with no raw traceback and exits 1 — the same ordered ladder `query` uses, extended to cover all three stores. `.openkos/vectors.db`, `.openkos/fts.db`, and `.openkos/graph.db` are `query`'s three retrieval seams (hybrid-retrieval-fusion Slice 3; graph-augmented-retrieval Slice 4; performance-caching Slice 5): run `reindex` at least once to enable dense/FTS/graph retrieval — without it, or with a corrupt store, `query` still works, falling back to whichever lists remain healthy, with a stderr hint.

## `openkos.yaml` (workspace config)

Structured settings for the workspace, read by the engine. It lives at the workspace root, beside `raw/` and `bundle/` — not inside the bundle, which holds concept documents and nothing else.

```yaml
model: qwen3:8b           # local model served via Ollama; see tech_stack.md
review: true              # show proposed changes and confirm before saving
default_sensitivity: private
confidential_local_exemption: true  # send confidential concepts to a LOCAL LLM backend
freshness_window: 7d      # age after which a stamp is flagged for re-observation

# Layout — where the engine keeps things, relative to this file.
raw: raw/                 # immutable sources; any extension, never rewritten
bundle: bundle/           # the OKF bundle root

# type_registry is maintained by the engine (canonical + emergent types)
```

### Sensitivity and the local backend

`sensitivity` governs what **leaves the machine**, so a `confidential` concept is held back from an `llm.chat` payload only when the backend is not verifiably local. Since [#240](https://github.com/jasonssdev/openkos/issues/240):

| Backend | `confidential` object | `--include-confidential` |
|---|---|---|
| Verified local | sent | not needed |
| Verified remote | blocked | still the escape hatch |
| Unknown / unparseable | blocked (fail closed) | still the escape hatch |

"Verified local" means the host the client will actually send to is loopback **by literal form** — `localhost`, `127.0.0.0/8`, or `::1`. No DNS, no allowlist: a name that resolves to loopback today can resolve elsewhere tomorrow, so anything unprovable is treated as remote. The host is read from the client itself, not from `OLLAMA_HOST`, so an explicit host override cannot be granted an exemption it does not qualify for.

**This is a deliberate change of behavior.** A workspace that relied on `confidential` meaning "never to any LLM" will now see those objects included when the backend is local. Set `confidential_local_exemption: false` in `openkos.yaml` to restore the old blanket gate. It is a workspace key rather than a per-command flag on purpose: a policy that depends on remembering to type a flag is not a policy. `--include-confidential` is unchanged and still works on every command.

Run `openkos doctor` to see which side of the line your backend is on (check 11).

**Terminal output is never gated by this.** Printing an object's title or id on your own screen is not an egress event — `list`/`status` show confidential titles in full, unredacted.

**`ingest`'s extraction floor gate does NOT take this exemption.** The table above governs the per-concept `confidential` filter on the five read verbs (`query`, `contradictions`, `adjudicate`, `suggest-relations`, `suggest-volatility`). `ingest`'s SEPARATE check — whether a workspace's `default_sensitivity` floor is confidential enough to skip concept extraction from a newly ingested Source entirely — refuses regardless of backend locality: it always keeps the Source-only fallback (the document is still embedded and searchable; only LLM-based concept extraction is skipped) rather than granting a local backend a pass. This is deliberate, not an oversight: extraction runs against content the operator has not yet reviewed at all, at ingest time, before any human has looked at it, so a local-backend reader should not be surprised that `ingest` still refuses here even with the exemption active elsewhere.

## Still deferred (MVP 3)

For orientation, these are **not** yet part of the CLI: the MCP server, the local REST API, and full OKF import/export, together with sensitivity enforcement at those new export/agent boundaries. Everything else described above — hybrid semantic/graph query, volatility-aware freshness windows, entity resolution and merge, the typed graph, reference-aware/cascade `forget`, and the `purge` verb — ships today (MVP 1 and MVP 2 complete).
