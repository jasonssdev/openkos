# Apply progress: superseded-history-in-query

## Slice 1 (PR 1 -> `main`): the bounded event-date resolver -- DONE

Tasks 1.1-1.13 complete. Slice 2a/2b/3 not started.

### Commits (on `feat/1014-superseded-history`, targeting `main`)

- `5b1d6d6` -- `feat(retrieval): add bounded event-date resolver`
  (`src/openkos/event_dates.py`, `tests/unit/test_event_dates.py`,
  ADR-0026 + its `docs/adr/README.md` row, staged as-is)
- `ffd7a03` -- `feat(resolution): alias DateState to the shared event-date leaf`
  (`src/openkos/resolution/decision_revision.py`,
  `tests/unit/resolution/test_decision_revision.py`)

`git diff --stat main...HEAD` (against `origin/main`, which local `main` was
fast-forwarded to match): 6 files changed, 536 insertions(+), 3 deletions(-).
Under the 400-line budget for authored-behavior lines once tests are
weighed against the ~280-line estimate; the increase is deliberate
triangulation coverage (see Deviations below), not scope creep.

### TDD Cycle Evidence

| Task | Test file | RED (observed) | GREEN | Mutation(s) caught |
|---|---|---|---|---|
| 1.1 | `tests/unit/test_event_dates.py` | `ImportError: cannot import name 'event_dates' from 'openkos'` (module moved aside to force genuine RED, then restored) | 12/12 passed | missing/multiple check-order swap; dropped `admit` refusal check |
| 1.2 | same | same RED (file didn't exist) | passed | recursing past hop 2 (nested non-Source entry followed) |
| 1.3 | same | same RED | passed | traversing into a refused intermediate's `sources/` entries (confirmed via `Path.read_text` call spy) |
| 1.4 | `src/openkos/event_dates.py` (impl) | n/a (impl task) | all of 1.1-1.3 green | see above |
| 1.5 | same test file | same RED (module didn't exist) | passed | disallowed import (`openkos.resolution.similarity`) added and caught |
| 1.6 | `tests/unit/resolution/test_decision_revision.py` | `AssertionError` on the source-text check (`"DateState = event_dates.DateState" in source` was `False`) -- see Deviations: the pure `is` check does NOT RED today because `typing.Literal` caches identical specs | passed after 1.7 | reintroducing the local `Literal["dated", "missing", "multiple", "none-reached"]` line -- caught by the source-text check, NOT by `is` alone |
| 1.7 | `src/openkos/resolution/decision_revision.py` (impl) | n/a (impl task) | 1.6 green; full 74-test file green | see above |
| 1.8 | (CHECK, no new test) | n/a | 74/74 passed, unaffected beyond 1.6 | n/a |

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/test_event_dates.py tests/unit/resolution/test_decision_revision.py` -> 86 passed |
| Runtime harness | `uv run python evals/run_self_tests.py` -> 43/43 harness self-tests green, incl. `evals/decision_revisions/run_decision_revisions_eval.py` (confirms the `DateState` alias end to end, not just import-compatible) |
| Rollback boundary | Revert `src/openkos/event_dates.py`, the one-line alias in `resolution/decision_revision.py` (restore the local `Literal`), and `tests/unit/test_event_dates.py`/the added test in `test_decision_revision.py`. ADR-0026 and its README row stay (staged as-is, not authored by this slice). |

### Full verification (this session)

- `uv run ruff check .` -> All checks passed! (after fixing an unused `noqa` and splitting one multi-part assert)
- `uv run ruff format --check .` -> 322 files already formatted
- `uv run mypy .` -> Success: no issues found in 322 source files
- `OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/run_self_tests.py` -> 43 of 43 harness self-test(s) run, 0 failing
- `uv run pytest` (full, unpiped) -> 6611 passed, 2 skipped in 352.76s (skips are pre-existing platform-gated tests, unrelated to this slice)

### Deviations from design.md

1. **`TYPE_TO_LINK_DIR["Source"]` does not exist.** design.md Decision 1's
   code snippet says a Source entry is recognized by
   `f"{TYPE_TO_LINK_DIR['Source']}/"`. Verified directly:
   `model/types.py`'s `TYPE_TO_LINK_DIR` dict comprehension is filtered to
   `ot.llm_classifiable or ot.name in BUILDER_ONLY_TYPES`, and `Source` is
   neither (`llm_classifiable=False`, and `BUILDER_ONLY_TYPES` holds only
   `Insight`) -- so `TYPE_TO_LINK_DIR["Source"]` raises `KeyError`.
   Implemented with the literal `"sources/"` prefix instead, matching the
   actual convention already used at `bundle/provenance.py:283` and
   `cli/main.py:7521` (`entry.startswith("sources/")`). `event_dates.py`
   therefore does not import `openkos.model.types` at all -- test 1.5's
   guard permits, but does not require, that import.
2. **Task 1.6's stated RED does not reproduce on this interpreter.**
   `typing.Literal` caches identical literal specs (`_tp_cache`), so two
   independently constructed `Literal["dated", "missing", "multiple",
   "none-reached"]` objects are already `is`-identical -- confirmed
   directly in a REPL. A pure identity assertion
   (`decision_revision.DateState is event_dates.DateState`) is therefore
   GREEN even before 1.7's edit, which would have made the RED/GREEN cycle
   for 1.6 vacuous and the test unable to catch the exact mutation it
   claims to kill (per this project's "a test that passes first try"
   practice). Strengthened the test with a source-inspection check
   (`inspect.getsource`) asserting the alias assignment literally appears
   and the local `Literal[` definition does not -- this DOES RED today and
   DOES catch the reintroduction mutation (both verified). The `is`
   assertion is kept as documentation/defense-in-depth but is not, by
   itself, load-bearing.
3. **Added one extra triangulation test**
   (`test_missing_precedes_multiple_in_aggregation`), not explicitly named
   in tasks.md, to make the "swap missing/multiple check order" mutation
   target (named in both tasks.md 1.1 and design.md's Testing Strategy
   table) actually catchable: none of the four originally-planned
   parametrize cases combine an unusable Source with multiple distinct
   dates in the same resolution, so the swap was previously silent. Kept
   the four originally-described cases too (`dated`, `missing` x4 variants,
   `multiple`, `none-reached` x2 variants) via `pytest.mark.parametrize`,
   consolidated into 4 test functions rather than one heavily-branched
   parametrize, for readability.

No other deviations. `resolve_event_date`'s signature, aggregation order,
one-hop bound, and `admit`-gate semantics match design.md Decision 1
exactly.

## Slice 2a (PR 2a -> PR 1's branch): the pure chain walk and nested budget -- DONE

Tasks 2.1-2.17 complete. Slice 1 already landed on `main` (PR #1027,
`a458ff8`); this batch built on top of it on `feat/1014-history-walk`, which
was even with `main` at the start of this batch. Slice 2b/3 not started.

### Commits (on `feat/1014-history-walk`, targeting PR 1's branch / `main`)

- `6271395` -- `feat(retrieval): add bounded revision-history walk`
  (`src/openkos/retrieval/history.py`, `tests/unit/retrieval/test_history.py`)
- `6a25784` -- `feat(retrieval): add nested budget split for revision history`
  (`src/openkos/prompt_budget.py`, `tests/unit/test_prompt_budget.py`)

`git diff --stat main...HEAD`: 4 files changed, 603 insertions(+). Over
design.md's own ~380-line estimate for this slice (194 lines of
`history.py`, 88 lines of `prompt_budget.py` delta, 244+77 lines of tests);
the increase is deliberate triangulation and mutation-discriminating test
coverage (see Deviations), not scope creep. This slice's own PR boundary was
fixed by the tasks.md/design.md split (2a is its own PR by the pre-agreed
`auto-chain`/`stacked-to-main` plan), so no further splitting was performed
within this batch.

### TDD Cycle Evidence

| Task | Test file | RED (observed) | GREEN | Mutation(s) caught |
|---|---|---|---|---|
| 2.1 | `tests/unit/retrieval/test_history.py` | `ImportError: cannot import name 'history' from 'openkos.retrieval'` (module moved aside to force genuine RED, then restored) | 8/8 passed | sort key reversed (`revises` before `supersedes`) |
| 2.2 | same | same RED (module didn't exist) | passed | holder_id tracking (verified by construction; not separately mutated) |
| 2.3 | same | same RED | passed | see Deviations: `depth == MAX_DEPTH` -> `>` is an EQUIVALENT MUTANT given `MAX_DEPTH == MAX_BLOCKS == 3` -- proven not to reproduce, empirically confirmed against the full suite |
| 2.4 | same | same RED | passed | cap-check placement (moved after the read) -- caught only after strengthening the test with a `reader.calls` assertion; vacuous `truncated=False` fallback |
| 2.5 | same | same RED | passed | removing the `visited.add` call (cycle guard) -- confirmed the walk still terminates via the independent cap, no infinite loop |
| 2.6 | same | same RED | passed | expanding a skipped node's edges |
| 2.7 | same | same RED | passed | removing the `if result is None: continue` guard (crashes on unpacking `None`) |
| 2.8 | `src/openkos/retrieval/history.py` (impl) | n/a (impl task) | 2.1-2.7 green first try except 2.1's own fixture bug (see Deviations) | see above |
| 2.9 | same test file | `AttributeError` — `date_phrase`/`label_note` didn't exist | passed | currency wording swap (`still current` <-> `no longer current`); earliest/latest order swap in the `multiple` phrase |
| 2.10 | `src/openkos/retrieval/history.py` (impl) | n/a (impl task) | 2.9 green | see above |
| 2.11 | `tests/unit/test_prompt_budget.py` | `AttributeError: module 'openkos.prompt_budget' has no attribute 'nested_shares'` | passed | outer computed as `budget - Σoverhead` instead of reusing `fair_shares` unchanged (caught by 2.13's test, not 2.11's -- see Deviations) |
| 2.12 | same | same RED | passed | (see Deviations: needed an added test, `test_nested_shares_slack_funds_the_nested_pool`, to catch skipping the slack stage -- 2.12's own "total fits budget" property held even under that mutation) |
| 2.13 | same | same RED | passed | dropped-group fallback using `head_sizes[i]` instead of `outer[i]` |
| 2.14 | `src/openkos/prompt_budget.py` (impl) | n/a (impl task) | 2.11-2.13 green first try | see above |
| 2.15 | (CHECK) | n/a | ruff/ruff format/mypy all green, repo-wide | n/a |
| 2.16 | (CHECK) | n/a | full `pytest`: 6623 passed, 2 skipped (same 2 pre-existing platform-gated skips slice 1 noted) | n/a |
| 2.17 | (CHECK) | n/a | 2 work-unit commits, scope `retrieval` | n/a |

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/retrieval/test_history.py tests/unit/test_prompt_budget.py` -> 45 passed |
| Runtime harness | N/A -- `history.py` and `nested_shares` have no caller yet (slice 2b wires them into `answer.py`); `OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/run_self_tests.py` -> 43/43 harness self-tests green, confirming `_bound_bodies` and every eval call site stayed untouched |
| Rollback boundary | Revert `src/openkos/retrieval/history.py`, the `GroupShares`/`nested_shares` addition to `src/openkos/prompt_budget.py` (the two commits above), and their two test files; nothing outside this slice references either symbol yet |

### Full verification (this session)

- `uv run ruff check .` -> All checks passed!
- `uv run ruff format --check .` -> 324 files already formatted
- `uv run mypy .` -> Success: no issues found in 324 source files
- `OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/run_self_tests.py` -> 43 of 43 harness self-test(s) run, 0 failing
- `uv run pytest` (full, unpiped) -> 6623 passed, 2 skipped in 351.85s (same 2 pre-existing platform-gated skips, unrelated to this slice)

### Deviations from design.md

1. **Task 2.3's named mutation (`depth == MAX_DEPTH` becomes `depth >
   MAX_DEPTH`) is an EQUIVALENT MUTANT given `MAX_DEPTH == MAX_BLOCKS ==
   3`, and is NOT actually catchable by any test of `walk_history`'s public
   contract.** Verified empirically: applying the mutation and running the
   full `test_history.py` suite (all 8 tests) leaves every test GREEN.
   Proof sketch: reaching a genuinely-fresh (unvisited, unskipped)
   successfully-read node at depth D via a single ancestor chain requires
   exactly D prior successful attaches along that chain -- any OTHER
   branch's attach would only add to the total, which (since the cap-check
   `break`s the ENTIRE walk once `len(attached) == MAX_BLOCKS`) would fire
   the cap before this chain ever reaches depth D itself, if it would push
   the total past `MAX_BLOCKS` first. So reaching an attached node at depth
   `MAX_DEPTH` (3) always coincides with `len(attached) == MAX_BLOCKS`
   (also 3) at that exact moment. Consequently, whatever the depth-check's
   own edge-visited/skip filtering would decide, the very next queue pop
   hits the cap-check (or an already-visited/skip `continue`) and produces
   the IDENTICAL `truncated` value the depth-check would have computed --
   for every topology, not just chains, because the same `break` argument
   applies to any branch structure. This is a genuine property of the
   chosen constants (both fixed at 3 by design.md itself), not a defect in
   the implementation or an admission of a weak test: `walk_history`
   correctly implements Decision 2's algorithm verbatim, including the
   `depth == MAX_DEPTH` branch, which is presently vestigial with respect
   to observable output but would matter the moment either constant
   changed independently of the other. `test_depth_bound` is kept as-is:
   it is still a faithful, meaningful test of the STATED depth-bound
   behavior (a 4-chain truncates at 3, a 3-chain does not), matching the
   delta spec's own scenario wording; it just cannot discriminate this one
   specific boundary-operator mutation given the current constants.
2. **Task 2.1's own test fixture had a bug on first write, unrelated to the
   implementation.** The "BFS level by level" half of
   `test_walk_order_mixed_edges_and_siblings` initially omitted the
   `reader_levels` entries for the depth-2 children (`p1`/`q1`), so the
   fake reader returned `None` for them (an accidental refused-read) and
   the test failed by returning only 2 predecessors instead of 3. Fixed by
   adding the missing entries; the implementation was correct on first
   write for this task (7 of 8 tests passed before this fix, and the fixed
   test passed immediately after, with no production-code change needed).
3. **Task 2.4's test needed strengthening beyond tasks.md's literal
   description to actually catch the named "cap-check placement"
   mutation.** Moving the cap-check from before the `read()` call to after
   it (still before attaching) left `predecessors`/`truncated` byte-for-byte
   identical -- the target simply got an extra, wasted `read()` call before
   being capped. Added `assert reader.calls == ["p1", "p2", "p3"]` to
   `test_block_cap` (design.md Decision 2 step 3 explicitly orders the cap
   check before the read), which does catch this mutation.
4. **Task 2.12's test does not catch "skipping the slack stage" on its
   own; added `test_nested_shares_slack_funds_the_nested_pool`.**
   `test_nested_shares_total_fits_budget`'s only invariant (total <=
   budget) still holds even when the slack stage is replaced with
   `extras = [0, ...]` -- a more conservative allocation trivially stays
   within budget too. Added a dedicated test matching the delta spec's own
   scenario ("Unspent budget lets a small history block fit without
   excerpting"): a hit well under budget with a small history block, where
   funding the block purely from slack (not carving into the hit's own
   share) is the only way `groups[0].head` stays at the hit's full
   `fair_shares` value. This test does discriminate the mutation
   (confirmed: `head` drops from `10` to `5` under the mutation).
5. **Task 2.11's named mutation is caught by 2.13's test, not 2.11's own,
   and only when overheads are non-uniform.**
   `test_nested_shares_no_history_equals_fair_shares` uses
   `inner_overheads = [0, ...]` throughout, so `budget - Σoverhead ==
   budget` and the mutation (outer computed as `budget - Σoverhead`) is
   invisible to it by construction.
   `test_nested_shares_dropped_group_keeps_outer_share` (2.13) uses a
   non-zero overhead and does catch it. No new test was added for this one
   since an existing test already discriminates it; noted here for
   transparency about which test actually does the catching.

No other deviations. `walk_history`'s algorithm (edge filter/sort, BFS
order, visited/skip/cap/depth semantics, `holder_id` tracking) and
`nested_shares`'s arithmetic (outer/slack/need/extra/pool/split, the two
drop conditions, the two pass-through cases) match design.md Decisions 2
and 4 exactly. `_bound_bodies` was not touched (confirmed via the full
`evals/run_self_tests.py` harness run and the full `pytest` suite, both
green with no changes outside the two new/modified files).

## Slice 2b (PR 2b -> PR 2a's branch): wiring `answer()` to walk, attach, budget, cite, disclose -- DONE

Tasks 3.1-3.38 complete. Slices 1 and 2a already landed (PR #1027, PR
#1028); this batch built on top of both on `feat/1014-history-wiring`,
which held both merged commits (`a458ff8`, `ebc209f`) at the start of this
batch. Slice 3 not started.

### Commits (on `feat/1014-history-wiring`, targeting PR 2a's branch / `main`)

- `fb32acc` -- `feat(retrieval): attach revision history to answer() context`
  (`src/openkos/retrieval/answer.py`, `tests/unit/retrieval/test_answer.py`,
  `tests/unit/retrieval/test_layering.py` (new))

`git diff --stat main...HEAD`: 3 files changed, 1486 insertions(+), 23
deletions(-). Well over design.md's own ~400-line estimate for this
slice (~150 source, ~250 tests); the increase is deliberate triangulation
and mutation-discriminating test coverage across all 36 task-defined
scenarios (attach/label/currency, send-time guards, dedupe, the nested
budget split's 6 scenarios, citation/attribution/truncation, the off-path
proofs, `--include-deprecated` suppression, and the layering guard), not
scope creep. This is a single self-contained work-unit commit rather than
the two the tasks.md suggestion named (`_guarded_read` extraction vs.
attach/budget/citation/truncation wiring): the two are tightly
interdependent in one file (the wiring calls the extracted helper
directly, and every new test exercises both together), and a safe,
non-fragile hunk split was not attempted under this batch's time budget.
Slice 2b's own PR boundary was fixed by the pre-agreed tasks.md/design.md
4-way split (2b is its own PR, already narrowed from a single larger
"retrieval slice" per design.md's own note), so no further splitting was
performed within this batch; the overage is reported here rather than
iterated against, per this project's "budget is not code-golf" practice.

### TDD Cycle Evidence

| Task | Test file | RED (observed) | GREEN | Mutation(s) caught |
|---|---|---|---|---|
| 3.1 | (CHECK) | n/a | baseline `uv run pytest tests/unit/retrieval/test_answer.py` -> 118 passed | n/a |
| 3.2 | (IMPL, pure refactor) | n/a | 118 passed, unchanged (regression net) | n/a |
| 3.3-3.4 | `tests/unit/retrieval/test_answer.py::test_history_block_attached_with_label_and_citation` | `TypeError: _assemble_context() got an unexpected keyword argument 'revision_history'` | passed | holder id replaced by top-level successor (confirmed via live mutation) |
| 3.5-3.6 | `test_date_phrase_rendering_end_to_end` | same RED (no `revision_history` kwarg) | passed | n/a (3.6 is N/A by construction; confirmed GREEN, no design violation) |
| 3.7-3.8 | `test_predecessor_send_time_guards` | same RED | passed | see 3.10-3.11 (admit-gate mutations) |
| 3.9 | `test_refused_predecessor_is_a_dead_end` | same RED | passed | n/a (walk-level dead-end already pinned in slice 2a; this re-confirms with the real `_guarded_read`) |
| 3.10-3.11 | `test_confidential_source_date_shown_as_unknown` | same RED | passed | sensitivity gate bypassed for predecessors (`_admit` forced `True`); `admit=` dropped from the `resolve_event_date` call -- both confirmed via live mutation |
| 3.12-3.13 | `test_dedup_rules` | same RED | passed | dedupe removed (`skip=frozenset()`) -- confirmed via live mutation |
| 3.14-3.21 | `test_budget_isolation_unrelated_hits_unchanged`, `test_bound_with_history_overhead_uses_hit_labels_only`, `test_fit_within_todays_bound`, `test_unspent_budget_lets_small_history_fit_without_excerpting`, `test_fully_spent_window_splits_within_own_share`, `test_oversized_history_block_excerpted_not_dropped`, `test_zero_share_history_block_dropped_and_disclosed`, `test_history_group_dropped_entirely_when_it_would_zero_the_successor` | `AttributeError: module has no attribute '_bound_with_history'` (or same `TypeError` at the `_assemble_context` call sites) | all 8 passed | unrelated hit body changed (`overhead_hits` measured over the flat label pool instead of hit labels only) -- confirmed via live mutation; the two zero-share/drop tests hand-derive their exact `nested_shares` arithmetic and verify via `monkeypatch` on `prompt_budget.budget_chars` rather than reverse-engineering a token/char ratio |
| 3.22-3.23 | `test_used_naming_and_citation_history_field` | `AttributeError`/`TypeError` (no `history` field on `Citation`, no `revision_history` kwarg) | passed | n/a |
| 3.24 | `test_context_block_count_includes_history` | same RED | passed | n/a |
| 3.25-3.27 | `test_truncation_signal_cap_and_depth_causes`, `test_disabled_reports_no_truncation` | `AttributeError` (`AnswerResult` has no `history_truncated_titles`) | both passed | truncation flag not set on depth/cap cut (`if False and history_truncated_out is not None:`) -- confirmed via live mutation |
| 3.28-3.30 | `test_off_path_zero_extra_reads`, `test_off_path_messages_byte_identical` | `TypeError` on the `revision_history=True` call, establishing the contrast | both passed | walk runs with the kwarg off (`if True:` instead of `if revision_history:`) -- confirmed via live mutation on both tests |
| 3.31-3.32 | `test_include_deprecated_suppresses_history_walk` | same RED as above | passed | include_deprecated ignored (`walk_history_enabled = revision_history`) -- confirmed via live mutation, using a strengthened fixture where the predecessor's OWN edge target (never itself a hit) is the only way to observe the walk running under the flag |
| 3.33-3.34 | `tests/unit/retrieval/test_layering.py` (new) | n/a (3.33's two guards confirmed GREEN by construction from earlier tasks; 3.34 is N/A) | 4/4 passed | n/a |

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/retrieval/test_answer.py tests/unit/retrieval/test_layering.py` -> 171 passed |
| Runtime harness | `OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/run_self_tests.py` -> 43/43 harness self-tests green, confirming `evals/query_sufficiency/run_query_sufficiency_probe.py:297`, `evals/query_entailment/run_query_entailment_probe.py:348,871`, and `evals/query_attribution/run_query_attribution_probe.py:806,839`'s `_assemble_context`/`_bound_bodies` call sites are unaffected -- no CLI/config path enables the kwarg until slice 3 |
| Rollback boundary | Revert the single commit `fb32acc` (`src/openkos/retrieval/answer.py`, `tests/unit/retrieval/test_answer.py`, `tests/unit/retrieval/test_layering.py`); `answer()`'s default `revision_history=False` means no caller anywhere is affected by a full revert |

### Full verification (this session)

- `uv run ruff check .` -> All checks passed!
- `uv run ruff format --check .` -> 325 files already formatted
- `uv run mypy .` -> Success: no issues found in 325 source files
- `OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/run_self_tests.py` -> 43 of 43 harness self-test(s) run, 0 failing
- `uv run pytest` (full, unpiped) -> 6648 passed, 2 skipped in 350.17s (same 2 pre-existing platform-gated skips slices 1/2a noted, unrelated to this slice)

### Live mutation testing (this session)

Per this project's "a test that passes first try" and
"mutation-pycache-invalidates-verdicts" practice, `__pycache__` was purged
before every verdict and every mutation was reverted by its exact inverse
edit (never `git checkout --`). All 8 orchestrator-named mutation classes
were exercised live against the actual committed code and confirmed
caught by name above: sensitivity gate bypassed for predecessors;
`admit` not passed (confidential Source date leaks); dedupe removed; walk
runs with the kwarg off (both off-path tests); include_deprecated
ignored; unrelated hit body changed (flat-pool overhead); holder id
replaced by top-level successor; truncation flag not set on depth/cap
cut. One weak test was caught and strengthened in the process: the
initial `test_include_deprecated_suppresses_history_walk` fixture let the
fused-set dedupe (Decision 3) mask the mutation entirely (the
predecessor was ALSO an ordinary hit, so it was skipped either way);
fixed by adding a second-hop predecessor reachable only through the
walk itself.

### Deviations from design.md

1. **Single work-unit commit instead of two.** See "Commits" above --
   the extraction and the wiring are inseparable in this file without a
   fragile hunk split; reported rather than risked.
2. **`_bound_with_history`'s exact internal shape (the `holders` grouping
   convention, `_classify_bound` helper) is this implementer's own
   design**, since design.md's Interfaces section names the function's
   signature and contract but not its internals. The nested-shares
   arithmetic itself is untouched, reused verbatim from slice 2a.
3. **Two budget-arithmetic tests
   (`test_zero_share_history_block_dropped_and_disclosed`,
   `test_history_group_dropped_entirely_when_it_would_zero_the_successor`)
   monkeypatch `prompt_budget.budget_chars` to an exact target value**
   rather than solving a token/char ratio by hand, because
   `TOKENS_PER_CHAR=0.4` (`=2.5` chars per token) does not hit every
   integer char-budget target exactly as `context_window` is swept --
   confirmed empirically, then this precise, zero-flakiness alternative
   was substituted. `test_budget_isolation_unrelated_hits_unchanged`'s
   own claim (flat-pool overhead mutation) similarly needed a SECOND,
   more surgical test
   (`test_bound_with_history_overhead_uses_hit_labels_only`) added
   alongside it, because the original integration-level fixture's
   `bounded_text` window-count search absorbed the small overhead delta
   at that body/window scale and did not discriminate the mutation --
   confirmed via a live mutation run that passed against the original
   test and failed against the new one.
4. **`test_used_naming_and_citation_history_field`'s first draft used
   `USED: 2` (naming only the history block) for its "ordinary hit
   citation carries history=None" assertion**, which made the hit's own
   citation absent from the result entirely (`StopIteration`) rather than
   present-with-`history=None` -- fixed to `USED: 1, 2` so both citations
   survive #753's attribution filter and the assertion is meaningful.
5. **`test_off_path_zero_extra_reads`'s original `count_default == 1`
   assertion was wrong**: the pre-existing bundle-wide
   `lifecycle.deprecated_concept_ids`/`sensitivity.sensitive_concept_ids`
   walks (status-aware-retrieval, sensitivity-fail-closed-filter) also
   read every file in the bundle via `okf._iter_docs`, independent of
   `revision_history` -- fixed to compare `count_default ==
   count_explicit_false` instead, which is what the requirement actually
   claims.
6. **The layering test's naive `"config.read_config" not in source`
   substring check false-failed on `answer.py`'s own module docstring
   prose** (which discusses `config.read_config`'s floor in an unrelated
   historical note) -- replaced with an AST-based check that no
   `.read_config` attribute access exists in the code at all.

No other deviations. `_assemble_context`'s attach/label/currency/dedupe
logic, `_bound_with_history`'s nested-split wiring, `Citation.history`,
`AnswerResult.history_truncated_titles`, and `answer()`'s
`revision_history`/`include_deprecated` interaction match design.md
Decisions 2-8 exactly; `_bound_bodies` and the pre-feature
`_assemble_context` tail are byte-for-byte untouched (confirmed via the
full `evals/run_self_tests.py` harness run and the full `pytest` suite,
both green).

## Slice 3 (PR 3 -> PR 2b's branch): the config, CLI, and `--save` surface -- DONE

Tasks 4.1-4.22 complete. Slices 1, 2a and 2b already landed on `main`
(PR #1027, PR #1028, PR #1029); this batch built on top of all three on
`feat/1014-history-surface`, which was even with `main` (holding all three
merged commits) at the start of this batch.

### Commits (on `feat/1014-history-surface`, targeting `main`)

- `10e9a87` -- `feat(config): add revision_history key`
  (`src/openkos/config.py`, `src/openkos/templates/openkos.yaml.template`,
  `examples/good-life-demo/openkos.yaml`, `tests/unit/test_config.py`)
- `783b328` -- `feat(okf): add optional related_notes to build_concept`
  (`src/openkos/model/okf.py`, `tests/unit/model/test_okf.py`)
- `990718c` -- `feat(query): thread revision_history and mark history
  citations on save` (`src/openkos/application/query.py`,
  `tests/unit/application/test_query_service.py`,
  `tests/unit/application/test_query_filing.py`)
- `7e15065` -- `feat(cli): render revision-history citation markers and
  truncation notice` (`src/openkos/cli/main.py`, `tests/unit/cli/test_query.py`)
- `3069f11` -- `docs: document the revision_history query surface`
  (`docs/cli.md`)

`git diff --stat main...HEAD`: 12 files changed, 516 insertions(+), 2
deletions(-). Over design.md's own ~320-line estimate for this slice; the
increase is the same pattern as every earlier slice in this change --
deliberate triangulation and mutation-discriminating test coverage across
config validation, `build_concept`'s new parameter, `stage_filed_answer`'s
marking, and the CLI marker/notice rendering, not scope creep. This is the
FINAL slice in the pre-agreed 4-way `auto-chain`/`stacked-to-main` split
(design.md "Migration / Rollout"), and the assigned scope (config key,
`run_query`/`stage_filed_answer` threading, `build_concept`'s
`related_notes`, the two CLI markers, the truncation notice, and
`docs/cli.md`) cannot be usefully sliced smaller without breaking one of
those cohesive, individually-tested units into an incomplete PR. Reported
as `size:exception` rather than iterated against, per this project's
"budget is not code-golf" practice (also applied in slice 2b).

### TDD Cycle Evidence

| Task | Test file | RED (observed) | GREEN | Mutation(s) caught |
|---|---|---|---|---|
| 4.1-4.3 | `tests/unit/test_config.py` | `AttributeError: 'Config' object has no attribute 'revision_history'` (absent/null/true cases); `DID NOT RAISE ValueError` (non-bool cases) | 6/6 passed (incl. template + leftover-key regression pins) | default flipped to `True` -- confirmed via live mutation |
| 4.4-4.5 | `tests/unit/application/test_query_service.py::test_run_query_threads_revision_history` | `KeyError: 'revision_history'` -- `answer` spy never received the kwarg | passed | key not threaded to `answer()` (hard-coded `revision_history=False`) -- confirmed via live mutation |
| 4.6-4.7 | `tests/unit/model/test_okf.py::test_build_concept_related_notes` | `TypeError: build_concept() got an unexpected keyword argument 'related_notes'` | passed | mapped-fallback dropped (`.get(ref, related_note)` -> `.get(ref, '')`) -- confirmed via live mutation |
| 4.8-4.9 | `tests/unit/application/test_query_filing.py::test_stage_filed_answer_marks_history_citations` | marked bullet text absent -- default `related_note` rendered for every citation | passed | `related_notes` filter dropped (built for every citation, not just history ones) -- confirmed via live mutation, caught by BOTH the marking test and the byte-identical-with-no-history test; `related_notes=None` forced at the `build_concept` call site -- also confirmed via live mutation |
| 4.10-4.11 | `tests/unit/cli/test_query.py::test_citation_history_markers` | `[superseded]`/`[refined]` never rendered | passed | markers swapped (`superseded`<->`refined`) -- confirmed via live mutation |
| 4.12-4.13 | `tests/unit/cli/test_query.py::test_history_truncation_stderr_notice(_names_multiple_titles)`, `test_no_history_truncation_prints_no_notice` | notice never printed (single-title/multi-title cases); n/a for the "no notice" case (already true) | all passed | notice printed unconditionally (`if True:`) -- confirmed via live mutation against the "no truncation" test |
| 4.14-4.15 | `tests/unit/test_config.py::test_the_template_documents_the_revision_history_key` | `AssertionError: 'revision_history' not in template` | passed | n/a (structural: template text presence) |
| 4.16-4.17 | `tests/unit/test_config.py::test_read_config_ignores_a_leftover_revision_history_key` | already GREEN (regression pin on existing `read_config` behavior, per design) | passed | n/a -- no production change (4.17 is `[IMPL] N/A`) |
| 4.18 | (IMPL, docs) | n/a | n/a (docs, no test) | n/a |

### Work Unit Evidence

| Evidence | Value |
|---|---|
| Focused test command | `uv run pytest tests/unit/test_config.py tests/unit/test_canonical_example.py tests/unit/application/test_query_service.py tests/unit/application/test_query_filing.py tests/unit/model/test_okf.py tests/unit/cli/test_query.py tests/unit/cli/test_query_save.py` -> 828 passed |
| Runtime harness | `uv run openkos init`-equivalent coverage via `tests/unit/test_canonical_example.py::test_config_is_byte_identical_to_a_fresh_init` (confirms the template/example stay in lockstep with a real `write_config` run); `OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/run_self_tests.py` -> 43/43 harness self-tests green, confirming no eval call site regresses from the `Config`/`build_concept` signature changes |
| Rollback boundary | Revert the five commits above in reverse order (`3069f11`..`10e9a87`); `Config.revision_history` was added LAST and DEFAULTED, `answer()`'s `revision_history` default stays `False`, and `build_concept`'s `related_notes` defaults to `None` -- a full revert leaves every existing caller, and a stale `revision_history:` line in a user's `openkos.yaml`, unaffected (`read_config` ignores unknown keys) |

### Full verification (this session)

- `uv run ruff check .` -> All checks passed!
- `uv run ruff format --check .` -> 325 files already formatted
- `uv run mypy .` -> Success: no issues found in 325 source files
- `OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/run_self_tests.py` -> 43 of 43 harness self-test(s) run, 0 failing
- `uv run pytest` (full, unpiped) -> 6662 passed, 2 skipped in 344.88s (same 2 pre-existing platform-gated skips every earlier slice noted, unrelated to this slice)

### Live mutation testing (this session)

Per this project's "a test that passes first try" and
"mutation-pycache-invalidates-verdicts" practice, `__pycache__` was purged
before every verdict and every mutation was reverted by its exact inverse
edit (never `git checkout --`). All 6 orchestrator-named mutation classes
were exercised live against the actual committed code and confirmed
caught: key not threaded to `answer()`; default flipped to `True`; markers
swapped; notice printed when empty; `related_notes` default changing
existing output (the filter-drop mutation, caught by two tests at once);
history bullet missing from `--save` (`related_notes=None` forced). A
seventh, code-specific mutation (the `okf.build_concept` mapped-fallback
dropped) was also run and caught, since the design's own named example for
this scenario (`related_notes={}` default instead of `None`) is an
EQUIVALENT MUTANT against this implementation -- verified directly: with
`related_notes={}`, `.get(ref, related_note)` for every ref falls back to
`related_note` identically to the `None` path, so that specific default
value would produce byte-identical output and is not a meaningful mutation
to assert against for this code shape. No weak tests were found this
session; every named mutation was caught by the test written for it on the
first attempt.

### Deviations from design.md

1. **Docs paragraph placed as one consolidated block rather than scattered
   inline mentions.** design.md's File Changes table says `docs/cli.md`
   gets "A `query` note: the key, the markers, and that it is unmeasured
   and not recommended" -- implemented as a single paragraph inserted
   between the `sufficiency_check` paragraph and the `#882` prompt-bound
   paragraph, matching this doc's existing per-feature-paragraph structure
   rather than editing the flag table or other paragraphs.
2. **`stage_filed_answer`'s `related_notes` dict-comprehension mutation
   (dropping the `if citation.history` filter) is caught by BOTH the
   marking test and the byte-identical-with-no-history test**, not just
   one -- confirmed live: the ordinary-citation assertion in the marking
   test fails (`concept cited to produce this answer` becomes `earlier
   version (None) cited as history for this answer`), and the dedicated
   byte-identical test fails identically. Kept both tests since they cover
   different scenarios (mixed history + ordinary citations vs. zero
   history citations) even though this one mutation happens to fail both.
3. **Test file placement followed the repo's actual layout, not the
   tasks.md placeholder paths.** tasks.md named
   `tests/unit/application/test_query.py` (4.4, 4.8) and
   `tests/unit/cli/test_query_save.py` (4.10, 4.12) as adjustable
   placeholders; the real files are `tests/unit/application/
   test_query_service.py` (run_query/threading tests, matching its
   existing `test_run_query_*` naming) and `tests/unit/application/
   test_query_filing.py` (`stage_filed_answer` tests, matching its
   existing `test_stage_filed_answer_*` naming) for 4.4/4.8, and
   `tests/unit/cli/test_query.py` (not `test_query_save.py`) for 4.10/4.12,
   since that file already holds every other citation-marker/stderr-notice
   test (`[confidential]`, `[partial]`, `[synthesis]`, the omitted/excerpted
   notices) this feature's tests needed to sit beside.

No other deviations. `Config.revision_history`'s validation/threading,
`build_concept`'s `related_notes` contract, `stage_filed_answer`'s marking,
and the CLI markers/notice match design.md Decisions 6, 7 and 9 exactly;
the template and example stay byte-identical to each other and to a fresh
`write_config` run (confirmed by
`tests/unit/test_canonical_example.py::test_config_is_byte_identical_to_a_fresh_init`,
green both before and after this slice's edits).

### Status

13/13 slice-1 + 17/17 slice-2a + 38/38 slice-2b + 22/22 slice-3 tasks
complete (90/90 total -- every task in tasks.md is done). Slices 1, 2a and
2b already merged to `main` (PR #1027, PR #1028, PR #1029). This batch's
five commits sit on `feat/1014-history-surface`, which was even with
`main` at the start of the batch; per the pre-agreed `stacked-to-main`
chain, since PR 2b already merged, PR 3 now targets `main` directly (the
chain's own "once each PR merges in order, GitHub retargets the next PR
onto `main` automatically" clause). Ready for PR 3 (this branch) and, once
it merges, for `sdd-archive` to merge the three delta specs into their
living specs and flip ADR-0026 from `Proposed` to `Accepted` (per
tasks.md's "Post-merge (archive phase, not a task here)" section) --
verification beyond what is already reported above remains optional.
