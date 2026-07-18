```yaml
schema: gentle-ai.verify-result/v1
verdict: pass_with_warnings
blockers: 0
critical_findings: 1
requirements: 8/8
scenarios: 12/12
test_command: "uv run pytest --cov=openkos --cov-report=term-missing"
test_exit_code: 0
test_output_hash: sha256:b91e37137ad18ac9c3c1ec4ff5e38471822757db668bd91d5f89b59a8b102400
build_command: "uv build"
build_exit_code: 0
build_output_hash: sha256:03fbf0d95614df07402b74cb20b054fab245c4c50a6bea3410c0f6510bd511b3
```

## Verification Report

**Change**: add-forget-command
**Mode**: Strict TDD

### Completeness
| Metric | Value |
|--------|-------|
| Tasks total | 12 |
| Tasks complete | 11 (Phases 1-4 fully done; Phase 5: 3/4 gates pass) |
| Tasks incomplete | 1 (5.2 ruff format --check fails on 2 files) |

### Build & Tests Execution
**Tests**: 316 passed, 0 failed, 0 skipped. Coverage: TOTAL 719 stmts/206 branches, 98.59% (fail_under=90 met).
Per-file: `bundle/index.py` 88% (missing lines 111,113,115,120-122 — `_link_identity` edge branches: quoted-title-only strip, empty-target-after-strip, external scheme URL, `..`-escape-to-None in a raw link target). `cli/main.py` 99% (446->448 partial branch). All other changed files 100%.

**ruff check .**: All checks passed.
**ruff format --check .**: FAILED — 2 files would be reformatted: `tests/unit/bundle/test_index.py`, `tests/unit/cli/test_forget.py` (both are trivial single-line collapses from ruff's line-length join rule, not content bugs).
**mypy src**: Success, no issues found in 12 source files (also verified `mypy tests`: clean, 18 files).
**uv build**: Successfully built `openkos-0.1.0.tar.gz` and `openkos-0.1.0-py3-none-any.whl`. Wheel smoke test (fresh venv install + `openkos --help`): `forget` command registered and described correctly.

### Spec Compliance Matrix
| Requirement | Scenario | Test | Result |
|---|---|---|---|
| Concept-ID Resolution and Path Safety | Traversal segment rejected | `test_forget.py::test_traversal_concept_id_refuses` | COMPLIANT |
| Concept-ID Resolution and Path Safety | Reserved filename rejected | `test_forget.py::test_reserved_basename_refuses` | COMPLIANT |
| Concept-ID Resolution and Path Safety | (bonus: absolute path) | `test_forget.py::test_absolute_concept_id_refuses` | COMPLIANT |
| Workspace Presence Check | Run outside a workspace | `test_forget.py::test_missing_workspace_refuses` | COMPLIANT |
| Nonexistent Concept Refusal | Concept file missing | `test_forget.py::test_nonexistent_concept_id_refuses` | COMPLIANT |
| Generic Index Entry Removal | Entry removed from any section | `test_index.py::test_remove_index_entry_drops_matching_bullet_from_any_section` (parametrized, all 4 sections) + `test_forget.py::test_successful_forget_of_sources_entry` + `test_successful_forget_of_hand_authored_bullet_across_link_forms` (5 param cases, Concepts/People) | COMPLIANT |
| Generic Index Entry Removal | No matching entry is a no-op | `test_index.py::test_remove_index_entry_zero_matches_returns_unchanged` | COMPLIANT |
| Log Entry on Forget | Plain log line recorded | `test_forget.py::test_successful_forget_of_sources_entry` (asserts `**Forget**` present, `tombstone` absent) | COMPLIANT |
| Review/Confirm Flow | Non-TTY without --auto refuses | `test_forget.py::test_non_tty_without_auto_refuses` | COMPLIANT |
| Review/Confirm Flow | --auto skips the prompt | `test_forget.py::test_auto_skips_the_prompt` (+ bonus `test_review_false_skips_the_prompt_like_auto`, `test_tty_confirm_prompts_then_writes`) | COMPLIANT |
| Catalog-Before-File Write Ordering | Catalog updated before file deletion | `test_forget.py::test_phase_b_ordering_catalog_before_file_delete` | COMPLIANT |
| Catalog-Before-File Write Ordering | Interrupted Phase B never leaves dangling catalog ref | same test (monkeypatched `fsio.remove_file` raises; asserts concept file still present as orphan, index/log already updated) | COMPLIANT |
| Malformed Bundle Handling | Malformed index.md | `test_forget.py::test_malformed_index_refuses` + `test_index.py::test_remove_index_entry_raises_valueerror_on_malformed_frontmatter` | COMPLIANT |

**Compliance summary**: 12/12 scenarios compliant (all covering tests passed at runtime).

### Correctness (Static Evidence + Non-Goals Audit)
| Item | Status | Notes |
|---|---|---|
| `src/openkos/model/okf.py` byte-unchanged | Verified | `git diff --stat -- src/openkos/model/okf.py` empty |
| No `bundle -> lint` import | Verified | `rg "lint" src/openkos/bundle/index.py` returns only a doc-comment reference to `lint.normalize_link` (naming a sibling concept for contrast), no `import` statement |
| No tombstones/purge/SQLite/inbound-dangling-link code | Verified | Log line is plain `**Forget**: Removed [...]`; no SQLite/operational-state code exists anywhere in `src/`; no inbound-link scan added |
| `docs/cli.md` pre-existing inaccuracies untouched | Verified | `git show HEAD:docs/cli.md` line 99 (SQLite "operational state" claim) and line 103 (dangling-link claim) are byte-identical to the diff's unchanged context lines; diff only inserts 2 new paragraphs between them documenting `forget`'s actual generic-section removal + reverse write ordering |
| Design deviation: log-line title uses `concept_id` not human "Title" | Documented | Recorded in apply-progress; satisfies spec's "naming the removed concept" wording; WARNING-level (design doc showed a placeholder, not a hard requirement) — not a spec violation |

### Coherence (Design)
| Decision | Followed? | Notes |
|---|---|---|
| D1 line-drop by resolved link target, frontmatter verbatim | Yes | `remove_index_entry` implemented exactly as designed |
| D2 bullet-match contract (markers, link regex, normalization, count semantics) | Yes | 0/1/>1 match semantics all tested and correct |
| D3 `fsio.remove_file` over inline `Path.unlink` | Yes | `remove_file(path) -> None` = `path.unlink()`, symmetric with existing `fsio` primitives |
| D4 Phase B ordering (index+log before unlink) | Yes | Verified by dedicated ordering test |
| D5 confirm gate reuse (verbatim from `ingest`) | Yes | `--auto` > `review:false` > TTY > non-TTY-refuse, all 4 paths tested |
| D6 no new Report/Result type | Yes | Plain `tuple[str, int]` return, inline preview strings |

### TDD Compliance
| Check | Result | Details |
|---|---|---|
| TDD Evidence reported | Yes | Full RED/GREEN/REFACTOR table present in apply-progress for all 3 code tasks |
| All tasks have tests | Yes | 3/3 code tasks (fsio, bundle/index, cli/forget) have dedicated test files |
| RED confirmed | Yes | Test files exist and match reported counts (12 fsio, 29 bundle/index total incl. pre-existing, 17 forget) |
| GREEN confirmed | Yes | All reported test files pass on this run (316 passed, 0 failed) |
| Triangulation adequate | Yes | Link-form matching parametrized 5x (CLI) + parametrized across all 4 sections + separate link-form parametrize (bundle layer); 0/1/>1-match counts each have a dedicated test |
| Safety Net for modified files | Yes | `fsio.py`, `bundle/index.py`, `cli/main.py` all pre-existing files with pre-existing passing test suites; new tests appended, no removals |

**TDD Compliance**: 6/6 checks passed

### Assertion Quality
No tautologies, no ghost loops, no assertion-without-production-call, no smoke-test-only patterns found in `test_forget.py` or the `test_index.py` additions. Refusal tests use `_snapshot(tmp_path)` before/after equality (real filesystem-state proof, not just exit-code checks). Success tests assert concrete file/index/log content changes, not just exit code 0.

**Assertion quality**: All assertions verify real behavior.

### Changed File Coverage
| File | Line % | Branch % | Uncovered | Rating |
|---|---|---|---|---|
| `src/openkos/fsio.py` | 100% | 100% | — | Excellent |
| `src/openkos/bundle/index.py` | ~92% (6/71 miss) | 86% (4/28 brpart) | L111,113,115,120-122 (`_link_identity` edge branches on malformed/exotic link targets embedded in hand-authored `index.md`, not on the CLI `concept_id` argument) | Acceptable |
| `src/openkos/cli/main.py` | 100% | ~98% (1/58 brpart) | L446->448 (one branch arm) | Excellent |
| `docs/cli.md` | N/A | N/A | — | doc-only |

**Average changed-file coverage**: ~97%, all above the 90% project gate.

### Issues Found
**CRITICAL**: None blocking spec/behavior. (See WARNING below re: task 5.2 formatting gate — not a spec or behavioral defect, but the task itself is not literally satisfied.)

**WARNING**:
1. Task 5.2 (`uv run ruff format --check .`) fails: 2 files need reformatting (`tests/unit/bundle/test_index.py`, `tests/unit/cli/test_forget.py`) — both are trivial single-line-length collapses ruff wants to auto-apply (`ruff format .` would fix both with zero behavior change). Task 5.2 as literally worded ("clean") is not yet satisfied; this blocks marking task 5.2 complete until `ruff format .` is run.
2. `bundle/index.py`'s `_link_identity` has 4 untested branches (88% file coverage) covering malformed/exotic raw link targets that could appear in a hand-authored or corrupted `index.md` (empty target after fragment/title strip, quoted-title-only line, external `scheme:` URL, and a `..`-escape that empties the path stack). None of these are reachable from the `concept_id` CLI argument (which has its own, fully-tested `_resolve_concept_path` validation) — they are defense-in-depth for arbitrary `index.md` content and are not spec-mandated scenarios, but are worth closing before this file is touched again.
3. Design deviation (log-line title = `concept_id`, not a human "Title") is honestly self-reported and defensible, but the design doc's placeholder wording could mislead a future reader; consider a design-doc addendum note (non-blocking).

**SUGGESTION**: None beyond the above.

### Verdict
**PASS WITH WARNINGS** — All 12 spec scenarios are compliant with passing runtime tests, `okf.py` is byte-unchanged, no `lint` import was introduced, all declared non-goals are respected, and the two pre-existing `docs/cli.md` inaccuracies are untouched. The only gap is task 5.2: `ruff format --check` fails on 2 test files due to a trivial, purely cosmetic line-wrap difference (no `ruff check` lint errors, no mypy errors, build succeeds, wheel smoke-tests fine). Recommend running `ruff format .` on those 2 files (mechanical, zero risk) before archiving; everything else is ready.

### Post-Bounded-Review Status
After the bounded review process (see verify-report addendum below), TWO CRITICAL issues were found and FIXED:
1. **Case-insensitive reserved-basename guard**: The reserved check was case-sensitive; on macOS (case-insensitive APFS), `forget INDEX --auto` bypassed it, deleting the real index.md. FIXED: case-insensitive comparison with `.lower()`.
2. **Concept-id normalization**: The raw concept_id was used un-normalized while the index match was normalized, causing `.`-prefixed paths like `./sources/x` to pass validation but skip the index match. FIXED: canonicalize concept_id once via `PurePosixPath.parts` and pass the canonical form throughout.

These two CRITICAL issues have been CORRECTED via a bounded review correction round (see sdd memory obs #929 for full lineage details). Final state after correction: **316 tests pass**, `okf.py` byte-unchanged, no `bundle -> lint` import, all 12 scenarios passing, bounded review APPROVED.
