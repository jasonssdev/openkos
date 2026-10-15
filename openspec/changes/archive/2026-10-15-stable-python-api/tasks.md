# Tasks: A Stable Python API For The Review Queue

MVP 6 stage "Stable Python API" (v1 = the review queue). Inputs: `proposal.md`,
`design.md` (authoritative), `specs/stable-python-api/spec.md`,
`specs/pending-work/spec.md` and ADR-0060 (content in `design.md`, status
Proposed, written in S5). No tracking issue is assumed for the facade; issue
#1391 (refresh over persisted verdicts) is covered by S1..S3 and may be referenced
by number. No commit or PR text may use `Closes`, `Fixes` or `Resolves` unless
the issue number is repeated with the keyword per issue; a PR names the change in
prose ("the `stable-python-api` change") and uses `Refs #1391` where it applies.

Spec shorthand: `A1`..`A15` are the requirements of `stable-python-api`, in file
order (A1 public package declares exactly its public names; A2 `API_VERSION`;
A3 additive within a major; A4 golden snapshot; A5 import guard; A6 Workspace
handle; A7 not-found failure; A8 `OpenKosError` root; A9 owner-local, no gate;
A10 status and pending report; A11 list/preview/apply; A12 judging quote/run;
A13 default backend inside the API; A14 refresh exposed; A15 importable without
the CLI). `R1`..`R5` are the `pending-work` delta requirements in file order (R1
refreshed without a model; R2 lock only in commit sections; R3 amend, no stats
inflation; R4 retire only after a complete pass; R5 identity stage shared).

## Review Workload Forecast

Authored changed lines are additions plus deletions. The generated
`tests/unit/api/api_surface.json` is excluded from estimates (it is still part of
its PR). The ~400-line figure is an advisory heuristic only: no test or doc is cut
and no slice is split artificially to fit it. **S5 (~480) is an accepted,
justified exception**: the first public name must land together with the snapshot,
the classifier and the guards that make it a promise; splitting them would merge a
public package with no enforcement.

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,470 authored across 8 slices (snapshot JSON excluded) |
| 400-line budget risk | High in total; per slice below (only S5 over) |
| Chained PRs recommended | Yes |
| Suggested split | S1 -> S2 -> S3 -> S4 -> S5 -> S6a -> S6b -> S7 (8 PRs, merged in this order) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Per-slice forecast

| Slice | Title | Forecast (authored) | Depends on | Over ~400? |
|-------|-------|---------------------|------------|------------|
| S1 | Identity stage lift (#1391) | ~220 | none | No |
| S2 | Serving predicate, store-only builders, `enqueue_stored_*` | ~350 | none | No |
| S3 | `errors.py` leaf; `queue_refresh.py` | ~330 | S1, S2 | No |
| S4 | `vcs/autocommit.py`; CLI delegators | ~320 | none | No |
| S5 | ADR-0060; error root; `openkos.api` skeleton; snapshot, guards | ~480 | S3 | Yes, accepted exception (see above) |
| S6a | `preview_decision`, `apply_decision`, commit wiring | ~300 | S4, S5 | No |
| S6b | `quote_judging`, `run_judging`, chat wiring, refresh export | ~320 | S5, S6a | No |
| S7 | Docs, spec merge, ADR-0060 Accepted, archive | ~150 | S6b | No |

Total about 2,470 authored lines, 8 PRs. S1, S2 and S4 are independent of one
another and could merge in any order; the chain order above is the plan so each PR
opens against a settled `main`.

**Serialization (validator correction).** S6a and S6b both regenerate
`tests/unit/api/api_surface.json`, so they are **serialized**: S6b branches from
`main` after S6a is squash-merged (S6b does not merely depend on S5). S7 depends
on both S6a and S6b. They are never opened in parallel.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| S1 | Split the daemon identity stage into compute and commit halves | PR 1 (base `main`) | `uv run pytest tests/unit/application/test_identity_stage.py tests/unit/cli/test_daemon.py` | Daemon `_identity_stage` pass over a seeded `findings.db`; `pending --stats` before and after | Revert restores the inline daemon stage |
| S2 | Store-only builders and `enqueue_stored_*` | PR 2 (base `main`) | `uv run pytest tests/unit/application/test_stored_builders.py tests/unit/application/test_queue_producers.py` | Builder payload equals the daemon's `relation_proposals` payload for one stored row | Revert removes the builders; the partition keeps its old inline check |
| S3 | `OpenKosError` leaf and `refresh_decision_queue` service | PR 3 (base `main`) | `uv run pytest tests/unit/application/test_queue_refresh.py tests/unit/test_errors.py` | Refresh over a workspace with persisted verdicts of all three kinds, backend patched to fail | Revert removes refresh and `errors.py`; the queue fills only on daemon passes |
| S4 | Print-free git commit core, CLI delegators byte-identical | PR 4 (base `main`) | `uv run pytest tests/unit/vcs/test_autocommit.py tests/unit/cli/test_main_autocommit.py tests/unit/cli/test_config_commit_decision.py` | Real temp git repositories (nested, non-repo, pre-staged file) | Revert restores the CLI-local commit helpers |
| S5 | First public names with snapshot, classifier, guards, ADR-0060 | PR 5 (base `main`) | `uv run pytest tests/unit/api tests/unit/test_error_root.py tests/unit/application/test_curate_decisions_boundary.py` | `python -I -c "import openkos.api, sys; assert 'openkos.cli' not in sys.modules"` | Revert removes the facade; nothing in the engine imports it |
| S6a | Preview and apply with commit wiring | PR 6 (base `main`) | `uv run pytest tests/unit/api/test_decisions_apply.py tests/unit/api/test_api_surface.py` | Apply one of two decisions in a temp git workspace; non-repository workspace; nested repository | Revert removes `preview_decision`/`apply_decision` and `_wiring.py`; snapshot reverts with it |
| S6b | Judging, default chat wiring, refresh export | PR 7 (base `main`, after S6a merges) | `uv run pytest tests/unit/api/test_judging_api.py tests/unit/api/test_wiring_parity.py tests/unit/api/test_refresh_api.py tests/unit/api/test_api_surface.py` | Quote with a raising chat client; stop set inside the first chunk | Revert removes judging/refresh exports and the conftest binding entry |
| S7 | Docs shape, delta specs merged, ADR-0060 Accepted, archive | PR 8 (base `main`) | `uv run pytest tests/unit/docs` (ADR-status and index tests) | Name-match every spec heading against the main specs | Docs only; revert restores the unmerged deltas |

## Conventions For Every Slice

- **Branch and PR naming.** Branch `feat/<name>` (or `fix/<name>` for S2a-style
  fixes; none here) off `origin/main`. PR title is a Conventional Commit using the
  scopes from `AGENTS.md`. Per slice:

  | Slice | Branch | PR title |
  |-------|--------|----------|
  | S1 | `feat/identity-stage-lift` | `refactor(cli): share the identity stage between the daemon and refresh` |
  | S2 | `feat/stored-verdict-builders` | `feat(cli): build queue proposals from persisted relation and volatility verdicts` |
  | S3 | `feat/queue-refresh-service` | `feat(cli): refresh the decision queue from persisted verdicts without a model` |
  | S4 | `feat/print-free-autocommit` | `refactor(cli): lift the git auto-commit into a print-free vcs core` |
  | S5 | `feat/python-api-skeleton` | `feat(api): add the versioned openkos.api facade with its snapshot and guards` |
  | S6a | `feat/python-api-decisions-apply` | `feat(api): preview and apply a decision through the python api` |
  | S6b | `feat/python-api-judging-refresh` | `feat(api): quote and run judging and refresh the queue through the python api` |
  | S7 | `docs/python-api-archive` | `docs(sdd): archive the stable-python-api change and accept ADR-0060` |

  (S1..S3 use scope `cli` because the producers are consumed by the daemon and
  refresh, the subsystem touched; S5, S6a and S6b use `api`.)
- **Commits are work units**: each task closes with at least one Conventional
  Commit carrying its tests and docs. No `Co-Authored-By`, no AI attribution.
- **Locate every target by content**, never by a line number copied from
  `design.md` (`main.py:1458`, `daemon.py:274-289`, `curate_decisions.py:1037`
  and so on). They drift. Re-derive each range against the working tree.
- **Strict TDD** (`uv run pytest`): RED observed failing for the stated reason
  (an `ImportError` counts only for a test of a module that does not exist yet;
  any other RED must fail on an assertion), then GREEN, then REFACTOR. Record the
  command and the observed result. Characterization tests that must stay green by
  design are named as such.
- **Gates per slice**, each run directly and never piped (a pipe hides failures
  from `pytest` and `ruff format --check`), after the last change:
  1. `uv run ruff format --check .`
  2. `uv run ruff check .`
  3. `uv run mypy .` (the whole repo, not `src/`)
  4. `uv run pytest --cov > <scratchpad>/pytest-<slice>.log 2>&1` (90% branch
     coverage; read the log, do not pipe)
  5. `uv run python evals/run_self_tests.py`
  Do not rely on `pre-commit` alone; it misses what CI lints.
- **Mutation protocol** ("independent mutation" notes): make the mutation with an
  exact `Edit` on the one line the test pins, preferring a change in byte length;
  run only the test under proof and observe it FAIL for the expected assertion;
  revert with the inverse `Edit` (never `git checkout --`); purge bytecode with
  `find src tests -name __pycache__ -type d -prune -exec rm -rf {} +`; re-run to
  GREEN.
- **New files** are `git add`-ed before any review or diff-based check.
- **Snapshot regeneration** (S5, S6a, S6b): `uv run python -m tests.unit.api.surface
  --write`, in the same commit as the surface change; the classified diff it
  prints is pasted in the PR body. Because S6a and S6b both rewrite
  `tests/unit/api/api_surface.json`, they are serialized (S6b rebases onto `main`
  after S6a merges and regenerates, never hand-merges the JSON).
- **Delivery** is `auto-chain` with `stacked-to-main`: each slice is its own PR
  targeting `main`, or the previous PR branch while it is unmerged; PRs merge in
  order. Prefer opening slice N+1 only after slice N is squash-merged and green;
  if opened early on the previous branch, rebase `--onto main` past the old base
  after the squash (a stacked PR on a squashed base goes DIRTY and gets no CI) and
  confirm check-runs on the head SHA. Delete merged branches locally and remotely.
  Skills: `/Users/jasonssdev/.agents/skills/chained-pr/SKILL.md` and
  `/Users/jasonssdev/.agents/skills/work-unit-commits/SKILL.md`.
- **Design corrections the proposal's Affected Areas omit** (cited in the tasks
  that carry them): `vcs/autocommit.py` (new, S4), `errors.py` (new leaf, S3),
  `cli/main.py` delegators (S4), `lock.py` (gains `OpenKosError`, S5), and
  `tests/unit/conftest.py` (S6b). The proposal's own area list does not cover
  them; `design.md` "Corrections to the proposal" 1 and the File Changes table are
  authoritative.

---

## S1: Identity stage lift (~220 authored lines)

Goal: `application/identity_stage.py` owns `identity_report` and
`enqueue_identity_report`; the daemon's `_identity_stage` calls both halves with
today's exact order and behavior. Satisfies: R5 (and groundwork for A14).
Depends on: none.

- [x] S1.1 RED: create `tests/unit/application/test_identity_stage.py` (module
  missing, ImportError RED). Tests: `identity_report` drops a judged-different
  group and preserves `truncation_notice`; it never builds a client
  (`application.backends.chat_client` patched to fail); `enqueue_identity_report`
  carries a servable stored verdict and amends an open unjudged row
  (`ProducerResult.amended == 1`, `inserted == 0`); a second call is `unchanged`
  and `kind_stats` is identical. Satisfies: R5, R3.
- [x] S1.2 GREEN: create `src/openkos/application/identity_stage.py` with
  `identity_report(root, layout, *, find_candidates_report)` (LLM-free:
  `duplicates_service.report_duplicates` reached through the module attribute, then
  drop judged-different groups) and `enqueue_identity_report(conn, layout, report,
  *, commit_section, clock=None)` (`servable_stored_verdicts`, then
  `queue_producers.enqueue_identity`). No client is built in either half.
  Satisfies: R5.
- [x] S1.3 RED: in `tests/unit/cli/test_daemon.py` add one test that the truncation
  notice is announced when the enqueue raises `WorkspaceBusyError` (today's
  order: disclose, then enqueue). Observe it green on the current tree (it
  characterizes existing behavior) and keep it as the order pin. Satisfies: R5.
- [x] S1.4 GREEN: in `src/openkos/cli/daemon.py` rewrite `_identity_stage(ctx)` to
  call `identity_stage.identity_report(ctx.root, ctx.layout,
  find_candidates_report=lambda *a, **k: resolution.find_candidates_report(*a,
  **k))` (call-time lambda so the `openkos.resolution.find_candidates_report`
  patches keep intercepting), then `_disclose(ctx.notify, "identity",
  report.truncation_notice)`, then `enqueue_identity_report(ctx.queue(),
  ctx.layout, report, commit_section=ctx.commit_section)`; return `StageResult()`.
  Keep `uses_model=False` on the registration. Satisfies: R5.
- [x] S1.5 Widen `tests/unit/application/test_layering.py`:
  `test_application_directory_is_scanned_completely` asserts `identity_stage.py` is
  scanned (and gains `queue_refresh.py` in S3). Satisfies: A5.
- [x] S1.6 REFACTOR and survival: run the existing identity tests in
  `tests/unit/cli/test_daemon.py` (13 references to `_identity_stage`)
  unchanged and observe GREEN; confirm the patches of `report_duplicates` and
  `find_candidates_report` still intercept. Mutate the disclose/enqueue order in
  `_identity_stage` and observe the S1.3 test redden; revert, purge, GREEN.
  Satisfies: R5.
- [x] S1.7 Run the five gates; commit with `Refs #1391`. Satisfies: R5.

Branch/PR: `feat/identity-stage-lift`; commits `test(cli): ...`, `refactor(cli):
...`. Independent mutation: swap the disclose and enqueue lines in
`_identity_stage`; the S1.3 test must fail.

---

## S2: Serving predicate, store-only builders, `enqueue_stored_*` (~350 authored lines)

Goal: persisted edge suggestions and cached volatility answers become the same
proposals the producers publish, with no client. Satisfies: R1, R3, R4.
Depends on: none.

- [x] S2.1 RED: in `tests/unit/application/test_stored_builders.py` (new) test
  `servable_edge_suggestion(stored, edge, current_digest, *, include_confidential)`:
  refuses each failed check (missing endpoint digest, extra digest, changed digest,
  invalid type); with `include_confidential=None` the confidential bit is not
  compared. Observe RED (function missing). Satisfies: R1.
- [x] S2.2 GREEN: in `src/openkos/application/suggest_relations_service.py` extract
  `servable_edge_suggestion` from the per-row check of
  `partition_edge_suggestion_serves`; the partition calls it unchanged. Existing
  partition and suggest-relations characterization tests stay green unchanged.
  Satisfies: R1.
- [x] S2.3 RED then GREEN: `stored_edge_suggestions(layout)` (latest row per
  directed pair, `Edge(source_id, target_id)` untyped, `corrected_edge`
  reconstructed from the stored rationale). Test: payloads are byte-equal to the
  daemon's `relation_proposals` payload for the same stored row
  (`pq.Proposal.payload` equality); an edge typed, unlinked or forgotten since is
  not served. Files: `src/openkos/application/suggest_relations_service.py`,
  `tests/unit/application/test_stored_builders.py`. Satisfies: R1, R4.
- [x] S2.4 RED: the disjunction pin. In `tests/unit/application/test_stored_builders.py`
  assert that `sensitivity` treats `(include_confidential=True,
  local_exemption=False)` and `(False, True)` identically when planning with
  `volatility_typing.plan_types`. This must be green before the builder relies on
  it; if it is red, stop and report to the orchestrator (the design's Assumption
  to pin). Satisfies: R1.
- [x] S2.5 RED then GREEN: `stored_volatility_answers(layout, cfg)` in
  `src/openkos/application/suggest_volatility_service.py`. Plans both regimes with
  `plan_types`, reads `cached_answers` under the task model keyed by the exact
  prompt digest. Tests: a type answered in one regime is served; answered
  identically in both is served once; answered differently in both is skipped and
  counted ambiguous; another model's rows are ignored; a type equal to its current
  effective tier yields no proposal. Satisfies: R1.
- [x] S2.6 RED then GREEN: `enqueue_stored_relations(conn, suggestions, *,
  current_digest, bundle_dir, commit_section, clock=None)` and
  `enqueue_stored_volatility(conn, answers, *, bundle_dir, commit_section,
  clock=None)` in `src/openkos/application/queue_producers.py`, reusing
  `relation_suggestion_proposal` and `volatility_suggestion_proposal` and calling
  `publish(..., complete=False, amend_same_inputs=True)`. Tests in
  `tests/unit/application/test_queue_producers.py`: they never retire
  (`complete=False`); a changed payload over equal inputs amends; the daemon's
  `enqueue_relations`/`enqueue_volatility` are unchanged. Satisfies: R3, R4.
- [x] S2.7 Survival: existing partition and suggest-relations/volatility tests pass
  unchanged. Mutate the `include_confidential` comparison in
  `servable_edge_suggestion` (invert it) and observe both the partition tests and
  the new test redden; revert, purge, GREEN. Satisfies: R1.
- [x] S2.8 Run the five gates; commit with `Refs #1391`. Satisfies: R1, R3, R4.

Branch/PR: `feat/stored-verdict-builders`. Independent mutation: make
`enqueue_stored_volatility` pass `complete=True`; the "never retire" test must
fail.

---

## S3: `errors.py` leaf and `queue_refresh.py` (~330 authored lines)

Goal: a model-free `refresh_decision_queue` application service and the
`OpenKosError` root module. Satisfies: R1, R2, R3, R4, R5. Depends on: S1, S2.

- [x] S3.1 RED: create `tests/unit/test_errors.py`: `errors.py` imports nothing from
  `openkos` (AST walk). Observe RED (module missing). Satisfies: A8.
- [x] S3.2 GREEN: create `src/openkos/errors.py` with `class OpenKosError(Exception)`
  and its docstring (catch-compatible root). Satisfies: A8.
- [x] S3.3 RED: create `tests/unit/application/test_queue_refresh.py`. With a chat
  factory and `application.backends.chat_client` patched to fail: judged identity,
  relation and volatility verdicts become open rows; a second refresh leaves
  `kind_stats` identical for all three kinds; a changed member retires the old row
  as `stale` and inserts one; identity retires an absent open row on a complete
  pass; relation and volatility rows the pass did not see stay open;
  a `source-supersession/1` row is never retired; a truncated identity report
  retires nothing and reports `complete` false; a stored non-servable verdict (old
  rubric) is not enqueued; an absent queue with nothing to publish is not created;
  every file under `bundle/` and the verdict stores are byte-identical; two
  disagreeing-regime volatility types are skipped and counted in
  `volatility_ambiguous`; `QueueRefreshRefused` on an unreadable workspace with
  nothing written. Satisfies: R1, R3, R4.
- [x] S3.4 RED: lock tests in the same file: a recording commit section proves every
  write happened inside a section and the compute phase held none (a lock taken by
  another process via `lock.workspace_lock` during compute does not block it);
  a section that raises busy surfaces `WorkspaceBusyError` unchanged with
  already-upserted rows kept; the queue schema is created only inside one section.
  Satisfies: R2.
- [x] S3.5 GREEN: create `src/openkos/application/queue_refresh.py`:
  `QueueRefreshRefused(OpenKosError)`, `RefreshOutcome`, and
  `refresh_decision_queue(root, *, commit_section, clock=None,
  find_candidates_report=<call-time lambda>)`. Compute all three outcomes first
  (no lock, no client), return all-zero results without creating an absent queue,
  otherwise open the queue with `derived.open_derived_connection`, create the
  schema inside one `commit_section()`, publish identity, relation_type, then
  volatility. Imports `openkos.errors` (leaf) only for the refusal base; never
  imports client builders. Satisfies: R1, R2, R3, R4, R5.
- [x] S3.6 Layering: extend `test_application_directory_is_scanned_completely` in
  `tests/unit/application/test_layering.py` to assert `identity_stage.py` and
  `queue_refresh.py` are scanned (the glob already covers them). Satisfies: A5.
- [x] S3.7 Survival and mutation: run existing producer/daemon tests unchanged
  (GREEN). Mutate `complete=` in the relation publish to `True` and observe the
  "stays open" test redden; revert, purge, GREEN. Satisfies: R4.
- [x] S3.8 Run the five gates; commit with `Refs #1391`. Satisfies: R1..R5.

Branch/PR: `feat/queue-refresh-service`. Independent mutation: call `publish` with
`amend_same_inputs=False` for identity; the second-refresh stats test must fail.

---

## S4: `vcs/autocommit.py` and CLI delegators (~320 authored lines)

Goal: the git auto-commit core lives in `vcs/autocommit.py` with no printing; the
CLI keeps every name as a delegator printing byte-identical output. This slice
exists because of a design correction (proposal Affected Areas omit
`vcs/autocommit.py` and the `cli/main.py` delegators): `application/` may not
import `openkos.vcs` and the only existing implementation prints with
`typer.echo`. Satisfies: A11 (commit contract), threat matrix rows below.
Depends on: none.

**Allowed imports for `vcs/autocommit.py` (explicit).** It may import the standard
library, `openkos.vcs.git` (called through module attributes so existing patches
of `openkos.cli.main.vcs_git.<name>` keep intercepting), `openkos.config` and
`openkos.model.okf`. This is the first `vcs` module to import `openkos.config` and
`okf`. It must never import `openkos.state`, `openkos.retrieval`,
`openkos.graph`, `openkos.application`, `openkos.cli`, `openkos.mcp`, `typer` or
`rich`.

- [x] S4.1 Pre-flight check (no code): confirm `openkos.config` and `openkos.model.okf`
  do not import `openkos.vcs` (no cycle) and that `tests/unit/vcs/test_layering.py`
  (`test_canonical_layer_does_not_import_vcs`: model/bundle/state may not import
  `vcs`) and `tests/unit/resolution/test_layering.py` stay valid with `vcs`
  importing `config`/`okf`. Record in the PR body whether `plan_config_edit`
  (the `ConfigCommitPlan`/`ConfigEditConflict` logic, which needs `config` and
  `okf`) belongs in `vcs/autocommit.py` as the design assigns or outside `vcs/`
  (for example a config-side module). Default is the design's placement; do not
  redesign. If any layering guard rejects the placement, stop and raise it to the
  orchestrator instead of relocating silently. Satisfies: A5.
  Finding (applied): no cycle (`config`, `fsio`, `model.okf` import nothing from `vcs`);
  the `vcs` guards only bar `model`/`bundle`/`state` importing `vcs`, so
  `plan_config_edit` stays in `vcs/autocommit.py` as designed.
- [x] S4.2 RED: extend `tests/unit/vcs/test_layering.py` with an AST assertion that
  `src/openkos/vcs/autocommit.py` imports only the allowed set above and never
  `openkos.state`, `openkos.retrieval`, `openkos.graph`, `openkos.application`,
  `openkos.cli`, `openkos.mcp`, `typer` or `rich`. Observe RED (module missing).
  Satisfies: A5.
- [x] S4.3 RED: create `tests/unit/vcs/test_autocommit.py`. Threat-matrix rows
  (Git repository selection): (1) a workspace nested in an outer repository commits
  only the listed paths; (2) a non-repository returns `skipped="not_a_repository"`
  and writes no `.git`. (Commit state): (1) a pre-staged unrelated file is neither
  committed nor unstaged; (2) re-applying the current tier yields
  `skipped="nothing_to_commit"` and no commit; (3) user edits to another
  `type_tiers` entry stay uncommitted in the working tree; (4) an edit to the same
  entry is `ConfigEditConflict` and nothing is written. Plus: `commit_paths`
  returns `skipped="identity_unset"` and `"failed"` with `sha=None`;
  `includes_confidential` is true only for an explicit `confidential`;
  `plan_config_edit` returns `None` on a clean file and a plan on unrelated dirty
  edits. Satisfies: A11.
- [x] S4.4 RED: widen `test_shared_write_helpers_are_never_forked` in
  `tests/unit/application/test_layering.py` with `commit_paths`, `plan_config_edit`
  and `commit_config_edit` (one definition each, never under `application/`).
  Observe RED. Satisfies: A5.
  Deviation (applied): `vcs/git.py` already defines `commit_paths`, so "one
  definition" cannot hold for that name; the guard asserts exactly two homes
  (`vcs/git.py`, `vcs/autocommit.py`), never under `application/`, and one
  definition each for `plan_config_edit` and `commit_config_edit`.
- [x] S4.5 GREEN: create `src/openkos/vcs/autocommit.py` per the design: `CommitOutcome`,
  `paths_include_confidential`, `commit_paths(root, paths, message,
  *, has_confidential=paths_include_confidential)`, `ConfigCommitPlan`,
  `ConfigEditConflict`, `plan_config_edit`, `commit_config_edit`. Bodies are
  lifted from `cli/main.py` `_commit_has_confidential`, `_autocommit`,
  `_decide_config_commit`, `_autocommit_config_edit`; no printing; git is called
  through `vcs.git` module attributes; pathspecs are workspace-relative and literal;
  `git add -- <paths>` then `git commit -- <paths>`, never `-a`/`-A`; no `-C`
  override and no `Path.cwd()` use. Satisfies: A11.
- [x] S4.6 GREEN: in `src/openkos/cli/main.py` turn `_autocommit`,
  `_commit_has_confidential`, `_decide_config_commit`, `_autocommit_config_edit`
  and `_ConfigCommitPlan` (alias) into delegators that print the exact lines they
  print today from `CommitOutcome`; `_autocommit` passes
  `has_confidential=lambda r, p: _commit_has_confidential(r, p)` so a patch of
  that name keeps working. Locate the helpers by name, not by line. Satisfies: A15
  (CLI byte-identity).
- [x] S4.7 Byte-identity survival: `tests/unit/cli/test_main_autocommit.py`,
  `test_config_commit_decision.py`, `test_curate_decision_ports.py` and every CLI
  golden pass unchanged. Mutate one echoed line in a delegator and observe a test
  redden; revert, purge, GREEN. Satisfies: A15.
- [x] S4.8 Run the five gates; commit. Satisfies: A5, A11.

Branch/PR: `feat/print-free-autocommit`. Independent mutation: in `commit_paths`
replace `git add -- <paths>` with `git add -A`; the pre-staged-file row must fail.

---

## S5: ADR-0060, error root, `openkos.api` skeleton, snapshot and guards (~480 authored lines; accepted exception)

Goal: land the first public names together with the promise machinery. Satisfies:
A1, A2, A3, A4, A5, A6, A7, A8, A9, A10, A15 (partially), and the first half of
A11 (list). Depends on: S3 (S1, S2 transitively; S4 is not required here).

**Documentation window.** After S5 merges, the generalized boundary guard makes
`api` an allowed importer while `docs/architecture.md:229` still describes `api/`
as the future async local HTTP adapter. That statement is stale until S7's
rename (the planned adapter becomes "the local HTTP API", built on `openkos.api`).
Record the window in the S5 PR body; do not edit `docs/architecture.md` in S5
(shape docs change once, in S7).

- [x] S5.1 ADR-0060: copy `docs/adr/template.md` to
  `docs/adr/0060-the-stable-python-api-is-a-versioned-facade-with-one-error-root.md`;
  transcribe Context, Decision, Consequences and Alternatives from the "ADR-0060
  (content to transcribe)" section of `design.md` (read-only), status **Proposed**,
  with `description`, date and timestamp; add the row to `docs/adr/README.md`.
  Written now, while the forces are fresh. Satisfies: ADR gate, A1..A15.
- [x] S5.2 RED: `tests/unit/test_error_root.py`: for each class in design Decision 8
  (`BackendError`, `WriteRefused`, `JudgingRefused`, `SuggestionRefused`,
  `QueueUnavailableError`, `WorkspaceBusyError`, `WorkspaceLockUnavailableError`,
  `ListUsageError`, `ConceptNotFound`, `QueueRefreshRefused`), the previous MRO is
  a subsequence of the new MRO and `issubclass(cls, OpenKosError)`. Observe RED.
  Satisfies: A8.
- [x] S5.3 GREEN: add the `OpenKosError` base (existing bases kept, same relative
  order) in `src/openkos/llm/base.py`, `src/openkos/lock.py`,
  `src/openkos/application/write_gate.py`, `src/openkos/application/judging.py`,
  `src/openkos/application/suggest_relations_service.py`,
  `src/openkos/application/pending_queue_report.py`,
  `src/openkos/application/list_service.py`,
  `src/openkos/application/concept_read.py`. `lock.py` and `llm/base.py` import only
  the leaf `openkos.errors`; confirm the llm guard (bans only `config` imports)
  and the lock leaf rule still pass. Satisfies: A8.
- [x] S5.4 RED then GREEN: `curate_decisions` public readers. Tests in
  `tests/unit/application/test_curate_decisions.py` for `relation_fields`
  (promoted from `_relation_fields`), `identity_detail_fields` and
  `tier_detail_fields` (valid payload yields the tuple; unparsable yields `None`).
  Then implement in `src/openkos/application/curate_decisions.py`; keep
  `_relation_fields` callers working. Satisfies: A11.
- [x] S5.5 RED: `tests/unit/api/test_workspace.py`: `Workspace.open` on a root; on a
  non-workspace (`WorkspaceNotFound` naming the path, nothing created); on a nested
  directory (`WorkspaceNotFound`, no upward search); on an unreadable or invalid
  `openkos.yaml` (`WorkspaceUnreadable`); a symlinked workspace path is refused
  (path taken as given, `absolute()` not `resolve()`); `Workspace(root=p)` equals
  `Workspace.open(p)`; attribute assignment raises; a model change in
  `openkos.yaml` is observed through a held handle. Satisfies: A6, A7.
- [x] S5.6 GREEN: create `src/openkos/api/_workspace.py` (`Workspace`,
  `WorkspaceNotFound`, `WorkspaceUnreadable`, `_layout` set in `__post_init__` via
  `object.__setattr__`, config validated once and not cached for services) and
  `src/openkos/api/__init__.py` (docstring per the design's ordered statements,
  `API_VERSION: Final = 1`, sorted `__all__`, re-exports). Satisfies: A1, A2, A6,
  A7, A9.
- [x] S5.7 RED then GREEN: `tests/unit/api/test_reads.py` and
  `src/openkos/api/_reads.py`: `status` and `pending_report` under a held lock
  return, build no client (factories patched to fail), write nothing; an absent
  queue reports `queue_computed=False`; an unreadable queue raises
  `QueueUnavailableError`; result types hold counts, strings and API values only
  (no lint findings, surveys, queue rows or job records). Satisfies: A10.
- [x] S5.8 RED then GREEN: `tests/unit/api/test_list_decisions.py` and
  `src/openkos/api/_decisions.py` (list only): `list_decisions` returns typed
  `IdentityDetail`/`RelationDetail`/`TierDetail`, `detail=None` for a malformed
  payload (still listed), no confidence, no payload mapping, no lock, no client.
  Satisfies: A11.
- [x] S5.9 RED then GREEN: snapshot tooling. `tests/unit/api/surface.py` (generator
  with `--write`, evaluated annotations via `annotationlib.get_annotations`,
  canonical rendering, classified diff print), `tests/unit/api/test_api_surface.py`
  (completeness of `__all__`; leak check for any rendered `openkos.*`/`sqlite3.*`
  name; classifier unit tests on synthetic before/after dicts for every row of the
  additive/breaking table; the committed golden matches; breaking diff needs
  `API_VERSION` above the previous committed snapshot via `git show HEAD:<path>`,
  skipping that half with a message when git is unavailable). Generate
  `tests/unit/api/api_surface.json` (first snapshot; JSON excluded from line
  estimate) and `git add` it. Satisfies: A3, A4.
- [x] S5.10 RED then GREEN: `tests/unit/api/test_api_guards.py`: import allowlist
  (stdlib, `openkos.api.*`, `openkos.errors`, `openkos.config`, `openkos.lock`,
  `openkos.application.*`, `openkos.llm.base`; `_wiring.py` additionally the two
  backend client modules and `openkos.vcs.autocommit`), with named assertions for
  `openkos.cli`, `openkos.mcp`/`openkos.mcp.gate`, `typer`, `rich`, `asyncio`,
  `openkos.state`, `sqlite3`; nothing outside `api/` imports `openkos.api`; a fresh
  `subprocess.run([sys.executable, "-I", "-c", ...])` shows `openkos.cli` and `typer`
  absent from `sys.modules`. Satisfies: A5, A15.
- [x] S5.11 Generalize the owner-local boundary in
  `tests/unit/application/test_curate_decisions_boundary.py`: `_ADAPTERS = ("mcp",
  "api")` becomes a scan of every top-level package under `src/openkos/` except
  `application`, `cli` and `api`; keep the spelling matrix and tolerated-missing
  tests, retargeted at a temporary package named `localapi`. Confirm `mcp` is
  still barred. This is the change that makes the `docs/architecture.md:229`
  window real (see the note above). Satisfies: A5.
- [x] S5.12 Mutation proofs (each reverted byte-exactly, `__pycache__` purged):
  add a public name without `__all__` (completeness reddens); remove a dataclass
  field and regenerate without a bump (classifier reddens); annotate a public
  return with a `state.*` type (leak check reddens); add `import typer` to an API
  module (allowlist reddens). Satisfies: A1, A3, A4, A5.
- [x] S5.13 Run the five gates; commit (several work-unit commits: `docs(docs):`
  for the ADR, `feat(api):` for the facade). Satisfies: A1..A11.

Branch/PR: `feat/python-api-skeleton`. PR body states the S5 size exception, the
documentation window, and links ADR-0060 (Proposed). Independent mutation: drop
one name from `__all__`; the completeness test must fail.

---

## S6a: `preview_decision`, `apply_decision` and commit wiring (~300 authored lines)

Goal: preview and apply one decision, with the API's own commit wiring over
`vcs/autocommit.py`. Satisfies: A11, A5. Depends on: S4, S5. S6b depends on this
slice (serialized because both regenerate `tests/unit/api/api_surface.json`).

- [x] S6a.1 RED: `tests/unit/api/test_decisions_apply.py`: `preview_decision` writes
  nothing (bundle and queue byte-identical) and returns API plans (`MergePlan`,
  `RelatePlan`, `TierPlan`, `KeepDistinctPlan`) with no `PreparedMerge`; `apply`
  of one of two open decisions leaves the other open and the applied row
  `applied`; a member edited after preview returns `Stale` (value, not raised);
  a no-longer-open decision returns `NotApplied`; a tier decision with unrelated
  dirty `openkos.yaml` edits commits only the tier change; a non-repository
  workspace returns `Applied(commit_sha=None)` and calls `on_warning` once; a
  held lock with `wait_seconds=0` raises `WorkspaceBusyError` and writes nothing.
  Observe RED. Satisfies: A11.
- [x] S6a.2 RED (threat matrix, Git repository selection row 3): the API apply from a
  process whose cwd is a different repository commits in the workspace's
  repository, never the cwd's. Also assert the API never reads `Path.cwd()`
  (AST check in `tests/unit/api/test_api_guards.py`). Satisfies: A11, A5.
- [x] S6a.3 GREEN: extend `src/openkos/api/_decisions.py` with `DecisionPreview`,
  the four plan types, `preview_decision` and `apply_decision` (policy
  `DecisionPolicy(no_reconcile=True)` on both, `ApplyOutcome = Applied | NotApplied
  | Stale`, `wait_seconds` validated against `lock_wait.MAX_WAIT_SECONDS`).
  Satisfies: A11.
- [x] S6a.4 GREEN: create `src/openkos/api/_wiring.py` (commit section via
  `lock_wait.locked_commit_section(ws.root, wait_seconds=...)`; auto-commit port
  `autocommit=lambda root, paths, message: _commit(...)` over
  `vcs.autocommit.commit_paths`; `plan_config_commit` mapping `ConfigEditConflict`
  to `ConfigCommitRefusal(message)` with API wording; `autocommit_config_edit`
  over `commit_config_edit`; skipped commit reported through `on_warning` with API
  wording and `Applied.commit_sha=None`). The module imports
  `openkos.vcs.autocommit` (allowed by the S5 allowlist) and no printing code.
  Satisfies: A11, A5.
- [x] S6a.5 Regenerate `tests/unit/api/api_surface.json`
  (`uv run python -m tests.unit.api.surface --write`); the classified diff must be
  additive only and `API_VERSION` stays 1. Satisfies: A3, A4.
- [x] S6a.6 Mutation: in the apply path drop the `on_warning` call for a skipped
  commit; the non-repository test must redden. Revert, purge, GREEN. Satisfies: A11.
- [x] S6a.7 Run the five gates; commit. Satisfies: A11.

Branch/PR: `feat/python-api-decisions-apply`. Independent mutation: pass
`policy=None` (allow reconcile) to apply but not preview; the preview/apply
agreement test must fail.

---

## S6b: Judging, default chat wiring, refresh export (~320 authored lines)

Goal: `quote_judging`, `run_judging`, `refresh_decision_queue` through the API with
the API's own default chat client. Satisfies: A12, A13, A14, A15. Depends on: S5
and S6a (serialized: branch from `main` after S6a merges, then regenerate the
snapshot rather than merging the JSON).

- [x] S6b.1 RED: add `"openkos.api._wiring"` to `_BACKEND_BINDING_MODULES` in
  `tests/unit/conftest.py` first; a conftest binding test asserts
  `openkos.api._wiring` is patched. Observe RED (the module does not yet bind the
  concrete classes). Satisfies: A13.
- [x] S6b.2 RED: `tests/unit/api/test_judging_api.py`: a quote with a chat client whose
  `chat` fails the test returns a token; the quote builds a client and makes no
  call; an override factory is used and no default client is built (factories
  patched to fail); a moved model returns `QuoteStale` with no chat call (value,
  not raised); a run with no token returns `RunRefused`; a stop set inside the
  first chunk's chat ends the run `stopped` before the second chunk; an
  unreachable backend raises an instance of `BackendUnavailable`, `BackendError`
  and `OpenKosError` and is not a `QuoteStale`; a persistently busy workspace ends
  `status="unpersisted"` rather than raising. Satisfies: A12, A13.
- [x] S6b.3 RED: `tests/unit/api/test_wiring_parity.py`: for each `(backend, task)`,
  the default chat client has the same constructor kwargs as the CLI's
  `_chat_client` (recording factories patched into both `openkos.cli.main` and
  `openkos.api._wiring`). Also assert `BackendFactories(...)` holds class
  references so `test_backend_construction_guard.py` stays unconditional.
  Satisfies: A13.
- [x] S6b.4 RED: `tests/unit/api/test_refresh_api.py`: `refresh_decision_queue`
  through the API resolves no client (factories patched to fail), returns three
  `KindRefresh` entries, relation_type and volatility report `complete` false and
  zero retired, identity reports the truncation notice, `QueueRefreshRefused` for
  an unreadable workspace, `WorkspaceBusyError` for a held lock with
  `wait_seconds=0`. Satisfies: A14.
- [x] S6b.5 GREEN: create `src/openkos/api/_judging.py` (`ChatClient`,
  `ChatClientFactory`, `RunResult`, `quote_judging`, `run_judging` mapping the
  override onto `JudgingPorts.chat_client` as `lambda cfg, task: override(task)`)
  and `src/openkos/api/_refresh.py` (`KindRefresh`, `QueueRefresh`,
  `refresh_decision_queue`); extend `src/openkos/api/_wiring.py` with
  `_backend_factories()` (own globals, call-time) and the default chat-client
  lambda over `application.backends.chat_client`. Update `__init__.py` exports.
  Satisfies: A12, A13, A14.
- [x] S6b.6 Regenerate `tests/unit/api/api_surface.json`; the classified diff must be
  additive only; `API_VERSION == 1`. Confirm the fresh-interpreter test still
  shows `openkos.cli` absent after these imports. Satisfies: A3, A4, A15.
- [x] S6b.7 Mutation: change the default chat-client lambda to ignore the freshly
  read `cfg` (use a cached one); the "default follows the configuration" and
  `QuoteStale` tests must redden. Revert, purge, GREEN. Satisfies: A12, A13.
- [x] S6b.8 Run the five gates; commit. Satisfies: A12..A15.

Branch/PR: `feat/python-api-judging-refresh`. Independent mutation: build a client
inside `refresh_decision_queue`; the no-client test must fail.

---

## S7: Docs, spec merge, ADR-0060 accepted, archive (~150 authored lines)

Goal: close the change. Docs only; no source or snapshot edit. Satisfies: A1..A15,
R1..R5. Depends on: S6a and S6b (both merged).

- [x] S7.1 `docs/architecture.md`: add `api/`, `errors.py` and `vcs/autocommit.py` to
  the package tree; add `api` to the "each package is a piece of the architecture"
  sentence and `errors` to the leaf-module list; rewrite the stale `api/` future
  async adapter text (around the line cited as `docs/architecture.md:229`, which
  S5's boundary generalization left contradicting the code) so the planned async
  adapter is "the local HTTP API", built on `openkos.api`; add
  `tests/unit/api/test_api_guards.py` to the AST guard list. No counts, no
  "since #NNN". Satisfies: A1, A5.
- [x] S7.2 `docs/roadmap.md`: the MVP 6 "stable Python API" bullet states it exists
  for the review queue and that ingest, query, doctor and setup are later slices.
  `src/openkos/application/__init__.py` docstring: the package stays internal;
  the public edge is `openkos.api`. Satisfies: A1.
- [x] S7.3 Merge delta specs: `openspec/specs/pending-work/spec.md` (ADDED R1..R5 with
  every heading name-matched against the main spec; four had drifted in a previous
  change) and new `openspec/specs/stable-python-api/spec.md`. Satisfies: all.
- [x] S7.4 ADR-0060 status Proposed -> Accepted in all three places: the ADR file, the
  `docs/adr/README.md` row, and any status-keeping test input. Do not edit any
  other accepted ADR (append-only). Satisfies: A1..A15 (decision record).
- [x] S7.5 Check each requirement against shipped code, not the task list (a passing
  spec is not a met goal): read the spec Purposes and the issue text; list any
  requirement the code does not meet. Satisfies: all.
- [x] S7.6 Record open follow-ups in the archive report: the `insecure_key_warning`
  notice the API does not carry; whether the CLI later delegates to
  `api/_wiring.py`; the volatility two-regime walk cost not measured on large
  bundles; lint findings as counts only in v1.
- [x] S7.7 Archive: move (not copy) `openspec/changes/stable-python-api/` to
  `openspec/changes/archive/<date>-stable-python-api/`, including any
  exploration file, and write `archive-report.md`. Diff against a sibling archive
  and check the disk: the archive agent has overclaimed completion before.
- [x] S7.8 Run the five gates plus the ADR-status test. Satisfies: all.

Branch/PR: `docs/python-api-archive`. Independent mutation: set the ADR README row
to `Proposed`; the ADR-status test must fail. Rename one merged requirement
heading; the name-match check must fail.

---

## Coverage Matrix

| ID | Requirement | Tasks |
|----|-------------|-------|
| A1 | The Public Package Declares Exactly Its Public Names | S5.6, S5.9, S5.12, S7.1, S7.2 |
| A2 | `API_VERSION` Is An Integer Independent Of The Package Version | S5.6, S6b.6 |
| A3 | Changes Within A Major Are Additive | S5.9, S5.12, S6a.5, S6b.6 |
| A4 | A Golden Snapshot Pins The Public Surface | S5.9, S5.12, S6a.5, S6b.6 |
| A5 | An Import Guard Bars Interactive And Disclosing Layers | S1.5, S3.6, S4.1, S4.2, S4.4, S5.10, S5.11, S6a.2, S6a.4 |
| A6 | A Workspace Handle Resolves The Workspace Once | S5.5, S5.6 |
| A7 | A Missing Workspace Is A Typed Not-Found Failure | S5.5, S5.6 |
| A8 | `OpenKosError` Is The Single Catchable Root | S3.1, S3.2, S5.2, S5.3 |
| A9 | Owner-Local, No Disclosure Gate | S5.6 |
| A10 | Status And The Pending Report Are Readable | S5.7 |
| A11 | Decisions Can Be Listed, Previewed And Applied | S4.3, S4.5, S5.4, S5.8, S6a.1..S6a.4, S6a.6 |
| A12 | Judging Is Quoted, Then Run Against That Quote | S6b.2, S6b.5, S6b.7 |
| A13 | Judging Resolves Its Default Backend Inside The API | S6b.1, S6b.2, S6b.3, S6b.5, S6b.7 |
| A14 | Refresh Is Exposed As A Model-Free Operation | S6b.4, S6b.5 |
| A15 | Importable And Usable Without The CLI | S4.6, S4.7, S5.10, S6b.6 |
| R1 | The Decision Queue Can Be Refreshed Without A Model | S2.1..S2.5, S3.3, S3.5 |
| R2 | Refresh Holds The Lock Only Inside Commit Sections | S3.4, S3.5 |
| R3 | Refresh Amends Unchanged Inputs, No Stats Inflation | S1.1, S2.6, S3.3, S3.5 |
| R4 | Refresh Retires Only After A Complete Pass | S2.3, S2.6, S3.3, S3.5, S3.7 |
| R5 | The Identity Stage Is Shared | S1.1..S1.4, S1.6, S3.5 |

Threat matrix (design, applicable rows only): Git repository selection rows 1-2
(S4.3), row 3 (S6a.2); Commit state rows 1-4 (S4.3). Rows marked N/A in the design
are omitted.

Requirements left uncovered: none.

## Notes carried into apply

1. **Boundary-guard window.** Between S5 merging and S7 renaming the
   `docs/architecture.md` future-adapter text, the docs say `api/` is a future
   HTTP adapter while the code ships `openkos.api` as the Python API. Documented
   in the S5 PR body; closed in S7.1.
2. **vcs layering.** `vcs/autocommit.py` is the first `vcs` module importing
   `config` and `okf`; S4.1/S4.2 pin the allowed imports and add the assertion.
   Whether `plan_config_edit` should live outside `vcs/` is recorded as a task-level
   check (S4.1), not a redesign.
3. **Snapshot serialization.** S6a then S6b, never parallel.
4. **S5 size.** Accepted exception; no artificial split.
