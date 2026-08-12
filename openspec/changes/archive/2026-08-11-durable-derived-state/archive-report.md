# Archive Report: durable-derived-state

**Change**: durable-derived-state
**Issues**: #550, #554 — both CLOSED (plus #573, #574, opened and closed during the change)
**Status**: CLOSED — 11 of 13 requirements landed; 2 unlanded pending their own issues
**Completed**: 2026-08-11
**Archived**: 2026-08-12
**PR**: #579 (merge commit `d0a14fa` on `main`)
**Mode**: hybrid (OpenSpec + Engram)

## Final State Authority

This report describes the change AT CLOSE. `apply-progress` and `verify-report` are
historical snapshots; where they disagree with this document, this document is correct.

The most important disagreement is recorded in "Specs synced to main" below.
**No slice merged its delta specs. All thirteen requirements were merged into
`openspec/specs/` at archive time**, and each was re-verified against shipped code
before landing rather than trusted because the change was marked done. Four had
drifted from the code the slices actually shipped: two were landed with corrected
wording, and two remain unlanded pending their own issues.

### Merged

PR #579 merged `tracker/durable-derived-state` into `main` as `d0a14fa`
(44 files changed, 5379 insertions, 139 deletions). Four review-sized slices merged
into the tracker first:

| PR | Slice | Merge commit | Changed lines |
|---|---|---|---|
| #575 | 1a-i — ledger sidecar store, two-phase write, crash recovery | `de1fb6a` | 1484 |
| #576 | 1a-ii — read wiring and privacy-sweep coverage | `1039423` | 2898 |
| #577 | 1b — reindex composition, doctor checks, the repair verb | `5bab299` | 2003 |
| #578 | remediation — `merge` refuses on a doctor-flagged ledger, with `--force` | `d66005d` | 481 |

Two follow-up PRs landed after the tracker: #580 folded the remaining slices in, and
#589 (`e034793`) recorded two undocumented gaps found in review, closing #573 and #574
by documenting them in the delta specs rather than by claiming a fix.

Issues #550 and #554 closed. 39/39 tasks ticked.

### Review and delivery authority

Receipt-driven development was off for this work, so delivery is
**`disabled/unmanaged`** — there is no review receipt and its absence is expected.

Verification verdict: PASS ("Archive is unblocked"), recorded in `verify-report.md`
after a prior FAIL whose CRITICAL (`merge` had no doctor-flagged-ledger refusal) was
closed by PR #578.

Final suite at archive: **4340 passed, 1 skipped**, unchanged from the `main` baseline —
this archive touches no source code. `ruff check`, `ruff format --check`, and
`mypy .` (198 source files) all clean.

## What shipped

ADR-0002 put every `merged_from` entry directly in the survivor's own OKF frontmatter,
and each entry carries the full pre-merge snapshot set — `absorbed_snapshot`,
`survivor_before`, `index_before`, `log_before`, plus recorded rewrites. Because
`survivor_before` retains every prior entry, the survivor's own file grew
geometrically with each merge. A concept became mostly its own history.

The ledger now lives in a per-survivor sidecar under `bundle/.state/ledger/`
(ADR-0013, which supersedes ADR-0002's storage clause only — the schema and the
round-trip contract are unchanged):

- `bundle/ledger.py` owns the sidecar store, the `.ledger.okf.pending` two-phase
  write, and `recover`'s hash-bound truth table.
- The survivor's own frontmatter no longer gains a `merged_from` key at all
  (`okf.build_merged_document` pops it).
- Every markdown walk excludes the store for free: sidecars are `*.ledger.okf`, and
  `rglob("*.md")` cannot match them. That is what makes `forget`'s inbound-reference
  scan ignore ledger bytes without a special case.
- `forget` and `purge` gained an explicit sweep of the store in their existing
  Phase B / `git filter-repo` pass — no second write, no second rewrite invocation.
- `doctor` gained two informational, read-only checks: torn writes (Check A,
  mechanically exact) and post-merge mutation (Check B, nested-prefix equality with
  three documented false negatives).
- A new `repair` verb migrates a legacy frontmatter-embedded ledger into the sidecar,
  with two refusal gates and no override flag of any kind.
- `reindex` now embeds composed text — title, description, tags, body, the same four
  fields `fts.py` indexes — instead of the document's raw bytes. That closes #554
  independently of the relocation: a bounded composition cannot be crowded out by
  anything, relocated or not.

## Specs synced to main

**The slices did not merge their delta specs. All of this was done at archive time**,
and each requirement was re-verified against shipped code before being landed rather
than trusted because the change was marked done.

Eleven of the thirteen delta requirements landed — nine verbatim (modulo the two
disclosed deviations below), two with corrected wording:

| Capability | Action | Requirement |
|---|---|---|
| `contradiction-detection` | ADDED | Merged-Body Candidate Source Relocates Without Changing Verdict Semantics |
| `doctor-command` | ADDED | Merge-Ledger Torn-Write Check |
| `doctor-command` | ADDED (corrected) | Merge-Ledger Integrity Check |
| `entity-resolution-merge` | ADDED (corrected) | `merge` Refuses On A Doctor-Flagged Ledger, With `--force` |
| `entity-resolution-merge` | MODIFIED | Reversibility Ledger (`merged_from`) |
| `entity-resolution-merge` | MODIFIED | Unmerge Achieves Round-Trip Parity |
| `forget-command` | ADDED | Inbound-Reference Scan Excludes Ledger Storage |
| `privacy-purge` | ADDED | Whole-History Expunge Covers The Ledger Sidecar Store |
| `reindex-command` | ADDED | Composed Embed Text Replaces Raw-Bytes Embedding |
| `sensitivity-aware-llm` | ADDED | Per-Entry Merged-Content Gate, Never Per-Survivor |
| `sensitivity-aware-llm` | MODIFIED | Walk-Incompleteness Observability |

Two deviations from verbatim, both deliberate and neither silent:

1. **Merge-Ledger Torn-Write Check** landed without one clause: "which resolves the
   pending marker on the next `merge`/`unmerge` that touches the affected survivor"
   is false of the verb it modifies. `openkos repair`'s Gate 1 refuses outright on
   any pending marker and redirects to `merge`/`unmerge`, so the repair verb does not
   resolve the marker — the next merge/unmerge does, via `bundle_ledger.recover`.
   Every MUST in the requirement is satisfied by shipped code. Worth a follow-up in
   its own right: doctor's remediation names a verb that will refuse.
2. **Whole-History Expunge Covers The Ledger Sidecar Store** landed verbatim
   *including* its own "UNIMPLEMENTED, and UNREACHABLE" block for Scenario 2. That
   block is not an oversight — it is the agreed resolution of #573, and dropping the
   requirement would have removed the disclosure along with the true half.

Also dropped, as change-internal scaffolding that means nothing in a living spec:
the "(Slice 1a)" aside in the reindex requirement and the "(Slice 1b)" aside in the
merge-refusal requirement.

### The two corrected requirements

Both failed re-verification for the identical reason, and both were corrected the
same way: the delta made the reset-and-replay remedy unconditional, and the shipped
code makes it conditional on the workspace having a reset point at all.

`_autocommit` is best-effort — it silently no-ops with no repository, no configured
git identity, or any `GitError`/`OSError` — so a workspace that never committed has
no reset point, and printing `git reset --hard <first-merge>~1` there would name a
command that cannot work. Both `doctor` check 13 (`cli/main.py:12396`) and
`_reject_flagged_ledger_write` (`cli/main.py:582`) therefore gate that half on
`vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root)`, printing
"no git reset point is available in this workspace … there is no remedy that
restores reversibility for the affected merge(s)" otherwise. This was a genuine
improvement found in review and tasked as 2.4/2.5 — the requirements it falsified
were simply never updated to match.

What changed in the landed text, in both requirements: the MUST was split into the
part that is unconditional (always name the repair verb, always state that pre-fix
reversibility is not guaranteed — both branches of the shipped code do this) and the
part that is conditional (the reset-and-replay path, named only when a reset point
exists, with the explicit no-remedy message otherwise). Each requirement gained one
scenario for the no-reset-point branch, and the pre-existing corrupted-ledger
scenario gained the git precondition it had been missing. Every other clause is
verbatim from the delta, including the skip-entries rule, the three false negatives
added for #574, and `--force`'s orthogonality to the confirm gate.

Test coverage is uneven and worth knowing: doctor's no-reset-point branch is pinned
by `test_doctor_nesting_violation_check_reports_no_reset_point_without_git_identity`
(`tests/unit/cli/test_doctor.py:1350`) and `repair`'s by
`test_repair_warns_no_reset_point_available_before_writing`
(`tests/unit/cli/test_repair.py:224`). **`merge`'s equivalent branch has no test.**
It ships — the code path was read directly — but nothing pins it.

## The four drifted requirements

Re-verification against shipped code found four requirements the code does not
satisfy as written. The first two were a wording problem and were landed corrected;
the last two describe behavior that genuinely differs from what shipped, so they stay
out of `openspec/specs/` and have issues of their own. A requirement bent to match
the code stops being able to catch anything, so the distinction matters: 1 and 2 were
the spec failing to keep up with a deliberate improvement, 3 and 4 are the code not
doing what the spec says.

### 1. `doctor-command` — Merge-Ledger Integrity Check — LANDED, CORRECTED

The delta said a `[FAIL]` line's remediation MUST name BOTH the repair verb AND
`git reset --hard <first-merge>~1` followed by `openkos reindex`. Shipped code
(`cli/main.py:12396`, doctor check 13) gates the second remedy on
`vcs_git.repo_root(root) is not None and vcs_git.has_reset_point(root)`; without a
reset point it prints "no git reset point is available in this workspace … there is
no remedy that restores reversibility" instead. That branch is deliberate (tasks
2.4/2.5, a gap found in review) and is pinned by
`test_doctor_nesting_violation_check_reports_no_reset_point_without_git_identity` —
the repo's own suite proved the requirement false as written. Landed with the
remediation clause split into its unconditional and conditional halves, plus one new
scenario for the no-reset-point branch. Everything else verbatim, including the
skip-entries rule and the three false negatives added for #574 — so #574's
documentation now does reach `openspec/specs/`.

### 2. `entity-resolution-merge` — `merge` Refuses On A Doctor-Flagged Ledger, With `--force` — LANDED, CORRECTED

Same defect, same root. `_reject_flagged_ledger_write` (`cli/main.py:582`) applies the
identical `has_reset_point` gate, so "The refusal message MUST print BOTH remediation
paths" did not hold unconditionally. Landed with the same correction and the same
extra scenario. The requirement's `--force` scenario — bypasses the integrity refusal
but never the confirm gate — ships exactly and is covered by three tests. Note that
`merge`'s own no-reset-point branch, unlike `doctor`'s and `repair`'s, has no test.

### 3. `entity-resolution-merge` — Repair Verb Refuses On Any Sign Of Cross-Survivor Pollution Risk — NOT LANDED

A larger divergence. The bundle-wide `>= 2` entries gate ships
(`bundle_ledger.bundle_wide_max_entries`), and so does "no override flag of any kind"
— `repair()` takes no `typer.Option` parameters at all. But the requirement's closing
sentence, "The refusal message MUST state that the only path forward is `git reset
--hard <first-merge>~1` followed by `openkos reindex`, and that reversibility of
merges made before this fix is not guaranteed", is satisfied by neither half: the
shipped Gate 2 message says "Run `openkos doctor` to inspect; if it reports a
corrupted ledger, its own remediation is the way forward, not this verb" and never
mentions reset-and-replay or reversibility.

### 4. `forget-command` — Deletion Sweep Includes Ledger Storage — NOT LANDED

The requirement demands that any entry whose `absorbed_snapshot`, `survivor_before`,
`index_before`, `log_before`, `relation_rewrites`, or `provenance_rewrites` snapshot
contains a purge-set member's body be redacted or removed.
`_sweep_ledger_sidecars_for_ids` matches on `entry.absorbed_id` equality only, and
never inspects snapshot content. Two leaks follow from that:

- A member's whole-file body can sit in a *third* survivor's entry as a
  `relation_rewrites` or `provenance_rewrites` snapshot, under an `absorbed_id` that
  is not the member.
- On a survivor with two or more entries, entry *k*'s `survivor_before` already embeds
  the bodies absorbed by entries 1..*k-1* — this is stated by
  `contradiction._own_body_before_merge`'s own docstring. Dropping entry 1 does not
  remove that body from entry 2.

Both of the requirement's own scenarios pass on a single-entry ledger, which is why
this was not caught earlier. The two ADDED scenarios are honest; the enumeration in
the requirement's normative text is not.

## Known follow-up

- **#562 — `unmerge --to <id>`** stays out of scope, and the purge disclosure block
  now landed in `openspec/specs/privacy-purge/spec.md` names it as the change that
  MUST close the cross-survivor sidecar gap, because it is what makes the absorbed-id
  precondition reachable.
- **Items 3 and 4 above remain unlanded**, each tracked by its own issue. Neither is
  fixable by narrowing the text.
  - **#602** — item 4. A privacy gap in code: `forget` leaves a purged concept's body
    in a ledger sidecar in two reachable shapes. The requirement lands once the sweep
    covers them.
  - **#603** — item 3, together with the `doctor` remediation defect below. Both are
    the same failure: the ledger-recovery verbs describe each other's responsibilities
    inaccurately, so an operator following the messages goes in a circle.
- **`doctor`'s torn-write remediation names a verb that refuses.** Check 12 prints
  `openkos repair`, but `repair`'s Gate 1 (`cli/main.py:12501`) refuses outright on
  any pending marker and redirects to `merge`/`unmerge`, which is what actually
  triggers `bundle_ledger.recover`. The remediation should name the resolving path
  directly. Tracked in **#603**.
- **`merge`'s no-reset-point refusal branch is untested.** `doctor`'s and `repair`'s
  equivalents are pinned; `_reject_flagged_ledger_write`'s is not. The scenario landed
  in `openspec/specs/entity-resolution-merge/spec.md` on a read of the shipped code,
  so it is specified and unpinned — a test should follow.
- `design.md` still does not state `scan_nesting_violations`'s skip rule in prose
  beyond what #589 added — carried forward from the verification SUGGESTION.

## Process notes worth carrying forward

**Delta specs do not merge themselves.** Four slices shipped code and closed two
issues while ALL THIRTEEN requirements sat unmerged in the change folder; the
verification pass read the delta specs and judged them honest without noticing they
were not the contract yet. A PASS on the delta is not a PASS on `openspec/specs/`.

**Verifying at merge time is where the bugs are.** All four drifted requirements were
found by grepping for the function, flag, or path each one names — not by reading the
requirement titles. Two of them contradict a shipped test.

**A refinement made in code must be pushed back into the spec in the same slice.** The
`has_reset_point` gate was a genuine improvement found in review, tasked (2.4/2.5),
implemented, tested, and recorded in `apply-progress.md` — and the requirement it
falsified was never touched. That is the whole of defects 1 and 2.

**Documenting a gap is a legitimate close.** #573 and #574 were both closed by writing
the gap down rather than by shipping a fix, and that is why the purge requirement
could be landed at all: the text tells the truth about itself.

## Rollback

`git revert` of `d0a14fa`. The ledger sidecar store is canonical bundle state, so a
revert makes any `bundle/.state/ledger/**` file already on disk unreadable to the
merge machinery, which would look for `merged_from` in survivor frontmatter again.
Merges made after the relocation would become effectively un-unmergeable until the
change is re-applied. Nothing is destroyed — the sidecars stay on disk — but this is
not a clean revert, and the `repair` verb migrates only in the forward direction.

## SDD cycle

Explore → Propose → Spec → Design → Tasks → Apply ×4 → Verify → Archive. Complete.
