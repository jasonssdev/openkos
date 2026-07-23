# Tasks: Status-Aware Retrieval (Gap #8 · S1)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~750-850 (5 files + 5 test files, incl. new `lifecycle.py` + tests) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 → PR2 → PR3 → PR4 |
| Delivery strategy | ask-on-risk (default; orchestrator to confirm) |
| Chain strategy | pending — user to choose stacked-to-main vs feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | New `lifecycle.py` predicate + `filter_hits` | PR 1 | `pytest tests/unit/test_lifecycle.py -v` | N/A — no consumer wired yet | Delete `src/openkos/lifecycle.py` + its test; nothing else imports it |
| 2 | Wire `answer()` query-path filtering | PR 2 | `pytest tests/unit/retrieval/test_answer.py -v` | N/A — CLI flag not wired until Unit 4 | Revert `answer.py` diff + test additions; contradiction/candidates/cli untouched |
| 3 | Wire `find_contradictions`/`find_candidates` filtering | PR 3 | `pytest tests/unit/resolution/test_contradiction.py tests/unit/resolution/test_candidates.py -v` | N/A — CLI flag not wired until Unit 4 | Revert both resolution diffs + tests; answer.py/cli untouched |
| 4 | Thread `--include-deprecated` through CLI | PR 4 | `pytest tests/unit/cli/ -v` | `openkos query "..." --include-deprecated` on a fixture workspace with a deprecated concept | Revert `cli/main.py` diff + CLI tests; library defaults stay `False` regardless |

## Phase 1: Foundation — `lifecycle.py` (PR 1)

- [x] 1.1 RED: `tests/unit/test_lifecycle.py` — own status=deprecated; superseded target deprecated regardless of own status; self-ref guarded live; mutual 2-cycle **both deprecated** (corrected R2, see 1.5); 3-cycle A→B→C→A all deprecated (R2 pin); 4-cycle-with-chord regression (all four deprecated, incl. the concept the review flagged as leaking); malformed `relations` → no edges, no crash; all-live bundle → empty set; `filter_hits` generic over `.concept_id`
- [x] 1.2 GREEN: create `src/openkos/lifecycle.py` — `deprecated_concept_ids(bundle_dir)` + `filter_hits(hits, deprecated)` per design (one `okf._iter_docs` walk, `okf.decode_relations`)
- [x] 1.3 REFACTOR: tidy walk/set-ops; docstring must state the pinned R2 rule (fail-safe: any non-self supersedes target is deprecated, no reciprocal-cancellation or cycle-length exemption)
- [x] 1.4 Verify: `pytest tests/unit/test_lifecycle.py -v`; confirm module imports only `okf` + stdlib
- [x] 1.5 CORRECTION (post-review): the original per-edge-pair reciprocal-cancellation rule cancelled `(target, source)` pairs individually, which let a cycle member escape deprecation when one of its edges also formed a mutual pair (CONFIRMED CRITICAL false-negative, counter-example `a→b, b→c, b→a, c→d, d→a` leaked `b`). Replaced with the simpler fail-safe rule: `superseded = {target for source, target in supersedes if target != source}` — no reciprocal exemption at all. Mutual 2-cycles now flip to fully-deprecated (was: both live). Flipped `test_mutual_two_cycle_*` to assert both deprecated; added `test_four_cycle_with_mutual_chord_marks_all_four_deprecated` regression.

## Phase 2: `answer()` Query Path (PR 2)

- [x] 2.1 RED: `tests/unit/retrieval/test_answer.py` — deprecated absent from `hits`/`vec_hits`/`graph_hits`/fused/citations by default; only-deprecated-match → `zero_hits` NO_MATCH; D→C neighbor (C surfaces on own merits, D never a hit); `include_deprecated=True` restores it AND skips the walk (spy); all-live bundle identical to pre-change; R3 pin — `fts_hit_count`/`dense_hit_count`/`graph_hit_count`/`fused_count` and the `retrieval:` line report POST-filter values
- [x] 2.2 GREEN: add `include_deprecated: bool = False` to `answer()`; filter `hits`/`vec_hits` pre-initial-fuse, `graph_hits` post-PPR via `lifecycle.filter_hits`. CORRECTION (caught by `mypy --strict` on `answer.py`, not by PR1's single-file check): `lifecycle.py`'s `_HasConceptId` Protocol declared `concept_id: str` as a plain (settable) attribute, but every real hit type (`FtsHit`/`VecHit`/`GraphHit`) is a frozen dataclass — read-only. mypy rejected all three as `H` until the Protocol member was narrowed to a read-only `@property`.
- [x] 2.3 REFACTOR: short-circuit the walk entirely when `include_deprecated=True`; update module docstring
- [x] 2.4 Verify: `pytest tests/unit/retrieval/test_answer.py -v`
- [x] 2.5 CORRECTION (post-review, reliability WARNING — coverage gap): added `test_deprecated_concept_never_becomes_a_graph_seed` closing a gap where no test proved a deprecated concept is withheld from the graph-stage SEED list specifically (all prior tests only asserted absence from the final output). Fixture: deprecated `D` is the sole FTS hit and would otherwise seed PPR; live `N` is `D`'s only graph neighbor, reachable no other way. Default: `N` absent (D stripped before `seeds = initial_fused[...]`, so PPR never runs). Contrast with `include_deprecated=True`: `D` seeds, PPR expands to `N`, `N` surfaces — proving the fixture is genuinely seed-reachable-only-through-`D`, not vacuous. Confirmed non-vacuous via a throwaway reorder of the filter to run after seed derivation (reverted): only this new test failed, all 54 prior tests stayed green.

## Phase 3: Resolution Filters (PR 3)

- [ ] 3.1 RED: `tests/unit/resolution/test_contradiction.py` — superseded concept never in a candidate pair by default; flag restores; all-live identical
- [ ] 3.2 RED: `tests/unit/resolution/test_candidates.py` — deprecated excluded from HIGH/LOW groups by default; flag restores; all-live identical
- [ ] 3.3 GREEN: add `include_deprecated: bool = False` to `find_contradictions`; filter `_candidate_pairs(store)` output against `lifecycle.deprecated_concept_ids(bundle_dir)`
- [ ] 3.4 GREEN: add `include_deprecated: bool = False` to `find_candidates`; filter `_iter_eligible(bundle_dir)` output before HIGH/LOW pairing
- [ ] 3.5 REFACTOR: short-circuit the walk on `include_deprecated=True` in both functions
- [ ] 3.6 Verify: `pytest tests/unit/resolution/test_contradiction.py tests/unit/resolution/test_candidates.py -v`

## Phase 4: CLI Wiring (PR 4)

- [ ] 4.1 RED: `tests/unit/cli/test_query.py`, `test_contradictions.py`, `test_adjudicate.py` — `--include-deprecated` threads through and restores results; default excludes
- [ ] 4.2 GREEN: add `--include-deprecated` Typer option to `query`/`contradictions`/`adjudicate` in `cli/main.py`; pass through to `answer`/`find_contradictions`/`find_candidates`
- [ ] 4.3 Verify: `duplicates` still calls `find_candidates(layout.bundle_dir)` with no flag (default `False`, behavior now excludes deprecated); run `pytest tests/unit/cli/test_duplicates.py -v` to confirm/accept this side effect
- [ ] 4.4 Verify: `pytest tests/unit/cli/ -v` full CLI suite green
