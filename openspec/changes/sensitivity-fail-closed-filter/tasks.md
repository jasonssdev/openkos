# Tasks: Sensitivity Fail-Closed Filter (Gap #8 · S3)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | S3a ~330 / S3b ~300 / S3c ~250 (total ~880 across chain) |
| 400-line budget risk | Medium (per-slice under 400; S3a+S3b co-merge ~630 stays under 800 review budget) |
| Chained PRs recommended | Yes |
| Suggested split | PR1 (S3a, root) -> PR2 (S3b, on PR1) -> PR3 (S3c, on PR2) |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: Medium

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 (S3a) | `sensitivity.py` predicate + 4 S1-pattern seams (query/contradictions/adjudicate/suggest-relations) + `--include-confidential` | PR1 (base: feature/tracker) | `uv run pytest tests/unit/test_sensitivity.py tests/unit/retrieval/test_answer.py tests/unit/resolution/test_contradiction.py tests/unit/resolution/test_adjudication.py tests/unit/resolution/test_edge_typing.py -q` | `uv run openkos query "..." --bundle <fixture-bundle>` against a fixture bundle with a confidential concept | Revert `src/openkos/sensitivity.py` + the 4 seam diffs; sensitivity-blind behavior restored, `sensitivity` frontmatter still written unchanged |
| 2 (S3b) | `LintDoc.sensitivity` + suggest-volatility seam + extract floor gate + `_assemble_context` defense-in-depth | PR2 (base: PR1 branch) | `uv run pytest tests/unit/resolution/test_volatility_typing.py tests/unit/extraction/test_concept.py tests/unit/retrieval/test_answer.py -q` | `uv run openkos suggest-volatility --bundle <fixture-bundle>` and `uv run openkos ingest <fixture-source>` with `default_sensitivity: confidential` | Revert `lint.py`/`volatility_typing.py`/`extraction/concept.py`/`cli/main.py` diffs; S3a predicate/seams unaffected |
| 3 (S3c) | `llm/parsing.py` public JSON helpers, migrate 5 call sites, `config.py` TypeError fix | PR3 (base: PR2 branch) | `uv run pytest tests/unit/llm/test_parsing.py tests/unit/resolution/test_adjudication.py tests/unit/resolution/test_edge_typing.py tests/unit/resolution/test_volatility_typing.py tests/unit/resolution/test_contradiction.py tests/unit/extraction/test_concept.py tests/unit/test_config.py -q` | N/A — pure internal refactor + exception-type bugfix, no observable CLI behavior change | Revert `llm/parsing.py` + 5 call-site diffs + `config.py` diff independently; sensitivity filtering (PR1/PR2) untouched |

---

## Phase 1: S3a — Predicate Spine (PR1, base: feature/tracker)

- [ ] 1.1 RED: create `tests/unit/test_sensitivity.py` mirroring `tests/unit/test_lifecycle.py::_write_doc` fixture helper; write failing tests for `sensitive_concept_ids(bundle_dir, threshold="confidential")`: explicit `confidential` -> blocked; missing `sensitivity` key -> blocked; blank/whitespace-only `sensitivity` -> blocked; malformed/unparseable frontmatter -> blocked; unreadable file (read error) -> blocked; unknown value (e.g. `top-secret`) -> blocked; `private` -> sent (not in returned set); `public` -> sent (not in returned set)
- [ ] 1.2 GREEN: create `src/openkos/sensitivity.py` with `sensitive_concept_ids(bundle_dir, *, threshold="confidential")` per design — explicit `read_error`/`parse_error` -> block, explicit absent/blank `raw` -> block, else delegate to `okf._rank(raw) >= floor` (do NOT delegate absent/blank to `_rank`, since `_rank(None)`/`_rank("")` return private, not confidential — spec: Fail-Closed Sensitivity Resolution)
- [ ] 1.3 REFACTOR: align module docstring/style with `lifecycle.py` (fail-CLOSED framing vs lifecycle's fail-SAFE framing); confirm `uv run pytest tests/unit/test_sensitivity.py -q` green

- [ ] 1.4 RED: extend `tests/unit/retrieval/test_answer.py` with a spy-`LLMBackend` case — confidential-fixture hit excluded from fused hits before `llm.chat`; private/public hits still sent (spec: Confidential excluded from query/answer)
- [ ] 1.5 GREEN: wire `retrieval/answer.py` hit seam (:368-381) to call `sensitivity.sensitive_concept_ids` and pass through `lifecycle.filter_hits` beside the existing `deprecated` filter (fts/vec/graph)

- [ ] 1.6 RED: extend `tests/unit/resolution/test_contradiction.py` — confidential concept dropped from `_candidate_pairs` before `llm.chat`; private/public pair still sent (spec: Confidential excluded from adjudicate/contradictions/suggest-relations)
- [ ] 1.7 GREEN: wire `resolution/contradiction.py` (:408-413) to compute the predicate once per run and drop pairs touching a blocked id before the `llm.chat` call

- [ ] 1.8 RED: extend `tests/unit/resolution/test_adjudication.py` — blocked `member_ids` excluded before `_load_members`/candidate assembly; private/public members still included
- [ ] 1.9 GREEN: wire `resolution/adjudication.py::run` (:236) to drop blocked `member_ids` before `_load_members` (:91-112)/:251

- [ ] 1.10 RED: extend `tests/unit/resolution/test_edge_typing.py` — edges with a blocked endpoint excluded from `suggest_relations` before `llm.chat`; private/public edges still sent
- [ ] 1.11 GREEN: wire `resolution/edge_typing.py::suggest_relations` (:267) to drop edges whose endpoint is blocked before `suggest_edge_types` (:250)/`_load_doc` (:131-147)

- [ ] 1.12 RED: add `--include-confidential` cases to `tests/unit/cli/test_query.py`, `test_contradictions.py`, `test_adjudicate.py`, `test_suggest_relations.py` — flag present -> confidential concept participates exactly as private/public would; flag absent -> excluded (spec: `--include-confidential` Escape Flag)
- [ ] 1.13 GREEN: add `--include-confidential` flag to `query`/`contradictions`/`adjudicate`/`suggest-relations` CLI commands in `cli/main.py`, mirroring `--include-deprecated` (skip the predicate walk entirely when `True`)
- [ ] 1.14 REFACTOR: confirm `uv run pytest tests/unit/test_sensitivity.py tests/unit/retrieval/test_answer.py tests/unit/resolution/test_contradiction.py tests/unit/resolution/test_adjudication.py tests/unit/resolution/test_edge_typing.py tests/unit/cli/test_query.py tests/unit/cli/test_contradictions.py tests/unit/cli/test_adjudicate.py tests/unit/cli/test_suggest_relations.py -q` all green; verify no cross-import of `_`-prefixed symbols introduced

## Phase 2: S3b — Divergent Seams (PR2, base: PR1 branch)

- [ ] 2.1 RED: extend `tests/unit/test_lint.py` / `tests/unit/resolution/test_volatility_typing.py` — `LintDoc.sensitivity` field populated from frontmatter; blocked docs excluded from `_sample_bodies_by_type`; all-blocked type yields no suggestion; private/public docs still sampled (spec: Confidential excluded from suggest-volatility)
- [ ] 2.2 GREEN: thread `sensitivity: str = str(metadata.get("sensitivity", ""))` into `LintDoc` (`lint.py`:113-123); filter blocked docs from `collect_docs` output in `resolution/volatility_typing.py` before `_sample_bodies_by_type` (:207)
- [ ] 2.3 RED: add `--include-confidential` case to `tests/unit/cli/test_suggest_volatility.py` — flag restores blocked docs to sampling
- [ ] 2.4 GREEN: add `--include-confidential` flag to `suggest-volatility` CLI command in `cli/main.py`, mirroring `--include-deprecated`

- [ ] 2.5 RED: extend `tests/unit/extraction/test_concept.py` — `default_sensitivity: confidential` short-circuits before `extract_concept` is called, returns `[]`, no `llm.chat` invocation, emits existing "keeping the Source only" degrade message; `default_sensitivity: private` calls `llm.chat` unchanged (spec: Extract Gates on the Workspace Sensitivity Floor)
- [ ] 2.6 GREEN: add floor gate in `cli/main.py::_stage_derived_objects` (after blank check ~L316, before `extract_concept` call ~L324): `if okf._rank(sensitivity) >= okf._rank("confidential")` -> emit degrade, return `[]`, skip `extract_concept`
- [ ] 2.7 RED: add `--include-confidential` case to `tests/unit/cli/test_ingest.py` — flag bypasses the floor gate, `extract_concept`/`llm.chat` called even at confidential floor
- [ ] 2.8 GREEN: add `--include-confidential` flag to `ingest` CLI command in `cli/main.py`, bypassing the floor gate in `_stage_derived_objects`

- [ ] 2.9 RED: extend `tests/unit/retrieval/test_answer.py` — `_assemble_context` (:161-183) skips a blocked cid on guarded re-read even if it slipped past the hit-seam filter (defense-in-depth, spec: Exclusion, Not Redaction)
- [ ] 2.10 GREEN: wire `_assemble_context` guarded re-read to skip any cid in `sensitive_concept_ids` before assembling the `llm.chat` context
- [ ] 2.11 REFACTOR: confirm `uv run pytest tests/unit/test_lint.py tests/unit/resolution/test_volatility_typing.py tests/unit/cli/test_suggest_volatility.py tests/unit/extraction/test_concept.py tests/unit/cli/test_ingest.py tests/unit/retrieval/test_answer.py -q` all green

## Phase 3: S3c — Hygiene (PR3, base: PR2 branch)

- [ ] 3.1 RED: create `tests/unit/llm/test_parsing.py` — `extract_json_object`/`extract_json_items` cases: plain object/list, fenced-code recovery, brace/bracket recovery, non-str input fails closed (returns `None`/`[]`, no exception)
- [ ] 3.2 GREEN: create `src/openkos/llm/parsing.py` exposing public `extract_json_object` + `extract_json_items`, consolidating the 5 existing local clones
- [ ] 3.3 REFACTOR: migrate `resolution/adjudication.py:131-163` to import and call `llm.parsing.extract_json_object`, delete local clone
- [ ] 3.4 REFACTOR: migrate `resolution/edge_typing.py:169-201` to import and call `llm.parsing.extract_json_object`, delete local clone
- [ ] 3.5 REFACTOR: migrate `resolution/volatility_typing.py:132-164` to import and call `llm.parsing.extract_json_object`, delete local clone
- [ ] 3.6 REFACTOR: migrate `resolution/contradiction.py:250-290` to import and call `llm.parsing.extract_json_object`, delete local clone
- [ ] 3.7 REFACTOR: migrate `extraction/concept.py:166-220` (list variant) to import and call `llm.parsing.extract_json_items`, delete local clone
- [ ] 3.8 RED: extend `tests/unit/test_config.py` — YAML mapping with an unhashable complex key raises `ValueError`, not uncaught `TypeError`
- [ ] 3.9 GREEN: `config.py`:375 change `except yaml.YAMLError` to `except (yaml.YAMLError, TypeError)`
- [ ] 3.10 Verify: confirm `uv run pytest tests/unit/llm/test_parsing.py tests/unit/resolution/test_adjudication.py tests/unit/resolution/test_edge_typing.py tests/unit/resolution/test_volatility_typing.py tests/unit/resolution/test_contradiction.py tests/unit/extraction/test_concept.py tests/unit/test_config.py -q` all green; full suite `uv run pytest -q` green; confirm `ollama.py` json guards left untouched (transport-level, unrelated)
