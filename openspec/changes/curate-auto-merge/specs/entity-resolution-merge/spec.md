# Delta for Entity Resolution Merge

## ADDED Requirements

### Requirement: The Stacked-Body Guardrail Refuses An Automatic Merge

The stacked-body domination guardrail (the absorbed share of the merged body
at or above the `STACKED_SHARE_GUARDRAIL` threshold) MUST be evaluated for
every merge applied by the automatic Identity pass (`identity-auto-merge`),
not only for the batch path. Where a human confirms each merge the guardrail
stays advisory; on the automatic path, where no human confirms, a plan that
exceeds the guardrail MUST be REFUSED: the group MUST NOT be merged, nothing
MUST be written for it, and the refusal MUST be reported with its reason. The
refusal MUST NOT stop the pass from considering later groups.

#### Scenario: A plan over the guardrail is refused on the automatic path

- GIVEN an in-class `same` pair whose stacked share meets the guardrail
- WHEN the automatic pass prepares its merge
- THEN no file is written for the pair and the report names the guardrail
  and the share

#### Scenario: A refusal does not stop later groups

- GIVEN two eligible pairs, the first refused by the guardrail
- WHEN the automatic pass runs
- THEN the second pair is still considered and merged

#### Scenario: A plan under the guardrail proceeds

- GIVEN an in-class `same` pair whose stacked share is below the guardrail
- WHEN the automatic pass prepares its merge
- THEN the merge proceeds

### Requirement: Merges Sharing One Commit Stay Individually Reversible

When several merges are committed together (`workspace-autocommit`: One
Commit For All Automatic Merges Of A Run), each merge MUST still append its
own `log.md` bullet, and `unmerge` MUST locate and remove a merge's bullet by
that bullet's exact text, with no reliance on the commit boundary. Undoing
one merge MUST NOT disturb another's ledger entry, bullet, or files.

#### Scenario: One of several same-commit merges unmerges alone

- GIVEN two merges committed in one commit
- WHEN `unmerge` of the first survivor is confirmed
- THEN the first pair is restored byte-for-byte, its bullet is removed, and
  the second merge's ledger entry, bullet and files are unchanged

#### Scenario: Unmerge works after a later commit

- GIVEN a run commit holding a merge, followed by an unrelated commit that
  appended to `log.md`
- WHEN `unmerge` of that survivor is confirmed
- THEN the merge's bullet is removed by its text and the later line is kept
