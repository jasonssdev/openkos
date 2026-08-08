<!--
  Manual end-to-end testing guide for OpenKOS.
  Companion to docs/user-journey.md (which explains the WHY); this doc is the
  step-by-step HOW for exercising every command against a real workspace.
-->

# End-to-End Testing Guide

This is a hands-on walkthrough for testing OpenKOS the way a real user would:
from an empty machine to a curated knowledge base, exercising the core command
surface (the coverage checklist at the end lists exactly what is walked; the
newer verbs `list`, `next`, `curate`, `set-sensitivity`, `backfill-sensitivity`,
and `backfill-source-titles` are not yet scripted here — see
[`cli.md`](cli.md) for their reference behavior).
It complements [`user-journey.md`](user-journey.md) — that document explains the
*philosophy*; this one is the *procedure*.

Use it to validate a release candidate, to reproduce a bug, or to onboard
yourself to the full surface of the tool.

> **Rule for the whole run.** After every command, check three things: the exit
> code (`echo $?`), what was printed, and what changed on disk. A test that only
> reads stdout misses half the behavior.
>
> **Where "what changed on disk" lives.** Every mutating verb (including
> `query --save`, since #331) **auto-commits** its own writes, so `git status`
> is normally clean and the evidence is in `git log -1 --stat` / `git show`. A
> *dirty* tree after a mutating verb is itself a finding — it means the
> auto-commit was skipped (check stderr for a `WARNING`).

---

## Phase 0 — Setup: install every dependency first

Complete every step in this section before any walkthrough phase; a fresh
machine needs all of it, and nothing here is assumed to be present already.
OpenKOS is local-first — there are no API keys and no cloud endpoints;
everything runs against a local [Ollama](https://ollama.com) server. (The
prerequisites here match the README's Getting started section.)

> **Looking for the automated test suite instead?** This document is the
> *manual* walkthrough. Contributors run the automated tests from a source
> checkout with `uv sync` followed by `uv run pytest` (add `--cov` for the
> coverage report, gated at 90% in CI); see
> [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the full development workflow.

### 0.1 First, decide which build you are testing

Decide this before installing anything — it is the most common way to waste a
test session. Everything below branches on this one choice:

| | **End user** | **Contributor** |
|---|---|---|
| What you test | the published PyPI package | your working tree (e.g. an unreleased fix) |
| Install path | §0.5a | §0.5b |
| Virtual environment | **none needed** — `uv tool install` manages its own isolated environment for you | `uv sync` creates a `.venv/` **inside the checkout**, used only for the automated suite |
| Needs the repo cloned | no | yes |

If you are here to validate a release candidate as a real user would, you are
the **end user**. Only pick the contributor path if you specifically need code
that is not published yet.

> **You never create or activate a virtualenv by hand for either path.** For the
> end-user path `uv tool install` isolates the CLI itself; for the contributor
> path `uv sync` and `uv run` manage `.venv/` for you.

### 0.2 Python 3.12+ and uv

OpenKOS targets **Python 3.12+** and both install paths use
[`uv`](https://docs.astral.sh/uv/), which can also provision Python itself:

```bash
# install uv
curl -LsSf https://astral.sh/uv/install.sh | sh   # or: brew install uv
uv --version                                      # verify

# install Python only if no 3.12+ is already present
python3 --version
uv python install 3.12
```

### 0.3 Ollama and **two** models

Install the Ollama runtime first — it is a separate application, not a Python
package, and nothing in OpenKOS installs it for you:

```bash
# macOS
brew install ollama          # or download the app from https://ollama.com/download

# Linux
curl -fsSL https://ollama.com/install.sh | sh

ollama --version             # verify before continuing
```

Then start the server and pull both models:

```bash
ollama serve            # leave running in its own terminal
ollama pull qwen3:8b    # chat model — the default written into openkos.yaml
ollama pull bge-m3      # embedding model — required by reindex and query
ollama list             # verify both appear
```

Both models are mandatory for full coverage — they serve different commands:

| Model | Used by |
|---|---|
| `qwen3:8b` (chat) | `ingest` extraction, `adjudicate`, `suggest-relations`, `suggest-volatility`, `contradictions`, `query` |
| `bge-m3` (embeddings) | `reindex`, and the dense-retrieval channel of `query` |

> The embedding dimension is hard-coded to 1024. `bge-m3` produces 1024
> dimensions; substituting a differently-sized embedding model fails at runtime.

### 0.4 git and git-filter-repo

git is **not optional**: `openkos init` sets the workspace up as a git
repository, every mutating verb auto-commits into it, and `forget`/`purge`
depend on that history.

```bash
# install git if missing
brew install git                    # macOS (or use Xcode Command Line Tools)
sudo apt install git                # Debian/Ubuntu
git --version                       # verify
```

git also needs an **identity**, or OpenKOS cannot create commits (it refuses to
invent a bot identity and prints a WARNING instead — you would silently test an
uncommitted workspace):

```bash
git config --get user.name || git config --global user.name "Your Name"
git config --get user.email || git config --global user.email "you@example.com"
```

`git-filter-repo` is a separate system tool, **not** installed with the package,
and is required **only** by `purge`. If it is missing, every phase works except
Phase 8.2:

```bash
pip install git-filter-repo     # or: brew install git-filter-repo
git-filter-repo --version       # verify
```

### 0.5 Install OpenKOS

Follow **only** the subsection matching your §0.1 decision.

#### 0.5a End user — the published package

```bash
uv tool install openkos     # or: pipx install openkos / pip install openkos
```

For a throwaway run of a specific release without installing anything
persistent: `uvx --from openkos==<version> openkos …`.

#### 0.5b Contributor — from source

```bash
git clone https://github.com/jasonssdev/openkos.git
cd openkos
uv sync                                     # dev environment (automated tests, lint, types)
uv tool install --force --from . openkos    # put THIS working tree on your PATH
```

#### Confirm what is actually on your PATH

```bash
which openkos
openkos --version
```

> Record that output in your findings log — a bug report without a version is
> not reproducible.

**Never run the walkthrough from inside the source checkout.** You may be
exercising local code instead of the build you think you are testing, and
`init` would try to set up git inside the OpenKOS repository itself. Use a
clean, separate workspace directory (Phase 2).

### 0.6 Warm the model

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

Expect an `openkos <version>` banner, then the `openkos doctor: checking
environment at <path>` header, a blank line, and then eleven
`[PASS]`/`[FAIL]`/`[SKIP]` lines — the banner and the header are not checks.
The eleven checks and their criticality (exit code is `1` only if a **critical**
check fails; a `[SKIP]` never causes exit 1):

| # | Check | Critical |
|---|---|---|
| 1 | Workspace initialized | no |
| 2 | Config valid | **yes** |
| 3 | Ollama reachable | **yes** |
| 4 | Chat model installed | **yes** |
| 5 | Embedding model installed | no |
| 6 | Bundle readable | no |
| 7 | Workspace vector index present | no |
| 8 | Vector extension loadable | no |
| 9 | `git` available | no |
| 10 | `git-filter-repo` available | no |
| 11 | Backend host locality | no |

**Adversarial sub-test.** Stop Ollama (`Ctrl-C` in its terminal), re-run
`openkos doctor`, confirm check 3 fails with an actionable remediation line and
the exit code is 1. Then restart Ollama.

---

## Phase 2 — Create the workspace

By convention a workspace lives at the root of your home directory and is named
`knowledge`:

```bash
mkdir -p ~/knowledge && cd ~/knowledge
```

Create the directory and nothing else — **do not run `git init` yourself.**
`openkos init` sets git up for you (see below). **Do not add a git remote**
either: `purge` refuses if any commit was published.

```bash
openkos init ; echo "exit: $?"
ls -la
```

> **The model prompt is a numbered picker.** On a TTY, `init` probes Ollama and
> prints `Installed chat models:` followed by a numbered list, with `qwen3:8b`
> first and marked `(recommended)`. Answer with a **number**; pressing Enter
> takes `1)`. An invalid answer reprompts (up to three attempts) instead of
> being accepted. Only when the probe fails or no chat model is installed does
> it degrade to the older free-text `Model [qwen3:8b]:` prompt — there, press
> **Enter**; typing `yes` would set the model to the literal string `yes`.
> Embedding models such as `bge-m3` are filtered out of the list by design.

Expect exactly five workspace artifacts, plus the git setup `init` performs
after them:

| Path | Purpose |
|---|---|
| `raw/` | immutable byte-for-byte copies of ingested files |
| `bundle/index.md` | the OKF catalog |
| `bundle/log.md` | append-only dated activity log |
| `AGENTS.md` | agent operating manual |
| `openkos.yaml` | workspace marker + config (written last) |
| `.git/` + `.gitignore` | git setup, done by `init` itself — see below |

There should be **no** `.openkos/` yet (created lazily by `reindex`) and no
concept-type subfolders (created by `ingest`).

### 2.1 Verify the git setup `init` performed

Once the five artifacts are on disk, `init` runs a **best-effort** git setup —
this is deliberately last, so a git failure can never leave a half-written
workspace. Verify all three parts:

```bash
git log --oneline        # expect one commit: "chore(openkos): initialize workspace"
git status               # expect a clean tree
head -5 .gitignore       # expect "# --- openkos workspace (derived artifacts) ---"
wc -l .gitignore         # expect the full template (~250 lines), not a stub
```

What `init` does, precisely:

1. **`git init`**, but only if the directory is not *already* inside a git
   working tree — it never nests a repository inside a parent one.
2. **Writes `.gitignore`** from the packaged template (the standard
   Python/macOS/Linux/Windows set, which ignores the derived `.openkos/`). An
   **existing `.gitignore` is never overwritten** — and you should never
   overwrite the generated one either.
3. **Commits** exactly the paths it just created (a scoped `git add -- <paths>`,
   never `-A`), with the message `chore(openkos): initialize workspace`.

Every part is non-fatal: a git failure prints a stderr `WARNING` and leaves the
workspace valid, without changing `init`'s exit code.

**Adversarial sub-test — missing git identity.** If `git config user.name` /
`user.email` are unset, `init` prints `WARNING -- git identity unset; skipped
the initial commit` and creates no commit; it never falls back to a bot
identity. If you skipped §0.4, you will see this here — fix the identity and
commit manually before continuing, because the whole run depends on history.

**Idempotence sub-test.** Run `openkos init` again — it must refuse without
writing anything.

---

## Phase 3 — Ingest your content

### 3.1 Constraints to design your material around

- **UTF-8 decodability decides everything.** A decodable file is embedded and
  sent to the LLM for extraction. A non-decodable file is still copied to `raw/`
  but extraction is skipped (exit 0, Source only). **There is no PDF or DOCX
  parser** — binary files copy but yield no extracted knowledge. Use `.md`,
  `.txt`, `.csv`, `.json`, `.yaml`, `.py`, `.html`, `.log`, or extensionless text.
- **Batch is one invocation.** `<path>` may also be a directory (non-recursive)
  or a quoted glob (recursion via `**`), driving every matched file through the
  same per-file pipeline with one up-front cost gate — see `cli.md` for the
  batch exit ladder (all-drift → 3, any hard skip → 1).
- **Max 5 derived objects per source.**
- **`raw/` is immutable.** A byte-identical re-ingest is idempotent and **re-runs
  extraction** (useful to recover from a transient LLM failure). A *different*
  file under the same basename is refused.
- **Same-slug collisions resolve by origin.** Two sources whose extracted
  objects produce the same slug now **both survive**: the later one is
  disambiguated to the first free numeric suffix and announces
  `'<slug>' already exists for a different source; disambiguating this candidate
  to '<slug>-2'`, with a durable `**Disambiguation**` bullet written to
  `log.md`. Only a collision from the **same** source is a create-only no-op
  (`'…' already exists; skipping this candidate (create-only)`) — that is
  idempotence, not data loss. Verify both behaviors: ingest two different files
  about the same entity, then re-ingest one of them.

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

> Extraction is a single blocking LLM call (~20 s), running **before** the
> "proposed changes" preview. On a TTY a spinner reading
> `openkos ingest: extracting concepts…` is shown on stderr for its duration —
> if you see it, the tool is working, not hung. (The spinner is stderr-only and
> no-ops when output is piped, so stdout stays clean for scripting.) On success
> the summary ends with a per-type tally: `extracted 3 objects — 2 Concept, 1
> Person`.

Inspect the result — note that `ingest` **auto-commits** on success, so a clean
`git status` is the expected outcome and the commit is where the diff lives:

```bash
git log -1 --stat        # expect "openkos: ingest <name> (+N concepts)"
git status               # expect clean
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
- **`suggest-relations`** no longer wastes its LLM calls on provenance mirrors:
  a body link that merely repeats the source's `provenance:` frontmatter is now
  typed `derived_from` at projection time, so the command should surface real
  concept↔concept candidates. On an ingest-only corpus it may legitimately find
  nothing — build genuine cross-links (Phase 7.1) if you want candidates to
  judge.

```bash
for f in /path/to/corpus/*.md; do openkos ingest "$f" --auto; done
git log --oneline        # one auto-commit per successful ingest
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
> `ingest` refreshes only the dense vector store for what it just wrote (so
> `suggest-relations` works in the same run); merge, forget, relate, reconcile
> update no index at all, and nothing but `reindex` ever rebuilds the FTS and
> graph stores. Edits stay invisible to `query`'s lexical and graph channels
> until the next `reindex`.

```bash
openkos query "a question your corpus can answer"
openkos query "..." --limit 10 --include-deprecated
```

Judge the answer harshly: are the citations real and traceable to your sources,
or does the model invent to fill gaps?

**The un-indexed gap test** (important):

```bash
openkos ingest /path/to/new.md --auto
openkos query "a question only the new file answers"    # dense may hit (ingest embeds); FTS/graph miss it — check the stderr retrieval: line
openkos reindex
openkos query "the same question"                       # all three channels now see it
```

**`query --save`** is the only writing form of query. Since #331 it
auto-commits its three writes (answer document, `index.md`, `log.md`) like
every other mutating verb, so no manual commit step remains:

```bash
openkos query "a synthesis question" --save --title "My Synthesis" --type Concept
git status                                    # expect clean — auto-committed
openkos reindex
```

---

## Phase 5 — Read-only inspection (deterministic, no LLM)

```bash
openkos status        # bundle counts, recent activity, conformance
openkos lint          # stale stamps, orphans, dangling refs/provenance, unextracted sources, sensitivity coverage
openkos duplicates    # candidate duplicates (difflib, no LLM)
openkos duplicates --include-deprecated
```

If `duplicates` finds nothing, your corpus lacks overlapping material — ingest
two docs about the same topic before Phase 6.

---

## Phase 6 — LLM advisors (read-only; they call the model)

All four **report**; in the forms below they never write. Each pairs with a
write path in Phase 7 — and `adjudicate` additionally has its own
`--apply`/`--apply-same` merge modes, exercised in Phase 7.2.

```bash
openkos adjudicate                 # SAME/DIFFERENT/UNCERTAIN on the duplicates groups
openkos adjudicate --same-only
openkos adjudicate --json          # machine-readable verdicts; suppresses the human report
openkos suggest-relations          # proposes a relation type per untyped edge
openkos suggest-volatility         # proposes a volatility tier per concept type
openkos contradictions             # detects conflicts between RELATED concepts
openkos contradictions --all
```

**Sensitivity governs egress, and is fail-closed about it:** against a backend
that is not verifiably on this machine, confidential concepts never reach the
LLM unless `--include-confidential` is passed. Against a local Ollama — the
default, and what this manual run uses — the confidential local exemption
applies and they participate normally (#240). To exercise the blocked path
here, set `confidential_local_exemption: false` in `openkos.yaml` first, and
confirm `openkos doctor` check 11 reports the exemption inactive.

Record for each whether it found what you planted. `contradictions` only inspects
**already-related** concepts, so relate the conflicting pair first (Phase 7.3).

---

## Phase 7 — Curation writes

Every verb in this phase auto-commits, so inspect each one with
`git log -1 --stat` (not `git status`, which should stay clean) and check stderr
for any `WARNING -- ... skipped auto-commit`. Run `reindex` after any of them.

### 7.1 `relate`

```bash
openkos relate <source_id> <relation_type> <target_id>
cat bundle/<...>/<source_id>.md    # verify the relations: frontmatter
```

Use one of the **seeded** relation types to avoid a warning: `caused_by`,
`depends_on`, `derived_from`, `member_of`, `part_of`, `produced_by`,
`references`, `related_to`.

### 7.2 `merge` / `unmerge` (round-trip)

Because both verbs auto-commit, byte-parity is verified **against the pre-merge
commit**, not with a bare `git diff` (the tree is committed and therefore always
clean):

```bash
BEFORE=$(git rev-parse HEAD)         # capture the pre-merge state
openkos merge <survivor_id> <absorbed_id>
openkos status                       # absorbed concept is gone
openkos unmerge <survivor_id> <absorbed_id>
git diff "$BEFORE" HEAD              # expect EMPTY — byte-parity restored
git log --oneline "$BEFORE"..HEAD    # expect two commits: the merge and the unmerge
```

Constraints: `unmerge` is **LIFO-only** (reverses only the most recent merge on
that survivor) and restores `index.md`/`log.md` from a pre-merge snapshot,
**discarding any intervening writes** — do not run other writes between a merge
and its unmerge during this test.

`adjudicate` can drive this same merge path in bulk. If your corpus still has
SAME pairs, test both modes:

```bash
openkos adjudicate --apply                            # interactive [y/N] walk per SAME pair
openkos adjudicate --apply-same --confirm-count <N>   # batch — <N> must equal the printed Total exactly
```

Try a wrong `--confirm-count` first and confirm it aborts with zero writes;
every applied merge is an ordinary commit, reversible via `unmerge`.

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
git log -2 --stat        # relate and reconcile each left their own auto-commit
openkos reindex
```

### 7.4 `suggest-volatility` → `set-volatility`

```bash
openkos suggest-volatility               # note one suggested tier, e.g. "Person: volatile"
openkos set-volatility Person volatile
grep -n -A5 'type_tiers' openkos.yaml    # verify the write landed
openkos set-volatility Person volatile   # re-run: idempotent no-op, nothing written
openkos set-volatility Person sometimes  # expect refusal — invalid tier
```

`set-volatility` validates both arguments before touching anything (the type is
case-sensitive PascalCase; the tier is one of `static`/`slow`/`volatile`), edits
`openkos.yaml` with comment-safe text surgery, and auto-commits a confirmed
write — no `git add` needed, and no `reindex` either (it changes config, not
bundle content).

---

## Phase 8 — Removal (run last)

> `forget`/`purge` depend on git. The tree should already be **committed and
> clean** here, since every earlier verb auto-committed (including
> `query --save`, since #331) — but verify with `git status` first: `purge`
> refuses to run on a dirty tree.

### 8.1 `forget` — recoverable

`forget` auto-commits the deletion, so the undo is **`git revert`**, not
`git restore` (there is nothing uncommitted left to restore):

```bash
openkos forget <concept_id>
git log -1 --stat             # the deletion, committed: concept + index.md + log.md
openkos status                # concept is gone
git revert --no-edit HEAD     # undo the ENTIRE forget in one commit
openkos status                # concept is back
```

Options: `--scope self` (default) or `--scope source` (cascades over provenance
descendants); `--force` proceeds even when inbound links would dangle (it does
**not** skip the confirmation prompt).

### 8.2 `purge` — irreversible

`purge` rewrites **all** git history via `git-filter-repo`, then expires the
reflog and runs `git gc`. There is no undo. Preflight:

```bash
openkos doctor       # checks 9 and 10 must PASS
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

Twenty rows for the 19 commands this walkthrough exercises — `query` appears
twice, once for its read-only default and once for its writing `--save` form.
Six shipped verbs are not yet scripted here and have no row: `list`, `next`,
`curate`, `set-sensitivity`, `backfill-sensitivity`, and
`backfill-source-titles` (see [`cli.md`](cli.md)).

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
| 10 | `adjudicate` | only with `--apply`/`--apply-same` | yes | ☐ |
| 11 | `suggest-relations` | no | yes | ☐ |
| 12 | `suggest-volatility` | no | yes | ☐ |
| 13 | `set-volatility` | yes (config) | no | ☐ |
| 14 | `contradictions` | no | yes | ☐ |
| 15 | `relate` | yes | no | ☐ |
| 16 | `merge` | yes | no | ☐ |
| 17 | `unmerge` | yes | no | ☐ |
| 18 | `reconcile` | yes | no | ☐ |
| 19 | `forget` | yes | no | ☐ |
| 20 | `purge` | yes | no | ☐ |

---

## Known issues — expect these, don't re-file them

When prior end-to-end testing has left issues open, they are listed here so you
add evidence to the existing issue rather than opening a new one.

**There are no open known issues right now.** Anything you hit in this round is
new: open an issue for it.

**Everything the previous rounds found is now fixed** — the `init` model prompt
(#128), same-slug source collisions (#131), `status` per-type counts (#133),
`suggest-relations` vocabulary noise and per-edge latency (#134) and its
provenance duplication (#135), missing `ingest` extraction feedback (#136),
`adjudicate` part-whole verdicts (#138), `purge --force` dangling references
(#141), `purge` deleting `vectors.db` (#142), `init` not setting up git
(#143), and the missing `--version` flag (#181). If any of them reappears,
that is a **regression** and deserves a new
issue, not a comment on the closed one.

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

MCP server, local REST API, full OKF import/export, a
`--sensitivity` flag on ingest, a configurable extraction cap, and `--json` or
structured output on any command other than `adjudicate` (which has `--json`).
All deferred by design (see [`roadmap.md`](roadmap.md)).
