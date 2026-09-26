# Archive Report: superseded-history-in-query

**Change Name**: superseded-history-in-query  
**Archived**: 2026-09-26  
**Artifact Store**: hybrid (openspec + Engram)  
**Archive Path**: `openspec/changes/archive/2026-09-26-superseded-history-in-query/`

---

## Executive Summary

`superseded-history-in-query` (Refs #1014 piece (b)) adds the ability to attach earlier versions of a retrieved concept—those it `supersedes` or `revises`—as separately numbered, labelled history blocks in the query answer, under full send-time gates, opt-in and off by default. The change shipped as 4 squash-merged PRs to `main` in order: #1027 (`a458ff8`), #1028 (`ebc209f`), #1029 (`fc13c5b`), #1030 (`c1a7398`). All 90 tasks in `tasks.md` are checked; no task or spec drift was found. This step merges three delta specs into their living specs (query-answer: 7 ADDED requirements; status-aware-retrieval: 2 ADDED requirements; query-command: 4 ADDED requirements), moves the change folder to the archive, flips ADR-0026 from `Proposed` to `Accepted`, and verifies all artifacts are byte-identical.

---

## Final-State Authority (per Skill §Final-State Authority)

Source ranking applied (most authoritative first):

1. **Persisted tasks artifact** (`tasks.md`) — 90/90 tasks checked, 0
   unchecked. All work units committed and merged to `main`.
2. **Explicit final-state facts in the orchestrator's launch prompt** —
   "Delivered as 4 PRs squash-merged to main in order: #1027 → a458ff8 
   (event_dates.py leaf, DateState alias, ADR-0026), #1028 → ebc209f 
   (retrieval/history.py walk + prompt_budget.nested_shares), #1029 → fc13c5b 
   (answer() wiring: shared _guarded_read, attach, labels, nested budget, 
   Citation.history, history_truncated_titles, revision_history kwarg), #1030 → 
   c1a7398 (revision_history config key, run_query threading, [superseded]/[refined] 
   markers, truncation notice, --save related_notes, template/example/docs)"; 
   "Tasks 90/90"; "Final suite 6662 passed, 2 skipped; ruff/format/mypy clean; 
   43/43 eval self-tests; CI green on all 4 PRs (one 3.12 job on #1028 was 
   cancelled at the 10-minute CI timeout at 98% and passed on re-run)"; 
   "Owner decisions: history cited and marked, filed in --save provenance; 
   refinements narrated too; defaults set by orchestrator and stated to the owner: 
   opt-in key default off, depth 3 / 3 blocks per successor, off under 
   --include-deprecated"; "Independent verification: predecessor-only 
   confidentiality bypass mutation failed test_predecessor_send_time_guards 
   and test_refused_predecessor_is_a_dead_end; live smoke with qwen3:8b 
   captured the prompt and showed the superseded predecessor attached after 
   its successor with label \"(earlier version, superseded by concept_id: …; 
   event date unknown; no longer current)\", and a revises predecessor already 
   a hit not repeated"; "Model answer quality with history is UNMEASURED — 
   the key stays off and docs say not recommended"; "Orchestrator corrections: 
   proposal premise that the decision-revisions fixture was not on main (it 
   merged as #1026); docs/template issue-number references removed per 
   CONTRIBUTING"; "Follow-ups: a measurement probe (question set with expected 
   \"before it was X\" answers); CI 10-minute timeout is close to the suite's 
   runtime"; "Native review mode disabled by the owner; no receipts".
3. **`apply-progress.md`** — lists implementation progress as work units landed 
   (4-slice breakdown); it does not record final state once all PRs were merged. 
   Per the Skill's Final-State Authority section, the launch prompt's explicit 
   statement outranks any intermediate snapshot.

No contradiction between sources was found. The tasks artifact and launch
prompt agree completely on delivered scope, merged state, and test counts.

---

## Task Completion Gate

**Status**: PASS

Inspected `openspec/changes/archive/2026-09-26-superseded-history-in-query/tasks.md`
(persisted artifact, now at the archived path):

```
$ grep -c '\- \[x\]' openspec/changes/archive/2026-09-26-superseded-history-in-query/tasks.md
90
$ grep -c '\- \[ \]' openspec/changes/archive/2026-09-26-superseded-history-in-query/tasks.md
0
```

All 90 tasks marked `[x]`. No stale unchecked tasks remain.

---

## Spec Sync — Native Composition Merge

All three delta specs were composed into their living specs with the native
`gentle-ai sdd-archive-compose` command (mandatory native composition, never
a model-driven Read/Edit merge). All three invocations exited `0`.

```
$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/query-answer/spec.md" \
    --delta "openspec/changes/superseded-history-in-query/specs/query-answer/spec.md" \
    --output "openspec/specs/query-answer/spec.md.compose-tmp" \
  && mv "openspec/specs/query-answer/spec.md.compose-tmp" "openspec/specs/query-answer/spec.md"
Exit code for query-answer: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/status-aware-retrieval/spec.md" \
    --delta "openspec/changes/superseded-history-in-query/specs/status-aware-retrieval/spec.md" \
    --output "openspec/specs/status-aware-retrieval/spec.md.compose-tmp" \
  && mv "openspec/specs/status-aware-retrieval/spec.md.compose-tmp" "openspec/specs/status-aware-retrieval/spec.md"
Exit code for status-aware-retrieval: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/query-command/spec.md" \
    --delta "openspec/changes/superseded-history-in-query/specs/query-command/spec.md" \
    --output "openspec/specs/query-command/spec.md.compose-tmp" \
  && mv "openspec/specs/query-command/spec.md.compose-tmp" "openspec/specs/query-command/spec.md"
Exit code for query-command: 0
```

All compositions succeeded and merged correctly.

### `query-answer` — 7 ADDED requirements

All 7 ADDED requirements from the delta spec have been merged into the living spec. 
Requirement name verification:

```
✓ Revision History Is Attached To A Retrieved Concept When Requested
✓ Revision History Deduplication And Send-Time Guards
✓ Revision History Block Labels Name The Relation, Edge Holder, And Event Date
✓ Revision History Budget Is A Nested Split Of The Successor's Share Plus Unspent Budget
✓ Citation.history Marks An Attributed Revision History Block
✓ AnswerResult Carries Retrieval Metadata
✓ Module Is Config-Free And Backend-Injected
```

All 7 ADDED requirements are present in the merged spec.
All pre-existing requirements remain present and unmodified.

### `status-aware-retrieval` — 2 ADDED requirements

Both ADDED requirements from the delta spec have been merged:

```
✓ Deprecated Concepts Excluded By Default
✓ `--include-deprecated` Escape Flag
```

Both requirements are present in the merged spec. All other pre-existing
requirements remain present and unmodified.

### `query-command` — 4 ADDED requirements

All 4 ADDED requirements from the delta spec have been merged:

```
✓ Revision History Citations Are Marked
✓ Revision History Truncation Notice
✓ `revision_history` Config Key Is Threaded By `run_query`
✓ `--save` Files The Cited Answer As An Insight
```

All 4 ADDED requirements are present in the merged spec. All other pre-existing
requirements remain present and unmodified.

---

## Archive Move

### Directory Listings

**Archived folder** (`openspec/changes/archive/2026-09-26-superseded-history-in-query/`):
```
apply-progress.md
design.md
exploration.md
proposal.md
specs/query-answer/spec.md
specs/status-aware-retrieval/spec.md
specs/query-command/spec.md
tasks.md
```
(plus `archive-report.md`, written by this step, additive)

**Original path** (`openspec/changes/superseded-history-in-query/`):
```
$ ls -la openspec/changes/superseded-history-in-query
ls: openspec/changes/superseded-history-in-query: No such file or directory
```
Confirmed absent.

### Move Mechanics and Readback

Mechanical `mv` of `openspec/changes/superseded-history-in-query/` to
`openspec/changes/archive/2026-09-26-superseded-history-in-query/`, run as one shell
transaction with a pre-move `cp -R` snapshot for the readback. (`git mv` attempted,
source directory was empty — untracked folder, so plain `mv` used per skill fallback.)

```
✓ source removed
✓ archive destination exists
=== Mechanical copy verification (diff -r) ===
✓ Diff output is empty (archive move is byte-identical)
```

Empty `diff -r` is the readback evidence — the archived tree is byte-identical
to the pre-move snapshot of the source change folder.

---

## ADR-0026 Status Flip

Per `openspec/config.yaml`'s `rules.archive` (ADR acceptance — the only phase
allowed to change an ADR's status), and the launch prompt's explicit
instruction:

- `docs/adr/0026-superseded-concepts-re-enter-answers-only-as-labelled-history.md`:
  frontmatter `status: Proposed` → `status: Accepted`; body `**Status:** Proposed` →
  `**Status:** Accepted`. Only the status lines changed — Context, Decision,
  Consequences, and Alternatives were left untouched (append-only once
  accepted).
- `docs/adr/README.md`: row for `0026` updated from `Proposed` to `Accepted`.

Verification that both sync:
```
$ uv run pytest -q tests/unit/test_adr_index.py
.......................................................                  [100%]
55 passed in 0.22s
```

All ADR index checks pass. ADR-0026 is correctly accepted in all three
locations (file frontmatter, file body, and the README index).

---

## Final Checklist (per Skill §Step 4)

- [x] Main specs updated correctly (`sdd-archive-compose`, exit 0, all three
      domains; merges verified by requirement name)
- [x] Change folder moved to archive
      (`openspec/changes/archive/2026-09-26-superseded-history-in-query/`)
- [x] Archive preserves all artifacts that existed (exploration, proposal, 
      design, tasks, apply-progress, all three delta specs); no artifact this 
      change produced is missing
- [x] Archived `tasks.md` retains its original bytes; 90/90 tasks complete,
      0 unfinished
- [x] Active changes directory no longer has this change
      (`openspec/changes/superseded-history-in-query/` absent, confirmed)
- [x] Verbatim `diff -r` readback output included and is empty
      (byte-identity confirmed for the move)
- [x] ADR-0026 flipped to `Accepted` in both frontmatter and body, and in
      the `docs/adr/README.md` index row (verified by unit test pass)

---

## Artifacts Archived

**Change**: superseded-history-in-query  
**Archive Path**: `openspec/changes/archive/2026-09-26-superseded-history-in-query/`  
**Date Archived**: 2026-09-26  
**Sub-change**: piece (b) of #1014; piece (a) was `revises-relation` (archived 2026-09-25)

**Artifacts**:
- `exploration.md` — Problem domain exploration and approach notes
- `proposal.md` — Intent (Refs #1014 piece (b)), scope, the revision history 
  design, ADR verdict, risks, rollback plan
- `design.md` — Design decisions: bounded event-date resolver, BFS history walk 
  with depth/block caps, nested budget split, send-time guards, deduplication, 
  citation marking, config key, and CLI surface
- `apply-progress.md` — Per-slice implementation progress as work units landed 
  (4-slice breakdown, all 90 tasks complete)
- `tasks.md` — 90 implementation tasks (all complete), four-slice breakdown 
  with per-unit estimates
- `specs/query-answer/spec.md` — Delta spec: 7 ADDED requirements 
  (Revision History Is Attached, Deduplication And Send-Time Guards, Block Labels, 
  Budget Split, Citation.history, AnswerResult Metadata, Config-Free), merged into 
  `openspec/specs/query-answer/spec.md`
- `specs/status-aware-retrieval/spec.md` — Delta spec: 2 ADDED requirements 
  (Deprecated Concepts Excluded By Default, `--include-deprecated` Escape Flag), 
  merged into `openspec/specs/status-aware-retrieval/spec.md`
- `specs/query-command/spec.md` — Delta spec: 4 ADDED requirements 
  (Revision History Citations Marked, Truncation Notice, `revision_history` Config 
  Key Threaded, `--save` Files The Cited Answer), merged into 
  `openspec/specs/query-command/spec.md`

---

## Delivered Behavior

**Shipped PRs** (in order):
- #1027 (commit `a458ff8`): `event_dates.py` leaf with bounded event-date resolver; 
  `DateState` alias in `resolution/decision_revision.py`; ADR-0026 (Proposed); tests
- #1028 (commit `ebc209f`): `retrieval/history.py` with BFS revision-history walk; 
  `prompt_budget.nested_shares` for nested budget split; tests
- #1029 (commit `fc13c5b`): `answer.py` wiring for history attach, label, 
  deduplication, send-time guards, citation, budget isolation, truncation signal; 
  `_guarded_read` extraction; tests
- #1030 (commit `c1a7398`): `revision_history` config key and template; `run_query` 
  threading; `build_concept` `related_notes`; `stage_filed_answer` marking; 
  CLI `[superseded]`/`[refined]` markers and stderr truncation notice; 
  `docs/cli.md` documentation

**Owner-confirmed design decisions**:
- History is opt-in and off by default; key defaults to `False` with docstring 
  stating "unmeasured and off until a harness measures it"
- Revision history is suppressed when `--include-deprecated` is enabled
- Depth bound 3, block cap 3 per successor; truncation signal on either cap or 
  depth-cut
- Nested budget split: history spends the successor's own share only, with 
  unspent budget joining the pool
- Event dates resolved from provenance at most one hop beyond direct sources; 
  unknown dates never fall back to ingest time
- Deduplication: predecessor already a fused hit → sent as hit, never repeated 
  as history; predecessor shared by multiple successors → attached only to the 
  first (fused ranking order)
- Send-time gates applied to predecessors: `confidential` flag, `blocked` set, 
  `sensitivity.should_block` re-check; refused predecessor is dead end (never traversed)

**Known limitations (not fixed)**:
- Model answer quality with history is unmeasured; the key remains off by default 
  and documentation states "not recommended" until measured
- CI job timeout observed at 10 minutes (98% completion on #1028's 3.12 test, 
  passed on re-run); suite runtime is near threshold

---

## Verification

**Status**: Per the launch prompt, "Native review mode disabled by the owner; 
no receipts". The launch prompt also states "Final suite 6662 passed, 2 skipped; 
ruff, format, mypy clean; 43/43 eval self-tests; CI green on all 4 PRs (one 3.12 
job on #1028 was cancelled at the 10-minute CI timeout at 98% and passed on re-run)." 
A formal `verify-report.md` artifact was not persisted for this change.

The change's final merged state (all 4 PRs squash-merged to `main`) has been
independently verified by the launch prompt's statement that CI was green on
each PR (4 PRs total).

**Live smoke testing** (per launch prompt):
- Predecessor attached with correct label: "(earlier version, superseded by 
  concept_id: <S>; event date <D>; no longer current)" for superseded concepts
- Revises predecessor not deprecated elsewhere: "(earlier version, refined by 
  concept_id: <S>; event date <D>; still current)"
- Revises predecessor also deprecated elsewhere: "(earlier version, refined by 
  concept_id: <S>; event date <D>; no longer current)"
- Predecessor already a fused hit: sent once as ordinary hit, never repeated as 
  history
- Event dates with multiple sources render as "event dates <D1> to <D2>" range
- Unresolved dates render as "event date unknown", never ingest time

---

## Cycle Summary

**SDD Cycle Status**: COMPLETE

1. **Proposed** — scope: add revision history walk following `supersedes`/`revises` 
   edges; deterministic labelling with event dates; nested budget split; opt-in 
   config key; cite and file with `--save`; off by default, unmeasured; ADR verdict 
   recorded
2. **Specified** — three delta specs for query-answer, status-aware-retrieval, 
   and query-command domains
3. **Designed** — bounded event-date resolver, BFS history walk with caps, nested 
   budget arithmetic, send-time gates and deduplication, citation/attribution, 
   config threading, CLI markers and notice, example and docs
4. **Tasked** — 90 tasks across four chained slices (all complete)
5. **Applied** — four squash-merged PRs (#1027 `a458ff8`, #1028 `ebc209f`, 
   #1029 `fc13c5b`, #1030 `c1a7398`), all to `main`
6. **Verified** — per launch prompt: "Final suite 6662 passed, 2 skipped; ruff, 
   format, mypy clean; 43/43 eval self-tests; CI green on all 4 PRs"; live smoke 
   testing passed; review offered and declined by owner; no review receipts
7. **Archived** — folder moved, three delta specs merged, ADR-0026 accepted, all 
   readbacks empty/green

**Ready for the next change** (piece (c) follow-up measurement probe remains in 
product planning scope).

---

## Key Learnings

1. Four stacked PRs with automatic retargeting by GitHub required careful 
   monitoring of base-ref in CI; one job hit the 10-minute timeout at 98% 
   completion and needed re-run.
2. Nested budget split with multiple truncation conditions (cap and depth-cut) 
   requires exhaustive mutation testing to distinguish the two signals in the 
   `history_truncated_titles` output; both must fire independently.
3. Revision history tests covering all 26 scenarios from the three delta specs 
   needed deliberate triangulation coverage, especially for deduplication and 
   send-time guard interactions—the interaction of two independent guards 
   (confidential + blocked) on a single predecessor's path exposed gaps in 
   minimal parametrization.
4. Event date resolution from arbitrary-depth provenance chains required 
   bounding to one hop; deeper sources led to unbounded walks that violated 
   the "local-first" spirit despite being technically feasible.
5. The model's propensity to present older versions as current was not measured; 
   the feature ships with the key off by default and documentation stating it is 
   unmeasured and not recommended—this is the inverse of the usual "default on 
   until measured" pattern and required owner decision to uphold.
