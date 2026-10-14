# Tasks: Curate Decisions As Data

MVP 6 stage "Curate decisions as data". Inputs: `proposal.md`, `exploration.md`,
`design.md`, `specs/curate-decisions/spec.md`, `specs/curate-command/spec.md` and
ADR-0053 (Proposed). No tracking issue is known: no commit or PR text in this
change may use `Closes`, `Fixes` or `Resolves`; PRs name the change in prose
("the `curate-decisions-as-data` change").

Conventions for every slice:

- **Commit scope** is `cli` for slices that move the `curate` walk or add the
  surface (the scope is the subsystem touched, not the command), `docs` for
  S0d, and `docs` for S5. Types: `test` (S0a, S0b), `refactor` (S1a, S1b, S2,
  S3, S4b), `feat` (S4a, which adds the new list/apply entry points).
- **Locate every target by content**, never by a line number copied from
  `design.md`. The line numbers there (curate.py:1387, 1435-1453, 2194-2270,
  2518-2567, main.py:13200-13205 and so on) are pre-S0 coordinates and drift with
  each slice. Re-derive each range against the working tree before editing.
- **Strict TDD** (`uv run pytest`): RED test first and observed failing for the
  stated reason, then GREEN, then REFACTOR. S0 is characterization, so its goldens
  pass by design; each one is instead proven by a mutation (see "Mutation
  protocol"). Never invent evidence: record the command and the observed result.
- **Gates per slice**, each run directly and never piped through `tail`/`head`
  (a pipe hides failures from `pytest` and `ruff format --check`):
  1. `uv run ruff check .`
  2. `uv run ruff format --check .`
  3. `uv run mypy .` (the whole repo, not `src/`)
  4. `uv run pytest --cov` (90% branch coverage)
  5. `uv run python evals/run_self_tests.py` (CI runs it)

  `uv run pre-commit run --all-files` may be added but is not the gate: it has
  reported Passed while `ruff format --check .` failed.
- **New files** are `git add`-ed before any review or `git diff` based check.
- **Delivery** is `auto-chain` with `stacked-to-main`: each slice is its own PR to
  `main`, merged in order. CI runs only on PRs targeting `main` or `tracker/**`,
  so slice N+1 opens its PR only after slice N is squash-merged, after rebasing
  onto `main` (`git rebase --onto main <old-slice-N-base> <slice-N+1-branch>`,
  then force-push, then confirm check-runs on the head SHA). Delete the merged
  branch locally and remotely.
- **Mutation protocol** (used by every "prove it" task):
  1. Make the mutation with an exact `Edit` on the one line the test pins. Prefer a
     mutation that changes the line's byte length. A same-size mutation can run
     stale bytecode.
  2. Run only the test or golden under proof and observe it FAIL for the
     expected key or assertion, not for an unrelated error.
  3. Revert with the inverse `Edit` (never `git checkout --` mid-feature, which
     wipes uncommitted work).
  4. Purge bytecode: `find src tests -name __pycache__ -type d -prune -exec rm -rf {} +`.
  5. Re-run and observe GREEN.

  Record the mutated line, the failing assertion and the green re-run in the task.

## Review Workload Forecast

Authored changed lines are additions plus deletions. Recorded golden JSON is
generated and excluded from the estimates below (it is still part of the PR). The
roughly 400-line figure is an advisory planning heuristic: no test or doc is cut,
and no slice is split artificially, to fit it. Where a slice naturally exceeds it,
the reason is stated.

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,950 code and tests across 9 slices, plus ~2,500 docs-only lines in S0d (the change folder and ADR-0053 that already exist on the branch) |
| 400-line budget risk | High in total; per slice below |
| Chained PRs recommended | Yes |
| Suggested split | S0d -> S0a -> S0b -> S1a -> S1b -> S2 -> S3 -> S4a -> S4b -> S5 (10 PRs) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Per-slice forecast

| Slice | Estimated lines | Chained PRs recommended | 400-line budget risk | Decision needed before apply |
|-------|-----------------|-------------------------|----------------------|------------------------------|
| S0d | ~2,500 (docs only) | Yes (PR 1 of 10) | High: a pure documentation PR, read once, not code. It exceeds the heuristic because the change folder (proposal, exploration, design, two delta specs, tasks) and ADR-0053 are one contract. Splitting a spec from its design would be artificial. | No |
| S0a | ~350 | Yes (PR 2) | Medium | No |
| S0b | ~350 | Yes (PR 3) | Medium | No |
| S1a | ~350 | Yes (PR 4) | Medium | No |
| S1b | ~450 | Yes (PR 5) | High: the merge preview/commit, the version token and its in-section re-validation are one cohesive protocol, and its RED tests are the bulk. Cutting tests to fit is forbidden. | No |
| S2 | ~300 | Yes (PR 6) | Low | No |
| S3 | ~300 | Yes (PR 7) | Low | No |
| S4a | ~480 | Yes (PR 8) | High: the list, the stale marking, the action table, `apply_decision`, `DecisionPolicy` and the boundary guard are one read-and-apply surface; the per-kind table cases are test-heavy. | No |
| S4b | ~320 | Yes (PR 9) | Medium | No |
| S5 | ~150 | Yes (PR 10) | Low | No |

Slice changes against the design's table, each with its reason:

- **S0d added (PR 1).** The design allows ADR-0053 and the design to land "with the
  first code PR (S1a) unless the chain strategy puts them in S0a". Both would push
  a 350-line slice past 2,800 lines of docs. A docs-only first PR keeps every code
  PR reviewable. It also lands the contract before any code that cites it.
- **S4 split into S4a and S4b.** The design's S4 mixes two independent concerns:
  the new read/apply surface (list, token apply, boundary guard) and the
  behavior-preserving rewiring of `gate`, `run_curate` and `_confirm_item` onto
  pure consent functions. Honest sizing is ~800 lines, which no cohesive single
  PR holds. The split is also the revert boundary the proposal already names
  ("S4 reverts the list and the consent table"). Neither half depends on the other
  at runtime; S4a goes first because it promotes `row_is_fresh`.
- **`row_inputs_changed` Stale reason lands in S4a, not S1b.** Design Decision 3
  places the `row_is_fresh` promotion in S4. S1b implements and tests the other
  three reasons (`content_changed`, `row_resolved`, `row_superseded`). S4a adds
  the fourth together with the predicate it needs.
- S0a, S0b, S1a, S1b, S2, S3 and S5 keep the design's scope and order.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| S0d | Land the change folder and ADR-0053 | PR 1 | `uv run pytest tests/unit/test_adr_index.py -q` (ADR index and status consistency) | N/A: documentation only, no runtime boundary | Revert the change folder, ADR-0053 and its README row together |
| S0a | Identity-side goldens, SHA normaliser, `adjudicate --apply` `d` golden | PR 2 | `uv run pytest tests/unit/cli/test_curate_characterization.py -q` | The goldens ARE the harness: `CliRunner` over a seeded temp workspace, with git identity pinned by `GIT_CONFIG_COUNT` | Revert the new test file, the goldens JSON and the `golden_support.py` addition; no production code touched |
| S0b | Structure and Metadata goldens, consent matrix | PR 3 | `uv run pytest tests/unit/cli/test_curate_characterization.py tests/unit/cli/test_curate_consent_matrix.py -q` | Same harness | Revert the added scenarios, the matrix file and the JSON delta |
| S1a | `WriteFailed`, module skeleton, keep-distinct and skip rewired | PR 4 | `uv run pytest tests/unit/application/test_curate_decisions.py tests/unit/application/test_layering.py tests/unit/cli/test_curate_characterization.py -q` | The S0 goldens run unchanged, plus the `adjudicate --apply` `d` golden | Revert `curate_decisions.py`, `WriteFailed`, and the `curate.py`/`main.py` keep-distinct wiring together; nothing downstream exists yet |
| S1b | Identity merge preview/commit with the version token | PR 5 | `uv run pytest tests/unit/application/test_curate_decisions.py tests/unit/cli/test_curate_characterization.py -q` | The S0 identity goldens (y, accept-recommended, `--auto-merge`, vanished, drift, write failure) | Revert the merge half of `curate_decisions.py`, `open_row_by_key`, and the `curate.py` identity wiring; S1a's keep-distinct stays functional |
| S2 | Relate preview/commit incl. reversed | PR 6 | `uv run pytest tests/unit/application/test_curate_decisions.py tests/unit/cli/test_curate_characterization.py -q` | The S0b Structure goldens | Revert the relate half and the `_structure_run` wiring; S1 stays |
| S3 | Tier preview/commit; `_plan_config_commit` split | PR 7 | `uv run pytest tests/unit/application/test_curate_decisions.py tests/unit/cli/test_curate_characterization.py tests/unit/cli/test_set_volatility.py -q` | The S0b Metadata goldens and the `set-volatility` verb tests | Revert the tier half, the `_metadata_run` wiring and the `_plan_config_commit` split together |
| S4a | `list_open_decisions`, `preview_decision`, `apply_decision`, `DecisionPolicy`, `row_is_fresh`, boundary guard | PR 8 | `uv run pytest tests/unit/application/test_curate_decisions.py tests/unit/application/test_curate_decisions_boundary.py -q` | `uv run python -c` that seeds a temp workspace through `pq.upsert_proposal` and calls `list_open_decisions` (LLM-free) | Revert the list/apply half, the `row_is_fresh` promotion and the boundary test; the CLI walks do not use it |
| S4b | Consent and bulk eligibility as data, wired into the CLI | PR 9 | `uv run pytest tests/unit/application/test_curate_decisions.py tests/unit/cli/test_curate_consent_matrix.py -q` | The S0b consent matrix run unchanged | Revert the consent functions and the three `curate.py` call sites together; `Stage.auto_acceptable` returns to a literal field |
| S5 | Archive: merge deltas, accept ADR-0053, check spec vs code | PR 10 | `uv run pytest tests/unit -q -k "adr or spec"` plus the heading name-match command in S5.2 | N/A: documentation and spec only | Revert the spec merge, the ADR status flip and the archive move together |

### Dependency Graph

- **S0d -> S0a -> S0b** (strict order, though S0a and S0b only share files): every
  code slice needs the goldens recorded on the tree before anything moves.
- **S0a, S0b -> S1a -> S1b** (strict): S1b needs the module skeleton, ports and
  outcomes from S1a.
- **S1b -> S2 -> S3** (strict order; S2 and S3 depend only on S1a's skeleton, but
  all three edit `curate_decisions.py` and `curate.py`, so a single order avoids
  rebase conflicts).
- **S3 -> S4a -> S4b** (strict order). S4a needs all three preview/commit pairs to
  exist before the generic dispatch; S4b rewires consent onto the data table.
- **S4b -> S5** (strict): S5 verifies the shipped code against the specs.
- Any prefix of the chain is a valid stopping point: every slice leaves `curate`
  working end to end and the S0 goldens passing unchanged.

### Requirement Traceability

| Requirement (spec) | Primary tasks |
|---|---|
| curate-decisions: The Surface Is Plain Data With No Interaction | S1a.6, S1a.7 (layering and `isatty` guard); S1a.3 (typed outcomes) |
| curate-decisions: The List Returns All Six Queue Kinds | S4a.1-S4a.4 |
| curate-decisions: Each Kind Carries Its Own Action Set | S4a.2-S4a.5; skip: S1a.4 |
| curate-decisions: Only Judged-SAME Identity Items Are Mergeable | S1b.1, S4a.3, S4a.9 |
| curate-decisions: Staleness Is Marked, Never Hidden | S4a.1, S4a.4 |
| curate-decisions: Apply Is A Two-Phase Preview And Commit Against A Version Token | S1b.1-S1b.4 (token, three reasons); S4a.6-S4a.10 (`row_inputs_changed`, `apply_decision`, `DecisionPolicy`); S2.1, S3.1 (tier covers proposed and effective tier) |
| curate-decisions: The Identity Merge Preview Discloses A Planned Reconciliation | S1b.1, S1b.3 |
| curate-decisions: Commit Re-Checks Members, Drift And The Catalog Before Writing | S1b.2-S1b.3 (guard order); S0a.12 (order golden) |
| curate-decisions: The Adapter Owns Rendering, Exit Codes And Commits | S1a.8-S1a.9, S1b.5-S1b.6, S2.4, S3.5 |
| curate-decisions: Keep-Distinct Records A Durable Ruling | S1a.2-S1a.5 |
| curate-decisions: Structure And Metadata Actions Resolve Their Rows | S2.1-S2.5, S3.1-S3.6 |
| curate-decisions: Consent And Bulk Eligibility Are Data | S4b.1-S4b.8 |
| curate-decisions: The Surface Computes No Judgment | S1b.7 (no model call), S4a.11 |
| curate-decisions: Payloads Are Owner-Local | S4a.12 (boundary guard) |
| curate-decisions: Confidence Is Not Presented As A Trust Signal | S4a.5 |
| curate-decisions: Behavior Of The Command Is Unchanged | S0a, S0b goldens re-run in every slice gate |
| curate-command (delta): Identity Stage Reuses Merge Cores | S1a, S1b, S0a goldens |
| curate-command (delta): Structure Stage Writes Through The Relate Core | S2, S0b goldens |
| curate-command (delta): Metadata Stage Writes Tiers, Reports Sensitivity | S3, S0b goldens |
| curate-command (delta): Curate Reads And Resolves The Pending Queue | S1a-S3 (resolution stays inside the write cores), S5.3 |

---

## Slice S0d - PR 1: land the change folder and ADR-0053 (docs only, ~2,500 lines)

Start: branch `feat/curate-decisions-as-data` has the change folder and ADR-0053
untracked or uncommitted. Finish: both are on `main`. Out of scope: any code.

### Phase 1: Stage and verify the planning artifacts

- [x] S0d.1 Confirm the change folder is complete: `openspec/changes/curate-decisions-as-data/proposal.md`, `openspec/changes/curate-decisions-as-data/exploration.md`, `openspec/changes/curate-decisions-as-data/design.md`, `openspec/changes/curate-decisions-as-data/specs/curate-decisions/spec.md`, `openspec/changes/curate-decisions-as-data/specs/curate-command/spec.md` and `openspec/changes/curate-decisions-as-data/tasks.md` exist. Check: `git status --short` lists them; none is empty.
- [x] S0d.2 Confirm ADR-0053 and its index row: `docs/adr/0053-curation-decisions-are-a-two-phase-application-surface.md` exists with status `Proposed` in BOTH the frontmatter `status` field and the `**Status:**` line, and `docs/adr/README.md` has the ADR-0053 row. Check: the ADR-status consistency test (`tests/unit/test_adr_index.py`, which reads the places an ADR status lives) passes.
- [x] S0d.3 Confirm the roadmap carries the "Judging as an application service" stage: `docs/roadmap.md` (read-only). The proposal notes PR #1367 lands it; this worktree already shows it. Check: `rg -n "Judging as an application service" docs/roadmap.md docs/adr/0053-curation-decisions-are-a-two-phase-application-surface.md` returns both.
- [x] S0d.4 Gates: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, `uv run pytest --cov`, `uv run python evals/run_self_tests.py`. All unpiped. (Docs-only, so expect no coverage change.)
- [x] S0d.5 `git add` the six change-folder files, the ADR and `docs/adr/README.md`; commit `docs(sdd): add the curate-decisions-as-data change and ADR-0053`. PR text references the change in prose, no `Closes`.

---

## Slice S0a - PR 2: identity-side goldens (~350 lines, tests only)

Start: `curate` has no characterization golden. Finish: identity-side behavior,
the SHA normaliser and the `adjudicate --apply` `d` path are pinned on the
UNCHANGED tree. Out of scope: any production edit.

Recording rule: record every golden on the tree as it is at the start of S0a
(`main` after S0d), before any production file moves. A golden recorded after a
move proves nothing.

### Phase 1: Harness and environment

- [x] S0a.1 RED/support: add a SHA normaliser to `tests/unit/cli/golden_support.py`: `normalise_shas(text) -> str` replacing every `committed as <hex>` token (7-40 hex chars) with `committed as <SHA>`. `Goldens.check` and `normalise` are unchanged. Test first in `tests/unit/cli/test_curate_characterization.py` (a small `test_normalise_shas_*` block): it must fail before the function exists (ImportError) and pass after; it also asserts a non-sha word is untouched and that two different shas normalise equal.
- [x] S0a.2 Create `tests/unit/cli/test_curate_characterization.py` with the shared fixtures: a seeded workspace helper reusing the existing curate test fixtures and monkeypatch targets (find them with `rg -n "def _seed|curate" tests/unit/cli/test_curate*.py`; reuse, do not fork); `isatty` patched; `OLLAMA_HOST` removed or poisoned so no real model is reachable; and a pinned git identity set through `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_0`/`GIT_CONFIG_VALUE_0` (`user.name`), `GIT_CONFIG_KEY_1`/`GIT_CONFIG_VALUE_1` (`user.email`), plus `GIT_CONFIG_GLOBAL` pointing at an empty file and `GIT_CONFIG_NOSYSTEM=1`. `GIT_AUTHOR_*` alone is NOT enough: `git config user.name` ignores it and a CI machine with no global identity then prints an `identity unset` line that reddens every golden. Check: the module-level fixture sets all of these through `monkeypatch`, never by mutating `os.environ` directly.
- [x] S0a.3 Add a record mode to the test module only (not to `Goldens`): when `RECORD_CURATE_GOLDENS=1` the helper writes `tests/unit/cli/fixtures/curate_characterization_goldens.json` instead of asserting; otherwise it calls `Goldens.check`. Each scenario records `stdout`, `stderr`, `exit_code`, `commits` (the ordered commit subjects made) and `rows` (the queue rows resolved with their resolution). Streams pass through `normalise` then `normalise_shas`.

### Phase 2: Record the identity scenarios (characterization, green by design)

Each scenario is one task: write the test, record its golden, run it green,
then prove it with a mutation. The mutation targets are located by content in
`src/openkos/cli/curate.py` and `src/openkos/cli/main.py`.

- [x] S0a.4 Identity `y` (merge accepted): golden for stdout, stderr, exit 0, the per-merge commit, row resolved `merged`. Prove: mutate the `committed as` disclosure prefix in the identity write path; the golden fails on `stdout`. Revert, purge `__pycache__`, green.
- [x] S0a.5 Identity `s` (skip): nothing written, no commit, the row stays `pending`. Prove: mutate the skip branch so it counts as declined; fails on `rows` or `stdout`. Revert, purge, green.
- [x] S0a.6 Identity `d` (keep distinct): ruling sidecar written, commit subject `openkos: keep distinct <a>/<b>`, row `declined`. Prove: mutate the commit-message literal; fails on `commits`. Revert, purge, green.
- [x] S0a.7 Accept-recommended: one `y` over fresh in-class SAME groups on a TTY writes each merge through the per-merge path. Prove: mutate the `_offer_accept_recommended` write call to skip the commit; fails on `commits`. Revert, purge, green.
- [x] S0a.8 `--auto-merge`: the automatic pass makes ONE commit per run (ADR-0049), not per merge. Prove: change the pass to commit per merge (or drop its commit); fails on `commits`. Revert, purge, green. This golden is what keeps `--auto-merge` off the new per-item apply.
- [x] S0a.9 Member vanished: a member deleted between the prompt and the section prints the merge-skip line on stderr and the walk continues. Prove: mutate that stderr line's text; fails on `stderr`. Revert, purge, green.
- [x] S0a.10 Drift exit 3 (identity): a member edited between the prompt and the section. Expect exit 3, the drift message, nothing written, no later item or stage. Prove: change the exit code mapping from 3 to 1; fails on `exit_code`. Revert, purge, green.
- [x] S0a.11 Write failure, exit 1, two shapes: (a) failure at prepare, (b) failure at the merge write. Expect `openkos curate: Identity: failed while merging A into B -- <err>.`, exit 1, and the disclosure of what was already applied. Prove each: mutate the `failed while merging` literal for (b), and the prepare-failure branch for (a); each fails on `stderr`. Revert, purge, green.
- [x] S0a.12 Guard order golden (design correction 4): construct a case where drift AND a catalog recompose failure would both fire; curate reports drift first (drift, then recompose, then read-drift), unlike `merge_concepts`. Prove: swap the first two guards in the commit-phase sequence; fails on `stderr`. Revert, purge, green.
- [x] S0a.13 Keep-distinct record failure (fail-open): make the ruling write raise; expect the notice on stderr, the walk continues, exit 0. Prove: mutate the fail-open `try/except` into a re-raise; fails on `exit_code`. Revert, purge, green.
- [x] S0a.14 Non-TTY refusal: a write walk with no TTY and no `--auto` refuses with the existing notice and writes nothing. Prove: mutate the notice text; fails on `stderr`. Revert, purge, green.
- [x] S0a.15 Partial availability: the backend goes down mid-batch with `ctx.partial_progress`; expect the partial-progress report and the exit code as today. Prove: mutate the partial-progress line; fails on `stdout` or `stderr`. Revert, purge, green.
- [x] S0a.16 `adjudicate --apply` `d`: the path that calls `_record_identity_decline_from_walk` WITHOUT a commit section or a member-presence check (design correction 2). Goldens for stdout, stderr, exit, the commit and the declined row. Prove: mutate the commit-subject literal in `_record_identity_decline_from_walk`; fails on `commits`. Revert, purge, green. This is the guard for S1a changing that helper's body.

### Phase 3: Environment independence and gate

- [x] S0a.17 Prove the goldens do not depend on the developer's git config: run `uv run pytest tests/unit/cli/test_curate_characterization.py -q` with `HOME` set to an empty temporary directory, `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`. All pass. Then remove the `GIT_CONFIG_COUNT` pinning for one run and observe at least one golden fail on an identity-unset line, confirming the pin is load-bearing; restore it.
- [x] S0a.18 Confirm the recorded JSON contains no workspace path (`<ROOT>` only), no raw SHA (`<SHA>` only) and no timestamp. Check: `rg -n "/tmp|/var/folders|[0-9a-f]{40}" tests/unit/cli/fixtures/curate_characterization_goldens.json` finds nothing.
- [x] S0a.19 `git add` the new test file and the JSON. Gate: the five commands from "Conventions", unpiped. Commit: `test(cli): record the identity-side curate characterization goldens`. PR text: tests only, recorded on the unchanged tree, references the change in prose.

---

## Slice S0b - PR 3: Structure, Metadata and consent-matrix goldens (~350 lines, tests only)

Start: S0a merged and this branch rebased onto `main`. Finish: every curate walk
and every consent combination is pinned. Out of scope: any production edit.

### Phase 1: Structure and Metadata scenarios

- [x] S0b.1 Structure `y`: relation written, row `applied` as proposed, the relate commit path and message. Prove: mutate the relate commit-message literal in `_structure_run`; fails on `commits`. Revert, purge, green.
- [x] S0b.2 Structure `n` (decline) and `s` (skip): both write nothing; only `n` appears in the `declined:` tally, `s` does not (spec scenario "A skipped suggestion is not listed as declined"). Prove: mutate the tally so `s` is listed; fails on `stdout`. Revert, purge, green.
- [x] S0b.3 Structure `a` and `r` with `--accept structure`: accept-remaining and reversed direction for an asymmetric type; the reversed edge resolves the row `applied` as modified. Prove: mutate the reversal (source and target not swapped); fails on `rows` and `stdout`. Revert, purge, green.
- [x] S0b.4 Structure in-section endpoint drop: an endpoint deleted so the in-section check drops the edge with its stderr line, and the pre-prepare drop variant. Prove: mutate the in-section drop line; fails on `stderr`. Revert, purge, green.
- [x] S0b.5 Structure drift exit 3 and write failure exit 1. Prove: mutate the exit mapping of each; fails on `exit_code`. Revert, purge, green.
- [x] S0b.6 Metadata `y`, `n`, `a`: tier written to `openkos.yaml`, the commit subject `openkos: set-volatility <type> -> <tier>`, row `applied`; `n` writes nothing. Prove: mutate the commit-message literal; fails on `commits`. Revert, purge, green.
- [x] S0b.7 Metadata drift exit 3. Prove: mutate the exit code; fails on `exit_code`. Revert, purge, green.
- [x] S0b.8 Dirty `openkos.yaml` config-commit refusal, exit 1 (the `_plan_config_commit` refusal text). Prove: mutate the refusal text; fails on `stderr`. Revert, purge, green. This is the guard for S3 splitting that function.

### Phase 2: The consent matrix

- [x] S0b.9 Create `tests/unit/cli/test_curate_consent_matrix.py`: parametrize stage x TTY x `--auto` x `--accept` (none, structure, metadata) x `--auto-merge` x `review` (true/false). For each combination assert the three observable outcomes: (1) the cost gate result (granted, asked, refused), (2) the write-walk refusal notice or its absence, (3) whether a per-item prompt was reached. Drive the real `gate`, `run_curate` and `_confirm_item` with probes stubbed so no model is called. The expected values are RECORDED from the current tree, not derived from the design, so the matrix pins what shipped.
- [x] S0b.10 Add the explicit rows the spec names: identity is never reachable by `--accept identity` or `review: false` (spec: bulk acceptance excludes identity); an asymmetric relation type is never bulk-accepted; `review: false` accepts only the non-destructive stages. Prove each with a mutation: make `_confirm_item` treat identity as bulk-eligible; the matrix fails on the "per-item prompt reached" outcome. Revert, purge, green. Mutate `gate()` so `--auto` skips its disclosure; the matrix fails on the gate outcome. Revert, purge, green.
- [x] S0b.11 Gate: the five commands from "Conventions", unpiped. `git add` the new matrix file. Commit: `test(cli): pin the curate structure, metadata and consent-matrix behavior`. PR text states the matrix must pass unchanged through S1a-S5.

---

## Slice S1a - PR 4: surface skeleton, keep-distinct and skip (~350 lines)

Start: goldens merged. Finish: `application/curate_decisions.py` exists with the
version, outcomes, ports, observer and the keep-distinct pair; `curate`'s `d` and
`s` paths and `_record_identity_decline_from_walk` use it; goldens unchanged.
Out of scope: merge, relate, tier, list, consent.

### Phase 1: Typed write failure and surface types (RED first)

- [x] S1a.1 RED: create `tests/unit/application/test_curate_decisions.py` with `WriteFailed` tests: it subclasses `write_gate.Refused` (so `WriteRefused`); carries `operation` in `{"merge","relate","set_tier"}`, `subjects: tuple[str, ...]`, `error: str`; chains its cause (`__cause__`); `message` is a neutral rendering with no `openkos <verb>:` prefix; and `except (OSError, ValueError)` does not swallow it. Must fail: `WriteFailed` does not exist.
- [x] S1a.2 GREEN: add `WriteFailed(Refused)` to `src/openkos/application/write_gate.py`. S1a.1 passes. REFACTOR: confirm no other `write_gate` consumer changes behavior (`rg -n "write_gate" src` unchanged semantics).
- [x] S1a.3 RED: in the same test file, add tests for `DecisionVersion` (frozen; `token()` is deterministic opaque hex; changing `decision_key`, `payload_digest` or `content_digest` changes the token; `payload_digest=None` allowed), for `Applied`/`NotApplied`/`Stale` (frozen, structured fields, `Action` enum values, `NotApplied.reason` literals), for `DecisionPorts` defaults (`commit_section` is `commit_phase.unlocked_section`; `on_warning` is a no-op) and for `DecisionObserver` (every method a no-op, three methods only). Must fail: module absent.
- [x] S1a.4 GREEN: create `src/openkos/application/curate_decisions.py` with the module docstring (internal, owner-local, never prompts, prints, reads a TTY or raises `typer.Exit`), `Action`, `DecisionVersion`, `Applied`, `NotApplied`, `Stale`, `ApplyOutcome`, `DecisionPorts`, `DecisionObserver`, and `skip_decision(decision_key) -> NotApplied` (writes nothing, returns `NotApplied(Action.SKIP, "skipped")`, per Decision 7). Imports: no `typer`, `rich`, `openkos.cli`, `openkos.vcs`. S1a.3 passes.

### Phase 2: Keep-distinct (RED first)

- [x] S1a.5 RED: tests for `record_keep_distinct(root, layout, member_ids, *, ports)`: writes the ruling sidecar through `bundle/decisions`, commits through `ports.autocommit` with subject `openkos: keep distinct <a>/<b>`, resolves the row `declined` through `duplicates_service.apply_identity_decision(on_warning=ports.on_warning)`, returns `Applied` carrying the sha; a degraded autocommit (`None`) still returns `Applied(commit_sha=None)`; a write error returns `NotApplied("record_failed", detail=<error>)`. And for `commit_keep_distinct(root, layout, member_ids, *, ports, observer)`: enters `ports.commit_section()` once, checks both members present INSIDE the section, a vanished member returns `NotApplied("member_missing")` with NO observer call and NO ruling written (design: it is silent today), and a record failure fires `observer.keep_distinct_unrecorded(member_ids, error)` inside the section and returns `NotApplied("record_failed")`. Record the call order with a recording fake for ports and a section fake that records entry and exit. Must fail: functions absent.
- [x] S1a.6 GREEN: implement `record_keep_distinct` and `commit_keep_distinct` in `src/openkos/application/curate_decisions.py` by moving the body of `_record_identity_decline_from_walk` and the in-section presence check (locate by content; main.py near the `keep distinct` autocommit message and curate.py near the keep-distinct block of the identity walk). Call `duplicates_service` and `merge_service` through the module attribute, never `from ... import`, so existing patches keep working. S1a.5 passes.
- [x] S1a.7 RED then GREEN, layering: extend `tests/unit/application/test_layering.py` with an AST assertion that `src/openkos/application/curate_decisions.py` never calls `isatty` (the existing `_application_modules()` scan already covers the import ban). RED first by adding a temporary `sys.stdin.isatty()` call to the module and observing the test fail, then remove it and observe GREEN. No existing check is weakened.

### Phase 3: Wire the CLI

- [x] S1a.8 Edit `src/openkos/cli/curate.py`: add `_decision_ports(ctx)` (autocommit as `lambda *a: cli_main._autocommit(*a)` so the monkeypatch of `openkos.cli.main._autocommit` keeps binding late; `commit_section` as `lambda: cli_main._commit_section_for(ctx.root)()` so a later patch of `_commit_section_for` still takes effect; `on_warning` as `cli_main._echo_warning`) and `_CurateDecisionObserver` (the keep-distinct failure line printed with the existing text). Route the identity walk's `d` answer through `commit_keep_distinct` and its `s` answer through `skip_decision`; map `NotApplied("record_failed")` to fail-open and `NotApplied("member_missing")` to a silent skip, exactly as before. `_autocommit`, `_reject_drifted_targets` and `_echo_commit_disclosure` keep their single definitions in `src/openkos/cli/main.py`.
- [x] S1a.9 Edit `src/openkos/cli/main.py`: `_record_identity_decline_from_walk` stays (it is shared with `adjudicate --apply`, which has no commit section and no presence check) and its body now delegates to `record_keep_distinct`. It prints from the returned `NotApplied.detail` the same record-failure line it printed before.
- [x] S1a.10 Run the S0 goldens and the matrix unchanged: `uv run pytest tests/unit/cli/test_curate_characterization.py tests/unit/cli/test_curate_consent_matrix.py -q`. All green with ZERO golden edits (`git diff --stat tests/unit/cli/fixtures/` shows nothing).

### Phase 4: Prove and gate

- [x] S1a.11 Mutation proofs: (a) change the keep-distinct commit subject inside `record_keep_distinct`; the `d` golden AND the `adjudicate --apply` `d` golden both fail on `commits`. (b) Swap `_decision_ports`'s late-binding lambda for a direct `cli_main._autocommit` reference; a test that patches `openkos.cli.main._autocommit` (for example the one in `tests/unit/cli/test_curate_auto_merge.py`) goes red, proving the monkeypatch site is live. Revert each with the inverse edit, purge `__pycache__`, re-run green.
  - Recorded deviations (S1a apply): (1) `DecisionPorts` also carries `record_identity_ruling`, defaulting to the shared writer, because `openkos.cli.main._apply_identity_decision` is patched by the keep-distinct failure golden and `test_curate_lock.py`; a direct `duplicates_service` call would have left those patches inert. (2) The `reconcile`, `plan_config_commit` and `autocommit_config_edit` ports are added by S1b and S3, which are the first slices to use them. (3) Proof (b) as written is inert: the `test_curate_auto_merge.py` patch exercises `--auto-merge`, which uses no port, and a patch installed before the ports are built cannot tell early from late binding. Replaced by `tests/unit/cli/test_curate_decision_ports.py`, which builds the ports first and patches afterwards; the early-binding mutation turns it red.
- [x] S1a.12 Gate: the five commands from "Conventions", unpiped. `git add` new files. Commit: `refactor(cli): route curate's keep-distinct and skip through the curate-decisions surface`.

---

## Slice S1b - PR 5: identity merge, two-phase (~450 lines)

Start: S1a merged. Finish: `_identity_write_one` is gone; both the per-item merge
and accept-recommended write through `commit_merge_decision`; the version token
path works for three Stale reasons; `--auto-merge` untouched. Out of scope:
relate, tier, list, `row_inputs_changed`.

### Phase 1: Preview and token (RED first)

- [x] S1b.1 RED: tests for `preview_merge(root, layout, group, *, ordered_pair, no_reconcile, reconcile)`: returns `MergeDecisionPreview` with `prepared`, `member_ids`, `decision_key` (`pq.stored_decision_key("identity", pq.identity_key(member_ids))`), `reconcile_planned` (from `application_lifecycle.reconcile_planned`), `cross_source_same_pair`, `cross_type_concern`, `survivor_criterion`; writes nothing; calls no model; a null or non-SAME adjudication returns `NotApplied("not_mergeable")` (returned, not raised). `version_of(preview, layout=..., payload_digest=...)`: `content_digest` is the sha256 over sorted `"<role>\t<relative path>\t<sha256>"` lines built from `merge_drift_targets(..., include_catalog=False)` (absorbed file as role `delete`), `read_dependencies.present` and `read_dependencies.documents`; editing a referencing document changes `content_digest` (the case `payload_digest` alone misses). Must fail: functions absent.
- [x] S1b.2 RED: tests for `commit_merge_decision(root, layout, preview, *, ports, observer, surface, expected=None)` in `tests/unit/application/test_curate_decisions.py` using recording fakes: (a) with `expected=None` it compares against the baselines read at preview (CLI path), re-previews nothing; (b) order is `ports.reconcile` (only when `reconcile_planned`; NOT called when none planned) BEFORE `ports.commit_section()`; autocommit only INSIDE the section; (c) inside the section, in order: members present (vanished -> `observer.merge_member_missing(survivor, absorbed)` fires inside the section, returns `NotApplied("member_missing")`, writes nothing), `describe_drift` (raises `DriftDetected` naming the drifted targets and carrying the `surface` label), catalog recompose (`CatalogRecomposeError` becomes `DriftDetected`), `describe_read_drift` (raises `DriftDetected`); (d) the guard ORDER is drift, recompose, read-drift (curate's order, not `merge_concepts`'), proven by a case where two fire and the first one's message wins; (e) an `OSError`/`ValueError` from the write raises `WriteFailed("merge", subjects, error)` with the cause chained; (f) success returns `Applied(decision_key, MERGE, commit_sha)`; no `typer.Exit` anywhere. Must fail.
- [x] S1b.3 RED: token-path tests: `expected` given -> before any model call and before the section, re-preview from current bytes; a changed `content_digest` returns `Stale("content_changed")` with nothing written and `ports.reconcile` NOT called; inside the section the open row is re-read by `decision_key` (`curate_queue.open_row_by_key`): no open row -> `Stale("row_resolved")`; a different `payload_digest` -> `Stale("row_superseded")`; `expected.payload_digest is None` -> `Stale("row_resolved")`; a move between re-preview and section falls through to the ordinary guards and raises `DriftDetected`, never `Stale`. A different `DecisionPolicy` never causes `Stale` (the policy is not in the token). Must fail.
- [x] S1b.4 RED: `tests/unit/application/test_curate_queue.py` (or the existing queue test module; locate with `rg -l "read_open_rows" tests`): `open_row_by_key(layout, decision_key)` is read-only, returns the open row or `None`, and ignores resolved rows. Must fail.
- [x] S1b.5 GREEN: implement `MergeDecisionPreview`, `version_of`, `preview_merge` and `commit_merge_decision` in `src/openkos/application/curate_decisions.py`, and `open_row_by_key` in `src/openkos/application/curate_queue.py`, relocating the sequencing of `_identity_write_one` (locate by content in `src/openkos/cli/curate.py`: reconcile, section, presence, drift, catalog recompose, read-drift, `commit_merge`, autocommit). Every lifecycle call goes through the module attribute (`application_lifecycle.merge_core`, `merge_service.commit_merge`), never `from ... import`, because `tests/unit/cli/test_adjudicate.py` and `tests/unit/cli/test_curate_lock.py` patch those attributes. The new module defines no `*_core`, so the `queue_resolution` registry needs no entry. S1b.1-S1b.4 pass.

### Phase 2: Wire the CLI

- [x] S1b.6 Edit `src/openkos/cli/curate.py`: the identity walk calls `preview_merge` BEFORE its prompt (the preview lines render from `prepared`), renders unchanged, then on `yes` calls `commit_merge_decision(..., expected=None, surface="curate")`. Bind the reconcile port to `cli_main._apply_reconciliation(root, prepared, no_reconcile=ctx.no_reconcile, reconcile=ctx.reconcile, verb="curate")` so its notices print at the same point. The adapter maps `DriftDetected`/`WorkspaceBusyError` to exit 3 and ends the run, `WriteFailed` to `openkos curate: Identity: failed while merging A into B -- <err>.` and exit 1, `NotApplied("member_missing"|"not_mergeable"|"not_offered")` to a counted skip, and prints `_echo_commit_disclosure(sha, prefix="  ")` after the section has been released when `Applied.commit_sha is not None`. `ctx.partial_progress` stays in the stage engine.
- [x] S1b.7 Edit `src/openkos/cli/curate.py`: `_offer_accept_recommended` writes each merge through `commit_merge_decision` (it called `_identity_write_one`). Delete `_identity_write_one`. `--auto-merge` still goes through `application/auto_merge.py::apply_auto_merges`, untouched: one commit per run. Check: `rg -n "_identity_write_one" src tests` finds nothing.
- [x] S1b.8 Run the S0 goldens and matrix unchanged. All green with zero golden edits.

### Phase 3: Prove and gate

- [x] S1b.9 Mutation proofs (revert each with the inverse edit, purge `__pycache__`, re-run green): (a) swap the drift and catalog-recompose guards inside `commit_merge_decision`; the S0a.12 order golden and the S1b.2(d) unit test both fail. (b) Drop the in-section `observer.merge_member_missing` call; the S0a.9 golden fails on `stderr`. (c) Make the commit-phase `ports.reconcile` run inside the section; the S1b.2(b) unit test fails. (d) Mutate one patched line that `tests/unit/cli/test_curate_lock.py` relies on (the `openkos.application.lifecycle.merge_core` patch target in `commit_merge_decision`); a test in that file goes red, proving the monkeypatch site is live and not silently inert.
- [x] S1b.10 No-model proof: assert in a unit test that neither `preview_merge` nor `commit_merge_decision` constructs or calls an `LLMBackend`; only the injected reconcile port may.
  - Recorded deviations (S1b apply): (1) `preview_merge` takes a keyword-only `verdict` (the persisted judgment: `Verdict | None`) and an optional `survivor_criterion`. The design's signature carries only `group`, which has no verdict, so the `not_mergeable` rule needs it as an input; the CLI passes the walk's `result.verdict` and the criterion it already printed (`None` derives one via `ordered_merge_pair`, as accept-recommended does). (2) `MergeDecisionPreview` also carries `group`, `no_reconcile` and `reconcile`, the inputs that made it, so the token path can re-preview "from current bytes" inside `commit_merge_decision` without the caller re-supplying them. (3) `DecisionPorts` gains the `reconcile` port (default: leave the plan as previewed). (4) A `Stale("content_changed")` comparison builds the re-preview's `content_digest` with `payload_digest=None` (the digest does not depend on it). (5) `_identity_write_one` is replaced by the adapter helper `_commit_identity_merge` plus `_merge_failed_line`, because both the walk and accept-recommended need the same outcome-to-exit mapping and a copy in each would drift. (6) Two existing tests were edited, no golden or fixture: `test_curate_accept_recommended.py` spied on the deleted `_identity_write_one` (now spies on `curate_decisions.commit_merge_decision`), and `test_curation_services_seams.py::test_curate_drives_the_identity_merge_through_the_service` asserted `merge_service.commit_merge(` in the curate source (now asserts it in the surface and `commit_merge_decision(` in curate). (7) The `documents` lines of the content digest are redundant with `present` for every readable document; a direct test over `ReadDependencies.documents` pins them. (8) `open_row_by_key` tests live in a new `tests/unit/application/test_curate_queue.py` (no such file existed).
- [x] S1b.11 Gate: the five commands from "Conventions", unpiped. `git add` new files. Commit: `refactor(cli): route curate's identity merge through a two-phase preview and commit`.

---

## Slice S2 - PR 6: structure, relate (~300 lines)

Start: S1b merged. Finish: the Structure walk writes through
`preview_relate`/`commit_relate_decision`, including reversed. No deduplication
with `apply_relation_suggestions` (design correction 3). Out of scope: tier, list.

### Phase 1: Relate (RED first)

- [x] S2.1 RED: tests for `preview_relate(root, layout, suggestion, *, reverse, now)` and `RelateDecisionPreview`: returns `prepared`, `source_id`, `target_id` (already swapped when `reverse`), `relation_type`, `decision_key` (`pq.relation_type_key`, unordered, so reversal keeps the key); the pre-prepare endpoint-presence check returns `NotApplied("member_missing")`; `now` is taken from the caller once per stage (not read from a clock per call); writes nothing. `version_of` for a relate preview hashes `prepared.source_bytes` and `prepared.target_bytes`. Must fail.
- [x] S2.2 RED: tests for `commit_relate_decision(root, layout, preview, *, ports, observer, surface, expected=None)`: the in-section endpoint-presence check fires `observer.relate_endpoint_missing(source, target)` and returns `NotApplied("member_missing")`; drift raises `DriftDetected`; a write error raises `WriteFailed("relate", ...)`; success calls `application_lifecycle.relate_core` (module attribute) and returns `Applied` with the sha; the commit path and message are byte-identical to the current relate block; reversed resolves the row `applied` as modified, as proposed otherwise; the token path returns `Stale` for the same three reasons as S1b. Must fail.
- [x] S2.3 GREEN: implement `RelateDecisionPreview`, `preview_relate`, `commit_relate_decision` in `src/openkos/application/curate_decisions.py`, relocating the relate block of `_structure_run` (locate by content in `src/openkos/cli/curate.py`: endpoint presence x2, `prepare_relate`, the section, drift, `relate_core`, the autocommit and message). `suggest_relations_service` is not touched; the shared `prepare_relate`, `recompose_relate_log`, `relate_core` keep being shared.
- [x] S2.4 Edit `src/openkos/cli/curate.py`: `_structure_run` calls `preview_relate` AFTER its prompt (that is where `prepare_relate` runs today, because the prompt renders from the suggestion alone) and `commit_relate_decision`; prints `_echo_dropped_edge` for the pre-prepare drop where it prints today; prints the commit disclosure after the section; maps exits as in S1b.6 (`operation="relate"`). `a`, `r`, `n`, `s` handling and the `declined:` tally stay in the adapter.
- [x] S2.5 Run the S0 goldens and matrix unchanged; zero golden edits.
  - Recorded deviations (S2 apply): (1) `preview_relate` returns `NotApplied("not_offered")` for a suggestion with no valid type; the walk filters those out before the prompt, so it is a guard for a stateless caller. (2) `RelateDecisionPreview` also carries `suggestion`, `reverse`, `now` and an `action` property, the inputs that made it, so the token path can re-preview from current bytes (as `MergeDecisionPreview` does). (3) `version_of` accepts either preview type; a relate digest is `write\t<concept>.md\t<sha256>` lines over the source and, when a deprecated-status export is planned, the target; `log.md` and `now` are not part of it. (4) The `relate_core` late-binding proof (S2.6 d) targets `curate_decisions.commit_relate_decision`; an early-bound reference reddens `test_curate_lock.py::test_structure_types_and_asks_without_the_lock_then_relates_with_it` and the new unit spy test. (5) No existing test or golden was edited.

### Phase 2: Prove and gate

- [x] S2.6 Mutation proofs (revert, purge, re-run): (a) drop the reversal swap in `preview_relate`; the S0b.3 golden fails. (b) Change the relate commit-message literal; the S0b.1 golden fails on `commits`. (c) Drop the in-section `observer.relate_endpoint_missing` call; the S0b.4 golden fails on `stderr`. (d) One patched-line mutation for a `relate_core` patch target that `tests/unit/cli/test_curate_lock.py` uses; a test goes red.
- [x] S2.7 Gate: the five commands, unpiped. `git add` new files. Commit: `refactor(cli): route curate's structure relate through the curate-decisions surface`.

---

## Slice S3 - PR 7: metadata, set-tier (~300 lines)

Start: S2 merged. Finish: the Metadata walk writes through
`preview_set_tier`/`commit_tier_decision`; `_plan_config_commit` is split without
changing `set-volatility`. Out of scope: list, consent.

### Phase 1: Tier (RED first)

- [ ] S3.1 RED: tests for `preview_set_tier(layout, type_name, tier)` and `TierDecisionPreview` (`prepared`, `type_name`, `tier`, `decision_key`): writes nothing; `version_of` hashes `prepared.config_bytes`; the spec rule that a volatility token covers the proposed AND the currently effective tier is tested (the effective tier changed elsewhere -> a stale token returns `Stale("content_changed")` and `openkos.yaml` is unchanged). Must fail.
- [ ] S3.2 RED: tests for `commit_tier_decision(root, layout, preview, *, ports, surface, expected=None)`: `ports.plan_config_commit` returns a `ConfigCommitPlan` or a `ConfigCommitRefusal`; a refusal raises `Refused` carrying the adapter-composed text; drift raises `DriftDetected`; a write error raises `WriteFailed("set_tier", ...)`; success calls `application_lifecycle.set_volatility_core`, then `ports.autocommit_config_edit(root, message, plan)` with message `f"openkos: set-volatility {type} -> {tier}"`, and returns `Applied`; the sha is returned for the adapter to print. Must fail.
- [ ] S3.3 RED: in the `_plan_config_commit` test module (locate with `rg -n "_plan_config_commit" tests`): a new decision function returns the plan or the refusal text WITHOUT echoing or raising `typer.Exit`, and the existing wrapper still echoes and exits 1 exactly as before (the `set-volatility` verb tests are unchanged). Must fail.
- [ ] S3.4 GREEN: split `_plan_config_commit` in `src/openkos/cli/main.py` into the decision function (reads git, `openkos.vcs`, stays adapter-side) plus the existing wrapper, which keeps `set-volatility`'s behavior. Implement `TierDecisionPreview`, `preview_set_tier`, `commit_tier_decision` in `src/openkos/application/curate_decisions.py`, relocating the tier block of `_metadata_run`. `ConfigCommitPlan` stays opaque to the service.
- [ ] S3.5 Edit `src/openkos/cli/curate.py`: `_metadata_run` calls `preview_set_tier` AFTER its prompt and `commit_tier_decision`; binds `plan_config_commit` and `autocommit_config_edit` ports to `cli_main` through late-binding lambdas; maps `Refused` to the existing refusal text and exit 1, `DriftDetected` to exit 3. Sensitivity gaps remain report-only; Metadata still never writes sensitivity.
- [ ] S3.6 Run the S0 goldens, the matrix and the `set-volatility` verb tests unchanged; zero golden edits.

### Phase 2: Prove and gate

- [ ] S3.7 Mutation proofs (revert, purge, re-run): (a) change the tier commit-message literal; the S0b.6 golden fails on `commits`. (b) Change the dirty-config refusal text in the split decision function; the S0b.8 golden AND the `set-volatility` refusal test both fail, proving the wrapper still shares the text. (c) One patched-line mutation for a `set_volatility_core` patch target in the curate tests; a test goes red.
- [ ] S3.8 Gate: the five commands, unpiped. `git add` new files. Commit: `refactor(cli): route curate's metadata tier through the curate-decisions surface`.

---

## Slice S4a - PR 8: the list and the token apply (~480 lines)

Start: S3 merged. Finish: `list_open_decisions`, `preview_decision` and
`apply_decision` exist for the three actionable kinds plus action-less listing for
the other three; `row_is_fresh` is public; a boundary guard keeps `mcp/` and
`api/` away. The CLI does not call these. Out of scope: consent, wiring changes.

### Phase 1: The list (RED first)

- [ ] S4a.1 RED: promote `curate_queue._fresh` to the public `row_is_fresh` (same semantics; the old name stays as an alias for existing callers). Test in `tests/unit/application/test_curate_queue.py` (locate with `rg -l "_fresh" tests`): `row_is_fresh` and `_fresh` give identical results for identity, relation_type and volatility rows (volatility fresh means effective tier equal to `current_default`). Must fail: `row_is_fresh` absent.
- [ ] S4a.2 RED: `list_open_decisions(layout)` tests in `tests/unit/application/test_curate_decisions.py` on a temp workspace seeded through `pq.upsert_proposal`: all six kinds in `pq.KINDS` order then queue order; each `DecisionItem` carries `decision_key`, `kind`, `targets`, `fresh`, `evaluation`, `actions`, `withheld`, `resolves_via` (from `pending_queue_report.row_resolving_command`), `payload`, `payload_digest`; an empty queue returns `()` with no error; an unreachable model backend does not matter (no backend is constructed); nothing is written and nothing is printed (capture stdout/stderr). Must fail.
- [ ] S4a.3 RED: the action table, one test per row of design Decision 7: identity judged SAME / 2 members / same type -> `merge`, `keep_distinct`, `skip`, no withheld; `adjudication: null` -> `keep_distinct`, `skip`, merge withheld `pending_evaluation`; judged DIFFERENT and still open -> merge withheld `judged_different`; cross-type -> merge withheld `cross_type` with `resolves_via` naming `merge --include-cross-type`; more than 2 members -> merge withheld `more_than_two_members`; relation_type with a valid type -> `relate`, `skip` (plus `relate_reversed` only for an asymmetric type; a symmetric type does not offer it); a `supersedes` row has no actions and resolves via `openkos relate`; volatility with a valid tier -> `set_tier`, `skip`; `contradiction`, `revision`, `watch_refusal` -> no actions and `resolves_via` as the spec names (`openkos contradictions --decline`/`reconcile`/`unmerge`; the producer; `openkos ingest`). Must fail.
- [ ] S4a.4 RED: staleness: an open identity row whose member concept was edited is PRESENT and `fresh=False` (never hidden), using the same predicates the typed readers use. Must fail.
- [ ] S4a.5 RED: confidence: two identity items with different stored confidences list in an order independent of confidence, and no field labels either trusted or recommended; `payload` carries the stored confidence as is. Must fail.
- [ ] S4a.6 GREEN: implement `row_is_fresh` (promotion), `DecisionKind`, `WithheldAction`, `DecisionItem` and `list_open_decisions` in `src/openkos/application/curate_decisions.py` and `src/openkos/application/curate_queue.py`, reading through `curate_queue.read_open_rows`. LLM-free, lock-free, write-free. S4a.1-S4a.5 pass.

### Phase 2: Token apply (RED first)

- [ ] S4a.7 RED: `row_inputs_changed`: a stale open row whose inputs no longer pass `row_is_fresh` returns `Stale("row_inputs_changed")` from the commit paths of S1b/S2/S3, checked inside the section after `row_resolved` and `row_superseded`. Must fail.
- [ ] S4a.8 RED: `preview_decision(root, layout, decision_key, action, *, policy=DecisionPolicy())` returns the matching preview with its `DecisionVersion`; `DecisionPolicy` (`no_reconcile`, `reconcile`, `now`) defaults mean a caller holding only the token and the `decision_key` can commit; none of the three fields enters the token. Must fail.
- [ ] S4a.9 RED: `apply_decision(root, layout, version, action, *, policy=DecisionPolicy(), ports, observer, surface)`: re-lists the item by `decision_key`; a merge on an unjudged item returns `NotApplied("not_mergeable")` and writes nothing; any other action outside the item's `actions` returns `NotApplied("not_offered")`; `Action.SKIP` writes nothing and returns `NotApplied("skipped")`, leaving the row `pending`; otherwise re-previews and follows the token path of Decision 3. The member ids, relation endpoints and type, and proposed tier come from the open row's payload. A round trip commits and resolves the row; a stale token returns `Stale` for each of the four reasons and writes nothing (no concept, no queue row, no commit). Must fail.
- [ ] S4a.10 GREEN: implement the `row_inputs_changed` check, `DecisionPolicy`, `preview_decision` and `apply_decision` in `src/openkos/application/curate_decisions.py`. No model call other than the injected reconcile port (S4a.11 proves it). S4a.7-S4a.9 pass.
- [ ] S4a.11 No-judgment proof: tests that list and skip succeed with no model backend configured, that listing an identity item with a null adjudication leaves it pending evaluation and starts no judging, and an AST/grep guard that `curate_decisions.py` never references `LLMBackend` construction or `adjudicate_candidates`.

### Phase 3: Boundary guard and prove

- [ ] S4a.12 RED then GREEN: create `tests/unit/application/test_curate_decisions_boundary.py`: an AST scan asserting no module under `src/openkos/mcp/` or `src/openkos/api/` imports `openkos.application.curate_decisions`; it tolerates a missing `src/openkos/api/`. RED first: add a temporary offending import in `src/openkos/mcp/` and observe the guard fail; remove it and observe GREEN. Also assert the MCP `pending` tool returns no payload text for a confidential-derived row (spec scenario "MCP does not expose payloads"), against the existing reduced projection.
- [ ] S4a.13 Mutation proofs (revert, purge, re-run): (a) hide stale rows in `list_open_decisions`; S4a.4 fails. (b) Sort the list by confidence; S4a.5 fails. (c) Drop the `payload_digest` comparison in the in-section row re-read; the `row_superseded` test fails. (d) Offer `relate_reversed` for a symmetric type; S4a.3 fails.
- [ ] S4a.14 Run the S0 goldens and matrix unchanged (the CLI does not use this half, so zero golden edits are expected). Gate: the five commands, unpiped. `git add` new files. Commit: `feat(cli): list open curation decisions and apply one against a version token`.

---

## Slice S4b - PR 9: consent and bulk eligibility as data (~320 lines)

Start: S4a merged. Finish: `gate`, `run_curate`'s write refusal and `_confirm_item`
evaluate the consent policy from `curate_decisions`; behavior identical; the S0b
matrix passes unchanged. Out of scope: any new consent rule. Accept-recommended
and `--auto-merge` stay OUTSIDE the table.

### Phase 1: The table and the pure functions (RED first)

- [ ] S4b.1 RED: `BULK_ELIGIBILITY` pinned test: `identity` is `NEVER`; `relation_type` is `SYMMETRIC_ONLY`; `volatility` is `ALWAYS`; `contradiction`, `revision`, `watch_refusal` are `NEVER`; the mapping is read-only (`MappingProxyType`). Must fail.
- [ ] S4b.2 RED: `spend_consent(*, auto, interactive)`, `write_walk_consent(*, writes, interactive, accepted, unattended_identity)`, `item_consent(kind, *, accepted, asymmetric, interactive)`: a table test over every combination whose expected values are the OUTCOMES RECORDED BY THE S0b MATRIX (the pure-function twin). `interactive` is a thunk and a test asserts it is NOT called on branches that do not read the TTY today (for example `gate` under `--auto`). `item_consent("identity", ...)` returns `"ask"` for every input; an asymmetric relation never returns `"bulk"`. Must fail.
- [ ] S4b.3 GREEN: add `BulkEligibility`, `BULK_ELIGIBILITY`, `spend_consent`, `write_walk_consent` and `item_consent` to `src/openkos/application/curate_decisions.py`. Pure: the thunk is the only I/O. S4b.1-S4b.2 pass.

### Phase 2: Wire the CLI

- [ ] S4b.4 Edit `src/openkos/cli/curate.py`: `gate()` prints its disclosures unchanged, then branches on `spend_consent`; `run_curate`'s write refusal branches on `write_walk_consent`; `_confirm_item` branches on `item_consent`. The adapter supplies `interactive=lambda: sys.stdin.isatty()`.
- [ ] S4b.5 RED then GREEN: a pinned test that `Stage.auto_acceptable` equals `BULK_ELIGIBILITY[...] is not NEVER` for every stage; `Stage.auto_acceptable` stays a field (tests construct `Stage`); `_STAGES` derives it from the table for the three write stages. Prove RED first by flipping one table entry and observing the test fail.
- [ ] S4b.6 Run the S0b consent matrix and the S0 goldens unchanged; zero edits to the recorded matrix expectations.

### Phase 3: Prove and gate

- [ ] S4b.7 Mutation proofs (revert, purge, re-run): (a) set `BULK_ELIGIBILITY["identity"]` to `ALWAYS`; S4b.1, the matrix and the pure-function twin all fail. (b) Make `item_consent("identity", ...)` return `"bulk"` when `accepted`; the S4b.2 test AND the matrix identity row fail (the second, data-level barrier). (c) Read `interactive()` unconditionally in `spend_consent`; the thunk-not-called assertion fails.
- [ ] S4b.8 Confirm accept-recommended and `--auto-merge` do not reach the table: `rg -n "BULK_ELIGIBILITY|item_consent" src/openkos/cli/curate.py` shows no use inside `_offer_accept_recommended` or the auto-merge call path.
- [ ] S4b.9 Gate: the five commands, unpiped. `git add` new files. Commit: `refactor(cli): evaluate curate's consent policy from data`.

---

## Slice S5 - PR 10: archive (~150 lines)

Start: S4b merged and `main` up to date. Finish: the change is archived, the main
specs hold the merged deltas, ADR-0053 is Accepted. Out of scope: any code.

Do these in order, and verify on disk. A previous archive agent reported moving
every artifact when it had moved two; check the filesystem, not the report.

- [ ] S5.1 Verify (sdd-verify) against the Purpose paragraph of both specs and the proposal's Intent and Success Criteria, not only against ticked tasks: a passing spec is not a met goal, and a spec can overstate shipped code. Record the result in the verify report.
- [ ] S5.2 Name-match every delta heading, exactly. For the new capability, `openspec/specs/curate-decisions/spec.md` receives all 16 `### Requirement:` headings of `openspec/changes/curate-decisions-as-data/specs/curate-decisions/spec.md`. For `openspec/specs/curate-command/spec.md`, the four MODIFIED headings must each exist verbatim before the merge and be replaced in place: `Identity Stage Reuses Merge Cores`, `Structure Stage Writes Through The Relate Core`, `Metadata Stage Writes Tiers, Reports Sensitivity`, `Curate Reads And Resolves The Pending Queue`. The six consent requirements the delta names as untouched stay byte-identical. Check: for each heading run `rg -n -F "### Requirement: <name>" openspec/specs/...` and expect exactly one hit, and `rg -c "### Requirement:" openspec/specs/curate-decisions/spec.md` equals the delta's count. A prior change verified PASS with all of its delta requirements unmerged; this check is what catches that.
- [ ] S5.3 Confirm `openspec/specs/pending-work/spec.md` (read-only) needs no change: the list reads the queue without altering its contract. State that in the archive report.
- [ ] S5.4 Check the specs against the shipped code, requirement by requirement, in both directions: no statement the code does not do, and no behavior the spec omits. Special attention: "Each Kind Carries Its Own Action Set" (does `relate_reversed` really only appear for asymmetric types), "Consent And Bulk Eligibility Are Data" (the `relation_type` entry is `SYMMETRIC_ONLY`; accept-recommended and `--auto-merge` stay outside), "Apply Is A Two-Phase Preview..." (volatility token covers the proposed and the effective tier), and "Behavior Of The Command Is Unchanged". Correct the spec text, not the code, where they diverge, and note it.
- [ ] S5.5 Check the follow-up pointer: `docs/roadmap.md` names "Judging as an application service" as a required MVP 6 stage after this change and before the desktop review screen, and ADR-0053 points to it. Run `rg -n "Judging as an application service" docs/roadmap.md docs/adr/0053-curation-decisions-are-a-two-phase-application-surface.md openspec/changes/archive`. The archive report names it as the follow-up, together with the three other follow-ups from design Open Questions: recording Structure and Metadata declines; the relate-sequence duplication between `curate` and `suggest-relations --apply`; and a non-printing commit adapter for a future API caller.
- [ ] S5.6 Accept ADR-0053 in BOTH places: the frontmatter `status` field and the `**Status:**` line of `docs/adr/0053-curation-decisions-are-a-two-phase-application-surface.md`, and its status in `docs/adr/README.md`. Check: the ADR-status consistency test passes (status lives in three places; a hand-synced copy drifts).
- [ ] S5.7 `docs/architecture.md`: change it only if its `application/` listing names services and the general shape changed. No counts, no "since #N". If nothing names services, leave it and say so. Do not sync `docs/cli.md` (no CLI change).
- [ ] S5.8 Archive: move `openspec/changes/curate-decisions-as-data/` to `openspec/changes/archive/<date>-curate-decisions-as-data/`, including `exploration.md`, and write `archive-report.md`. Diff the folder listing against a sibling archive (for example `openspec/changes/archive/2026-09-08-lifecycle-application-service/`) and confirm the original folder is gone: `ls openspec/changes/curate-decisions-as-data` must fail.
- [ ] S5.9 Gate: the five commands, unpiped. `git add` new files. Commit: `docs(sdd): archive the curate-decisions-as-data change`. PR text references the change in prose, no `Closes`.
