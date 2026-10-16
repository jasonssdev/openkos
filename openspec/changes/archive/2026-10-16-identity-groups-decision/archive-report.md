# Archive Report: identity-groups-decision

**Archived**: 2026-10-16 (S5, the last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-16-identity-groups-decision/`
**Store mode**: hybrid. Filesystem merge and `git mv`, plus apply progress saved to Engram as `sdd/identity-groups-decision/apply-progress`.
**Tracking issue**: #1430 (refs #1408)

## Final State At Close

- **Implementation**: five slices merged to `main`, each one squash-merged PR, stacked to `main` in order. S5 (this archive) closes the issue.

| Slice | PR | Squash commit | Authored lines (additions + deletions; plan docs and generated snapshots excluded) | CI `test (3.14)` |
|-------|----|---------------|--------------------------------------------------------------------------------------|------------------|
| S1 selection, group preview, version | #1433 | `538ee8e4` | about 710 (also carried the plan commit) | 11m27s |
| S2a group commit, row resolution | #1434 | `abcd1ba0` | about 1030 | 11m44s |
| S2b unmarked rulings, interrupted group | #1435 | `5d3e64c6` | about 360 | 10m06s |
| S3 offer, dispatch, `openkos.api` facade | #1436 | `2ccb182d` | about 930 | 11m47s |
| S4 local HTTP API | #1437 | `cc24f904` | about 280 | 11m13s |
| S5 wording, CHANGELOG, spec merge, ADR, archive | this PR | | docs and one test file | |

  S1, S2a and S3 carry the `size:exception` label with a comment: each is a small amount of source (190 to 290 lines) with the matrices that are the point of the slice, and none has a coherent split.
- **Shipped surface** (all additive, `API_VERSION` unchanged at 1): `Action.MERGE_GROUP`, `GroupSelection`, `DecisionVersion.selection`, `preview_decision(selection=)`, `MergeStep`, `MergePlan.steps` / `kept_distinct_ids` / `survivor_type`, `Applied.merged` / `kept_distinct`, `NotApplied` reason `invalid_selection`, and `MergeGroupInterrupted` (a `WriteFailed`). Over HTTP: `selection` in the preview body, the `invalid_selection` (422) and `merge_group_interrupted` (500) rows, and the regenerated OpenAPI snapshot.
- **ADR-0063**: Accepted, in all three places (frontmatter, the `**Status:**` body line, the `docs/adr/README.md` row). The status test is `tests/unit/test_adr_index.py`.
- **Not changed, on purpose**: the `curate` Identity stage still prints the pairwise `openkos merge` commands for a group (design Decision 11). `docs/cli.md` does not change, because no verb changes.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| curate-decisions | Updated | 3 MODIFIED requirements replaced in place (exact heading match, each found once): Each Kind Carries Its Own Action Set; Only Judged-SAME Identity Items Are Mergeable; Apply Is A Two-Phase Preview And Commit Against A Version Token. 5 ADDED requirements appended. 22 requirements now. |
| stable-python-api | Updated | 1 ADDED requirement appended: Group Decisions Extend v1 Additively. 16 requirements now. |
| local-http-api | Updated | 2 MODIFIED replaced in place (Responses Are The Python API's Result Types, Populated And Round-Tripping; Outcomes And Errors Map To Status Codes With A Uniform Body), 1 ADDED appended (The Group Selection Travels In The Preview Body And The Echoed Version). 18 requirements now. |
| pending-work | Updated | 1 MODIFIED replaced in place: Only Human-Facing Write Paths Resolve A Row. 20 requirements now. |

The delta's "(Previously: ...)" lines were kept as written. One consequence: the "Only Judged-SAME Identity Items Are Mergeable" requirement had an older "(Previously: the evaluation field distinguished judged SAME, judged DIFFERENT and pending evaluation only ...)" note from an earlier change, which the delta replaced with its own. That history stays in git and in that change's archive.

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of the four deltas was compared as an exact line against its main spec after the merge. Result: 88 headings, 0 missing, 0 non-unique (curate-decisions 48, stable-python-api 5, local-http-api 28, pending-work 7). Before the merge each of the 6 MODIFIED headings matched exactly once in its living spec, and none of the 6 ADDED headings existed.

No REMOVED or RENAMED headings exist in any delta.

## Requirement-vs-Code Check (S5.3)

Each requirement was read against `src/openkos/application/curate_decisions.py`, `lifecycle.py`, `queue_resolution.py`, `merge_service.py`, `write_gate.py`, `src/openkos/api/`, `src/openkos/localapi/` and the tests, not against the task list.

| Requirement | Result |
|-------------|--------|
| curate-decisions: Each Kind Carries Its Own Action Set | Met. `_identity_actions` lists `(MERGE_GROUP, KEEP_DISTINCT, SKIP)` for a judged-SAME group of three or cross-type pair and leaves a same-type pair at `(MERGE, KEEP_DISTINCT, SKIP)`. `test_curate_decisions_group_offer.py`; two older listing tests were updated to the modified action set. |
| curate-decisions: Only Judged-SAME Identity Items Are Mergeable | Met. `merge_group` is carried exactly where `merge` is withheld for `more_than_two_members` or `cross_type`; a group that is pending, DIFFERENT or UNCERTAIN withholds both with the evaluation reason; a pending same-type pair withholds only `merge`. `_refusal` maps a withheld `merge_group` to `not_mergeable` and a pair's to `not_offered`. |
| curate-decisions: Apply Is A Two-Phase Preview And Commit Against A Version Token | Met. The selection is a defaulted last field of `DecisionVersion` and part of `token()` only when set (two literal digests pin every existing token). The re-preview reads the selection from the TOKEN, so an altered selection is `Stale("content_changed")`; a version for another action that carries a selection is `invalid_selection`. |
| curate-decisions: A Group Selection Names The Marked Members And The Survivor | Met. `validate_selection` returns the canonical selection (deduplicated, ascending) or `NotApplied("invalid_selection")` for no selection, a survivor not marked, a member outside the group, or fewer than two distinct members; order and repeats leave the token unchanged. The surface never picks a survivor. |
| curate-decisions: A Group Decision Merges The Marked Members Pairwise Into The Survivor | Met. Ascending absorbed order through `merge_core`, one ledger entry per step; `unmerge` of each step newest-first and `unwind_merges` to the first absorbed member both restore A, B and C byte for byte. The preview's type outcomes are simulated in write order and equal the written `type_alternative`. The row resolves `applied` (as proposed when every member is marked, modified otherwise) before the retirement of rows naming the absorbed member; a replayed token is `Stale("row_resolved")`. |
| curate-decisions: Members Not Marked Are Kept Distinct From The Survivor | Met. `_group_ruling_sets` (survivor with each unmarked member, plus the remainder when two or more) through `ports.record_identity_ruling`; open rows over exactly those members are declined; none between two unmarked members alone; none when all are marked; a failing ruling is fail-open with one fixed-text warning and is absent from `Applied.kept_distinct`. |
| curate-decisions: A Cross-Type Group Merges Only Into The Survivor The Selection Names | Met. `merge` stays withheld with `cross_type`; the survivor is always the selection's; the plan carries `cross_type_concern` over the marked members; the chosen-type survivor records the other type through the unchanged merge write (`test_the_member_of_the_chosen_type_survives_and_records_the_other_type`). `BULK_ELIGIBILITY["identity"]` is `NEVER` and `item_consent` is `ask`. |
| curate-decisions: A Group Decision Is Checked Once, Holds The Lock Once And Commits Once | Met. One content comparison before the section; one section holds the row re-read, presence of every member, the first step's drift, catalog and read-drift checks, the inputs check and all writes; later steps are prepared inside it. A held lock with no wait raises `WorkspaceBusyError` and writes nothing. One auto-commit holds every path and ruling. A later-step failure keeps and commits what landed, records no ruling and raises `MergeGroupInterrupted`; a first-step failure is the plain `WriteFailed`. |
| stable-python-api: Group Decisions Extend v1 Additively | Met. Every difference against the merge base was classified additive at each slice, `API_VERSION` is 1, and `DecisionPreview.plan` and `ApplyOutcome` are unchanged (`test_group_decisions_leave_the_plan_union_and_the_outcome_union_unchanged`). Plans carry Concept IDs only. |
| local-http-api: Responses Are The Python API's Result Types, Populated And Round-Tripping | Met, with a correction to the spec's wording (see Deviations): `plan` is declared through a `PreviewPlan` component that is the `oneOf` with the `kind` discriminator. A populated merge-group plan over a cross-type group of three with one unmarked member parses back equal to the Python preview. |
| local-http-api: Outcomes And Errors Map To Status Codes With A Uniform Body | Met. `invalid_selection` 422 (landed with S1, because the reason and its row must ship together) and `merge_group_interrupted` 500, not retryable, placed before `write_failed`, with fixed messages that name no id. |
| local-http-api: The Group Selection Travels In The Preview Body And The Echoed Version | Met. `PreviewRequest` is an `_EchoRequest` with `selection`; a malformed selection is 422 `invalid_request` with the API not called; the apply body gains no field; an echoed selection with a member removed is 409 `stale`; `GroupSelection` is one OpenAPI component. |
| pending-work: Only Human-Facing Write Paths Resolve A Row | Met. The first step passes `GroupRow` to `merge_core`, which resolves the group's row before `_retire_naming`; a relation row naming the absorbed member is still retired `stale`. The write-core registry is unchanged (`merge_core` maps to `resolve_merged`). |

## Deviations From The Design

1. **`PreviewPlan` wrapper (S1).** The defaulted `DecisionVersion.selection` made the OpenAPI `DecisionVersion` split into `-Input` and `-Output` components, because the preview response flags defaulted fields as required (`json_schema_serialization_defaults_required` on `DecisionPreview`) and the apply request does not. The design only anticipated the split for `GroupSelection`. The flag moved onto a new `PreviewPlan` root model that wraps the plan union, so it no longer reaches `DecisionVersion`. The wire JSON of the plan is identical; the OpenAPI document names the plan component `PreviewPlan`, and a preview response now serves `version.selection: null`. An apply request that omits `selection` is still accepted (pinned by a test).
2. **Spec wording corrected.** The living local-http-api spec says `plan` is declared through a `PreviewPlan` component, not as an inline `oneOf`.
3. **Test locations.** The listing and dispatch tests live in `tests/unit/application/test_curate_decisions_group_offer.py` and `tests/unit/api/test_decisions_group.py`, not in the 4000-line `test_curate_decisions.py`. `committed_confidential` is asserted through `openkos.api` in S3, where the commit port that sets it is wired, not in S2a.
4. **Redundant `keep` lines.** Every step's own lines already hash each bundle document (`capture_bundle_documents` declares them all as read dependencies), so the design's `keep` lines for unmarked members are a second guard. They are kept, and a test removes the first guard to pin the second.
5. **S2a to S2b gap.** Between the two PRs a later-step failure was a plain `WriteFailed` that committed nothing. Nothing offered the action until S3, so no client could reach it.
6. **Side fixes inside the slices.** Two older listing tests (`test_a_cross_type_identity_withholds_the_merge_and_names_the_command`, `test_an_identity_of_more_than_two_members_withholds_the_merge`), the `Applied` JSON of the HTTP apply test and the discriminator assertion of the OpenAPI test were updated to the modified contract. One of these (the `Applied` JSON) was missed in S2a's first local run and caught by running `tests/unit/localapi`; it was fixed before the PR was opened.
7. **No `exploration.md`.** This change was planned without a separate exploration artifact, so the archive holds `proposal.md`, `design.md`, `tasks.md` and `specs/` besides this report.

## Measured Lock Hold (S2a task 2.7)

A group of four on a 300-concept generated bundle (auto-commit stubbed, so the git commit is not in the number), median of 5 runs: **0.074 s** inside the commit section (runs 0.073 to 0.075 s); 0.162 s for the whole commit including the pre-section re-preview. No assertion on time in the suite. ADR-0036 bounds the lock to a short commit phase, not to one write, and this is one write of N-1 merges.

## Verification Record

- Strict TDD in every slice: RED observed first (an import error on the new names, or failing tests), then GREEN. Mutations on the exact lines of the key new tests, with `__pycache__` purged and the line restored from a byte copy: S1 4 (one survived until an isolating test was added), S2a 6, S2b 6, S3 7, S4 4, S5 2, all killed.
- Each slice ran `ruff check`, `ruff format --check`, `mypy .` and the 58 harness self-tests locally and in CI, and the surface test against the merge base with `OPENKOS_REQUIRE_API_BASE_REF=1`.
- Full `OPENKOS_REQUIRE_API_BASE_REF=1 uv run pytest --cov`: S1 alone 13339 passed, 6 skipped, 96.76% coverage; the cumulative content of S2b to S4, run once before they were merged, 13418 passed, 6 skipped, 96.77%. CI ran the full suite on every PR.

## Follow-ups

- #1432: an `openkos.api` and local API unmerge operation, which the app needs for **Undo** and **Split again** on any combine.
- A `curate` Identity stage group walk reusing `commit_merge_group_decision` (design Decision 11), filed as #1438.
