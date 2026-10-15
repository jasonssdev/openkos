# Verify Report: curate-decisions-as-data

**Source**: Engram observation #3549 (`sdd/curate-decisions-as-data/verify-report`, created 2026-10-14 19:39:57).
**Provenance note**: #3549 is a condensed summary. Its text states "Full report returned to orchestrator"; the full report was not persisted to Engram, and no full copy exists in this change folder. This file reproduces the persisted summary only. It does not add findings.

## Verdict

PASS-WITH-CORRECTIONS: 0 CRITICAL, 3 WARNING, 4 SUGGESTION. No code defect.

Verification at verify time: 521 targeted tests pass; `ruff check`, `ruff format --check` and `mypy .` clean.

## Confirmed Findings

- (a) `--auto-merge` uses `application_auto_merge.apply_auto_merges` (`src/openkos/cli/curate.py:1265`), not the identity merge action. `accept-recommended` does use `commit_merge_decision` (`curate.py:1516-1530`). The spec sentence overstates the shared path.
- (b) `item_consent` exempts via an asymmetric flag under every non-NEVER row (`curate_decisions.py:1473-1477`). Production sets it only for asymmetric relations (`curate.py:2261`); Metadata never sets it. The spec is silent on this.

## Other Corrections (from the summary)

1. `supersedes` relation rows carry no actions (`curate_decisions.py:1123`).
2. Merge is withheld also for `judged_different`, `cross_type` and `more_than_two_members` (`curate_decisions.py:1085-1103`).
3. Token content: the half compared before the section (`619-628`); the row half is inside (`637`, `667`). Row-level Stale can follow a reconcile model call.
4. Volatility token = proposed tier plus `openkos.yaml` bytes (`495-505`); effective tier is indirect, plus `_stale_inputs`.
5. `fresh` is `bool | None`.
6. Keep-distinct record failure returns `NotApplied('record_failed')` (`268`); tier config refusal is raised as `Refused` (`933`).
7. The pure consent check takes an `unattended_identity` bool, not `--auto-merge`.

## Other Notes

- S5.3: `pending-work` spec needs no change.

## Status of Corrections C1-C8

The exact old-to-new sentences for C1-C8 were not available in the persisted record. They were not applied. See the archive report for the disposition.
