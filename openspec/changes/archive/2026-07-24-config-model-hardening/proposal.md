# Proposal: Config Model Hardening (Issue #128, Slice A)

## Intent

Three independently confirmed defects let a YAML 1.1 boolean/null (e.g. `model: yes` → Python `True`) flow through the config layer as if it were a valid model string, culminating in a `doctor` crash (`TypeError: argument of type 'bool' is not iterable`). This slice hardens the config read/validate contract so a mistyped `model:` value fails cleanly with an actionable message instead of lying at the type level or crashing a diagnostic command.

This change is **Slice A** of issue #128, split by user decision. It ADVANCES but does NOT CLOSE #128 — the interactive model picker is **Slice B** (`init-model-picker`), which builds on this hardened layer.

## Scope

### In Scope
- **Defect #1 — `read_config` type gap** (`config.py:384/392`): enforce `isinstance(model, str)`, slotting into the existing "checked `is not None`, not truthiness" conditional without breaking the `review: false`-survives-untouched pattern (`test_read_config_preserves_explicit_review_false`).
- **Defect #2 — `validate_model` allowlist gap** (`config.py:56-83`): reject the EXACT PyYAML default-resolver boolean/null set (`yes/no/on/off/true/false/null/~`, case-insensitive), not a guessed list, so no accepted model string changes type on YAML round-trip.
- **Defect #3 — `doctor` never raises on non-str model** (`ollama.py:291` via `main.py:5595+`): doctor must render `[FAIL]` with remediation, never `TypeError`, reusing the accumulated-never-raised `CheckResult` convention (no new output shape).

### Out of Scope (Non-Goals)
- Interactive model picker — deferred to Slice B (`init-model-picker`).
- No change to `list_models()` return contract.
- No embedding-model exclusion / capability-signal work.
- No new doctor output shapes or check reordering.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `workspace-init`: `validate_model` rejects YAML 1.1 reserved boolean/null words (case-insensitive); `read_config` enforces `str` type on the `model` field.
- `doctor-command`: doctor never raises on a non-str `model`; emits `[FAIL] Config valid` (or equivalent guarded check) with remediation.

## Approach

Type-check at read (`read_config`) plus reserved-word rejection at write-time validation (`validate_model`) are complementary defenses. **Open design question for sdd-design:** whether making `read_config` raise `ValueError` on a non-str `model` already SUBSUMES defect #3 (doctor check 2 already wraps `read_config` in `try/except (OSError, ValueError)`), or whether an independent doctor-side guard for checks 4/5 (`main.py:5595+`, no independent guard today) is still warranted for defense-in-depth against callers that bypass `read_config`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/config.py` | Modified | `read_config` str-check (#1), `validate_model` reserved-word rejection (#2) |
| `src/openkos/cli/main.py` | Modified | `doctor` checks 2/4/5 guard (#3), pending design decision |
| `src/openkos/llm/ollama.py` | Possibly modified | `model_tag_matches` (#3) only if a call-site guard is chosen |
| `tests/unit/test_config.py` | New tests | reserved-word + str-type tables |
| `tests/unit/cli/test_doctor.py` | New tests | non-str model crash-guard |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Reserved-word set drifts from PyYAML resolver | Med | Match PyYAML default-resolver set exactly; test the full grammar |
| str-check breaks another field's fallback pattern | Low | Slot into existing conditional; assert `review: false` unchanged |
| Redundant doctor guard vs. #1 subsumption | Low | Design phase resolves subsumption question before tasks |

## Rollback Plan

Single-slice revert: `git revert` the slice PR. No migrations, no persisted-format change — `openkos.yaml` schema is unchanged; only stricter validation/read behavior is added.

## Dependencies

- Slice B (`init-model-picker`) depends on this slice merging first.

## Success Criteria

- [ ] `model: yes` (and full reserved set) rejected by `validate_model` with clear error.
- [ ] `read_config` raises `ValueError` on a non-str `model`; str fallback pattern for other fields unchanged.
- [ ] `doctor` renders `[FAIL]` with remediation on a non-str model config, never crashes.
- [ ] `uv run pytest` green; slice under 800-line review budget.
