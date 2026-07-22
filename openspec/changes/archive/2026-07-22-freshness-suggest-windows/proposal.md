# Proposal: freshness-suggest-windows (S2) — `suggest-volatility`

## Intent

S1 shipped a hardcoded type→tier map (`ObjectType.default_tier`, e.g. `Person=slow`). Real bundles vary: a domain where `Person` records churn wants `Person=volatile`; today the only fix is editing engine code. S2 closes the "is this the right tier?" loop with a read-only LLM advisory verb that proposes a per-type tier + rationale, plus a config layer that makes an accepted suggestion applicable by hand-edit — no code change.

## Scope

### In Scope
- New read-only CLI verb `suggest-volatility` (clone of `suggest-relations` wiring).
- New config-free engine leaf `resolution/volatility_typing.py` (caller injects `LLMBackend`).
- New read-only config layer `type_tiers:` (type-name → tier map) in `openkos.yaml` + template comment.
- New precedence step in `lint.window_for_doc`: per-concept `volatility` → **`type_tiers` override** → per-type registry `default_tier` → global fallback.
- Docs.

### Out of Scope (deferred / non-goals)
- Config writes / auto-accept (no safe partial-YAML writer exists — hand-edit only).
- Duration/numeric window suggestions.
- Contradiction/staleness detection (S3), guided reconcile write-verb (S4).

## Capabilities

### New Capabilities
- `volatility-suggestion`: read-only LLM verb suggesting a per-type volatility tier + rationale; print-only, zero writes.

### Modified Capabilities
- `concept-volatility`: adds the `type_tiers:` config override layer and the new `window_for_doc` precedence step.

## Approach

Mirror `suggest-relations` exactly: `require_workspace` gate → `read_config` guard (`except OSError/ValueError`) → `OllamaClient(model=cfg.model)` → engine → 3-tier ORDERED `OllamaError` handler (Unavailable→ModelNotFound→generic, each exit 1) → plain stdout report, closing `Next:` hint pointing at hand-editing `type_tiers:`. Iterate per concept TYPE; one `llm.chat` per type; input = type name + current `default_tier` + sample concept bodies of that type from the bundle. Fail-closed parse `{"tier":..., "rationale":...}` validated against `VOLATILITY_TIERS`; an invalid tier degrades that type to `[?]` (never crashes); transport/model errors propagate unswallowed. The `type_tiers` layer is read-only and degrades never-fail: unknown type-name or invalid tier value → ignore that entry, fall through (mirrors `resolve_windows`' non-mapping guard).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` | New | `suggest_volatility_cmd` verb |
| `src/openkos/resolution/volatility_typing.py` | New | config-free LLM leaf |
| `src/openkos/config.py` | Modified | `Config.type_tiers` passthrough |
| `src/openkos/lint.py` | Modified | new precedence step in `window_for_doc` |
| `openkos.yaml.template` | Modified | commented `type_tiers:` block |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| New precedence step weakens never-fail/deterministic lint contract | Med | Degrade-never-fail: invalid entries ignored, existing behavior when `type_tiers` absent |
| New config schema element | Med | Additive, read-only, absent-default `{}` |
| LLM suggestion precision | Med | Advisory print-only; human hand-edits |

## Rollback Plan

Config rule. Revert the four source files; remove the `type_tiers:` template block. With `type_tiers` absent, `window_for_doc` collapses to the exact S1 precedence. No data migration; the verb is additive.

## Dependencies

- S1 volatility model (shipped): `VOLATILITY_TIERS`, `TYPE_TO_DEFAULT_VOLATILITY`, `window_for_doc`, ADR-0007 (Accepted).

## ADR Evaluation (two-limb gate)

**Verdict: extends ADR-0007 — NO new ADR; reference it.**
- Limb A (hard to reverse?): No. The `type_tiers:` key is additive, read-only, absent-default `{}`, degrades on invalid entries, removable without migration. The precedence step is internal and reversible.
- Limb B (new decision?): No. It is the natural extension of the type→tier seam ADR-0007 already accepted — one more override source in the same precedence ladder, not a new architectural axis.
Both limbs point away from a new ADR. Reference ADR-0007 in the spec; a one-line amendment note is sufficient.

## Success Criteria

- [ ] `suggest-volatility` prints one tier + rationale per concept type, `[?]` on invalid, zero writes.
- [ ] 3-tier `OllamaError` handler + gate/guard match `suggest-relations`.
- [ ] `type_tiers:` override applies via `window_for_doc`; absent → identical S1 behavior.
- [ ] Invalid/unknown `type_tiers` entry ignored, never raises.
