# Verification Report

**Change**: durable-pending-work (#572, #556)
**Mode**: Strict TDD
**Branch inspected**: `tracker/durable-pending-work` at `5d0b038`, with all four slice PRs merged into it
**Verdict**: PASS WITH WARNINGS — two spec-vs-code gaps found, both resolved before archive

## Completeness

| Metric | Value |
|--------|-------|
| Tasks total | 34 (A 15, B1 12, B2 13, C 8, minus shared gate checklist) |
| Tasks complete | all |
| Tasks incomplete | 0 |

Ticks were audited against disk state and against real test names, not trusted from the checkbox.

## Build and tests

Every command run unpiped.

| Command | Result |
|---|---|
| `uv run pytest` | 4335 passed, 1 skipped (baseline before the change: 4287 passed, 1 skipped) |
| `uv run ruff check .` | all checks passed |
| `uv run ruff format --check .` | 198 files already formatted |
| `uv run mypy .` | success, 198 source files |
| coverage | 96.93% branch, against a 90% gate |

## D6 — the sweep precedes the writer

The load-bearing property of this change: no commit on any branch may exist where a decision file can be written but `purge` cannot reach it.

Proven by ancestry rather than by narrative commit ordering:

```
git merge-base --is-ancestor 423da0d 6d26bde   # true
```

`423da0d` is the purge/forget decision-subtree sweep (slice B1). `6d26bde` is the `--decline`/`--reopen` writer (slice B2). The sweep is an ancestor of the writer on every reachable path. Slice A's `bundle/decisions.py` was confirmed unwired to any CLI verb by grep at the time it landed. The tracker branch had not yet reached `main` when this was verified, so no gap window was ever exposed on the default branch.

## Honesty guard

`next_action.py`'s `open_contradictions` pre-filters on open ∧ not stale ∧ not declined, and `_tier_open_contradictions` carries that guard as its whole reason to exist. `_NO_ACTION_LINE` and the `None`-action contract are untouched — the tier was written to fit them, not the reverse. A bundle whose only findings are stale or declined yields no action while the status pointer still renders, so an unranked finding cannot read as a clean bundle.

## Findings

### 1. RESOLVED — `curate` re-showed declined findings

The pending-work spec requires a declined finding to stay out of ordinary `curate` output. The Contradictions stage echoed every verdict it recomputed with no reference to the operator's decision, so a declined contradiction returned on the next run — the exact failure the change exists to remove.

Fixed in `8f544f3` before archive: the echo loop filters through the same `_is_contradiction_declined` check the `contradictions` verb uses. Verdicts are still persisted unfiltered.

### 2. RESOLVED — the spec claimed `status` reads persisted findings

`status` contains no call to `state.findings.open_findings`; its `needs_attention` list is built from OKF conformance findings, dangling-reference lint and sensitivity checks, which are a different concept entirely. No slice ever carried a `status` task — the requirement was drafted ahead of the plan rather than from it.

Resolved in `15cae06` by narrowing the requirement and the stale-visibility scenario to `next` and the declined-listing view, which is what ships. `status` remains named in the declined-hiding requirement, where the constraint holds today and must keep holding if `status` ever grows a findings reader. The gap is tracked as issue **#598**.

### 3. RESOLVED — broken traceability citation

The requirement traceability table cited task `C2.10`, which was never written. Corrected to `C2.1`.

### 4. CLOSED — decision identity wording

The draft spec named "verdict kind" as part of the decision identity while the shipped `decision_key_for` takes only `pair_ids` and `merged_absorbed_id`. The landed spec already matches the shipped signature; the mismatch existed only in the change-folder draft.

## Delivery posture

Receipt-driven development was switched off by the maintainer during this work. Delivery is `disabled/unmanaged` — no review receipt exists, and its absence is expected rather than a blocker.
