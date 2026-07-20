# Tasks: Entity-Resolution Candidates (slice 1, read-only)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~550-650 (new package ~250, tests ~320, cli/docs ~80) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 (library core) → PR 2 (CLI verb + docs + integration) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending (ask maintainer) |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | `resolution` library: normalize, similarity, `find_candidates`, dataclasses | PR 1 | `uv run pytest tests/unit/resolution -q` | N/A — pure stdlib unit tests, no external process | revert `src/openkos/resolution/` + `tests/unit/resolution/`, no other file touched |
| 2 | `duplicates` CLI verb, docs, layering guard, real-bundle proof | PR 2 | `uv run pytest tests/unit/cli/test_duplicates.py tests/unit/resolution/test_layering.py -q` | `uv run openkos duplicates` against `examples/good-life-demo` | revert `cli/main.py` hunk + `docs/cli.md` hunk + new CLI/layering tests |

## Phase 1: Normalization (`normalize.py`)

- [x] 1.1 RED: `tests/unit/resolution/test_normalize.py` — table cases: case-fold, whitespace collapse, punctuation strip, diacritics (NFKD) removal.
- [x] 1.2 GREEN: `src/openkos/resolution/normalize.py` — `normalize_key(title)` per design steps (NFKD → drop combining → casefold → punctuation→space → collapse).
- [x] 1.3 REFACTOR: dedupe repeated char-class logic; confirm mypy strict, ruff clean.

## Phase 2: Similarity (`similarity.py`)

- [x] 2.1 RED: `tests/unit/resolution/test_similarity.py` — lock 0.75 boundary ("stoic"~"stoicism" ratio 0.769 passes), min token length 3, a clear negative (dissimilar tokens), token-subset containment.
- [x] 2.2 GREEN: `src/openkos/resolution/similarity.py` — tokenize, `difflib.SequenceMatcher` pairwise ratio, subset-containment check.
- [x] 2.3 REFACTOR: extract threshold/min-len as named constants.

## Phase 3: Core `find_candidates` (`candidates.py`)

- [x] 3.1 RED: `tests/unit/resolution/test_candidates.py::test_partitions_by_exact_type` — cross-type similar titles yield no candidate; Sources excluded.
- [x] 3.2 RED: `test_high_tier_exact_key` — N-member HIGH group via shared normalized key.
- [x] 3.3 RED: `test_low_tier_near_match` — LOW candidate for near-match, not already HIGH; HIGH∩LOW disjoint.
- [x] 3.4 RED: `test_no_self_pair_and_pair_once` + `test_stable_ordering` (tie-break by concept_id) + `test_determinism_repeated_runs`.
- [x] 3.5 RED: `test_degrade_not_crash` — malformed/unreadable doc skipped, valid pair still returned (mirrors `_iter_docs`/`DocScan`).
- [x] 3.6 RED: `test_empty_and_single_object_bundle` — no candidates, no raise.
- [x] 3.7 GREEN: `src/openkos/resolution/candidates.py` — `Tier` enum, frozen `Candidate`/`CandidateGroup`, `find_candidates(bundle_dir)` reusing `okf._iter_docs`.
- [x] 3.8 GREEN: `src/openkos/resolution/__init__.py` — re-export `find_candidates`, `CandidateGroup`, `Tier`.
- [x] 3.9 REFACTOR: confirm 90% branch coverage on `resolution/`, mypy strict.

## Phase 4: CLI verb + layering

- [x] 4.1 RED: `tests/unit/cli/test_duplicates.py` — no-workspace refuses via `config.require_workspace` (exit 1, stderr); with-candidates prints groups, exit 0, writes nothing; no-candidates prints clear empty report, exit 0.
- [x] 4.2 GREEN: register `duplicates` verb in `src/openkos/cli/main.py` (mirror `lint`/`status` shape; no `--auto`/confirm).
- [x] 4.3 RED: `tests/unit/resolution/test_layering.py` — `model`/`bundle`/`state` modules import no `resolution` symbol (static import-graph assertion).
- [x] 4.4 GREEN: confirm layering test passes as-is (no production change expected).
- [x] 4.5 Update `docs/cli.md` — document `duplicates` alongside `lint`/`status`.

## Phase 5: Integration proof

- [x] 5.1 `tests/unit/resolution/test_candidates.py::test_real_bundle_readonly` — run `find_candidates` over `examples/good-life-demo/bundle`; assert no exception, no bundle file bytes/mtime change.
