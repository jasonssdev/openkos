# Verify Report: curate-decisions-as-data

**Verdict: PASS-WITH-CORRECTIONS.** The findings are 0 CRITICAL, 3 WARNING and 4 SUGGESTION, and verify found no code defect. The shipped code delivers the Purpose and every Success Criterion in the proposal. The spec text overstated or omitted behaviour in eight places (C1–C8). Each was corrected in the spec text before the archive closed, and none required a code change.

This verify ran on branch `feat/curate-decisions-s5`, cut from `main` at 7e9c5848 after every implementation slice had merged. Python was 3.14.6.

## Checks run

- **Backing test files:** `uv run pytest -q` over the eight files `test_curate_decisions.py`, `test_curate_decisions_boundary.py`, `test_curate_queue.py`, `test_curate_characterization.py`, `test_curate_consent_matrix.py`, `test_curate_consent_table.py`, `test_set_volatility_output_golden.py` and `test_layering.py`. 521 passed, exit 0.
- **Static checks:** `ruff check .`, `ruff format --check .` and `mypy .` are clean.
- **Goldens:** the characterization and consent-matrix goldens were not edited after they were recorded. Only #1370 and #1371 touch them.

## Goal check

Every Success Criterion in `proposal.md` is met:

- The goldens and the consent matrix pass unchanged.
- The module imports none of `typer`, `rich`, `openkos.cli` or `openkos.vcs`, and the layering test passes, including the `isatty` AST assertion.
- The list returns all six kinds without a model call and marks stale rows.
- A superseded token returns the typed Stale result.
- The adapter's commit helpers each have exactly one definition, in `cli/main.py`.
- The follow-up "Judging as an application service" is named in `docs/roadmap.md` and in ADR-0053.

## Requirement-by-requirement (two-way)

| # | Requirement | Verdict |
|---|---|---|
| 1 | Plain Data, No Interaction | OK |
| 2 | List Returns Six Kinds | OK (`pq.KINDS` order, no model) |
| 3 | Each Kind Carries Its Own Action Set | Spec omitted the `supersedes` case (C1) |
| 4 | Only Judged-SAME Identity Items Are Mergeable | Spec incomplete (C2) |
| 5 | Staleness Is Marked, Never Hidden | Spec imprecise about `None` (C3) |
| 6 | Apply Is A Two-Phase Preview And Commit | Spec overstated (C4, C5) |
| 7 | Merge Preview Discloses Reconciliation | OK |
| 8 | Commit Re-Checks | OK; tier refusal omitted (C6b) |
| 9 | Adapter Owns Rendering | OK |
| 10 | Keep-Distinct Records A Durable Ruling | OK; fail-open omitted (C6a) |
| 11 | Structure/Metadata Resolve Rows | OK |
| 12 | Consent And Bulk Eligibility Are Data | Spec overstated (C7, C8) |
| 13 | No Judgment | OK (only the injected `reconcile` port calls a model) |
| 14 | Payloads Are Owner-Local | OK (boundary guard; a missing `api/` is tolerated) |
| 15 | Confidence Is Not A Trust Signal | OK |
| 16 | Behavior Of The Command Is Unchanged | OK |

The four MODIFIED requirements in the `curate-command` delta match the code. `apply_decision` is registered as a non-resolving core.

## Corrections applied (spec text only)

- **C1 (WARNING).** A `relation_type` row whose suggested type is `supersedes` carries no action and resolves through `openkos relate`.
- **C2 (SUGGESTION).** Merge is offered only when three conditions hold: a persisted SAME judgment, exactly two members, and a passed cross-type check.
  - When merge is withheld, the item names the reason.
  - Keep-distinct and skip are always offered.
- **C3 (SUGGESTION).** Action-less kinds report `fresh` as `None`, never `True`.
- **C4 (WARNING).** The token is re-validated in two halves:
  - The content half is compared against a re-preview from current bytes, before the commit section, and fails as `content_changed`.
  - The row half is re-read inside the section and fails as `row_resolved`, `row_superseded` or `row_inputs_changed`.
  - A change between the two halves is drift.
  - A row-level Stale can arrive after a planned reconciliation has already run. That costs one model call but writes nothing, which is why it is not classed as a defect. A pre-section freshness check could remove the call later.
- **C5 (WARNING).** The volatility token covers the proposed tier plus the bytes of `openkos.yaml`, so any configuration change makes it stale. Inside the section, the recorded tier is also compared with the effective one.
- **C6 (SUGGESTION).**
  - (a) A keep-distinct record failure is fail-open: it returns `NotApplied("record_failed")` and notifies the observer.
  - (b) A tier commit the adapter refuses is raised as the typed refusal.
- **C7 (WARNING).** Accept-recommended writes through the identity merge action. `--auto-merge` keeps `apply_auto_merges`, does not use that action, and commits once per run (ADR-0049).
- **C8 (SUGGESTION).** The table decides which kinds may be accepted in bulk. The per-item `asymmetric` flag in `item_consent` decides which items of an accepted kind still ask, and the shipped command sets it only for asymmetric relations. The consent inputs also name the unattended identity pass, which the adapter derives from `--auto-merge`.

## Observation beyond the spec

On a TTY, an accepted Structure run offers `a` (accept every remaining item of this asymmetric type) and `r` (reverse). This is a per-type operator choice in the CLI. It does not conflict with "asymmetric is never bulk-eligible" for `--accept`.

## pending-work spec

`openspec/specs/pending-work/spec.md` needs no change. The list only reads the queue, and no schema, status or resolution rule changed.

## Limitations

The mutation proofs were not re-run during verify; they are recorded per slice in `tasks.md`. Requirements checked by reading the source carry file and line evidence in the Engram copy of this report.
