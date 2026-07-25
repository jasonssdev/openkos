# Proposal: set-volatility write verb (#140)

## Intent

`suggest-volatility` advises a per-type volatility tier but leaves the user to hand-edit `type_tiers:` in `openkos.yaml` — an error-prone, comment-destroying footgun (#128). This slice completes the advisor→action pattern with a write verb `openkos set-volatility <ConceptType> <tier>` that edits `type_tiers:` safely, plus an extracted core that makes a future `suggest-volatility --apply` cheap.

## Scope

### In Scope
- `set-volatility <ConceptType> <tier>` CLI command (mirrors `relate`).
- Comment-safe config core `config.set_type_tier(...)` (text surgery, `fsio.write_atomic`).
- Strict write-time validation of ConceptType + tier (only gate before `lint.window_for_doc`).
- Preview + confirm gate + idempotent no-op + `_autocommit`.
- Update `suggest-volatility`'s trailing `Next:` hint (main.py:4379).

### Out of Scope
- `suggest-volatility --apply` interactive walk — DEFERRED (extracted core makes it a thin follow-up).
- Any `ruamel.yaml` / new dependency — rejected; text surgery only.
- Changing volatility inference/typing logic, `lint`, or `type_tiers` read semantics.

## Capabilities

### New Capabilities
- `set-volatility`: write verb + comment-safe `type_tiers:` config edit, validation, preview/confirm, autocommit.

### Modified Capabilities
- `volatility-suggestion`: the "hand-edit only / zero writes" hint becomes `Next: openkos set-volatility <ConceptType> <tier>`.

## Locked Decisions
1. **Vocabularies (strict, fail-closed)**: tier ∈ `VOLATILITY_TIERS {static,slow,volatile}`; ConceptType ∈ full 10-entry `REGISTRY` (incl. `Source`), NOT `CLASSIFIABLE_TYPES`. Exact-match PascalCase (REGISTRY names are `Concept…Source`). Unknown value → clear stderr message listing valid options + exit 1.
2. **Comment-safe edit**: raw-text surgery, no PyYAML round-trip. Core handles both states — absent/commented `type_tiers:` (append fresh block) and present (update existing entry or insert new line). FAIL CLOSED (refuse, exit 1, workspace untouched) on any shape it cannot confidently edit (bad indentation, inline flow-mapping, malformed block).
3. **Preview + confirm**: mirror `relate` Phase A validate → preview `ConceptType: <old> -> <new>` (old = current `type_tiers` value, else `TYPE_TO_DEFAULT_VOLATILITY`) → confirm gate (`--auto`/`cfg.review`/TTY, main.py:2324-2333) → Phase B write + autocommit.
4. **Idempotence**: type already at requested tier → no-op message, no write/commit, exit 0.
5. **Autocommit**: after write, `_autocommit` `openkos.yaml`; message `set-volatility: <ConceptType> -> <tier>`.
6. **Extract-for-reuse**: core separable from CLI/confirm/commit (merge_core precedent) so `--apply` reuses it.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Text surgery corrupts comments/config | Med | Fail-closed on any unparseable shape; before/after text fixtures; atomic write |
| Wrong vocabulary rejects valid `Source` | Med | Validate against full REGISTRY, tested |
| No downstream re-validation of type_tiers | High | Write-time validation is strict + complete |
| Non-TTY confirm ambiguity | Low | Reuse relate's non-TTY refusal precedence |

## Rollback Plan
Revert the PR; the change is additive (new command + core) plus one hint-line edit. No migration, no data change; autocommit is a standard revertible commit.

## Success Criteria
- [ ] `set-volatility <type> <tier>` edits `type_tiers:` preserving all comments.
- [ ] Invalid type/tier and unparseable config fail closed (exit 1, no write).
- [ ] Idempotent re-set is a no-op (exit 0); successful write autocommits.
- [ ] `config.set_type_tier` is independently callable; `suggest-volatility` hint updated.
