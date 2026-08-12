# Archive Report: durable-pending-work

**Change**: durable-pending-work
**Issues**: #572, #556 — both CLOSED
**Status**: CLOSED
**Archived**: 2026-08-12
**PR**: #599 (merge commit `64640ce` on `main`)
**Mode**: hybrid (OpenSpec + Engram)

## Final State Authority

This report describes the change AT CLOSE. `apply-progress` and `verify-report` are historical snapshots; where they disagree with this document, this document is correct.

### Merged

PR #599 merged `tracker/durable-pending-work` into `main` as `64640ce`. Four review-sized slices merged into the tracker first:

| PR | Slice | Merge commit | Changed lines |
|---|---|---|---|
| #594 | A — findings store, decision primitives, curate persistence | `72251fd` | 1430 |
| #595 | B1 — purge/forget decision-subtree sweep | `916f1e1` | 571 |
| #596 | B2 — decline, reopen, the declined view | `7946ae6` | 685 |
| #597 | C — the `next` tier, the honesty guard, the pending-work spec | `cf66d5d` | 764 |

CI green on every one across the full matrix: ruff + mypy, tests on 3.12/3.13/3.14, the isolated wheel smoke test, and GitGuardian. Issues #572 and #556 closed via #599. All five branches deleted, local and remote.

### Review and delivery authority

Receipt-driven development was switched off by the maintainer during this work, so delivery is **`disabled/unmanaged`** — there is no review receipt and its absence is expected, not a gap. The reason it was switched off is recorded below under Process notes.

Verification verdict: PASS WITH WARNINGS. Two spec-vs-code gaps were found and both were fixed before archive rather than disclosed and shipped.

Final suite: **4335 passed, 1 skipped** (baseline 4287/1, +48 tests). Coverage 96.93% against a 90% branch gate.

## What shipped

The engine bought judgment at real cost and threw it away. `curate`'s Contradictions stage ran an LLM over candidate pairs — 64 calls / 3m59s for three contradictions in the measured run — and `ContradictionVerdict`'s own docstring said the outcome plainly: *"Ephemeral -- never a persisted OKF type or `bundle`/`state` file."*

Two different things were being lost, and separating them is the load-bearing insight:

| | Nature | Recomputable | Volume | Quotes concept text |
|---|---|---|---|---|
| Findings — contradiction verdicts | machine inference | yes, at cost | large | yes |
| Decisions — declined / reopened | human judgment | never | tiny | no |

Two stores with opposite requirements: findings in `.openkos/findings.db` under `vectors.db`'s delete-without-rebuild posture, decisions as canonical bundle state extending ADR-0013 by exactly one kind. ADR-0014 records both, and records why no two-phase write is needed — they are joined at read time by recomputing `decision_key` from a finding's own pair ids.

Behaviour now available:

- `curate` persists every verdict with the digest of each input it was computed from; changing one input marks only the findings that read it stale.
- `contradictions --decline` records the operator's judgment, `--reopen` reverses it, `--declined` lists what is hidden.
- `next` ranks an open contradiction, guarded on open ∧ not stale ∧ not declined.
- `curate` no longer re-shows a declined contradiction.
- `purge` expunges the decision subtree from the whole history in the same `git filter-repo` pass as the concept; `forget` sweeps the live tree.

## D6 — the property the slice order exists to protect

No commit on any branch may exist where a decision file can be written but `purge` cannot reach it.

Enforced by ordering rather than by review vigilance: slice A shipped `bundle/decisions.py` wired to nothing, B1 shipped the sweeps, B2 shipped the writer on top. Proven by ancestry, not narrative — `git merge-base --is-ancestor 423da0d 6d26bde` is true, so the sweep precedes the writer on every reachable path. `main` received the whole vertical at once, with the property already intact.

## Specs synced to main

| Capability | Action |
|---|---|
| `pending-work` | CREATED — the new capability spec |
| `curate-command` | MODIFIED — "Contradictions Stage Is Report-Only And Last" now names the bundle as the surface it protects; "Resumability By Construction" distinguishes completed output from a checkpoint |
| `privacy-purge` | ADDED — "Whole-History Expunge Covers The Pending-Work Decision Subtree" |
| `forget-command` | ADDED — "Forget Sweeps Live Decision Entries Referencing The Purge Set" |
| `workspace-autocommit` | MODIFIED — "Scoped Staging Only" covers the decision path |

All landed by the slices themselves as they merged, not at archive.

## Known follow-up

**Issue #598 — `status` cannot see persisted contradiction findings.** `status` reads none of `.openkos/findings.db`; its `needs_attention` list is built from OKF conformance findings, dangling-reference lint and sensitivity checks. No slice ever had a `status` task. The pending-work spec was drafted claiming otherwise, verification caught it, and the requirement was narrowed to what ships rather than the feature being bolted onto the end of the chain. Deliberately out of scope, tracked, not unfinished work.

## Process notes worth carrying forward

**The forecast was low by roughly 3x.** `tasks.md` forecast ~1370 changed lines; the chain measured 3450 — A 1430 · B1 571 · B2 685 · C 764. The cause is this repo's per-symbol docstring convention, not extra behaviour: slice A's two new source modules are 212 and 195 lines and the rest was tests and the ADR. Two maintainer-accepted overruns are recorded in the attempt ledger rather than absorbed silently.

**Chained PRs were running with no CI.** `ci.yml` gated only `pull_request: branches: [main]`, so PR #594 opened with zero checks and every slice would have been unvalidated until the final PR. Commit `7cad80b` extends it to `tracker/**`. Note that a push to the base branch does not retrigger a `pull_request` workflow — the PR branch had to be rebased and force-pushed for CI to fire.

**Two tests passed vacuously on first run.** In both cases the green meant nothing until a mutation proved the guard. That is why the task list prescribed explicit mutation steps, and why `__pycache__` was purged before every revert re-run — a same-length revert can otherwise execute stale bytecode and report a false pass.

**A spec that overstates the code is worse than one that admits a gap.** It is the product contract. Both gaps found in verification were closed by changing the thing that was wrong: the code for `curate`, the wording for `status`.

## Rollback

`git revert` of `64640ce`. Findings are recomputable at LLM cost, so nothing durable is lost there. Decisions are not recomputable — a revert would drop the ability to read or write them, but any `bundle/.state/decisions/**` files already written stay on disk as canonical bundle state and become readable again if the change is re-applied.

## SDD cycle

Explore → Propose → Spec → Design → Tasks → Apply ×4 → Verify → Archive. Complete.
