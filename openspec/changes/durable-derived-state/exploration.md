# Exploration: Where should durable derived state live? (#550 × #572)

Change: `durable-derived-state`
Date: 2026-08-11
Phase: explore (no implementation)

## Answer up front

Two different substrates, one shared location.

Keep the merge ledger's durable snapshot bytes in the bundle (git-tracked,
portable) but stop embedding them in the survivor concept's own frontmatter —
move them to one sidecar file per survivor under a new reserved bundle path
(exact naming is an open product question, see §8).

Give #572's pending-work objects (contradictions, candidate edges, duplicate
groups, volatility proposals) their own store — structurally similar, but NOT
the same file family, because their lifecycle is the opposite: ledger entries
are immutable historical fact; pending-work entries are mutable, retractable,
and go stale.

`.openkos/` is disqualified for BOTH: it is entirely `.gitignore`d
(`.gitignore:254-258`) and `purge`/`reindex` physically delete and rebuild
files under it (`cli/main.py:4240-4266`, `4385-4390`), with no carve-out
possible that does not contradict its documented "rebuildable cache, never
source of truth" contract.

> **Path-notation warning (added during proposal review).** This
> document uses two different `bundle/` prefixes and they are NOT the same thing.
> `bundle/merge.py`, `bundle/references.py` etc. are shorthand for source modules
> under **`src/openkos/bundle/`**. `bundle/concepts/…`, `bundle/index.md`, and the
> proposed `bundle/.state/…` are **runtime workspace paths** in a user's OKF
> bundle — there is no `bundle/` directory in this repository. Relatedly:
> `bundle/concepts/adk.md` at 8370 lines is a *user-workspace measurement reported
> in #550's end-to-end run*, valid evidence of the failure mode but not a citation
> verifiable from this checkout. Downstream phases must keep the two senses apart.

## 1. Current state (file:line)

- **Ledger write:** `bundle/merge.py::plan_merge` (`bundle/merge.py:80-165`)
  builds a `MergeLedgerEntry` (`model/okf.py:512-556`) and appends it to the
  survivor's `merged_from` frontmatter list (`model/okf.py:52` key,
  `merge.py:143-144`). CLI orchestration: `cli/main.py:6574-6585` (prepare),
  `cli/main.py:6676-6747` (`merge_core`, writes survivor LAST at
  `cli/main.py:6727-6734`).
- **Ledger entry fields:** `schema`, `merged_at`, `absorbed_id`,
  `absorbed_snapshot` (full absorbed file), `survivor_before` (full survivor
  file, RETAINING all prior entries — this is what makes growth geometric,
  documented verbatim at `model/okf.py:515-524`), `index_before`, `log_before`,
  `link_rewrites`, `sensitivity_before`/`sensitivity_after`, `relation_rewrites`
  (v2), `provenance_rewrites` (v3). Schema versions at `model/okf.py:118-133`.
- **`unmerge` read-back:** `bundle/merge.py::plan_unmerge`
  (`merge.py:168-208`), LIFO-tail-only, enforced by the
  `entries[-1].absorbed_id == absorbed_id` check (`merge.py:192-197`).

### Confirmed readers of the ledger structure, beyond merge/unmerge

- `bundle/references.py::find_inbound_references` — forget's Phase-A scan,
  reuses `bundle_links.find_inbound_link_rewrites` over raw file text and does
  NOT exclude ledger-embedded snapshot bytes. This is #550 consequence 3.
- `state/reindex.py:270-284` — embeds `raw_bytes.decode("utf-8")` verbatim per
  document, frontmatter and all, then calls `embedder.embed([text])`. This is
  #550 consequence 1 and the whole subject of #554.
- `state/fts.py:220-234` — correctly re-parses via `okf.load_frontmatter` and
  indexes only title/description/tags/body. FTS is unaffected, exactly as #550
  states.
- `resolution/contradiction.py::_merged_body_candidates`
  (`contradiction.py:431-482`) — a genuine consumer not named in the issue
  bodies: it reads `entry.survivor_before` / `entry.absorbed_snapshot` bodies
  directly off each `MergeLedgerEntry` to build intra-document contradiction
  candidates, one per ledger entry per survivor. Any relocation MUST keep the
  per-entry snapshot bytes reachable, not merely their presence/absence.
- `sensitivity.py::merged_content_blocked` (`sensitivity.py:181+`) — **see §1a.
  This is the constraint the first pass under-weighted.**
- No reader in `doctor`, `next`, or `status` inspects `merged_from` today. The
  ledger-consistency and stale-finding checks proposed below are new surface,
  not a refactor of existing checks.

### 1a. The sensitivity gate — a privacy constraint on relocation

`sensitivity.merged_content_blocked` (shipped for #409) exists **because** the
ledger embeds full historical document bodies. Its docstring
(`sensitivity.py:195-225`) states the reason directly: a survivor's
`MergeLedgerEntry.absorbed_snapshot` embeds a full historical body that may have
been written at a HIGHER sensitivity than the survivor's current value now
reads. The induction "current sensitivity dominates every absorbed body" holds
across merges (because `combine_sensitivity` only ever raises) but **breaks
across `set-sensitivity`**, which can deliberately lower a concept's
sensitivity (ADR-0008).

The gate therefore ranks fail-closed over three values per entry —
`current_sensitivity`, `entry.sensitivity_before`, `entry.sensitivity_after` —
and its docstring is emphatic that it MUST be called once **per ledger entry**,
never once per survivor.

Two consequences for this change, both of which any proposal must carry:

1. **The relocated store must keep per-entry frozen sensitivity fields
   reachable at judge time.** A sidecar that flattens or summarizes entries
   breaks a shipped privacy gate.
2. **The sidecar becomes a new PII-bearing surface in its own right.** Today
   the confidential historical bodies sit inside a `.md` file that `forget`,
   `purge`, and the sensitivity scanners already walk. A sidecar outside
   `bundle/**.md` is excluded from those walks *by construction* — which is
   exactly what fixes #550 consequence 3, and exactly what could silently drop
   confidential bytes out of every privacy sweep. This is adjacent to open
   issue **#571** (set-sensitivity does not contain replicated PII in derived
   objects) and must not be treated as a side effect.

### `.openkos/` today

`config.py:284-316` — holds `vectors.db`, `fts.db`, `graph.db` only. Entirely
`.gitignore`d (`.gitignore:254-258`). `reindex` is read-only over the bundle and
write-only to those three files. `purge` physically `unlink`s all three and
best-effort rebuilds FTS and graph, deliberately leaving `vectors.db` deleted
(`cli/main.py:4240-4266`, `4385-4390`). Nothing durable exists there today: it
is 100% disposable cache by design and by every writer's contract.

## 2. ADR-0002 — what it actually committed to

`docs/adr/0002-reversible-merge-ledger.md` explicitly decided to embed verbatim
snapshots INSIDE the survivor's OKF frontmatter as "an ordinary unknown key".
This was deliberate, chosen against three named alternatives (git-only
breadcrumb, redirect-stub tombstone, hash-referenced snapshot), all rejected for
weaker reversibility guarantees. The ADR foresaw frontmatter growth ("acceptable
— data, not a new type") but did NOT foresee:

- `index.md` / `log.md` being embedded on **every** merge — they are touched by
  every merge, so their inclusion in every entry is what turns linear growth
  geometric;
- `reindex.py` feeding raw frontmatter text to the embedder (#554 / #550
  consequence 1 postdates this ADR);
- `forget`'s inbound-reference scanner not excluding ledger bytes (#550
  consequence 3).

**Architectural commitment — must survive any relocation:** LIFO-tail-only
reversal; byte-for-byte round-trip parity as the contract; ledger written and
read only via `okf.dump_frontmatter` / `load_frontmatter`, never hand-spliced;
survivor-write-before-absorbed-delete ordering for crash safety.

**Incidental implementation — relocatable without violating the ADR's intent:**
storing the snapshot payload specifically inside the survivor's own frontmatter
key, as opposed to a separate but still git-tracked, still bundle-owned file the
survivor references by id.

**Verdict:** relocating the storage location (not the reversal semantics) does
not contradict ADR-0002's core reasoning — "markdown text… never the graph
store" as the durable substrate — because a sidecar is still plain text in the
git-tracked bundle. It DOES require a new ADR or an explicit amendment, because
ADR-0002's Decision text is normatively specific about embedding in the
survivor's frontmatter, and downstream ADRs built on that assumption.

**Other constraining ADRs:** ADR-0005 (merge edge rewiring, v2 ledger
`relation_rewrites`) and ADR-0011 (provenance retarget on merge, v3 ledger
`provenance_rewrites`) both extend the same embedded-frontmatter contract. Both
need at minimum a cross-reference update; their v2/v3 schema fields carry over
unchanged regardless of where the entry list is stored. ADR-0008 (human
sensitivity override) is what makes §1a's gate necessary and is therefore also
in scope for cross-referencing.

## 3. The `.openkos/` vs bundle tension, resolved with evidence

Disqualifying facts, not merely "rebuild risk":

- `.openkos/` is `.gitignore`d outright — nothing under it is ever git history,
  so it cannot hold the durable record for a git-portable OKF bundle. A
  `git clone` would lose all merge history and all pending findings.
- `purge` — a *privacy* operation, unrelated to merge or curate — physically
  deletes its contents. Durable state there would be silently destroyed by an
  unrelated command.
- No current writer contract (`reindex_gate`, `open_derived_connection`,
  `purge`'s db-list) has a carve-out for a fourth, non-rebuildable file in that
  directory. Adding one creates a structural exception every future
  `.openkos/`-touching change must remember not to break.

**"Survives a derived-index rebuild" in this codebase concretely means:**
survives `openkos reindex` (rebuilds `vectors.db` / `fts.db` / `graph.db` only)
AND survives `openkos purge` (deletes those three plus scrubs history for
privacy). Anything that must survive both cannot live under `.openkos/` at all
under current contracts.

## 4. Candidate storage options

| Option | Durable across rebuild | Git-friendly / diffable | `forget` scan impact | Retrieval impact | Serves #572's five requirements | Migration cost |
|---|---|---|---|---|---|---|
| **A. Status quo** (embed in survivor frontmatter) | Yes, but self-falsifying (#550-2) | No — one file becomes thousands of lines | Broken today (phantom refs) | Broken today (truncation) | n/a | None (shipped) |
| **B. Sidecar per survivor in bundle** | Yes | Yes — isolates ledger diffs from concept diffs | Fixed: scanners walk `bundle/**.md`, sidecar excluded by construction (**but see §1a.2**) | Fixed: reindex/FTS walk `.md` only | n/a (ledger shape) | Moderate |
| **C. Single append-only ledger file** | Yes | Partial — one growing file, less diffable per entry | Fixed | Fixed | n/a | Moderate + needs id-indexed lookup |
| **D. SQLite inside the bundle dir** | Yes if tracked | No — binary diffs defeat the bundle's stated git-diffable promise (`docs/knowledge-object-model.md:316`) | Fixed | Fixed | Structurally good, but violates markdown-first | High; binary merge conflicts |
| **E. Hybrid — durable record in bundle + rebuildable index in `.openkos/`** | Yes (source of truth in bundle) | Yes (source stays text) | Fixed | Fixed | **Best fit** — ranked queries for `next`/`status`, reconstructable from the tracked files | Highest short-term, cleanest long-term |

For #572 specifically, the pending set needs ranking, decline-tracking,
pagination and staleness — a pure flat-file store makes ranking and pagination
awkward without an index. Option E mirrors the split `reindex` already uses for
FTS and vectors, and matches the `state/derived.py` pattern the codebase
already trusts.

## 5. One mechanism or two — argued

**Two are needed**, not because unification is impossible but because it costs
correctness.

- The merge ledger is **immutable history**: an entry records what the bundle
  looked like before one merge, and must never be edited — only appended to (a
  new merge) or reversed (unmerge pops the tail). One writer, a small closed
  set of readers.
- Pending work is a **mutable, rankable, retractable working set**: a finding
  can be declined but retained, re-surfaced, superseded by a fresher run over
  changed inputs, and paginated past a display cap. It needs a status field
  (`open` / `declined` / `stale` / `applied`) for which the ledger has no
  analogue — a merge is never "declined"; it either happened or was reversed.

Forcing both into one schema means either the ledger grows staleness machinery
it will never use, or pending work loses append-only simplicity it does not
need.

They CAN share a **location convention** — both under a bundle-relative reserved
directory, both excluded from the concept walk, both under the same "durable in
git, indexed in `.openkos/`" pattern — without sharing a **format or schema**.

## 6. Migration

#550 consequence 2 (later merges rewrite links inside earlier snapshots) means
**existing embedded snapshots may already be internally inconsistent**. A
mechanical migration that moves the current `merged_from` list to a sidecar
unchanged would durably enshrine already-corrupted snapshots — converting a
self-healing, git-revertible bug into a permanent one.

Recommended shape:

- `doctor` gains a check: *merge ledger entries free of post-merge mutation*.
- For a clean ledger (single merge, or merges that did not touch each other's
  index/log region), a repair verb extracts entries out of frontmatter into the
  new location, verbatim.
- For an already-corrupted ledger (multi-merge, `adk.md`-shaped), the correct
  migration is the one **#550's own "Recovery for affected users" section
  already states** — verified: `git reset --hard <first-merge>~1` followed by
  `openkos reindex`, since every operation auto-commits. Discard and replay
  under fixed code; do not repair old bytes.
- Reversibility of any merge made on a pre-fix version should be documented as
  **not guaranteed**, because `unmerge` on an old entry may already be restoring
  bytes that never existed.

## 7. Recommended slicing (auto-chain, 2000-line review budget)

- **Slice 1 — P0, unblocks #550 standalone, no dependency on #572.** Move
  `merged_from` entries out of survivor frontmatter into a per-survivor sidecar
  under a reserved bundle path (Option B). Update `plan_merge` / `plan_unmerge`
  I/O, `references.py`'s scan, `contradiction.py::_merged_body_candidates`'s
  read path, `sensitivity.merged_content_blocked`'s per-entry access (§1a), and
  the privacy-sweep coverage of the new path. Split for review budget:
  - **1a** — ledger relocation + merge/unmerge + sensitivity-gate re-wiring.
  - **1b** — `reindex.py` embed-text composition matched to `fts.py`'s
    title/description/tags/body scheme (closes #554; verify and close it
    explicitly rather than assuming), plus the `doctor` check and migration
    note from §6.
- **Slice 2 — P1, #572 groundwork.** Pending-work durable store per Option E,
  independent format from Slice 1. `next` / `status` read it.
- **Slice 3 — P1, #572 wiring.** `curate`'s contradictions, suggest-relations
  and duplicate-group stages write into Slice 2's store instead of printing and
  discarding; decline/retract, staleness, pagination.
- **Slice 4 — P2, #562.** `unmerge --to <id>` with a printed unwinding plan.
  Cleaner against the sidecar's per-entry structure than against embedded
  frontmatter, so schedule it after Slice 1.

## 8. Open questions — product decisions, not engineering

1. ~~**Reserved-path naming** for the sidecar location.~~ **DECIDED
   2026-08-11: `bundle/.state/{ledger,pending}/`** — one reserved directory
   under the bundle, two subdirectories with independent formats, per §5's
   "shared location convention, separate schemas". Rationale: the bundle stays
   portable as one unit (a `git clone` carries merge history and reversibility
   with it), the leading dot keeps it out of Obsidian's browsable tree, and
   privacy sweeps get **one** reserved path to cover rather than one per state
   type — which directly addresses §1a.2's risk that the relocated snapshots
   fall out of every sweep. Rejected: two sibling dotted directories (two
   independent paths every future privacy-touching change must remember), and a
   location outside `bundle/` (copying `bundle/` alone would lose the ledger,
   breaking OKF's "everything is a bundle" portability promise).
2. **New ADR vs amendment in place** for ADR-0002's storage clause — determines
   how much prior ADR text is marked superseded.
3. **Declined findings (#572):** visible via a CLI surface (an "undecline"
   verb), or final until the next `curate` recomputes fresh?
4. **Pre-fix unreversible merges (§6):** surfaced by `doctor` as a hard warning,
   or left to a CHANGELOG note?

## Verification note

Every file:line citation above was re-checked against the working tree at
`2a03a56` before this artifact was written. All confirmed. Two corrections were
applied to the first pass:

- A summary-level citation of `merge.py:519-524` was wrong — `merge.py` is 208
  lines; the cited text is `model/okf.py:515-524`. Corrected here.
- `sensitivity.py` was flagged by the first pass as "not fully read". It was
  read; §1a is new material, and it changes Slice 1's scope.
