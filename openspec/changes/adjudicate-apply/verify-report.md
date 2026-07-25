```yaml
schema: gentle-ai.verify-result/v1
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 12/12
scenarios: 18/18
test_command: uv run pytest -q
test_exit_code: 0
test_output_hash: sha256:90c7fa6c15fb6405869fc4cc1de0b441d645996a7cf5c0128ce1f7e3b2207671
build_command: uv run mypy .
build_exit_code: 0
build_output_hash: sha256:f22722ad615746b653b99513b65b320be88ca4dea31fb80dd9c9945aaae1b984
```

## Verification Report

**Change**: adjudicate-apply (#137, Slice 2b-ii — destructive interactive slice)
**Version**: N/A
**Mode**: Strict TDD

**Note on requirement/scenario counts**: The dispatch brief stated "12 requirements, 29 scenarios." The actual retrieved spec delta (`openspec/changes/adjudicate-apply/specs/entity-resolution-adjudication/spec.md`, cross-checked against the Engram `sdd/adjudicate-apply/spec` artifact — identical content) contains **12 requirements and 18 scenarios**. Counts below use the actual retrieved numbers per verify-skill rule ("never invent envelope totals").

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 28 |
| Tasks complete | 28 |
| Tasks incomplete | 0 |

### Build & Tests Execution
**Build**: ✅ Passed (`uv run mypy .` — Success: no issues found in 133 source files)
**Lint**: ✅ Passed (`uv run ruff check .` — All checks passed!)
**Format**: ✅ Passed (`uv run ruff format --check .` — 133 files already formatted)

**Tests**: ✅ 2023 passed / 0 failed / 0 skipped
```text
$ uv run pytest -q
2023 passed in 100.84s (0:01:40)

$ uv run pytest tests/unit/cli/test_adjudicate.py -q
61 passed in 2.93s   (59 def-level tests; +2 from the 3-way `declining_input` parametrize)
```

**Coverage**: New `_run_adjudicate_apply` helper (lines ~435-550) — fully covered EXCEPT the `prepare_merge` `except (OSError, ValueError)` branch (lines 498-504), which has zero runtime coverage. See WARNING-1 below. Whole-file coverage of `src/openkos/cli/main.py` is not meaningful as a metric here (single 5600-line CLI module); scoped instead to the new function.

### Scope Guard (destructive-change critical check)
```
git diff --stat <merge-base> -- .
 src/openkos/cli/main.py           | 142 +++++++++
 tests/unit/cli/test_adjudicate.py | 590 ++++++++++++++++++++++++++++++++++++++
 2 files changed, 732 insertions(+)
```
- **Only** `src/openkos/cli/main.py` and `tests/unit/cli/test_adjudicate.py` changed (plus new, untracked `openspec/changes/adjudicate-apply/` planning artifacts). ✅
- `git diff <merge-base> -- src/openkos/cli/main.py` shows **142 insertions, 0 deletions** — the entire diff is additive: a new `_run_adjudicate_apply()` helper, a new `apply` typer.Option, a D1 mutual-exclusion guard, unconditional `index_path`/`log_path` computation, and the `if apply: ...; return` call site. Zero deletions means it is structurally impossible for this diff to have touched a single line inside `prepare_merge`, `merge_core`, `_autocommit`, `_resolve_concept_path`, `merge`, or `unmerge` — confirmed by inspection, none of those function bodies appear in the diff. ✅
- `merge_core` is called with the design-corrected 4-arg signature `merge_core(layout.bundle_dir, index_path, log_path, prepared)`, matching design D7 and the real signature at `merge_core` (design table row, verified). ✅

**Verdict on scope guard: PASS.** This is the most important guardrail for a destructive change and it holds cleanly — no production code outside the new, additive `_run_adjudicate_apply` helper and the `adjudicate` command's new flag/guard was touched.

### Spec Compliance Matrix
| Requirement | Scenario | Test | Result |
|-------------|----------|------|--------|
| `--apply` Eligibility Filter | SAME 2-member group is offered | `test_adjudicate_apply_offers_a_same_two_member_group` | ✅ COMPLIANT |
| `--apply` Eligibility Filter | DIFFERENT group is never offered | `test_adjudicate_apply_never_prompts_different_or_uncertain_groups` (covers DIFFERENT **and** UNCERTAIN) | ✅ COMPLIANT |
| `--apply` Eligibility Filter | SAME group with >2 members is skipped, not prompted | `test_adjudicate_apply_same_group_with_three_members_is_skipped_not_prompted` | ✅ COMPLIANT |
| Survivor/Absorbed Preview And Prompt | Preview precedes the exact prompt text | `test_adjudicate_apply_preview_precedes_the_exact_prompt_text` | ✅ COMPLIANT |
| Prompt Response Semantics | `y` applies the merge | `test_adjudicate_apply_accepts_merge_updates_filesystem_and_ledger` (`input="y\n"`) | ✅ COMPLIANT |
| Prompt Response Semantics | empty input does not merge | `test_adjudicate_apply_declining_inputs_do_not_merge_and_continue["\n"]` | ✅ COMPLIANT |
| Prompt Response Semantics | `skip` does not merge | `test_adjudicate_apply_declining_inputs_do_not_merge_and_continue["skip\n"]` | ✅ COMPLIANT |
| Prompt Response Semantics | `N`/`n` does not merge | `test_adjudicate_apply_declining_inputs_do_not_merge_and_continue["n\n"]` | ✅ COMPLIANT |
| Accepted Merge Executes And Is Reversible | Applied merge updates filesystem and ledger | `test_adjudicate_apply_accepts_merge_updates_filesystem_and_ledger` | ✅ COMPLIANT |
| Accepted Merge Executes And Is Reversible | Applied merge is unmerge-reversible | `test_adjudicate_apply_then_unmerge_restores_the_absorbed_member` (real merge → real `unmerge`, no mocks on write path) | ✅ COMPLIANT |
| Per-Merge Auto-Commit | Two applied merges produce two commits | `test_adjudicate_apply_two_accepted_merges_produce_two_separate_commits` (real `git log --format=%H` count) | ✅ COMPLIANT |
| Stale-Id Guard Across Sequential Merges | Later group referencing an already-absorbed member is skipped | `test_adjudicate_apply_overlapping_groups_second_reports_already_merged` (real overlapping groups `a/b` + `b/c`, first `y`, second skipped, no crash) | ✅ COMPLIANT |
| `--apply` Rejects `--json` | `--apply --json` exits 2 | `test_adjudicate_apply_and_json_rejected_with_exit_code_two` | ✅ COMPLIANT |
| `--apply` Composes With `--same-only` As A No-Op | `--apply --same-only` behaves like `--apply` | `test_adjudicate_apply_same_only_is_a_no_op_composition` (byte-diff of stdout, workspace-path line stripped) | ✅ COMPLIANT |
| Mid-Run Write Failure Stops The Run | `merge_core` failure halts remaining groups | `test_adjudicate_apply_mid_run_merge_core_failure_stops_the_run` | ✅ COMPLIANT (see SUGGESTION-1 for a scope note) |
| End-Of-Run Summary With Breakdown | Summary reflects applied and skipped counts | `test_adjudicate_apply_summary_reflects_applied_and_skipped_counts` | ✅ COMPLIANT |
| Empty / No-Eligible State | No eligible groups, nothing applied | `test_adjudicate_apply_no_eligible_groups_prints_nothing_to_apply` (asserts filesystem snapshot unchanged) | ✅ COMPLIANT |
| Plain `adjudicate` Is Unchanged | Non-`--apply` behavior is unaffected | Full pre-existing `test_adjudicate.py` suite (plain/`--json`/`--same-only` paths) + full 2023-test suite, all green | ✅ COMPLIANT |

**Compliance summary**: 18/18 scenarios compliant.

### Correctness (Static Evidence)
| Requirement | Status | Notes |
|------------|--------|-------|
| D1 mutual exclusion | ✅ Implemented | First statement in `adjudicate` body, before workspace gate — fail-fast, zero side effects, matches design |
| D3 eligibility (`survivor=member_ids[0]`, `absorbed=member_ids[1]`) | ✅ Implemented | `survivor_id, absorbed_id = group.member_ids` after `len(...) == 2` filter |
| D4 stale-id guard | ✅ Implemented | `_resolve_concept_path` × 2 in `try/except ValueError`, reused verbatim (not modified) |
| D5 preview line | ✅ Implemented | Exact format from `PreparedMerge.sensitivity_before/after`, `touched_files`, canonical absorbed id |
| D6 prompt parse | ✅ Implemented | `typer.prompt(...)`, strict `{"y","yes"}` allowlist (case-insensitive via `.lower()`), all others decline |
| D7 apply + per-merge commit | ✅ Implemented | `prepare_merge` → `merge_core(bundle_dir, index_path, log_path, prepared)` (4-arg, design-corrected) → `_autocommit` per accepted merge |
| D8 mid-run failure | ✅ Implemented | try/except `(OSError, ValueError)` around **both** `prepare_merge` and `merge_core` calls → stderr + `typer.Exit(code=1)` |
| D9 end summary | ✅ Implemented | Four counters, exact wording `applied X, skipped Y (N>2: a, already-merged: b, declined: c)`, "nothing to apply -- " prefix on zero-eligible |

### Coherence (Design)
| Decision | Followed? | Notes |
|----------|-----------|-------|
| D1–D9 | ✅ Yes | All 9 architecture decisions implemented exactly as specified in `design.md`, including the `merge_core` 4-arg signature correction the design itself flagged as a proposal mismatch |
| "All logic lives inline in `adjudicate`" | ✅ Yes (as `_run_adjudicate_apply` helper) | Design says "inline in `adjudicate`"; implementation factors it into a same-file helper function called from `adjudicate` — a reasonable, non-deviating interpretation (still one file, same command, easier to unit-test in isolation) |
| Zero-eligible message format | ✅ Yes, reconciled | apply-progress documents a deliberate, non-contradictory merge of the spec's "clear nothing to apply message" text and the design's exact `applied 0, skipped 0 (...)` breakdown format into one line — verified in code, matches both |

### Strict TDD

#### TDD Compliance
| Check | Result | Details |
|-------|--------|---------|
| TDD Evidence reported | ✅ | Full RED/GREEN table present in `apply-progress.md` for all 6 phases |
| All tasks have tests | ✅ | 28/28 tasks; every RED task names its exact failing test, every GREEN task names the implementation that turned it green |
| RED confirmed (tests exist) | ✅ | All named test functions exist in `tests/unit/cli/test_adjudicate.py` (verified by direct read, line-by-line) |
| GREEN confirmed (tests pass) | ✅ | 2023/2023 pass on independent re-run; `test_adjudicate.py` 61/61 pass in isolation |
| Triangulation adequate | ✅ | Prompt-parse triangulated across 4 distinct inputs (`y`, `\n`, `n\n`, `skip\n`); eligibility triangulated across SAME-2/DIFFERENT/UNCERTAIN/SAME->2 |
| Safety Net for modified files | ✅ | Task 1.3 ran the full pre-existing `test_adjudicate.py` suite as an explicit regression baseline before further changes |

**TDD Compliance**: 6/6 checks passed

#### Test Layer Distribution
| Layer | Tests | Files | Tools |
|-------|-------|-------|-------|
| Unit (CliRunner, monkeypatched candidate/adjudicate fns) | 16 | 1 | `typer.testing.CliRunner`, `pytest.monkeypatch` |
| Integration (real filesystem + real git, no mocks on write path) | 2 | 1 | real `git` subprocess via `isolate_git_identity` fixture |
| **Total (new `--apply` tests)** | **18** | **1** | |

#### Changed File Coverage
| File | Coverage of new code | Uncovered Lines | Rating |
|------|----------------------|------------------|--------|
| `src/openkos/cli/main.py` (`_run_adjudicate_apply`, ~L435-550) | All branches except one | L498-504 (`prepare_merge` except-block) | ⚠️ Acceptable — see WARNING-1 |
| `src/openkos/cli/main.py` (`adjudicate` command body, L3908-4102, incl. new option + D1 guard + call site) | 100% | — | ✅ Excellent |
| `tests/unit/cli/test_adjudicate.py` | N/A (test file) | — | — |

#### Assertion Quality
No tautologies, ghost loops, or ineffective assertions found. All new tests exercise real production code paths: `runner.invoke(app, [...])` with real `CliRunner`, real filesystem assertions (`.exists()`, file content reads), and two tests use a real git repository with zero mocks on the write path (`test_adjudicate_apply_then_unmerge_restores_the_absorbed_member`, `test_adjudicate_apply_two_accepted_merges_produce_two_separate_commits`). No implementation-detail coupling (no CSS/mock-call-count assertions — not applicable to this CLI codebase). Mock/assertion ratio is low; `monkeypatch.setattr` targets are all at the `find_candidates`/`adjudicate_candidates`/`merge_core` seam level (legitimate Ollama/LLM and destructive-failure simulation boundaries), never the code under test itself.

**Assertion quality**: ✅ All assertions verify real behavior

#### Quality Metrics
**Linter**: ✅ No errors (`ruff check .` — All checks passed!)
**Formatter**: ✅ No diffs (`ruff format --check .` — 133 files already formatted)
**Type Checker**: ✅ No errors (`mypy .` — Success: no issues found in 133 source files)

### Item 5 — `_seed_commit` Test Helper Analysis (mandatory scrutiny per dispatch)

**Finding: test-infrastructure only, and does NOT mask a production gap.**

Evidence:
1. `_seed_commit` (test file, `vcs_git.commit_paths` wrapper) appears **only** in `tests/unit/cli/test_adjudicate.py` — zero occurrences in any `src/` file. Confirmed by direct grep across the diff and the full file.
2. Root cause it works around: `_write_doc()` in test setup writes concept files directly to the filesystem, bypassing any openkos verb, so those files are never git-tracked. `git add -- <path>` for a deleted path that was never tracked fails (`fatal: pathspec ... did not match any files`), which is a genuine, pre-existing git behavior unrelated to this change.
3. **Traced production reachability of the same condition**: `_autocommit` (reused verbatim, unchanged by this slice) is explicitly documented as "best-effort, non-fatal" — it checks `vcs_git.repo_root(root) is None` and `not vcs_git.has_git_identity(root)` **before** ever calling `commit_paths`, and wraps the `commit_paths` call itself in `except (vcs_git.GitError, OSError)` that prints a stderr WARNING and returns normally (never raises). This exact pattern is called from **8 call sites** in `main.py` — `ingest`/`forget`/`relate`/`merge`/`unmerge`/`reconcile` (all pre-existing, per file docstring) plus the new `_run_adjudicate_apply` (line 531) — i.e., every mutating verb, including the pre-existing `merge` command this slice reuses.
4. Two scenarios where a bundle file could theoretically be on-disk-but-untracked when `--apply` later tries to merge/delete it:
   - **No repo / no git identity**: `_autocommit`'s own guard clauses catch this *before* attempting `git add`, for both the file's original writer verb AND for `--apply`'s own commit attempt — same non-fatal WARNING path, no crash, no divergence from pre-existing `merge` behavior.
   - **Repo + identity present, but a prior verb's own `commit_paths` genuinely failed** (e.g., transient git-commit failure): `_autocommit` already swallows this as a non-fatal WARNING for the *original* verb. If `--apply` later tries to delete that never-committed file, `_autocommit`'s **own** `except (vcs_git.GitError, OSError)` catches the resulting `git add` failure identically — non-fatal WARNING, loop continues (`applied` still increments since `_autocommit` never raises upward to the D7/D8 call site). No crash, no data loss (merge_core's filesystem writes already landed), consistent with how `merge` (unchanged) already behaves in the same theoretical edge case.
5. Conclusion: the `_seed_commit` helper accurately mirrors real usage (every mutating verb already auto-commits its own writes in the common case), and the narrow edge case it works around in tests (raw filesystem write bypassing any verb) has **no reachable, unguarded production analog** — `_autocommit`'s own non-fatal exception handling (unchanged, reused verbatim, and already relied upon by `merge`/`ingest`/etc. before this slice) already absorbs the identical failure mode. This is not a gap introduced or exposed by this slice.

### Issues Found

**CRITICAL**: None

**WARNING**:
1. **Untested `prepare_merge` except-branch** (`src/openkos/cli/main.py` L498-504): D8 wraps *both* `prepare_merge` and `merge_core` in identical `try/except (OSError, ValueError)` blocks, but the only mid-run-failure test (`test_adjudicate_apply_mid_run_merge_core_failure_stops_the_run`) monkeypatches `merge_core` only. The `prepare_merge`-failure except-block has never been observed to execute at runtime — a genuine strict-TDD RED/GREEN gap for that specific branch (the code is a near-identical duplicate of the covered `merge_core` except-block, so risk is low, but it is untested application code on a destructive-failure exit path). Not a spec-scenario gap (the spec's literal scenario text specifies "fails inside `merge_core`"), so this does not block the verdict, but should be closed with a follow-up test before further slices build on this helper.

**SUGGESTION**:
1. The "Mid-Run Write Failure Stops The Run" requirement prose asserts "commits from prior successfully applied merges... MUST remain intact," but the spec's own scenario (and its covering test) has the *first* accepted merge be the one that fails — so no test exercises "an earlier merge succeeds and commits, then a later merge fails," which would be the strongest direct proof that prior commits survive a later failure. This is logically implied by the code structure (each merge commits synchronously before the next iteration; there is no rollback code) and independently corroborated by the separate two-commits test, but a combined 3-group test (`merge1: y` succeeds → `merge2: y` fails) asserting `git log` still shows exactly 1 commit post-failure would close this gap directly. Non-blocking.
2. Minor self-report drift: `apply-progress.md` (task 1.3) states the pre-existing regression baseline was "45 tests," but the current file has 59 `def test_` functions of which 18 are new `--apply` tests, implying a baseline of 41. This does not affect the actual (currently passing) test evidence and is not a code defect — likely a stale count carried from an earlier point in development. Worth a quick self-report hygiene note for future apply runs, non-blocking.

### Verdict
**PASS WITH WARNINGS** — Scope guard holds cleanly (destructive core functions genuinely untouched, zero deletions in the diff), all 18 spec scenarios have passing covering tests including real, mock-free filesystem/git integration tests for the merge/unmerge round-trip and the stale-id guard, the full 2023-test suite plus lint/format/mypy are all green, and the test-only `_seed_commit` helper is confirmed not to mask a reachable production gap. Two non-blocking WARNINGs (an untested duplicate except-branch; a scenario-count/spec-count correction) and two SUGGESTIONs are recorded for the record but do not block archive.
