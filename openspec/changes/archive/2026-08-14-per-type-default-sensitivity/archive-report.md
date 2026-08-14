# Archive Report: Per-Type Default Sensitivity (Person born above the workspace floor)

**Change**: per-type-default-sensitivity
**Issue**: #669
**Archived**: 2026-08-14
**Artifact Store**: hybrid (Engram + filesystem)
**Status**: Complete, closed

## Summary

`type-sensitivity-defaults` lets specific OKF types (shipping with `Person`
only, one level above the floor) be born a fixed number of levels above the
workspace's `default_sensitivity` floor, without weakening Source
inheritance, without touching `set-sensitivity` or any other post-birth
write path, and without migrating any concept already on disk. The change
shipped as four chained/stacked PRs targeting `main`, all merged with green
CI.

## What Shipped

**All 4 PRs merged to main:**
- PR #679 (commits `8387c68` -> squash-merged as `5055a5e`): `feat(sensitivity): raise_by helper + type_sensitivity_defaults config seam (#669 s1)` — WU1 (`okf.raise_by` model-layer helper) + WU2 (config seam: `type_sensitivity_defaults`, eager validation, `type_birth_sensitivity` resolver).
- PR #680 (commits `4dd808c` -> squash-merged as `7301daa`): `feat(ingest): derived objects consult the per-type birth sensitivity (#669 WU3)` — ingest seam (`_stage_derived_objects` wiring, run-summary advisory, count plumbing).
- PR #681 (commits `1fc813a` -> squash-merged as `d90dc65`): `feat(query): --save filed answers consult the per-type birth sensitivity (#669 WU4)` — `query --save` seam (`_stage_filed_answer` wiring, three-way preview branch, success-message advisory).
- PR #682 (commits `8fb9634` -> squash-merged as `093abe6`): `docs(adr): ADR-0015 per-type default sensitivity + openkos.yaml reference (#669 WU5)` — ADR-0015 + `docs/cli.md` reference section.

CI green on each PR's head SHA (per verify-report and the launch prompt's
final-state facts). Native SDD attempt ledger settled `complete` for all
four work units (WU1-WU5, WU1+WU2 landed together in slice 1).

## Verify Verdict (authoritative final state)

Per `verify-report.md` (Engram observation #2788, persisted 2026-08-14
02:18:58, at commit `8fb9634`/branch `feat/669-s4-adr-docs` before PR #682
merged): **PASS — ready for archive.**

- 0 CRITICAL / 0 WARNING / 0 SUGGESTION.
- Scoped suite: `tests/unit/model/test_okf_sensitivity.py
  tests/unit/test_config.py tests/unit/cli/test_ingest.py
  tests/unit/cli/test_set_sensitivity.py tests/unit/cli/test_query_save.py`
  — **655/655 passed**, 0 failed, 0 skipped.
- Twin-rule birth-site guard mutation-confirmed independently at both call
  sites: reverting the ingest site (`main.py:3269-3271`) to
  `sensitivity=stamp_sensitivity` broke 5 tests; reverting the `--save` site
  (`main.py:13093-13097`) to `sensitivity=cited_high_water_mark` broke 4
  tests; both restored byte-identical afterward, full scoped suite green
  again (655/655) each time. No single shared resolver-level test would
  have caught either mutation in isolation — the guard is genuine per-site
  coverage, not one shared test disguised as two.
- All 25 `tasks.md` checkboxes ticked `[x]` and spot-checked truthful
  against the diff (no overclaiming found).
- Two recorded design deviations, both assessed **acceptable** with no
  spec/security impact: (1) `_DerivedPlan.sensitivity` extra field not
  named in design's File Changes table; (2) `_stage_filed_answer` gained a
  nullable `cfg: Config | None = None` instead of a required parameter, to
  avoid touching 15+ pre-existing unrelated call sites.
- Per the launch prompt's final-state facts (authoritative over this
  snapshot per Final-State Authority): all four slices are merged to `main`
  (PR #679, #680, #681, #682), CI green on each — this supersedes the
  verify-report's snapshot description of slice 4 as "committed locally,
  not yet merged."

No Native Review Receipt Gate `reviewGate` was found in structured status
for this candidate; archive proceeds under ordinary repository policy.

## Requirement-Merge Map

### `type-sensitivity-defaults` — NEW capability

The delta spec (`openspec/changes/per-type-default-sensitivity/specs/type-sensitivity-defaults/spec.md`)
already had a full `# Type Sensitivity Defaults Specification` / `## Purpose`
header mirroring `sensitivity-config`'s structure, so it was copied
mechanically (`cp`, verified `diff -r` empty) to
`openspec/specs/type-sensitivity-defaults/spec.md` as a brand-new main spec.
9 requirements, all with complete scenario coverage:

1. Per-Type Offset Config Shape
2. Eager Validation At Config Load
3. Floor-Relative Raise, Never A Bypass Of Source Inheritance
4. Both `build_concept` Birth Seams Consult The Type Default
5. Write-Time Advisory Names Type-Defaulted Objects And The Retrieval Consequence
6. One-Line Extension To Add A Type
7. No Backfill Of Existing On-Disk Concepts
8. Sources Are Never Type-Defaulted
9. `set-sensitivity` Downgrade Remains Unaffected

### `ingestion` — MODIFIED requirement

`### Requirement: Derived Object Provenance and Sensitivity Inheritance`
name-matched exactly at `openspec/specs/ingestion/spec.md:918` (no heading
drift). Replaced with the delta's version (`(Previously: ...)` note
dropped). **Reconciliation note**: the pre-existing main spec had a second
scenario ("Inheritance tracks the Source's resolved value, not the config
default") that the delta's replacement supersedes rather than preserves.
This was verified as an intentional, correct supersession, not an
oversight: that scenario asserted derived-object sensitivity is
*unconditionally* equal to the Source's resolved value, which is no longer
universally true once a per-type offset is configured (e.g. `Person` is now
born strictly above the Source's value when the shipped `{"Person": 1}`
mapping applies) — the delta's two replacement scenarios express the same
underlying guarantee correctly qualified ("no per-type sensitivity offset
configured" / "a per-type sensitivity offset configured"). Dropping the
unconditional scenario is therefore correct, not a coverage loss.

### `query-command` — MODIFIED requirement

`### Requirement: Sensitivity Is The High-Water-Mark Of Cited Concepts`
name-matched exactly at `openspec/specs/query-command/spec.md:502` (no
heading drift). The requirement's prose was extended with the delta's new
sentences (type-default floor + advisory) and the delta's new scenario ("A
type-defaulted filed answer is saved above the cited high-water-mark") was
inserted. **Reconciliation note**: unlike the `ingestion` merge, this
requirement's delta only listed 2 of the main spec's 6 scenarios. The 4
scenarios the delta did not mention ("Unreadable or unparseable citation
folds to confidential", "Zero citations refuse to file", "A raised
high-water mark is disclosed in the preview", "A fold landing on the
default stays undisclosed") were **preserved**, not dropped, because each
remains independently true and unaffected by the type-default seam (e.g.
the unreadable-citation fold already lands on `confidential`, the ceiling,
so a subsequent type-default raise is a no-op via clamping). This is the
correct call under the OpenSpec convention "PRESERVE requirements not
mentioned in the delta," applied at scenario granularity for an additive
(not fully-replacing) delta.

### `participant-coverage-probe` — MODIFIED requirement

`### Requirement: No Per-Type Sensitivity Behavior in Probe Scope`
name-matched exactly at
`openspec/specs/participant-coverage-probe/spec.md:82` (no heading drift,
last requirement in the file). Replaced with the delta's version:
requirement prose narrowed from "no per-type sensitivity behavior anywhere"
to "no per-type sensitivity behavior in the probe's own measurement path,"
and one new scenario added ("A workspace-wide per-type default does not put
the probe out of compliance"). The delta's scenario set was a strict
superset of the original (1 matched + 1 new), so this was a clean full
replacement with no reconciliation needed.

## Post-Merge Verification

```
$ grep -n "### Requirement: Derived Object Provenance" openspec/specs/ingestion/spec.md
918:### Requirement: Derived Object Provenance and Sensitivity Inheritance

$ grep -n "### Requirement: Sensitivity Is The High-Water-Mark" openspec/specs/query-command/spec.md
502:### Requirement: Sensitivity Is The High-Water-Mark Of Cited Concepts

$ grep -n "### Requirement: No Per-Type Sensitivity Behavior" openspec/specs/participant-coverage-probe/spec.md
82:### Requirement: No Per-Type Sensitivity Behavior in Probe Scope

$ grep -c "^### Requirement:" openspec/specs/type-sensitivity-defaults/spec.md
9
```

All merged/created requirement headings are greppable in their main spec.
No stray `(Previously:` marker exists inside any of the three edited
requirement blocks (verified with a scoped `sed` + `grep -c` over each
block's exact line range; unrelated `(Previously:` markers do exist
elsewhere in `ingestion/spec.md` and `query-command/spec.md`, belonging to
other, untouched requirements from earlier changes).

## Deviations Recorded

Carried forward from `verify-report.md` (both assessed acceptable, no
spec/security impact — see Verify Verdict section above):
1. `_DerivedPlan.sensitivity` extra field, not explicitly named in design's
   File Changes table.
2. `_stage_filed_answer` gained a nullable `cfg: Config | None = None`
   parameter instead of a required one.

Archive-time reconciliation (both documented above, neither is an error):
3. `ingestion` merge dropped one pre-existing scenario that the delta's
   more precisely-qualified replacement scenarios correctly supersede.
4. `query-command` merge preserved 4 pre-existing scenarios the delta did
   not restate, since they remain independently true.

## Follow-Ups

None known. `verify-report.md` recorded zero CRITICAL/WARNING/SUGGESTION
findings, and no open issue was filed against this change's scope.

## Artifact Store Traceability

**Engram observations read (all required artifacts):**
- #2781: `sdd/per-type-default-sensitivity/proposal`
- #2782: `sdd/per-type-default-sensitivity/spec (ingestion+query-command gap closed)`
- #2783: `sdd/per-type-default-sensitivity/design`
- #2784: `sdd/per-type-default-sensitivity/tasks`
- #2785: `sdd/per-type-default-sensitivity/apply-progress`
- #2788: `sdd/per-type-default-sensitivity/verify-report`

**Archive report**: `sdd/per-type-default-sensitivity/archive-report` (this
file, persisted to Engram at archive time).

**Filesystem artifacts:**
- `openspec/specs/type-sensitivity-defaults/spec.md` (created, new capability)
- `openspec/specs/ingestion/spec.md` (merged, existing + MODIFIED requirement)
- `openspec/specs/query-command/spec.md` (merged, existing + MODIFIED requirement)
- `openspec/specs/participant-coverage-probe/spec.md` (merged, existing + MODIFIED requirement)
- `openspec/changes/archive/2026-08-14-per-type-default-sensitivity/` (folder with all change artifacts)

## Archive Folder Contents Verified

```
openspec/changes/archive/2026-08-14-per-type-default-sensitivity/
├── exploration.md
├── proposal.md
├── design.md
├── tasks.md (all 25 tasks marked complete)
├── apply-progress.md
├── verify-report.md (0 CRITICAL, 0 WARNING, 0 SUGGESTION)
├── archive-report.md (this file)
└── specs/
    ├── ingestion/spec.md (delta)
    ├── participant-coverage-probe/spec.md (delta)
    ├── query-command/spec.md (delta)
    └── type-sensitivity-defaults/spec.md (new capability)
```

All artifacts accounted for (7 files + `specs/` subtree with all 4 domain
deltas). `git mv` moved the tracked files; the untracked `verify-report.md`
moved along with the directory rename and was confirmed present in the
destination by directory listing. `openspec/changes/per-type-default-sensitivity/`
no longer exists (verified via `ls openspec/changes/`).

**Mechanical copy/move readback (mandatory evidence):**
- New-capability copy (`type-sensitivity-defaults/spec.md`): `diff -r` source
  vs. destination — empty, exit code 0.
- Archive folder move: `diff -r` pre-move recursive snapshot vs. archived
  folder — empty, exit code 0.
- Structural parity vs. `2026-08-14-first-class-participants`: identical file
  shape (proposal/design/exploration/tasks/apply-progress/verify-report/specs
  subtree), only difference is this change's own `archive-report.md`
  (additive, expected) and the differing spec domain names (expected, per
  each change's own scope).

## SDD Cycle Complete

The change has been fully planned, implemented, verified, and archived.

**Date closed**: 2026-08-14
**Archival mode**: hybrid (Engram + filesystem)
**Next phase**: none — SDD cycle closed
