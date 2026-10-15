# Tasks: Judging As An Application Service

MVP 6 stage "Judging as an application service". Inputs: `proposal.md`,
`exploration.md`, `design.md`, `specs/judging-application-service/spec.md`,
`specs/curate-decisions/spec.md`, `specs/pending-work/spec.md` and ADR-0059
(`docs/adr/0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md`,
Proposed). No tracking issue is known: no commit or PR text may use `Closes`,
`Fixes` or `Resolves`; PRs name the change in prose ("the
`judging-application-service` change").

Spec shorthand used in the tasks below (full names in the Coverage Matrix):
`J1`..`J19` are the requirements of `judging-application-service`, `D1`..`D3` of
`curate-decisions`, `P1`..`P2` of `pending-work`.

## Review Workload Forecast

Authored changed lines are additions plus deletions. Recorded golden JSON is
generated and excluded from the estimates (it is still part of the PR). The
~400-line figure is an advisory heuristic only: no test or doc is cut and no slice
is split artificially to fit it. Where a slice naturally exceeds it, the reason is
stated.

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,700 authored across 9 slices (golden JSON excluded) |
| 400-line budget risk | High in total; per slice below |
| Chained PRs recommended | Yes |
| Suggested split | S0a -> S0b -> S1 -> S2a -> S2b -> S2c -> S3a -> S3b -> S4 (9 PRs) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### Per-slice forecast

| Slice | Title | Forecast (authored) | Tasks | Over ~400? |
|-------|-------|---------------------|-------|------------|
| S0a | Spend-path characterization goldens | ~380 | 9 | No |
| S0b | Daemon-clobber strict xfail; UNCERTAIN listing pinned | ~140 | 4 | No |
| S1 | Identity serve/persist/reassemble into `application/` | ~320 | 8 | No |
| S2a | Amend-only write-through, daemon fix, `judged_uncertain` | ~360 | 11 | No |
| S2b | `judging.py`: binding, token, identity quote, notices | ~380 | 9 | No |
| S2c | Identity run, retry, outcome, equivalence test | ~420 | 13 | Slightly. The run, its retry and its equivalence test are one coherent behavior; splitting the run from its retry would leave an unsafe intermediate (a run with no bound on lost paid work). |
| S3a | `relation_type` on the protocol | ~300 | 7 | No |
| S3b | `volatility` on the protocol | ~250 | 6 | No |
| S4 | Delta specs merged, ADR accepted, archive | ~150 | 8 | No |

Total: 75 tasks, about 2,700 authored lines. Only S2c is expected to exceed the
heuristic.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| S0a | Pin the unpinned spend-path output | PR 1 | `uv run pytest tests/unit/cli/test_judging_characterization.py` | The goldens themselves run `curate`, `adjudicate` through `CliRunner` | Delete the test file and fixture; no source change |
| S0b | RED daemon test (strict xfail) and UNCERTAIN pin | PR 2 | `uv run pytest tests/unit/cli/test_daemon.py tests/unit/application/test_curate_decisions.py` | `_identity_stage` pass against a seeded `findings.db` | Revert the two test edits; no source change |
| S1 | Move identity helpers behind delegators | PR 3 | `uv run pytest tests/unit/application/test_identity_adjudication.py tests/unit/cli/test_judging_characterization.py tests/unit/cli/test_adjudicate.py` | S0a goldens (byte-identical CLI streams) | Revert restores the helpers in `cli/main.py` |
| S2a | Queue write-through, daemon fix, `judged_uncertain` | PR 4 | `uv run pytest tests/unit/state/test_pending_queue.py tests/unit/cli/test_daemon.py tests/unit/application/test_curate_decisions.py` | Daemon identity pass over a judged row; `pending --stats` before and after | Revert restores the clobber (today's behavior); verdicts stay in the store |
| S2b | Identity quote and token | PR 5 | `uv run pytest tests/unit/application/test_judging.py` | Quote against a workspace with a raising `chat` client | Delete `judging.py` and its tests; guards revert |
| S2c | Identity run and equivalence | PR 6 | `uv run pytest tests/unit/application/test_judging.py tests/unit/application/test_judging_equivalence.py` | Equivalence fixture (curate vs service, then one daemon pass each) | Revert removes `run_judging`; the quote stays valid |
| S3a | `relation_type` quote and run | PR 7 | `uv run pytest tests/unit/application/test_judging.py -k relation` | Measured per-invocation overhead probe | Revert the relation functions and constant |
| S3b | `volatility` quote and run | PR 8 | `uv run pytest tests/unit/application/test_judging.py -k volatility` | Stop-at-chunk-boundary scenario | Revert the volatility functions and constant |
| S4 | Merge specs, accept ADR, archive | PR 9 | `uv run pytest tests/unit/docs` (the ADR-status and index tests) | Name-match every spec heading against the main specs | Docs only; revert restores the unmerged deltas |

## Conventions For Every Slice

- **Commit scope** is `cli` for S0a, S0b, S1, S2a, S2b, S2c, S3a and S3b (the scope
  is the subsystem, and `daemon`/`curate`/`adjudicate` changes are `cli`); `docs`
  for S4. Types: `test` (S0a, S0b), `refactor` (S1), `fix` (S2a's daemon part is
  its own commit), `feat` (S2a queue and `judged_uncertain`, S2b, S2c, S3a, S3b),
  `docs` (S4). Each task closes with at least one work-unit commit.
- **Locate every target by content**, never by a line number copied from
  `design.md` (`main.py:13249-13591`, `daemon.py:253-283` and so on). They are
  pre-S0 coordinates and drift. Re-derive each range against the working tree.
- **Strict TDD** (`uv run pytest`): RED observed failing for the stated reason,
  then GREEN, then REFACTOR. Record the command and the observed result. S0a is
  characterization and green by design; each golden is proven by a mutation. A RED
  test that must land on `main` ahead of its fix uses
  `pytest.mark.xfail(strict=True, raises=AssertionError)` (S0b only).
- **No monkeypatched name moves.** Delegators stay in `cli/main.py`
  (`_partition_adjudication_serves`, `_persist_adjudications`,
  `_reassemble_adjudications`, `AdjudicationServes`). Every port that reaches a
  patched name is a call-time lambda in the adapter. Each slice mutates one patched
  line and confirms a test reddens.
- **Goldens are recorded once (S0a) before any move and are never edited
  afterwards.** No golden or fixture edit in any later slice. If a later slice
  turns a golden red, the slice is wrong, not the golden.
- **Gates per slice**, each run directly and never piped (a pipe hides failures
  from `pytest` and `ruff format --check`), after the last change:
  1. `uv run ruff check .`
  2. `uv run ruff format --check .`
  3. `uv run mypy .` (the whole repo, not `src/`)
  4. `uv run pytest --cov > <scratchpad>/pytest-<slice>.log 2>&1` (about 15
     minutes; 90% branch coverage; read the log, do not pipe)
  5. `uv run python evals/run_self_tests.py`
- **Mutation protocol** ("independent mutation" notes): make the mutation with an
  exact `Edit` on the one line the test pins, preferring a change in byte length;
  run only the test under proof and observe it FAIL for the expected assertion;
  revert with the inverse `Edit` (never `git checkout --`); purge bytecode with
  `find src tests -name __pycache__ -type d -prune -exec rm -rf {} +`; re-run to
  GREEN.
- **New files** are `git add`-ed before any review or diff-based check (a review
  projects the working tree and silently omits untracked files).
- **Delivery** is `auto-chain` with `stacked-to-main`: each slice is its own PR to
  `main`, branched from `origin/main` after the previous slice is squash-merged and
  independently green in CI. Slice N+1 opens its PR only after slice N merges.
  Delete merged branches locally and remotely. Confirm check-runs on the head SHA.
- **Measurement before placeholders.** `PERSIST_ATTEMPTS` and its backoff (S2c) and
  `RELATION_CHUNK_SIZE` / `VOLATILITY_CHUNK_SIZE` (S3a) are UNMEASURED
  placeholders. Each has a measurement task that precedes the task that fixes it.
  Record the measured values in the PR body and in the constant's comment.

---

## S0a: Spend-path characterization goldens (~380 authored lines)

Goal: pin the unpinned `curate` / `adjudicate` spend-path output byte for byte
before anything moves. Green by design; no source change.

- [ ] S0a.1 Create `tests/unit/cli/test_judging_characterization.py` with the
  pinned-environment harness: `golden_support.Goldens`, `CliRunner`, `GIT_CONFIG_COUNT`
  pinning, `isatty` patched, no ambient `OLLAMA_HOST`, judge stubbed through the
  existing monkeypatch targets. Reuse the pattern of the existing S0a/S0b goldens
  rather than inventing a new one. Satisfies: J18.
- [ ] S0a.2 Add the golden for the `curate` Identity cost line with `served > 0`,
  captured as a full stdout/stderr/exit-code stream (fixture: two groups, one with
  a servable stored verdict). Files: `tests/unit/cli/test_judging_characterization.py`.
  Satisfies: J18.
- [ ] S0a.3 Add the goldens for the model-disclosure lines inside a full `curate`
  stream (the line the quote's `model` notice mirrors). Same file. Satisfies: J18, J5.
- [ ] S0a.4 Add the golden for declining at the gate with real probes (the walk
  answers no at `gate()`), full stream and exit code. Same file. Satisfies: J18.
- [ ] S0a.5 Add the goldens for partial-batch failure for Structure and for
  Metadata (a backend error mid-batch). Same file. Satisfies: J18.
- [ ] S0a.6 Add the goldens for the adjudications persist fail-open advisory and
  the busy advisory, for both `curate` and `adjudicate`. Same file. Satisfies: J18, J12
  (pins the advisories `PersistResult` must reproduce byte for byte in S1).
- [ ] S0a.7 Add the goldens for the edge-suggestion persist fail-open and busy
  advisories (`suggest-relations`). Same file. Satisfies: J18.
- [ ] S0a.8 Record the goldens on the current tree: create
  `tests/unit/cli/fixtures/judging_characterization_goldens.json` and `git add` it.
  Run the test file unchanged twice (record, then compare) and observe GREEN both
  times. This file is never edited in a later slice. Satisfies: J18.
- [ ] S0a.9 Prove each golden can fail: for each of S0a.2..S0a.7, mutate the one
  source line it pins (for example the cost line's format string in `cli/curate.py`,
  and each advisory string in the persist helpers), observe the specific golden
  FAIL, revert byte-exactly, purge `__pycache__`, observe GREEN. Record the mutated
  line, the failing key and the green re-run. Satisfies: J18.

Gates: the five gates above.
Forecast: ~380 authored lines (JSON excluded).
Independent mutation: change the cost-line f-string in `cli/curate.py` that prints
the Identity "to judge" figure (a length-changing edit, e.g. append a character);
the S0a.2 golden must fail on that stream.

---

## S0b: Daemon-clobber strict xfail and UNCERTAIN pin (~140 authored lines)

Goal: land the one intended RED (the daemon erases a judged verdict) without
turning `main` red, and pin today's UNCERTAIN listing so S2a's flip is a visible
diff. Tests only.

- [ ] S0b.1 Pin today's behavior in `tests/unit/application/test_curate_decisions.py`:
  an identity row whose persisted verdict is `uncertain` (including a
  malformed-reply degrade) is listed with evaluation `pending_evaluation`. Green by
  design. Satisfies: D2 (the "before" half; flipped in S2a.10).
- [ ] S0b.2 RED: in `tests/unit/cli/test_daemon.py` add the daemon-clobber test. Seed
  `findings.db` via `pq.upsert_proposal` (open row carrying `adjudication:
  {"verdict": "same", ...}`) and `record_adjudications` (a servable store row).
  Assert the preconditions OUTSIDE the xfailed part: store row servable, open row
  carries `verdict: same`, the chat stub is never called. The one xfailed assertion
  compares the open row's `payload["adjudication"]` after `_identity_stage` with the
  seeded value; today it is `None`. Mark
  `@pytest.mark.xfail(strict=True, raises=AssertionError, reason=...)`. Observe
  XFAIL on this tree. Satisfies: P1 (scenario "A judged-SAME row survives a daemon
  pass").
- [ ] S0b.3 Prove setup errors are not absorbed: temporarily break a precondition
  (seed a stale rubric digest, or have the stub raise), observe an ordinary FAIL
  rather than XFAIL (the `raises=AssertionError` filter plus the outside-the-marker
  preconditions). Revert, purge `__pycache__`, re-run to XFAIL. Satisfies: P1.
- [ ] S0b.4 Run the S0a goldens unchanged (they must stay GREEN; no source changed)
  and the five gates. Satisfies: J18.

Gates: the five gates above.
Forecast: ~140 authored lines.
Independent mutation: in `cli/daemon.py` `_identity_stage`, no source mutation is
possible to make the xfail pass until S2a; instead change the seeded verdict in the
test's setup to `None` and confirm the xfail then reports XPASS(strict) as a FAIL,
proving the marker is strict.

---

## S1: Identity serve, persist and reassemble into `application/` (~320 authored lines)

Goal: `application/identity_adjudication.py` owns the helpers; `cli/main.py` keeps
delegators and re-exports; the two `typer.echo` advisories become fields on a typed
result and are re-rendered byte for byte by the delegator.

- [ ] S1.1 RED: create `tests/unit/application/test_identity_adjudication.py` with
  tests for the moved behavior: `partition_adjudication_serves` (identical
  partition, `rubric_stale`, `store_read` degrade), `servable_verdict` (each failed
  check refuses: no latest row, rubric mismatch, member-set mismatch, digest mismatch,
  confidential-bit mismatch, `withdraw_self_refuting_same` applied),
  `persist_adjudications` (drops for moved/unreadable members, DIFFERENT
  resolution, `PersistResult` fields `persisted`/`dropped`/`store_error`/`busy`),
  `reassemble_adjudications`. Observe RED (module missing). Satisfies: J2, J9, J1.
- [ ] S1.2 RED: add a layering/guard test (extend `tests/unit/application/test_layering.py`
  usage, which scans new modules automatically) plus a dedicated AST assertion that
  `identity_adjudication.py` never calls `isatty` or `typer.echo`. Files:
  `tests/unit/application/test_identity_adjudication.py`. Satisfies: J1.
- [ ] S1.3 GREEN: create `src/openkos/application/identity_adjudication.py` and move
  `AdjudicationServes`, `partition_adjudication_serves`, `servable_verdict` (the one
  predicate, with `include_confidential: bool | None`, `None` meaning the bit is
  not compared), `persist_adjudications` returning `PersistResult`, and
  `reassemble_adjudications`. Semantics unchanged. `persist_adjudications` takes a
  `commit_section` port and a `write_through: bool = False` parameter whose `True`
  branch is added in S2c (the parameter is declared and ignored when `False`).
  Files: `src/openkos/application/identity_adjudication.py`. Satisfies: J2, J9, J1.
- [ ] S1.4 GREEN: in `src/openkos/cli/main.py` replace the bodies with delegators
  and re-exports (the #1168 precedent). `_persist_adjudications` passes
  `_commit_section_for(layout.root)` (call-time, so patch sites keep intercepting)
  and renders the two advisories byte for byte from `store_error` and `busy` with
  the same `surface`. Locate the helpers by name, not by line. Satisfies: J18, J1.
- [ ] S1.5 RED then GREEN: update the registry key in
  `src/openkos/application/queue_resolution.py` from
  `openkos.cli.main._persist_adjudications` to
  `openkos.application.identity_adjudication.persist_adjudications`; first change
  the registry test expectation so it fails, then the key. Files:
  `src/openkos/application/queue_resolution.py`, its test. Satisfies: J9, J18.
- [ ] S1.6 Confirm the chat-timeout AST guard
  (`tests/unit/cli/test_chat_timeout_wiring.py`) is unaffected: the moved module
  makes no chat call. If the guard flags the new module, widen it per the design
  (values become sets) rather than bypassing it. Record the result. Satisfies: J1.
- [ ] S1.7 REFACTOR and prove survival: run S0a goldens, `test_adjudicate.py` and the
  curate tests unchanged and observe GREEN. Mutate one patched line (a delegator's
  `_commit_section_for(...)` call in `cli/main.py`), observe a test redden, revert,
  purge, GREEN. Satisfies: J18.
- [ ] S1.8 Run the five gates. Satisfies: J18.

Gates: the five gates above.
Forecast: ~320 authored lines (net of the moved code).
Independent mutation: in the `_persist_adjudications` delegator, change the busy
advisory's rendering (or drop the `surface` argument); the S0a.6 busy golden must
fail.

---

## S2a: Amend-only write-through, daemon fix, `judged_uncertain` (~360 authored lines)

Goal: a persisted verdict reaches the open queue row without inflating
`pending --stats`; the daemon stops erasing verdicts; UNCERTAIN is listed as
`judged_uncertain`. The strict xfail from S0b is removed here.

- [ ] S2a.1 RED: in `tests/unit/state/test_pending_queue.py` add tests for
  `amend_open_proposal`: no open row creates nothing (`no_open_row`); a resolved
  (`applied`, `declined`) or `stale` row keeps its status and no new row appears; an
  absent queue is not created; an open row with differing inputs writes nothing
  (`inputs_changed`); equal inputs and differing digest returns `amended`, and the
  row keeps `id`, `status`, `claimed_by`, `created_at`, targets and input digests;
  a claimed row keeps its claim; a declined key returns `suppressed`;
  identical payload returns `unchanged`; `kind_stats` is identical before and after
  an amendment. Observe RED. Satisfies: D1, P2.
- [ ] S2a.2 GREEN: in `src/openkos/state/pending_queue.py` add
  `_amend_open_in_transaction` and the amend-only public `amend_open_proposal`
  (never inserts, shares the decline check with `upsert_proposal`). Add
  `UpsertOutcome.AMENDED`. Satisfies: D1, P2.
- [ ] S2a.3 RED: tests for `upsert_proposal(amend_same_inputs=True)`: the "open row,
  different digest, same inputs" case returns `AMENDED`; with the flag false (the
  default) the case still retires as `stale` and inserts; a real input change still
  retires the row as `stale` and inserts one new `pending` row; every other outcome
  is unchanged. Observe RED. Files: `tests/unit/state/test_pending_queue.py`.
  Satisfies: P2.
- [ ] S2a.4 GREEN: add the `amend_same_inputs: bool = False` parameter to
  `upsert_proposal` in `src/openkos/state/pending_queue.py`, calling the same
  helper. Satisfies: P2.
- [ ] S2a.5 RED then GREEN: `ProducerResult.amended: int = 0` and
  `enqueue_identity` passing `amend_same_inputs=True`. Test in the producers test
  module: a judged-then-unjudged proposal over unchanged inputs counts as amended,
  not retired. Files: `src/openkos/application/queue_producers.py`, its test.
  Satisfies: P1, P2.
- [ ] S2a.6 RED then GREEN: `curate_queue.set_identity_verdicts(layout, results, *,
  pinned, commit_section)`, which builds each SAME or UNCERTAIN result's proposal
  with `queue_producers.identity_group_proposal`, calls `amend_open_proposal`, skips a
  result whose members moved since `pinned`, never touches a DIFFERENT result, and
  never creates a row. Tests cover: SAME and UNCERTAIN amend the open row, no open
  row writes nothing, moved members are not written through, DIFFERENT untouched.
  Files: `src/openkos/application/curate_queue.py`,
  `tests/unit/application/test_curate_queue.py`. Satisfies: D1.
- [ ] S2a.7 RED then GREEN: `identity_adjudication.servable_stored_verdicts(layout,
  groups)` reading the adjudications store read-only and applying `servable_verdict`
  with `include_confidential=None`. Tests refuse each failed check (latest row
  missing, rubric differs, member set differs, digest differs), an absent or
  unreadable store carries nothing, no client is built. Files:
  `src/openkos/application/identity_adjudication.py`,
  `tests/unit/application/test_identity_adjudication.py`. Satisfies: P1.
- [ ] S2a.8 GREEN (the S0b RED turns green): in `src/openkos/cli/daemon.py`
  `_identity_stage` pass `adjudicated=identity_adjudication.servable_stored_verdicts(
  ctx.layout, report.groups)`; keep `uses_model=False`. Remove the `xfail` marker
  from the S0b.2 test in `tests/unit/cli/test_daemon.py` (a strict xfail that passes
  fails CI, so this cannot be forgotten). Add daemon tests: a servable verdict is
  carried into a null row; an old-rubric verdict amends the row to null; an edited
  member carries nothing; a verdict judged under either confidential inclusion is
  carried and no model is called; the adjudications store is byte-identical after the
  pass; a pass over unchanged inputs yields `UNCHANGED` and only last-seen moves,
  `pending --stats` counts do not move. Satisfies: P1, P2.
- [ ] S2a.9 RED then GREEN: `judged_uncertain`. In
  `src/openkos/application/curate_decisions.py` `_identity_actions` maps a row
  verdict `uncertain` (and a malformed-reply degrade) to evaluation
  `judged_uncertain` with keep-distinct and skip and the merge withheld for reason
  `judged_uncertain`; both literals are additive on `DecisionItem.evaluation` and
  `WithheldAction.reason`; an unknown or missing verdict stays `pending_evaluation`.
  Add the merge-not-applied scenario (`not_mergeable`, nothing written). Satisfies:
  D2, D1.
- [ ] S2a.10 Flip the S0b.1 pin in `tests/unit/application/test_curate_decisions.py`
  from `pending_evaluation` to `judged_uncertain` (the visible diff the pin was
  created for). This is a test edit, not a golden edit. Satisfies: D2.
- [ ] S2a.11 Patched-line mutation and the five gates. Run S0a goldens unchanged and
  observe GREEN. Mutate one patched line, confirm a test reddens, revert, purge.
  Satisfies: J18.

Gates: the five gates above.
Forecast: ~360 authored lines.
Independent mutation: in `amend_open_proposal`, change the open-row guard so a
`declined` row is also amendable (or drop the `inputs_changed` early return); the
"resolved row keeps its status" test must fail.

---

## S2b: `judging.py` binding, token, identity quote, notices (~380 authored lines)

Goal: an LLM-free, kind-tagged identity quote and its token; `QuoteStale` type and
the binding comparison. No run yet.

- [ ] S2b.1 RED: widen the guards first so they fail for the missing module.
  `tests/unit/cli/test_chat_timeout_wiring.py`: `_EXPECTED_SERVICE_TASKS` values become
  sets, `judging.py` is expected to name `adjudication`. `tests/unit/application/
  test_curate_decisions_boundary.py`: `_TARGET` becomes the tuple
  `("curate_decisions", "judging", "identity_adjudication")` and every spelling check
  runs for each. Observe RED. Widen, never bypass. Satisfies: J19, J1.
- [ ] S2b.2 RED then GREEN: types in `src/openkos/application/judging.py`:
  `JudgingKind`, `QuoteBinding`, `WorkItem`, `QuoteToken` (with `digest()` covering
  every field, sorted and NUL-separated), `QuoteStale`, `QuoteNotice`,
  `JudgingQuote`, `JudgingPorts`, `JudgingObserver`. Tests in
  `tests/unit/application/test_judging.py`: digest is stable for equal tokens and
  differs per field; no field holds a time. Satisfies: J3.
- [ ] S2b.3 RED then GREEN: `QuoteBinding` comparison returning the changed fields
  (`model`, `backend`, `locality`, `confidential`, `rubric`); test one case per field
  and that a changed work set alone is not a binding change. The run returns the
  `QuoteStale` in S2c; here only the comparator and type exist. Satisfies: J8.
- [ ] S2b.4 RED then GREEN: `quote_judging(root, "identity", ...)`: builds the client
  only to read locality and the model tag, uses `find_candidates_report`, drops
  kept-distinct, partitions with `partition_adjudication_serves` under the effective
  confidential bit, reports `total`, `served`, `to_judge`, `calls`. Tests with a client
  whose `chat` raises: the quote succeeds with an unreachable backend; five groups,
  two servable gives `total` 5, `served` 2, `to_judge` 3, `calls <= 3`; an edited
  member counts in `to_judge`; a rubric change leaves no group `served`; no
  stdout/stderr output; no ETA field. Satisfies: J2, J1.
- [ ] S2b.5 Resolve the zero-call group (see Conflicts below, item 1) and cover it:
  an identity group with no readable member adds nothing to `calls`. Test, then
  GREEN. Satisfies: J2.
- [ ] S2b.6 RED then GREEN: `include_uncertain`. Default: decided and uncertain are
  split, uncertain reported as `uncertain` count and in neither `to_judge` nor
  `calls`; with `include_uncertain=True` they move into both, before consent; the
  flag is bound into the token; an edited member returns the group to `to_judge`
  with or without the flag. Satisfies: J4, J3.
- [ ] S2b.7 RED then GREEN: notices as data: exactly one `model` notice naming the
  resolved model and backend, `truncated`, `quarantined`, `rubric_rejudge`, and
  `unattended_active` from `.openkos/jobs.db` read-only via
  `jobs.open_jobs_read_only` (open job, or newest job started within
  `cfg.unattended.maintenance_interval_seconds`; unreadable record yields
  `state="unknown"`; absent file yields no notice and the file is not created). The
  notice changes neither price nor token. Tests with a seeded `jobs.db`. Satisfies:
  J5, J15.
- [ ] S2b.8 Token stability and age test: two quotes with no change in between bind
  the same context and work set; the token carries no clock. Satisfies: J3.
- [ ] S2b.9 Patched-line mutation, guard sanity (add and remove a temporary offending
  `isatty` line to see the AST guard redden), and the five gates. Satisfies: J1, J18.

Gates: the five gates above.
Forecast: ~380 authored lines.
Independent mutation: in `quote_judging`, move the `uncertain` items into `calls`
unconditionally; the "uncertain is not re-priced by default" test must fail.

---

## S2c: Identity run, retry, outcome, equivalence (~420 authored lines)

Goal: `run_judging` for identity with the binding ceiling, chunks of one group,
cancellation, progress, bounded persist retry and the typed outcome, plus the
equivalence test. Over the heuristic by design: the run, its retry and its
equivalence are one behavior.

- [ ] S2c.1 Measure before fixing `PERSIST_ATTEMPTS` (3) and the backoff (1.0 s, 2.0
  s): time `lock_wait.locked_commit_section` under contention (a held lock released
  after a known delay) and record the observed wait distribution in the PR body and
  the constant's comment. The constants stay UNMEASURED placeholders until this is
  recorded. Files: none beyond the constant's comment. Satisfies: J12.
- [ ] S2c.2 RED then GREEN: `run_judging(token=None)` returns
  `RunRefused("no_quote_token")` before the config is read, with no client built and
  no write. Satisfies: J6.
- [ ] S2c.3 RED then GREEN: a changed model, backend, locality, rubric or confidential
  flag returns `QuoteStale` naming each changed field, with no chat call and no
  write; a work-set change alone is not stale; an old token over unchanged state is
  not stale. Satisfies: J8, J3.
- [ ] S2c.4 RED then GREEN: the binding ceiling. Re-partition LLM-free into `judge`,
  `not_in_quote`, `settled_since_quote` by `(work_key, member_digests)`; judge only
  `needed ∩ token.work` in quote order; a counting client wrapper asserts calls
  never exceed `token.calls` (an excess is an internal `AssertionError`); an edited
  member lands in `not_in_quote` and `settled_since_quote`; a group served elsewhere,
  vanished or kept-distinct is settled and not judged; a default token never judges
  uncertain items; the `not_in_quote` outcome names the work keys. Satisfies: J7, J4.
- [ ] S2c.5 RED then GREEN: the chunk loop (`IDENTITY_CHUNK_SIZE = 1`, `chunk_size`
  override validated `>= 1`): check stop, pin member digests,
  `adjudicate_candidates([group])`, persist one chunk inside `ports.commit_section()`.
  Tests: a chunk persists before the next chunk's call (section fake records order);
  a stop signalled from inside the fake chat call lets the in-flight chunk persist and
  defers the rest; a stop before the first chunk makes no call and defers everything;
  a re-quote after a stop shows the persisted chunks as `served`. Satisfies: J9, J10, J14.
- [ ] S2c.6 RED then GREEN: wire `persist_adjudications(..., write_through=True)` so
  the section writes the store, resolves DIFFERENT rows as `declined` (no decision
  sidecar), then calls `curate_queue.set_identity_verdicts` in the same section. Tests:
  a verdict about content edited during the call is dropped; an unreadable-member
  result is not persisted; a SAME verdict is never merged and both concepts stay
  unchanged. Files: `src/openkos/application/identity_adjudication.py`,
  `src/openkos/application/judging.py`. Satisfies: J9, J16, D1.
- [ ] S2c.7 RED then GREEN: progress. The leaf's `on_progress(index, total, result)`
  is wrapped; indices are rebased to the run and `total = len(judge)`; the observer
  receives `progress(JudgingProgress)` (verdict only, never the rationale) and
  `chunk_persisted(n)`. Test: events run 1..3 with `total` 3. Satisfies: J11, J19.
- [ ] S2c.8 RED then GREEN: observer guard. Every observer call goes through one guard
  catching `Exception` (`# noqa: BLE001 -- an observer is the client's callback; its
  failure must not cost paid work`), sets `observer_failed` and stops calling the
  observer. Test: an observer raising on the second event leaves every earlier chunk
  stored and the chunk in flight persisted. Satisfies: J11.
- [ ] S2c.9 RED then GREEN: persist retry and the `unpersisted` outcome. Enter the
  section up to `PERSIST_ATTEMPTS` times on `WorkspaceBusyError`, `ports.sleep(1.0)`
  then `ports.sleep(2.0)` between attempts, no lock held between attempts. A transient
  busy is absorbed; a persistent busy ends with `status="unpersisted"`,
  `unpersisted` count, `unpersisted_cause`, the rest `deferred`; a store error is the
  `store_error` cause. Satisfies: J12, J1.
- [ ] S2c.10 RED then GREEN: typed backend failures. A non-availability `BackendError`
  persists the prefix, ends with `status="failed"`, `failure` set and `failed_at` as
  the 1-based run index (not the chunk index); `BackendUnavailable` and
  `BackendModelNotFound` are re-raised unchanged after the prefix persists and after
  `chunk_persisted` was delivered (see Conflicts, item 2). Satisfies: J13.
- [ ] S2c.11 RED then GREEN: lock and daemon guarantees. A section fake records entry
  and exit around the fake chat and asserts no section is open during a chat call; a
  workspace whose `jobs.db` shows an in-progress job still runs; a run takes no lease
  and does not count against `max_calls_per_day`; a run and a daemon pass both
  persisting one group leave a servable verdict and one open row. Satisfies: J14, J15.
- [ ] S2c.12 Equivalence test `tests/unit/application/test_judging_equivalence.py`
  per the design: four two-member groups (`g1` SAME, `g2` DIFFERENT, `g3` UNCERTAIN
  each with an open `pending_evaluation` row; `g4` with a servable stored verdict and
  no row), judge stub keyed by member ids, `--auto-merge` off, the walk answers `s`.
  Assert, ignoring ids and timestamps: (1) adjudications rows equal; (2) `g2`'s row
  `declined` on both sides; (3) the immediate queue difference is asserted as intended
  (curate leaves `g1`/`g3` null and inserts `g4`'s verdict row; the service amends
  `g1`/`g3` and writes no `g4` row), then one daemon identity pass on each copy and
  the queue verdicts are equal. Satisfies: J9, J16, P1.
- [ ] S2c.13 Document the worst-case stop latency in the `judging.py` module
  docstring (one in-flight chat call plus one persist, bounded by `cfg.chat_timeout`
  plus the persist's bounded wait; nothing preempts a call). Mutate one patched line,
  confirm a test reddens, run the five gates. Satisfies: J10, J18.

Gates: the five gates above.
Forecast: ~420 authored lines (exceeds the ~400 advisory by about 20; coherent
behavior, not split).
Independent mutation: in `run_judging`, change the judge-set intersection to iterate
the current needed set instead of `token.work` (or drop the `calls` ceiling check);
the "new work is not judged" / `not_in_quote` test must fail.

---

## S3a: `relation_type` on the protocol (~300 authored lines)

Goal: `relation_type` quote and run over `suggest_relations_service`, reusing
`max_calls`, `deferred` and the relations `CostQuote`.

- [ ] S3a.1 Measure first: time the per-invocation overhead of one
  `suggest_relations_service` call (the LLM-free candidate/graph walk) and of one
  `suggest_volatility_service` call, over a representative fixture and the
  `examples/good-life-demo/` workspace. Record the numbers, then fix
  `RELATION_CHUNK_SIZE` (placeholder 8) and `VOLATILITY_CHUNK_SIZE` (placeholder 4)
  and state each worst-case stop latency (chunk size times `chat_timeout`). Both
  constants are fixed here; S3b only consumes its. Files: `judging.py` constant
  comments only. Satisfies: J10, J17.
- [ ] S3a.2 RED then GREEN: widen `_EXPECTED_SERVICE_TASKS` so `judging.py` names the
  relation task as well as `adjudication` (values stay sets). Files:
  `tests/unit/cli/test_chat_timeout_wiring.py`. Satisfies: J1, J17.
- [ ] S3a.3 RED then GREEN: `quote_judging(root, "relation_type", ...)` returns a
  kind-tagged quote binding model, backend, locality and the confidential flag
  (rubric digest `None`), `served` for edges with a servable persisted suggestion,
  `to_judge` for the rest, and the one `model` notice. Satisfies: J17, J2, J3.
- [ ] S3a.4 RED then GREEN: `run_judging` for `relation_type`: re-invokes the existing
  service with `max_calls=chunk_size` and a candidate port filtered to the remaining
  quoted keys; the service's own persist runs per invocation; honors the ceiling,
  `not_in_quote`, `QuoteStale`, stop at chunk boundaries, progress, the typed outcome
  and the `deferred` semantics of the shipped service. Satisfies: J17, J7, J8, J10, J11, J13.
- [ ] S3a.5 RED then GREEN: a stop signalled during the first relation chunk persists
  that chunk and reports the rest `deferred`; a re-quote shows it `served`. Satisfies:
  J17, J9.
- [ ] S3a.6 Confirm `suggest-relations` goldens (S0a.7) and its tests are unchanged
  and GREEN; mutate one patched line, confirm a test reddens. Satisfies: J18.
- [ ] S3a.7 Run the five gates. Satisfies: J18.

Gates: the five gates above.
Forecast: ~300 authored lines.
Independent mutation: in the relation run, drop the `max_calls=chunk_size` argument
(or the remaining-keys filter); the stop-at-chunk-boundary test must fail.

---

## S3b: `volatility` on the protocol (~250 authored lines)

Goal: `volatility` quote and run over `suggest_volatility_service`. It has no
`CostQuote`, so the judging surface builds the quote from its own partition.

- [ ] S3b.1 RED then GREEN: the volatility quote built by the judging surface from its
  own partition (`suggest_volatility_service` has no `CostQuote`), reusing
  `max_calls` and `deferred`; kind-tagged; binds model, backend, locality and the
  confidential flag; servable answers count as `served`. Satisfies: J17, J2, J3.
- [ ] S3b.2 RED then GREEN: `run_judging` for `volatility` with
  `VOLATILITY_CHUNK_SIZE` (fixed in S3a.1), ceiling, `not_in_quote`, `QuoteStale`,
  progress and typed outcome. Satisfies: J17, J7, J8, J11, J13.
- [ ] S3b.3 RED then GREEN: "A volatility run stops at a chunk boundary": a stop in the
  first chunk persists it and defers the remaining items. Satisfies: J17, J10.
- [ ] S3b.4 Update the module docstring's latency statement with the measured per-kind
  worst cases from S3a.1, and widen `_EXPECTED_SERVICE_TASKS` for the volatility task
  (sets). Files: `src/openkos/application/judging.py`,
  `tests/unit/cli/test_chat_timeout_wiring.py`. Satisfies: J10, J1.
- [ ] S3b.5 Confirm `suggest-volatility` goldens and tests are unchanged and GREEN;
  mutate one patched line, confirm a test reddens. Satisfies: J18.
- [ ] S3b.6 Run the five gates. Satisfies: J18.

Gates: the five gates above.
Forecast: ~250 authored lines.
Independent mutation: in the volatility quote, count served answers in `to_judge`
instead of `served`; the volatility quote partition test must fail.

---

## S4: Merge, accept, archive (~150 authored lines)

Goal: close the change. Docs only; no source, golden or fixture edit.

- [ ] S4.1 Merge the three delta specs into `openspec/specs/`: new
  `openspec/specs/judging-application-service/spec.md`; MODIFIED and ADDED
  requirements into `openspec/specs/curate-decisions/spec.md`; ADDED requirements into
  `openspec/specs/pending-work/spec.md`. Name-match every requirement heading against
  the main specs (4 had drifted in a previous change). Satisfies: all requirements.
- [ ] S4.2 Hand-fix the `curate-decisions` Purpose paragraph in
  `openspec/specs/curate-decisions/spec.md`, which still calls judging a "separate,
  required follow-up"; it now names `judging-application-service` as the place a
  pending-evaluation item becomes decidable. Satisfies: D3.
- [ ] S4.3 ADR-0059 status Proposed -> Accepted in all three places: the ADR file
  `docs/adr/0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md`,
  the `docs/adr/README.md` index row, and any status-keeping test input. Do not edit
  any other accepted ADR (append-only). Satisfies: J-all (decision record).
- [ ] S4.4 Docs: update `docs/architecture.md` only if the general shape changed (a new
  application module pair); otherwise record "no shape change" in the PR. Do not add
  counts or "since #NNN". Satisfies: J1.
- [ ] S4.5 Check each requirement against shipped code, not the task list (a passing
  spec is not a met goal): confirm the Purpose paragraph and the issue text agree with
  behavior; list any requirement the code does not meet and any overstatement.
  Satisfies: all requirements.
- [ ] S4.6 Record the open follow-ups in the archive report (not blocking): curate's
  `_serve_identity_rows` serves queue verdicts without a rubric check; token
  serialization belongs to the MVP 6 API stage (ADR-0039); contradictions and
  revisions judging.
- [ ] S4.7 Archive: move (not copy) `openspec/changes/judging-application-service/`
  to `openspec/changes/archive/<date>-judging-application-service/`, including
  `exploration.md`, and write `archive-report.md`. Diff against a sibling archive and
  check the disk: the archive agent has overclaimed completion before.
- [ ] S4.8 Run the five gates plus the ADR-status test. Satisfies: J18.

Gates: the five gates above.
Forecast: ~150 authored lines (specs merged and docs).
Independent mutation: rename one merged requirement heading in
`openspec/specs/judging-application-service/spec.md`; the name-match check (S4.1) must
fail. For the ADR, set the README row status to `Proposed` and the ADR-status test must
fail.

---

## Coverage Matrix

| ID | Requirement | Tasks |
|----|-------------|-------|
| J1 | The Surface Is Plain Data With No Interaction | S1.1, S1.2, S1.3, S1.6, S2b.1, S2b.4, S2b.9, S2c.9, S3a.2, S3b.4, S4.4 |
| J2 | A Quote Is LLM-Free, Kind-Tagged And Costs Nothing | S1.1, S1.3, S2b.4, S2b.5, S3a.3, S3b.1 |
| J3 | A Quote Token Binds The Context Consent Was Given For | S2b.2, S2b.6, S2b.8, S2c.3, S3a.3, S3b.1 |
| J4 | Judged-Uncertain Items Are Excluded From The Quote By Default | S2b.6, S2c.4 |
| J5 | A Quote Carries Its Notices As Data | S0a.3, S2b.7 |
| J6 | Consent Is A Reference To The Quote Token | S2c.2 |
| J7 | A Run Never Exceeds Its Quote | S2c.4, S3a.4, S3b.2 |
| J8 | A Changed Model, Backend, Locality, Rubric Or Confidential Flag Returns QuoteStale | S2b.3, S2c.3, S3a.4, S3b.2 |
| J9 | Verdicts Persist Per Chunk Before The Next Chunk Is Judged | S1.1, S1.3, S1.5, S2c.5, S2c.6, S2c.12, S3a.5 |
| J10 | Cancellation Takes Effect At Chunk Boundaries | S2c.5, S2c.13, S3a.1, S3a.4, S3b.3, S3b.4 |
| J11 | Progress Is Reported Through An Observer | S2c.7, S2c.8, S3a.4, S3b.2 |
| J12 | A Busy Workspace Is Retried, Then Reported As Unpersisted | S0a.6, S2c.1, S2c.9 |
| J13 | The Run Outcome Is Typed | S2c.10, S3a.4, S3b.2 |
| J14 | Model Calls Hold No Workspace Lock | S2c.5, S2c.11 |
| J15 | The App May Judge While The Daemon Runs | S2b.7, S2c.11 |
| J16 | The Surface Ends At Persisted Verdicts | S2c.6, S2c.12 |
| J17 | Relation Type And Volatility Use The Same Protocol | S3a.1-S3a.5, S3b.1-S3b.4 |
| J18 | Existing Commands Keep Their Output | S0a.1-S0a.9, S0b.4, S1.4, S1.5, S1.7, S1.8, S2a.11, S2b.9, S2c.13, S3a.6, S3a.7, S3b.5, S3b.6, S4.8 |
| J19 | Payloads Are Owner-Local | S2b.1, S2c.7 |
| D1 | A Persisted Verdict Written Through To An Open Row Is Reflected In The List | S2a.1, S2a.2, S2a.6, S2a.9, S2c.6 |
| D2 | Only Judged-SAME Identity Items Are Mergeable (MODIFIED) | S0b.1, S2a.9, S2a.10 |
| D3 | The Surface Computes No Judgment (MODIFIED) | S4.1, S4.2 |
| P1 | An Identity Producer Carries A Servable Stored Verdict | S0b.2, S0b.3, S2a.5, S2a.7, S2a.8, S2c.12 |
| P2 | Writing A Verdict Through To An Open Row Does Not Count As A New Or Stale Proposal | S2a.1, S2a.2, S2a.3, S2a.4, S2a.5, S2a.8 |

Requirements left uncovered: none. J2, J3 and J13 are covered with a spec/design
wording difference, listed next.

## Conflicts Between Spec And Design (carried into the tasks)

1. **Zero-call groups in `calls`.** J2 says an identity group with no readable
   member MUST add nothing to `calls`; the design's `QuoteToken.calls` is described as
   `== len(work)` for identity. S2b.5 resolves it in the spec's favor: the quote
   asks the leaf's own zero-call determination (adjudication.py short-circuit for an
   all-unreadable or all-confidential group) and excludes those groups from `calls`
   while keeping them in `work`. The ceiling check is `calls_made <= token.calls`.
2. **Partial counts on a raised backend error.** J13 says the partial counts are
   "available to the caller" when `BackendUnavailable` / `BackendModelNotFound`
   propagate; the design re-raises them unchanged, so the only carrier is the
   observer's `chunk_persisted` events. S2c.10 asserts this carrier and the
   persisted prefix. If the spec means a field on the exception, the design must add
   one before S2c; recommend rewording J13 to "reported through the observer".
3. **Token opacity.** J3 says the token is opaque to the caller; the design
   exposes a frozen dataclass whose `digest()` is the transport form. S2b.2 follows
   the design (as ADR-0053's `DecisionVersion` does); not a blocker.
