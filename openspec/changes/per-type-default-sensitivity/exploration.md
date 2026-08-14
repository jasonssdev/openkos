# Exploration: per-type default sensitivity (Person born above workspace floor) — issue #669

## Current State

- `config.py::DEFAULT_SENSITIVITY = "private"` (line 95); `Config.default_sensitivity` resolved in `read_config` (lines 861-865) — single workspace-wide scalar, no per-type structure today.
- Closest config-seam precedent: `type_tiers: dict[str, str]` (`config.py:661-667, 738, 893`) — raw passthrough, `{}` fallback, validated lazily downstream in `lint.window_for_doc`. The `models: dict[str,str]` field is the counter-precedent — validated eagerly at `read_config` (lines 808-846) because a silently-wrong entry changes security-relevant behavior invisibly, which applies to a per-type sensitivity default too.
- `okf.SENSITIVITY_ORDER = ("public", "private", "confidential")` (`model/okf.py:49`); `combine_sensitivity(a,b)` (line 553-563) is the pure rank-max high-water-mark used everywhere.
- **Birth seam**: `okf.build_concept` has exactly TWO call sites codebase-wide:
  1. `cli/main.py:3249`, inside `_stage_derived_objects` (the `ingest` extraction path) — `sensitivity=stamp_sensitivity`, `type=extraction.type` both available here; `stamp_sensitivity` is the Source's resolved sensitivity (`combine_sensitivity(on_disk_sensitivity, cfg.default_sensitivity)` on re-ingest, or `cfg.default_sensitivity` on fresh ingest — `cli/main.py:4300-4312`). This is the primary Person-birth seam and the natural insertion point.
  2. `cli/main.py:12993`, inside `_stage_filed_answer` (`query --save --type Person` is possible — `save_type` accepts any `BUILDABLE_TYPES` member, `cli/main.py:13060-13062`). Not mentioned in owner rulings — open scope question.

## Affected Areas

- `src/openkos/config.py` — new `type_sensitivity_defaults`-style field (mirror `type_tiers`), plus a decision on eager vs. lazy validation.
- `src/openkos/cli/main.py:3249` (`_stage_derived_objects`) — the primary call site to consult the per-type default.
- `src/openkos/cli/main.py:12993` (`_stage_filed_answer`) — secondary birth path, scope TBD.
- `src/openkos/model/okf.py` — needs a small "raise by one level, clamped at ceiling" helper; `SENSITIVITY_ORDER`/`combine_sensitivity` unchanged.
- `openspec/specs/sensitivity-config/spec.md` — closest existing spec owner; likely needs a new requirement block, or a sibling spec file.
- `docs/adr/` — likely needs a new ADR (hard-to-reverse security-policy decision per the ADR gate); ADR-0003/0008/0012 are the existing precedents in this area.

## Interactions traced (issue-named) — all confirmed non-breaking

1. **#602/#667 forget scrub** — `_scrub_entry_snapshots` is generic by ID, no type/sensitivity branching. Non-issue.
2. **#645 merge + #569 high-water-mark** — `combine_sensitivity`/`MergeLedgerEntry` are purely rank-based, no type parameter anywhere. A type-defaulted `confidential` Person raises merge survivors exactly like any other route to `confidential`. Confirmed type-blind, works unchanged.
3. **#569 explicit `confidential` flag / fail-closed retrieval exclusion** — WILL correctly exclude a type-defaulted Person from `query`/`contradictions`/`suggest-relations` against a non-local backend. This is the intended effect. No UX advisory for this specific case exists today — flag for design.
4. **`set-sensitivity` + downgrade flag** — fully independent write path, never consults a type-default mechanism; an operator can freely lower a type-defaulted Person. Confirmed no conflict.
5. **`lint.check_below_source_sensitivity`** — only flags concepts BELOW the Source's combined level; a Person born ABOVE can never trigger it (high-water-mark logic). Confirmed non-issue.

## Approaches

1. **Dict-keyed `type_sensitivity_defaults` config seam consulted at the `build_concept` call site in `_stage_derived_objects`**, folded via existing `combine_sensitivity`.
   - Pros: minimal surface; reuses all existing machinery unchanged; one-line extension for `Organization` (ruling 2 compliance).
   - Cons: needs a new "raise by one, clamped" helper; needs eager-vs-lazy validation decision; needs `query --save` scope decision.
   - Effort: Low-Medium.
2. **Bespoke `PERSON_DEFAULT_SENSITIVITY_BONUS` constant, no general seam.**
   - Pros: less code for Person-only MVP.
   - Cons: directly violates owner ruling 2 (not a one-line change for Organization). Disqualified.
   - Effort: Low, but rejected on ruling grounds.

## Recommendation

Approach 1. It's the only option compliant with owner ruling 2, and every adjacent mechanism (merge, retrieval fail-closed filter, `set-sensitivity`, lint) is confirmed to already compose correctly with a higher-born Person. Four open decisions for `sdd-propose`/`sdd-design`: (a) eager vs. lazy config validation, (b) whether `query --save --type Person` is in scope, (c) whether a new ADR is warranted (likely yes), (d) which spec file owns the new requirement.

## Risks

- Double-raising ambiguity: "one level above the WORKSPACE FLOOR" (ruling 1, literal) vs. one level above the Source's already-inherited sensitivity — must design as `combine_sensitivity(stamp_sensitivity, raise_one(cfg.default_sensitivity))`, never bypassing Source inheritance.
- Sources vs. derived objects: `build_source_concept` is structurally separate from `build_concept`, so accidental cross-application is unlikely but must be an explicit non-goal.
- `query --save --type Person` secondary birth path left silently type-blind would create an inconsistency if not explicitly scoped.
- No migration/backfill for existing on-disk Person concepts (owner-stated, applies at birth only) — must be an explicit non-goal to avoid scope creep.
- Eager vs. lazy config validation precedent split (`type_tiers` lazy vs. `models` eager) needs an explicit call.

## Ready for Proposal

Yes — no blocking unknowns found; all owner rulings are consistent with existing type-blind sensitivity machinery.

## Owner rulings (fixed, from the 2026-08-14 session)

1. RELATIVE default: a Person is born ONE level above the workspace floor (`public` floor → `private`; `private` floor → `confidential`; `confidential` floor stays `confidential`).
2. Person ONLY; the seam must make adding `Organization` a one-line config change.
3. Full SDD cycle.
