# Verify Report: stable-python-api

**Phase**: S7.5, the requirement check run during the S7 docs slice, before archive. Recorded at archive time from the S7.5 result in the launch scope.
**Scope**: the 15 requirements of `specs/stable-python-api/spec.md` and the 5 ADDED requirements of `specs/pending-work/spec.md`. Each is checked against shipped code, not against the task list.
**Source of this record**: the S7.5 result as reported in the archive launch scope, cross-checked against the Engram apply notes (#3583, #3588) and the code paths they cite. This file is a summary. It is not a per-requirement evidence dump.

## Verdict

- CRITICAL: none.
- Requirements met: all except one, which is partially met (see below).
- Partially met: "Changes Within A Major Are Additive; A Breaking Change Bumps The Version". The bump check compares against the snapshot at `git HEAD`, so it cannot fail in CI once the snapshot is committed. Fixed by spec wording in the archive phase. The requirement still holds as a review obligation and as a local check. Follow-up 1 tracks CI enforcement.
- Caveat, not a failure: the volatility "ambiguous" branch (`Two regimes that disagree are skipped and counted`) is reachable only through a test stub. Today's store keeps one row per `type_name`, so the count is always 0. Fixed by spec wording in the archive phase. The behaviour is kept.
- Issue #1391 acceptance: met.

## Requirement Table

| Requirement | Capability | Status | Note |
|-------------|-----------|--------|------|
| The Public Package Declares Exactly Its Public Names | stable-python-api | Met | |
| `API_VERSION` Is An Integer Independent Of The Package Version | stable-python-api | Met | |
| Changes Within A Major Are Additive; A Breaking Change Bumps The Version | stable-python-api | Partially met | Bump check is local-only. Spec reworded in archive. |
| A Golden Snapshot Pins The Public Surface | stable-python-api | Met | Snapshot equality enforced in CI. Bump half local-only (see above). |
| An Import Guard Bars Interactive And Disclosing Layers From The API | stable-python-api | Met | |
| A Workspace Handle Resolves The Workspace Once | stable-python-api | Met | |
| A Missing Workspace Is A Typed Not-Found Failure | stable-python-api | Met | |
| `OpenKosError` Is The Single Catchable Root | stable-python-api | Met | |
| The Surface Is Owner-Local And Applies No Disclosure Gate | stable-python-api | Met | |
| Workspace Status And The Pending Report Are Readable | stable-python-api | Met | |
| Open Decisions Can Be Listed, Previewed And Applied | stable-python-api | Met | |
| Judging Is Quoted, Then Run Against That Quote | stable-python-api | Met | |
| Judging Resolves Its Default Backend Inside The API Without The CLI | stable-python-api | Met | |
| Refreshing The Decision Queue Is Exposed As A Model-Free Operation | stable-python-api | Met | |
| The API Is Importable And Usable Without The CLI | stable-python-api | Met | |
| The Decision Queue Can Be Refreshed Without A Model | pending-work | Met | |
| Refresh Holds The Workspace Lock Only Inside Commit Sections | pending-work | Met | |
| Refresh Amends Unchanged Inputs And Does Not Inflate The Queue Statistics | pending-work | Met | |
| Refresh Retires A Row Only After A Complete Pass For Its Kind | pending-work | Met | |
| The Identity Stage Is Shared Between The Daemon And Refresh | pending-work | Met | |

The volatility ambiguous branch sits under "The Decision Queue Can Be Refreshed Without A Model" (pending-work). That requirement is marked Met. Its ambiguous branch is reachable only through a stub, as the caveat above states.

## Notes

- Operational gaps the spec does not require are listed as follow-ups in `archive-report.md`: the confidential-commit notice, the insecure-key warning, and `Stale("row_resolved")` for a never-existing key.
- The archive phase changed spec wording only. It changed no code and no snapshot.
