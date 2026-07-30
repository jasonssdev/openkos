# Verification Report — `next-action-pointer`

**Change**: `next-action-pointer` | **Branch**: `feat/next-action-pointer` (`21fb0a4`, `41c823b`, `861fe9a`, `46cbd95` on `f883f54`) | **Mode**: hybrid | **Verdict (final, after remediation)**: **PASS**

This report has two passes. Pass 1 (original) found 5 CRITICAL untested spec scenarios and returned FAIL. Pass 2 (this update) re-verifies the remediation commit `46cbd95` that was written in response. Both passes are kept below — the original findings are not discarded.

---

## Pass 2 — Re-verification of remediation commit `46cbd95`

### Command evidence (executed this session)

| Command | Result |
|---|---|
| `git show --stat 46cbd95` | 2 files: `tests/unit/cli/test_next.py` (+156), `openspec/changes/next-action-pointer/tasks.md` (+28/-16) — confirmed `src/openkos/cli/next_action.py` and `src/openkos/cli/main.py` are NOT in this commit's diff |
| `uv run pytest -q` | **2704 passed** in 90.02s (was 2699; +5 matches the 5 new tests, +23→28 in `test_next.py`) |
| `git diff main...HEAD --stat` (whole branch) | 8 files, 1950 insertions(+), 0 deletions — consistent with orchestrator's reported numbers |

### The five new tests — read as they actually landed, checked against `spec.md` directly (not the orchestrator's summary)

**1. `test_tier2_only_path_triggers_exactly_one_bundle_walk`**
Fixture: vector index present, one unextracted source, nothing else. Spies on `openkos.cli.next_action.lint_check.collect_docs` and `.find_exact_title_groups` via the shared `_spy_walks` helper, invokes `runner.invoke(app, ["next"])`. Asserts `"openkos ingest raw/notes.txt"` in stdout, `docs_calls["n"] == 1`, `groups_calls["n"] == 0`.
- **Covers the claimed scenario?** Yes — matches spec's "Stopping at tier 2 performs exactly one bundle walk" GIVEN (vector present, failed-extraction Source) and THEN (recommends ingest, one walk) exactly.
- **Real path?** Yes — goes through `runner.invoke`/`next_action()`, not `_BundleSignals` directly.
- **Non-vacuous — verified by literal mutation, not by trusting the report.** I removed the first-hit short-circuit in `next_action()` (changed the loop to keep evaluating every tier and take the first non-`None` result instead of returning immediately) and reran this test: **it failed** (`groups_calls["n"]` went from 0 to 1, since evaluation no longer stopped at tier 2). Reverted; `git diff` on `next_action.py` confirmed empty afterward. Confirmed non-vacuous by direct experiment.

**2. `test_tier3_only_path_shares_tier2s_single_bundle_walk`**
Fixture: vector index present, a below-source-sensitivity bundle, **no** unextracted source (so tier 2 evaluates `.docs`, declines, and tier 3 evaluates `.docs` again in the same run — the exact "sharing" case). Same `_spy_walks` pattern, real CLI invocation. Asserts `"openkos backfill-sensitivity"` in stdout, `docs_calls["n"] == 1`, `groups_calls["n"] == 0`.
- **Covers the claimed scenario?** Yes, and this is the one my Pass-1 report specifically flagged as needing to go through the real path rather than `_BundleSignals` directly. Confirmed: this test calls `runner.invoke`, never touches `_BundleSignals` directly, and the fixture genuinely makes both tier 2 and tier 3 read `.docs` in one run (tier 2 declines because no unextracted source exists, tier 3 matches) — this is a materially different, stronger test than the pre-remediation `test_docs_property_calls_collect_docs_exactly_once_when_read_twice`, not a rename of it (both now coexist in the file).
- **Non-vacuous — verified by literal mutation.** I removed the memoization in `_BundleSignals.docs` (made the property call `collect_docs` on every access instead of caching). Reran this test: **it failed** (`docs_calls["n"]` went from 1 to 2 — `assert 2 == 1`). Reverted and confirmed `git diff` empty. Confirmed non-vacuous by direct experiment.

**3. `test_tier4_path_performs_at_most_three_bundle_walks`**
Fixture: vector index present, a genuine exact-title duplicate pair (`Stoicism`/`STOICISM`), nothing else. Uses `_spy_walks(monkeypatch, real_groups=True)` so `find_exact_title_groups` actually runs (needed for tier 4 to genuinely fire with real duplicate data, not a stub). Asserts `"openkos duplicates"` in stdout and `docs_calls["n"] + (2 * groups_calls["n"]) <= 3`.
- **Is the `2×groups_calls` weighting justified, or an invented looser bound?** Checked against `next_action.py`'s own `_BundleSignals.exact_title_groups` docstring (line 87-90, unchanged by this commit): *"2 further walks (`find_exact_title_groups`'s own `_iter_eligible` plus `lifecycle.deprecated_concept_ids`)"* — i.e. the module's own design comment states one **call** to `find_exact_title_groups` costs **two** bundle walks. This matches `design.md`'s cost table (`Stops at 4/none: collect_docs=1, find_exact_title_groups=1, Walks=3`, i.e. 1 + 2×1 = 3). The formula's weighting is not invented; it is lifted directly from the shipped design/code accounting.
- **Is the bound the spec's bound, not a looser one?** Spec: "Reaching tier 4 ... MUST perform at most three bundle walks in total." Test asserts `<= 3`. Exact match, not loosened.
- **Non-vacuous?** In the unmutated run, `docs_calls=1, groups_calls=1` → `1+2=3`, exactly at the boundary (a tight, not slack, proof). Under my memoization-removal mutation above, this test **also failed** (`docs_calls` became 2, `2+2=4 > 3`) — confirmed sensitive to at least one of its two guards by direct experiment, not merely by re-deriving the arithmetic on paper.

**4. `test_duplicate_group_check_does_not_run_when_tier_2_fires`**
Fixture: vector present, unextracted source, plus a duplicate pair. Spies only on `find_exact_title_groups`. Asserts tier 2 fired (`"openkos ingest raw/notes.txt"` in stdout) and `calls["n"] == 0`.
- Matches spec's "Duplicate-group check does not run when tier 2 fires" GIVEN/THEN exactly, real CLI path.
- **Non-vacuous — verified by literal mutation** (same short-circuit-removal experiment as test 1): **failed** (`calls["n"]` went from 0 to 1). Confirmed by direct experiment.

**5. `test_duplicate_group_check_does_not_run_when_tier_3_fires`**
Fixture: vector present, below-source-sensitivity bundle, plus a duplicate pair. Spies only on `find_exact_title_groups`. Asserts tier 3 fired (`"openkos backfill-sensitivity"` in stdout) and `calls["n"] == 0`.
- Matches spec's "Duplicate-group check does not run when tier 3 fires" GIVEN/THEN exactly, real CLI path.
- **Non-vacuous — verified by literal mutation:** **failed** under the same short-circuit-removal experiment (`calls["n"]` went from 0 to 1). Confirmed by direct experiment.

**Summary of the mutation experiments** (both mutations applied and reverted this session, `next_action.py` confirmed byte-identical afterward via `git diff --stat`):
- Short-circuit removal → tests 1, 4, 5 failed as expected; test 3 unaffected (expected: tier 4 is the last tier, so removing short-circuit changes nothing for a fixture that already reaches tier 4); test 2 unaffected (expected: its guard is memoization, not short-circuit).
- Memoization removal → tests 2 and 3 failed as expected; test 1 unaffected (expected: its fixture only ever accesses `.docs` once regardless of memoization, since tier 2 fires and returns immediately).

Every test failed under exactly the mutation targeting its own claimed guard, and passed clean on the reverted file (`uv run pytest tests/unit/cli/test_next.py -q` → 28 passed). This is stronger evidence than reasoning through the source alone, and stronger than accepting the apply agent's self-report — I ran the breakage myself.

### `tasks.md`'s corrected Requirement Coverage Cross-Reference — checked against the test file, not restated

| Row | Claim | My check |
|---|---|---|
| First-Hit Short-Circuit Cost Contract | 4/4 | Correct: tier1 (pre-existing `test_tier1_only_path_triggers_zero_bundle_walks`), tier2 (new test 1), tier3 (new test 2), tier4 (new test 3) — all four now have a genuine, real-path covering test. |
| Duplicate-Group Check Gated on Higher Tiers | 4/4 | Correct: tier1 (pre-existing), tier2 (new test 4), tier3 (new test 5), "runs when 1-3 empty" (pre-existing) — all four covered. |
| Pinned Tier Order | "3/4 dedicated; tier1-outranks-tier2 only implied" | Correct and honest — this row is unchanged from my Pass-1 finding and the remediation did not touch it (it wasn't in Pass 1's CRITICAL list, only a WARNING). Still an accurate, non-overclaiming statement. |
| All other rows | unchanged, matches Pass 1 | Verified unchanged and still accurate. |

The correction is accurate. Unlike the original table (which claimed 4/4 for both flagged requirements while only 1/4 and 2/4 existed), this corrected version's claims match what is actually in the test file, verified by literal mutation testing above, not merely by counting test names.

### Final scenario tally

**24 of 24 scenarios now have a named, passing, real-path test — confirmed non-vacuous by direct mutation experiment for the 5 that were previously missing.**

One item remains a WARNING, not a gap: "Tier 1 outranks tier 2" (Pinned Tier Order) still has no *dedicated, minimal* test — it is covered only as a strict subset of `test_all_four_tiers_present_tier_1_wins`'s stronger fixture. This was never one of the 5 CRITICAL findings and the remediation did not target it; it remains a legitimate, low-severity WARNING carried forward from Pass 1.

---

## Pass 1 — Original findings (2026-07-30, before remediation) — kept for history

*(Condensed; full original text is preserved in Engram observation history under this topic key.)*

- `uv run pytest -q`: 2699 passed. `uv run pytest --cov`: TOTAL 97.52% (gate 90%), `next_action.py` 94%. `ruff check`/`ruff format --check`/`mypy --strict`: all clean.
- `status` confirmed byte-identical (D2): `test_status.py` diff empty, `main.py` diff was pure insertion.
- No scope creep found: no `--json`, no ADR, no new dependency, no SDD/tooling terms in changed files.
- Both named traps (`multi-source-uncovered` negation, `check_unextracted` bare fallback) verified non-vacuous by reasoning through guard removal.
- No Model Backend Constructed: verified structurally — `next_action.py` imports nothing OllamaClient-related.
- D5 (no count of unseen findings) and the honesty guard: both solid.
- **5 of 24 spec scenarios had zero covering test — CRITICAL:**
  1. Cost Contract: "Stopping at tier 2 performs exactly one bundle walk" — no test.
  2. Cost Contract: "Stopping at tier 3 ... sharing tier 2's walk" — no test; nearest test (`test_docs_property_calls_collect_docs_exactly_once_when_read_twice`) exercised `_BundleSignals` directly, not the real path.
  3. Cost Contract: "Reaching tier 4 performs at most three bundle walks" — no test bounding the combined total.
  4. Duplicate-Group Gated: "does not run when tier 2 fires" — no test.
  5. Duplicate-Group Gated: "does not run when tier 3 fires" — no test.
- `tasks.md` (pre-remediation) claimed 4/4 coverage for both flagged requirements; the claim did not match the test file (documentation overclaim).
- 1 WARNING: "Tier 1 outranks tier 2" scenario only implied by a superset fixture, no dedicated test.
- **Original verdict: FAIL.**

---

## Verdict

**PASS.** All 24 spec scenarios now have a named, passing test through the real CLI/`next_action()` path. The 5 previously-CRITICAL gaps were closed by tests-only remediation (`next_action.py`/`main.py` untouched, confirmed via `git show --stat`), and I independently verified all 5 new tests are non-vacuous by literally breaking their target guards in `next_action.py`, observing the expected failures, and reverting — not by accepting the remediation commit's self-report. `tasks.md`'s corrected Requirement Coverage Cross-Reference now matches the test file on disk. One WARNING remains (Pinned Tier Order's "tier1 outranks tier2" scenario lacks a dedicated/minimal test, covered only implicitly) — not blocking. Full suite: 2704/2704 passing.
