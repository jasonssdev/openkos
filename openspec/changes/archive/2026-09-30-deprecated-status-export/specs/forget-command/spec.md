# Delta for Forget Command

## MODIFIED Requirements

### Requirement: Resurrection Interaction Disclosure

For EVERY concept in the purge set, when it carries an OUTBOUND `supersedes`
edge to a concept OUTSIDE the purge set, that target concept is no longer
effective-deprecated once the cascade completes and re-enters retrieval.
`openkos forget` MUST disclose this in the Phase A preview, naming the
target and the purge-set member whose edge caused it, before the confirm
gate.

Forget MUST keep the deprecated-status export (`deprecated-status-export`)
consistent with this resurrection: for every concept OUTSIDE the purge set
whose superseded-ness changes because the purge set's edges disappear,
Phase A MUST evaluate the export projection over the post-forget bundle
(every non-purged document, with the purge set's relations removed), and
Phase B MUST write each resulting WITHDRAW or DROP-MARKER outcome. A target
still superseded by a surviving concept keeps its export. When the walk is
incomplete, the withdrawal is skipped and reported per "Withdrawal Requires
A Complete Edge Walk". The preview line for each resurrected target MUST
also state its status change (`status → stable`) when one will be written,
or that its own human-authored `status` is kept.
(Previously: forget disclosed the resurrection only; no path wrote a deprecated-status export, so there was nothing to withdraw.)

#### Scenario: A cascade member's supersedes edge discloses resurrection
- GIVEN a purge-set member M has an outbound `supersedes` edge to concept Y
  outside the set
- WHEN `openkos forget <source-id> --scope source` runs
- THEN the preview names Y and states that Y re-enters retrieval once the
  cascade completes

#### Scenario: No out-of-set supersedes edge, no disclosure
- GIVEN no purge-set member has an outbound `supersedes` edge to a concept
  outside the set
- WHEN `openkos forget <source-id> --scope source` runs
- THEN the preview contains no resurrection-disclosure line

#### Scenario: A resurrected target's export is withdrawn
- GIVEN purge-set member M holds the only `supersedes` edge to Y outside
  the set, and Y carries `status: deprecated` and `status_derived_from:
  supersedes`
- WHEN `openkos forget M` is confirmed
- THEN Y carries `status: stable` and no `status_derived_from` key, and the
  preview named Y's status change before the confirm gate

#### Scenario: A target superseded by a surviving concept keeps its export
- GIVEN Y is superseded by both purge-set member M and surviving concept N,
  and carries a valid export
- WHEN `openkos forget M` is confirmed
- THEN Y's bytes are unchanged

#### Scenario: A human-authored deprecation survives the forget
- GIVEN M supersedes Y, and Y carries `status: deprecated` with no
  `status_derived_from` key
- WHEN `openkos forget M` is confirmed
- THEN Y's bytes are unchanged


### Requirement: Catalog-Before-File Write Ordering

Phase B MUST rewrite `index.md` (removing every purge-set member's entry)
and `log.md` (appending all N tombstone lines) BEFORE deleting any concept
file, so the catalog never references a file that does not exist. The
deprecated-status export rewrites of resurrected targets (see "Resurrection
Interaction Disclosure") MUST run after the catalog and BEFORE any deletion,
in sorted order by concept-id, each under the same drift guard as the rest
of Phase B. The N concept-file deletions (`fsio.remove_file`) MUST run
LAST, in deterministic sorted order by concept-id. A failure between the
export rewrites and the deletions leaves only export drift — which never
changes retrieval (`deprecated-status-export`) and which `openkos lint`
reports and `openkos repair` resolves. Phase B is NOT required to be transactional; a
failure partway through the N unlinks MAY leave a partial, git-recoverable
result with the catalog already consistent-forward. On such a partial failure
of a cascade (N > 1), the error MUST report how many of the N members were
removed before failing and how many remain, and point to recovery (git or
`openkos lint`), so the operator is not left to reconstruct partial state.
(Previously: Phase B wrote only the catalog and the deletions; there were no export rewrites to order.)

#### Scenario: Catalog updated before any cascade file deletion
- GIVEN a confirmed `--scope source` forget of 3 concepts
- WHEN Phase B writes execute
- THEN `index.md` and `log.md` are fully updated before any of the 3 files
  is deleted

#### Scenario: Partial cascade deletion is git-recoverable
- GIVEN a Phase B run interrupted after 2 of 3 unlinks
- WHEN the bundle is inspected afterward
- THEN `index.md`/`log.md` reflect all 3 removals, one file may remain as a
  benign orphan, the error states how many of the 3 were removed and how many
  remain, and recovery is via `git status`/`git checkout`

#### Scenario: Export withdrawals land after the catalog and before any deletion
- GIVEN a confirmed forget whose purge-set member supersedes Y outside the
  set, and Y carries a valid export
- WHEN Phase B writes execute
- THEN `index.md` and `log.md` are updated first, Y's export is withdrawn
  next, and the purge-set files are deleted last
