# Tasks: auto-merge-safe-class — measure first, then (only on PASS) apply the safe class automatically

Refs #1054. Design: `design.md`. Proposal: `proposal.md`. ADR-0034 is
written, status `Proposed`, with its `docs/adr/README.md` row, in the
planning commit; no task below edits it except archive's status flip.

**Order is the contract.** Phase 1 builds the instrument, Phase 2 runs it
and decides. Phases 3-6 exist only on the PASS branch of task 2.6. Nothing
in Phases 3-6 may start, even as a draft branch, before 2.6 records `PASS`.

**The decision rule is frozen.** `design.md` §"Decision rule" was committed
before any live run. No task may edit it after task 2.2 starts. A different
rule is a new change with its own pre-registration.

Strict TDD is ON, runner `uv run pytest`. Every behavioral task pairs a
`[TEST]` (observed RED, with the reason it is RED today) with the `[IMPL]`
that turns it GREEN. A test GREEN on first run proves nothing until a
`[MUT]` task breaks the exact line it guards and observes RED; revert
mutations with the inverse edit (never `git checkout --`) and purge
`__pycache__` before trusting a verdict. Harness self-tests follow the same
rule: every bar in `decide()` is shown able to fail.

Gate per phase: `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy .`, `uv run pytest --cov`, `uv run python
evals/run_self_tests.py` — each run directly, unpiped.

## Review Workload Forecast

| Phase | Slice | Est. lines | Risk | Conditional |
|---|---|---|---|---|
| 1 | Harness, fixture, self-test, `decide()` | ~400 | passive for users (evals only) | no |
| 2 | Live runs, verdict file, issue comment | ~60 + results | passive | no |
| 3 | Constants, model column, eligibility plan | ~350 | high (feeds a destructive write) | PASS only |
| 4 | `curate --auto-merge` pass, one commit, run bullet | ~400 | high (unattended deletion) | PASS only |
| 5 | Disclosure + round-trip/unmerge tests | ~300 | medium | PASS only |
| 6 | Docs + archive preparation | ~80 | passive | PASS only |

## Phase 1 — The measurement harness (`evals/auto_merge/`)

- [x] 1.1 [PRE] Confirm the instrument does not exist and the prior holds:
  `ls evals/auto_merge` fails; `grep -n "Confidence carries no information"
  evals/adjudication/README.md` hits. Record both outputs in the PR.
  Confirmed at session start: `ls evals/auto_merge` -> "No such file or
  directory"; the grep hit at `evals/adjudication/README.md:245`.
- [x] 1.2 [TEST] Self-test fixture checks, written first against an empty
  `auto_merge_fixtures.py` so they are RED: class minimums (≥ 12 negative
  pairs over ≥ 5 classes incl. `week-apart` ≥ 3 and `asym-recurrence` ≥ 3;
  ≥ 10 positive pairs over ≥ 4 classes incl. `reingest-dup` ≥ 3); every
  document has `type`, `title`, `sensitivity: private`, non-empty
  `provenance`; fixture digest identical across two materializations.
- [x] 1.3 [IMPL] `evals/auto_merge/auto_merge_fixtures.py`: invented,
  de-identified content only (no private corpus text, ever). Import
  `recurrence`, `asym-recurrence` (with `grupo-calidad-datos`), `event-same`,
  `asym-same` and same-type `part-whole`/`aspect-of` pairs from
  `evals/adjudication/adjudication_fixtures.py`; write new `week-apart` (one
  series title, disjoint provenance, dates seven days apart, different
  attendees/decisions), `namesake-person`, `reingest-dup` (shared
  provenance), `person-same`, `alias-same`. Module docstring states the
  labels are constructed, not adjudicated.
  26 pairs across the 10 classes (15 negative, 11 positive), well above the
  design floor. `alias-same`'s two pairs use a short-title/qualified-title
  variant of one name (not two unrelated names): candidate discovery is
  title-based, so a wholly different alias would never be nominated as a
  candidate at all -- observed directly (see 1.4's RED below).
- [x] 1.4 [TEST] D3 structural eligibility in the self-test: for every
  labelled pair, on the materialized bundle, `find_candidates` yields
  exactly one 2-member group, both members declare one type,
  `lifecycle.cross_type_concern` is `None`, and `prepare_one_merge` is not
  guardrail-refused. RED until 1.3's pairs are shaped to pass.
  Observed RED with the first `alias-same` draft (unrelated names "Task
  Queue" / "Job Queue"): "no labelled pair is missing from find_candidates:
  got ['alias-same:...']" -- reshaped to a near-matching title pair per 1.3's
  note, then GREEN.
- [x] 1.5 [MUT] Give one `week-apart` document a different `type`; the
  self-test goes RED naming that pair. Revert, purge `__pycache__`, GREEN.
  Observed: `cross_type_concern is not None -- members declare different OKF
  types (Project / Event)` naming the mutated pair exactly. Reverted,
  `__pycache__` purged, confirmed GREEN.
- [x] 1.6 [TEST] `decide(cal, conf)` on synthetic arms: one case per
  outcome — `PASS`; `FAIL (no separator)` (highest bad ≥ every good);
  `FAIL R2` (a confirmation negative at ≥ `t*`); `FAIL R3` (a `week-apart`
  auto-merge in calibration only); `FAIL R4` (retention 0.49); `FAIL R5`
  (stability 0.79); `INVALID` (one `<missing>` trial; mismatched fixture
  digest). Plus: `B = -inf` labels `t*` non-binding.
  All eight cases implemented and GREEN. Deviation, documented in
  `run_auto_merge_eval.py` and `README.md`: the `FAIL R3` case is
  constructed in the CONFIRMATION arm, not calibration -- Step 1's own
  invariant (`t* > B` always, and `B` is the max over ALL calibration
  negative `same` trials, week-apart included) makes a calibration-only R3
  violation mathematically unreachable under a correct implementation; the
  cross-arm check's real bite is catching a lucky confirmation run.
- [x] 1.7 [IMPL] `evals/auto_merge/run_auto_merge_eval.py`: `decide()` exactly
  as `design.md` §"Decision rule" states; `--self-test` (model-free);
  `--arm {calibration,confirmation} --runs 15 --model qwen3:8b` over the
  real `find_candidates` + `adjudicate_candidates` path in a temp bundle
  with no `findings.db`, production context window and generation ceiling,
  no seed/temperature; `--decide CAL.json CONF.json`. JSON schema
  `openkos.eval.auto_merge/v1` and the three report files per
  `design.md` §"Results file format", rationales verbatim, `tier` and
  `cross_source` per trial, secondary cross-source-excluded population.
- [x] 1.8 [MUT] For each bar R2-R5 and the separator step, invert one
  comparison in `decide()` (e.g. `>` → `>=` in `t*` selection); the
  matching 1.6 case goes RED. Revert each, purge `__pycache__`.
  Five mutations, each observed RED on its matching case then reverted
  (`__pycache__` purged before each re-run): separator `>`→`>=` (no
  separator case flips to a spurious PASS); R2 `if false_merges:` →
  `if len(false_merges) > 1:`; R3 the analogous off-by-one (co-fires with
  R2 by construction -- a week-apart trial is also a negative trial, so a
  pure-R3-only failure is not reachable, but the R3-specific reason text
  disappearing was observed RED); R4 `< 0.50` → `< 0.49`; R5 `< 0.80` →
  `< 0.79`. All five reverted and reconfirmed GREEN.
- [x] 1.9 [TEST] `uv run python evals/run_self_tests.py` discovers the new
  harness (count rises by one) and passes under the poisoned
  `OLLAMA_HOST`.
  Observed: 45 of 45 harness self-tests run (44 pre-existing + this one),
  `evals/auto_merge/run_auto_merge_eval.py` listed `ok 0.3s`, 0 failing.
- [x] 1.10 [DOC] `evals/auto_merge/README.md`: why the harness exists, the
  fixture table, the frozen decision rule (verbatim copy, with the design
  commit sha it was frozen at), usage, and "What a PASS does not
  establish". Commit Phase 1 following the harness precedent
  (`eval(adjudication): ...`, `eval(decision-revisions): ...`):
  `eval(auto-merge): measure-first harness and hard-negative fixture (#1054)`.

## Phase 2 — Run it and record the verdict (the STOP gate)

- [ ] 2.1 [PRE] Ollama is up with `qwen3:8b` pulled (`ollama list`); start it
  locally if down — never record the eval as blocked on it. Record
  `git rev-parse HEAD` and confirm `design.md` §"Decision rule" is
  byte-identical to the planning commit (`git diff <planning-sha> --
  openspec/changes/auto-merge-safe-class/design.md` is empty for that
  section).
- [ ] 2.2 [RUN] `--arm calibration --runs 15`. Unpiped. Commit the JSON and
  report as soon as it lands; persist verdicts only, never a private
  corpus object.
- [ ] 2.3 [RUN] `--arm confirmation --runs 15` as a SEPARATE invocation
  (never a split of 2.2's runs). Commit the JSON and report.
- [ ] 2.4 [RUN] `--decide` over 2.2 and 2.3; commit the verdict file. If
  `INVALID`, re-run only the invalid arm (R0 names why) and repeat 2.4.
- [ ] 2.5 [CHECK] Read the verdict file against the raw JSON by hand for
  R2/R3: list every negative trial with `same`, its confidence, and `t*`,
  and confirm the counts (print `n of TOTAL`, never a filtered count).
  Report `B`, `t*`, and exposure in the PR body.
- [ ] 2.6 [GATE] **If the verdict is `FAIL`: STOP. Do not build the
  auto-apply.** Post the verdict, the failed bars, the per-class table and
  the secondary (cross-source-excluded) result as a comment on #1054; mark
  Phases 3-6 below `[-] skipped: pre-registered rule failed (<verdict file>)`;
  delete the four conditional delta specs from this change; keep the
  harness, results, README and ADR-0034 (which stands: no class qualifies
  yet); archive. The owner decides whether another signal (design D2's
  alternatives) earns its own pre-registered rule in a new change.
  **Only if the verdict is `PASS`** continue to Phase 3, carrying `t*` and
  the model from the verdict file.

## Phase 3 — Constants, model provenance, eligibility plan (PASS only)

- [ ] 3.1 [TEST] `state.adjudications`: a fresh persisted row records
  `model`; an old-shape store reads `model` as `NULL` without raising;
  serving is unchanged (entity-resolution-adjudication ADDED requirement).
- [ ] 3.2 [IMPL] Nullable `model` column, migration mirroring #838's
  `rubric_digest`; thread the task model into the persist call in
  `adjudicate` and curate's Identity.
- [ ] 3.3 [TEST] `lifecycle.auto_merge_plan(...)`: one test per eligibility
  step 1-7 (curate-command "Automatic Merge Eligibility Is Fail-Closed"),
  each asserting the recorded deferral reason; (A,B)+(B,C) admits exactly
  one; survivor pinned from `ordered_merge_pair`.
- [ ] 3.4 [IMPL] `AUTO_MERGE_THRESHOLD = <t* from the verdict file>` and
  `AUTO_MERGE_MEASURED_MODELS = frozenset({"<model>"})` in
  `application/lifecycle.py` with a docstring citing the verdict file;
  `auto_merge_plan` as a pure function beside `preview_apply_same`.
- [ ] 3.5 [MUT] Flip `>=` to `>` on the threshold check; and drop step 6;
  each goes RED on its own test. Revert, purge `__pycache__`.

## Phase 4 — `curate --auto-merge` (PASS only)

- [ ] 4.1 [TEST] Flag surface: off by default is byte-identical to today;
  `--auto-merge --reconcile` and `--auto-merge --accept identity` exit 2
  before the workspace gate; unmeasured model applies nothing and prints
  the notice; `review: false` does not enable it.
- [ ] 4.2 [TEST] Automatic pass on a pipe merges an eligible pair with no
  prompt; deferred and ineligible pairs take the existing walk/hint; the
  pass never plans reconciliation (`reconcile_planned(..., no_reconcile=True)`).
- [ ] 4.3 [TEST] One commit per run: two eligible pairs → exactly one new
  commit containing both merges and one `**Auto-merge**` bullet; each
  merge's own `**Merge**` bullet present; zero eligible → no bullet, no
  commit.
- [ ] 4.4 [IMPL] The automatic pass in `cli/curate.py`'s Identity stage via
  the lifecycle service (prepare with pinned pair, drift check,
  `merge_core`), then the run bullet and one `_autocommit`; mid-run failure
  stops, writes the bullet for applied merges, commits them.
- [ ] 4.5 [MUT] Move `_autocommit` inside the loop; 4.3 goes RED. Revert.

## Phase 5 — Disclosure and reversibility (PASS only)

- [ ] 5.1 [TEST] stderr summary line (including zero), per-merge lines with
  confidence and the exact `openkos unmerge` command; run summary counts
  automatic merges separately; failed-pass disclosure.
- [ ] 5.2 [TEST] Round trip: snapshot → auto merge (S, A) → `unmerge S A` →
  concept files, `index.md`, ledger byte-identical; `log.md` differs only by
  the run bullet and the unmerge audit line. Same pair via manual
  `merge --no-reconcile` reaches the same post-unmerge bytes.
- [ ] 5.3 [TEST] The `**Auto-merge**` bullet contains no
  `merge_log_entry(...)` substring for any pair it lists; `unmerge` of one
  of two auto merges keeps the other's bullet and the run bullet.
- [ ] 5.4 [IMPL] Disclosure through the shared commit-line helper and one
  new stderr formatter.
- [ ] 5.5 [MUT] Make the run bullet reuse `merge_log_entry` text; 5.3 goes
  RED (and `unmerge` refuses). Revert.

## Phase 6 — Docs and archive preparation (PASS only)

- [ ] 6.1 [DOC] `docs/cli.md` curate entry: `--auto-merge`, what it merges,
  how to undo. `docs/knowledge-object-model.md` Merge section: one sentence
  that the measured class may be applied after-the-fact reviewable
  (ADR-0034). No counts, no "since #NNN".
- [ ] 6.2 [CHECK] `openkos curate --help` matches `docs/cli.md`; run
  `lint` and `status` on `examples/good-life-demo/` unchanged.
- [ ] 6.3 [ARCHIVE-NOTE] At archive: flip ADR-0034 to Accepted in both
  places and its README row; reword entity-resolution-merge Non-Goals
  "automatic no-confirm merge" (see that delta's header note); name-match
  every MODIFIED heading against the canonical spec before merging.
