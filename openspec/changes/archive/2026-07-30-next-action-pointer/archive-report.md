# Archive Report: next-action-pointer (#265)

**Change**: next-action-pointer
**Archived to**: `openspec/changes/archive/2026-07-30-next-action-pointer/`
**Archive date**: 2026-07-30
**Branch**: feat/next-action-pointer, HEAD 46cbd95
**All artifacts included**: proposal.md, design.md, tasks.md, verify-report.md, specs/next-action-pointer/spec.md

## Artifact Observation IDs (for Engram traceability)

- Proposal: #2194
- Spec: #2196
- Design: #2197
- Tasks: #2198
- Verify-report: #2200

## Change Summary

The `next-action-pointer` capability delivers `openkos next`, a deterministic read-only verb that answers "which single command should I run next" by ranking actionable findings in a fixed pinned order. The feature closes GitHub #265.

The implementation:
1. **Tier engine** (`src/openkos/cli/next_action.py`) — owns an ordered tuple of four tier callables (reindex → ingest → backfill-sensitivity → duplicates) plus one lazily-memoized signal holder enforcing the cost contract by construction.
2. **CLI wiring** (`src/openkos/cli/main.py`) — adds the `next` command with workspace gate, one `next_action()` call, and output rendering. `status` body, output, order, and spec are untouched per D2.
3. **Test coverage** — 28 tests in `tests/unit/cli/test_next.py`, all passing with 2704 total suite tests at 97.52% coverage (gate 90% branch).
4. **Documentation** — `docs/cli.md` entry added.

## Verification Summary

**Verdict: PASS** (after remediation commit 46cbd95)

- **Pass 2 (remediation re-verification)**: Commit `46cbd95` added 5 tests to close coverage gaps the verification pass 1 had flagged (CRITICAL: cost-contract tier 2/3 shared walk, tier 4 max-3-walks, duplicate-gate tier 2/3 firing). All 5 tests confirmed non-vacuous via direct mutation of `next_action.py` (removing short-circuit, removing memoization) and revert.
- **Final tally**: 24 of 24 spec scenarios now have a named, passing, real-path test.
- **All 2704 suite tests pass**, all quality gates clean (ruff check, ruff format, mypy --strict).
- **Carry-over warning (non-blocking)**: "Tier 1 outranks tier 2" scenario has only an indirect/superset test, no dedicated minimal test. Genuine but low-risk per test-coverage analysis; unchanged from pass 1.

Per the launch prompt's final-state facts: the verification pass 1 found 5 CRITICAL uncovered scenarios; commit `46cbd95` closed all five with tested guards. No implementation defect was ever found in `src/openkos/cli/next_action.py`.

## Design Decisions Carried Forward

Five product decisions closed during proposal phase and affirmed by design:

- **D1 — Priority order**: Blocked work first (reindex) → incomplete work (ingest) → unsafe work (backfill-sensitivity) → ambiguous work (duplicates). Matches `next`'s tier 1-4 order.
- **D2 — No status refactor**: `next` stands alone as a new read-only module; `status` body, output, spec untouched. Justification: different questions; unifying specs would touch 8 shipped requirements for zero user-visible gain.
- **D3 — find_exact_title_groups gating**: Tier 4 evaluation gated on tiers 1-3 being empty. Deliberate divergence from `status` (which calls it unconditionally).
- **D4 — Commandless findings**: `next` ranks only kinds carrying a runnable command. Honesty guard (mandatory) names `openkos status` on the no-action path; never asserts the bundle is clean.
- **D5 — No count of unseen findings**: `next` never prints a count (pointer, no number). Constraint: counting requires every bundle walk, which the cost requirement forbids. Overrules issue #265's sample output.

## Spec Changes Applied

### New specification created

**next-action-pointer/spec.md** — NEW full spec (9 requirements, 24 scenarios)
- Workspace Presence Check (2 scenarios)
- Read-Only and Human-Readable Only (1 scenario)
- Pinned Tier Order (4 scenarios)
- First-Hit Short-Circuit Cost Contract (4 scenarios)
- Per-Tier Command Reflects the Finding's Own Command (4 scenarios)
- No-Runnable-Action Output Never Claims Cleanliness (2 scenarios)
- No Count of Unseen Findings (2 scenarios)
- No Model Backend Constructed (1 scenario)
- Duplicate-Group Check Gated on Higher Tiers (4 scenarios)

No modifications to existing specs: `status/spec.md` byte-identical (D2), no change to lint/spec.md, no change to any other spec.

## Implementation Summary

- **Total changed lines**: 1950 (insertions only, zero deletions)
- **Files modified**: 8 (all additions or minor wiring)
  - `src/openkos/cli/next_action.py` — NEW (221 lines)
  - `src/openkos/cli/main.py` — MODIFIED (~40 lines, workspace gate + call + echo)
  - `tests/unit/cli/test_next.py` — NEW (906 lines, 28 tests)
  - `docs/cli.md` — MODIFIED (~25 lines)
- **Test coverage**: 2704 passed / 97.52% branch (gate 90%)
- **Linting**: ruff check / ruff format / mypy --strict all clean
- **Build**: uv build produces sdist + wheel, exit 0
- **All 14 implementation tasks complete**: Phases 1-4 plus 5.1 (remediation) marked [x]

> **Correction (issue #280).** The two file sizes above originally read
> "200 lines" and "280 lines, 28 tests". Both were wrong as written; the
> figures now shown are measured at `46cbd95`, the HEAD this report itself
> declares. The test module was understated by more than 3x — 906 lines,
> not 280 — and the test COUNT (28) was the only part that was right.
>
> This matters beyond tidiness, because every size judgment downstream
> rests on it. The design forecast ~450-650 changed lines, the task list
> recorded "800-line budget risk: Low", and the "Risk" line below concludes
> the change fit the 800-line budget with no chaining needed. Against the
> real total that conclusion does not hold: the change should have been
> split, and the seam was named in its own design. The judgment is left
> standing as it was made, with this note beside it — an archived report is
> the record of what was believed at the time, and correcting the numbers
> while silently rewriting the conclusion would destroy the very lesson the
> numbers teach.

## Commits on feat/next-action-pointer

1. **21fb0a4** — Tier engine foundation + cost-contract tests (_BundleSignals, NextAction, _TIERS, tiers 1-4, both trap guards + test_next.py scaffolding)
2. **41c823b** — CLI wiring (workspace gate, next command, render_lines, no-backend guard, honesty output)
3. **861fe9a** — Docs + quality gate pass (docs/cli.md entry, uv pytest/ruff/mypy all green)
4. **46cbd95** — Remediation (5 new tests closing CRITICAL coverage gaps: tier 2 cost, tier 3 cost+shared, tier 4 max, dup-gate tier 2/3; tasks.md correction)

## Corrected Premise from Issue #265

The issue claims "four of the six finding kinds name no command at all". **This is FALSE.** Verified by reading `lint.py` directly:

- `missing-vector-index` → command `openkos reindex` (main.py:5333-5334)
- `unextracted` → command `openkos ingest <resource>` (lint.py:630, fallback :632)
- `below-source-sensitivity` → command `openkos backfill-sensitivity` (lint.py:729-730)
- `duplicate-groups` → command `openkos duplicates` (main.py:5323)
- `conformance` → NAMES NOTHING
- `dangling` → NAMES NOTHING
- `multi-source-uncovered` → NAMES A COMMAND ONLY TO RULE IT OUT (lint.py:766-768)

**Accurate count**: 4 of 7 kinds carry a runnable command (not 2 of 6); 2 name nothing; 1 names a command only in a negating sentence. Consequence: the real work was ranking the 4 actionable kinds + short-circuiting, not mapping new commands. `next` reads the command string the finding already carries.

Two traps in the design close this:
1. **Trap 1** (`test_trap1_multi_source_uncovered_never_surfaces_a_negated_command`): `multi-source-uncovered`'s detail string contains a negating backtick command; tier 3 filters on `kind == "below-source-sensitivity"` first, avoiding the temptation to extract the negated command.
2. **Trap 2** (`test_trap2_bare_ingest_fallback_never_fires_tier_2`): when `resource` is empty, the fallback is a bare `openkos ingest` with no argument; tier 2 accepts only commands carrying arguments, declining to fire and letting evaluation continue.

Both traps verified non-vacuous by mutation testing and test names in the suite.

> **Correction (issue #280).** The `missing-vector-index` citation above
> originally read `lint.py:5332-5333`. That text has never lived in
> `lint.py`, which is 773 lines long at this report's HEAD; it is in
> `main.py`, and at lines 5333-5334 rather than 5332-5333. The adjacent
> `duplicate-groups` row cited `main.py` correctly, which is what makes the
> slip legible as a copy rather than a misreading.
>
> The two trap test names were also invented rather than quoted — the
> report named `test_tier3_does_not_fire_on_multi_source_uncovered` and
> `test_tier2_does_not_fire_on_bare_ingest_fallback`; neither has ever
> existed. The real names are shown above. The claim they support is
> sound — both traps are genuinely covered — but a reader following the
> citation to check would have found nothing and had no way to tell
> whether the guard or the name was missing.

## Delivery

**Delivery strategy**: single PR, four work-unit commits (as listed above).
**Risk**: Low (fits 800-line budget, no chaining needed, single-slice revert).
**Changed lines**: 1950 (insertions only).
**Branch**: `feat/next-action-pointer` on `main` (f883f54).
**Status**: Clean diff vs main; 4 commits not yet pushed.

## SDD Cycle Complete

The change has been fully planned (proposal), specified (one new spec, 24 scenarios), designed (with design gates re-run and ADR gate closed for spec-level reversibility), implemented (4 commits, all tests passing), verified (pass 1 found CRITICAL gaps; pass 2 closed all 5 with remediation commit and re-verified), and archived.

Ready for the next change. PR delivery is the user's responsibility per the phase boundary.

---

**Archive summary**: All artifacts preserved in `/Users/jasonssdev/Dev/Projects/openkos/openspec/changes/archive/2026-07-30-next-action-pointer/` and persisted to Engram under topic key `sdd/next-action-pointer/archive-report` for traceability.
