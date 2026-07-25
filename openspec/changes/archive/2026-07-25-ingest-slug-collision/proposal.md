# Proposal: Deterministic Slug-Collision Disambiguation at Ingest (#131)

## Intent

At ingest, when a derived candidate's slug collides with an existing concept file, the current code silently DROPS the candidate (`main.py:1024-1033`, stderr-only). This loses information and defeats BOTH `duplicates → adjudicate → merge` (needs ≥2 docs, `candidates.py:119`) and `contradiction-detection` (needs 2 typed-edge-connected ids). Replace the silent drop with deterministic disambiguation so the candidate becomes a distinct, resolvable concept, and record the collision durably. Closes #131. Maintainer decision: **(c) distinct file + resolution** paired with **(a) audit log**.

## Scope

### In Scope
- Replace the single `.exists()` drop at `_stage_derived_objects` (`main.py:1024`) with a collision loop that computes the first-free unique slug and writes the candidate (Source-page write unchanged).
- Disambiguation id scheme: **numeric suffix** (`<slug>-2`, `-3`, … first free). Justification below.
- Audit entry via existing `bundle_log.insert_log_entry` (`log.py:45`) recording: source slug, extracted title, original colliding slug, chosen disambiguated slug. Surfaced by `status` (existing "Recent activity").
- Re-ingest idempotency guard so byte-identical / same-source re-ingest does NOT spawn `-2/-3` (see Approach + Risks).
- Test coverage confirming the disambiguated pair forms a `find_candidates` group and is visible to contradiction detection.

### Out of Scope (Non-Goals)
- Option (b) provenance-enrichment / body-merge at ingest — permanently defeats duplicates/adjudicate + contradictions, reverses D5 with no reversibility ledger.
- Any NEW LLM seam in the ingest write path (no LLM body reconciliation).
- Auto-merging concepts at ingest — resolution stays human-in-the-loop via `adjudicate --apply`.
- Changes to `find_candidates`/`adjudicate`/`merge` logic (they group generically by normalized title/id — verified, no change needed).

## Capabilities

### New Capabilities
- None

### Modified Capabilities
- `ingestion`: derived-slug write becomes uniqueness-preserving (disambiguate instead of drop on collision) + durable audit log of the disambiguation.

## Approach

Replace the drop with `while derived_path.exists(): derived_path = <base>-<n>.md` (n from 2), stopping at the first free path. D5's "existing derived object untouched" invariant is preserved — only the NEW candidate changes destination. Emit one `insert_log_entry` capturing the collision and chosen slug. Resolution is unchanged: two same-title concepts at `claude-code` and `claude-code-2` form a HIGH candidate group naturally (`candidates.py:111-124`) and, once graph-connected, are comparable by contradiction detection.

**Id-scheme rationale (numeric vs source-qualified):** these files exist to be RESOLVED/MERGED, so the id is transient — a numeric suffix is simplest, stays consistent with current human-readable slugs, and does NOT couple a concept id to a source (a concept describes an entity, not its source). Source-qualified (`<slug>--<source_slug>`) is more descriptive but longer-lived and leaks source identity into a canonical-name that adjudicate may later re-pick. Numeric is deterministic and preferred.

**Idempotency (KEY):** a naive loop would spawn `-2/-3` on every re-ingest of the SAME source. The loop MUST treat a collision with a concept that already carries THIS source's provenance (`sources/<source_slug>`) as a create-only no-op (today's safe behavior), disambiguating ONLY against foreign-source collisions. Byte-identical re-ingest (D2 short-circuit, `main.py:1122-1130`) stays a no-op for derived objects. Exact predicate deferred to design.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py:1024-1033` | Modified | Collision loop + provenance-aware idempotency guard; audit-log call |
| `openspec/specs/ingestion/spec.md` | Modified | New derived-slug uniqueness contract |
| tests (ingest) | New | Disambiguation, re-ingest no-op, candidate/contradiction visibility |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Re-ingest spawns duplicate `-2/-3` files | High if naive | Provenance-aware skip; own-source collision = create-only no-op |
| Re-ingest of a source that previously WON a disambiguated slug re-collides and spawns `-3` | Med | **Open item — flag for design**: recognize prior `-N` slug owned by this source |
| Unbounded loop on pathological collisions | Low | First-free numeric scan; deterministic and finite per bundle |

## Rollback Plan

Revert the `main.py` diff to restore the single `.exists()` drop and remove the `insert_log_entry` call and spec delta. No migration: already-created `-2` files remain valid concepts resolvable via `adjudicate`; no schema or bundle-shape change to undo.

## Dependencies

- Existing `bundle_log.insert_log_entry` (`log.py:45`) and `status` "Recent activity" rendering.
- Existing `find_candidates`/`adjudicate`/`merge` resolution flow (unchanged).

## Success Criteria

- [x] Colliding derived candidate is written to a disambiguated slug, not dropped.
- [x] Disambiguation recorded in `log.md` and shown by `status`.
- [x] Byte-identical / same-source re-ingest produces no new `-N` files (idempotent).
- [x] `find_candidates` groups the pair; contradiction detection can compare them.
- [x] `uv run pytest` green; #131 closed.
