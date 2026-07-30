# Tasks: `openkos next` — deterministic pointer to the one action worth taking

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~450-650 (module ~200, `main.py` ~40, `test_next.py` ~280, `docs/cli.md` ~25) |
| 800-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR, four work-unit commits (see below) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (not needed at this estimate) |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
800-line budget risk: Low

The design's own forecast (~450-650 lines) sits comfortably under the 800-line budget raised for
this session. No chaining is proposed. If actual implementation trends toward the top of that
range plus unplanned rework, re-forecast before the docs commit and flag to the user rather than
silently exceeding 800.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `_BundleSignals` cost-contract foundation (memoized walks) | PR 1 (commit 1) | `uv run pytest tests/unit/cli/test_next.py -k cost` | N/A — pure unit test, no CLI invocation yet | Delete `_BundleSignals` and its tests; nothing else depends on it yet |
| 2 | Tier engine: `NextAction`, `_TIERS`, tiers 1-4, both trap guards | PR 1 (commit 2) | `uv run pytest tests/unit/cli/test_next.py -k "tier or trap"` | N/A — tier functions tested directly, no CLI wiring yet | Delete tier functions and `_TIERS`; Unit 1 stands alone |
| 3 | CLI wiring: `next` command, workspace gate, honesty output, no-backend guard | PR 1 (commit 3) | `uv run pytest tests/unit/cli/test_next.py` | `openkos next` run against a seeded temp workspace (via `CliRunner`, already in harness) | Remove the `next` command registration from `cli/main.py`; module stays importable and unused |
| 4 | Docs + full quality gate | PR 1 (commit 4) | `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy .` | `openkos next --help` renders | Revert `docs/cli.md` entry only |

## Phase 1: Cost-Contract Foundation (`_BundleSignals`) — RED then GREEN

- [x] 1.1 RED: write `tests/unit/cli/test_next.py` scaffolding (`_init_workspace` copied from
      `test_status.py:37`, `_OfflineOllama` reuse) with monkeypatched counting wrappers on
      `openkos.cli.next_action.lint_check.collect_docs` and
      `openkos.cli.next_action.find_exact_title_groups`, asserting call counts per the design's
      walk table before any tier exists (spec: First-Hit Short-Circuit Cost Contract). Originally
      1/4 scenarios genuinely covered; 3 more added in remediation (5.1) — see cross-reference.
- [x] 1.2 GREEN: create `src/openkos/cli/next_action.py` with `_BundleSignals` (memoized
      properties `vector_store_empty`, `docs`, `exact_title_groups`) satisfying: tier-1-only path
      triggers 0 walks; `docs` accessed twice in one run still calls `collect_docs` once.

## Phase 2: Tier Engine and Order — RED then GREEN

- [x] 2.1 RED: seed one bundle carrying all four tier findings at once; assert tier 1 wins and
      tiers 2-4 are not mentioned (spec: Pinned Tier Order, "All four tiers present, tier 1
      wins"). Peel tier 1 off, assert tier 2 wins ("Tier 2 outranks tier 3" precondition covered
      here); peel tier 2, assert tier 3; peel tier 3, assert tier 4 (4 tests total, matching all 4
      Pinned Tier Order scenarios).
- [x] 2.2 RED: assert tier 1's command is exactly `openkos reindex` and tier 4's is exactly
      `openkos duplicates` regardless of finding detail (spec: Per-Tier Command, scenarios 3-4).
- [x] 2.3 RED: assert tier 2's printed command equals the unextracted-source finding's own retry
      command, and tier 3's equals `openkos backfill-sensitivity` from the finding's own detail
      (spec: Per-Tier Command, scenarios 1-2).
- [x] 2.4 RED — trap 1: seed a bundle with only a `multi-source-uncovered` finding (no
      `below-source-sensitivity` finding); assert tier 3 does NOT fire and does NOT surface the
      negated `openkos backfill-sensitivity` command that `multi-source-uncovered`'s detail
      string contains (`lint.py:766-768`). Covers the design's first named trap.
- [x] 2.5 RED — trap 2: seed a bundle where every unextracted finding has an empty `resource`
      (the `check_unextracted` fallback, `lint.py:632`); assert tier 2 does NOT fire (declines
      rather than printing the bare, non-runnable `openkos ingest`) and evaluation continues to
      tier 3/4. Covers the design's second named trap.
- [x] 2.6 RED: assert the duplicate-group check (`find_exact_title_groups`) does not run when
      tier 1 fires, and runs only when tiers 1-3 are all empty (spec: Duplicate-Group Check
      Gated on Higher Tiers). Originally 2/4 scenarios genuinely covered; "does not run when
      tier 2 fires" and "does not run when tier 3 fires" added in remediation (5.1).
- [x] 2.7 GREEN: add `NextAction` dataclass, `_command_from_detail()`, `_TIERS` tuple (D1 order:
      reindex, ingest, backfill-sensitivity, duplicates), and `next_action()` implementing
      first-hit short-circuit, the `kind`-before-extraction filter (trap 1), and the
      argument-carrying-command-only acceptance rule (trap 2), making 2.1-2.6 pass.

## Phase 3: CLI Wiring, Honesty Output, No-Backend Guard — RED then GREEN

- [x] 3.1 RED: assert `openkos next` exits non-zero with a clean stderr message and no traceback
      outside a workspace, and exits 0 on every in-workspace state (spec: Workspace Presence
      Check, both scenarios).
- [x] 3.2 RED: assert no `--json` flag is accepted and no file under the workspace changes across
      an empty, healthy, and all-tiers-firing run, using the `_snapshot`/`_snapshot_entry` pattern
      from `test_status.py:26-34` (spec: Read-Only and Human-Readable Only).
- [x] 3.3 RED: assert `openkos.cli.main.OllamaClient` is never constructed (sentinel raising on
      `__init__`) on every workspace state, including the no-action path (spec: No Model Backend
      Constructed).
- [x] 3.4 RED: assert the no-runnable-action output names `openkos status` and never claims the
      bundle is clean, both on a truly empty bundle and on a bundle with only commandless findings
      (conformance, dangling, multi-source-uncovered) present (spec: No-Runnable-Action Output
      Never Claims Cleanliness, both scenarios).
- [x] 3.5 RED: assert no numeral describing unseen/skipped findings appears in output, both when a
      tier fires and when tier 4 has already paid every walk (spec: No Count of Unseen Findings,
      both scenarios).
- [x] 3.6 GREEN: add `render_lines()` (appends the `openkos status` pointer on every path) and wire
      the `next` command into `src/openkos/cli/main.py` — workspace gate, one `next_action()` call,
      echo loop — making 3.1-3.5 pass. Confirm `test_status.py` still passes unmodified (D2).

## Phase 4: Docs and Quality Gate

- [x] 4.1 Add the `openkos next` entry to `docs/cli.md` (command, one-line purpose, tier order
      summary, non-goals note per the spec's Purpose/Non-Goals).
- [x] 4.2 Run the full gate: `uv run pytest`, `uv run ruff check . && uv run ruff format --check
      .`, `uv run mypy .`; confirm 90% branch coverage and `mypy --strict` pass with zero new
      exemptions.

## Requirement Coverage Cross-Reference

Verified test-by-test against the actual test file, not restated from task descriptions. Two
rows below previously overclaimed 4/4 while only 1/4 and 2/4 scenarios had a real covering
test (`verify-report.md`); remediation task 5.1 closed the gap.

| Spec requirement | Tasks | Scenarios covered |
|---|---|---|
| Workspace Presence Check | 3.1 | 2/2 |
| Read-Only and Human-Readable Only | 3.2 | 1/1 |
| Pinned Tier Order | 2.1 | 3/4 dedicated; "tier 1 outranks tier 2" only implied by a superset fixture |
| First-Hit Short-Circuit Cost Contract | 1.1, 1.2, 5.1 | 4/4 |
| Per-Tier Command Reflects the Finding's Own Command | 2.2, 2.3 | 4/4 |
| No-Runnable-Action Output Never Claims Cleanliness | 3.4 | 2/2 |
| No Count of Unseen Findings | 3.5 | 2/2 |
| No Model Backend Constructed | 3.3 | 1/1 |
| Duplicate-Group Check Gated on Higher Tiers | 2.6, 5.1 | 4/4 |
| Design traps (not separate spec requirements, D5/lint.py) | 2.4, 2.5 | both traps verified non-vacuous |

## Phase 5: Remediation (post-verification gap closure)

- [x] 5.1 Added 5 tests to `tests/unit/cli/test_next.py` closing the scenarios `verify-report.md`
      flagged uncovered. Each guard confirmed non-vacuous by temporarily breaking it in
      `next_action.py` (removing `docs` memoization; removing the tier loop's first-hit `return`),
      observing the correct failure, then reverting (`git diff` on `next_action.py` is empty).
