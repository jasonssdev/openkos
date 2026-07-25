# Design: Config Model Hardening (Issue #128, Slice A)

## Technical Approach

Harden the config read/validate contract so a YAML 1.1 boolean/null (`model: yes`
→ Python `True`) fails cleanly at the source instead of lying at the type level
or crashing `doctor`. Two complementary source-layer fixes in `config.py`; no
`doctor` code change (the fix is subsumed — see the fork decision). All three
reported defects resolve inside the existing `read_config` / `validate_model`
contracts and the existing accumulate-then-exit `CheckResult` convention.

## Architecture Decisions

### Decision: Doctor-side guard is NOT needed — #1 subsumes #3 at the source

**Choice**: Fix defect #3 entirely by str-checking `model` AND `embedding_model`
in `read_config`; add only a regression test to `test_doctor.py`, no `main.py`
change.
**Alternatives considered**: An independent guard in doctor checks 4/5.
**Rationale (control flow, main.py:5530-5555)**: `cfg` is initialized `None`;
`read_config` runs inside `try/except (OSError, ValueError)`. When it raises,
`cfg` stays `None`, check 2 renders `[FAIL] Config valid` (detail = exc), and
line 5552 falls back to `config.DEFAULT_MODEL` (a valid str). Checks 4/5 then run
against valid defaults — no `model_tag_matches(True, ...)` crash. Doctor does not
short-circuit but continues safely. The ONLY residual crash path is
`embedding_model: yes` with a valid `model` (cfg not None → check 5 consumes
`True`). Str-checking BOTH fields closes that path, so no doctor guard is
warranted. Fixing at the `Config` type contract benefits every caller and adds no
new output shape.

### Decision: Reserved-word rejection = exact-token frozenset before the regex

**Choice**: In `validate_model`, after the blank check, reject the lowercased
whole trimmed token if it is in `frozenset({"yes","no","true","false","on","off","null"})`.
**Alternatives considered**: Regex-embedded blocklist; guessed word list.
**Rationale**: This is the exact PyYAML default-resolver YAML-1.1 bool/null word
grammar (case-insensitive). Membership is on the WHOLE lowercased token, so
`yesmodel`, `on-prem`, `false-positive:1b` still pass. `~` and the empty scalar
are already rejected (regex allowlist / blank check), so they need no entry.

### Decision: str-check slots beside the existing `is not None` fallback

**Choice**: Add `if x is not None and not isinstance(x, str): raise ValueError(...)`
for `model` and `embedding_model`, keeping the `x if x is not None else DEFAULT`
lines unchanged.
**Rationale**: Preserves the documented "checked is not None, not truthiness"
design (config.py:370-372). `review: false` is untouched (different field, bool
is legitimate) → `test_read_config_preserves_explicit_review_false` stays green.
An explicit `model: null`/bare `model:` still falls back to DEFAULT (None path),
not an error.

## Data Flow

    openkos.yaml ──safe_load──▶ read_config
                                  │  model/embedding_model present & not str?
                                  │      └─▶ raise ValueError ──▶ doctor check 2 [FAIL]
                                  ▼                                cfg=None → DEFAULT
                              Config(model: str)  ──▶ doctor checks 4/5 (safe)

    init prompt/--model ──▶ validate_model ──reserved-word/allowlist──▶ write_config

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/config.py` | Modify | `validate_model`: reserved-word frozenset check after blank check (#2). `read_config`: isinstance(str) guard for `model` and `embedding_model` before Config construction (#1). |
| `tests/unit/test_config.py` | Modify | Parametrized reserved-word rejection table + exact-token pass cases; str-type raise table for model & embedding_model; assert review:false + null-fallback unchanged. |
| `tests/unit/cli/test_doctor.py` | Modify | Regression: `model: yes` and `embedding_model: yes` → exit 1, `[FAIL] Config valid`, no traceback, later checks still render (#3). |

`main.py` and `ollama.py`: NO change (fork resolved to source-layer fix).

## Interfaces / Contracts

No signature changes. `read_config` now enforces its already-declared
`Config.model: str` / `embedding_model: str` contract. `validate_model` keeps
`(tag: str) -> str`, raising `ValueError` on reserved words. Error messages follow
existing tone (e.g. `"model must not be a YAML reserved word (yes/no/true/false/on/off/null)"`,
`"openkos.yaml: 'model' must be a string, got bool"`). Doctor remediation is the
existing `"fix openkos.yaml"` — no new set-volatility-style guidance.

## Testing Strategy (strict TDD — RED first)

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit (config) | #2 each reserved word (yes/Yes/YES/no/NO/on/off/true/false/null/NULL) raises; `yesmodel`/`on-prem`/`false-positive:1b` pass | `parametrize` raw→raises / raw→ok tables |
| Unit (config) | #1 `model: yes`, `model: 8`, `embedding_model: yes` raise ValueError; `model: null` falls back to DEFAULT; review:false survives | `parametrize` on read_config |
| Unit (doctor) | #3 non-str model/embedding_model → exit 1, `[FAIL] Config valid`, no crash, bundle check still `[PASS]` | `CliRunner`, write yaml post-init, assert accumulate |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file
classification, or process integration. (`validate_model`'s allowlist is
YAML-scalar-safety hardening, tightened here, not new attack surface.)

## Migration / Rollout

No migration. `openkos.yaml` schema unchanged; only stricter validation added.
Single-slice git revert.

## Open Questions

None. Fork resolved: source-layer fix, no doctor guard. No ADR warranted — the
decision is low-risk and trivially reversible (revert the two guards).
