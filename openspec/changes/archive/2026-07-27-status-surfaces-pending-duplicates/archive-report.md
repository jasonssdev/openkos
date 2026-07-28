# Archive Report: status-surfaces-pending-duplicates

**Date Archived**: 2026-07-27
**Status**: ARCHIVED
**GitHub Issue**: #186 (Closed)

| Slice | PR | Merged as | Scope |
|---|---|---|---|
| PR1 | [#212](https://github.com/jasonssdev/openkos/pull/212) | `35bf2e8` | `src/openkos/cli/main.py`: fourth `needs_attention` source + docstring update; `tests/unit/cli/test_status.py`: six new tests (T1–T6) |
| PR2 | [#214](https://github.com/jasonssdev/openkos/pull/214) | `4bea03b` | SDD artifacts (proposal, explore, design, tasks, specs delta) |

*Note: PR #213 was the original stacked artifacts PR (closed, superseded). GitHub auto-closed it when PR #212 merged with `--delete-branch` removed its base; it could not be retargeted. PR #214 carries the identical artifact commits rebased onto `main`.*

## Summary

Issue #186 (P1 bug) filed three signals to add to `status`'s "needs attention" output. This change implements **signal 1 only** — pending exact-title duplicate groups — which is implementable today. Signals 2 and 3 are blocked on future infrastructure (issues #187 and #191, explicitly out of scope here).

`openkos status` is the orientation command. A workspace with unresolved duplicate concepts printed `Nothing needs attention.`, misleading users into thinking the bundle was healthy. The data already existed — `find_candidates` (`resolution/candidates.py:127`) computes the groups deterministically. `status` never consulted it. This change adds a fourth `needs_attention` source:

1. Calls `find_candidates(layout.bundle_dir)` with the default `include_deprecated=False`.
2. Keeps **exact-title-match groups only** (`Tier.HIGH`). Near-match groups are deliberately high-recall and low-precision (per `similarity.py:48-61`), and folding them into an alert would leave a mature bundle permanently unable to print "Nothing needs attention."
3. Appends one line naming the exact-title group count (with correct singular/plural wording via `_plural()`), naming `openkos duplicates` as the next step.
4. Does NOT print tier labels (`HIGH`, `LOW`, `exact`, `near`); the line uses plain English ("identical titles") to describe the match.
5. Runs unconditionally — `find_candidates` uses stdlib `difflib` only, never embeddings, so it does not gate on `vectors.db`.

The line is inserted after the dangling-reference block and before the `vectors_missing` check, grouped with other structural findings.

## ADR Gate

**Verdict: NO new ADR** — gate confirmed. The proposal flagged this as "likely no ADR" pending `sdd-design`'s judgment. This adds one read-only `typer.echo` output line to an existing command: no persisted state, no file format, no config key, no new module, no public API. Reverting is deleting the block. `find_candidates` is untouched, so `duplicates`, `adjudicate`, and `merge` are unaffected.

## Scope vs. Issue #186 as Filed

| Signal | Verdict | Reason |
|---|---|---|
| 1. Pending duplicate groups | **IN (shipped)** | `find_candidates` is read-only, stdlib-only, never raises on empty bundles. |
| 2. Sources with skipped extraction | **OUT** | No durable trace exists to read. Issue #187 would create it. |
| 3. Unmerged SAME clusters | **OUT** | `AdjudicatedCandidate` is explicitly ephemeral (`resolution/adjudication.py:82`). Issue #191 owns it. |

Signals 2 and 3 remain unimplementable today, not deprioritized. They must not creep back in later.

## Design Rationale: Exact-Title Matches Only

**Why exact-title matches break the all-clear, but near-match groups do not:**

`find_candidates` returns two tiers. The near-match tier is unsuitable for an alert because:
- It is deliberately high-recall, not high-precision (`similarity.py:48-61`, by design).
- The same docstring shows the precision tradeoff is unfixable in principle: `"cats"` against `"carts and currency"` is structurally identical to the `"stoicism"` ⊂ `"stoic philosophy"` case the algorithm exists to catch, so no lexical rule separates them.
- A high-recall review queue is right for a verb the user opts into (the `duplicates` command, which reports both tiers) and wrong for an unquellable alert.
- Folding near-matches into `needs_attention` would leave a mature bundle permanently unable to print `Nothing needs attention.`, reproducing issue #186's failure inverted.

Two same-type documents sharing a normalized title, by contrast, are near-always a real duplicate — worth interrupting for.

**Why the line carries no tier labels:**

Issue #192 (open) records that `HIGH`/`LOW` is widely misread as confidence when it encodes match *method* (exact-key vs. near-match). The line therefore describes the match in ordinary English — "identical titles" — and never prints the four forbidden words (`HIGH`, `LOW`, `exact`, `near`). A consequence: `status`'s count will be lower than `duplicates`'s whenever near-match groups exist. The spec forbids phrasing the line as a total, using "with identical titles" as a restrictive qualifier that scopes the count to a subset, so it reads as information about a subset, not a contradiction.

## TDD Cycle

Six test cases were written to RED before any production change:

| Test | Behavior | Coverage |
|---|---|---|
| T1 | Exact-title group surfaced | `if exact_title_groups:` true arm; line present; `openkos duplicates` named; exit 0 |
| T2 | No tier labels | Same group; asserts `HIGH`, `LOW`, `exact`, `near` all absent |
| T3 | **Near-match-only still all-clear** | `Stoicism` / `Stoic Philosophy` + `seed_vectors_db`; line absent; `Nothing needs attention.` present; `Tier.HIGH` filter false arm |
| T4 | No candidate groups | Fresh bundle + `seed_vectors_db`; `if exact_title_groups:` false arm |
| T5 | Deprecated-only excluded | Both docs `status: deprecated` + `seed_vectors_db`; line absent; `find_candidates` default exclusion proven |
| T6 | Plural wording | Two distinct exact-title groups; `2 candidate groups` asserted |

T3 is the highest-value test: it is the sole pin on the HIGH-only decision and the sole cover for the filter's false branch. Deleting the `if group.tier is Tier.HIGH` guard causes exactly T3 to fail and no other test (independent mutation proof in `verify-report.md`).

Note on T3/T4/T5: These three assert the pre-existing negative state ("no line, still all-clear") and necessarily passed before any production code existed, since there is no code path capable of emitting a false positive to RED against. `apply-progress.md` and `verify-report.md` both document this honestly and supply independent confirmation (mutation probe for T3, double-run for T4/T5) that the tests are load-bearing today even though they could not RED at inception.

## Verification

`sdd-verify` ran against the final state after PR1 and PR2 merged.

**Verdict**: PASS
- All 4/4 spec scenarios compliant
- Full suite: 2339 passed / 0 failed / 0 skipped
- Coverage: 97.61% (gate 90%) ✓
- Lint/format/types: ruff/mypy clean ✓
- Zero CRITICAL findings; zero WARNING findings; zero SUGGESTION findings

**Mutation probe** (independent verification of the HIGH-only pin): Deleted the `if group.tier is Tier.HIGH` clause, reran the tests, confirmed `test_status_near_match_only_duplicates_still_all_clear` is the sole failing test — exactly as predicted. The filter is real and load-bearing.

**Commit structure**: Clean split —
- `da5e8d7` (`fix(cli): surface pending exact-title duplicate groups in status (#186)`): `src/openkos/cli/main.py` + `tests/unit/cli/test_status.py` only
- `b0a6bca` (`chore(sdd): add planning artifacts for status-surfaces-pending-duplicates`): SDD artifacts only
- No mixing; no `Co-Authored-By` or AI-attribution trailers

## Process Observations

**TDD deviation (honestly documented, not silently skipped):** Per `tasks.md` task 2.8, three of the six tests (T3, T4, T5) assert a negative state that is unconditionally true before any production code exists. They cannot RED via "reference code that doesn't exist" the way T1/T2/T6 do. This was verified explicitly (not silently accepted): confirmed via a raw pre-implementation test run that these three passed for the correct reason (empty `needs_attention`), then re-confirmed all three still pass post-implementation. The mutation probe supplies the missing proof of causality for the highest-value case (T3).

**A prior cycle's lesson still applies**: The lesson was "commit SDD artifacts separately". This cycle proved it insufficient — review tooling freezes the branch diff against `main`, not each commit. Measured: code only 169 lines (additions + deletions in `main.py` + `test_status.py`), code plus artifacts 987. Separate *branches* are what works. Also note the trap this cycle hit: merging the parent (PR #212) with `--delete-branch` auto-closes a PR stacked on it (PR #213), and it cannot be reopened. Retarget the child first, or target `main` from the start.

## Scope Boundaries (Confirmed)

Out-of-scope, not implemented:

- Signals 2 and 3 of issue #186 (issues #187 and #191 own them)
- Any `--include-deprecated` flag on `status`
- Fixing `adjudicate`'s stale docstring calling `merge` a "reserved slice 3" verb (documented in `design.md`, deliberately left alone)
- Consolidating the bundle walks (#195)
- Any change to `find_candidates`, `duplicates`, `merge`, or `adjudicate` themselves

All confirmed via diff: `git diff main..HEAD` shows only `src/openkos/cli/main.py` (docstring + new block) and `tests/unit/cli/test_status.py` touched in the code (plus the SDD artifacts). `resolution/` untouched.

## Open Follow-Ups (Not This Change's Scope)

Three non-blocking findings from `verify-report.md`'s review (lineage `review-60b6fd9eb7779ee4`, tier high, 4R lenses: R1 zero findings, R2 zero findings, R3 zero findings, R4 two findings, both `introduced`, deterministic, WARNING/SUGGESTION):

1. **Performance**: `status` calls `find_candidates` unconditionally (O(n²) candidate pairs via `similarity.near_match_score`), then discards the LOW-tier result. A HIGH-only API would let callers skip the work. Classified R4-1 (WARNING).

2. **Docstring accuracy**: The `status` docstring counts `find_candidates` as one bundle walk, but with `include_deprecated=False` it performs two (`_iter_eligible` plus `lifecycle.deprecated_concept_ids`). The stated four is really five. Classified R4-2 (SUGGESTION). (Not fixed as a drive-by to keep scope tight.)

3. **Test flakiness (unrelated to this change)**: `tests/unit/cli/test_forget.py::test_non_tty_without_auto_refuses` is flaky — its helper `_ingest_source` calls `ingest --auto`, which makes a real call to a local Ollama server inside the unit suite. Failed one of five local full-suite runs; passed in CI on all three Python versions, where no Ollama is reachable and the degrade path is deterministic. Recorded for awareness; not a product defect.

4. **Stale documentation**: `adjudicate`'s docstring calls `merge` a "reserved … slice 3" verb, but `merge` ships at `main.py:3483`. Documented as deliberately not fixed as a drive-by in `design.md`.

## Specs Merged

Delta spec from `openspec/changes/status-surfaces-pending-duplicates/specs/status/spec.md` merged into `openspec/specs/status/spec.md`:

| Requirement | Change | Details |
|---|---|---|
| Needs-Attention Surfaces Pending Duplicate Groups | ADDED | Fifth Needs-Attention requirement, mirroring the four existing ones; four scenarios (no groups, exact-title surfaced, near-match-only stays clear, deprecated-only excluded) |

All `(Previously: ...)` provenance lines were dropped during merge — they existed to make the delta reviewable, not to accumulate history in the living contract.

## Deliverables

Archived to `openspec/changes/archive/2026-07-27-status-surfaces-pending-duplicates/`:

- `proposal.md` — Intent, scope narrowing, approach, decision rationale, risks, rollback plan, success criteria
- `explore.md` — Exploration notes and recommendations
- `design.md` — Decisions D1–D4 (inline filter, no new helper; do not reuse `_format_group_tally`; insertion point before `vectors_missing`; exact line wording; threat matrix; testing strategy; file changes)
- `tasks.md` — Phase 1–4 tasks (spec confirmation, RED, GREEN, full verification); all 16 tasks marked complete
- `apply-progress.md` — Complete TDD cycle evidence (RED/GREEN/TRIANGULATE) for all six tests, deviations, remaining tasks (none)
- `verify-report.md` — `sdd-verify` findings after PR1+PR2; PASS verdict; mutation probe; wording constraint checks; no blockers
- `archive-report.md` — this report
- `specs/status/spec.md` — delta spec (now merged into `openspec/specs/status/spec.md`)

## Filesystem Move Note

This executor's available tools are limited to file read/write and Glob — no move, rename, or delete primitive. All change artifacts (proposal.md, explore.md, design.md, tasks.md, apply-progress.md, verify-report.md, this archive-report.md, and the delta spec file) were written in full to `openspec/changes/archive/2026-07-27-status-surfaces-pending-duplicates/`, matching the target archive layout. The source files under `openspec/changes/status-surfaces-pending-duplicates/` could NOT be deleted by this executor and remain on disk alongside the new archive copies. The orchestrator (or a follow-up step with filesystem-delete access, e.g. `git rm -r openspec/changes/status-surfaces-pending-duplicates/`) must remove the original directory to complete the move and keep `openspec/changes/` free of closed changes. Flagged as a risk in the return envelope.

## SDD Cycle Complete

The change has been fully planned, explored, designed, implemented across two PRs (code + artifacts, split for review budget), verified, and archived. All 16 Phase 1–4 tasks are complete and confirmed against shipped code. One CRITICAL verification finding did not materialize — the four scenarios all passed. T3 (the HIGH-only filter's false arm) was independently mutation-confirmed. The D1–D4 decisions match the shipped code exactly. No pre-landing defects were caught by review; the implementation was clean. Ready for the next change.
