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
