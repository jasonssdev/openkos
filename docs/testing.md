<!--
  Manual end-to-end testing guide for OpenKOS.
  Companion to docs/user-journey.md (which explains the WHY); this doc is the
  step-by-step HOW for exercising every command against a real workspace.
-->

# End-to-End Testing Guide

This is a hands-on walkthrough for testing OpenKOS the way a real user would:
from an empty machine to a curated knowledge base, exercising all 18 commands.
It complements [`user-journey.md`](user-journey.md) — that document explains the
*philosophy*; this one is the *procedure*.

Use it to validate a release candidate, to reproduce a bug, or to onboard
yourself to the full surface of the tool.

> **Rule for the whole run.** After every command, check three things: the exit
> code (`echo $?`), what was printed, and what changed on disk (`git status`). A
> test that only reads stdout misses half the behavior.

---

## What you are testing, and against which build

Decide this first — it is the most common way to waste a test session.

- **The published package:** `uv tool install openkos` (or `uvx --from openkos==<version> openkos …` for a throwaway run). This is what real users get.
- **A local build (e.g. an unreleased fix):** `uv tool install --force --from /path/to/openkos openkos`. This replaces the `openkos` on your PATH with your working tree.

Confirm which one is active before you start:

```bash
which openkos
openkos --help | head -3
```

**Never run the test from inside the source checkout** unless you specifically
intend to — you may be exercising local code instead of the build you think you
are testing. Use a clean, separate workspace directory.

---

## Phase 0 — Machine prerequisites

OpenKOS is local-first. There are no API keys and no cloud endpoints; everything
runs against a local [Ollama](https://ollama.com) server.

### 0.1 Ollama and **two** models

```bash
ollama serve            # leave running in its own terminal
ollama pull qwen3:8b    # chat model — the default written into openkos.yaml
ollama pull bge-m3      # embedding model — required by reindex and query
```

Both models are mandatory for full coverage — they serve different commands:

| Model | Used by |
|---|---|
| `qwen3:8b` (chat) | `ingest` extraction, `adjudicate`, `suggest-relations`, `suggest-volatility`, `contradictions`, `query` |
| `bge-m3` (embeddings) | `reindex`, and the dense-retrieval channel of `query` |

> The embedding dimension is hard-coded to 1024. `bge-m3` produces 1024
> dimensions; substituting a differently-sized embedding model fails at runtime.

### 0.2 System tools

```bash
git --version
git-filter-repo --version    # optional — required ONLY by `purge`
```

`git-filter-repo` is **not** installed with the package. If it is missing, every
phase works except `purge`. Install it with `pip install git-filter-repo` or your
package manager if you intend to test purge.

### 0.3 Warm the model

The first LLM call after idle pays a cold-start cost (loading ~10 GB into memory)
that can exceed the request timeout and surface as `Ollama … timed out`. Warm it
before a batch of ingests:

```bash
ollama run qwen3:8b "ok" >/dev/null    # or any command that hits the model
```

---

## Phase 1 — Preflight before anything exists

`doctor` is designed to work **outside** a workspace. Test that first.

```bash
cd ~
openkos doctor ; echo "exit: $?"
```

Expect nine `[PASS]`/`[FAIL]`/`[SKIP]` lines. The nine checks and their
criticality (exit code is `1` only if a **critical** check fails; a `[SKIP]`
never causes exit 1):

| # | Check | Critical |
|---|---|---|
| 1 | Workspace initialized | no |
| 2 | Config valid | **yes** |
| 3 | Ollama reachable | **yes** |
| 4 | Chat model installed | **yes** |
| 5 | Embedding model installed | no |
| 6 | Bundle readable | no |
| 7 | Vector extension loadable | no |
| 8 | `git` available | no |
| 9 | `git-filter-repo` available | no |

**Adversarial sub-test.** Stop Ollama (`Ctrl-C` in its terminal), re-run
`openkos doctor`, confirm check 3 fails with an actionable remediation line and
the exit code is 1. Then restart Ollama.

---

## Phase 2 — Create the workspace

```bash
mkdir -p ~/kos-test && cd ~/kos-test
git init
```

`openkos init` does **not** run `git init` for you, and does not scaffold a
`.gitignore`. Do both yourself — git is the undo mechanism for `forget`/`merge`,
and `purge` cannot run without committed history. **Do not add a git remote**:
`purge` refuses if any commit was published.

```bash
openkos init ; echo "exit: $?"
ls -la
```

> **Watch the model prompt.** `init` prints `Model [qwen3:8b]:` — this is a
> *value* prompt with a default in brackets, **not** a yes/no confirmation. Press
> **Enter** to accept `qwen3:8b`. Typing `yes` sets the model to the literal
> string `yes`, which YAML parses as a boolean and breaks every later LLM call.

Expect exactly five artifacts and nothing else:

| Path | Purpose |
|---|---|
| `raw/` | immutable byte-for-byte copies of ingested files |
| `bundle/index.md` | the OKF catalog |
| `bundle/log.md` | append-only dated activity log |
| `AGENTS.md` | agent operating manual |
| `openkos.yaml` | workspace marker + config (written last) |

There should be **no** `.openkos/` yet (created lazily by `reindex`) and no
concept-type subfolders (created by `ingest`).

**Idempotence sub-test.** Run `openkos init` again — it must refuse without
writing anything.

Now set up git so `forget`/`purge` work later:

```bash
printf '.openkos/\n.DS_Store\n' > .gitignore    # .openkos/ is derived — never commit it
git add -A && git commit -m "chore: initialize workspace"
```

---

## Phase 3 — Ingest your content

### 3.1 Constraints to design your material around

- **UTF-8 decodability decides everything.** A decodable file is embedded and
  sent to the LLM for extraction. A non-decodable file is still copied to `raw/`
  but extraction is skipped (exit 0, Source only). **There is no PDF or DOCX
  parser** — binary files copy but yield no extracted knowledge. Use `.md`,
  `.txt`, `.csv`, `.json`, `.yaml`, `.py`, `.html`, `.log`, or extensionless text.
- **One file per invocation.** No batch, glob, or directory ingest.
- **Max 5 derived objects per source.**
- **`raw/` is immutable.** A byte-identical re-ingest is idempotent and **re-runs
  extraction** (useful to recover from a transient LLM failure). A *different*
  file under the same basename is refused.
- **Same-title collision.** Two sources whose extracted objects produce the same
  slug do **not** both survive: the first wins, the later one prints
  `'…' already exists; skipping this candidate (create-only)` and is dropped. Give
  overlapping sources distinct titles if you need both.

### 3.2 First ingest

```bash
openkos ingest /path/to/first.md ; echo "exit: $?"
```

You will hit the confirmation gate (identical across `ingest`/`forget`/`relate`/
`merge`/`unmerge`/`reconcile`/`query --save`):

1. `--auto` → write without asking.
2. Else `review: false` in `openkos.yaml` → write without asking.
3. Else interactive terminal → prompt; declining aborts (exit 1).
4. Else (non-interactive, `review: true`, no `--auto`) → refuse to write (exit 1).

> Extraction is a single blocking LLM call (~20 s). It runs **before** the
> "proposed changes" preview, so the terminal is silent during it — this is
> normal, not a hang.

Inspect the result:

```bash
git status
cat bundle/sources/*.md | head -40
cat bundle/index.md
```

Record: how many derived objects were extracted, whether they are accurate, and
which type folders appeared under `bundle/`.

### 3.3 Build enough mass to test curation

Ingest **at least 8–10 files**. Later phases need specific *shapes* of content
that generic docs will not produce:

- **`duplicates`/`adjudicate`/`merge`** need two concepts about the same thing.
  A set of related docs (e.g. an entity and its components) naturally produces
  these.
- **`contradictions`/`reconcile`** need two **related** concepts that disagree —
  and, because of the same-title collision above, they must have **distinct
  titles** or one is dropped before it can contradict anything. Example that
  works: two files, `# MCP Launch` ("launched 2024-11") and `# MCP Origin`
  ("originated 2004-01").
- **`suggest-relations`** currently only ever sees concept→source provenance
  edges; expect it to produce provenance noise rather than concept↔concept
  relations (see Known Issues).

```bash
for f in /path/to/corpus/*.md; do openkos ingest "$f" --auto; done
git add -A && git commit -m "feat: ingest corpus"
```

### 3.4 Adversarial ingest sub-tests

```bash
openkos ingest /path/to/a/directory/     # expect refusal
openkos ingest /nonexistent.md           # expect refusal
openkos ingest /path/to/some.pdf         # expect exit 0, copied, no extraction
openkos ingest /path/to/first.md         # identical re-ingest: idempotent, re-runs extraction
```

---

## Phase 4 — Build the derived stores, then query

`reindex` is the **sole writer** of the three derived stores, and `query` never
builds them or checks whether they are stale.

```bash
openkos reindex ; echo "exit: $?"
ls -la .openkos/          # vectors.db, fts.db, graph.db now exist
```

> **The rule for the rest of the run: re-run `reindex` after every write.**
> Ingest, merge, forget, relate, reconcile — none update the indexes. Edits stay
> invisible to `query` until the next `reindex`.

```bash
openkos query "a question your corpus can answer"
openkos query "..." --limit 10 --include-deprecated
```

Judge the answer harshly: are the citations real and traceable to your sources,
or does the model invent to fill gaps?

**The un-indexed gap test** (important):

```bash
openkos ingest /path/to/new.md --auto
openkos query "a question only the new file answers"    # expect degraded / missing + a stderr hint
openkos reindex
openkos query "the same question"                       # now answers
```

**`query --save`** is the only writing form of query:

```bash
openkos query "a synthesis question" --save --title "My Synthesis" --type Concept
openkos reindex && git add -A && git commit -m "feat: save synthesized concept"
```

---

## Phase 5 — Read-only inspection (deterministic, no LLM)

```bash
openkos status        # bundle counts, recent activity, conformance
openkos lint          # stale stamps and orphan pages
openkos duplicates    # candidate duplicates (difflib, no LLM)
openkos duplicates --include-deprecated
```

If `duplicates` finds nothing, your corpus lacks overlapping material — ingest
two docs about the same topic before Phase 6.

---

## Phase 6 — LLM advisors (read-only; they call the model)

All four **report**; they never write. Each pairs with a write verb in Phase 7.

```bash
openkos adjudicate                 # SAME/DIFFERENT/UNCERTAIN on the duplicates groups
openkos adjudicate --same-only
openkos suggest-relations          # proposes a relation type per untyped edge
openkos suggest-volatility         # proposes a volatility tier per concept type
openkos contradictions             # detects conflicts between RELATED concepts
openkos contradictions --all
```

**Sensitivity is fail-closed:** confidential concepts never reach the LLM unless
`--include-confidential` is passed.

Record for each whether it found what you planted. `contradictions` only inspects
**already-related** concepts, so relate the conflicting pair first (Phase 7.3).

---

## Phase 7 — Curation writes

Run `git status` before and after each command. `reindex` after any of them.

### 7.1 `relate`

```bash
openkos relate <source_id> <relation_type> <target_id>
cat bundle/<...>/<source_id>.md    # verify the relations: frontmatter
```

Use one of the **seeded** relation types to avoid a warning: `caused_by`,
`depends_on`, `derived_from`, `member_of`, `part_of`, `produced_by`,
`references`, `related_to`.

### 7.2 `merge` / `unmerge` (round-trip)

```bash
openkos merge <survivor_id> <absorbed_id>
openkos status                       # absorbed concept is gone
openkos unmerge <survivor_id> <absorbed_id>
git diff                             # expect NO diff — byte-parity restored
```

Constraints: `unmerge` is **LIFO-only** (reverses only the most recent merge on
that survivor) and restores `index.md`/`log.md` from a pre-merge snapshot,
**discarding any intervening writes** — do not run other writes between a merge
and its unmerge during this test.

### 7.3 `contradictions` → `reconcile`

```bash
openkos relate <id_a> related_to <id_b>    # contradictions needs them related
openkos reindex
openkos contradictions                     # should detect the conflict
openkos reconcile <id_a> <id_b> --winner <id_a>    # directional: id_a supersedes id_b
```

Without `--winner`, `reconcile` records a symmetric `reconciled_with` on both. A
conflicting re-resolution is refused (test that too).

```bash
openkos reindex && git add -A && git commit -m "fix: curate concepts"
```

---

## Phase 8 — Removal (run last)

> `forget`/`purge` depend on git. Ensure the workspace is **committed and clean**
> before this phase — otherwise `forget` has nothing to restore and `purge` will
> not run.

### 8.1 `forget` — recoverable

```bash
openkos forget <concept_id>
git status                    # concept deleted; index.md/log.md modified
git restore .                 # undo the ENTIRE forget (concept + index + log)
git status                    # clean again
openkos status                # concept is back
```

Options: `--scope self` (default) or `--scope source` (cascades over provenance
descendants); `--force` proceeds even when inbound links would dangle (it does
**not** skip the confirmation prompt).

### 8.2 `purge` — irreversible

`purge` rewrites **all** git history via `git-filter-repo`, then expires the
reflog and runs `git gc`. There is no undo. Preflight:

```bash
openkos doctor       # checks 8 and 9 must PASS
git status           # working tree MUST be clean
git remote -v        # MUST be empty
```

Six fail-closed rails, in order: (1) refuses if other concepts reference the
target, unless `--force`; (2) `git`/`git-filter-repo` on PATH; (3) workspace root
== git repo root; (4) clean tree; (5) no published remote; (6) an **exact typed
phrase** — `purge <concept-id>` (a bare `y` never works; there is no `--auto`).

Test at least two rails before the real run: make the tree dirty and confirm rail
4 stops you; type `yes` at the phrase prompt and confirm rail 6 rejects it. Then:

```bash
openkos purge <concept_id>          # type the exact phrase when prompted
openkos reindex                     # purge deletes vectors.db and does NOT rebuild it
openkos query "..."                 # confirm dense retrieval works again
```

Verify the erasure is total:

```bash
git log --all -p -- raw/<source-filename>     # expect nothing
grep -r "<distinctive string from that file>" .    # expect nothing
```

---

## Coverage checklist

| # | Command | Writes? | LLM | ✓ |
|---|---|---|---|---|
| 1 | `doctor` | no | probes | ☐ |
| 2 | `init` | yes | optional | ☐ |
| 3 | `ingest` | yes | yes (degrades) | ☐ |
| 4 | `status` | no | no | ☐ |
| 5 | `lint` | no | no | ☐ |
| 6 | `duplicates` | no | no | ☐ |
| 7 | `reindex` | yes (derived) | embeddings | ☐ |
| 8 | `query` | no | both models | ☐ |
| 9 | `query --save` | yes | yes | ☐ |
| 10 | `adjudicate` | no | yes | ☐ |
| 11 | `suggest-relations` | no | yes | ☐ |
| 12 | `suggest-volatility` | no | yes | ☐ |
| 13 | `contradictions` | no | yes | ☐ |
| 14 | `relate` | yes | no | ☐ |
| 15 | `merge` | yes | no | ☐ |
| 16 | `unmerge` | yes | no | ☐ |
| 17 | `reconcile` | yes | no | ☐ |
| 18 | `forget` | yes | no | ☐ |
| 19 | `purge` | yes | no | ☐ |

---

## Known issues — expect these, don't re-file them

Surfaced by prior end-to-end testing and already tracked. If you hit one, it is
expected; add evidence to the existing issue rather than opening a new one.

| Area | Behavior you'll see | Issue |
|---|---|---|
| `init` model prompt | `Model [qwen3:8b]:` reads like a yes/no; typing a word breaks config | #128 |
| Same-title sources | Second source's concept dropped as `create-only`; blocks contradiction detection | #131 |
| `status` counts | Every non-Source type lumped under `Concepts:` (Procedures/Events hidden) | #133 |
| `suggest-relations` | Wall of "not a seeded relation type" notes; one slow LLM call per edge | #134 |
| `suggest-relations` | Only types concept→source provenance edges | #135 |
| `ingest` feedback | No spinner during the ~20 s extraction; no per-type counter | #136 |
| `adjudicate` → `merge` | No batch/`--json`/interactive apply — merges are manual, one at a time | #137 |
| `adjudicate` | Flat `0.95` confidence on every verdict; part-whole pairs marked SAME | #138 |
| `duplicates`/`adjudicate` | Long unsummarized output; `[LOW] … 1.000` labels confuse | #139 |
| `suggest-volatility` | No write verb — "Next: edit type_tiers in openkos.yaml" by hand | #140 |
| `purge --force` | Leaves dangling references that `lint`/`status` never detect | #141 |
| `purge` | Deletes `vectors.db` without rebuilding it or prompting `reindex` | #142 |
| `init` | No `git init` / `.gitignore`, yet `forget`/`purge` depend on git | #143 |

---

## Findings log template

Record every friction point so it converts directly into an issue.

```
### Finding — <one-line title>
Phase / command:
What I ran:
What I expected:
What happened (output + exit code):
Severity: blocker | major | minor | polish
Reproducible: yes / no
```

The most valuable findings are rarely crashes — they are the moments where you,
knowing the tool, still had to stop and guess what to do next.

---

## Not available yet — do not test as missing features

MCP server, local REST API, full OKF import/export, batch/glob ingest, a
`--sensitivity` flag on ingest, a configurable extraction cap, and any `--json`
or structured-output mode. All deferred by design (see [`roadmap.md`](roadmap.md)).
