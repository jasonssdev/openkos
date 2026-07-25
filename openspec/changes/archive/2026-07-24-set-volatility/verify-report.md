# Verification Report: set-volatility (#140)

```yaml
schema: gentle-ai.verify-result/v1
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 9/9
scenarios: 16/16
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:a6636cbf194b5f3c13b7cf6f38d14f94d2d68aefd8519f6b20665afece8134bc
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:6c36b089391100a72a8cc523a2cfb38ccdc66db71c1dbd9ae2315ed9f61ce7eb
```

**Change**: set-volatility (#140)
**Mode**: Strict TDD
**Branch**: feat/set-volatility (uncommitted working tree), merge-base `2f74fbc52a892bc0ef8534e27a73c9494df1d241`

## Completeness

20/20 tasks `[x]`. All 4 phases complete.

## Build & Tests (independently executed)

- `uv run pytest -q` -> 2051 passed in 106-114s. Matches self-report.
- `uv run ruff check .` -> All checks passed!
- `uv run ruff format --check .` -> 134 files already formatted.
- `uv run mypy .` -> Success: no issues found in 134 source files.

## Scope Guard

`git diff --stat` vs merge-base: exactly `src/openkos/cli/main.py` (+152/-7), `src/openkos/config.py` (+206/-0, pure additive), `tests/unit/cli/test_suggest_volatility.py` (+1/-1), `tests/unit/test_config.py` (+134/-0), plus new `tests/unit/cli/test_set_volatility.py`. `pyproject.toml`/`uv.lock` untouched (no ruamel/new dep). `read_config`, volatility inference/typing (`types.py`), and every other CLI command untouched -- main.py's only deletions are docstring text + the old hint line, no other command logic touched.

## Comment-Safety (load-bearing) -- VERIFIED

`config.set_type_tier` (config.py:540-616) is pure text-in/text-out, never a YAML round-trip. All 3 edit cases verified against fixtures containing real comments:

- Case (a) `test_set_type_tier_case_a_rewrites_existing_entry_value_only`: fixture has `model: gemma3`, `Project: static  # rarely changes`, `review: true` -- asserts ONLY `Person`'s value changes, everything else (including the trailing comment) byte-identical.
- Case (b) `test_set_type_tier_case_b_inserts_new_entry_under_existing_block` + empty-block variant: new entry inserted at canonical/fixed indent, rest untouched.
- Case (c) `test_set_type_tier_case_c_appends_fresh_block_when_header_absent` + `..._when_fully_commented`: confirms the shipped fully-commented template state (`# type_tiers:`) is correctly treated as absent (leading `#` never matches the header prefix) and appended fresh at EOF, rest of file untouched.

## Fail-Closed (load-bearing) -- VERIFIED with one coverage gap noted

All 6 shapes (inline flow-mapping, multiple headers, non-mapping scalar, tab-indented, inconsistent indent, duplicate entry) are parametrized (8 fixtures covering the 6 shapes, 3 sub-variants for non-mapping scalar) in `test_set_type_tier_fails_closed_on_unparseable_shapes`, each asserting `pytest.raises(ValueError)`.

CLI-level byte-identical + non-zero-exit is explicitly asserted for only ONE shape (`test_unparseable_config_shape_fails_closed`, inline flow-mapping). The other 5 shapes are not individually re-verified at the CLI/file-byte level.

Code trace (main.py:2459-2464): `config_text = layout.config_path.read_text(...)`; `new_config_text = config.set_type_tier(...)` wrapped in `try/except (OSError, ValueError)` that raises `typer.Exit(1)` BEFORE `fsio.write_atomic` is ever reached (write_atomic call is at line 2481, strictly after this catch block returns/raises). Since the except clause is generic over `ValueError` (not shape-specific), and one shape is proven end-to-end, the wiring is provably shape-agnostic -- but this is inference from a single sample, not direct evidence for the other 5. **WARNING**, not CRITICAL: the code path is structurally shape-agnostic (single generic try/except with no shape branching), so the risk of a shape-specific behavioral difference is low, but per-shape CLI-level assertions would raise this to a stronger PASS.

## Validation -- VERIFIED

- Tier not in {static,slow,volatile} -> `test_invalid_tier_rejected_no_write_no_commit`, exit != 0, no write.
- ConceptType not in REGISTRY (10 names) -> `test_invalid_concept_type_rejected_lists_valid_types`, stderr lists valid names including `Source`, exit != 0, no write.
- `Source` acceptance: `types.py` REGISTRY includes `Source` (default_tier "static"); CLI's `valid_types = {ot.name for ot in types.REGISTRY}` (not `CLASSIFIABLE_TYPES`) correctly includes it -- confirmed by source inspection. **WARNING**: no test actually invokes `set-volatility Source <tier>` as a positive write; `Source` only appears in the negative test's stderr listing assertion. Requirement text is satisfied by code, but the "Source is accepted" behavior lacks direct positive test coverage.

## Preview/Confirm/Commit -- VERIFIED

- Preview format `<Type>: <old-or-default> -> <new>` -> `test_preview_line_format_printed_before_confirm` (`"Person: slow -> volatile"`).
- `--auto` skip -> `test_auto_skips_the_prompt_and_writes`. Non-TTY+review refusal exit 1 -> `test_non_tty_without_auto_refuses`. Decline no write -> `test_interactive_decline_writes_nothing`. Accept writes -> `test_interactive_accept_writes`.
- Commit message `openkos: set-volatility <Type> -> <tier>` (WITH `openkos:` prefix) -> `test_successful_write_lands_and_autocommits` asserts exact message. One commit per successful write via existing shared `_autocommit` helper (unmodified).

## Idempotence -- VERIFIED

`test_idempotent_already_set_tier_is_noop` (no-op, exit 0, no write/commit) AND `test_explicit_override_equal_to_registry_default_is_real_write` (Person->slow, the REGISTRY default, correctly treated as a REAL write since not present in the parsed map) -- both present and passing.

## Hint Update -- VERIFIED

`suggest-volatility` hint changed from `"Next: edit type_tiers in openkos.yaml"` to `"Next: openkos set-volatility <ConceptType> <tier>"` (main.py:4519). Diff of `test_suggest_volatility.py` is exactly 1 line (the hint assertion) -- no other suggest-volatility assertion touched; full suite confirms non-regression (17/17 suggest-volatility tests pass along with the rest of the 2051).

## Strict TDD Compliance

| Check | Result |
|-------|--------|
| TDD Evidence reported | Yes -- full RED/GREEN/TRIANGULATE/SAFETY-NET table in apply-progress |
| All tasks have tests | 20/20 |
| GREEN confirmed (tests pass now) | Yes -- 2051/2051 |
| Assertion quality | No tautologies, no ghost loops, no orphan-empty-only checks found in `test_set_type_tier_*` or `test_set_volatility.py`; mock/assertion ratio low (2 monkeypatch vs 35 asserts in CLI test file) |

## Issues Found

**CRITICAL**: None.

**WARNING**:
1. Only 1 of 6 fail-closed shapes is verified at the CLI/file-byte-identical level (the other 5 verified only at the pure-core level); CLI wiring is structurally generic so risk is low but not directly proven per-shape.
2. No positive test invokes `set-volatility Source <tier>`; `Source`'s acceptance is provable by source inspection (REGISTRY-derived valid_types) but not exercised end-to-end.

**SUGGESTION**: Consider adding the remaining 5 CLI-level byte-identical fail-closed tests and one `Source`-tier positive-write test in a follow-up if reviewer risk tolerance requires it; not blocking.

## Verdict

**PASS WITH WARNINGS** -- 0 CRITICAL, 2 WARNING, 1 SUGGESTION. Full spec coverage (9/9 requirements, 16/16 scenarios) with real passing tests; quality gate green; scope tightly bounded to the intended 4+1 files; comment-safety and fail-closed behavior verified by direct code trace and fixture inspection. The two WARNINGs are coverage-depth gaps, not behavioral defects -- safe to archive.
