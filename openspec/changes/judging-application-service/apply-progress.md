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
