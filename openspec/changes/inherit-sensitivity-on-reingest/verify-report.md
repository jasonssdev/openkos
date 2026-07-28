```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:058c549748021d34ce3c7c4aa983d39473895f17bcb1539bc36020a0d1eed7b3
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 1/1
scenarios: 13/13
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:2dd1db686ab0083e405e15c22f42a003d6712c63c9e27d27daaaa049f4fc9bca
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:186cc0c1aea3775e003f558152b988d5ca54bba36db4f9b6701f3eeb10d30ec3
```

## Verification Report

**Change**: inherit-sensitivity-on-reingest
**Version**: N/A (openspec delta)
**Mode**: Strict TDD
**Branch**: `fix/reingest-preserves-source-sensitivity`, 3 commits off `main` @ `03e92b3`, HEAD `0ffa992`

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 32 |
| Tasks complete | 32 |
| Tasks incomplete | 0 |

### Build & Tests Execution
**Build**: ✅ Passed
```text
$ uv run ruff check .          -> All checks passed! (exit 0)
$ uv run ruff format --check . -> 143 files already formatted (exit 0)
$ uv run mypy .                -> Success: no issues found in 143 source files (exit 0)
```

**Tests**: ✅ 2410 passed / ❌ 0 failed / ⚠️ 0 skipped
```text
$ uv run pytest -q
2410 passed in 87.61s (0:01:27)  (exit 0)

$ uv run pytest tests/unit/cli/test_ingest.py -q -k "reingest"
32 passed, 86 deselected in 7.17s  (exit 0)
```

**Coverage**: 97.52% total / threshold 90% -> ✅ Above. `src/openkos/cli/main.py`: 96% line, changed-lines-only zero missing except one defensive `except OSError` branch (see SUGGESTION below).

### Independent Experiments (not taken on trust)

| # | Experiment | Method | Result |
|---|---|---|---|
| 1 | Source no longer downgraded on re-ingest | Created scratch `git worktree` at unmodified `main`, copied this branch's `tests/unit/cli/test_ingest.py` in, ran `-k reingest`. Removed worktree after, confirmed `git status` clean on the real repo. | 9/32 reingest-tagged tests FAIL on `main` (exactly the 6 Phase-2 + 3 Phase-4 tests apply-progress claimed were genuinely RED pre-fix — independently reproduced, not trusted). All 32 PASS on HEAD. `test_reingest_does_not_downgrade_the_source_document` is one of the 9: FAIL on `main`, PASS on HEAD. |
| 2 | Derived-object stamping genuinely reaches the stamp path (not coincidence) | Same worktree run as #1. | `test_reingest_stamps_new_derived_objects_with_the_preserved_level` FAILS on `main` (asserts `confidential`; unfixed code stamps `private`, the config default) and PASSES on HEAD. Not a coincidental pass — the two values are provably distinct on the buggy path. |
| 3 | `workspace_floor` pin is load-bearing | In-place mutation: `workspace_floor=cfg.default_sensitivity` -> `workspace_floor=resolved_sensitivity` at the `_stage_derived_objects` call site, reran `-k reingest`, restored the file exactly, confirmed `git status`/`git diff` clean. | Mutation breaks `test_reingest_stamps_new_derived_objects_with_the_preserved_level` and `test_reingest_resolved_sensitivity_does_not_leak_into_workspace_floor` (`fake.calls == []` — LLM never called, extraction silently short-circuited). Pin confirmed load-bearing. Tree restored byte-identical. |
| 4 | Post-`forget` absent-concept path does not escalate `public` -> `private` | Read code: absent-concept branch assigns `resolved_sensitivity = cfg.default_sensitivity` directly, never calling `combine_sensitivity`. Then mutated it to call `combine_sensitivity(on_disk_sensitivity, cfg.default_sensitivity)` (`on_disk_sensitivity = None` in that branch) to force the trap, reran the forget test, restored the file exactly. | Mutated version reproduces the exact trap: `AssertionError: assert 'private' == 'public'`. Confirms (a) the trap is real, (b) the shipped code avoids it, (c) `test_reingest_after_forget_uses_the_config_default` genuinely pins it. Tree restored byte-identical (`git status` clean). |
| 5 | Unparseable frontmatter aborts, doesn't degrade | Read code path (`_read_source_sensitivity`'s `except Exception` -> `ValueError` -> outer `except (OSError, ValueError)` at the `ingest` try block -> `typer.Exit(code=1)`, before `write_atomic` at line 1865). Cross-checked against the passing test, which asserts `exit_code == 1`, `"refusing to ingest"` in stderr, and on-disk bytes unchanged from the pre-invoke corrupted state. | Confirmed real: `write_atomic(concept_path, ...)` is called well after the resolve block, so a raised `ValueError` provably prevents any write. `yaml.parser.ParserError` (raised by `frontmatter.loads` on the test's malformed YAML) is confirmed via direct `frontmatter.loads(...)` call to be a `yaml.YAMLError` subclass of bare `Exception`, not `OSError`/`ValueError` — the broad `except Exception` is necessary, not accidental over-catching. |
| 6 | Existing derived objects stay untouched | Ran `test_reingest_leaves_existing_derived_objects_byte_untouched`; read `_stage_derived_objects`'s create-only reconciliation (`write_exclusive`, unmodified by this diff). | Passes; behavior is provably out of scope of this diff (zero lines of the write_exclusive/reconciliation path touched by `git diff main..HEAD`). |
| 7 | Fresh ingest byte-identical to today | Full suite (2410 tests, including all pre-existing fresh-ingest tests, e.g. `test_sensitivity_matches_config_default`) green; fresh-ingest branch (`main.py:1768-1775` region) untouched by the diff. | No regression. |

### Spec Compliance Matrix
| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| Default Sensitivity from Config | Fresh ingest still stamps the config default | `test_ingest.py::test_sensitivity_matches_config_default` (pre-existing, unmodified) | ✅ COMPLIANT |
| Default Sensitivity from Config | Re-ingest preserves an on-disk value raised above the config default | `test_reingest_does_not_downgrade_the_source_document` + `test_reingest_stamps_new_derived_objects_with_the_preserved_level` | ✅ COMPLIANT (proven via differential experiment #1/#2) |
| Default Sensitivity from Config | Re-ingest raises to a config default above the on-disk value | `test_reingest_raises_when_workspace_default_exceeds_on_disk` | ✅ COMPLIANT |
| Default Sensitivity from Config | Re-ingest with equal values is byte-identical to today | `test_reingest_with_equal_values_writes_byte_identical_output` | ✅ COMPLIANT |
| Default Sensitivity from Config | Existing derived objects are untouched by re-ingest | `test_reingest_leaves_existing_derived_objects_byte_untouched` | ✅ COMPLIANT |
| Default Sensitivity from Config | Missing on-disk sensitivity floors to private | `test_reingest_with_missing_on_disk_sensitivity_resolves_to_private` | ✅ COMPLIANT (resolves WARNING-1) |
| Default Sensitivity from Config | Blank on-disk sensitivity floors to private | `test_reingest_with_blank_on_disk_sensitivity_resolves_to_private` | ✅ COMPLIANT (resolves WARNING-1) |
| Default Sensitivity from Config | Unrecognized or non-string on-disk sensitivity fails closed to confidential | `test_reingest_with_unknown_on_disk_sensitivity_fails_closed_to_confidential` (unrecognized-string disjunct) + `test_reingest_with_non_string_on_disk_sensitivity_fails_closed_to_confidential` (non-string disjunct) | ✅ COMPLIANT |
| Default Sensitivity from Config | Extraction gate still reads the workspace default, not the resolved value | `test_reingest_resolved_sensitivity_does_not_leak_into_workspace_floor` + `test_extract_gate_still_reads_workspace_floor` (pre-existing, unmodified, still green) | ✅ COMPLIANT (proven via mutation experiment #3) |
| Default Sensitivity from Config | Preview reports a preserved level | `test_reingest_preview_reports_preserved_level` | ✅ COMPLIANT |
| Default Sensitivity from Config | Preview reports a raised level | `test_reingest_preview_reports_raised_level` | ✅ COMPLIANT |
| Default Sensitivity from Config | Preview reports an unchanged level | `test_reingest_preview_reports_unchanged_level` | ✅ COMPLIANT |
| Default Sensitivity from Config | Preview reports the workspace default after `forget` | `test_reingest_after_forget_preview_reports_workspace_default_clause` | ✅ COMPLIANT (resolves WARNING-2; scenario added to both spec copies) |

**Compliance summary**: 13/13 scenarios fully compliant, 0 partial. (WARNING-1 and WARNING-2 are both resolved below.)

### Correctness (Static Evidence)
| Requirement | Status | Notes |
|------------|--------|-------|
| Resolve-before-build | ✅ Implemented | `had_prior_source` gate at `main.py:1708`, resolve block before `build_source_concept` call, matches design exactly |
| `workspace_floor` untouched | ✅ Implemented | `main.py:1739`, literal `cfg.default_sensitivity`; confirmed load-bearing via mutation |
| Abort-not-degrade on unreadable/unparseable frontmatter | ✅ Implemented | `_read_source_sensitivity` (`main.py:1138-1165`), never returns a default on failure |
| Canonical spec matches delta | ✅ Confirmed | `git diff main..HEAD -- openspec/specs/ingestion/spec.md` is byte-identical in content to the delta file's added text |
| Architecture guard (`test_ingest_and_forget_do_not_reference_state_fts`) | ✅ Passes for the right reason | Reworded comment ("is always named") contains no substring "state"; confirmed via `grep -i state` on the full diff — zero matches. Meaning preserved ("is always stated" -> "is always named" — same claim, different verb) |

### Coherence (Design)
| Decision | Followed? | Notes |
|----------|-----------|-------|
| Resolve before build, not merge after build | ✅ Yes | |
| `_read_source_sensitivity` fails closed, never degrades | ✅ Yes | |
| `workspace_floor` stays literal | ✅ Yes | Proven load-bearing, not just asserted |
| ADR-0010, additive only, Status Proposed | ✅ Yes | Does not supersede ADR-0003/0008/0009; those three files are byte-unedited (`git diff main..HEAD` empty for all three) |
| Preview wording table (4 variants: preserved/raised/unchanged/no-prior-file) | ✅ Yes | Code implements all 4; the delta and canonical specs now pin all 4, including the post-forget "from the workspace default" variant — see WARNING-2 |

### Issues Found

**CRITICAL**: None

**WARNING**:
1. **Delta spec Scenario 6 ("Malformed on-disk sensitivity fails closed to confidential") is factually inaccurate for its "missing" disjunct, and that disjunct is untested at the `ingest` integration level.** The scenario's GIVEN clause groups "missing, non-string, or otherwise unrecognized" as all resolving to `confidential`. Verified directly: `okf.combine_sensitivity(None, "public")` returns `"private"`, not `"confidential"` (confirmed both by direct invocation and by the pre-existing, unmodified, still-passing unit test `tests/unit/model/test_okf.py:1002`). This exact correction is already stated in this change's own `design.md` ("A missing key or blank string floors at `private`, not `confidential`"), but the design's correction was never propagated into the delta spec's Scenario 6 text, nor into the now-canonical `openspec/specs/ingestion/spec.md`. Only the "unrecognized string" disjunct (`"secret"`) is exercised by `test_reingest_with_unknown_on_disk_sensitivity_fails_closed_to_confidential`; no test forges a Source file with a genuinely *missing* `sensitivity` key to confirm end-to-end behavior for that disjunct. Recommend: split Scenario 6 into two scenarios (missing/blank -> `private`; non-string/unrecognized-string -> `confidential`), matching the requirement paragraph's own more careful "malformed or non-string" wording, and add one integration test for the missing-key sub-case before archive. Not a runtime defect — the implementation and the underlying primitive are correct and already well-tested; this is a spec-accuracy and integration-coverage gap in newly authored text.
   **RESOLVED by `cf36e57` (same branch).** Both spec copies (`openspec/changes/inherit-sensitivity-on-reingest/specs/ingestion/spec.md` and the canonical `openspec/specs/ingestion/spec.md`) now carry three separate scenarios — "Missing on-disk sensitivity floors to private", "Blank on-disk sensitivity floors to private", and "Unrecognized or non-string on-disk sensitivity fails closed to confidential" — replacing the single lumped Scenario 6. `cf36e57` also added `test_reingest_with_missing_on_disk_sensitivity_resolves_to_private`, closing the missing-key integration-coverage gap. A follow-up review found that test's original assertion did not discriminate a correct `combine_sensitivity` call from an implementation that just wrote `cfg.default_sensitivity` (both land on `private` when the config default is left at its packaged value); it was strengthened to set `default_sensitivity: public` so the two implementations diverge, and a sibling test, `test_reingest_with_blank_on_disk_sensitivity_resolves_to_private`, was added for the blank/whitespace-only disjunct using the same technique — closing the last untested case named in the new spec split.
2. **The post-forget "from the workspace default" preview clause (`main.py:1832`) is implemented per design's 4-row table but is not pinned by any delta-spec scenario or any test asserting that exact stdout string.** `grep -n "from the workspace default" tests/unit/cli/test_ingest.py` returns no matches. Recommend adding a scenario + test, or accept as an intentionally under-specified corner of the preview surface.
   **PARTIALLY RESOLVED by `cf36e57` (same branch), fully resolved in this round.** `cf36e57` added `test_reingest_after_forget_preview_reports_workspace_default_clause`, which asserts the exact stdout string `"~ bundle/sources/notes.md (regenerated -- sensitivity private from the workspace default)"` — that half was accurate. It did NOT add a spec scenario, and an earlier draft of this report incorrectly claimed one existed ("the delta/canonical specs carry the corresponding scenario"); neither `openspec/specs/ingestion/spec.md` nor the change's own delta spec had a post-`forget` scenario at that point. This round adds the missing "Preview reports the workspace default after `forget`" scenario to both the delta and canonical spec files, so the test and the spec now agree.

**SUGGESTION**:
1. The `except OSError` branch in `_read_source_sensitivity` (`main.py:1152-1156`) is unreached by any test (`coverage: 1153` uncovered) since `concept_path.exists()` is always confirmed true immediately before the call; the branch only guards a TOCTOU race or permission error. Defensible as defensive coding for a security-classification read, but currently dead in test terms — consider a targeted `monkeypatch`-based test if 100% branch coverage on this file is ever required, or leave as documented defensive code.
2. The broad `except Exception` in `_read_source_sensitivity` is intentional and load-bearing (confirmed: `frontmatter.loads` raises `yaml.parser.ParserError`, a `yaml.YAMLError`/`Exception` subclass, not `OSError`/`ValueError`), and the design doc explicitly calls out this gotcha. No change recommended; documenting this here only because the task asked for an explicit verdict.

### TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ✅ | Found in apply-progress, full RED/GREEN/TRIANGULATE/SAFETY NET table |
| All tasks have tests | ✅ | 13/13 new behaviors had test files at this report's original snapshot (HEAD `0ffa992`); now 17/17 — `cf36e57` added 2 (missing-key resolution, post-forget preview clause), a follow-up fix closed WARNING-1/2 above by strengthening `test_reingest_with_missing_on_disk_sensitivity_resolves_to_private` (no new function) and adding `test_reingest_with_blank_on_disk_sensitivity_resolves_to_private` (+1), and this correction round added `test_reingest_with_non_string_on_disk_sensitivity_fails_closed_to_confidential` (+1) for the untested non-string disjunct |
| RED confirmed (tests exist) | ✅ | 13/13 test files verified present in `tests/unit/cli/test_ingest.py` at this report's original snapshot; 17/17 present as of the current branch tip |
| GREEN confirmed (tests pass) | ✅ | 122/122 in `test_ingest.py`, 2414/2414 full suite, independently re-run (current branch tip); `tests/unit/cli/test_ingest.py -k reingest` now 36/36 (was 32/32 at the original snapshot) |
| RED cross-validated independently | ✅ | Reproduced the exact reported RED set (9/32 reingest-tagged tests fail) via a scratch `git worktree` at unmodified `main` with this branch's test file copied in — not taken on the apply agent's word |
| Triangulation adequate | ✅ | 10 distinct Phase-2 scenarios + 3 distinct Phase-4 preview-direction scenarios |
| Safety Net for modified files | ✅ | 105/105 baseline before Phase 2 edits, 115/115 before Phase 4 edits (per apply-progress, consistent with cumulative counts observed) |

**TDD Compliance**: 7/7 checks passed

---

### Test Layer Distribution
| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit (CLI integration via `CliRunner`, real OKF bundles, faked LLM only) | 17 (13 at this report's original snapshot + 2 from `cf36e57` + 1 from the WARNING-1/2 follow-up fix + 1 from this correction round) | 1 (`tests/unit/cli/test_ingest.py`) | pytest, typer.testing |
| Integration | 0 | 0 | not applicable |
| E2E | 0 | 0 | not applicable |
| **Total** | **17** | **1** | |

---

### Changed File Coverage
| File | Line % | Branch % | Uncovered Lines | Rating |
|------|--------|----------|-----------------|--------|
| `src/openkos/cli/main.py` (whole file; new code ~1138-1165, ~1696-1717, ~1810-1834) | 96% | — (704 branches, 23 partial, whole file) | 1153 (defensive `OSError` branch in new helper, see SUGGESTION-1); remainder is pre-existing unrelated code | ✅ Excellent (new code itself has zero missing lines except the one defensive branch) |

**Average changed file coverage**: 96% (whole-file; new code is effectively 100% except one defensive branch)

---

### Assertion Quality
✅ All assertions verify real behavior. Scanned all 17 new test functions (13 at this report's original snapshot + 4 added across follow-up fixes and this correction round): no tautologies, no ghost loops (no loops over collections at all), no CSS/implementation-detail coupling, no ratio problems (`_patch_llm`/`_set_source_sensitivity` are test-setup helpers, not `vi.mock`-style call-count assertions). `test_reingest_raises_when_workspace_default_exceeds_on_disk` was strengthened in this round with a final re-ingest step so its expected value is not a bare pass-through of the config default; confirmed to FAIL against a mutated production implementation that writes `cfg.default_sensitivity` unconditionally, then confirmed the production file was restored byte-identical. Every test asserts either on-disk frontmatter content, exit code, stdout preview text, or stderr text — all observable behavior through the real CLI (`CliRunner`) against real tmp-path OKF bundles.

**Assertion quality**: 0 CRITICAL, 0 WARNING

---

### Quality Metrics
**Linter**: ✅ No errors (`uv run ruff check .`)
**Formatter**: ✅ No diffs (`uv run ruff format --check .`, 143 files already formatted)
**Type Checker**: ✅ No errors (`uv run mypy .`, 143 source files)

### Verdict
PASS WITH WARNINGS
Implementation is correct, all 7 invariants independently proven via real differential/mutation experiments (not trusted from test names or the apply report); 2 non-blocking WARNINGs concern spec-text accuracy and coverage completeness for a rarely-reached corner case, not runtime behavior.
