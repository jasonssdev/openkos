# Archive Report: partial-read-verb-reports

**Change Name**: partial-read-verb-reports
**Archived**: 2026-09-21
**Artifact Store**: openspec
**Archive Path**: `openspec/changes/archive/2026-09-21-partial-read-verb-reports/`

---

## Executive Summary

`partial-read-verb-reports` (Refs #1002, items A and C) fixed `doctor` and
`lint` compute-then-render fragility: a single raise inside either service
used to destroy the entire report. Both verbs now degrade the raising check
to a structured `not-run` outcome, render every other check, and exit `2`
when the report is incomplete. The change shipped as two chained PRs — #1006
(`doctor`, commit `4ccd64d`) and #1007 (`lint`, commit `9dc91a9`) — both
already merged to `main` before this archive step ran. All 37 tasks in
`tasks.md` are checked; no delta spec or task drift was found. This step
merges both delta specs into their living specs, moves the change folder to
the archive, and flips ADR-0022 from `Proposed` to `Accepted`.

---

## Final-State Authority (per Skill §Final-State Authority)

Source ranking applied (most authoritative first):

1. **Persisted tasks artifact** (`tasks.md`) — 37/37 tasks checked, 0
   unchecked.
2. **Explicit final-state facts in the orchestrator's launch prompt** —
   "Both slices are MERGED to main already" (`4ccd64d`, `9dc91a9`) and "Both
   verified PASS."
3. **`verify-report.md`** — does not exist for this change on this branch or
   anywhere in git history (`git log --all -- "*partial-read-verb-reports*verify*"`
   returns nothing). There is no lower-ranked snapshot to reconcile against;
   the orchestrator's launch-prompt statement is the only account of
   verification and is recorded as such, not independently re-derived by
   this archive step.

No contradiction between sources was found. `tasks.md`'s "Explicitly out of
scope" section independently corroborates two items the launch prompt did
not mention: the `doctor-command` spec's pre-existing "ten `CheckResult`s"
banner-count drift (tested count is fifteen) is explicitly left for the
reviewer and was **not** touched by this change or by this archive step; and
the ADR file/README row were already present on the branch pre-archive,
with tasks.md noting status flip to `Accepted` belongs to archive, not to an
implementation task — consistent with what this step performed.

---

## Task Completion Gate

**Status**: PASS

Inspected `openspec/changes/partial-read-verb-reports/tasks.md` (persisted
artifact, now at the archived path):

```
$ grep -c '\- \[x\]' tasks.md
37
$ grep -c '\- \[ \]' tasks.md
0
```

All 37 tasks marked `[x]`. No stale unchecked tasks remain.

---

## Spec Sync — Heading-by-Heading Merge Mapping

Both delta specs were composed into their living specs with the native
`gentle-ai sdd-archive-compose` command (mandatory native composition, never
a model-driven Read/Edit merge). Both invocations exited `0`.

```
$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/doctor-command/spec.md" \
    --delta "openspec/changes/partial-read-verb-reports/specs/doctor-command/spec.md" \
    --output "openspec/specs/doctor-command/spec.md.compose-tmp" \
  && mv "openspec/specs/doctor-command/spec.md.compose-tmp" "openspec/specs/doctor-command/spec.md"
DOCTOR COMPOSE EXIT 0
DOCTOR MOVE OK

$ gentle-ai sdd-archive-compose \
    --canonical "openspec/specs/lint/spec.md" \
    --delta "openspec/changes/partial-read-verb-reports/specs/lint/spec.md" \
    --output "openspec/specs/lint/spec.md.compose-tmp" \
  && mv "openspec/specs/lint/spec.md.compose-tmp" "openspec/specs/lint/spec.md"
LINT COMPOSE EXIT 0
LINT MOVE OK
```

### `doctor-command` — 4 MODIFIED, 1 ADDED

| delta heading | base spec line (pre-merge) | outcome |
| --- | --- | --- |
| Doctor Runs And Prints All Applicable Checks | 12 | MODIFIED, replaced in full |
| Exit Code Reflects Critical Failures Only | 97 | MODIFIED, replaced in full |
| Workspace Vector Index Presence Check | 279 | MODIFIED, replaced in full |
| Workspace FTS Index Presence Check | 313 | MODIFIED, replaced in full |
| Not-Run Is Structured Data On The Returned CheckResult | — (no base heading) | ADDED, inserted |

All 4 MODIFIED headings name-matched an existing base heading exactly, per
the parent-verified mapping in the launch prompt. Evidence — new text
present, old text gone:

**1. "Doctor Runs And Prints All Applicable Checks"** — new `not-run`
vocabulary present in the merged base spec:
```
$ sed -n '12,31p' openspec/specs/doctor-command/spec.md
...MUST NOT stop or skip remaining checks after any single check fails.

A check whose own read raises an unexpected exception — specifically
`okf.survey_bundle` (the bundle-readable check), `bundle_ledger.scan_torn_writes`
and `bundle_ledger.scan_nesting_violations` (the two merge-ledger checks),
and the injected `reset_point_available()` thunk failing inside the
merge-ledger-integrity check's violation branch — MUST be reported as
`not-run`, a fourth structured outcome carrying the raised reason...
```
Old text confirmed gone — the pre-merge base spec (line 12) described only
`[PASS]`/`[FAIL]`/`[SKIP]` with no `not-run` outcome and no mention of
`okf.survey_bundle` or the merge-ledger raise sites; that phrasing does not
appear anywhere in the merged file outside this requirement's own new text.

**2. "Exit Code Reflects Critical Failures Only"** — new exit-`2` vocabulary
present:
```
$ sed -n '181,187p' openspec/specs/doctor-command/spec.md
`doctor` MUST exit `0` when every applicable check completes (no
`not-run`) and no CRITICAL check ... reports `fail`; `1` when at least one
CRITICAL check reports `fail`... and `2` when at least one check reports
`not-run` and no CRITICAL check reports `fail`...
```
Old binary exit rule confirmed gone as *governing* text — the only
occurrence of the old predicate is inside this same requirement's own
`(Previously: exit was binary — 0/1 — driven solely by
any(status == "fail" and critical))` clause (line 193-194), which is
deliberate historical framing carried by the delta itself (confirmed
present verbatim in the pre-merge delta file), not leftover old-spec
content:
```
$ grep -n "Previously:" openspec/changes/partial-read-verb-reports/specs/doctor-command/spec.md
47:(Previously: these four sites let their exception propagate uncaught,...
126:(Previously: exit was binary — `0`/`1` — driven solely by
184:(Previously: this requirement described only `pass`/`fail`/`skip`, and an...
244:(Previously: this requirement described only `pass`/`fail`/`skip`, and an...
```

**3. "Workspace Vector Index Presence Check"** — new `not-run`/dependency
text present (`sed -n '/^### Requirement: Workspace Vector Index Presence
Check/,/^### Requirement:/p'`): "This check decides between `skip` and
`fail` using the bundle-readable check's own reading of the bundle. When
that reading did not happen, this check MUST report `not-run`..." Old text
("described only `pass`/`fail`/`skip`") confirmed gone as governing text —
present only inside this requirement's own `(Previously: ...)` framing
clause, same pattern as above.

**4. "Workspace FTS Index Presence Check"** — same shape, new text present:
"Mirroring the Workspace Vector Index Presence Check exactly, this check
MUST report `not-run` when the bundle-readable check reported `not-run`...".
Old text confirmed gone under the same `(Previously: ...)` pattern.

**5. "Not-Run Is Structured Data On The Returned CheckResult"** (ADDED) —
confirmed present at the end of the merged spec: `run_diagnostics`'s
returned `CheckResult` MUST carry `not-run` as a `status` peer of
`pass`/`fail`/`skip` on the returned dataclass.

Post-merge requirement count: 18 (17 original + 1 ADDED); all 13
pre-existing requirements not touched by the delta (e.g. "Failed Checks
Print Actionable Remediation", "Doctor Works Outside An Initialized
Workspace", "Merge-Ledger Integrity Check") are present unchanged and in
their original relative order.

### `lint` — 4 MODIFIED, 1 ADDED

| delta heading | base spec line (pre-merge) | outcome |
| --- | --- | --- |
| Non-NFC Names Scan | 482 | MODIFIED, replaced in full |
| Read-Only and Human-Readable Only | 209 | MODIFIED, replaced in full |
| Non-Gating Exit Contract | 189 | MODIFIED, replaced in full |
| Not-Run Is Structured Data On The Returned LintReport | — (no base heading) | ADDED, inserted |

This mapping was derived the same way as the doctor mapping (not handed by
the orchestrator): `grep -n "^### Requirement" openspec/changes/partial-read-verb-reports/specs/lint/spec.md`
against the pre-merge `openspec/specs/lint/spec.md`. All 3 MODIFIED headings
name-matched an existing base heading exactly; no unmatched delta heading
was found.

Evidence — new text present, old text gone:

**1. "Non-NFC Names Scan"** — new `not-run` degradation text present: "WHEN
this scan's own directory walk raises an unreadable-directory error
(`OSError`), the check MUST degrade to `not-run` — carrying the raised
reason — rather than raising uncaught...". Old text (silent on the raise
case) confirmed gone as governing text; the only "Previously" framing is
this requirement's own delta-authored clause.

**2. "Read-Only and Human-Readable Only"** — new text present: "Two further
late bundle walks fall under this same read-only guarantee and share the
Non-NFC scan's not-run failure-mode contract: `check_state_dir_contains_no_markdown`
and `check_dot_dir_markdown`...". This is new content entirely (the base
requirement previously said nothing about these two walks' failure mode).

**3. "Non-Gating Exit Contract"** — new exit-`2` vocabulary present:
"`openkos lint` MUST exit `0` on any complete run... It MUST exit `2` when
at least one late bundle walk... reports `not-run`...". Old text ("the only
nonzero exit was 'workspace cannot be read'") confirmed gone as governing
text, present only inside this requirement's own `(Previously: ...)`
clause.

**4. "Not-Run Is Structured Data On The Returned LintReport"** (ADDED) —
confirmed present: `build_lint_report`'s returned `LintReport` MUST carry a
`not_run` field naming which late-walk check(s) could not run.

Post-merge requirement count: 13 (12 original + 1 ADDED); all 9 pre-existing
requirements not touched by the delta (e.g. "Workspace Presence Check",
"Stale-Stamp Scan", "Orphan-Page Scan", "Unbacked-Provenance-Claim Scan")
are present unchanged and in their original relative order.

---

## Archive Move

### Directory Listings

**Archived folder** (`openspec/changes/archive/2026-09-21-partial-read-verb-reports/`):
```
design.md
exploration.md
proposal.md
specs/doctor-command/spec.md
specs/lint/spec.md
tasks.md
```
(plus `archive-report.md`, written by this step, additive)

**Original path** (`openspec/changes/partial-read-verb-reports/`):
```
$ ls -la openspec/changes/partial-read-verb-reports
ls: openspec/changes/partial-read-verb-reports: No such file or directory
```
Confirmed absent.

### Sibling Diff (against `2026-09-09-okf-codec-seam`)

```
$ comm -23 <sibling file list> <this archive's file list>
archive-report.md   <- sibling's own report; this change writes its own (additive, present)
specs/README.md     <- sibling-specific: that change's own "no delta spec written" decision
                        record (its spec phase decided against writing a delta spec).
                        This change DID write real delta specs for both domains, so it
                        has no equivalent decision record to carry — not a missing artifact.
verify-report.md    <- does not exist for this change (see Final-State Authority above);
                        recorded as absent, not fabricated.
```

No artifact this change actually produced is missing from the archived
folder. `exploration.md`, `proposal.md`, `design.md`, `tasks.md`, and both
domain delta specs under `specs/` are all present.

### Move Mechanics and Readback

Mechanical `git mv` of `openspec/changes/partial-read-verb-reports/` to
`openspec/changes/archive/2026-09-21-partial-read-verb-reports/`, run as one
shell transaction with a pre-move `cp -R` snapshot for the readback.

```
$ git mv "openspec/changes/partial-read-verb-reports" "openspec/changes/archive/2026-09-21-partial-read-verb-reports"
git mv OK
source gone: confirmed
$ diff -r "$snapshot_root/source" "openspec/changes/archive/2026-09-21-partial-read-verb-reports"
DIFF EMPTY - PASS
```

Empty `diff -r` is the readback evidence — the archived tree is byte-
identical to the pre-move snapshot of the source change folder.

**Note on git status vs. disk content.** `git status --short` shows
`openspec/changes/archive/.../specs/doctor-command/spec.md` as `RM`
(renamed + further modified relative to the index) rather than a clean `R`.
This is *not* a move-time truncation: the file this session started with
already carried an uncommitted working-tree edit relative to `HEAD`
(`9dc91a9`) — the initial git status snapshot in this session's context
already reported `M openspec/changes/partial-read-verb-reports/specs/doctor-command/spec.md`
before any action was taken. `git show HEAD:.../doctor-command/spec.md`
has only 2 MODIFIED requirements + 1 ADDED (192 lines); the working tree
(and now the archived file, and the `--delta` input actually fed to
`sdd-archive-compose`) has all 4 MODIFIED + 1 ADDED (304 lines), matching
`design.md`, `proposal.md`, and the merge evidence above. The working-tree
version is the correct, current state of the change; `git mv` carried its
actual current bytes (confirmed by the empty `diff -r` against the pre-move
snapshot, which was also taken from the working tree). This commit stages
that pre-existing uncommitted content alongside the archive move.

---

## Verification

**Status**: No `verify-report.md` artifact exists for this change (see
Final-State Authority). The orchestrator's launch prompt states both slices
were "verified PASS" but supplies no locator or report content to read back;
that claim is recorded here as the launch prompt's own statement, not
independently re-derived by this archive step. `tasks.md` shows no
unchecked verification-related tasks, and this archive step's own
independent gates (below) re-confirm the shipped tree is green.

---

## This Archive Step's Own Gates (independently run)

```
$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
311 files already formatted

$ uv run mypy .
Success: no issues found in 311 source files

$ uv run pytest
6324 passed, 2 skipped in 335.01s (0:05:35)
[exited with code 0]
```

All four gates green on the post-merge, post-archive tree (docs-only diff:
two spec files, two ADR files, and the archive move; no `src/` or `tests/`
edits were made by this step).

---

## ADR-0022 Status Flip

Per `openspec/config.yaml`'s `rules.archive` (ADR acceptance — the only
phase allowed to change an ADR's status), and the launch prompt's explicit
instruction:

- `docs/adr/0022-incomplete-read-verbs-report-incompleteness.md`: frontmatter
  `status: Proposed` → `status: Accepted`; body `**Status:** Proposed` →
  `**Status:** Accepted`. Only the status lines changed — Context, Decision,
  Consequences, and Alternatives were left untouched (append-only once
  accepted).
- `docs/adr/README.md`: row for `0022` updated from `Proposed` to
  `Accepted`.

No new ADR was created by this step, and none of ADR-0022's decision content
was rewritten.

---

## Carried-Forward Gaps (not failures, recorded honestly)

1. **`doctor-command` spec's pre-existing "ten `CheckResult`s" banner-count
   drift** (`Doctor Prints A Leading Version Banner`, untouched by this
   change) still reads "ten" while the tested/actual count is fifteen. The
   proposal's own "Rider" section and `tasks.md`'s "Explicitly out of scope"
   section both flag this as pre-existing, unrelated to items A/C, and
   deliberately left for the reviewer — not adopted or fixed by this change
   or by this archive step.
2. **No `verify-report.md`** exists for this change (see Final-State
   Authority and Verification sections above) — recorded as absent rather
   than fabricated.
3. **`openkos-context.md`** is an untracked file present in the working
   directory at session start (visible in `git status`), unrelated to this
   change; this archive step did not touch it and it is not included in the
   commit.

---

## Final Checklist (per Skill §Step 4)

- [x] Main specs updated correctly (`sdd-archive-compose`, exit 0, both
      domains; heading-by-heading evidence above)
- [x] Change folder moved to archive
      (`openspec/changes/archive/2026-09-21-partial-read-verb-reports/`)
- [x] Archive preserves all artifacts that existed (proposal, exploration,
      design, tasks, both delta specs); no artifact this change produced is
      missing
- [x] Archived `tasks.md` retains its original bytes; 37/37 tasks complete,
      0 unfinished
- [x] Active changes directory no longer has this change
      (`openspec/changes/partial-read-verb-reports/` absent, confirmed)
- [x] Verbatim `diff -r` readback output included and is empty
      (byte-identity confirmed for the move)
- [x] ADR-0022 flipped to `Accepted` in both frontmatter and body, and in
      the `docs/adr/README.md` index row

---

## Observation IDs (Engram lineage)

*Not applicable for filesystem artifacts*: this change uses `openspec`
artifact store mode. This archive-report.md itself is additionally saved to
Engram at topic key `sdd/partial-read-verb-reports/archive-report` per the
skill's persistence contract, recorded as a full-content save (not a
read-back observation ID from an earlier phase, since no earlier phase
persisted to Engram for this change).

---

## Artifacts Archived

**Change**: partial-read-verb-reports
**Archive Path**: `openspec/changes/archive/2026-09-21-partial-read-verb-reports/`
**Date Archived**: 2026-09-21

**Artifacts**:
- `proposal.md` — Intent (Refs #1002 items A, C), scope, decisions, ADR
  verdict, risks, rollback plan
- `design.md` — Design decisions for the shared not-run primitive across
  `doctor` and `lint`
- `exploration.md` — Pre-proposal exploration notes
- `tasks.md` — 37 implementation tasks (all complete), slice-boundary
  rationale, explicit out-of-scope notes
- `specs/doctor-command/spec.md` — Delta spec: 4 MODIFIED + 1 ADDED
  requirement, merged into `openspec/specs/doctor-command/spec.md`
- `specs/lint/spec.md` — Delta spec: 3 MODIFIED + 1 ADDED requirement,
  merged into `openspec/specs/lint/spec.md`

---

## Cycle Summary

**SDD Cycle Status**: COMPLETE

1. Proposed — scope A/C only, ADR verdict recorded
2. Specified — delta specs for `doctor-command` and `lint`
3. Designed — shared not-run primitive
4. Tasked — 37 tasks across two chained slices (all complete)
5. Applied — two merged PRs (#1006 `4ccd64d`, #1007 `9dc91a9`)
6. Verified — no persisted verify-report; orchestrator's launch prompt
   states both slices verified PASS (recorded per Final-State Authority,
   not independently re-derived); this archive step's own gate re-run is
   green
7. Archived — folder moved, delta specs merged, ADR-0022 accepted, all
   readbacks empty/green

**Ready for the next change.**

---

## Key Learnings

1. A change folder's working-tree content can legitimately differ from the last commit on the branch; `git mv` moves the current working-tree bytes, and the resulting index `RM` status is bookkeeping, not truncation — confirm with `diff -r` against a pre-move snapshot, not with `git status` alone.
2. `gentle-ai sdd-archive-compose` applies MODIFIED requirements by exact heading match and preserves every unrelated requirement, but a delta requirement's own `(Previously: ...)` framing clause is authored content from the delta, not leftover old-spec text — check the pre-merge delta file before flagging it as an incomplete merge.
3. A sibling archive's `specs/README.md` was that specific change's own "no delta spec written" decision record, not a universal archive artifact; a file-list diff against a sibling needs interpretation, not blind gap-filling.
4. No `verify-report.md` existing on disk or in git history for a change with two merged, reportedly-verified PRs is a legitimate final state under hybrid/openspec archive — record the orchestrator's explicit final-state claim with its rank in the hierarchy rather than fabricating or silently omitting the gap.
