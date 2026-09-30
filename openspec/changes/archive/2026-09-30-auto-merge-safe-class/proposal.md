# Proposal: auto-merge-safe-class — apply the measured-safe class of Identity merges without per-item consent, reviewable and reversible after the fact

Refs #1054. ADR-0034.

## Intent

No merge ever happens unattended today. `curate --accept identity` is
refused (`openspec/specs/curate-command/spec.md`, "Per-Stage Accept-All Is
Opt-In And Never Covers Identity"), `review: false` never reaches Identity,
and `adjudicate --apply-same` requires typing the previewed count. A user
who ingests a large folder keeps its duplicates until they sit down and
adjudicate them, which brings back the maintenance cost the project exists
to remove (`docs/philosophy.md`).

This change lets `curate` apply one narrow class of Identity merges
automatically, reviewable and reversible AFTER the fact, **but only if a
measurement committed before the feature says a safe class exists.** The
owner made that the precondition on the issue (2026-09-29): the confidence
threshold "is measured in a harness with pre-registered bars before the mode
ships." So the change is two changes in sequence:

1. **Measure** (Phases 1-2): a model-backed harness with a model-free
   `--self-test`, a committed labelled fixture with hard negatives, and a
   decision rule written into `design.md` before any live number exists.
2. **Build** (Phases 3-6), **only if the rule passes**: the opt-in mode.

The prior evidence points at FAIL. `evals/adjudication/README.md` records
that the adjudicator's stated confidence "carries no information": every
verdict in the no-clause arms, right and wrong, was stated at 0.95, and the
wild-shape recurrence pair (`grupo-calidad-datos`) is judged `same` 13 of
15 runs "stably, at 0.95 confidence". A threshold cannot separate what the
signal does not separate. That is exactly why the harness comes first: if
the rule fails, Phase 2 records the verdict on #1054 and the change stops.
A stopped change is a successful outcome of this plan, not a failure of it.

## Binding decisions (owner comment on #1054, 2026-09-29)

| # | Decision | Where it lands |
|---|---|---|
| B1 | "post-hoc review is acceptable" | ADR-0034; curate-command MODIFIED |
| B2 | "Opt-in: the mode is off by default." | `curate --auto-merge`, default off |
| B3 | "only 2-member `SAME` groups of the same OKF type, with no `cross_type_concern` flag" | eligibility predicate, harness population |
| B4 | threshold "measured in a harness with pre-registered bars before the mode ships"; fixture "must include hard negatives, such as two different meetings a week apart" | Phases 1-2, `design.md` §Decision rule |
| B5 | "No LLM rewrite of merged bodies (`--no-reconcile` semantics)" | auto merges never plan reconciliation |
| B6 | "At most one automatic merge per survivor per run" | per-run participation cap |
| B7 | "all merges of one run land in one git commit, with one `log.md` entry listing survivor, absorbed and confidence" | workspace-autocommit MODIFIED, run summary bullet |
| B8 | "Outside that subset, #702's rule stands" | every ineligible pair keeps per-item consent |

**Discrepancy flagged, resolved in favour of the comment.** The issue body
lists "no `cross_source_same_pair` ... flag" among the eligibility rules and
its summary calls cross-source pairs out of scope; the owner's comment,
which is later and binding, lists the eligible class exhaustively and does
not exclude cross-source pairs. The comment's own named hard negative (two
different meetings a week apart) is a cross-source pair by construction, so
excluding cross-source pairs would leave the harness with zero exposure on
the negative the owner named. The harness therefore measures the comment's
population as primary and reports the cross-source-excluded subset as a
secondary population (`design.md` D3), so the owner can tighten eligibility
from data without a re-run.

## Scope

### In Scope

- **Phase 1:** `evals/auto_merge/` — harness, committed labelled fixture,
  model-free `--self-test` (discovered by `evals/run_self_tests.py`), an
  offline `--decide` that applies the pre-registered rule to stored runs,
  and the results file format.
- **Phase 2:** the calibration and confirmation runs, the verdict, and the
  STOP gate.
- **Phases 3-6, conditional on PASS:** `curate --auto-merge`; the measured
  threshold and measured-model set as code constants; the adjudicating model
  recorded on persisted verdicts; one commit and one `**Auto-merge**`
  `log.md` bullet per run; the disclosure; docs.
- ADR-0034 and its `docs/adr/README.md` row (in the planning commit).

### Out of Scope

- Any change to the adjudication prompt, rubric, or withdrawal markers
  (#838: a rubric edit re-spends every stored verdict).
- A user-configurable threshold. The threshold is a measured constant; a
  knob would let a workspace step outside the measurement.
- N>2 groups, cross-type pairs, and anything the rule does not admit — they
  keep today's per-item consent.
- A batch-undo verb. Reversal stays per-merge `unmerge` (LIFO per survivor).
- `merge`, `adjudicate --apply`, `adjudicate --apply-same`: unchanged.
- A config key for the switch (`design.md` D6: flag only, additive later).

## Impact on principles

- **Human curates, engine maintains.** Revised for this class only, as
  ADR-0034 records: "reviewable" means announced, committed, logged and
  reversible, not confirmed before. Outside the class nothing changes.
- **Local-first, sensitivity:** no new egress; the confidential gate and the
  local-backend exemption apply exactly as in Identity today.
- **Reconstructible, provenance:** unchanged; each auto merge writes the
  same ledger entry a manual `merge --no-reconcile` writes.
- **OKF:** no format change.

## Rollback plan

- Before Phase 3 nothing ships to users: the harness lives under `evals/`.
- After Phase 3 the mode is off by default; omitting the flag restores
  today's behavior exactly. Each run is one commit (`git revert <sha>`
  undoes it wholesale) and each merge is one `unmerge` away.
- Code rollback is a revert of the Phase 3-5 PRs; no stored state needs
  migrating back, because the new `adjudications.model` column is nullable
  and ignored by older builds' explicit column lists.

## Risks

| Risk | Mitigation |
|---|---|
| A wrong auto merge sits in the bundle until noticed | off by default; one commit + log bullet + stderr disclosure per run; `unmerge` per pair |
| The rule passes on a fixture easier than real bundles | fixture carries the wild-shape class that already fails; secondary report on the real-corpus shape; limits written into the results file |
| Threshold chosen on the data it is judged on | calibration and confirmation arms are separate invocations; `t*` is fixed from calibration only |
| Measured on one model, used on another | measured-model set is a constant; any other adjudication model refuses the mode |
| A stored verdict from an unmeasured model is served | verdicts gain a `model` column; rows without one are ineligible |
