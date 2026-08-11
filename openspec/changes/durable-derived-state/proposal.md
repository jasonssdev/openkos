# Proposal: Relocate the merge ledger to `bundle/.state/ledger/`

> **Correction to the exploration (read first).** Exploration §Answer-up-front and the
> orchestrator brief both cite `bundle/concepts/adk.md` at ~8370 lines as measured evidence.
> **That file does not exist in this repository** — there is no `bundle/` directory here at all
> (`Glob bundle/**/*.md` → no matches). The 8370-line figure is a *user-workspace observation
> reported in issue #550*, not a repo artifact. It is still valid evidence of the failure mode;
> it is not a citation anyone can verify from this checkout. Every other exploration citation
> was re-verified and holds, with one path correction: the symbols live under `src/openkos/`
> (`src/openkos/bundle/merge.py:80,168`, `src/openkos/bundle/references.py:90`,
> `src/openkos/resolution/contradiction.py:431`, `src/openkos/sensitivity.py:181`).
> A second issue-body fact could not be re-read here: this executor has no Bash tool, so
> `gh issue view 550/572/562/554/571` was not run. Issue content below is carried from the
> validated exploration, not re-fetched.

## Intent

Every `openkos merge` appends a `MergeLedgerEntry` (`src/openkos/model/okf.py:512-556`) into the
survivor concept's own `merged_from` frontmatter, and that entry embeds the *entire* survivor file
including all prior entries (`model/okf.py:515-524`). Growth is therefore geometric, not linear:
after a handful of merges a concept file is overwhelmingly historical snapshot bytes, of which only
a few dozen lines are the concept. Three live consequences follow. Retrieval breaks —
`state/reindex.py:270-284` embeds `raw_bytes.decode("utf-8")` verbatim, so the embedding is mostly
history and the concept truncates out (#554). Snapshots corrupt each other — later merges rewrite
links *inside* earlier embedded snapshots, so the ledger no longer round-trips. And `forget`'s
inbound-reference scan (`bundle/references.py:90`) counts ledger-embedded bytes as real references,
producing phantom inbound links. This proposal cuts the active data corruption by moving the ledger
out of concept frontmatter into a reserved, still-git-tracked bundle path.

## Scope

### In scope — Slice 1 only (P0, no dependency on #572)

| Slice | Work | Forecast (add+del, incl. tests) |
|---|---|---|
| **1a** | Ledger relocation to `bundle/.state/ledger/`; `plan_merge`/`plan_unmerge` I/O; per-entry sensitivity-gate rewiring; `contradiction.py` read path; `references.py` scan exclusion; privacy-sweep coverage of the new path | ~700–900 |
| **1b** | `reindex.py` embed-text composition matched to `fts.py`'s title/description/tags/body scheme (closes #554 — verify and close explicitly); new `doctor` check; migration/repair verb | ~500–700 |

Modules that change:

| Path | Impact | Change |
|---|---|---|
| `src/openkos/bundle/merge.py` | Modified | `plan_merge` writes entries to the sidecar; `plan_unmerge` reads the tail from it |
| `src/openkos/model/okf.py` | Modified | `MergeLedgerEntry` schema unchanged; frontmatter key handling moves out |
| `src/openkos/bundle/references.py` | Modified | Inbound-reference scan no longer sees ledger bytes (fixes #550-3) |
| `src/openkos/resolution/contradiction.py` (`_merged_body_candidates`) | Modified | Reads per-entry `survivor_before`/`absorbed_snapshot` from the new store |
| `src/openkos/sensitivity.py` (`merged_content_blocked`) | Modified | Same per-entry contract, new source of entries |
| `src/openkos/state/reindex.py` | Modified (1b) | Composed embed text, not raw bytes |
| `doctor` check + repair verb | New (1b) | Ledger-integrity check; extraction/replay migration |
| **New** `bundle/.state/ledger/` | New | One sidecar per survivor, git-tracked, plain text via `okf.dump_frontmatter` |

### Out of scope (non-goals)

- **#572's pending-work store** (`bundle/.state/pending/`) — Slices 2 and 3. Reserved by this
  proposal's directory layout, deliberately *not* built here. It is a mutable, rankable,
  retractable set with an `open/declined/stale/applied` lifecycle; the ledger is immutable append
  history. Shared location convention only — **not** a shared format or schema.
- **#562 `unmerge --to <id>`** — Slice 4, scheduled after this change because it is cleaner against
  per-entry sidecar structure than against embedded frontmatter.
- Any change to `.openkos/` — disqualified for durable state (exploration §3): it is `.gitignore`d
  and `purge` physically deletes its contents.
- Ranked/indexed query over ledger entries. Flat text is sufficient for LIFO-tail reversal.

## Invariants that must survive (ADR-0002)

1. **LIFO-tail-only reversal** — `unmerge` pops the last entry; the
   `entries[-1].absorbed_id == absorbed_id` guard (`merge.py:192-197`) stays.
2. **Byte-for-byte round-trip parity** — merge→unmerge restores exact prior bytes.
3. **Ledger I/O only via `okf.dump_frontmatter` / `load_frontmatter`** — never hand-spliced text.
4. **Survivor-write-before-absorbed-delete ordering** for crash safety
   (`cli/main.py:6727-6734`). With a sidecar this becomes a three-step order:
   **sidecar write → survivor write → absorbed delete**.

## Privacy constraint — the highest-risk part of this change

`sensitivity.merged_content_blocked` (`src/openkos/sensitivity.py:181`) exists *because* the ledger
embeds full historical bodies that may have been written at a higher sensitivity than the survivor
now reads. The induction "current sensitivity dominates every absorbed body" holds across merges
(`combine_sensitivity` only raises) but **breaks across `set-sensitivity`**, which can deliberately
lower a concept (ADR-0008). Two hard requirements:

1. **Per-entry frozen fields stay reachable at judge time.** The gate ranks fail-closed over
   `current_sensitivity`, `entry.sensitivity_before`, `entry.sensitivity_after`, and MUST be called
   **once per ledger entry**, never once per survivor. A sidecar that flattens, summarizes, or
   lazily-truncates entries breaks a shipped privacy gate.
2. **Two different walks, opposite directions.** Moving snapshots outside `bundle/**.md` removes
   them from the concept walk — that is precisely the fix for #550 consequence 3, and precisely a
   new way to drop confidential bytes out of every privacy sweep.

**Decision: privacy-sweep coverage of `bundle/.state/ledger/` is IN SCOPE for Slice 1a, not
deferred to #571.** Rationale: this change *creates* the gap. Shipping the relocation without the
sweep is a net privacy regression authored by us, whereas #571 (set-sensitivity does not contain
replicated PII in derived objects) is a pre-existing, separate defect. Concretely:

- `references.py`'s inbound-reference scan **excludes** the ledger (that is the bug fix).
- `forget`, `purge`, and the sensitivity scanners **include** `bundle/.state/ledger/` (that is the
  regression guard).

Do not collapse these into one "walk the bundle" predicate. Note also that `doctor` is spec-bound
read-only (`openspec/specs/doctor-command/spec.md` — "Doctor Is Read-Only"), so the migration
**repair** is a separate verb; `doctor` only detects.

## Migration

| Ledger state | Action |
|---|---|
| Clean (single merge, or merges that never touched each other's index/log region) | Repair verb extracts entries out of frontmatter into `bundle/.state/ledger/` **verbatim** |
| Corrupted (multi-merge, `adk.md`-shaped) | Reset and replay: `git reset --hard <first-merge>~1` then `openkos reindex` — the recovery #550 itself proposes, viable because every operation auto-commits |
| Detection | New `doctor` check: *merge ledger entries free of post-merge mutation* |

**Reversibility of merges made on pre-fix versions is NOT guaranteed** and must be documented as
such (CHANGELOG + `doctor` remediation text). `unmerge` on an old entry may already be restoring
bytes that never existed. A mechanical verbatim migration of a corrupted ledger would convert a
self-healing, git-revertible bug into a permanent one — so the repair verb must **refuse** to run on
a ledger the `doctor` check flags.

## Assumptions resolved by the user (2026-08-11)

The proposal question round could not run under `auto` execution mode. The three
assumptions it would have raised were resolved by the orchestrator and the user
afterwards; they are now decisions, not assumptions.

1. **The repair verb is in scope** (Slice 1b), not manual-only migration with
   `doctor` remediation text as the whole answer. Rationale: hand-migration for a
   P0 this project authored is user-hostile.
2. **`openkos merge` on a `doctor`-flagged ledger refuses, with a `--force`
   escape.** Rationale: merging onto a corrupted ledger deepens damage that is
   still git-revertible today and pushes the clean reset point further away, so
   the default must protect. But `curate` merges in batches and a hard wall mid
   session is punitive, and this project already uses exactly this
   refuse-plus-`--force` shape in `forget` for the same dilemma — follow the
   existing convention rather than inventing a second one. The refusal message
   must print both remediation paths (repair verb for clean ledgers,
   reset-and-replay for corrupted ones) and state that pre-fix reversibility is
   not guaranteed.
3. **`bundle/.state/` is git-tracked and inspectable, but not hand-edited.** The
   leading dot signals "engine-owned", not "secret". Docs should say so; no
   mechanism enforces it.

## Open design input — atomicity is weaker than ADR-0002 assumed

Raised by the orchestrator during proposal review; **not yet answered, and
`sdd-design` owns it.**

ADR-0002's crash-safety invariant (survivor-write-before-absorbed-delete) was
formulated for a world where the ledger entry and the survivor's own bytes were
**one file write** — they could not disagree, by construction. A sidecar splits
them into two writes, and the three-step order in "Invariants" above
(sidecar → survivor → absorbed-delete) does not restore that property: a crash
between step 1 and step 2 leaves a ledger entry describing a merge the survivor
never recorded.

The design phase must re-derive crash safety for a two-file world rather than
carry the old invariant forward unchanged. It should state explicitly which
partial states are reachable, which are self-healing, and whether the new
`doctor` ledger-integrity check is also the detector for a torn write.

## ADR plan

**Decision: a new ADR (`docs/adr/0013-*.md`, next free number) that supersedes ADR-0002 in part —
its storage clause only. Not an amendment in place.**

Reasoning: `openspec/config.yaml` `rules.archive` is explicit and non-negotiable — "ADRs are
immutable once accepted: never rewrite Context, Decision, Consequences, or Alternatives during
archive… that is a new ADR superseding this one, never edit the old one." ADR-0002's Decision text
is normatively specific about embedding in the survivor's frontmatter and was chosen against three
named alternatives; editing it would erase the record of that reasoning. ADR-0002 gets its
**Status** line marked superseded-in-part, and nothing else. Created with status `Proposed`; archive
flips it to `Accepted`.

Required cross-references in the new ADR:

- **ADR-0005** (merge edge rewiring, v2 `relation_rewrites`) — schema field carries over unchanged.
- **ADR-0011** (provenance retarget on merge, v3 `provenance_rewrites`) — same.
- **ADR-0008** (human sensitivity override) — the reason the per-entry gate exists at all.

## Capabilities

### New Capabilities

- None. The reserved-path convention is a storage decision recorded in the new ADR, not a new
  user-visible capability.

### Modified Capabilities

- `entity-resolution-merge`: "Reversibility Ledger (`merged_from`)" and "Unmerge Achieves Round-Trip
  Parity" — storage location changes; LIFO and parity requirements unchanged.
- `sensitivity-aware-llm`: per-entry gate reads from the relocated store; "Walk-Incompleteness
  Observability" extends to `bundle/.state/`.
- `forget-command`: inbound-reference scan excludes ledger bytes; deletion/redaction sweep includes
  `bundle/.state/ledger/`.
- `privacy-purge`: sweep covers `bundle/.state/`.
- `reindex-command` (1b): composed embed text replaces raw-bytes embedding.
- `doctor-command` (1b): new read-only ledger-integrity check.
- `contradiction-detection`: `_merged_body_candidates` source changes; per-entry semantics unchanged.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Migrating a corrupted ledger verbatim permanently enshrines bad data** — today the bug is git-revertible; after migration it is durable fact | Med | **Critical** | Repair verb hard-refuses on any ledger the `doctor` check flags; reset-and-replay is the only path for corrupted ledgers; document non-guaranteed pre-fix reversibility |
| Relocated snapshots drop out of `forget`/`purge`/sensitivity sweeps | Med | High | Sweep coverage is in Slice 1a scope, not deferred; explicit test per sweep |
| Per-entry sensitivity gate degraded to per-survivor during refactor | Med | High | Test asserting the gate is invoked once per entry; mutate the call site to confirm the test fails |
| Round-trip parity lost via new file-ordering | Low | High | Sidecar-write → survivor-write → absorbed-delete ordering; parity test on merge→unmerge bytes |
| `bundle/.state/` painted into a corner by ledger-shaped assumptions | Low | Med | Pending store gets an independent format under `bundle/.state/pending/`; nothing in 1a may assume a single shared schema |
| #554 assumed closed rather than verified | Med | Low | 1b verifies and closes #554 explicitly |

## Rollback Plan

Slice 1a and 1b each land as separate PRs on a feature-branch chain. Rollback is `git revert` of the
slice PR: the ledger format on disk is plain text produced by `okf.dump_frontmatter`, and the repair
verb is opt-in and one-way-guarded, so a revert leaves any workspace that never ran the repair verb
byte-identical. Workspaces that *did* run it need the inverse: re-embed entries into frontmatter, or
`git reset --hard` to the pre-repair auto-commit. Document that second path in the repair verb's own
output before it writes.

## Dependencies

- None blocking. Explicitly **not** dependent on #572.
- Sequencing: #562 (Slice 4) should land after this change.

## Success Criteria

- [ ] No `merged_from` key remains in concept frontmatter after merge; entries live under `bundle/.state/ledger/`.
- [ ] merge → unmerge produces byte-for-byte parity on the survivor and absorbed files.
- [ ] `merged_content_blocked` is invoked once per ledger entry, proven by a test that fails when the call is hoisted per-survivor.
- [ ] `forget`'s inbound-reference count no longer includes ledger-embedded bytes (#550-3 closed).
- [ ] `forget`, `purge`, and sensitivity scanners each cover `bundle/.state/ledger/`, each with a test.
- [ ] Reindex embed text matches `fts.py`'s title/description/tags/body scheme; #554 verified and closed.
- [ ] `doctor` reports post-merge-mutated ledger entries and prints actionable remediation; `doctor` stays read-only.
- [ ] Repair verb refuses to migrate a flagged ledger.
- [ ] ADR-0013 created (`Proposed`) superseding ADR-0002's storage clause, cross-referencing ADR-0005, ADR-0011, ADR-0008.
