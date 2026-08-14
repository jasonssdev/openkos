# Proposal: Per-Type Default Sensitivity (Person born above the workspace floor)

Issue #669.

## Intent

Sensitivity is one workspace-wide scalar, so a `Person` derived object is born at the same level as any other type. People are the highest-risk objects a bundle holds. Give the workspace a per-type birth floor, shipping with `Person` only.

## Scope

### In Scope

- Config seam: per-type mapping `OKF type -> relative offset above the workspace floor`, default `{"Person": 1}`. Adding `Organization` is one line.
- **Eager** validation in `read_config` (decision a): unknown type key or out-of-range offset fails config load with a clear error.
- Born level at BOTH `build_concept` call sites (decision b), via one shared helper:
  `combine_sensitivity(base_sensitivity, raise_by(cfg_floor, offset))`, clamped at `confidential`.
  - `_stage_derived_objects` (ingest): base = the Source's resolved `stamp_sensitivity`.
  - `_stage_filed_answer` (`query --save --type Person`): base = the existing cited-concept high-water-mark.
- One advisory line at write time (ingest summary and `--save` success message) naming how many objects were born above the floor by type default, and that `confidential` excludes them from non-local-backend `query`/`contradictions`/`suggest-relations` (#569). This effect is intended.
- New ADR (decision c): both gate conditions hold — a security-policy default, socially hard to reverse once bundles ship.

### Out of Scope

- Migration or backfill of existing on-disk `Person` concepts. Birth-time only.
- Sources: `build_source_concept` is untouched.
- `Organization` or any other type default. Seam-ready, ships empty.
- `set-sensitivity`, merge, lint, forget: all confirmed type-blind and unchanged.

## Capabilities

### New Capabilities

- `type-sensitivity-defaults`: per-type birth floor — config shape, eager validation, the floor-relative raise, and the write-time advisory.

### Modified Capabilities

- `ingestion`: "Derived Object Provenance and Sensitivity Inheritance" — inheritance becomes a floor, not equality.
- `query-command`: filed-answer birth level gains the same type floor.
- `participant-coverage-probe`: "Sensitivity remains a single workspace-level setting, unaffected by object type" is no longer true workspace-wide; narrow it to probe scope.

`sensitivity-config` is NOT modified (decision d): its Purpose scopes it to the `set-sensitivity` verb over existing concepts.

## Approach

Exploration Approach 1. Reuse `combine_sensitivity` unchanged; add only a "raise N levels, clamp at ceiling" helper in `okf.py`. Source inheritance is never bypassed — the type default is a floor-relative MINIMUM, and the high-water-mark still wins when the base is higher.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/config.py` | Modified | New per-type field + eager validation |
| `src/openkos/model/okf.py` | Modified | Raise-and-clamp helper |
| `src/openkos/cli/main.py:3249` | Modified | Ingest birth seam + advisory |
| `src/openkos/cli/main.py:12993` | Modified | `--save` birth seam + advisory |
| `docs/adr/` | New | Policy ADR |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Double-raising (offset applied to the Source's inherited value) | Med | Formula fixed above: offset applies to `cfg_floor`, never to `base` |
| Persons silently disappear from non-local retrieval | Med | Write-time advisory line |
| Eager validation breaks existing workspaces | Low | Absent field defaults to `{"Person": 1}`; only malformed explicit entries fail |
| Offset seam over-generalizes | Low | Bounded range; only `Person` ships |

## Rollback Plan

Revert the commit. Concepts already born higher keep their level (no backfill either way); lower them with `set-sensitivity --allow-downgrade`.

## Dependencies

None.

## Success Criteria

- [ ] `public` floor -> Person born `private`; `private` -> `confidential`; `confidential` -> `confidential`.
- [ ] A Source above the floor+offset still wins (high-water-mark preserved).
- [ ] Both birth seams produce identical levels for the same inputs.
- [ ] Malformed per-type config fails `read_config` with a clear message.
- [ ] Adding `Organization` is a one-line config change with no code edit.
- [ ] Existing on-disk Person concepts are byte-identical after an unrelated run.
