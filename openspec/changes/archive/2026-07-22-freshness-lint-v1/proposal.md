# Proposal: Freshness Lint v1 — Slice 1 (Volatility Taxonomy + Volatility-Aware Windows)

## Intent

Lint v0 uses one global `freshness_window: 7d` for every doc (`lint.resolve_window`,
`config.DEFAULT_FRESHNESS_WINDOW`). A stable `Place` and a fast-moving `Procedure` are
flagged identically, so staleness is not meaningful per kind of knowledge. S1 makes the
stale window depend on how volatile the knowledge is — without adding writes, LLM calls,
or breaking lint's read-only / never-fail / deterministic (injected clock) contract.

## Scope

### In Scope
- Fixed 3-tier taxonomy: `static` / `slow` / `volatile`.
- New `volatility:` frontmatter field (separate from `freshness`; per-concept override).
- Per-type default tier on the `ObjectType` registry (`model/types.py`).
- Volatility-aware window resolution in `lint.py` (per-doc window, not one global).
- Config shape: per-tier windows in `openkos.yaml` + hardcoded code defaults; global
  `freshness_window` retained as ultimate fallback.
- Revise `openspec/specs/lint/spec.md` Non-Goal ("lint never reads it").

### Out of Scope (deferred future slices — planned chain)
- S2: LLM-suggested per-type windows (`suggest-windows`).
- S3: contradiction detection (`contradictions`).
- S4: guided `reconcile` write verb.
- No change to `freshness: snapshot` semantics (stays the orthogonal skip flag).

## Capabilities

### New Capabilities
- `concept-volatility`: taxonomy, `volatility` field, per-type registry defaults,
  per-concept override, resolution precedence.

### Modified Capabilities
- `lint`: stale-stamp scan resolves a per-doc window by volatility; Non-Goal revised.

## Approach

Add `volatility` to `ObjectType` with per-type defaults (grounded in the real registry:
Place/Event/Decision/Source → `static`; Concept/Entity/Person/Organization → `slow`;
Procedure/Project → `volatile`). Extend `LintDoc` to carry `type` + `volatility`.
Precedence, never raising: per-concept `volatility:` → per-type default → global
`freshness_window`. Unknown/invalid value degrades to the next tier down the chain.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/model/types.py` | Modified | `volatility` on `ObjectType` + per-type defaults |
| `src/openkos/lint.py` | Modified | `LintDoc.type/volatility`; per-doc window resolution |
| `src/openkos/config.py` + template | Modified | Per-tier window config + defaults |
| `src/openkos/model/okf.py` | Modified? | Optional `volatility` in `build_concept` output |
| `openspec/specs/lint/spec.md` | Modified | Revise Non-Goal + stale-scan requirement |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| New field touches concept data model / ingest output | Med | Leave `volatility` absent by default; resolve from type default |
| Resolution-precedence bug | Med | Deterministic pure function; injected clock; table tests |
| Break never-fail contract | Low | Every unknown/invalid value degrades, never raises |

## Rollback Plan

Pure additive read-path change. Revert the PR: `volatility` frontmatter (if present)
becomes inert; lint falls back to the single global `freshness_window`. No data migration
needed — bundles without the field are already the default path.

## Dependencies

- ADR for taxonomy + data-model field (see verdict below), authored in design/apply.

## Success Criteria

- [ ] A `volatile` doc and a `static` doc with the same stamp age resolve to different windows.
- [ ] A concept with no `volatility` field resolves via its type default → global fallback.
- [ ] Invalid `volatility` / config never raises; lint still exits 0.
- [ ] Lint spec Non-Goal revised; `freshness: snapshot` skip behavior unchanged.
