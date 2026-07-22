# Verify Report: freshness-suggest-windows (S2) — PR1 of 2

Scope: `type_tiers` config override layer ONLY (config.py + lint.py precedence
step + template + canonical `concept-volatility` spec sync + 2 test files).
Phase 4 (engine leaf `resolution/volatility_typing.py`) and Phase 5 (CLI verb
`suggest-volatility`) are PR2 — intentionally NOT implemented, NOT flagged as
incomplete.

Commit verified: `c100141` on branch `feat/freshness-type-tiers`.

## Verdict: PASS

0 CRITICAL, 0 WARNING, 0 SUGGESTION.

## Spec Compliance (concept-volatility delta only)

Canonical spec: `openspec/specs/concept-volatility/spec.md`
(re-read at HEAD — matches the PR1 delta observation exactly).

| Requirement | Scenario | Test | Result |
|---|---|---|---|
| `type_tiers` Config Override Layer (ADDED) | Valid entry overrides registry default | `test_window_for_doc_type_tiers_override_wins_over_registry_default` | PASS |
| `type_tiers` Config Override Layer (ADDED) | Invalid entry ignored, never raises (bad tier OR bad type name) | `test_window_for_doc_type_tiers_invalid_tier_value_falls_through`, `test_window_for_doc_type_tiers_unknown_type_key_is_ignored` | PASS |
| `type_tiers` Config Override Layer (ADDED) | Absent `type_tiers` reproduces exact S1 behavior | `test_window_for_doc_absent_type_tiers_reproduces_s1_precedence_table` (11-case regression table) | PASS |
| Deterministic, Never-Raising Window Resolution (MODIFIED) | Unknown/invalid per-concept volatility degrades without raising | `test_window_for_doc_precedence_table` (kept) | PASS |
| Deterministic, Never-Raising Window Resolution (MODIFIED) | No override + no type match falls back to global window (extended precondition: no `type_tiers` match either) | `test_window_for_doc_type_tiers_unknown_type_key_is_ignored` | PASS |
| Deterministic, Never-Raising Window Resolution (MODIFIED) | `type_tiers` override wins over registry default (new) | `test_window_for_doc_type_tiers_override_wins_over_registry_default` | PASS |
| Deterministic, Never-Raising Window Resolution (MODIFIED) | `type_tiers` resolving to `static` is never flagged (new) | `test_window_for_doc_type_tiers_resolving_to_static_is_never_flagged` | PASS |

Requirements in scope: 2 (1 ADDED, 1 MODIFIED). Scenarios in scope: 7 total
(3 ADDED-requirement scenarios + 4 MODIFIED-requirement scenarios, full
replace per spec). All 7 covered by real passing tests — 0 untested.

Additional supporting coverage (config passthrough, non-mapping guard,
per-concept-still-wins ordering check): `test_read_config_type_tiers_*` (3),
`test_resolve_windows_type_tiers_*` (2 param groups),
`test_window_for_doc_per_concept_volatility_still_wins_over_type_tiers` (1).

### Design-vs-spec deviation check (apply-progress claim)

Confirmed: `window_for_doc` gates the `type_tiers` lookup on BOTH tier-value
validity (`not in VOLATILITY_TIERS`) AND registry membership
(`doc.type in types.TYPE_TO_DEFAULT_VOLATILITY`), not tier-value validity
alone as the design snippet literally showed. This matches the spec's two
independent ignore conditions ("type name is unknown ... OR ... tier value
is not one of static/slow/volatile"). Regression test
`test_window_for_doc_type_tiers_unknown_type_key_is_ignored` present and
passing — confirmed by direct source read of `src/openkos/lint.py`
(`window_for_doc`) and by running the test.

## Scope Verification

`git show --stat c100141` (single commit, parent is current `main` merge-base
`7e734dbd`):

```
openspec/changes/freshness-suggest-windows/{design,proposal,tasks}.md   | new (planning artifacts)
openspec/changes/freshness-suggest-windows/specs/concept-volatility/spec.md | new (delta)
openspec/changes/freshness-suggest-windows/specs/volatility-suggestion/spec.md | new (PR2 delta, planning only)
openspec/specs/concept-volatility/spec.md   | 64 +++++--  (canonical sync)
src/openkos/config.py                       |  9 ++
src/openkos/lint.py                         | 68 +++++---
src/openkos/templates/openkos.yaml.template |  2 +
tests/unit/test_config.py                   | 41 ++++
tests/unit/test_lint.py                     | 128 ++++++++++
```

Confirmed: NO changes to `cli/main.py`, no new `resolution/volatility_typing.py`
module — PR2 (engine leaf + CLI verb) correctly absent from this commit.
`docs/adr/0007-volatility-taxonomy.md`: `git diff <merge-base>..HEAD --
docs/adr/0007-volatility-taxonomy.md` returns empty — untouched, confirmed.

Lint's read-only/never-fail/deterministic contract preserved: `window_for_doc`
remains a pure function of `(doc, windows)`; every new degrade path (non-mapping
`cfg.type_tiers`, unknown type key, invalid tier value) falls through via
`.get()`/`not in` guards, never raises — confirmed by source read and by
`test_resolve_windows_never_raises`-style coverage plus the 3 new type_tiers
degrade tests all passing.

## Task Completion (tasks.md)

Phase 1 (config passthrough): 1.1–1.3 all `[x]` — verified against actual diff.
Phase 2 (precedence layer): 2.1–2.5 all `[x]` — verified against actual diff,
including the documented deviation note at 2.4.
Phase 3 (spec sync): 3.1 `[x]` — verified, canonical spec.md matches PR1 spec
delta observation exactly.
Phase 4 (engine leaf): 4.1–4.3 `[ ]` — correctly unchecked, PR2, out of scope.
Phase 5 (CLI verb): 5.1–5.2 `[ ]` — correctly unchecked, PR2, out of scope.
Phase 6 (verification gate): 6.1–6.5 unchecked in tasks.md but independently
re-run below with all commands passing; will be formally checked off at PR2
merge per the chained-PR plan — not a defect for this PR1 verification.

## Independent Gate Re-Run (this session, not apply's cached numbers)

| Command | Result | Exit |
|---|---|---|
| `uv run pytest -q` | 1352 passed in 45.53s | 0 |
| `uv run pytest tests/unit/test_config.py tests/unit/test_lint.py -k "type_tiers or window_for_doc"` | 26 passed | 0 |
| `uv run ruff check .` | All checks passed! | 0 |
| `uv run ruff format --check .` | 102 files already formatted | 0 |
| `uv run mypy .` | Success: no issues found in 102 source files | 0 |

## Risks

None blocking. PR2 (Phase 4 engine leaf + Phase 5 CLI verb) remains to be
implemented and verified in a follow-up sdd-apply/sdd-verify cycle per the
stacked-to-main chain strategy recorded in tasks.md.
