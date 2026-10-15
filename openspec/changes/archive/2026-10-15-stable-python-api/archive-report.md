# Archive Report: stable-python-api

**Archived**: 2026-10-15 (S7, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-15-stable-python-api/`
**Store mode**: hybrid. Filesystem merge and move, plus this report saved to Engram as `sdd/stable-python-api/archive-report`.

## Artifacts Read

- Files read: `proposal.md`, `exploration.md`, `design.md`, `tasks.md`, the two delta specs under `specs/` (`stable-python-api/spec.md` and `pending-work/spec.md`).
- Engram observations read in full: `sdd/stable-python-api/apply-progress` #3583, and its addendum `sdd/stable-python-api/apply-progress-s6b-followup` #3588.
- Engram observations available and not read, because their locators are file paths in hybrid mode: explore #3573, proposal #3575, spec #3577, design #3578, tasks mirror #3579, planning decision #3574.
- No `verify-report` existed in Engram or on disk before this archive. `verify-report.md` was written in this phase from the S7.5 result in the launch scope (see below).
- `apply-progress.md` and `verify-report.md` were written in this phase as condensed records. They are intermediate snapshots and are marked as such.

## Final State At Close

- **Implementation**: all eight slices are merged to `main`. The planning PR is #1393. Slices S1 to S6b are #1394, #1395, #1397, #1396, #1398, #1399 and #1400. The S7 docs and archive PR is this change (branch `docs/python-api-archive`).
- **ADR-0060**: Accepted in commit `a4c8364d` on the S7 branch, in all three places (frontmatter, the ADR index row, and the status test). It was Proposed in S5.
- **Public surface**: `openkos.api` with `API_VERSION` 1. `__all__` has 72 names (per #3588). The golden snapshot is `tests/unit/api/api_surface.json`.
- **Tasks**: the launch scope and `tasks.md` agree on the remaining S7 items. S7.3 (spec merge), S7.6 (follow-ups recorded here) and S7.7 (archive) were closed in this phase. See "Tasks" below for the counts.
- **Verification**: per the S7.5 result in the launch scope, all requirements are met except the version-bump requirement, which is partially met for the reason in "Spec Corrections" below. Full status is in `verify-report.md`.

## Spec Corrections Applied Before Merge

Both were applied to the delta specs in the change folder before the merge, so the merged main spec describes shipped behaviour.

| Item | Location | Amendment |
|------|----------|-----------|
| Version bump | `specs/stable-python-api/spec.md`, "Changes Within A Major Are Additive; A Breaking Change Bumps The Version" | The bump check compares the live surface with the snapshot at `git HEAD` (`tests/unit/api/test_api_surface.py`). It fails for a breaking change only while the regenerated snapshot is uncommitted. Once committed, CI cannot fail on the bump half. CI enforces snapshot equality only. Scenario reworded to say so. Verified in the test body (`_previous_committed`). |
| Version bump, golden | `specs/stable-python-api/spec.md`, "A Golden Snapshot Pins The Public Surface" and its last scenario | Same limit, stated where the snapshot requirement says a breaking diff must need a bump. |
| Volatility ambiguous | `specs/pending-work/spec.md`, "The Decision Queue Can Be Refreshed Without A Model" and its "Two regimes that disagree" scenario | The behaviour is kept (skip and count). Added: with today's store (`state/volatility_suggestions.py`, `type_name` primary key, one row per type) the ambiguous count is always 0, because both regimes can only resolve to the same stored row. It is a guard for a future multi-row store. The requirement is not deleted. |

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| pending-work | Updated | `gentle-ai sdd-archive-compose` exit 0, output to `spec.md.compose-tmp`, then `mv`. 5 ADDED appended. Living spec now has 20 requirements. The pre-merge file was diffed against the post-merge file. It shows 0 removed lines, and one added hunk at lines 518 to 722. File mode restored to 0644. |
| stable-python-api | Created | `openspec/specs/stable-python-api/spec.md`, copied mechanically from the amended delta with `cp` and `mv` (the SKILL block, no model Read or Write). `diff -r` exit 0. 15 requirements. The delta already has Purpose and Requirements, with no ADDED or MODIFIED headers. File mode 0644. |

### Heading Name-Match

Every `### Requirement:` heading in each delta was grepped as an exact line in its main spec. Each match is exactly once.

| Delta | Requirement heading | Main-spec count |
|-------|---------------------|-----------------|
| pending-work (ADDED) | The Decision Queue Can Be Refreshed Without A Model | 1 |
| pending-work (ADDED) | Refresh Holds The Workspace Lock Only Inside Commit Sections | 1 |
| pending-work (ADDED) | Refresh Amends Unchanged Inputs And Does Not Inflate The Queue Statistics | 1 |
| pending-work (ADDED) | Refresh Retires A Row Only After A Complete Pass For Its Kind | 1 |
| pending-work (ADDED) | The Identity Stage Is Shared Between The Daemon And Refresh | 1 |
| stable-python-api (new spec) | The Public Package Declares Exactly Its Public Names | 1 |
| stable-python-api | `API_VERSION` Is An Integer Independent Of The Package Version | 1 |
| stable-python-api | Changes Within A Major Are Additive; A Breaking Change Bumps The Version | 1 |
| stable-python-api | A Golden Snapshot Pins The Public Surface | 1 |
| stable-python-api | An Import Guard Bars Interactive And Disclosing Layers From The API | 1 |
| stable-python-api | A Workspace Handle Resolves The Workspace Once | 1 |
| stable-python-api | A Missing Workspace Is A Typed Not-Found Failure | 1 |
| stable-python-api | `OpenKosError` Is The Single Catchable Root | 1 |
| stable-python-api | The Surface Is Owner-Local And Applies No Disclosure Gate | 1 |
| stable-python-api | Workspace Status And The Pending Report Are Readable | 1 |
| stable-python-api | Open Decisions Can Be Listed, Previewed And Applied | 1 |
| stable-python-api | Judging Is Quoted, Then Run Against That Quote | 1 |
| stable-python-api | Judging Resolves Its Default Backend Inside The API Without The CLI | 1 |
| stable-python-api | Refreshing The Decision Queue Is Exposed As A Model-Free Operation | 1 |
| stable-python-api | The API Is Importable And Usable Without The CLI | 1 |

Totals: 5 ADDED in pending-work, now 20 requirements in that spec. 15 in stable-python-api. No duplicate headings. No ADDED, MODIFIED, REMOVED or RENAMED headers remain in either main spec.

## Mechanical Readback

- **pending-work merge**: `diff` of the pre-merge file against the post-merge file. Removed lines: 0. Added hunk: `517a518,722`.
- **stable-python-api create**: `diff -r` of the delta against the new main spec. Output was empty, exit 0.
- **Archive move**: see "Archive Move Readback" below. It is filled from the command output.

## ADR

- ADR-0060 is Accepted in commit `a4c8364d`. The status test was run and passed (see "Checks").
- No other ADR was edited. ADRs are append-only.

## Architecture And Roadmap Docs (S7.1, S7.2)

- `docs/architecture.md` gained `api/`, `errors.py` and `vcs/autocommit.py` in the tree. Its future-async text was rewritten to a local HTTP API built on `openkos.api`.
- `docs/roadmap.md`: the MVP 6 stable API bullet states that the surface exists for the review queue, and that ingest, query, doctor and setup are later slices.
- `src/openkos/application/__init__.py` docstring: the package stays internal. The public edge is `openkos.api`.

## Deviations From Design And Spec

1. `commit_paths` exists in both `vcs/git.py` and `vcs/autocommit.py`. The guard pins exactly those two.
2. S6a: the tasks said a resolved decision returns `NotApplied`. The shipped behaviour returns `Stale("row_resolved")`. The spec allows either outcome, so no spec change was needed. The same value is returned for a key that never existed, and the reason text is misleading in that case.
3. S3: the default candidate walk was bound at import time, despite its docstring. It was fixed with a RED-first test before merge.
4. S6b: two tasks had their code written before their tests ran. The RED was a collection `ImportError`. Mutation checks compensated.
5. S2, S3, S4, S5, S6a and S6b each exceeded the ~400 authored-line advisory budget, mostly because of tests.
6. S6b review follow-up (`2306f168`, tests only): the earlier refresh tests asserted counts only. Two mapping mutations survived. `tests/unit/api/test_refresh_public_path.py` now pins the end-to-end mapping.

## Final-State Contradictions And How They Were Ranked

1. **S7 status**. `sdd/stable-python-api/apply-progress` #3583 says S7 is PARTIAL, with S7.3, S7.6 and S7.7 remaining. The launch scope says S7 is this archive PR, and it lists the merges. The apply-progress was written before S7 closed. Ranked as an intermediate snapshot. Reported here: S7.3, S7.6 and S7.7 are closed by this phase.
2. **Test count**. #3583 records 12827 passed for S6b. #3588, written later and covering the second commit `2306f168`, records 12830 passed, 96.73% coverage. The later figure is carried here. It is from an intermediate snapshot, so it is a record at that time and not a current gate result.
3. **Volatility ambiguous reachability**. #3583 says it is unreachable and tested only by monkeypatch. The launch scope says the same. The spec amendment states that it is always 0 under today's store. The reasoning was checked against `state/volatility_suggestions.py` (`type_name` primary key) and `application/suggest_volatility_service.py` (both regimes resolve to the same single stored row). No unresolved conflict.

## Open Findings And Follow-ups

1. **Version bump not enforced in CI.** Make the `API_VERSION` bump check bite in CI by comparing against the base branch's snapshot. Until then the bump is a review obligation.
2. **Confidential-commit notice not surfaced.** The CLI prints a notice from `CommitOutcome.includes_confidential`. `api/_wiring.py` drops it. The CLI path reads it only in `cli/main.py`.
3. **Insecure-key warning not surfaced.** The API does not carry the CLI's insecure-key warning. This is the `insecure_key_warning` notice in the S7.6 task.
4. **`Stale("row_resolved")` for a never-existing key.** The reason text is misleading. Decide whether to split it.
5. **CLI delegation.** Whether the CLI later delegates to `api/_wiring.py` is undecided. Today the two wirings are parallel.
6. **Volatility two-regime walk cost** is not measured on large bundles.
7. **ADR-0054** is still Proposed, although it was implemented in #1369. This is outside this change and noted for a later docs pass.
8. **Carried from the judging archive**: `PERSIST_ATTEMPTS` and its backoff are unmeasured. The daemon-budget exclusion test is missing. The suggest-volatility digest guard is missing. The curate rubric-unchecked identity serve is open.
9. **Lint findings as counts only in v1.** `lint` findings reach the API as counts, not as the full finding objects. This is a v1 design limit from the S7.6 task.

## Tasks

- At archive start, `tasks.md` showed 64 checked and 3 unchecked (S7.3, S7.6, S7.7).
- S7.3 was ticked after the merge was observed. S7.6 was ticked after this report was written. S7.7 was ticked after the move was observed, as a separate edit.
- Final counts are given in the phase result, after the post-move edit.
- Remaining unfinished work: none in the change's own tasks. The follow-ups above are not tasks of this change.

## Checks Run In This Phase

Recorded in the phase result with exit codes. The checks are:

- `uv run pytest -q -p no:cacheprovider tests/unit/test_config_keys_specified.py tests/unit/test_adr_index.py`
- The tests that read `openspec/` (`tests/unit/bundle/test_listing.py`, `tests/unit/cli/test_ingest.py`, `tests/unit/cli/test_list.py`, `tests/unit/cli/test_reconcile.py`, `tests/unit/graph/test_analysis.py`, `tests/unit/test_adr_index.py`, `tests/unit/test_agents_template_provenance.py`, `tests/unit/test_config_keys_specified.py`, `tests/unit/test_sensitivity.py`, `tests/unit/test_source_title.py`). Selected by grep for `openspec` under `tests/`.
