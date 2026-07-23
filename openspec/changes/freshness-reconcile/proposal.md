# Proposal: freshness-reconcile (S4 — reconcile write verb)

## Intent

S3 `contradictions` surfaces conflicting concept pairs read-only but records nothing. S4 lets a human record the *resolution*: either "these coexist — here's the note" or "this one wins". `openkos reconcile <id-a> <id-b>` is a deterministic, additive, git-reversible WRITE that annotates the resolution. It completes the freshness-lint-v1 arc (gap #7) and lays the reconcile foundation MVP-3's scheduled loops build on. This is the arc's FIRST write verb — safety and reversibility are paramount.

## Scope

### In Scope
- `reconcile <id-a> <id-b>` verb (Phase-A pure plan → preview → confirm-gate → Phase-B atomic write), cloning `relate`.
- Default write: SYMMETRIC `reconciled_with` typed edge on BOTH concepts (each gets `{target: other, type: reconciled_with}`, via `okf.encode_relations`).
- `--winner <id>` flag: DIRECTIONAL edge `winner --supersedes--> loser` (one outbound `supersedes` edge on the winner). `<id>` must be exactly one of the two pair members; the other is the loser; else error.
- Both concepts get a `# Reconciliation` body note recording the resolution and referencing the counterpart.
- A `log.md` `**Reconcile**` line.
- Idempotent re-run (see below).

### Out of Scope (defer)
- `status: deprecated` / deprecate writes — needs gap #8 (lifecycle); `status` is INERT today (nothing reads it), so writing it would be cosmetic/misleading.
- Delete/`forget`-based reconcile; LLM content-merge (that's `merge`); any ledger / `unreconcile`.
- Re-running `find_contradictions` inside the write (no LLM in the write path — pair-id args only).

## Capabilities

### New Capabilities
- `reconcile-command`: the `reconcile` verb, its two write shapes, confirm-gate, body-note, log entry, and idempotency contract.

### Modified Capabilities
- None. Relation vocabulary is already OPEN (`relations.py`); `reconciled_with`/`supersedes` are new string values, no schema/contract change.

## Approach

Clone `relate` (`main.py:923`) — the exact additive typed-edge template: workspace gate → `_resolve_concept_path` on both ids (reject absolute/`..`/reserved/nonexistent) → distinct-id guard → build in memory (`load_frontmatter`/`decode_relations`/`encode_relations`/`dump_frontmatter`) → preview → confirm-gate (`--auto` bypass | config `review:false` | TTY `typer.confirm(abort=True)` | non-TTY refuse exit 1) → Phase-B `fsio.write_atomic` (content then `log.md`). The `# Reconciliation` note appends to each body via the same `load_frontmatter`→append→`dump_frontmatter` path. `Relation` is outbound `{target, type}`, so symmetric = one edge per side; directional = one edge on the winner.

## Reversibility & Safety

Mutated: two concept files' `relations:` + bodies, and `log.md`. Reversibility = **git-undo** (matches `relate`), NO ledger, NO `unreconcile`. ADR-0002's snapshot ledger exists only because `merge` is LOSSY (deletion, high-water sensitivity, provenance dedup). Annotate loses nothing → git-undo is sufficient and proportionate; a ledger would be over-engineering.

**Idempotency**: re-running on an already-reconciled pair does NOT duplicate — the edge is deduped on `(target, type)` (mirrors `relate`), and an existing `# Reconciliation` note citing the same counterpart is not re-appended; a `**Reconcile**: ...; no change.` log line is written instead.

`supersedes` semantic is LABEL-ONLY today: nothing reads it. It *plants* a semantic that gap #8 (lifecycle) can later honor, but S4 does NOT enforce deprecation. State plainly — do not imply enforcement.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | New | `reconcile` verb (clone of `relate`) |
| concept `.md` files | Modified (write) | additive `relations:` edge + `# Reconciliation` body note |
| `bundle/log.md` | Modified (write) | `**Reconcile**` dated entry |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| First write verb of arc — mutation risk | Med | confirm-gate + git-undo + additive-only (never delete/overwrite) |
| `supersedes` implies enforcement it lacks (#8 unbuilt) | Med | label-only; documented as inert plant, not deprecation |
| Re-run duplicates edges/notes | Low | idempotent dedup on `(target,type)` + counterpart-note presence |
| `--winner` id not in pair | Low | validate id ∈ {a,b}, error otherwise |

## Rollback Plan

`git checkout`/`git revert` the touched concept files and `log.md` — additive edits leave no lossy state, so a clean checkout fully restores prior content. No ledger replay needed.

## Dependencies

None unbuilt. Reuses only shipped primitives (`relate`/`encode_relations`, `merge`'s confirm-gate/atomic-write/log). Independent of gap #8.

## Success Criteria

- [ ] `reconcile a b` writes symmetric `reconciled_with` edges + `# Reconciliation` notes + log line, behind the confirm-gate.
- [ ] `reconcile a b --winner a` writes one `a --supersedes--> b` edge; `--winner c` (not in pair) errors.
- [ ] Re-run on a reconciled pair is idempotent (no duplicate edge/note; "no change" log line).
- [ ] No `status` write; no LLM in the write path; git-undo restores prior state.
