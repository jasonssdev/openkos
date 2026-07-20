# Design: Entity Resolution — Confirm-Gated Reversible Merge (slice 3)

## Technical Approach

Mirror `forget` (`cli/main.py:715-841`): Phase A pure preview → confirm gate → Phase B write, doubled for two objects. Keep the layering clean: `merge`/`unmerge` **WRITE the bundle**, so all domain logic lives in the **canonical** layer (`model`, `bundle`), never derived (`graph`, `resolution`). CLI verbs stay thin, reusing `_resolve_concept_path` (676-712), `bundle_index`, `bundle_log`, `fsio.write_atomic`/`remove_file`. Reversibility is a self-contained ledger embedded in the survivor's frontmatter; `unmerge` restores from verbatim snapshots. Round-trip parity (merge→unmerge = byte-identical pre-merge bundle) is the contract, verified by a property test.

## Architecture Decisions

| Decision | Choice | Alternatives rejected | Rationale |
|---|---|---|---|
| Home | No new `lifecycle/` package. `combine_sensitivity`+`build_merged_document` in `model/okf.py`; new `bundle/merge.py` (pure `plan_merge`/`plan_unmerge`) + `bundle/links.py` (rewrite); verbs in `cli/main.py` | Top-level `lifecycle/` package; all-inline in `cli/main.py` | Merge is a bundle mutation composed of existing bundle primitives — canonical layer is its correct home. A new package is empty scaffolding (rules.tasks). Builders belong with `build_concept`. |
| Reversibility (parity is lossy to invert) | Ledger stores **verbatim pre-merge snapshots** of absorbed, survivor, `index.md`, `log.md`; `unmerge` writes them back byte-for-byte | Reparse dict→re-dump (reserialization drift); deterministic inversion | Sensitivity recompute + provenance-dedup are **lossy** — cannot be inverted. Verbatim snapshot is the only parity-safe path. **Requires snapshotting the survivor, not just the absorbed object** (surfaced below). |
| Link rewrite | `bundle/links.py` owns its `_LINK_RE` + `_mask_fenced_code_blocks` copied from `graph/sqlite_graph.py:41-82` | Import graph helpers | Canonical MUST NOT import derived (`graph`). Same duplication precedent as `bundle/index.py:145` (#922). |
| Serialization | Route ledger through `okf.dump_frontmatter`/`load_frontmatter` (symmetric YAML); never hand-splice | Manual text splice | Symmetric dumps/loads is lossless regardless of scalar style; block-literal `|` is an optional readability refinement, not a correctness dependency. |

**Reconciliation flag (serves, does not contradict, the locked contract):** round-trip parity *logically requires* a **survivor pre-merge snapshot** because sensitivity high-water-mark is irreversible. The locked contract names only the absorbed snapshot; this design adds `survivor_before` (+ `index_before`/`log_before`) as a necessary consequence of the parity contract. Spec must agree.

## Ledger schema (`merged_from`, a **list** — supports sequential pairwise merges, LIFO)

Each entry: `schema`, `merged_at`, `absorbed_id`, `absorbed_snapshot` (verbatim text), `survivor_before`, `index_before`, `log_before` (verbatim), `link_rewrites: [{file, old_link, new_link}]`, `sensitivity_before`/`sensitivity_after` (audit). Verbatim snapshots are the **exact bytes read** from disk → byte parity guaranteed.

`survivor_before` is the survivor's **full verbatim bytes immediately prior to THIS merge's write**, explicitly **RETAINING any prior `merged_from` entries** from earlier merges — it excludes ONLY the new entry being created by this merge (which does not exist yet at snapshot time). It does NOT strip the whole `merged_from` key. This is what makes sequential pairwise merges reverse losslessly in LIFO order: each `survivor_before` already carries the ledger state left by every earlier merge.

## `combine_sensitivity`

`SENSITIVITY_ORDER = ("public","private","confidential")`; `combine_sensitivity(a, b) -> str` returns `SENSITIVITY_ORDER[max(rank(a), rank(b))]`. `rank`: missing/blank → `private`; present-but-unknown/non-str (malformed) → `confidential` (fail-closed to most restrictive). Lives in `model/okf.py`; reads `metadata.get("sensitivity")` (today only inherited verbatim, `cli/main.py:318,542`).

## Frontmatter conflict rule (pinned)

Survivor-wins scalars (`type/title/description/status/version/resource`); union-list (`tags`, `provenance`, order-preserving dedup); `freshness`+`timestamp` from the most-recent-`timestamp` doc; `sensitivity` **recomputed** via `combine_sensitivity`. Body: survivor body + delimited `## Merged content ({absorbed_id})` + absorbed body.

## Data Flow

    merge S A: Phase A plan_merge(texts) → MergePlan(new_index, new_log,
      rewritten_docs, merged_survivor+ledger, snapshots, preview) [NO writes]
      → confirm gate → Phase B: write index, log, rewritten docs,
      SURVIVOR (ledger persisted) ... then delete ABSORBED **last**
    unmerge S A: read the LIFO tail ledger entry → require A == tail
      `absorbed_id` (else clean error, no write) → restore
      survivor(`survivor_before`)/absorbed(`absorbed_snapshot`)/index
      verbatim, restore `log_before` then append one unmerge audit line +
      reverse link substitutions [fail-closed if a file drifted]

`unmerge <survivor-id> <absorbed-id>` is **two-arg, LIFO-enforced**: it reverses ONLY the most-recent unreversed `merged_from` entry (the tail), and the supplied `absorbed-id` MUST equal that tail entry's `absorbed_id`, else the command refuses with a clean error and no write. This is a safety check on a destructive reverse — reversing a non-tail entry is unsafe due to nested snapshots / overlapping rewrites. Every file is restored to byte parity; `log.md` is restored from `log_before` and THEN gains a single appended unmerge audit line, so the append-only audit trail net-grows by the merge+unmerge record while all files match their pre-merge bytes elsewhere.

**Bounded reversal note:** inbound-link-rewrite reversal MUST be bounded to the specific recorded occurrences (each recorded `{file, old_link, new_link}`), NOT a blind replace-all. A coincidental pre-existing identical `[text](/survivor-id.md)` link elsewhere — one this merge never created — MUST never be flipped; only the exact rewrites the ledger recorded are reversed.

Phase B ordering invariant: the survivor (carrying the snapshot) is written **before** the absorbed file is deleted — a crash never destroys the only copy. Catalog-first preserves `forget`'s "index never references a missing file".

## File Changes

| File | Action | Description |
|---|---|---|
| `model/okf.py` | Modify | `SENSITIVITY_ORDER`, `combine_sensitivity`, `build_merged_document`, ledger encode/decode, `MERGED_FROM_KEY` |
| `bundle/merge.py` | Create | pure `plan_merge`/`plan_unmerge` + `MergePlan`/`UnmergePlan` (text-in/out) |
| `bundle/links.py` | Create | `rewrite_inbound_links`/`reverse_link_rewrites` (own fence-mask + regex, anchor-preserving) |
| `cli/main.py` | Modify | `merge`/`unmerge` verbs, Phase A/B, reuse `_resolve_concept_path` |
| `docs/adr/0002-*.md`, `0003-*.md`, `README.md` | Create/Modify | ledger + sensitivity ADRs (Proposed) |
| `docs/knowledge-object-model.md`, `docs/cli.md` | Modify | merge/unmerge section (apply phase) |

## Testing Strategy

| Layer | What | How |
|---|---|---|
| Unit | `combine_sensitivity` table (all pairs, missing, malformed, non-str) | parametrized |
| Unit | fence-masked, anchor-preserving rewrite; no substring over-match | text fixtures |
| Unit | `plan_merge` frontmatter/body/provenance/sensitivity combine | text-in/out |
| CLI | declined-confirm writes **nothing**; non-TTY refusal; self-merge / nonexistent / reserved refusal; partial-write recoverability | `CliRunner`, no Ollama (deterministic) |
| Property | **merge→unmerge byte-identical** across every bundle file (+ absorbed restored) | round-trip snapshot compare |
| Property | **sequential parity**: `merge(A,B)` → `merge(A,C)` → `unmerge(A,C)` → `unmerge(A,B)` restores the bundle to its original pre-any-merge byte state (LIFO order), proving multi-merge reversibility — not just single merge→unmerge | round-trip snapshot compare |

## Threat Matrix (focused — no shell/subprocess/routing/PR automation → those rows N/A)

| Threat | Applicable | Behavior / RED test |
|---|---|---|
| Path-traversal delete via concept-id | Yes | `_resolve_concept_path` guards both ids (abs/`..`/reserved/nonexistent) before any write |
| Self-merge (S==A) | Yes | refuse, exit 1 |
| Unmerge restore collision (absorbed path exists) | Yes | refuse, exit 1 |
| Non-atomic partial write | Yes | survivor(ledger)-before-delete ordering → benign, git-recoverable |
| Link-file drift before unmerge | Yes | exact-substring reverse; if `new_link` absent → fail-closed refuse |

## Migration / Rollout

No data migration. Feature-branch chain: **U1** sensitivity HWM (+ADR-0003) · **U2** merge core+ledger (+ADR-0002) · **U3** inbound-link rewrite · **U4** merge CLI+gate · **U5** unmerge+parity test. 400-line budget risk **Medium-High** on U2/U5 — chained PRs recommended; split U2 if forecast exceeds. STOP before merge-to-main (destructive slice, human checkpoint).

## Open Questions

- [ ] Spec must adopt `survivor_before`/`index_before`/`log_before` snapshots (parity requirement).
- [ ] One combined ADR vs. two (0002 ledger + 0003 sensitivity) — drafted as two; reviewers may merge.
