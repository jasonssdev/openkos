# Tasks: nfc-canonical-concept-ids (issue #430)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~460 (≈340 tests, ≈120 src) |
| 400-line budget risk | Low — src surface is ~120 lines; the rest is pinned tests |
| Chained PRs recommended | No |
| Delivery strategy | single-pr |

Decision needed before apply: No

## PR — one slice (targets: main)

Satisfies: `specs/concept-identity/spec.md` ADDED requirements "Concept Id
Derivation Is NFC-Normalized" and "Concept Path Reconstruction Tolerates A
Decomposed On-Disk Name"; `specs/graph-projection/spec.md` MODIFIED
requirement "Node Identity Is The OKF Concept ID".

### Phase 1 — RED

- [x] 1.1 `tests/unit/model/test_okf.py` — `concept_id_for` normalization
  suite: NFD filename yields NFC id; both spellings collapse to one id;
  decomposed directory segment normalized; already-NFC id byte-identical.
- [x] 1.2 `tests/unit/model/test_okf.py` — `concept_path_for` suite: direct
  hit; NFD name found from NFC id (with `Path.exists` forced False so the
  scan answers, not macOS's insensitive lookup); miss returns direct path;
  unreadable parent degrades; symlink never admitted through the fallback;
  ASCII id never pays a scan.
- [x] 1.3 `tests/unit/graph/test_sqlite_graph.py` — typed edge survives a
  decomposed filename spelled NFC in `relations:`.
- [x] 1.4 `tests/unit/cli/test_merge_core.py` — `_resolve_concept_path`
  resolves a decomposed filename from an NFC id.

### Phase 2 — GREEN

- [x] 2.1 `src/openkos/model/okf.py` — `concept_id_for` NFC-normalizes;
  docstring records the decision and its safety argument.
- [x] 2.2 `src/openkos/model/okf.py` — add `concept_path_for` with the two
  guards (D2).
- [x] 2.3 Route the nine reconstruction sites: `cli/main.py`
  `_resolve_concept_path`, `cli/curate.py` Structure stage,
  `resolution/contradiction.py` ×2, `resolution/adjudication.py`,
  `resolution/edge_typing.py`, `retrieval/answer.py`.

### Phase 3 — Quality gates

- [x] 3.1 `uv run pytest` full suite green (3896 passed).
- [x] 3.2 `uv run ruff check . && uv run ruff format --check .`
- [x] 3.3 `uv run mypy .`
- [x] 3.4 Coverage ≥ 90 (branch): 97.24%.

### Phase 4 — Follow-up

- [ ] 4.1 File the follow-up issue: `lint` detection of non-NFC on-disk
  names + rename migration decision (out of scope here, D3).
