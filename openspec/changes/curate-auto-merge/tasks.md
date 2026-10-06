# Tasks: curate-auto-merge — apply the measured structural Identity class without per-item consent, and offer "accept recommended"

Refs #1298. ADR-0034, ADR-0044; records ADR-0049 (written by slice 1). Design:
`design.md`. Proposal: `proposal.md`. Delta specs under `specs/`:
`identity-auto-merge` (new), `curate-command`, `entity-resolution-adjudication`,
`entity-resolution-merge`, `workspace-autocommit`, `llm-client`, `job-runtime`.
Binding ship rules: `evals/auto_merge/PREREGISTRATION-1298.md`.

Strict TDD is ON, runner `uv run pytest`. Every behavior task is a `[TEST]`
(observed RED, with the reason it is RED today), then the `[IMPL]` that turns it
GREEN, then a `[MUT]` that breaks the exact line the test guards and observes it
go RED. A `[TEST]` that is GREEN on its first run proves nothing until its
`[MUT]` is observed. Revert every mutation with the inverse edit (never
`git checkout --`), mutate the exact line (not a sibling), and purge
`__pycache__` before trusting any verdict. Fixtures run in `tmp_path`; no test
reaches an LLM (`OLLAMA_HOST=http://127.0.0.1:9`). Every eligibility guard
(model tag, digest, rubric, each window setting, the sampling pins,
confidential, LLM-blocked, in-class predicate, `t*`, one per survivor, the
stacked-body guardrail) has its own test, written so that mutating that one
guard alone turns it red, from an all-valid baseline with exactly one deviation.
Use `git add` on new files before any native review (untracked files are
excluded from the candidate).

Gate for every slice, run directly and unpiped (a pipe hides failures):
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`,
`uv run pytest --cov` (90% branch), and
`OLLAMA_HOST=http://127.0.0.1:9 uv run python evals/run_self_tests.py`.

Conventional Commits, scope per subsystem (`sdd`/`llm`/`cli`/`docs`/`okf`...).
No AI attribution. One slice = one PR; auto-chain, sequential, never parallel
(slices 1+2 share `application/auto_merge.py` and its test file; 3+4 share
`cli/curate.py`).

## Verified facts (recorded, not gates)

- **Rubric identity: VERIFIED MATCH.** `rubric_digest()` computed on the measured
  tree `63b5f551` and at HEAD both return
  `sha256:72d7c7cb794a6ee81478d3c51cfaf88c9067f390bcc15d62759f299885ad3483`;
  `git diff 63b5f551 HEAD -- src/openkos/resolution/adjudication.py src/openkos/llm`
  is empty. The class is NOT ineligible from day one. This replaces design D4's
  "compute and stop if it differs" gate; slice 1 only pins the constant.
- **Q1 resolved:** the sampling pins are one more run-eligibility guard. The
  class is ineligible if the adjudication call would run with a `temperature`
  or `seed` different from the measured run's (unpinned, the production
  defaults). Reported, never silent.
- **Q2 resolved:** confidential (and LLM-blocked) groups are excluded from the
  accept-recommended list (conservative reading; owner-revisable by one
  argument, `blocked=frozenset()`, plus its tests).
- **Q3 resolved:** without `--auto-merge`, cached in-class verdicts are not
  recommended, so default spend is unchanged.
- **Q4 resolved:** on a mid-run auto-pass failure, later stages still run and
  `curate` exits 1 at the end.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,710 total (430 / 420 / 400 / 340 / 120), advisory |
| 400-line budget risk | High (whole change); Medium per slice (slices 1 and 2 about 5-8% over) |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (seam) -> PR 2 (pass core) -> PR 3 (CLI wiring) -> PR 4 (accept-recommended) -> PR 5 (docs) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

Slice 1 and slice 2 are naturally a little over about 400 authored lines
(tests dominate). That is the advisory planning heuristic only; do not split
artificially, delete comments, or omit tests to fit it. Each PR merges to
`main` in order and rebases onto it after the previous squash (a stacked PR on a
squashed base goes DIRTY and gets no CI until rebased with `--onto main` and
force-pushed).

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Seam: class predicate moved, measured constants, run eligibility (incl. sampling pins), `InstalledModel.digest`, ADR-0049 | PR 1, base `main` | `uv run pytest tests/unit/application/test_auto_merge.py tests/unit/llm -q` | `OLLAMA_HOST=http://127.0.0.1:9 uv run python evals/auto_merge/run_structural_class.py --self-test` | revert PR 1: no behavior reaches a user; eval regains its own predicate |
| 2 | Pass core: judging partition, planner gates 1-6, `stacked_body_refused`, one-lock commit phase with gates 7-9, restore-on-failure, survivor-edit check | PR 2, base = PR 1 merged `main` | `uv run pytest tests/unit/application/test_auto_merge.py tests/unit/application -q -k "auto_merge or apply_same or unmerge"` | N/A: application-level, no CLI entry yet; proven against a real tmp bundle + git repo and real `unmerge` | revert PR 2: `auto_merge.py` returns to slice-1 surface; `lifecycle.preview_apply_same` returns to its inline guardrail |
| 3 | CLI wiring: `--auto-merge`, `--reconcile` refusal, probe parity, non-TTY exemption, pass call, disclosure, caveat, exit 1, daemon pin | PR 3, base = PR 2 merged `main` | `uv run pytest tests/unit/cli/test_curate_auto_merge.py tests/unit/cli/test_daemon.py -q` | non-TTY `openkos curate --auto --auto-merge` over the e2e fixture (stub judge) via `CliRunner` with a real git repo | revert PR 3: flag disappears, curate Identity returns to today's behavior |
| 4 | Accept-recommended: selector, `_identity_write_one` extraction, TTY pre-pass | PR 4, base = PR 3 merged `main` | `uv run pytest tests/unit/cli/test_curate_accept_recommended.py tests/unit/cli -q -k "identity or curate"` | TTY-simulated `curate` with scripted `y` / `N` input via `CliRunner` | revert PR 4: pre-pass gone, walk's write block inlined again |
| 5 | Docs: `docs/cli.md` entry, `CHANGELOG.md` | PR 5, base = PR 4 merged `main` | `uv run pytest tests/unit/test_adr_index.py -q` plus a `--help` diff by hand | `uv run openkos curate --help` compared with the docs entry | revert PR 5: docs only |

## Phase 1 — Slice 1: the seam (PR 1)

Owns: `src/openkos/application/auto_merge.py` (new),
`evals/auto_merge/run_structural_class.py`, `src/openkos/llm/base.py`,
`src/openkos/llm/ollama.py`,
`docs/adr/0049-structural-identity-class-merges-under-measured-constants.md`,
`docs/adr/README.md`, `tests/unit/application/test_auto_merge.py`,
`tests/unit/llm/test_ollama.py`, `tests/unit/llm/test_openai_compatible*.py`.
Branch `feat/1298-curate-auto-merge` (or one branch per slice per auto-chain).

- [x] 1.1 [TEST] In `tests/unit/application/test_auto_merge.py`, port the 13
  `_self_test_predicate()` cases from `evals/auto_merge/run_structural_class.py`
  (lines ~676-712) plus one boundary per clause to
  `auto_merge.in_structural_class`: LOW tier, ACRONYM tier, 3 members, mixed
  types, Event, Person, cross-type, `-a/-b`, `-1/-2`, `-2a`, other directory
  each exclude on their own; both member orders of base/`-N` admit; a group
  whose member files do not exist still returns in class without raising (spec
  "reads no verdict and no file"). RED: `ImportError`, module missing.
- [x] 1.2 [IMPL] Create `src/openkos/application/auto_merge.py` with
  `in_structural_class(group)` moved verbatim from
  `evals/auto_merge/run_structural_class.py:85-96` (imports limited to
  `resolution.candidates`, `resolution.normalize`,
  `application.ingest.ATTACH_EXCLUDED_TYPES`; no `openkos.cli`, no concrete
  `openkos.llm.*`, no `yaml`). Edit `evals/auto_merge/run_structural_class.py`
  to `from openkos.application.auto_merge import in_structural_class` and
  delete its own definition (name stays in its namespace so `exposure()` and
  `_self_test_predicate()` are unchanged). GREEN 1.1; run
  `OLLAMA_HOST=http://127.0.0.1:9 uv run python evals/auto_merge/run_structural_class.py --self-test`.
- [x] 1.3 [TEST] Spec "The eval harness uses the production predicate": assert
  `run_structural_class.in_structural_class is auto_merge.in_structural_class`
  and that the eval source defines no `def in_structural_class`. RED before
  1.2's edit is applied to the eval (write and observe RED against the old
  eval, then 1.2 turns it GREEN; if 1.2 already landed, [MUT] 1.4 proves it).
- [x] 1.4 [MUT] One mutation per clause of the predicate, each on its exact
  line: drop the HIGH-tier check; `== 2` to `>= 2`; drop the same-type check;
  drop the `ATTACH_EXCLUDED_TYPES` membership check; replace `is_suffix_family`
  with a looser relation. Each must turn a named 1.1 test RED. Also re-define
  the predicate in the eval and observe 1.3 RED. Revert by inverse edit, purge
  `__pycache__`.
- [x] 1.5 [TEST] `test_constants_match_the_committed_stamp` in
  `tests/unit/application/test_auto_merge.py`: load the committed
  `evals/auto_merge/results/runs-structural-calibration-20261005T164453Z-gemma4-26b-a4b.json`
  (read-only) and
  `evals/auto_merge/results/runs-structural-confirmation-20261005T170308Z-gemma4-26b-a4b.json`
  (read-only) and assert `MEASURED_MODEL`, `MEASURED_MODEL_DIGEST`,
  `MEASURED_AT_COMMIT`, `MEASURED_PROMPT_SHA256_16`, `MEASURED_CONTEXT_WINDOW`,
  `MEASURED_MAX_GENERATION_TOKENS` equal `stamp.model.name`/`digest`,
  `stamp.harness.commit`, `stamp.prompts[adjudication/system].sha256_16` and
  `settings` in BOTH arms; assert `T_STAR == 0.90` (verdict
  `evals/auto_merge/results/auto-merge-verdict-1298-20261005T181724Z-gemma4-26b-a4b.md`
  (read-only) states `t*: 0.9000`). Pin `MEASURED_RUBRIC_DIGEST ==
  rubric_digest()` and `MEASURED_PROMPT_SHA256_16 == prompt_hash(_SYSTEM_PROMPT)`
  with a failure message telling the editor that a rubric change invalidates the
  measurement. RED: constants missing.
- [x] 1.6 [IMPL] Add the `typing.Final` constants to `auto_merge.py`, each with
  a docstring citing its evidence (D3 table): `MEASURED_MODEL =
  "gemma4:26b-a4b"`, `MEASURED_MODEL_DIGEST =
  "001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c"`,
  `T_STAR = 0.90`, `MEASURED_AT_COMMIT =
  "63b5f551541f7e0e98c939331898f747e23f38da"`, `MEASURED_PROMPT_SHA256_16 =
  "aaed5c3e06569c83"`, `MEASURED_RUBRIC_DIGEST =
  "sha256:72d7c7cb794a6ee81478d3c51cfaf88c9067f390bcc15d62759f299885ad3483"`
  (verified fact above; no scratch-tree computation needed), 
  `MEASURED_CONTEXT_WINDOW = 12288`, `MEASURED_MAX_GENERATION_TOKENS = 8192`.
  GREEN 1.5.
- [x] 1.7 [MUT] Change each constant by one character (and `T_STAR` to 0.9001);
  each must turn 1.5 RED (stamp tie or rubric/prompt pin). Revert, purge.
- [x] 1.8 [TEST] `tests/unit/llm/test_ollama.py`: `list_models` returns
  `InstalledModel.digest` as the listed 64-hex string; a missing, `null`,
  empty-string or non-string `digest` yields `None` and does not abort the
  listing; an existing `InstalledModel(tag=..., family=...)` construction stays
  valid (default `None`). RED: `AttributeError`/`TypeError`, field missing.
- [x] 1.9 [IMPL] `src/openkos/llm/base.py`: add `digest: str | None = None` as
  the LAST field of `InstalledModel` (`llm/base.py:91-100`).
  `src/openkos/llm/ollama.py` (`list_models`, ~641-690): read `entry.get("digest")`,
  keep it only when a non-empty `str`, inside the existing parse `try`. GREEN 1.8.
- [x] 1.10 [TEST] `tests/unit/llm/test_openai_compatible*.py`: `list_models`
  yields entries whose `digest is None` (pin the no-change contract; spec
  "unknown digest"). Written GREEN on first run, so it is mutation-checked next.
- [x] 1.11 [MUT] (a) Make ollama keep a non-string digest as-is; (b) make it keep
  an empty string; (c) make openai-compatible stamp a fake digest in a scratch
  edit. Each must turn 1.8 or 1.10 RED. Revert, purge.
- [x] 1.12 [TEST] `static_ineligibility(cfg) -> tuple[str, ...]` in
  `tests/unit/application/test_auto_merge.py`, from an all-valid baseline `Config`
  (measured model, `context_window=12288`, `max_generation_tokens=8192`,
  sampling unpinned) that returns `()`; then ONE test per deviation, each the
  only failing fact: (a) `resolve_task_model(cfg, "adjudication")` differs
  (including `models: {adjudication: null}` falling back to a different
  `cfg.model`); (b) `context_window` is `None`; (c) `context_window` is another
  value; (d) `max_generation_tokens` is `None`; (e) another value;
  (f) a pinned `temperature` (Q1); (g) a pinned `seed` (Q1); (h) a different
  rubric digest (monkeypatch `auto_merge.rubric_digest` at the exact import site
  and assert the patch was hit); (i) an explicit key equal to the measured value
  stays eligible. Each reason names the check with expected and observed values
  (spec: "names every failed check"); a combined test with two deviations returns
  both reasons. Confirm the real config field names that feed
  `chat_client`'s `temperature`/`seed` at apply time. RED: function missing.
- [x] 1.13 [IMPL] `static_ineligibility(cfg)`: pure, no I/O, collects every
  failing static reason in order (model tag, `context_window`,
  `max_generation_tokens`, sampling pins, rubric digest). GREEN 1.12.
- [x] 1.14 [TEST] `model_digest_ineligibility(list_models, tag)` and
  `run_eligibility(cfg, list_models) -> RunEligibility`: baseline eligible;
  ONE test per reason from the baseline: `list_models` raises `BackendError`
  ("could not list installed models"); tag not listed; tag listed with
  `digest=None`; digest differs (shown as first 12 hex chars); the lookup uses
  exact tag equality (a longer sibling tag does not match). A `list_models` spy
  asserts it is NEVER called when any static reason exists, and called exactly
  once otherwise; reasons from static checks and digest combine when static
  passes. `RunEligibility.eligible` is `not reasons`. RED: functions missing.
- [x] 1.15 [IMPL] `model_digest_ineligibility`, `RunEligibility`,
  `run_eligibility` (static first, digest last, one `list_models` call). GREEN
  1.14.
- [x] 1.16 [MUT] One mutation per guard, each on its exact line: drop the model
  tag compare; drop `context_window`; drop `max_generation_tokens`; drop
  `temperature`; drop `seed`; drop the rubric compare; swallow `BackendError`
  as eligible; treat unlisted as eligible; treat `digest None` as eligible;
  compare digests with `startswith`; call `list_models` before the static
  checks. Each must turn exactly its named 1.12/1.14 test RED. Revert, purge.
- [x] 1.17 Write `docs/adr/0049-structural-identity-class-merges-under-measured-constants.md`
  from `docs/adr/template.md`, status `Proposed` in frontmatter AND the
  `**Status:**` body line, with `description`, date and timestamp. Content per
  design D12 (admitted class and per-group requirements; the D3 constants table
  verbatim plus the verdict file; ineligibility rules including the sampling
  pins; fresh verdicts only; the log-shape refinement of ADR-0034 decision 4;
  one lock across the run and model-free re-planning inside it as a refinement
  of ADR-0036 Decision One; accept-recommended on its own bars with
  confidential members excluded; never unattended; rejected alternatives).
  Add its row to `docs/adr/README.md`.
- [x] 1.18 [TEST] Run `uv run pytest tests/unit/test_adr_index.py -q` (ADR
  status is checked in more than one place) and
  `uv run pytest tests/unit/application/test_layering.py -q` (the new module
  imports no cli, concrete llm client, or yaml).
- [x] 1.19 Run the five-command slice gate (header). All green; record
  observed results.
- [ ] 1.20 Commit as separate work units (`feat(llm): add model digest to
  InstalledModel`, `feat(ingest): add structural class seam and run
  eligibility for auto-merge`, `docs(sdd): add ADR-0049`). Open PR 1 (`Refs #1298`, name the change in prose, no
  `Closes`). Wait for green CI on a branch up to date with `main`.

## Phase 2 — Slice 2: the pass core (PR 2)

Owns: `src/openkos/application/auto_merge.py`,
`src/openkos/application/lifecycle.py`,
`tests/unit/application/test_auto_merge.py`. No `cli/` file.
Depends on: slice 1 merged.

- [x] 2.1 [TEST] `judging_partition(groups, served_by_key, to_judge, *,
  auto_merge, interactive, statically_eligible)` in
  `tests/unit/application/test_auto_merge.py`, each case the only deviation
  from an eligible-interactive baseline: `auto_merge=False` returns inputs
  unchanged (today's serve); `statically_eligible=False` unchanged; eligible
  and interactive removes every in-class group from `served_by_key` and appends
  it to `to_judge` (cached row and queued row alike); eligible and non-interactive
  makes `to_judge` exactly the in-class groups (served or not) and leaves
  non-class groups neither judged nor served; a non-class served group is
  untouched in the interactive case. RED: function missing.
- [x] 2.2 [IMPL] `judging_partition`. GREEN 2.1.
- [x] 2.3 [MUT] Make `auto_merge` ignored; make `statically_eligible` ignored;
  stop removing from `served_by_key`; judge non-class groups too; drop the
  non-interactive branch. Each turns a named 2.1 test RED. Revert, purge.
- [x] 2.4 [TEST] `strict_blocked_members(bundle_dir)` with a tmp bundle:
  a confidential member, an unreadable file, an unparseable frontmatter, an
  absent sensitivity and a blank one are all blocked; a `private` and `public`
  member are not; assert it ignores `include_confidential` and
  `local_exemption` by calling `sensitivity.sensitive_concept_ids` with both
  off (spy on the exact call and assert the arguments). RED: missing.
- [x] 2.5 [IMPL] `strict_blocked_members` via
  `sensitivity.sensitive_concept_ids(bundle_dir)` with both escape hatches off
  (D7a). GREEN 2.4. [MUT] flip an escape hatch on; each of the blocked classes
  must be killed by its own assertion.
- [x] 2.6 [TEST] `plan_auto_merges(results, *, fresh_keys, blocked,
  cross_type_concern, ordered_pair)` on hand-built `AdjudicatedCandidate`s, each
  gate the ONLY failing fact from an all-pass baseline (which plans exactly one
  merge with survivor = base id): gate 1 a SAME verdict at 1.0 whose key is not
  in `fresh_keys` is skipped with reason `no fresh verdict this run` (kills
  "served verdict acted on"); gate 2 `different`/`uncertain` at 0.99 is not
  planned and not listed in `skipped`; gate 3 boundary: `0.90` plans and
  `0.8999` is skipped with `confidence 0.8999 below 0.90` (kills `>=` to `>`;
  also the T_STAR constant is the value used, not a literal); gate 4 a blocked
  member is skipped with the confidential/LLM-blocked reason, for a blocked
  survivor AND for a blocked absorbed member; gate 5 a non-`None`
  `cross_type_concern` is skipped with the concern text; gate 6 two hand-built
  overlapping groups (`foo`/`foo-2`, `foo`/`foo-3`) plan the first in candidate
  order and skip the second with `survivor already merged this run` (one per
  survivor); a three-member group and a non-class group never appear in
  `planned` or `skipped`; the first failing gate is the reported reason when two
  fail. RED: function missing.
- [x] 2.7 [IMPL] `AutoMergePlan`, `PlannedAutoMerge`, `AutoMergeSkip` and
  `plan_auto_merges` with gates 1-6 in the design's order. GREEN 2.6.
- [x] 2.8 [MUT] One mutation per gate on its exact line: skip the
  `fresh_keys` check; drop the SAME check; `>=` to `>`; make the threshold
  `0.8`; ignore `blocked` for the absorbed member only; ignore
  `cross_type_concern`; drop the survivor-already-planned set; admit non-class
  groups. Each must turn exactly its named 2.6 test RED. Revert, purge.
- [x] 2.9 [TEST] Extract the guardrail:
  `lifecycle.stacked_body_refused(prepared) -> bool` has direct tests (a plan
  that crosses the `lifecycle.py:3372-3375` threshold returns True, one just
  under returns False), and the existing `preview_apply_same` tests stay green
  AND a new test asserts `preview_apply_same` calls the shared predicate (spy
  at the exact call site, assert it was hit). RED: function missing.
- [x] 2.10 [IMPL] Extract the one-line predicate from `lifecycle.py:3372-3375`;
  `preview_apply_same` calls it. GREEN 2.9. [MUT] flip the comparison inside the
  predicate and observe both the new tests and the existing `apply-same`
  guardrail test RED; revert, purge.
- [x] 2.11 [TEST] `apply_auto_merges(root, layout, plan, *, commit_section,
  autocommit, judged_digests, digest_of)` happy path on a tmp bundle in a real
  git repo (pinned `GIT_CONFIG_COUNT` identity): three planned merges with
  distinct survivors; `commit_section` is a spy entered EXACTLY once; exactly
  one `autocommit` call whose paths are the ordered de-duplicated union of
  `merge_commit_paths(prepared, result)` (assert `index.md` and `log.md` appear
  once, each absorbed deletion appears once) and whose message is
  `openkos: auto-merge 3 pair(s) in the measured identity class` plus a blank
  line and one `merge <absorbed> into <survivor>` line per merge; zero applied
  merges make NO `autocommit` call; each merge keeps its own `log.md` bullet
  (three distinct bullets); the survivor body is the stacked body under
  `## Merged content (...)` and a chat stub that raises if called is never
  called (no reconcile). `AutoMergeRecord.survivor_after_sha256` equals
  `bundle_ledger.survivor_sha256` of the written survivor. RED: missing.
- [x] 2.12 [TEST] Threat-matrix commit-state RED tests (design): (a) a file the
  user staged before the run is NOT in the auto-merge commit and stays staged;
  (b) the absorbed files' deletions ARE in the commit; (c) duplicate paths
  appear once in the `autocommit` argument; (d) zero eligible merges create no
  commit; (e) a degraded `autocommit` (returns `None`: no repo or no identity)
  leaves the merges standing and `AutoMergeOutcome.sha is None`.
- [x] 2.13 [TEST] Gates 7-9 under the lock, each the only failing fact, with
  the real `prepare_one_merge`: (gate 7) a member edited after judging (its
  `digest_of` differs from `judged_digests`) is skipped with `changed since it
  was judged` and nothing is written for it; (gate 8) a member that no longer
  resolves (`prepare_one_merge` returns `None`) is skipped; (gate 9) a plan
  whose stacked body crosses the guardrail is skipped, nothing written, reason
  names the guardrail; a skipped merge does not stop the later ones; the second
  merge is re-planned against the bundle the first left (assert it succeeds
  where a pre-computed plan would read-drift: two merges whose links cross).
- [x] 2.14 [IMPL] `apply_auto_merges`, `AutoMergeRecord`, `AutoMergeOutcome`:
  one `with commit_section():`, per merge steps 7-12 (judged-content check,
  `prepare_one_merge` re-plan, `stacked_body_refused`, snapshot via
  `merge_drift_targets(layout, prepared, include_catalog=True)` plus the ledger
  sidecar's bytes or absence, `merge_core` with no reconciliation, record), then
  one `autocommit(root, union_paths, message)`. Match
  `judged_digests`/`digest_of` types to `application_pending.current_finding_digest`.
  GREEN 2.11-2.13.
- [x] 2.15 [MUT] Enter `commit_section` per merge instead of once; skip the
  gate-7 digest compare; skip the `None` check; skip the guardrail check;
  re-plan outside the lock (use a precomputed plan); call `autocommit` on zero
  merges; pass non-de-duplicated paths; omit the absorbed deletion from the
  union; call a reconcile in `merge_core`'s place. Each must turn its named test
  RED (assert every monkeypatch target was hit; an unread patch fails silently).
  Revert, purge.
- [x] 2.16 [TEST] Mid-run failure: with three planned merges, `merge_core`
  raising `OSError` (then `ValueError`) on merge 2 after a partial write
  (monkeypatch at the exact call target the pass imports, and assert the patch
  was hit): the tree equals the post-merge-1 state byte-for-byte (absorbed file
  recreated, ledger sidecar restored or absent as before); the single commit
  holds merge 1 only; merge 3 is never attempted; `failure` names pair 2 and the
  error, `restored=True`. A failure on merge 1 commits nothing. A failing
  restore (make the restore write raise) means NO commit, `restored=False`, and
  `unrestored_paths` lists every path left modified, with the landed merges
  reported uncommitted. A `WorkspaceBusyError` from `commit_section` propagates
  before anything is written.
- [x] 2.17 [IMPL] Snapshot restore and the failure branch of `apply_auto_merges`
  (D7 mid-run failure). GREEN 2.16. [MUT] skip the restore; commit anyway when
  restore fails; keep attempting merge 3; swallow the failure. Each RED.
- [x] 2.18 [TEST] Unmerge parity with the real `unmerge` core: after a run that
  auto-merged `foo` and `bar` in one commit, `unmerge foo` restores `foo` and its
  absorbed file byte-for-byte, removes only the `foo` merge's `log.md` bullet by
  exact text, and leaves the `bar` merge and its bullet untouched; repeat for
  `bar`. Written GREEN on first run, so mutate next.
- [x] 2.19 [MUT] Replace the per-merge `log.md` bullets with one run bullet;
  stack a reconciled body in place of the stacked body. Each must turn 2.11 or
  2.18 RED. Revert, purge.
- [x] 2.20 [TEST] `survivors_edited_since(layout, records)`: returns exactly the
  survivors whose current `survivor_sha256` differs from the recorded one, in
  record order; an unchanged survivor, a missing survivor file and an empty
  record list return nothing. RED: missing. [IMPL] implement it. GREEN.
  [MUT] compare against the wrong field; return all survivors; each RED.
- [x] 2.21 Confirm the adjudication store write replaces the row for a group key
  rather than adding one beside it (D8 write-back): add a test beside the
  existing adjudication-store tests if none states it, observed RED/GREEN as
  appropriate. Files: the existing adjudication-store test module.
- [x] 2.22 Run the five-command slice gate. All green; record observed results.
- [ ] 2.23 Commit work units (`refactor(graph): extract stacked-body guardrail
  predicate`, `feat(ingest): add auto-merge pass core`; scopes must come from
  the project list in AGENTS.md). Open PR 2
  (`Refs #1298`), CI green on a branch rebased onto `main`.

## Phase 3 — Slice 3: CLI wiring (PR 3)

Owns: `src/openkos/cli/curate.py`, `src/openkos/cli/main.py`,
`tests/unit/cli/test_curate_auto_merge.py` (new),
`tests/unit/cli/test_daemon.py`. Depends on: slice 2 merged.

- [x] 3.1 [TEST] `tests/unit/cli/test_curate_auto_merge.py`: `curate --reconcile
  --auto-merge` exits 2 with
  `openkos curate: --auto-merge merges mechanically and cannot be combined with --reconcile.`
  BEFORE any workspace read (a workspace-gate stub fails if called);
  `--no-reconcile --auto-merge` is accepted; `curate --help` names the class,
  the per-run opt-in, the one commit and the undo. RED: no such option (exit 2
  for the wrong reason: assert the exact message, not just the code).
- [x] 3.2 [IMPL] `src/openkos/cli/main.py::curate`: add `--auto-merge` (default
  `False`), the module-constant message beside `_RECONCILE_CONFLICT_MESSAGE`, the
  refusal next to the #803 refusal; `src/openkos/cli/curate.py`: add
  `CurateContext.auto_merge: bool = False` and
  `auto_merged: list[...] = field(default_factory=list, init=False)`. GREEN 3.1.
- [x] 3.3 [MUT] Drop the refusal; make the refusal run after the workspace read;
  exit 1 instead of 2; default the flag `True`. Each RED. Revert, purge.
- [x] 3.4 [TEST] Spend consent stays separate: `--auto-merge` alone on a non-TTY
  is declined with zero chat calls and nothing judged (stage declined, existing
  unattended hint); `--auto-merge` alone on a TTY still shows the cost prompt;
  `--auto --auto-merge` on a non-TTY passes the cost gate; without the flag,
  `--auto` alone on a non-TTY keeps today's Identity refusal (spec "Without the
  flag nothing is merged automatically"); `review: false` and `--accept identity`
  do not enable it. Chat-call counter stub proves "zero". RED: flag inert.
- [x] 3.5 [IMPL] Non-TTY exemption at `cli/curate.py:2610`: the condition gains
  `and not _unattended_identity(ctx, stage)` where `_unattended_identity` is
  `stage.name == "Identity" and ctx.auto_merge`; `accepted_stages` untouched.
  GREEN 3.4. [MUT] make the exemption unconditional (no `ctx.auto_merge`); make
  it apply to every stage; each RED.
- [x] 3.6 [TEST] Probe parity: the `_identity_probe` `llm_calls` equals the stub's
  actual chat-call count for each case: no flag; flag + eligible (in-class cached
  groups now counted fresh); flag + statically ineligible (counts as today);
  flag + eligible probe but digest mismatch at run time (run pays LESS than
  priced, never more). RED: probe ignores the flag.
- [x] 3.7 [IMPL] `_identity_probe` (`cli/curate.py:1005-1054`) calls
  `auto_merge.judging_partition(..., statically_eligible=static_ineligibility(cfg) == ())`
  so the cost line prices the run. GREEN 3.6. [MUT] ignore
  `statically_eligible`; pass `True` always; each RED.
- [x] 3.8 [TEST] Ineligible-run reporting: for each reason (model tag,
  `context_window`, `max_generation_tokens`, `temperature`/`seed`, digest
  mismatch, digest unknown on `openai-compatible`, listing failure) the stderr
  line is exactly
  `openkos curate: Identity: --auto-merge is not available this run -- <reason>[; <reason>...]; every group keeps its per-item prompt.`
  naming every failed check; a TTY then proceeds into today's walk; a non-TTY is
  `declined` with the existing unattended hint, nothing judged (zero chat
  calls); an eligible run prints no ineligibility line. RED: no line.
- [x] 3.9 [IMPL] `_identity_run`: read `interactive = sys.stdin.isatty()` once;
  build the `list_models` callable from
  `application_backends.diagnostics_client(cfg, model=tag, timeout=..., factories=ctx.backend_factories).list_models`;
  call `auto_merge.run_eligibility`; print the line; apply
  `judging_partition` with the full eligibility. GREEN 3.8. [MUT] swallow the
  reason; print only the first reason; run the pass anyway when ineligible; each
  RED.
- [x] 3.10 [TEST] The e2e (`CliRunner`, tmp workspace, real git repo with pinned
  `GIT_CONFIG_COUNT` identity, stub judge): `curate --auto --auto-merge` on a
  non-TTY with an in-class pair judged `same` at 0.95, an in-class pair at 0.85,
  an in-class homonym `different`, a confidential in-class pair, and a
  non-class pair: EXACTLY one new commit holding exactly the eligible merge;
  stdout has the header
  `openkos curate: Identity: merged <N> pair(s) automatically (measured class, gemma4:26b-a4b, confidence >= 0.90):`,
  one `  <absorbed> -> <survivor> (confidence 0.95) -- undo: openkos unmerge <survivor>`
  line per merge, then the shared commit-disclosure sentence only when
  `autocommit` returned a sha; every other pair is unmerged on disk; the
  not-merged in-class SAME groups each print
  `openkos curate: Identity: not merged automatically: <absorbed> -> <survivor> -- <reason>.`
  on stderr; the notice
  `applied <n> automatically; <m> in-class group(s) left for review -- run \`openkos curate\` on a terminal.`
  appears; `typer.prompt` is monkeypatched to raise and is never called (the
  patch is asserted hit by a TTY control case). Also: a cached `same` at 0.99 for
  an in-class pair whose fresh judgment is `uncertain` does not merge; a queued
  `same` row is not acted on; groups judged within a cap-truncated batch (#441)
  still merge their completed verdicts and the stage reports its failure.
  RED: nothing merges.
- [x] 3.11 [IMPL] `_identity_run` pass wiring: `fresh_keys` from `batch.results`;
  persist fresh in-class verdicts through `cli_main._persist_adjudications(...)`
  with the #1137 `judged_digests` pins; `blocked = strict_blocked_members(...)`;
  `plan_auto_merges(...)` with `lifecycle.cross_type_concern` and
  `lifecycle.ordered_merge_pair`; `apply_auto_merges(...)` with
  `commit_section` and `cli_main._autocommit`; append records to
  `ctx.auto_merged`; print the disclosure and skip lines; on a non-TTY stop
  after the pass (never enter the walk). GREEN 3.10. [MUT] act on a served
  verdict; skip the persisted write-back; enter the walk on a non-TTY; commit
  per merge; print no undo; each RED.
- [x] 3.12 [TEST] Failure semantics (Q4): an injected `apply_auto_merges`
  failure on merge 2 returns a `failed` `StageOutcome` and skips the walk, later
  stages (Structure, Metadata) still run, the summary and the derived refresh
  still print for what landed, and `curate` exits 1 at the end; a
  `WorkspaceBusyError` exits 3 before anything is written; the pass never exits 3
  on a changed member (it skips). RED: exit 0.
- [x] 3.13 [IMPL] `_identity_run` returns `failed`; `cli/main.py::curate` exits 1
  at the end when the auto pass failed mid-write. GREEN 3.12. [MUT] exit 0;
  stop the whole run at the failure; each RED.
- [x] 3.14 [TEST] Survivor-edit caveat: with Metadata/Structure editing an
  auto-merged survivor in the same run, the output includes
  `  note: <survivor> changed after its automatic merge; \`openkos unmerge <survivor>\` will refuse unless run with --discard-survivor-edits, which discards that later edit.`
  and is absent for an untouched survivor. RED: absent.
- [x] 3.15 [IMPL] `cli/main.py::curate` calls `auto_merge.survivors_edited_since`
  after `run_curate` returns. GREEN 3.14. [MUT] print for every survivor;
  never print; each RED.
- [x] 3.16 [TEST] Daemon pin in `tests/unit/cli/test_daemon.py`: (behavioural)
  the maintenance identity stage on an in-class fixture with a stub judge that
  answers `same` at 1.0 deletes no file, makes no merge commit and enqueues only
  identity rows; (structural) `cli/daemon.py` and `application/runner.py` do not
  import `openkos.application.auto_merge` (AST import check). Written GREEN on
  first run; mutate next.
- [x] 3.17 [MUT] Add `from openkos.application import auto_merge` to
  `cli/daemon.py` in a scratch edit and observe the structural pin RED; make the
  stub stage call `apply_auto_merges` and observe the behavioural pin RED.
  Revert, purge.
- [x] 3.18 Run the five-command slice gate. All green; record observed results.
  Also run `uv run pytest tests/unit/cli -q` for the full CLI suite (Typer help
  and command-classification guards).
- [ ] 3.19 Commit work units (`feat(cli): add curate --auto-merge`,
  `test(cli): pin the daemon never auto-merges`). Open PR 3 (`Refs #1298`), CI
  green on a rebased branch.

## Phase 4 — Slice 4: accept-recommended (PR 4)

Owns: `src/openkos/application/auto_merge.py` (selector only),
`src/openkos/cli/curate.py`,
`tests/unit/cli/test_curate_accept_recommended.py` (new),
`tests/unit/application/test_auto_merge.py` (selector cases).
Depends on: slice 3 merged.

- [x] 4.1 [TEST] `auto_merge.recommended(results, *, fresh_keys, blocked,
  cross_type_concern, ordered_pair, excluded_survivors)` each gate the only
  failing fact from an all-pass baseline: an in-class SAME at 0.60 IS
  recommended (gate 3 removed; kills a stray `T_STAR` check); a stale (not in
  `fresh_keys`) verdict is not; `different`/`uncertain` is not; a blocked member
  (confidential) is not (Q2); a cross-type concern is not; a survivor already
  merged by the auto pass (`excluded_survivors`) is not; two groups sharing a
  survivor recommend only the first; a three-member or Person pair is not; an
  ineligible run yields an empty set (spec). RED: function missing.
- [x] 4.2 [IMPL] `recommended(...)`. GREEN 4.1.
- [x] 4.3 [MUT] Re-add the confidence gate; drop `fresh_keys`; drop `blocked`
  (the Q2 revision point: also assert that passing `blocked=frozenset()` admits
  the group, proving the one-argument change); drop `excluded_survivors`; drop
  the one-per-survivor set. Each RED. Revert, purge.
- [x] 4.4 [TEST] Extraction guard: the existing per-item walk tests stay green
  (run them first as the characterization baseline), and a new test asserts the
  walk and the pre-pass both call `_identity_write_one` (spy at the exact call
  site, assert hit): reconciliation per the context flags, the commit-phase
  re-validation, `commit_merge`, and the per-item `_echo_commit_disclosure`;
  a drift refusal still exits 3.
- [x] 4.5 [IMPL] Extract `curate.py:1315-1386` into
  `_identity_write_one(ctx, prepared) -> bool`; the walk calls it. GREEN 4.4
  with no behavior change (existing walk tests unchanged).
- [x] 4.6 [TEST] `tests/unit/cli/test_curate_accept_recommended.py`
  (`CliRunner`, scripted input, TTY simulated, real git repo): the list shows
  only fresh in-class SAME groups at any confidence, one line each with
  absorbed, survivor, confidence and `undo: openkos unmerge <survivor>`, then
  `Accept all <n> recommended merge(s)? [y/N]`; `y` accepts every listed item
  and makes ONE COMMIT PER MERGE (N commits, #800), each with its per-item
  `_echo_commit_disclosure`; `N` and Enter send them all to the per-item walk
  with today's prompts; blocked, cross-type and guardrail-crossing groups are
  excluded (the guardrail applies when each item is prepared, a crossing item
  keeps its per-item prompt); a non-TTY never shows the pre-pass; without
  `--auto-merge`, served in-class verdicts are not recommended and the default
  cost line is unchanged (Q3); when `--auto-merge` already computed eligibility
  it is reused, and otherwise eligibility (the `/api/tags` read) is computed
  only when at least one fresh in-class SAME exists (spy asserts the read does
  not happen with zero candidates); an ineligible run offers nothing. RED:
  pre-pass absent.
- [x] 4.7 [IMPL] Wire the TTY pre-pass in `_identity_run` after the auto pass and
  before the walk; each accepted item goes through `_identity_write_one`. GREEN
  4.6.
- [x] 4.8 [MUT] Offer on a non-TTY; commit once for all items; accept on Enter;
  skip the guardrail at preparation; list a stale verdict; compute eligibility
  even with zero candidates; each RED. Revert, purge.
- [x] 4.9 Run the five-command slice gate. All green; record observed results.
- [x] 4.10 Commit work units (`refactor(cli): extract identity write helper`,
  `feat(cli): add accept-recommended to curate identity`). Open PR 4
  (`Refs #1298`), CI green on a rebased branch.

## Phase 5 — Slice 5: docs (PR 5)

Owns: `docs/cli.md`, `CHANGELOG.md`. Depends on: slice 4 merged.

- [x] 5.1 `docs/cli.md`: add the `--auto-merge` flag to the `curate` entry (the
  class, per-run opt-in, `--auto` is still the only spend consent, one commit,
  per-merge `log.md` bullets, undo with `openkos unmerge <survivor>` and the
  `--discard-survivor-edits` caveat, ineligibility reporting, refused with
  `--reconcile`, never in the daemon) and the accept-recommended Identity
  answer. State timelessly: no counts, no "since #NNN", no issue numbers; do not
  restate every constant (the ADR and `--help` own that).
- [x] 5.2 `CHANGELOG.md`: an Unreleased entry for `--auto-merge` and
  accept-recommended, plus `InstalledModel.digest`, in the file's existing
  format.
- [x] 5.3 Audit the docs against the code, not by re-reading them: diff
  `uv run openkos curate --help` against the `docs/cli.md` flag text; grep
  `src/openkos/templates` (including `openkos.yaml.template`) for any comment
  that teaches pre-change Identity behavior and fix it if it does; confirm no
  doc claims a flag or config key that does not exist (there is no config key).
- [x] 5.4 Run the five-command slice gate plus
  `uv run pytest tests/unit/test_adr_index.py -q`. Open PR 5 (`Refs #1298`),
  CI green on a rebased branch. Do NOT flip ADR-0049 to Accepted here:
  archive owns the status change (in both the frontmatter and the body line,
  plus the README row).

## Delta-spec merge reminder (archive, not apply)

At archive, name-match every requirement heading of the seven delta specs
against `openspec/specs/{domain}/spec.md` (deltas do not merge themselves and
headings drift), and check the shipped code against each requirement rather
than against this task list. The `sdd-archive` agent copies, it does not move:
diff against a sibling archive and check the disk for `exploration.md` and
`archive-report.md`.
