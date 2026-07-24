# Verify Report: purge-transactional-cleanup (git-lifecycle Slice 3)

**Change**: purge-transactional-cleanup | **Branch**: feat/purge-transactional-cleanup
**Implementation commit**: 42af547 | **Planning commit**: cda2beb
**Verdict**: PASS WITH WARNINGS

## 1. Ground Truth

| Command | Result |
|---|---|
| `uv run pytest` | 1962 passed in 97.73s |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 132 files already formatted |
| `uv run mypy .` | Success, no issues found in 132 source files |

## 2. Coverage

`uv run pytest --cov=src --cov-branch`: **97.79% total** (>=90% required, CI `fail_under=90` satisfied). No "No data collected" artifact this run (unlike Slices 1-2).

- `src/openkos/lint.py`: 100%
- `src/openkos/vcs/git.py`: 98% — missing lines 398, 425 (`is_clean`/`has_published_commits`, pre-existing, untouched by this diff) and 816->818 (`expunge_paths`, pre-existing). The NEW `paths_dirty` function (lines 477-493) is 100% covered by 4 dedicated tests.
- `src/openkos/cli/main.py`: 96% — remaining misses are pre-existing branches unrelated to this change (verified new purge/lint/status/doctor blocks are exercised by the targeted test runs below).

## 3. Scenario Conformance (4 capabilities)

| Capability | Scenario | Covering Test | Result |
|---|---|---|---|
| lint | `relations:` target absent → flagged | `test_check_dangling_targets_relations_target_absent_is_flagged` | PASS |
| lint | body link to absent id → flagged | `test_check_dangling_targets_body_link_to_absent_id_is_flagged` | PASS |
| lint | existing reference not flagged | `test_check_dangling_targets_existing_concept_is_not_flagged` | PASS |
| lint | self-link never flagged | `test_check_dangling_targets_self_link_is_never_flagged` | PASS |
| lint | external/anchor links ignored | `test_check_dangling_targets_ignores_external_and_anchor_links` | PASS |
| lint | dedupe same missing target per doc | `test_check_dangling_targets_dedupes_the_same_missing_target_per_doc` | PASS |
| lint | purge leaves detectably-dangling ref (post-purge) | No literal purge→lint integration test; covered by state-equivalent fixture tests + manual smoke (apply-progress) only | **WARNING (gap)** |
| lint | findings don't change exit contract | `test_lint_renders_empty_dangling_references_section`, `test_lint_flags_a_dangling_relations_target` (both assert exit 0) | PASS |
| status | dangling ref surfaced under needs attention | `test_status_surfaces_dangling_reference` | PASS |
| status | no dangling refs → no new entries | `test_status_no_dangling_references_no_new_entries` | PASS |
| status | missing vectors.db surfaced | `test_status_surfaces_missing_vectors_db` | PASS |
| status | present vectors.db → no entry | `test_status_present_vectors_db_produces_no_entry` | PASS |
| privacy-purge | successful purge warns dense retrieval degraded | `test_purge_success_output_warns_dense_retrieval_degraded` | PASS |
| privacy-purge | no interactive prompt / auto-reindex | `test_purge_does_not_prompt_or_auto_reindex` | PASS |
| privacy-purge | clean purge → exactly no commit, no WARNING | `test_purge_clean_cleanup_creates_no_commit_and_no_warning` | PASS |
| privacy-purge | non-no-op purge → exactly one commit | `test_purge_non_no_op_cleanup_creates_exactly_one_commit` | PASS |
| privacy-purge | commit failure non-fatal | `test_purge_autocommit_failure_is_non_fatal` | PASS |
| privacy-purge | empty diff after filter-repo's own rewrite still succeeds | `test_purge_clean_cleanup_creates_no_commit_and_no_warning` | PASS |
| doctor-command | present workspace vectors.db passes | `test_doctor_workspace_vectors_present_shows_pass` | PASS |
| doctor-command | absent workspace vectors.db fails + reindex remediation | `test_doctor_workspace_vectors_absent_shows_fail_with_reindex_remediation` | PASS |
| doctor-command | check skipped outside a workspace | `test_doctor_workspace_vectors_check_skipped_outside_workspace` | PASS |
| doctor-command | distinct from check 7 (`:memory:` probe) | `test_doctor_workspace_vectors_check_distinct_from_extension_loadable_check` | PASS |

All scenarios have a passing covering test except the single flagged gap above (WARNING, not CRITICAL — the underlying mechanism is fully unit/integration-tested with state-equivalent fixtures; only the literal "run purge, then run lint" end-to-end wiring is exercised solely by an unrepeatable manual smoke test, not CI).

## 4. Key Scrutiny

### 4a. #141 dangling-reference detection
- Confirmed: `relations:` target absent AND body-link (`normalize_link`) to absent id both flagged (`check_dangling_targets`, `src/openkos/lint.py:476-524`).
- Confirmed: valid references not flagged, external/anchor links ignored (`normalize_link` returns `None`, skipped).
- Confirmed: corrupt `relations:` → `okf.decode_relations` raises `ValueError`, caught in `collect_docs`, emits `"{id}.md: skipped (invalid relations)"`, doc excluded, no crash — `test_collect_docs_skips_doc_with_corrupt_relations_key` passes, asserts `docs == []` and the skip notice, no exception propagates.
- Confirmed: finding surfaces in BOTH `lint` output and `status`'s "Needs attention" (`main.py` diff — `lint` renders "Dangling references:" section; `status` folds `check_dangling_targets` output into needs-attention).
- Purge scenario: mechanism proven via fixture tests + one manual smoke run (see gap above, WARNING).

### 4b. #142 vectors.db awareness
- Confirmed: purge success output unconditionally echoes "dense retrieval degraded... run `openkos reindex`" (`main.py` purge, post-cleanup).
- Confirmed: `status` reports absent `layout.vectors_db_path` under needs attention.
- Confirmed: `doctor` adds workspace vectors.db-presence check (6b) as workspace-only/SKIP-outside, distinct from check 7. Diff confirms check 7's own code block (`probe_vec_loadable()` against `:memory:`) is UNCHANGED — only its surrounding docstring/comment renumbering ("nine checks" → "ten checks") was touched.

### 4c. Purge auto-commit + empty-diff guard (highest risk)
- Confirmed: `paths_dirty(root, ["bundle/index.md", "bundle/log.md"])` gates the commit. Clean purge (self-scope no-op, the common case) → `paths_dirty` returns `False` → no commit, no WARNING (`test_purge_clean_cleanup_creates_no_commit_and_no_warning`). Non-no-op cleanup (forced via monkeypatched `remove_index_entry`, since real filter-repo scrub usually already makes cleanup a no-op) → exactly ONE commit (`test_purge_non_no_op_cleanup_creates_exactly_one_commit`).
- Confirmed NON-FATAL: `paths_dirty` raising `GitError` is caught and falls through to attempt `_autocommit` anyway (`should_commit = True` on except); `_autocommit`'s own try/except keeps the whole step non-fatal — purge exit code unchanged (`test_purge_falls_through_to_autocommit_when_dirty_probe_raises`, `test_purge_autocommit_failure_is_non_fatal`).
- Confirmed BYTE-UNCHANGED: `git show 42af547 -- src/openkos/vcs/git.py` shows only a new `paths_dirty` function (19 lines) inserted after `commit_paths`; no lines inside `commit_paths` itself are touched. `git show 42af547 -- src/openkos/cli/main.py` shows the purge call site adds a new block after `_purge_clean_live_log`/`_purge_rebuild_indexes`, and `_autocommit` (the shared helper, defined elsewhere in main.py) is not part of this diff at all — confirmed unchanged.
- Confirmed ordering: typed confirmation phrase check → Phase B point-of-no-return → `expunge_paths` → `_purge_clean_live_index`/`_purge_clean_live_log` → `paths_dirty`-gated `_autocommit`, verified by line-number trace in `src/openkos/cli/main.py` (purge function body).

## 5. Guards

- **No `openspec/changes/` test coupling**: `grep -rn "openspec/changes" tests/unit/{test_lint.py,cli/test_lint.py,cli/test_status.py,cli/test_purge.py,vcs/test_git_adapter.py,cli/test_doctor.py}` — zero matches. Guard held (Slice 1's archive break avoided).
- **Layering**: `src/openkos/lint.py` imports only `config`, `model.okf`/`model.types`. `src/openkos/vcs/git.py` has zero internal `openkos` imports. No reverse `from openkos.cli` imports found in either. Canonical layer clean.

## 6. Size / Delivery

Diff: **949 insertions(+), 44 deletions(-) = 993 changed lines**, 10 files (`git show 42af547 --stat`), matching the stated figures. Production: `main.py` +98, `lint.py` +83, `vcs/git.py` +19 (≈200 production lines); tests ≈751 lines; `tasks.md` +42/-a few.

Judgment vs. the 800-line budget and design's suggested split: **993 total changed lines exceeds the 800-line budget cited for this verification**, and design.md explicitly recommends chaining two autonomous PRs (#141 dangling-ref core, self-contained no-git-changes; #142+auto-commit, self-contained VCS/UX slice) specifically because "combined authored diff risks the 400-line review budget but is likely under 800." The actual delivery came in as a single PR/commit at 993 lines — over the design's own upper estimate and over the 800-line reference budget, despite `tasks.md`'s forecast recording "Chained PRs recommended: No" / "Suggested split: Single PR" / "800-line budget risk: Medium" with an estimate of only ~650-700 lines. The forecast under-shot the actual delivery by roughly 300 lines and the single-PR decision was made without revisiting that call once the real diff size was known, and without an explicit recorded `size:exception`.

This is a process/delivery-guard finding, not a functional defect — the code itself is correct and well-tested — but it is a genuine deviation from the design's explicit recommendation, worth flagging as WARNING for reviewer-cognitive-load reasons (993 lines in one PR is a heavy single review, and the design pre-identified two natural, self-contained fault lines: #141 dangling-ref detection is fully independent of #142+auto-commit).

## Issues

**CRITICAL**: None.

**WARNING**:
1. No literal `purge` → `lint`/`status` end-to-end automated test for the "purge leaves referring doc detectably dangling" spec scenario; covered only by state-equivalent unit fixtures plus a one-off manual smoke test (not re-run in CI). Recommend adding one integration test that runs `purge --force` on a referenced concept and asserts `lint`/`status` output, before archiving, or explicitly accepting the gap.
2. Single-PR delivery (993 changed lines) exceeds the 800-line budget and design.md's own two-PR recommendation; `tasks.md`'s forecast (~650-700 lines, Medium risk) undershot the actual diff by ~300 lines and the single-PR call was not revisited. No recorded `size:exception`.

**SUGGESTION**:
1. Consider recording an explicit `size:exception` rationale in tasks.md/apply-progress when a design-recommended PR split is overridden, to make the review-workload-guard decision auditable.

## Task/Code Alignment

21/21 tasks in `openspec/changes/purge-transactional-cleanup/tasks.md` marked `[x]`. 0 unchecked. `apply-progress` reports match code state (files changed, TDD cycle evidence, verification numbers 1962 passed / 97.79% coverage all independently reproduced above).
