<!--
  Manual end-to-end testing guide for OpenKOS.
  Companion to docs/user-journey.md (which explains the WHY); this doc is the
  step-by-step HOW for exercising every command against a real workspace.
-->

# End-to-End Testing Guide

This is a hands-on walkthrough for testing OpenKOS the way a real user would:
from an empty machine to a curated knowledge base. It walks the core loop in
order; `openkos --help` is the authority on the full command surface, and
[`cli.md`](cli.md) is the per-verb reference. It complements
[`user-journey.md`](user-journey.md) — that document explains the *philosophy*;
this one is the *procedure*.

> **Bring your own content.** Test against text you actually know — your own
> notes, meeting transcripts, course material. You cannot judge whether an
> extracted object is right, or whether an answer is grounded, on a corpus you
> have not read. Two contrasting shapes are worth having: **structured
> documents** with clear titles and headings, and **unstructured transcripts**
> of people talking. They exercise the extractor very differently, and the
> difference between them is itself informative.

Use it to validate a release candidate, to reproduce a bug, or to onboard
yourself to the full surface of the tool.

> **Rule for the whole run.** After every command, check three things: the exit
> code (`echo $?`), what was printed, and what changed on disk. A test that only
> reads stdout misses half the behavior.
>
> **Where "what changed on disk" lives.** Every mutating verb (including
> `query --save`) **auto-commits** its own writes, so `git status`
> is normally clean and the evidence is in `git log -1 --stat` / `git show`. A
> *dirty* tree after a mutating verb is itself a finding — it means the
> auto-commit was skipped (check stderr for a `WARNING`).

---

## Phase 0 — Setup: install every dependency first

Complete every step in this section before any walkthrough phase; a fresh
machine needs all of it, and nothing here is assumed to be present already.
OpenKOS is local-first — there are no API keys and no cloud endpoints;
everything runs against a local [Ollama](https://ollama.com) server. (The
prerequisites here match the README's Quickstart section.)

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
| `qwen3:8b` (chat) | `ingest` extraction, `adjudicate`, `suggest-relations`, `suggest-volatility`, `contradictions`, the merged-body reconciliation pass (`merge`, `curate` Identity, `adjudicate --apply`/`--apply-same`), `query` |
| `bge-m3` (embeddings) | `reindex`, every bundle-writing verb's end-of-run index refresh, and the dense-retrieval channel of `query` |

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
openkos --version           # STOP and read this
```

**Verify the version before going any further.** A package index can serve an
older release than the one you meant to test, and nothing downstream will tell
you: every command will run, and you will spend the session filing findings
against code that was superseded weeks ago. Compare `openkos --version` against
the version you intended to test.

If it does not match, install from the repository instead — same engine,
current code:

```bash
uv tool install --force git+https://github.com/jasonssdev/openkos
openkos --version
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

The first LLM call after idle pays a cold-start cost (loading several GB into
memory) that can exceed the request timeout. It surfaces either as
`Ollama … timed out` or, more quietly, as a degraded first result — so warm the
model before every batch, not just the first:

```bash
ollama run qwen3:8b "ok" >/dev/null    # or any command that hits the model
```

Do this even if you intend to test the cold path, so that when you *do* see a
first-file anomaly you know it was not self-inflicted.

---

## Phase 1 — Preflight before anything exists

`doctor` is designed to work **outside** a workspace. Test that first.

```bash
cd ~
openkos doctor ; echo "exit: $?"
```

Expect an `openkos <version>` banner, then the `openkos doctor: checking
environment at <path>` header, a blank line, and then one
`[PASS]`/`[FAIL]`/`[SKIP]` line per check — the banner and the header are not
checks. **`doctor` itself is the authority on which checks exist**; the set
grows as the engine does, so this guide deliberately does not list them.

What to verify instead:

- Outside a workspace, *Workspace initialized* is the only check expected to
  fail or skip.
- Every failing line prints a remediation — the command that fixes it. A `[FAIL]`
  with no actionable next step is a finding.
- The exit code is `1` only if a **critical** check fails; a `[SKIP]` never
  causes exit 1.

**Adversarial sub-test.** Stop Ollama (`Ctrl-C` in its terminal), re-run
`openkos doctor`, confirm the reachability check fails with an actionable
remediation line and the exit code is 1. Then restart Ollama.

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

> **Then a second picker follows.** After the chat model, `init` prints a
> sticky-model note and asks `Installed embedding models:` with its own
> numbered list (`bge-m3` first and `(recommended)`) — answer with a
> **number** here too, or press Enter for `1)`. The note is worth reading:
> the embedding model is **sticky** — changing it in this workspace later
> forces a full corpus re-embed on the next `openkos reindex` — which makes
> this the one `init` answer with a real switching cost. The chat model has
> no such stickiness.

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

There should be **no** `.openkos/` yet (created lazily by the first write that
builds a derived store — an `ingest`, or an explicit `reindex`) and no
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
- **The number of derived objects per source is not fixed.** A short, tightly
  scoped document may yield one; a long transcript may yield a dozen or more.
  There is a backstop against pathological replies, and the run announces it
  when it binds (`N of M extracted object(s) kept (cap reached)`). Judge the
  count against the document, not against a target — and treat a long,
  content-rich source that yields a single object as a finding worth recording.
- **`raw/` is immutable.** A byte-identical re-ingest of an already-extracted
  source is idempotent and **skips extraction** — no model call, nothing
  written, one stderr line saying so ([#773](https://github.com/jasonssdev/openkos/issues/773)).
  Extraction re-runs without any flag only when the previous run left
  retryable debt (`extraction_status: failed`, or a judge-degrade
  `extraction_notice` — the transient-LLM-failure recovery); `--re-extract`
  forces a redo on a healthy source. A *different* file under the same
  basename is refused.
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
openkos ingest /path/to/an/empty/dir/    # expect refusal — nothing matched
openkos ingest /nonexistent.md           # expect refusal
openkos ingest /path/to/some.pdf         # expect exit 0, copied, no extraction
openkos ingest /path/to/first.md         # identical re-ingest: idempotent, skips extraction (#773)
```

A directory with readable text files is **not** a refusal — it is the batch
path (§3.1). Point `ingest` at a folder that also contains non-prose files
(`.DS_Store`, `__init__.py`, a lockfile) and check what the cost gate counts:
what it announces is what it is about to compile into your bundle.

---

## Phase 4 — Check the derived stores, then query

Every bundle-writing verb maintains the retrieval indexes for what it just
wrote: at the end of a successful run it refreshes all three derived stores in
one pass — FTS first, then the graph projection, then the vectors. So the
stores already exist by the time you get here, built by the Phase 3 ingests,
and `reindex` should have nothing to catch up:

```bash
ls -la .openkos/          # vectors.db, fts.db, graph.db — already present
openkos reindex ; echo "exit: $?"    # expect a cheap pass: cache hits, little or nothing re-embedded
```

`reindex` remains the manual rebuild — the fallback when a refresh degraded,
and, with `--force`, the way to ignore the content-hash cache. It is no longer
a step you owe after every write.

> **The rule for the rest of the run: the indexes should already be fresh, so
> a staleness signal is itself the finding.** After an ordinary successful
> write, `openkos status` should list no derived store under *Needs
> attention*, `openkos next` should not recommend `reindex`, and `query`
> should answer without a `warning: derived indexes are stale (...)` line.
> Seeing any of those after a write that reported success means the
> write-time refresh did not happen — record it.
>
> Check stderr before you file, because the refresh is fail-open and says so
> when it degrades: the verb prints `derived-index refresh incomplete -- …;
> run 'openkos reindex' to finish.` in one line naming which stores were
> skipped, and its exit code stays unchanged. A staleness warning that
> *follows* that advisory is the safety net working as designed, not a second
> bug. The reverse is still a finding too: being told to reindex when nothing
> changed.
>
> The refresh runs **once per invocation and only on the success path**. A
> declined confirmation gate, a refusal, or a failed write invalidated nothing
> and must refresh nothing — decline an `ingest` at the prompt and confirm no
> embedding work follows.
>
> One real asymmetry survives, and it is worth knowing before you read a
> result as a bug: the stores are refreshed cheap-first, so the graph is
> rebuilt *before* the vectors are, and its proximity-candidate edges are
> nominated from the **pre-refresh** `vectors.db`. After a content write, the
> document you just added can therefore be missing from the proximity
> candidates until the next content change or a manual `openkos reindex
> --force`. Explicit relations are unaffected — `relate` writes frontmatter,
> and the graph rebuild reads it directly.

```bash
openkos query "a question your corpus can answer"
openkos query "..." --limit 10 --include-deprecated
```

Judge the answer harshly: are the citations real and traceable to your sources,
or does the model invent to fill gaps?

**The fresh-index test** (important):

```bash
openkos ingest /path/to/new.md --auto
openkos query "a question only the new file answers"    # all three channels should already see it — check the stderr retrieval: line
```

No `reindex` in between: that is the point of the test. The `retrieval:` line
on stderr reports the raw FTS and dense hit counts, so it tells you whether
both channels found the new document. A lexical miss on a question whose
wording appears verbatim in the file you just ingested — with no
`refresh incomplete` advisory to explain it — is a finding.

**`query --save`** is the only writing form of query. It auto-commits its three
writes (answer document, `index.md`, `log.md`) like every other mutating verb,
so no manual commit step remains:

```bash
openkos query "a synthesis question" --save
git status                                    # expect clean — auto-committed
head -15 bundle/insights/*.md                 # inspect what was filed
```

On a complete refresh the run ends with `openkos query: the filed insight is
indexed and searchable.` — that sentence is a claim about the derived stores,
so test it: ask a follow-up question the filed answer alone can answer, with
no `reindex` in between, and confirm it is retrievable. The line is printed
**only** when every store refreshed; if a store degraded you get the
`refresh incomplete` advisory instead, and the claim is correctly withheld.

A filed answer is **not** an extracted concept and is stored apart from one.
Three things to verify, because each is a distinct guarantee:

1. It records **provenance** to the objects it was built from — that is what
   lets it be flagged when they change.
2. It **inherits sensitivity** from what it cites. Ask a question whose answer
   must cite a confidential concept and confirm the filed document comes out
   confidential too. A synthesis that is less sensitive than its inputs is a
   serious finding.
3. Later queries can **cite it back**, marked as a synthesis. Save two related
   answers and check whether the second cites the first — a knowledge base that
   compounds on its own output rather than on your sources is a direction worth
   catching early.

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
for any `WARNING -- ... skipped auto-commit` or `derived-index refresh
incomplete`. None of them needs a `reindex` afterwards — each refreshes the
derived stores itself — so treat a stale-index warning after one of these
writes as a finding (Phase 4).

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
git diff "$BEFORE" HEAD -- ':!bundle/log.md'   # expect EMPTY — concept files byte-parity restored
git diff "$BEFORE" HEAD -- bundle/log.md       # expect ONE appended unmerge line — the audit trail
git log --oneline "$BEFORE"..HEAD    # expect two commits: the merge and the unmerge
```

Byte-parity is a claim about the **concept documents**, not the whole tree:
`unmerge` deliberately appends its own entry to `bundle/log.md` (its plan
discloses this before you confirm — `~ log.md (remove this merge's entry,
append unmerge)`), so a bare whole-tree diff against `$BEFORE` shows exactly
that one log line. The concept files themselves must be absent from the diff.

Constraints: `unmerge` reverses merges **in reverse order** — reversing an
earlier merge means unwinding the ones that followed it, and the refusal names
the sequence required. `--to <id>` unwinds the chain in one step after showing
the plan. It restores `index.md`/`log.md` from a pre-merge snapshot,
**discarding any intervening writes** — do not run other writes between a merge
and its unmerge during this test.

Read the plan before you confirm: above a threshold on how much of the merged
body comes from the absorbed side, `merge` also runs a **reconciliation pass**
by default — one model call that rewrites the two bodies as a single coherent
document instead of stapling them — and the plan discloses it as a line of its
own. `--no-reconcile` opts out and keeps the appended form with no model call;
run one merge each way and compare the results.

Worth measuring while you are here: compare the survivor's size before and
after a merge (`wc -l`). A stapled merge should cost roughly the size of the
absorbed document; a reconciled one can legitimately come out shorter, because
removing duplication is the point. A survivor that grows by orders of
magnitude is a serious finding, and so is a reconciled body that lost content
the two inputs carried.

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
openkos contradictions                     # should detect the conflict
openkos reconcile <id_a> <id_b> --winner <id_a>    # directional: id_a supersedes id_b
```

No `reindex` between the `relate` and the `contradictions`: `relate` rebuilds
the graph projection as part of its own run, so the new relation is already
visible to candidate seeding. If `contradictions` cannot see a relation you
just wrote, that is a finding — check stderr for a `refresh incomplete`
advisory on the `relate` before filing it.

Without `--winner`, `reconcile` records a symmetric `reconciled_with` on both. A
conflicting re-resolution is refused (test that too).

```bash
git log -2 --stat        # relate and reconcile each left their own auto-commit
openkos status           # expect nothing under "Needs attention" — both verbs refreshed their own indexes
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
> `query --save`) — but verify with `git status` first: `purge`
> refuses to run on a dirty tree.

### 8.1 `forget` — recoverable

`forget` auto-commits the deletion, so the undo is **`git revert`**, not
`git restore` (there is nothing uncommitted left to restore):

```bash
openkos forget <concept_id>
git log -1 --stat             # the deletion, committed: concept + index.md + log.md
openkos status                # concept is gone
git revert --no-edit HEAD     # undo the ENTIRE forget in one commit
openkos status                # concept is back — but the indexes are now stale
openkos reindex               # a git-side restore is not an openkos write; nothing refreshed it
```

The revert is the expected place to see a staleness warning during this run,
and it is not a finding: `git revert` restores the bundle behind OpenKOS's
back, so no verb ran to refresh the derived stores. `forget` itself refreshes
them.

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
openkos reindex                     # the one place a manual reindex is still required — see below
openkos query "..."                 # confirm dense retrieval works again
```

`purge` is the exception to Phase 4's rule, deliberately. It physically
**deletes** all four derived stores — `.openkos/vectors.db`, `fts.db`,
`graph.db` and `findings.db` — because row-level deletes would leave
recoverable pages, which defeats an erasure. It then rebuilds **only** FTS and
the graph: `vectors.db` is left for a later `openkos reindex` to re-embed
(rebuilding it in-line would make `purge` depend on a running Ollama, which it
must never do), and `findings.db` is left deleted and never rebuilt, because
regenerating a contradiction finding costs LLM calls. So after a purge, expect
`.openkos/` to hold `fts.db` and `graph.db` only, and expect dense retrieval to
stay dark until you reindex.

Verify the erasure is total:

```bash
git log --all -p -- raw/<source-filename>     # expect nothing
grep -r "<distinctive string from that file>" .    # expect nothing
```

---

## Coverage checklist

**`openkos --help` is the authority on what exists**, grouped by what each group
is for. Print it and tick off the list it gives you rather than one copied here,
which would fall behind the engine:

```bash
openkos --help
```

The phases above walk the core loop end to end. Verbs the walkthrough does not
script are still worth a pass of their own — read their `--help`, run them, and
judge whether the output tells you what to do next. That judgement is the point
of this document; a command that runs correctly and leaves you unsure what it
meant is a finding.

One shortcut worth knowing: **`openkos curate` exercises most of the curation
surface in one dependency-ordered session** — identity, structure, metadata and
contradictions, each with its own cost gate you can decline independently.
Running it once tells you more about the product's real ergonomics than
invoking the underlying verbs separately, because it is the path a user
actually takes.

---

## Known issues — expect these, don't re-file them

**Check the open issues before filing.** Listing them here would rot within a
week, so the authority is the tracker itself:

```bash
gh issue list --repo jasonssdev/openkos --label P0 --label P1
gh issue list --repo jasonssdev/openkos --search "<a phrase from what you hit>"
```

If what you hit is already there, add your evidence to that issue — a second
independent reproduction, on different content, is more valuable than a
duplicate report. If it is not there, open one.

Two things worth knowing before you start, because both look like bugs and are
not:

- **Some proposals are deliberately marked uncertain.** Relation directions in
  particular are presented as `(direction model-suggested, unverified)`. That
  wording is the engine telling you it cannot vouch for the direction, not a
  defect. Rejecting those proposals is the intended use.
- **A merged body may still be a staple — and the plan tells you which you are
  getting.** When the absorbed text is a meaningful share of the merged
  document, `merge` plans a reconciliation pass (one model call that rewrites
  both bodies as a single coherent document) and discloses it before you
  confirm. Below that threshold, with `--no-reconcile`, or when the model's
  reply fails the safety checks, the bodies are appended instead and the plan
  says so (`bodies were appended, not reconciled`, with a percentage). Falling
  back to the staple is the designed behaviour — a bad rewrite would be silent
  data loss — not a defect.

A finding that reappears after being closed is a **regression** and deserves a
new issue, not a comment on the closed one.

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
