# Design: Deterministic Slug-Collision Disambiguation at Ingest (#131)

## Technical Approach

Replace the single create-only drop at `_stage_derived_objects` (main.py:1024-1033)
with a provenance-aware disambiguation loop. All work stays inside Phase A (pure,
read-only): the loop reads existing on-disk concept files to decide the destination
slug, and Phase B still only `mkdir` + `write_exclusive` per plan (D5 preserved).
The change is local to one function plus one spec delta plus tests. Numeric-suffix
scheme (`<slug>-2`, `-3`, ... first free). One `insert_log_entry` audit record per
disambiguation. No change to `find_candidates`/adjudicate/merge, no LLM seam.

## The Idempotency Predicate (critical)

Source identity key = `f"sources/{source_slug}"` — the exact string written into a
concept's `provenance:` list at creation (main.py:1041). A concept's provenance is
read via `okf.load_frontmatter(text)[0]["provenance"]` (a `list[str]`).

On a derived candidate whose base slug `<slug>` already exists on disk:

1. Build the collision family: `<slug>.md` plus every `<slug>-N.md` (N>=2) present
   in `bundle_dir / link_dir`. Sort by N ascending (deterministic).
2. Scan each family member's `provenance:`. If **any** member already contains this
   ingest's `sources/<source_slug>` key → **create-only NO-OP**: skip this
   candidate entirely (no plan, no new file, no log entry). This is D5-identical
   to today's drop and is the guarantee that re-ingest never spawns `-N`.
3. Otherwise (this source is represented **nowhere** in the family) → this is a
   genuinely foreign-source collision: choose the first-free `<slug>-N` (scan N=2
   upward until `<slug>-N.md` is absent), stage a normal single-source create, and
   emit one audit-log entry.

The predicate is **provenance-membership**, not byte comparison. It scans the whole
family so a source that previously WON a disambiguated slug (`<slug>-2`) is
recognized there and re-collides to a no-op — this closes the proposal's OPEN
ITEM (re-ingest of a prior `-N` winner must not spawn `-3`).

### Worked walkthrough

| Step | Action | Family scanned | Outcome |
|------|--------|----------------|---------|
| Source A ingest | title→`<slug>`, absent | — | create `<slug>` (prov A) |
| Source B ingest | `<slug>` exists, A only | `<slug>` | foreign → create `<slug>-2` (prov B) |
| Re-ingest A | `<slug>` exists | `<slug>`,`-2` | A found in `<slug>` → **no-op** |
| Re-ingest B | `<slug>` exists | `<slug>`,`-2` | B found in `-2` → **no-op** (not `-3`) |
| Source C ingest | `<slug>` exists | `<slug>`,`-2` | foreign → create `<slug>-3` |

Terminates: family is finite per bundle; scan is bounded; each source maps to at
most one family member, so the family size equals the number of distinct sources —
it cannot grow on re-ingest. Idempotent by construction.

### Relationship to the D2 byte-identical short-circuit (main.py:1122-1130)

D2 governs only the **raw file + Source concept** re-ingest path; it does not touch
derived candidates. Derived idempotency is guaranteed **solely** by the new
provenance-scan, not by D2. D2 is **unchanged**. When raw bytes differ, ingest
refuses before staging; when identical, extraction re-runs and the provenance-scan
is what prevents duplicate `-N`. Two independent layers, no overlap.

## Collision loop mechanics

`existing = sorted(link_path.glob(f"{base}-*.md") + [base.md])` filtered to exact
`<base>` / `<base>-<int>` names (regex `^{base}(-\d+)?$` to avoid matching
`<base>-foo`). First-free N: iterate N from 2, return first `<base>-N.md` not on
disk. Also register the chosen slug into the batch-local `seen_slugs` set so two
foreign candidates in one batch cannot re-pick the same `-N`.

## Audit-log entry

Phase B, after the disambiguated write, one call:
`insert_log_entry(log_text, today, entry)` where `entry` is a single line, e.g.
`"**Disambiguation**: [<chosen-slug>](/<link_dir>/<chosen-slug>.md) — '<title>' from source '<source_slug>' collided with '<original-slug>'; wrote distinct concept."`
Fields: source slug, extracted title, original colliding slug, chosen slug. Newline
already rejected by `_reject_newline`. Rendered by `status` "Recent activity".

## Provenance on the disambiguated concept

Single-source: `provenance=[f"sources/{source_slug}"]` — byte-identical to a normal
create (main.py:1041). No cross-source union at ingest; multi-source synthesis stays
reachable only via `adjudicate --apply`.

## File Changes

| File | Action | Est. lines |
|------|--------|-----------|
| `src/openkos/cli/main.py` (`_stage_derived_objects` + Phase B log call) | Modify | ~55-75 |
| `openspec/specs/ingestion/spec.md` | Modify | ~25 |
| `tests/.../test_ingest*.py` | New/Modify | ~120-160 |

## Resolution machinery unchanged (candidates.py evidence)

`find_candidates` groups by `normalize_key(title)` per `okf_type` (candidates.py:158-176);
two same-title concepts at `<slug>` and `<slug>-2` share a key → HIGH group with
>=2 members (`len(members) < 2: continue`, line 119 is satisfied). Confirmed: no
change to find_candidates/adjudicate/merge/contradiction.

## Testing Strategy (strict TDD, RED first)

| Layer | Test | Expect |
|-------|------|--------|
| Unit/predicate | foreign collision | writes `<slug>-2` |
| Unit | third foreign source | writes `<slug>-3` |
| Unit | re-ingest owner of `<slug>` | no new file (no-op) |
| Unit | re-ingest owner of `<slug>-2` | no `-3`, no-op |
| Unit | non-colliding candidate | unchanged path |
| Unit | audit entry | `insert_log_entry` bullet present, shown by `status` |
| Integration | disambiguated pair | `find_candidates` emits one HIGH group |
| Integration | D2 byte-identical re-ingest | derived no-op still holds |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process-integration boundary. Pure in-process filesystem staging.

## ADR

Design note suffices, no formal ADR. This is a behavior change to a core ingest
path, but fully reversible: rollback restores the single `.exists()` drop; already
created `-N` files remain valid, resolvable concepts (no schema/bundle-shape change,
no migration). Documented inline here per project convention (significant +
hard-to-reverse → ADR; this is significant but reversible).

## Migration / Rollout

No migration required.

## Open Questions

None blocking. Regex family-match (`^{base}(-\d+)?$`) chosen over naive `glob` to
avoid false family membership on `<base>-word` slugs — confirmed in loop mechanics.
