# Apply Progress: judging-application-service

Mode: Strict TDD (characterization variant). Runner: `uv run pytest`.

## S0a: Spend-path characterization goldens (complete, 9/9)

Branch `feat/judging-s0a`, stacked-to-main, no source change.

Files: `tests/unit/cli/test_judging_characterization.py` (harness plus 13 golden
scenarios and 2 meta-tests) and `tests/unit/cli/fixtures/judging_characterization_goldens.json`
(recorded once, never edited by a later slice). Re-record only with
`RECORD_JUDGING_GOLDENS=1` on an unchanged tree.

### Tasks

- [x] S0a.1 Harness: git identity pinned through `GIT_CONFIG_COUNT`/`KEY_n`/`VALUE_n`,
  empty global config, `GIT_CONFIG_NOSYSTEM`, `OLLAMA_HOST` poisoned, frozen progress
  clock, `isatty` patched via `_simulate_tty`; `golden_support.Goldens`; judge and
  typing seams stubbed through the existing monkeypatch targets.
- [x] S0a.2 `identity_cost_line_served` (cost line `2 candidate group(s), 1 served -> 1 LLM call(s)`).
- [x] S0a.3 `identity_model_disclosure` (`this stage runs on '<model>'.` with a keyed
  `models:` entry). The default stream (S0a.2) also pins the unkeyed model line; the
  Structure streams pin the optional-upgrade line.
- [x] S0a.4 `identity_gate_declined` (real probes, `Proceed? [y/N]: n`).
- [x] S0a.5 `structure_partial_batch`, `metadata_partial_batch` (backend down mid-batch).
- [x] S0a.6 `adjudicate_persist_failure`, `adjudicate_persist_busy`,
  `curate_persist_failure`, `curate_persist_busy`.
- [x] S0a.7 `suggest_relations_persist_failure`, `suggest_relations_persist_busy`,
  `structure_persist_failure`, `structure_persist_busy`.
- [x] S0a.8 Recorded on the unchanged tree, then compared twice: 15 passed, 15 passed.
- [x] S0a.9 Fail-proof checks (below).

Every scenario pins stdout, stderr, exit code, commit subjects, `pending_items` rows,
`adjudications` rows (verdict, rationale) and `edge_suggestions` rows.

### TDD Cycle Evidence

| Task | RED (golden can fail) | GREEN | REFACTOR |
|------|-----------------------|-------|----------|
| S0a.1-S0a.8 | Characterization: green by construction; RED proven per golden in S0a.9 | `uv run pytest tests/unit/cli/test_judging_characterization.py`: 15 passed (twice) | ruff format/check and mypy clean |

### S0a.9 fail-proof checks

Each mutation is a length-changing edit to one source string, applied, the named test
run, then reverted byte-exactly and `__pycache__` purged. All 13 failed as expected.

| Golden (test) | Mutated line | Observed failure |
|---------------|--------------|------------------|
| identity_cost_line_served | `cli/curate.py` `cost_line` served f-string, extra space before `->` | `identity_cost_line_served: stderr` |
| identity_model_disclosure | `cli/curate.py` `model_notice` trailing `.` -> `!` | `identity_model_disclosure: stderr` |
| identity_gate_declined | `cli/curate.py` `notice="declined -- no LLM calls made."` -> `were made` | `identity_gate_declined: stdout` |
| structure_partial_batch | `cli/curate.py` `Already applied ... before the failure.` -> `before failure.` | `structure_partial_batch: stdout` |
| metadata_partial_batch | same line | `metadata_partial_batch: stdout` |
| adjudicate_persist_failure | `cli/main.py` `_persist_adjudications` `re-judge them` -> `judge them again` | `adjudicate_persist_failure: stderr` |
| curate_persist_failure | same line | `curate_persist_failure: stderr` |
| adjudicate_persist_busy | `cli/main.py` busy advisory `not persisted` -> `NOT persisted` | `adjudicate_persist_busy: stderr` |
| curate_persist_busy | same line | `curate_persist_busy: stderr` |
| suggest_relations_persist_failure | `application/suggest_relations_service.py` `re-type them` -> `type them again` | `suggest_relations_persist_failure: stderr` |
| structure_persist_failure | same line | `structure_persist_failure: stderr` |
| suggest_relations_persist_busy | same file, busy advisory `re-type them` -> `retype them` | `suggest_relations_persist_busy: stderr` |
| structure_persist_busy | same line | `structure_persist_busy: stderr` |

Afterwards `git status` showed no `src/` change and the file re-ran 15 passed.

### Observations

- The Metadata cost line reports `2 concept type(s), 1 served -> 1 LLM call(s)` when the
  typing stub returns one result; this is current behavior and is pinned as recorded.
- A busy workspace also skips the Structure queue enqueue (`structure_persist_busy`
  records no `pending_items` row; the failure variant records one).
- Pre-existing goldens already pin `suggest_relations` `persist_failure`; this file adds
  the busy variant and the `curate` surface so S1/S3 moves are covered on every surface.

### Deviations from design

None. No source file changed.

## S0b: Daemon-clobber strict xfail and UNCERTAIN pin (complete, 4/4)

Branch `feat/judging-s0b`, stacked-to-main, tests only (no `src/` change, no golden or
fixture edited).

Files: `tests/unit/cli/test_daemon.py` (fixture `judged_same_after_a_pass`, helper
`_require`, strict-xfail test `test_a_judged_same_row_survives_a_daemon_identity_pass`)
and `tests/unit/application/test_curate_decisions.py`
(`test_an_identity_judged_uncertain_is_listed_as_pending_evaluation`, parametrized
`judged` / `degraded`).

### Tasks

- [x] S0b.1 UNCERTAIN pin (confidence 0.5 and the 0.0 degrade shape): actions
  `(KEEP_DISTINCT, SKIP)`, merge withheld `pending_evaluation`, evaluation
  `pending_evaluation`.
- [x] S0b.2 Daemon-clobber RED as `xfail(strict=True, raises=AssertionError)`: XFAIL.
- [x] S0b.3 Setup errors are not absorbed (below).
- [x] S0b.4 S0a goldens unchanged and green; gates recorded in the slice report.

### TDD Cycle Evidence

| Task | RED | GREEN | REFACTOR |
|------|-----|-------|----------|
| S0b.2 | Marker removed: `AssertionError: assert None == {'verdict': 'same', 'confidence': 0.9, 'rationale': 'r'}` on the open row's `payload["adjudication"]` | Marker restored: XFAIL (strict). Real GREEN arrives in S2a | n/a |
| S0b.1 | Characterization; failure proven by perturbing `curate_decisions.py` else-branch to `judged_different` (2 failed), then reverted | 3 passed | n/a |

### Deviation from the design text (and why)

pytest applies an xfail marker to the SETUP phase as well, so an `AssertionError` raised
in a fixture is absorbed as XFAIL. A plain `assert` precondition therefore does NOT stay
outside the xfailed part (observed: a broken precondition reported XFAIL). Preconditions
go through `_require`, which calls `pytest.fail` (raises `Failed`, not `AssertionError`),
so `raises=AssertionError` cannot absorb it. Observed: stored verdict broken -> ERROR
`precondition failed: stored verdict is not SAME`; seeded verdict `None` -> ERROR
`precondition failed: open row does not carry the seeded SAME verdict`.

Note: the "chat stub raises" perturbation still reports XFAIL because `_identity_stage`
never calls the chat client; the never-called precondition is a recorder
(`chat_calls == []`), which would fail if the stage ever called it.

## S1: Identity serve, persist and reassemble into `application/` (complete, 8/8)

Branch `feat/judging-s1`, stacked-to-main on S0a and S0b. Behavior-preserving move; no
golden or fixture edited, the S0b strict xfail still reports XFAIL (S1 does not fix the
daemon).

### What moved where

| From `cli/main.py` | To `application/identity_adjudication.py` |
|---|---|
| `AdjudicationServes` | `AdjudicationServes` (main re-exports it by alias) |
| `_partition_adjudication_serves` | `partition_adjudication_serves` (`warn_on_failure` becomes `on_warning: Callable \| None`) |
| the serve rule inlined in the partition | `servable_verdict` (`include_confidential: bool \| None`, `None` = bit not compared), over a private `_serve` that also names the refusal so `rubric_stale` is counted without a second rule |
| `_reassemble_adjudications` | `reassemble_adjudications` |
| `_persist_adjudications` | `persist_adjudications` returning `PersistResult(persisted, dropped, store_error, busy)` |

Delegators kept in `cli/main.py` (names unchanged): `_partition_adjudication_serves`,
`_persist_adjudications`, `_reassemble_adjudications`, and the `AdjudicationServes`
alias. `_persist_adjudications` still resolves `_commit_section_for(layout.root)` at call
time and renders both advisories byte for byte; it still asks for no section when the
batch is empty. `queue_resolution.WRITE_CORE_RESOLUTIONS` now keys
`openkos.application.identity_adjudication.persist_adjudications`.

### Tasks

- [x] S1.1 RED: `tests/unit/application/test_identity_adjudication.py` (33 tests); RED was
  `ImportError: cannot import name 'identity_adjudication'`.
- [x] S1.2 Guard: the module's AST test (no `isatty`/`echo`/`print`/`input`, no
  `typer`/`rich`/`openkos.cli`/`openkos.vcs`) plus the existing `test_layering.py`.
- [x] S1.3 GREEN: the module, 33 passed. `write_through=True` raises
  `NotImplementedError` until S2c, rather than being silently ignored.
- [x] S1.4 Delegators in `cli/main.py`; unused imports removed (`NamedTuple`,
  `queue_resolution`, `rubric_digest`, `withdraw_self_refuting_same`; no test patches them).
- [x] S1.5 RED then GREEN: registry key (below).
- [x] S1.6 `test_chat_timeout_wiring.py` unaffected (the module makes no chat call); no
  widening needed.
- [x] S1.7 Survival proven (below).
- [x] S1.8 Gates recorded in the slice report.

### TDD Cycle Evidence

| Task | RED | GREEN |
|------|-----|-------|
| S1.1/S1.3 | collection `ImportError` (module missing) | 33 passed (one test rationale corrected: a self-refuting SAME is withdrawn to DIFFERENT, not UNCERTAIN) |
| S1.2 layering | temporary `import typer`, `import sys`, `typer.echo(...)`, `sys.stdin.isatty()` in the module: 3 failed (`test_application_modules_never_import_cli_typer_or_rich`, the two new AST tests); reverted byte-exact | 56 passed |
| S1.5 registry | new test `test_the_adjudication_persist_core_is_registered_at_its_application_home` failed with `KeyError`; after the move the old `openkos.cli.main._persist_adjudications` key also failed `test_a_resolving_core_calls_its_resolver` | key updated: all registry tests pass |
| delegators | `tests/unit/cli/test_identity_adjudication_delegators.py` (6) pins the section seam, the empty-batch no-section behavior and both advisories byte for byte | 6 passed |

### Survival evidence

Before the move: S0a goldens, `test_adjudicate.py`, `test_curate_auto_merge.py`, the two
queue-resolution tests: 260 passed. After: the same set plus the new tests, all green.

Mutations (applied, test run, reverted byte-exact, `__pycache__` purged):

| Mutation | Result |
|---|---|
| delegator `_commit_section_for(layout.root)` replaced by `lock_wait.locked_commit_section(...)` | SURVIVED the first run: no test patched that seam for the persist. Added the delegator tests; now 2 failed |
| delegator busy advisory `busy` -> `BUSY` | 3 failed (the delegator test and both S0a busy goldens) |
| moved `rubric_stale += 1` -> `+= 2` | 2 failed |
| moved `if stored.rubric_digest != rubric` -> `is None` | 3 failed |
| moved `dropped = len(results) - len(batch)` -> `0` | 3 failed |
| moved `if digest is None or (` -> `if (` | 3 failed |

### Deviations from the design text

- `servable_verdict` is a thin wrapper over a private `_serve` that returns the refusal
  kind, so the partition counts `rubric_stale` without a second copy of the rule.
- `PersistResult.persisted` is 0 on a store error or a busy workspace (nothing was
  written); `dropped` is reported on a store error and is 0 when the section was never
  entered.
- A new test file for the delegators was added (not in the task list) to close the
  surviving-mutation gap above.

## S2a: Amend-only write-through, daemon fix, `judged_uncertain` (complete, 11/11)

Branch `feat/judging-s2a`, stacked-to-main on S0a, S0b and S1. No golden or fixture
edited; the S0a goldens pass unchanged.

### Tasks

- [x] S2a.1/S2a.2 `pq.amend_open_proposal` and `_amend_open_in_transaction`
  (`UpsertOutcome.AMENDED`). An absent queue returns `no_open_row` before entering the
  commit section, so nothing is created (the `resolve_open_by_key` precedent).
- [x] S2a.3/S2a.4 `upsert_proposal(amend_same_inputs=False)` shares the helper.
- [x] S2a.5 `ProducerResult.amended` and `enqueue_identity(amend_same_inputs=True)`
  (`publish` gained the same keyword, default off, so no other kind changes).
- [x] S2a.6 `curate_queue.set_identity_verdicts` (returns the amended count).
- [x] S2a.7 `identity_adjudication.servable_stored_verdicts`.
- [x] S2a.8 Daemon `_identity_stage` carries it; the S0b strict xfail marker removed.
- [x] S2a.9 `judged_uncertain` on `DecisionItem.evaluation` and `WithheldAction.reason`.
- [x] S2a.10 The S0b.1 pin flipped (see below).
- [x] S2a.11 Mutations and gates (below, and the slice report).

### TDD Cycle Evidence

| Task | RED | GREEN |
|------|-----|-------|
| S2a.1/3 | 13 failed: `AttributeError: ... no attribute 'amend_open_proposal'`, `TypeError: upsert_proposal() got an unexpected keyword argument 'amend_same_inputs'` | 44 passed (one test fixed: an `applied` row's resolution is `as_proposed`) |
| S2a.5 | 2 failed: `AttributeError` on `result.amended` | 63 passed |
| S2a.7 | 11 failed: `AttributeError` (no `servable_stored_verdicts`) | 44 passed |
| S2a.8 | With the carry in place and the marker still on: `[XPASS(strict)] ... fixed by the judging-application-service change`, 1 failed. That strict XPASS is the RED. Marker then removed, assertion untouched | 65 passed. With the kwarg reverted to `adjudicated=()`: 5 failed (the S0b test and the 4 carry tests) |
| S2a.6 | 7 failed: `AttributeError` (no `set_identity_verdicts`) | 15 passed |
| S2a.9/10 | 2 failed: `pending_evaluation != judged_uncertain` on the flipped pin | 237 passed |

### Existing tests modified

- `tests/unit/cli/test_daemon.py::test_a_judged_same_row_survives_a_daemon_identity_pass`:
  only the `xfail` marker removed; the assertion is unchanged.
- `tests/unit/application/test_curate_decisions.py`: the S0b.1 pin
  `test_an_identity_judged_uncertain_is_listed_as_pending_evaluation` renamed and flipped
  to `judged_uncertain` (the curate-decisions delta: an UNCERTAIN verdict, malformed-reply
  degrade included, lists as `judged_uncertain`). The unjudged and unknown-verdict items
  still list `pending_evaluation`.
- `tests/unit/application/test_queue_producers.py::test_identity_adjudication_verdict_rides_in_the_payload_and_changes_the_digest`:
  asserted `result.replaced == 1` for an unjudged row that gains a verdict over unchanged
  inputs. Under the pending-work delta that is an amendment, so it now asserts
  `(amended, replaced, inserted) == (1, 0, 0)` and a single `pending` row.

### Mutations (applied, run, reverted from a byte copy, `__pycache__` purged)

| Mutation | Result |
|---|---|
| `_amend_open_in_transaction`: `if not _same_inputs(...)` -> `if False` | 2 failed |
| `amend_open_proposal`: no open row inserts and reports `amended` | 4 failed (the reopen and no-row tests) |
| `servable_stored_verdicts`: `include_confidential=None` -> `False` | 2 failed |

## S2b: `judging.py` binding, token, identity quote, notices (complete, 9/9)

Branch `feat/judging-s2b`, stacked-to-main on S0a, S0b, S1 and S2a. No golden or fixture
edited. The surface has no run yet (S2c).

### Tasks

- [x] S2b.1 Guards widened first: `_EXPECTED_SERVICE_TASKS` values are sets and expect
  `judging.py` to name `adjudication`; the boundary `_TARGET` is the tuple
  `("curate_decisions", "judging", "identity_adjudication")` with every spelling check run
  per surface; the `isatty` AST guard in `test_layering.py` is parametrized over
  `curate_decisions.py` and `judging.py`.
- [x] S2b.2 `JudgingKind`, `QuoteBinding` (with `digest()`), `WorkItem`, `QuoteToken`
  (with `digest()`, sorted and NUL-separated), `QuoteStale`, `QuoteNotice`, `JudgingQuote`,
  `JudgingPorts`, `JudgingObserver`, `JudgingProgress`, `JudgingRefused`.
- [x] S2b.3 `changed_fields(consented, current)` returns every moved field in a fixed order;
  the work set is not part of it.
- [x] S2b.4 `quote_judging(root, "identity", ...)`.
- [x] S2b.5 Zero-call group: in `work`, adds nothing to `calls` (spec wins over the design's
  `calls == len(work)`).
- [x] S2b.6 `include_uncertain`.
- [x] S2b.7 Notices: one `model`, `truncated`, `rubric_rejudge`, `unattended_active`.
- [x] S2b.8 Token stability and age (no clock in the token; a quote 400 days later binds the same).
- [x] S2b.9 Mutations, guard sanity, gates (below and in the slice report).

### TDD Cycle Evidence

| Task | RED | GREEN |
|------|-----|-------|
| S2b.1 | `test_the_extracted_verbs_still_resolve_their_own_task_model` and `test_the_owner_local_surfaces_never_read_a_terminal[judging.py]` failed with `FileNotFoundError: .../application/judging.py` | 79 passed after the module existed |
| S2b.1 boundary | temporary `from openkos.application import judging` appended to `mcp/tools.py`: `test_no_disclosing_adapter_imports_the_decision_surface[mcp]` failed (assertion); the same with `identity_adjudication`: failed. Reverted from a byte copy | 29 passed |
| S2b.9 isatty / import guards | temporary `sys.stdin.isatty()` in `judging.py`: `test_the_owner_local_surfaces_never_read_a_terminal[judging.py]` failed; temporary `import typer`: `test_application_modules_never_import_cli_typer_or_rich` failed. Reverted byte-exact | pass |
| S2b.2-S2b.8 | **Not observed RED.** The module was written before its tests, so the tests were green on first run. The substitute is the mutation table below: 16 single-line mutations, each killed | 57 passed |

### Mutations (applied, `test_judging.py` run, restored from a byte copy, `__pycache__` purged)

All 16 failed the suite: `wanted = fresh | uncertain` (unconditional) and `wanted = fresh`
(never); `model`, `backend`, `locality`, `confidential` and `rubric` comparisons each removed
or inverted; `calls = len(needed)` (zero-call groups priced); `include_uncertain` dropped from
the token digest; window comparison flipped; open-job test flipped; kept-distinct filter
removed; `served` counting uncertain; `rubric_rejudge` notice dropped; truncation `>` to `>=`;
unreadable digest `""` to `"x"`.

### Existing tests modified

- `tests/unit/cli/test_chat_timeout_wiring.py`: `_EXPECTED_SERVICE_TASKS` becomes set-valued
  and gains `judging.py` (the design's correction 6); the assertion compares sets.
- `tests/unit/application/test_curate_decisions_boundary.py`: `_TARGET` is a tuple; the
  scan matches any member; the spelling test is parametrized over each surface.
- `tests/unit/application/test_layering.py`: the `isatty` test is parametrized over two files.

### Deviations from the design text (and why)

- **Zero-call determination.** The leaf has no public predicate for it and S2b leaves the
  leaf unchanged. The quote runs `adjudicate_candidates` once, over the groups a run would
  judge, with a recording stand-in for the client that counts `chat` calls and returns an
  empty reply, and reads off which groups called it. The leaf's own short-circuit decides;
  no model is reached and nothing is written. Cost: one LLM-free sensitivity walk and one
  read per member for the quote.
- `QuoteNotice` gained `produced` (truncated only), so a client can render "N of M".
- `JudgingPorts` gained `now` (the unattended window needs a clock) and defaults every port
  except `chat_client` to the real implementation, as `SuggestRelationsPorts` does.
- The identity quote emits no `quarantined` notice: that is pass 3's edge-typing withholding
  and arrives with `relation_type` (S3a). The literal exists in the type.
- A quote of an unsupported kind raises `ValueError`; a non-workspace or unreadable
  configuration raises `JudgingRefused`.
- `QuoteBinding.digest()` exists alongside `QuoteToken.digest()`.
- `uncertain` counts servable UNCERTAIN verdicts whether or not the caller opted in; with
  the opt-in they are also in `to_judge`. `total == served + uncertain + fresh` always.
- A default client in the tests is non-local: a local client turns the exemption on and the
  effective confidential bit with it, so stored `include_confidential=False` verdicts would
  not serve.

## S2c: Identity run, retry, outcome, equivalence (complete, 13/13)

Branch `feat/judging-s2c`, stacked-to-main on S0a through S2b (all merged). No golden or
fixture edited; the S0a goldens pass unchanged. Strict TDD, test first for every
behavior below except the rows marked otherwise.

### API added (`application/judging.py`)

- `run_judging(root, token: QuoteToken | None, *, stop: StopToken, ports: JudgingPorts,
  observer: JudgingObserver | None = None, chunk_size: int | None = None) ->
  JudgingOutcome | QuoteStale | RunRefused`
- `JudgingOutcome(kind, status, judged, calls, persisted, unpersisted, unpersisted_cause,
  dropped, deferred, not_in_quote, settled_since_quote, failure=None, failed_at=None,
  observer_failed=False)`; `RunRefused(reason: Literal["no_quote_token"])`
- `IDENTITY_CHUNK_SIZE = 1`, `PERSIST_BACKOFF_SECONDS = (1.0, 2.0)`,
  `PERSIST_ATTEMPTS = len(PERSIST_BACKOFF_SECONDS) + 1` (3), both UNMEASURED (below)
- private: `_context`, `_partition_identity`, `_open_workspace` (the quote now uses them too),
  `_CeilingClient`, `_ObserverGuard`, `_persist_chunk`
- `identity_adjudication.persist_adjudications(..., write_through=True)` now writes each
  persisted SAME or UNCERTAIN verdict through `curate_queue.set_identity_verdicts` inside the
  one section (amend-only; the amendment is handed `contextlib.nullcontext` because the lock
  is not re-entrant).

### Tasks

- [x] S2c.1 Measured, constants kept UNMEASURED (below).
- [x] S2c.2 `RunRefused("no_quote_token")` before the config is read or a client built.
- [x] S2c.3 `QuoteStale` per field; moved work set and an old token are not stale.
- [x] S2c.4 Binding ceiling: `judge` / `not_in_quote` / `settled_since_quote` by
  `(work_key, member_digests)`; `_CeilingClient` refuses the call past `token.calls`.
- [x] S2c.5 Chunk loop, stop between chunks, `chunk_size` override (`>= 1`).
- [x] S2c.6 Write-through wired in `persist_adjudications` (the S1 `NotImplementedError` is gone).
- [x] S2c.7 Progress: run-wide index, `total = len(judge)`, verdict only.
- [x] S2c.8 Observer guard (`# noqa: BLE001`), `observer_failed`.
- [x] S2c.9 Bounded retry, `unpersisted` with `workspace_busy` / `store_error`.
- [x] S2c.10 Typed backend failures; `BackendUnavailable` / `BackendModelNotFound` re-raised.
- [x] S2c.11 Lock, daemon and silence guarantees.
- [x] S2c.12 `tests/unit/application/test_judging_equivalence.py`.
- [x] S2c.13 Module docstring states the stop latency; patched-line mutation; gates.

### TDD Cycle Evidence (RED observed before GREEN)

| Behavior (tasks) | Test(s) | Observed RED | GREEN |
|---|---|---|---|
| Write-through in persist (S2c.6) | 7 `test_write_through_*` in `test_identity_adjudication.py` | 7 failed: `NotImplementedError: write-through persistence arrives with the run` | 50 passed |
| Refusal and staleness (S2c.2, S2c.3) | `test_a_run_without_a_token_is_refused...`, `test_a_moved_binding_field_is_stale...[5 fields]`, `..._moved_onto_this_machine...`, `..._moved_work_set_alone...`, `..._old_token...`, `..._not_a_workspace_is_refused_by_a_run` | 10 failed: `AttributeError: module 'openkos.application.judging' has no attribute 'run_judging'` | with the next row |
| Ceiling, chunks, stop, queue effect, dropped (S2c.4, S2c.5, S2c.6) | the "binding ceiling", "chunks, persistence and cancellation" and "what a persisted verdict does to the queue" groups in `test_judging.py` | 28 failed in all (the 10 above plus 18), every one the same `AttributeError ... run_judging` | 85 passed. One test helper was wrong (`PendingItem` has no `key`; it keys on `targets`); fixed in the test, not a RED |
| Progress and observer guard (S2c.7, S2c.8) | `test_progress_*`, `test_an_observer_that_raises_loses_no_paid_work[1,2]` | 4 failed: `assert [] == [(1, 3), (2, 3), (3, 3)]`, `assert [] == [(1, 5), ...]`, `assert False is True` (`observer_failed`) | 90 passed. A ghost loop was found in the rationale test (`all()` over an empty list) and `assert len(...) == 5` added before it |
| Retry and `unpersisted` (S2c.9) | 4 tests in "a busy workspace" | 4 failed: `assert 'completed' == 'unpersisted'` (x2), `assert ('completed', 4, 0) == ('completed', 5, 0)`, `['chat', 'chat', ...] == ['chat', 'sleep', ...]` | 94 passed. mypy then asked for two restructurings (closure name shadowing a local, a possibly-undefined `result`), behavior unchanged |
| Backend errors (S2c.10) | 5 tests in "backend errors" | 6 failed: `Failed: DID NOT RAISE BackendUnavailable` (x2), `DID NOT RAISE BackendModelNotFound`, `assert 'completed' == 'failed'`, `assert None == 4`, `assert None is BackendGenerationCapped(...)` | 98 passed, 2 failed `[1, 2, 2] == [1, 2]` (a chunk that produced nothing still emitted `chunk_persisted`); fixed with `if batch.results:` -> 100 passed |
| Lock free during a call, engine active, silence (S2c.11) | 4 tests in "locks, the daemon and silence" | **Not observed RED**: written after the code that satisfies them. Mutation instead: the chat call wrapped in `with ports.commit_section():` fails `test_the_workspace_lock_is_free_during_every_model_call` and `test_no_section_is_ever_open_while_a_chat_call_runs` (2 failed); a `print` to stderr in the run fails `test_a_run_writes_nothing_to_a_stream` (AssertionError); a row inserted into `jobs.db` fails `test_an_active_engine_does_not_block_a_run_and_its_records_are_untouched` (AssertionError) | 104 passed |
| Equivalence (S2c.12) | 2 tests in `test_judging_equivalence.py` | **Not observed RED**: an integration pin over code already written, green first run. Mutation instead (below) | 2 passed |

### Mutations (applied, run, restored from a byte copy, `__pycache__` purged)

`test_judging.py` (first failure shown; `git status` clean afterwards):

| Mutation | Result |
|---|---|
| chat call wrapped in the commit section | `test_each_chunk_persists_before_the_next_chunk_is_judged` (and the two lock tests) fail |
| ceiling `>=` -> `>` | `test_a_call_past_the_ceiling_is_refused_before_it_is_made` fails |
| stop check -> `if False` | `test_a_stop_from_inside_a_call_lets_that_chunk_persist_and_defers_the_rest` fails |
| judge set iterates the current needed set instead of `token.work` | `test_new_work_is_not_judged_and_is_named` fails |
| `failed_at` chunk-relative | `test_a_failed_item_ends_the_run_with_its_prefix_kept` fails |
| backoff sleep removed | `test_a_transient_busy_workspace_is_absorbed_and_the_run_continues` fails |
| `write_through=False` | `test_a_run_amends_open_rows_declines_different_and_never_merges` fails |
| `not_in_quote` by work key only | `test_a_member_edited_since_the_quote_is_no_longer_the_quoted_item` fails |
| observer not silenced after its failure | `test_an_observer_that_raises_loses_no_paid_work[1]` fails |
| `None` token no longer refused | `test_a_run_without_a_token_is_refused_before_anything_is_built` fails |
| stale check -> `if False` | `test_a_moved_binding_field_is_stale...[model]` fails |
| only `BackendUnavailable` re-raised | `test_an_availability_error_propagates_after_the_prefix_is_persisted[error1]` fails |
| `chunk_persisted(result.persisted)` (not cumulative) | `test_progress_events_carry_the_run_index_and_total` fails |
| `deferred` ignores `failed_at` | `test_a_failed_item_ends_the_run_with_its_prefix_kept` fails |

`test_judging_equivalence.py`:

| Mutation | Result |
|---|---|
| service `write_through=False` | both tests fail |
| daemon `adjudicated=()` | both tests fail |
| DIFFERENT never resolved (`if False`) | the convergence test fails |
| service persists `include_confidential=False` | both tests fail |

Patched-line mutation (S2c.13): `cli/main.py` `_persist_adjudications` delegator
`include_confidential=include_confidential` -> `not include_confidential`: 3 failed
(`test_identity_model_disclosure_names_a_keyed_stage_model`,
`test_declining_the_gate_with_real_probes_spends_nothing` and a delegator test); restored from
a byte copy, 21 passed.

### S2c.1 measurement (not a tuning)

Script run over a temp workspace on macOS (60 persists of one chunk with write-through, real
`lock.workspace_lock`, `locked_commit_section`):

- persist lock hold: median 1.0 ms, p95 1.2 ms, max 4.3 ms (n=60);
- a contender waiting on a held lock acquired it 0.05 s to 0.09 s after the release
  (holders of 0.05, 0.25, 1.0 s; `acquire_with_backoff`'s own jittered backoff);
- `wait_seconds=0` against a held lock refuses in 0.4 ms.

That bounds this surface's own contribution to contention. It does not measure the thing the
retry is for: how long the daemon's or an ingest's commit section holds the lock (a git
autocommit runs inside it), and no such workload was run. So `PERSIST_ATTEMPTS` (3) and the
backoff (1.0 s, 2.0 s) stay UNMEASURED placeholders, labelled so in the constants' docstring and
the module docstring; no tuned value was invented. Follow-up: time the real contenders before
the app adapter picks its `wait_seconds`.

### Deviations from the design text (and why)

- **`JudgingOutcome.dropped` added.** Verdicts dropped on purpose (member edited, raised,
  forgotten or unreadable) are neither persisted nor unpersisted; without a field the counts do
  not close: `judged == persisted + dropped + unpersisted`.
- **`deferred` excludes the failed item.** A failed item was reached; `failed_at` names it and
  `deferred = len(judge) - failed_at`. For stopped and unpersisted runs it is
  `len(judge) - judged`.
- **Persist failure wins over a backend failure in one chunk.** With `chunk_size > 1` a chunk can
  hold results and a failure; if its persist cannot complete the outcome is `unpersisted` (with
  `failure` and `failed_at` still set) even for an availability error, so the lost paid work is
  reported instead of being hidden by a raise. At the default chunk size a failing chunk has no
  results, so this never arises.
- **The ceiling refuses the call, not the total.** `_CeilingClient` raises `AssertionError`
  before the call that would pass `token.calls`, rather than asserting at the end after the
  spend. Same exception, strictly earlier.
- **`chunk_persisted` fires only for a chunk that produced results**, with the cumulative
  persisted count.
- **`PERSIST_ATTEMPTS` is derived** (`len(PERSIST_BACKOFF_SECONDS) + 1`) so the two cannot
  disagree.
- **Refactor under green.** The quote's client/binding, workspace opening and partition moved
  into `_context`, `_open_workspace` and `_partition_identity` so the quote and the run derive
  them one way. The 57 S2b quote tests were unchanged and stayed green.
- **Equivalence walk answers `n`, not `s`.** The Identity walk reads both as a skip, but the
  other stages' `[y/N]` prompts accept only y/n and re-ask forever on `s` (the first run hung
  for that reason). Nothing is written to `bundle/` either way. The run side uses the quote's
  real leaf (so `calls` is priced) and the keyed `_Judge` stub only as the run's
  `adjudicate_candidates` port, so the stub never chats and the equivalence outcome has
  `calls == 0`; the ceiling itself is covered in `test_judging.py`.
- The chat-timeout AST guard needed no change: `judging.py` still names exactly `adjudication`
  (the one `chat_client` call is in `_context`).

### Existing tests modified

- `tests/unit/application/test_identity_adjudication.py`:
  `test_persist_write_through_is_not_available_until_the_run_exists` (asserted the S1
  `NotImplementedError`) is replaced by seven write-through tests. S1 declared that refusal as
  temporary ("until the run exists"); this slice is the run.
- No other existing test was edited. `test_judging.py` gained imports (`contextlib`, `json`,
  `lock`, `StopToken`, the leaf types and backend errors) and the run tests; its S2b tests are
  untouched.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/application/test_judging.py tests/unit/application/test_judging_equivalence.py tests/unit/application/test_identity_adjudication.py`: green |
| Runtime harness | the equivalence fixture: curate's real Identity stage in one copy, the service in the other, then one real daemon identity pass each |
| Rollback boundary | revert removes `run_judging` and its types and restores the `NotImplementedError` in `persist_adjudications`; the quote stays valid |

## S3a: `relation_type` on the protocol (complete, 7/7)

Branch `feat/judging-s3a`, stacked-to-main on S0a through S2c (all merged). No golden or
fixture edited; the S0a and `suggest-relations` goldens pass unchanged. No monkeypatched name
moved (nothing was renamed or relocated; the service module is untouched).

### API added (`application/judging.py`)

- `quote_judging(root, "relation_type", include_confidential=..., ports=...)` and
  `run_judging(root, token, ...)` now accept the kind. The signatures are unchanged.
- `RELATION_CHUNK_SIZE = 4`, `VOLATILITY_CHUNK_SIZE = 1` (measured, below); `_CHUNK_SIZES` maps
  each runnable kind to its default.
- `JudgingPorts` gained `open_proximity`, `build_graph`, `candidate_edges` and
  `suggest_edge_types`, the relations service's four seams, each defaulting to the real one.
- `QuoteNotice.text`: the relations service's own truncation and quarantine notice, carried as
  data for `relation_type` (the service already restricts them to what the caller may see and
  derives no count this surface could repeat).
- private: `_run_relations`, `_quote_relations`, `_walk_relations`, `_RelationsObserver`,
  `_relations_ports`, `_ChunkTyping`, `_RetryingSection`, `_classify_chunk`, `_progress_hook`,
  `_edge_item`, `_partition_relations`; the identity run moved unchanged into `_run_identity`.
- How it reuses the service: the quote asks `suggest_relations_service.suggest_relations` to
  price and its observer declines the cost question (no model, no write), reading the candidate
  edges off its candidate port; the counts are the service's own `partition_edge_suggestion_serves`
  (the same function) and a test pins them equal to the `CostQuote` the service states for the
  same state. A run re-invokes the service per chunk with `max_calls=chunk_size` and a candidate
  port narrowed to the remaining quoted edges; the service's own persist stores each chunk.

### S3a.1 measurement (LLM-free, instantly answering stub chat, macOS)

Per invocation, median of 5-7 runs; the script was run from the scratchpad and is not in the repo.

| Bundle (concepts / candidate edges) | Relations walk only (declined) | Relations, one chunk of 8 edges (persist) | Volatility, zero-call invocation | Volatility, chunk of 4 |
|---|---|---|---|---|
| good-life-demo (8 / 14) | 3.5 ms | 6.9 ms (chunk of 1: 4.3 ms) | 3.3 ms | 3.3 ms |
| synthetic 50 / 150 | 8.0 ms | 9.8 ms | 7.0 ms | 7.2 ms |
| synthetic 200 / 600 | 30.0 ms | 32.3 ms | 27.2 ms | 27.0 ms |
| synthetic 800 / 2400 | 121.8 ms | 126.4 ms | 109.0 ms | 109.7 ms |
| synthetic 800 / 2400, store holds all 2400 rows, serve partition on (`fresh=False`), 8 edges filtered | | 279.3 ms | | |

Steady state at 200 concepts is 42.5 ms and at 50 concepts 9.6 ms. The walk is linear in the
bundle (about 0.15 ms per concept), and the store read plus endpoint hash adds up to about 0.35
ms per concept at 800. Per-chunk invocation is therefore workable (not the STOP condition): the
overhead is two to three orders of magnitude under a model call and does not compound with the
run, only with the bundle.

Chosen: `RELATION_CHUNK_SIZE = 4` (placeholder was 8). The overhead criterion is at most 5 % of
a chunk's model time against a 5 s call floor at the largest measured bundle: that needs at least
2 edges (0.28 s over 10 s is 2.8 %); 4 leaves a margin for a bundle about 4x larger (1.4 % at
800 concepts) and halves the worst-case stop latency the placeholder 8 would have had.
`VOLATILITY_CHUNK_SIZE = 1` (placeholder was 4): the volatility service has no graph walk, a
workspace has a few dozen concept types at most, so one type per chunk is at most a few dozen
invocations (about 3 s of walk at 800 concepts) and gives the same latency as identity. Worst-case
stop latency: relation_type `4 x chat_timeout` (4 x 600 s by default, typically about 80 s at
20 s per call) plus one persist; volatility `1 x chat_timeout` plus one persist. S3b.4 carries
the volatility figure into the module docstring.

### Tasks

- [x] S3a.1 Measured; both constants fixed from the table (comments in `judging.py` only).
- [x] S3a.2 `_EXPECTED_SERVICE_TASKS["judging.py"]` is `{"adjudication", "edge_typing"}`.
- [x] S3a.3 The quote: kind-tagged, binds the `edge_typing` model, backend, locality and the
  effective confidential bit (rubric digest `None`), served versus to-judge from the shared
  partition, one `model` notice plus the carried `truncated` / `quarantined` / `unattended_active`.
  This also delivers the `quarantined` notice S2b deferred.
- [x] S3a.4 The run: ceiling, `not_in_quote`, `settled_since_quote`, `QuoteStale`, stop between
  chunks, progress, observer guard, bounded retry of a busy workspace, `unpersisted`, typed backend
  failures and `failed_at`, all with identity's semantics.
- [x] S3a.5 A stop during the first chunk persists it and defers the rest; a re-quote shows it served.
- [x] S3a.6 The `suggest-relations` goldens and tests are unchanged and green; one patched line
  (the service's busy advisory `re-type them` to `retype them`) reddens
  `test_suggest_relations_busy_workspace_costs_the_persist_an_advisory` and
  `test_structure_busy_workspace_costs_the_persist_an_advisory`; restored from a byte copy, 53 passed.
- [x] S3a.7 The five gates (below).

### TDD Cycle Evidence (RED observed before GREEN)

| Behavior (tasks) | Test(s) | Observed RED | GREEN |
|---|---|---|---|
| Guard widening (S3a.2) | `test_the_extracted_verbs_still_resolve_their_own_task_model` | `AssertionError`: `('judging.py', {'adjudication'})` against the widened `{"adjudication", "edge_typing"}` | passes once `_context` names `edge_typing` |
| The quote (S3a.3) | 15 `relation` / `two_directions` / `edited_endpoint` / `confidential_bit` quote tests in `test_judging.py` | 15 failed, each `TypeError: JudgingPorts.__init__() got an unexpected keyword argument 'candidate_edges'` | 120 passed |
| The run (S3a.4, S3a.5) | 35 relation run tests | 35 failed, each `ValueError: judging kind 'relation_type' cannot be run yet` (the no-token and `chunk_size < 1` tests passed by construction: both refuse before the kind check) | first GREEN attempt: 13 failed for a TEST defect, not a code one: the shared `_Judge` answers an identity verdict by default, so every edge degraded and was dropped. Fixed with `_edge_judge` in the test; 157 passed |
| `local backend serves a suggestion stored under the effective bit` | one test | **Not observed RED.** Written after a mutation (`effective=include_confidential` in the quote's partition) SURVIVED the first suite; with the test, the same mutation fails it | passes |
| `a run of a kind this stage does not run yet` | one test | **Not observed RED.** It covers the new kind check (a line the first coverage run reported uncovered); mutating the check to admit `volatility` fails it | passes |

Existing tests modified: see below. The `_Judge` helper gained a `default_reply` argument
(additive, defaults to the identity reply); `_seed_job` gained a union annotation.

### Mutations (applied, `test_judging.py` run, restored from a byte copy, `__pycache__` purged)

| Mutation | First failure |
|---|---|
| drop `max_calls=size` | `test_each_relation_chunk_persists_before_the_next_chunk_is_typed` |
| drop the remaining-edges filter (`if True`) | `test_a_relation_edge_added_since_the_quote_is_named_and_never_typed` |
| drop the pinned-digest check | `test_a_relation_verdict_about_content_edited_during_the_call_is_dropped` |
| `cause` always `store_error` | `test_a_persistently_busy_relation_workspace_stops_the_spend_and_says_so` |
| `vanished = 0` | `test_a_relation_stop_that_leaves_every_remaining_edge_settled_ends_the_run` |
| service handed the raw client instead of the ceiling client | `test_a_relation_call_past_the_ceiling_is_refused_before_it_is_made` |
| progress `before=0` | `test_relation_progress_events_carry_the_run_index_total_edge_and_type` |
| classification partition under `not effective` | relation run tests (7 failed) |
| retry sleep removed | `test_a_transient_busy_relation_workspace_is_absorbed` |
| `failed_at` chunk-relative | `test_a_relation_failed_at_is_the_run_position_not_the_chunk_position` |
| `chunk_persisted` fires on an empty chunk | `test_a_relation_availability_error_on_the_first_call_writes_nothing` |
| `deferred` ignores `vanished` | the same settled test |
| degraded result counted persisted | `test_a_relation_reply_that_degrades_is_judged_but_never_persisted` |
| `judge` iterates the current needed set instead of `token.work` | `test_a_relation_edge_added_since_the_quote_is_named_and_never_typed` |
| quote: served from `len(walk.edges)`, `calls` from the total, rubric always set, task always `adjudication`, effective bit from the request | each fails its quote test (the last survived until the local-backend pin was added) |

### Existing tests modified

- `tests/unit/application/test_judging.py::test_an_unsupported_kind_is_refused` used
  `relation_type` as its example of a kind a quote refuses. That kind is now supported, so it uses
  `volatility` (S3b will have to move it once more).
- `tests/unit/cli/test_chat_timeout_wiring.py`: `_EXPECTED_SERVICE_TASKS["judging.py"]` widened to
  `{"adjudication", "edge_typing"}` (task S3a.2; widened, not bypassed).
- No golden or fixture file was touched.

### Deviations from the design text (and why)

- **Chunk sizes changed from the placeholders** (8 and 4 to 4 and 1), as the measurement allows.
- **The quote's counts come from the shared serve partition, not the `CostQuote` object.** The
  service is asked to price and declines, which gives the walk, the candidate edges and the notices;
  the work set needs keys and digests the `CostQuote` does not carry, so the partition is called
  directly (the same function the service calls) and a test pins the counts equal to the service's
  `CostQuote` for the same state. A second store read happens in that window; a write between the
  two reads would be a quote no worse than one a moment later.
- **`QuoteNotice.text`** carries the service's rendered notice for `truncated` and `quarantined`
  rather than a count; the visible counts are derived through the sensitivity walk inside the
  service, and repeating that here would fork it.
- **`include_uncertain` is ignored for `relation_type`** and recorded `False` in the token (no
  uncertain state exists), so the flag cannot make two tokens differ for no reason.
- **Persist classification reads the store, not the service's advisory text.** After each
  invocation every fresh suggestion lands in exactly one bucket: no type or edited since the pin
  (`dropped`), served by the store now (`persisted`), else `unpersisted`; the cause is
  `workspace_busy` when the retrying section exhausted its attempts, else `store_error`. The pin is
  taken inside the typing port, microseconds after the service's own pin, not before the call.
- **The service's raise-path refusals (`BackendNotReachable` and friends) are not mapped back.** They
  fire only for a backend that raises outside the typing seam's own guard, which the shipped typing
  never does; a backend error raised by `chat` comes back inside the batch and is typed as for identity.
- **Progress verdict** for `relation_type` is the suggested type, or `degraded`; never the rationale.
- **Retry lives in the commit section handed to the service** (it enters the section once per
  persist), not in a persist wrapper, so the service's own fail-open persist is reused as is.

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/application/test_judging.py -k relation`: green (and the whole file, 159 tests) |
| Runtime harness | the shipped service, graph build, serve partition and persist run for real over a workspace made by `init`; only the candidate walk is a fixed list and the model is a scripted stub |
| Rollback boundary | revert removes the relation functions and the two chunk-size constants; the identity quote and run are untouched (the identity run only moved into `_run_identity`) |

### Gates (after the last source change)

- `uv run ruff check .`: All checks passed
- `uv run ruff format --check .`: 602 files already formatted
- `uv run mypy .`: Success, no issues found in 602 source files
- `uv run pytest --cov`: 12440 passed, 6 skipped in 888.80 s, total coverage 96.59 % (required 90 %);
  `application/judging.py` 98 %. One later test-only addition (the unsupported-run-kind test) ran
  with the judging and wiring files: 184 passed; ruff and mypy re-run clean.
- `uv run python evals/run_self_tests.py`: 57 of 57 harness self-tests run, 0 failing
