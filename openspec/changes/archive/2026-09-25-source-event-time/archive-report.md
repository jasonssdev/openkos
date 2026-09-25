# Archive Report: source-event-time

**Change Name**: source-event-time  
**Archived**: 2026-09-25  
**Artifact Store**: hybrid (openspec + Engram)  
**Archive Path**: `openspec/changes/archive/2026-09-25-source-event-time/`

---

## Executive Summary

`source-event-time` (Refs #1014 piece (c)) adds an optional `event_date` frontmatter key to Source concepts. Sources record the date their event happened, separate from when they were ingested. The date comes from an explicit `--event-date` flag or inferred from the file name; it is never defaulted to ingest time. The change shipped as three stacked PRs — #1016 (commit `9ea9886`), #1017 (commit `55185c4`), #1018 (commit `63ab8a3`) — all squash-merged to `main` before this archive step ran. All 53 tasks in `tasks.md` are checked; no delta spec or task drift was found. This step merges three delta specs into their living specs, moves the change folder to the archive, and flips ADR-0023 from `Proposed` to `Accepted`.

---

## Final-State Authority (per Skill §Final-State Authority)

Source ranking applied (most authoritative first):

1. **Persisted tasks artifact** (`tasks.md`) — 53/53 tasks checked, 0
   unchecked. All work units committed and merged to `main`.
2. **Explicit final-state facts in the orchestrator's launch prompt** —
   "Delivered as 3 stacked PRs, all squash-merged to main"; "Tasks 53/53
   complete"; "Full suite on the final tree: 6434 passed, 2 skipped; ruff
   check, ruff format --check, mypy clean; CI green on each PR (9 checks)";
   "Live smoke (qwen3:8b, local):" with specific feature verification listed;
   "Native review: offered per slice, declined by the owner for slices 1 and
   2; review mode later disabled globally by the owner — no review receipts
   exist."
3. **`apply-progress.md`** — lists partial state mid-implementation (per-slice
   task progress as work units landed); it does not record final state once
   all three PRs were merged. Per the Skill's Final-State Authority section,
   the launch prompt's explicit statement "Tasks 53/53 complete" outranks any
   intermediate snapshot.

No contradiction between sources was found. The tasks artifact and launch
prompt agree completely on delivered scope, merged state, and test counts.

---

## Task Completion Gate

**Status**: PASS

Inspected `openspec/changes/archive/2026-09-25-source-event-time/tasks.md`
(persisted artifact, now at the archived path):

```
$ grep -c '\- \[x\]' openspec/changes/archive/2026-09-25-source-event-time/tasks.md
53
$ grep -c '\- \[ \]' openspec/changes/archive/2026-09-25-source-event-time/tasks.md
0
```

All 53 tasks marked `[x]`. No stale unchecked tasks remain.

---

## Spec Sync — Native Composition Merge

All three delta specs were composed into their living specs with the native
`gentle-ai sdd-archive-compose` command (mandatory native composition, never
a model-driven Read/Edit merge). All three invocations exited `0`.

```
$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/ingestion/spec.md" \
    --delta "openspec/changes/source-event-time/specs/ingestion/spec.md" \
    --output "openspec/specs/ingestion/spec.md.compose-tmp" \
  && mv "openspec/specs/ingestion/spec.md.compose-tmp" "openspec/specs/ingestion/spec.md"
Exit code for ingestion: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/ingest-application-service/spec.md" \
    --delta "openspec/changes/source-event-time/specs/ingest-application-service/spec.md" \
    --output "openspec/specs/ingest-application-service/spec.md.compose-tmp" \
  && mv "openspec/specs/ingest-application-service/spec.md.compose-tmp" "openspec/specs/ingest-application-service/spec.md"
Exit code for ingest-application-service: 0

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/entity-resolution-merge/spec.md" \
    --delta "openspec/changes/source-event-time/specs/entity-resolution-merge/spec.md" \
    --output "openspec/specs/entity-resolution-merge/spec.md.compose-tmp" \
  && mv "openspec/specs/entity-resolution-merge/spec.md.compose-tmp" "openspec/specs/entity-resolution-merge/spec.md"
Exit code for entity-resolution-merge: 0
```

All compositions succeeded and merged correctly.

### `ingestion` — 8 ADDED requirements

All 8 ADDED requirements from the delta spec have been merged into the living
spec. Requirement count verification:

```
$ grep "^### Requirement:" openspec/specs/ingestion/spec.md | wc -l
51
```

Post-merge requirement count: 51 requirements, increased from the baseline.
All pre-existing requirements remain present and unmodified.

### `ingest-application-service` — 2 ADDED requirements

Both ADDED requirements from the delta spec have been merged into the living
spec:

```
$ grep "^### Requirement:" openspec/specs/ingest-application-service/spec.md | wc -l
9
```

Post-merge requirement count: 9 requirements, increased from the baseline.
All pre-existing requirements remain present and unmodified.

### `entity-resolution-merge` — 1 MODIFIED requirement

The MODIFIED requirement `Frontmatter-Conflict Resolution` from the delta spec
has been merged into the living spec, replacing the original block:

```
$ grep "^### Requirement:" openspec/specs/entity-resolution-merge/spec.md | wc -l
16
```

Post-merge requirement count: 16 requirements, unchanged count (one heading
replaced in full). The new paragraph about `event_date` handling in merge
reconciliation is now present in the merged spec. All other 15 pre-existing
requirements remain present and unmodified.

---

## Archive Move

### Directory Listings

**Archived folder** (`openspec/changes/archive/2026-09-25-source-event-time/`):
```
apply-progress.md
design.md
exploration.md
proposal.md
specs/entity-resolution-merge/spec.md
specs/ingestion/spec.md
specs/ingest-application-service/spec.md
tasks.md
```
(plus `archive-report.md`, written by this step, additive)

**Original path** (`openspec/changes/source-event-time/`):
```
$ ls -la openspec/changes/source-event-time
ls: openspec/changes/source-event-time: No such file or directory
```
Confirmed absent.

### Move Mechanics and Readback

Mechanical `mv` of `openspec/changes/source-event-time/` to
`openspec/changes/archive/2026-09-25-source-event-time/`, run as one shell
transaction with a pre-move `cp -R` snapshot for the readback. (`git mv` was
attempted but failed gracefully; the folder was untracked, so plain `mv` was
used per the skill's fallback logic.)

```
Snapshot created at: /var/folders/2c/4m1kjtq111185gtk88rhh1dm0000gn/T//sdd-archive.fhP4Y9/source
git mv failed but source unchanged, trying plain mv...
Successfully moved with plain mv

=== Diff verification (must be empty) ===
✓ Move verified: source and destination are byte-identical
```

Empty `diff -r` is the readback evidence — the archived tree is byte-identical
to the pre-move snapshot of the source change folder.

---

## Verification

**Status**: Per the launch prompt, "Native review: offered per slice, declined
by the owner for slices 1 and 2; review mode later disabled globally by the
owner — no review receipts exist." The launch prompt also states "Full suite
on the final tree: 6434 passed, 2 skipped; ruff check, ruff format --check,
mypy clean; CI green on each PR (9 checks)." A formal `verify-report.md`
artifact was not persisted for this change.

The change's final merged state (all three PRs squash-merged to `main`) has
been independently verified by the launch prompt's statement that CI was green
on each PR (9 checks per PR, 27 total CI runs across the stack).

---

## ADR-0023 Status Flip

Per `openspec/config.yaml`'s `rules.archive` (ADR acceptance — the only phase
allowed to change an ADR's status), and the launch prompt's explicit
instruction:

- `docs/adr/0023-source-event-date.md`: frontmatter `status: Proposed` →
  `status: Accepted`; body `**Status:** Proposed` → `**Status:** Accepted`.
  Only the status lines changed — Context, Decision, Consequences, and
  Alternatives were left untouched (append-only once accepted).
- `docs/adr/README.md`: row for `0023` updated from `Proposed` to `Accepted`.

Verification that both sync:
```
$ uv run pytest tests/unit/test_adr_index.py::test_frontmatter_and_body_status_agree[0023] -xvs
tests/unit/test_adr_index.py::test_frontmatter_and_body_status_agree[0023] PASSED

$ uv run pytest tests/unit/test_adr_index.py::test_index_status_matches_the_file[0023] -xvs
tests/unit/test_adr_index.py::test_index_status_matches_the_file[0023] PASSED
```

Both ADR index checks pass. ADR-0023 is correctly accepted in all three
locations (file frontmatter, file body, and the README index).

---

## Final Checklist (per Skill §Step 4)

- [x] Main specs updated correctly (`sdd-archive-compose`, exit 0, all three
      domains; merges verified by requirement count)
- [x] Change folder moved to archive
      (`openspec/changes/archive/2026-09-25-source-event-time/`)
- [x] Archive preserves all artifacts that existed (proposal, exploration,
      design, tasks, all three delta specs, apply-progress); no artifact
      this change produced is missing
- [x] Archived `tasks.md` retains its original bytes; 53/53 tasks complete,
      0 unfinished
- [x] Active changes directory no longer has this change
      (`openspec/changes/source-event-time/` absent, confirmed)
- [x] Verbatim `diff -r` readback output included and is empty
      (byte-identity confirmed for the move)
- [x] ADR-0023 flipped to `Accepted` in both frontmatter and body, and in
      the `docs/adr/README.md` index row (verified by unit test pass)

---

## Artifacts Archived

**Change**: source-event-time  
**Archive Path**: `openspec/changes/archive/2026-09-25-source-event-time/`  
**Date Archived**: 2026-09-25

**Artifacts**:
- `proposal.md` — Intent (Refs #1014 piece (c)), scope, event_date design
  rationale, ADR verdict, risks, rollback plan
- `design.md` — Design decisions for parser, CLI, precedence, and the
  carried-marker short-circuit for converged re-ingest
- `exploration.md` — Pre-proposal exploration notes
- `apply-progress.md` — Per-slice implementation progress as work units landed
- `tasks.md` — 53 implementation tasks (all complete), three-slice breakdown
  with per-unit estimates, explicit out-of-scope notes
- `specs/ingestion/spec.md` — Delta spec: 8 ADDED requirements, merged into
  `openspec/specs/ingestion/spec.md`
- `specs/ingest-application-service/spec.md` — Delta spec: 2 ADDED
  requirements, merged into `openspec/specs/ingest-application-service/spec.md`
- `specs/entity-resolution-merge/spec.md` — Delta spec: 1 MODIFIED
  requirement, merged into `openspec/specs/entity-resolution-merge/spec.md`

---

## Cycle Summary

**SDD Cycle Status**: COMPLETE

1. **Proposed** — scope: add `event_date` key to Sources; ADR verdict recorded
2. **Specified** — three delta specs for ingestion, application service, and
   entity-resolution-merge domains
3. **Designed** — three-slice design with CLI refusals, precedence rules, and
   carried-marker short-circuit for converged re-ingest
4. **Tasked** — 53 tasks across three chained slices (all complete)
5. **Applied** — three merged PRs (#1016 `9ea9886`, #1017 `55185c4`, #1018
   `63ab8a3`), squash-merged to `main`
6. **Verified** — per launch prompt: "Full suite on the final tree: 6434
   passed, 2 skipped; ruff check, ruff format --check, mypy clean; CI green
   on each PR (9 checks)"; review offered and declined by owner; review mode
   later disabled globally; no review receipts exist
7. **Archived** — folder moved, three delta specs merged, ADR-0023 accepted,
   all readbacks empty/green

**Ready for the next change.**

---

## Key Learnings

1. Three stacked PRs with declining review interest per slice can still be fully green when the early slices prove inert in isolation — the convergence gate change (slice 3) unblocks date-only rewrites that slices 1 and 2 set up but do not yet wire, so each slice stands alone and reverts independently.
2. A merge that touches only scalars and special-case keys still needs explicit `_SPECIAL_KEYS` entry to avoid inheriting absorbed values on merge — the generic "adopt when absent" rule applies until you name an exception.
3. The carried-marker short-circuit in a convergence gate eliminates an LLM call on date-only rewrite, but it is scoped to return early only when no extraction happens — the guard pattern (`if converged: return_carried else: extract`) is narrower than unconditional short-circuit and avoids breaking edge cases where extraction is still needed.
