# Delta for Privacy Purge

## ADDED Requirements

### Requirement: Purge Withdraws The Deprecated-Status Export Of Resurrected Targets

Because `purge` resolves its purge set through `forget`'s Phase A, it MUST
compute the same deprecated-status export withdrawals `forget` does
(`forget-command` "Resurrection Interaction Disclosure"): for every concept
outside the purge set whose superseded-ness changes because the purge
set's edges disappear, the export projection is evaluated over the
post-purge bundle, and the preview names each resulting status change
before the typed confirmation. After the history rewrite, `purge` MUST
write each WITHDRAW or DROP-MARKER outcome as part of its live-tree
cleanup, and MUST stage every rewritten concept document in the SAME
post-rewrite auto-commit as `bundle/index.md` and `bundle/log.md`. A
failure writing an export rewrite MUST be reported as a non-fatal WARNING
naming the concept and `openkos repair`, and MUST NOT change `purge`'s
exit code, for the same reason the commit step is non-fatal: the
irreversible rewrite has already landed, and export drift never changes
retrieval (`deprecated-status-export`).

#### Scenario: A purged superseder's target is withdrawn in the same commit

- GIVEN purge-set member M holds the only `supersedes` edge to Y outside
  the set, and Y carries a valid export
- WHEN `openkos purge M` completes
- THEN Y carries `status: stable` and no `status_derived_from` key, and
  the post-rewrite commit contains `bundle/Y.md` beside `bundle/index.md`
  and `bundle/log.md`

#### Scenario: A failed withdrawal does not fail the purge

- GIVEN a purge whose history rewrite succeeded and whose write of Y's
  withdrawn export raises `OSError`
- WHEN the live-tree cleanup runs
- THEN a non-fatal WARNING names Y and `openkos repair`, and `purge` exits
  with its normal success code
