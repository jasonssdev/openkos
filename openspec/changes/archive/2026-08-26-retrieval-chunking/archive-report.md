# Archive Report: retrieval-chunking

**Archived**: 2026-08-26
**Change folder**: `openspec/changes/archive/2026-08-26-retrieval-chunking/`
**Branch**: `retrieval-chunking-888` (9 commits at time of archive, HEAD `d07bbb1`, local only — NOT pushed, no PR opened)
**Artifact store mode**: hybrid (filesystem + Engram)

## Final-State Authority Applied

This report records the state of the change AT CLOSE, per the Final-State Authority
hierarchy. Two of the persisted snapshots (`apply-progress.md`, `verify-report.md`)
contain claims that were true when written but are superseded by later work in the
same branch. Both are cited below with the commit that superseded them.

### 1. Pair-nomination gate: PASSES (was FAIL in apply-progress body)

`apply-progress.md`'s main body (written before commit `5b596c2`) records the D9
pair-nomination probe as FAIL: post-change margin `-0.0820` vs pre-change `-0.0489`.
Its own trailing "Final-state correction" section (written after `5b596c2`)
supersedes that: two pairs were removed from the fixture's `unrelated` set because
both violated the fixture's own labelling criterion. Rescored: pre `-0.0328`, post
`-0.0298`. **Both margins remain NEGATIVE.** The gate's only claim is that chunking
did not make the false-positive risk on this fixture worse than the (already
negative) pre-change baseline — it does NOT claim the underlying signal separates
positive from negative. `verify-report.md` independently re-derived this PASS from
`compare-pre-vs-post.txt` and `pair_labels.json` and confirmed the correction is
accurate and disclosed, not hidden.

### 2. Verify-report WARNING closed: confidential multi-chunk exclusion now has a runtime test

`verify-report.md` (written before `d07bbb1`) recorded a WARNING: the
confidential-chunked-document sensitivity-exclusion scenario was "structurally
sound but not runtime-proven by a dedicated test." Commit `d07bbb1` (the final
commit on the branch, after verify-report was persisted) closes this by adding
`test_confidential_multi_chunk_document_never_reaches_the_llm`. Mutation evidence
recorded on the branch: the exclusion path has THREE independent guards
(`answer.py:858`, `:490`, `:506`); the test stays green with any one or two
disabled and goes red only with all three disabled — the correct signature for an
outcome test over a redundant, defense-in-depth path.

### 3. Test count and static checks (final, post-`d07bbb1`)

**5687 passed, 1 skipped** (verify-report recorded 5686 before the new test in
`d07bbb1` was added). `mypy .` clean across 273 files. `ruff check .` and
`ruff format --check .` clean. These final numbers supersede the count in
`verify-report.md`.

### 4. Attempt/line-budget ledger

Resolved by an explicit maintainer decision accepting 3,878 total lines: 1,995
authored code+tests, 1,717 SDD artifacts, 166 eval goldens. Reset recorded under
ledger id `rc-reset-001`.

### 5. Task 5.2 — open follow-up, NOT reconciled to done

`tasks.md` (carried into the archive unchanged) has exactly one unchecked
implementation-adjacent task:

> 5.2 NOT RUN this batch: `evals/edge_typing/`, `evals/contradictions/`,
> `evals/query_identity/` against their recorded bands. ... Left for a follow-up
> verification pass.

This task lives under "Phase 5: Cross-Cutting Verification (no new production
code)" — a verification/eval activity, not shipped feature-implementation work,
and the change's own production diff is zero in every code path those evals
exercise (see #6 below). `apply-progress.md`'s final-state correction and
`verify-report.md` both independently endorse deferring this: `edge_typing` and
`contradictions` score classifiers over fixed fixtures that do not depend on live
proximity nomination, and `query_identity` measures the question-vector space this
change never touches (`state/question_vectors.py`,
`resolution/insight_identity.py` are explicitly out of scope per the
`embedding-chunking` capability's Non-Goals section).

**This checkbox is left UNCHECKED in the archived `tasks.md`.** It is not stale —
it reflects real, currently-open work — so it is recorded here as an **explicit,
approved partial-archive exception** per the Strict-vs-OpenSpec Archive Policy,
backed by the orchestrator's launch-prompt instruction and by independent
verify-report endorsement of the deferral rationale, not by proof that the task is
actually complete. Follow-up: re-run `evals/edge_typing/`, `evals/contradictions/`,
`evals/query_identity/` against their recorded bands in a subsequent pass.

### 6. Production diff scope (final)

Zero production diff in `src/openkos/retrieval/answer.py`, `retrieval/fusion.py`,
`graph/proximity.py` for the entire change. Only `cli/main.py`,
`state/reindex.py`, and `state/vectorstore.py` changed in production code.

## Native Review Receipt Gate

Checked via `gentle-ai review status --cwd /Users/jasonssdev/Dev/Projects/openkos
--contract gentle-ai.review-integration/v2 --agent claude-code --next-transition`
immediately before this archive. Result: `"candidates": []`, `"applicability":
"unrelated"`, `"receipt": {"status": "not_applicable"}` — no review was ever
started or discovered for this candidate. `reviewGate` is structurally absent.
Receipt-driven development is on (`gentle-ai review mode status` reports "on
(decided by default)"), but with no review ever begun for this candidate, archive
proceeds under ordinary repository policy per the gate's second absent-case. This
is an invitation state, not a block; nothing about proceeding without a review is
recorded as a decline.

## Task Completion Gate

`tasks.md` has 43 of 44 checkboxes checked. The one open item (5.2) is handled
under the explicit partial-archive exception in section 5 above, not reconciled to
`[x]`. No stale-checkbox reconciliation was performed — every `[x]` in the archived
`tasks.md` reflects work `sdd-apply` itself marked complete; this phase did not
alter any checkbox.

## Verify Report

`verify-report.md` (observation carried from `sdd/retrieval-chunking/verify-report`)
recorded `critical_findings: 0` and an explicit `**CRITICAL**: None.` line. No
CRITICAL issues exist at any point in this cycle's history — none needed to be
resolved for archive to proceed.

## Specs Synced

| Domain | Action | Requirements touched | Notes |
|---|---|---|---|
| `embedding-chunking` | Created | 3 requirements (full new capability) | Mechanical `cp` — no live spec existed. `diff -r` empty. |
| `vector-store` | Updated | 2 MODIFIED (`Idempotent Vector Schema`, `k-NN Query Data Flow`) + 3 ADDED (`Legacy-Shape Store Is Migrated, Not Silently Reused`, `Multi-Chunk Upsert Is Atomic And Orphan-Free`, `Neighbors Reads The Derived Document Vector And Preserves The Never-Raises Degrade`) = 5 requirements touched, 18 total requirements in file afterward, 0 duplicate headings | Both MODIFIED headings matched the live spec byte-for-byte before merge |
| `reindex-command` | Updated | 2 MODIFIED (`Composed Embed Text Replaces Raw-Bytes Embedding`, `Per-Doc Embed Failure Is Isolated, Not Fatal`) + 1 ADDED (`Reindex Discloses The Real Re-Embed Trigger, Not A False Model-Change Claim`) = 3 requirements touched, 15 total requirements in file afterward, 0 duplicate headings | See scenario-count detail below |
| `privacy-purge` | Updated | 1 MODIFIED (`Deferred-Reembed Warning On Success`) | Scenario count 2 → 3 (added the pre-emptive-quoting scenario) |
| `query-answer` | Updated | 3 ADDED (`Chunk-Backed Dense Retrieval Reaches A Document's Tail`, `Chunk Collapse Is Invisible To Citation, Attribution, And Save Provenance`, `The Sensitivity Re-Check Still Runs Before Any Chunk's Content Reaches The LLM`) | 17 total requirements in file afterward, 0 duplicate headings |
| `graph-projection` | Updated | 1 ADDED (`Embedding-Proximity Pairs Are Derived From Chunk-Backed Document Vectors`) | 16 total requirements in file afterward, 0 duplicate headings |

### Per-Doc Embed Failure Is Isolated, Not Fatal — scenario count (special care item)

- **Before merge (live spec)**: 6 scenarios.
- **After merge (delta fully replaces the block)**: 7 scenarios.
- Verified by direct count (`grep -c '^#### Scenario:'` scoped to the requirement
  block) before writing the Edit and again after, both via `awk` range extraction
  post-merge. All three FATAL-subclass scenarios (`OllamaUnavailable`,
  `OllamaModelNotFound`, `OllamaEmbeddingDimensionMismatch` mid-chunk-loop) are
  present in the merged block; none were dropped.

## Mechanical Copy / Move Verification

All diffs below are verbatim tool output captured during this phase.

**`embedding-chunking` mechanical copy** (source vs. temp, then source vs. final target):
```
$ diff -r openspec/changes/retrieval-chunking/specs/embedding-chunking/spec.md <temp>
(no output — diff status 0)
$ diff -r openspec/changes/retrieval-chunking/specs/embedding-chunking/spec.md openspec/specs/embedding-chunking/spec.md
(no output — diff status 0)
```

**Archive folder move** (`git mv openspec/changes/retrieval-chunking
openspec/changes/archive/2026-08-26-retrieval-chunking`, readback against a
pre-move recursive snapshot taken at `$snapshot_root/source`):
```
$ diff -r "$snapshot_root/source" "openspec/changes/archive/2026-08-26-retrieval-chunking"
(no output — diff status 0)
```
`git mv` succeeded directly (no fallback to plain `mv` was needed). The source
directory `openspec/changes/retrieval-chunking/` no longer exists on disk —
confirmed via `ls` returning "No such file or directory" immediately after the
move, before the readback diff ran.

## Archive Contents (verified on disk)

- `proposal.md` — present (11,741 bytes)
- `exploration.md` — present (22,237 bytes) — NOT dropped
- `design.md` — present (22,490 bytes)
- `tasks.md` — present (13,630 bytes), 43/44 checked, 5.2 open per exception above
- `apply-progress.md` — present (14,234 bytes)
- `verify-report.md` — present (21,616 bytes) — was untracked at archive time
  (written after the branch's last commit); `git mv` carried the tracked files,
  and the untracked `verify-report.md` moved with the directory via the same `mv`
  syscall underlying `git mv`'s directory rename, confirmed present at the
  destination by direct `ls` and included, byte-identical, in the empty `diff -r`
  readback above.
- `specs/` — present, all 6 domain subfolders (`embedding-chunking`,
  `vector-store`, `reindex-command`, `privacy-purge`, `query-answer`,
  `graph-projection`)
- `archive-report.md` — this file (additive, did not exist in source, correctly
  excluded from the move's `diff -r` comparison)
- `STATUS.md` — written alongside this report

## Observation IDs Read (traceability)

Per Engram artifact retrieval convention, the following `sdd/retrieval-chunking/*`
topics were consulted for this archive (filesystem was authoritative per hybrid
mode; Engram observations corroborate the same content):

- `sdd/retrieval-chunking/proposal`
- `sdd/retrieval-chunking/spec`
- `sdd/retrieval-chunking/design`
- `sdd/retrieval-chunking/tasks`
- `sdd/retrieval-chunking/apply-progress`
- `sdd/retrieval-chunking/verify-report`
- `sdd/retrieval-chunking/explore`

(Exact numeric observation IDs were not required for the filesystem-authoritative
merge/move in this hybrid-mode run; the archive report itself is persisted to
Engram as `sdd/retrieval-chunking/archive-report`, closing the traceability loop
for any future search by topic key.)

## Delivery

No commit, push, PR, or merge was performed by this phase. All changes (spec
merges + archive move) are currently unstaged/staged working-tree modifications on
branch `retrieval-chunking-888`. The maintainer decides delivery
(`delivery_strategy: single-pr` was cached at session start).

## Risks / Open Items Carried Forward

1. **Pair-nomination margins are negative on both sides of the change.** Chunking
   did not worsen the measured false-positive risk on the D9 fixture, but the
   fixture does not demonstrate positive/negative separation. Not a regression;
   pre-existing condition, disclosed rather than hidden.
2. **Task 5.2 is open**: `edge_typing`, `contradictions`, `query_identity` evals
   need to be re-run against recorded bands in a follow-up pass. Low measured risk
   (zero production diff in the code paths those evals exercise), but genuinely
   unmeasured post-change.
3. **Branch is local-only, unpushed, no PR.** Per launch-prompt instruction, this
   phase did not push, open a PR, or merge — that decision belongs to the
   maintainer.
