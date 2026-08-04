# Tasks: extraction decides multiplicity per object, not per source

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | PR 1: ~40-80 (docs/report). PR 2: ~180-260 (one prompt constant, docstrings, ~10 new tests, spec delta) |
| 400-line budget risk | Low |
| Chained PRs recommended | Yes (proposal's two-slice plan, not size) |
| Suggested split | PR 1 (harness + D1 verdict) -> PR 2 (prompt revision + spec + tests) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Commit D1 verdict artifacts | PR 1 | N/A — docs only | `run_title_ab.py --arms h1,stem` (already run; frozen) | Revert the 5 committed files |
| 2 | DD1 title-label reframe | PR 2 | `pytest test_concept.py -k title` | `run_title_ab.py --arms h1` vs baseline | Revert `_build_messages` label |
| 3 | D2 rubric re-point | PR 2 | `pytest test_concept.py` | `--arms h1`; `multi_obj_rate` > 0.09 | Revert `concept.py:37` |
| 4 | D3 multiplicity paragraph | PR 2 | `pytest test_concept.py` | `--arms h1`; `call-with-maria` -> 3 | Revert added paragraph |
| 5 | D4 anti-twin clause | PR 2 | `pytest test_concept.py` | `--arms h1`; `twin_rate` < 0.34 | Revert added clause |
| 6 | Spec + fixture acceptance | PR 2 | `openspec validate --strict` | `openkos ingest` on call-with-maria (operator-assisted) | Revert spec delta |

## Phase 1: PR 1 — Package the D1 measurement (already run)

- [x] 1.1 `git status`; confirmed set: `report-title-ab.md` (modified), `design.md`/`specs/`/`tasks.md` (untracked); `proposal.md` already committed (a36b3dc).
- [x] 1.2 Commit `report-title-ab.md`. Note: `results/` is gitignored by design (`evals/model_spike/.gitignore:3`) — timestamped snapshots are local harness baselines, never committed; git history of the report is the durable record. The earlier `142601Z` local snapshot is superseded by `151148Z` (5 runs vs 2). Delivered via PR #390, single squash commit — receipt froze all four files as one candidate.
- [x] 1.3 Commit `design.md`, `specs/ingestion/spec.md`, `tasks.md` as the D1-verdict artifact commit. Delivered via PR #390, single squash commit — receipt froze all four files as one candidate.
- [x] 1.4 Open PR 1; body cites the Verdict section (twin_rate 0.34 vs 0.13; multi_obj_rate flat). Delivered via PR #390, single squash commit — receipt froze all four files as one candidate.
- [x] 1.5 Quality gate green: 3421 passed, coverage 97.20%, ruff check + format clean, mypy strict clean (2026-08-04).

## Phase 2: PR 2 / DD1 — reframe the title label (RED-first)

- [x] 2.1 RED: add a prompt-text test asserting the reframed non-authoritative `SOURCE TITLE` label in `_build_messages`'s user content; confirm it fails first. `test_prompt_frames_source_title_as_non_authoritative_metadata` added, confirmed failing before the edit.
- [x] 2.2 GREEN: edit `_build_messages` in `concept.py` per DD1; `derive_source_title`/`main.py:2688` untouched.
- [x] 2.3 Update the `_build_messages` docstring.
- [x] 2.4 Operator-assisted: `run_title_ab.py --arms h1` vs baseline; confirm no regression (DD1 shouldn't move rates). Gate run 20260804T162351Z (8-source subset × 3 runs, per-source comparison vs the frozen 151148Z h1 column): no source below its baseline count; call-with-maria still 1 (expected), notes-on-enchiridion still 3; 02-how-claude-code-works enumerated 5 in 2 of 3 runs (above baseline); twins unchanged (D4's gate); 1 empty run within baseline empty rate (4 of 90).

## Phase 3: PR 2 / D2 — re-point rubric per candidate (RED-first)

- [x] 3.1 RED: test asserting `concept.py:37` no longer asks "what the source is about" as one question, instead frames per-candidate application; confirm fails first. `test_prompt_repoints_rubric_to_candidate_objects_not_the_whole_source` added, confirmed failing before the edit.
- [x] 3.2 GREEN: edit the framing line per DD2; nine bullets and `CLASSIFIABLE_TYPES` untouched. New framing: "First identify the candidate distinct objects the source contains, then classify EACH candidate independently against the type rubric below:".
- [x] 3.3 Update `_SYSTEM_PROMPT` docstring to describe the per-candidate framing (design D2).
- [x] 3.4 Confirm the three DD3 alarm tests still pass unedited (3 passed, unmodified).
- [x] 3.5 Operator-assisted: `--arms h1` vs baseline; `multi_obj_rate` > 0.09, `call-with-maria` > 1; record run. Gate run 20260804T164504Z (8-source subset × 3): HALF-MET. Multi-object rate clearly above baseline (02-how-claude-code-works 5,5,1; 08-the-claude-file 5,3,1 — a former solid twin now decomposes; enchiridion stable 3,3,3). call-with-maria still 1,1,1 — deferred to D3's gate per design DD2. YELLOW FLAG: empties rose to 3 of 24 (06-mcp-client twice, 05-workflow once) vs 4 of 90 baseline — the #129 pendulum direction; D3's positive multiplicity test must bring instructional sources back to >=1 or D2 gets re-evaluated.

## Phase 4: PR 2 / D3 — multiplicity test paragraph (RED-first)

- [x] 4.1 RED: test asserting the new single-vs-multi-topic paragraph exists, additive next to (not inside) the anti-enumeration block; confirm fails first. `test_prompt_states_multiplicity_decision_test_adjacent_to_anti_enumeration` added, confirmed failing before the edit.
- [x] 4.2 GREEN: add the paragraph near `concept.py:122-134`. New paragraph: "Multiplicity is decided per subject, not per source: a source developing several distinct subjects -- e.g. a person discussed, an idea corrected, a decision made -- yields one object per subject, each classified independently. A source developing only one subject still yields exactly ONE object." Placed after the verbatim-pinned anti-enumeration paragraph, before the positive default paragraph. Byte delta: +298 bytes (6662 -> 6960); cumulative delta from the 6,573 baseline is +387 bytes (5.89%), still well under the 15% axis budget with D4 still pending.
- [x] 4.3 Add a fake-backend test: multi-topic reply parses to N `ExtractionResult`s. `test_multi_topic_reply_parses_to_n_extraction_results` added (Person+Concept+Decision, mirroring the call-with-maria fixture) — 3 results, distinct types, order preserved.
- [x] 4.4 Confirm the three DD3 alarm tests still pass unedited (3 passed, unmodified).
- [ ] 4.5 Operator-assisted: `--arms h1`; `call-with-maria` reaches 3 (Person+Concept+Decision); the five #129 instructional files still yield >=1; record run.

## Phase 5: PR 2 / D4 — anti-twin clause (RED-first)

- [ ] 5.1 RED: test asserting the anti-twin clause exists after the anti-enumeration paragraph; confirm fails first.
- [ ] 5.2 GREEN: add the clause, additive only.
- [ ] 5.3 Confirm the three DD3 alarm tests still pass unedited.
- [ ] 5.4 Operator-assisted: `--arms h1`; `twin_rate` < 0.34 with no `multi_obj_rate` regression; record as post-D4 snapshot.

## Phase 6: Spec, fixture acceptance, prompt-size check

- [ ] 6.1 Reconcile `specs/ingestion/spec.md` scenarios against what Phases 3-5 measured.
- [ ] 6.2 Operator-assisted: `openkos ingest` on `call-with-maria-2026-07-14.txt`; confirm the three declared objects are written.
- [ ] 6.3 Operator-assisted: full 18-source corpus re-run; mean count > 1, no twins, blank sources still `[]`.
- [ ] 6.4 Measure `_SYSTEM_PROMPT` byte growth vs the 6,573-byte baseline; must stay under ~15%; record in `design.md`.
- [ ] 6.5 Update the eval report with the final before/after across all three axes.

## Phase 7: Quality gate and PR 2

- [ ] 7.1 `pytest --cov` — confirm coverage of every new test.
- [ ] 7.2 `ruff check` + `ruff format --check` on `concept.py` and `test_concept.py`.
- [ ] 7.3 `mypy --strict` on the modified module.
- [ ] 7.4 Open PR 2; body cites per-axis measurements and the 6.2 fixture proof.
