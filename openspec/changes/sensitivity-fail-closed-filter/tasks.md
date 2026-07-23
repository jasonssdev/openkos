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

- [x] 1.1 RED: create `tests/unit/test_sensitivity.py` mirroring `tests/unit/test_lifecycle.py::_write_doc` fixture helper; write failing tests for `sensitive_concept_ids(bundle_dir, threshold="confidential")`: explicit `confidential` -> blocked; missing `sensitivity` key -> blocked; blank/whitespace-only `sensitivity` -> blocked; malformed/unparseable frontmatter -> blocked; unreadable file (read error) -> blocked; unknown value (e.g. `top-secret`) -> blocked; `private` -> sent (not in returned set); `public` -> sent (not in returned set)
- [x] 1.2 GREEN: create `src/openkos/sensitivity.py` with `sensitive_concept_ids(bundle_dir, *, threshold="confidential")` per design — explicit `read_error`/`parse_error` -> block, explicit absent/blank `raw` -> block, else delegate to `okf._rank(raw) >= floor` (do NOT delegate absent/blank to `_rank`, since `_rank(None)`/`_rank("")` return private, not confidential — spec: Fail-Closed Sensitivity Resolution)
- [x] 1.3 REFACTOR: align module docstring/style with `lifecycle.py` (fail-CLOSED framing vs lifecycle's fail-SAFE framing); confirm `uv run pytest tests/unit/test_sensitivity.py -q` green

- [x] 1.4 RED: extend `tests/unit/retrieval/test_answer.py` with a spy-`LLMBackend` case — confidential-fixture hit excluded from fused hits before `llm.chat`; private/public hits still sent (spec: Confidential excluded from query/answer)
- [x] 1.5 GREEN: wire `retrieval/answer.py` hit seam (:368-381) to call `sensitivity.sensitive_concept_ids` and pass through `lifecycle.filter_hits` beside the existing `deprecated` filter (fts/vec/graph)

- [x] 1.6 RED: extend `tests/unit/resolution/test_contradiction.py` — confidential concept dropped from `_candidate_pairs` before `llm.chat`; private/public pair still sent (spec: Confidential excluded from adjudicate/contradictions/suggest-relations)
- [x] 1.7 GREEN: wire `resolution/contradiction.py` (:408-413) to compute the predicate once per run and drop pairs touching a blocked id before the `llm.chat` call

- [x] 1.8 RED: extend `tests/unit/resolution/test_adjudication.py` — blocked `member_ids` excluded before `_load_members`/candidate assembly; private/public members still included
- [x] 1.9 GREEN: wire `resolution/adjudication.py::run` (:236) to drop blocked `member_ids` before `_load_members` (:91-112)/:251

- [x] 1.10 RED: extend `tests/unit/resolution/test_edge_typing.py` — edges with a blocked endpoint excluded from `suggest_relations` before `llm.chat`; private/public edges still sent
- [x] 1.11 GREEN: wire `resolution/edge_typing.py::suggest_relations` (:267) to drop edges whose endpoint is blocked before `suggest_edge_types` (:250)/`_load_doc` (:131-147)

- [x] 1.12 RED: add `--include-confidential` cases to `tests/unit/cli/test_query.py`, `test_contradictions.py`, `test_adjudicate.py`, `test_suggest_relations.py` — flag present -> confidential concept participates exactly as private/public would; flag absent -> excluded (spec: `--include-confidential` Escape Flag)
- [x] 1.13 GREEN: add `--include-confidential` flag to `query`/`contradictions`/`adjudicate`/`suggest-relations` CLI commands in `cli/main.py`, mirroring `--include-deprecated` (skip the predicate walk entirely when `True`)
- [x] 1.14 REFACTOR: confirm `uv run pytest tests/unit/test_sensitivity.py tests/unit/retrieval/test_answer.py tests/unit/resolution/test_contradiction.py tests/unit/resolution/test_adjudication.py tests/unit/resolution/test_edge_typing.py tests/unit/cli/test_query.py tests/unit/cli/test_contradictions.py tests/unit/cli/test_adjudicate.py tests/unit/cli/test_suggest_relations.py -q` all green; verify no cross-import of `_`-prefixed symbols introduced

## Phase 2: S3b — Divergent Seams (PR2, base: PR1 branch)

- [x] 2.1 RED: extend `tests/unit/test_lint.py` / `tests/unit/resolution/test_volatility_typing.py` — `LintDoc.sensitivity` field populated from frontmatter; blocked docs excluded from `_sample_bodies_by_type`; all-blocked type yields no suggestion; private/public docs still sampled (spec: Confidential excluded from suggest-volatility)
- [x] 2.2 GREEN: thread `sensitivity: str = str(metadata.get("sensitivity", ""))` into `LintDoc` (`lint.py`:113-123); filter blocked docs from `collect_docs` output in `resolution/volatility_typing.py` before `_sample_bodies_by_type` (:207)
- [x] 2.3 RED: add `--include-confidential` case to `tests/unit/cli/test_suggest_volatility.py` — flag restores blocked docs to sampling
- [x] 2.4 GREEN: add `--include-confidential` flag to `suggest-volatility` CLI command in `cli/main.py`, mirroring `--include-deprecated`

- [x] 2.5 RED: extend `tests/unit/cli/test_ingest.py` (deviation from tasks.md's `tests/unit/extraction/test_concept.py`: `_stage_derived_objects` is private to `cli/main.py`, not `extraction/concept.py`, and is only reachable end-to-end through the `ingest` CLI, so the RED test lives where the gate actually runs) — `default_sensitivity: confidential` short-circuits before `extract_concept`/`llm.chat` is called, returns `[]`, emits existing "keeping the Source only" degrade message; `default_sensitivity: private` calls `llm.chat` unchanged (spec: Extract Gates on the Workspace Sensitivity Floor)
- [x] 2.6 GREEN: add floor gate in `cli/main.py::_stage_derived_objects` (after blank check ~L316, before `extract_concept` call ~L324): `if okf._rank(sensitivity) >= okf._rank("confidential")` -> emit degrade, return `[]`, skip `extract_concept`
- [x] 2.7 RED: add `--include-confidential` case to `tests/unit/cli/test_ingest.py` — flag bypasses the floor gate, `extract_concept`/`llm.chat` called even at confidential floor (NOTE: implemented alongside 2.6's GREEN in the same edit rather than strictly RED-first; the confirming test was added and verified passing immediately after — deviation noted per strict-tdd.md's failure-reporting rule)
- [x] 2.8 GREEN: add `--include-confidential` flag to `ingest` CLI command in `cli/main.py`, bypassing the floor gate in `_stage_derived_objects`

- [x] 2.9 RED: extend `tests/unit/retrieval/test_answer.py` — `_assemble_context` (:161-183) skips a blocked cid on guarded re-read even if it slipped past the hit-seam filter (defense-in-depth, spec: Exclusion, Not Redaction)
- [x] 2.10 GREEN: wire `_assemble_context` guarded re-read to skip any cid in `sensitive_concept_ids` before assembling the `llm.chat` context
- [x] 2.11 REFACTOR: confirm `uv run pytest tests/unit/test_lint.py tests/unit/resolution/test_volatility_typing.py tests/unit/cli/test_suggest_volatility.py tests/unit/cli/test_ingest.py tests/unit/retrieval/test_answer.py -q` all green (extraction/test_concept.py untouched — gate lives in cli/main.py, see 2.5 deviation note)

## Phase 3: S3c — Hygiene (PR3, base: PR2 branch)

- [x] 3.1 RED: create `tests/unit/llm/test_parsing.py` — `extract_json_object`/`extract_json_items` cases: plain object/list, fenced-code recovery, brace/bracket recovery, non-str input fails closed (returns `None`/`[]`, no exception)
- [x] 3.2 GREEN: create `src/openkos/llm/parsing.py` exposing public `extract_json_object` + `extract_json_items`, consolidating the 5 existing local clones
- [x] 3.3 REFACTOR: migrate `resolution/adjudication.py:131-163` to import and call `llm.parsing.extract_json_object`, delete local clone
- [x] 3.4 REFACTOR: migrate `resolution/edge_typing.py:169-201` to import and call `llm.parsing.extract_json_object`, delete local clone
- [x] 3.5 REFACTOR: migrate `resolution/volatility_typing.py:132-164` to import and call `llm.parsing.extract_json_object`, delete local clone
- [x] 3.6 REFACTOR: migrate `resolution/contradiction.py:250-290` to import and call `llm.parsing.extract_json_object`, delete local clone (also updated `tests/unit/resolution/test_contradiction.py`'s 3 direct calls to the removed private clone to call `openkos.llm.parsing.extract_json_object` instead, since the module-local symbol no longer exists)
- [x] 3.7 REFACTOR: migrate `extraction/concept.py:166-220` (list variant) to import and call `llm.parsing.extract_json_items`, delete local clone
- [x] 3.8 RED: extend `tests/unit/test_config.py` — YAML mapping with an unhashable complex key raises `ValueError`, not uncaught `TypeError` (deviation: verified empirically that with this project's pinned PyYAML 6.0.3 pure-Python `SafeLoader`, `BaseConstructor.construct_mapping`'s `isinstance(key, Hashable)` guard already wraps every unhashable-complex-key shape tried, e.g. `"? - a\n  - b\n: c\n"`, as `yaml.constructor.ConstructorError` -- a `YAMLError` subclass -- so it does NOT escape as a raw `TypeError` in this environment today; the RED test instead forces the scenario via `monkeypatch.setattr(yaml, "safe_load", ...)` so the defensive fix stays covered regardless of PyYAML version internals)
- [x] 3.9 GREEN: `config.py`:375 change `except yaml.YAMLError` to `except (yaml.YAMLError, TypeError)`
- [x] 3.10 Verify: confirm `uv run pytest tests/unit/llm/test_parsing.py tests/unit/resolution/test_adjudication.py tests/unit/resolution/test_edge_typing.py tests/unit/resolution/test_volatility_typing.py tests/unit/resolution/test_contradiction.py tests/unit/extraction/test_concept.py tests/unit/test_config.py -q` all green (306 passed); full suite `uv run pytest -q` green (1672 passed, +15 vs. 1657 baseline: +14 new `test_parsing.py` cases, +1 `test_config.py` TypeError case); `uv run mypy .`/`uv run ruff check .`/`uv run ruff format --check .` all clean; confirmed `ollama.py` json guards left untouched (transport-level, unrelated)

## Correction batch (post-4R-review)

Bounded correction applied to `feat/s3-filter` on top of Phase 1+2 (5f8610f + 6ec78fc), addressing 4 CONFIRMED 4R adversarial review findings. Baseline before this batch: 1649 tests passing. Scope: the 4 fixes below + their tests + `design.md`'s follow-ups note + `docs/cli.md`. Phase 3 (S3c) remains untouched, still `[ ]` above.

- [x] C.1 (R1 risk, CONFIRMED — fail-open) RED: `tests/unit/test_sensitivity.py` — `blocks_llm_send(None|""|"  ")` -> `True`; `blocks_llm_send("confidential")` -> `True`; `blocks_llm_send("top-secret")` -> `True` (unrecognized, fails closed via `okf._rank`); `blocks_llm_send("private"|"public")` -> `False`; custom `threshold=` respected. `tests/unit/cli/test_ingest.py::test_blank_default_sensitivity_still_trips_the_confidential_floor_gate` — `default_sensitivity: ""` MUST skip extraction (`llm.chat` never called), proven RED first (bare `okf._rank("")` resolves to `"private"`, gate untripped, extraction proceeded — fail-open confirmed)
- [x] C.2 GREEN: added `sensitivity.blocks_llm_send(value, *, threshold="confidential")` — the ONE shared fail-closed authority; refactored `sensitive_concept_ids` to delegate to it per-doc (behavior-identical, confirmed by the full existing `test_sensitivity.py` suite staying green); wired `cli/main.py::_stage_derived_objects`'s floor gate to call `blocks_llm_send(sensitivity)` instead of a bare `okf._rank(sensitivity) >= okf._rank("confidential")` comparison
- [x] C.3 REFACTOR: confirmed `uv run pytest tests/unit/test_sensitivity.py tests/unit/cli/test_ingest.py -q` green (92 passed); existing confidential/private/include-confidential extract tests (2.5-2.8) unaffected

- [x] C.4 (R4 resilience, CRITICAL — walk-bypass leak) RED: `tests/unit/retrieval/test_answer.py::test_assemble_context_independently_excludes_a_doc_the_walk_never_saw` — a confidential doc NOT in the precomputed `blocked` set (simulating an `okf._iter_docs` walk that silently missed it, e.g. an unlistable subtree) but still directly readable by path MUST still be excluded from `_assemble_context`'s output; `test_assemble_context_include_confidential_skips_the_independent_recheck` — the escape flag also skips the new re-check; `test_confidential_doc_invisible_to_the_walk_is_still_excluded_end_to_end` — monkeypatches `sensitivity.sensitive_concept_ids` to return an empty frozenset (walk-blind) and proves `answer()` still never calls `llm.chat` with the confidential doc's content. All three proven RED first
- [x] C.5 GREEN: `_assemble_context` gained an `include_confidential: bool = False` keyword-only param; after the existing guarded re-read + frontmatter parse, unless `include_confidential` is `True`, it independently re-checks THIS doc's own freshly re-read `sensitivity` value via the shared `sensitivity.blocks_llm_send` (walk-independent defense-in-depth at the actual send point, distinct from the `blocked`-set check which stays walk-dependent); `answer()` now passes its own `include_confidential` through to `_assemble_context`. This is QUERY-SPECIFIC per the review's scoping: every other `llm.chat` seam derives its candidates from the SAME walk-based predicate with no independent re-read-by-path step, so a walk-invisible doc is never a candidate there and needed no change (recorded as a follow-up in design.md, not fixed now)
- [x] C.6 REFACTOR: confirmed `uv run pytest tests/unit/retrieval/test_answer.py -q` green (64 passed, including all pre-existing S3a/S3b sensitivity tests); updated `answer()`'s and `_assemble_context`'s docstrings to document the walk-bypass rationale

- [x] C.7 (R2 readability, CONFIRMED footgun — dead field) Approval-style removal: confirmed via `grep .sensitivity` that `LintDoc.sensitivity` (populated in `lint.collect_docs`) had exactly ONE reader outside its own tests: none — `resolution/volatility_typing.py::suggest_volatility` filters via the shared `sensitivity.sensitive_concept_ids(bundle_dir)` walk predicate, never `doc.sensitivity`. Removed the `LintDoc.sensitivity` field, its population line in `collect_docs`, its two isolated tests (`test_collect_docs_reads_sensitivity_field`, `test_collect_docs_defaults_sensitivity_to_empty_string_when_absent`), and the now-unused `sensitivity=` parameter from `test_lint.py::_doc`'s factory
- [x] C.8 REFACTOR: confirmed `uv run pytest tests/unit/test_lint.py -q` green (110 passed) and `uv run mypy .` clean after the dataclass field removal (no stray `.sensitivity` references anywhere in `src/`)

- [x] C.9 (R3 reliability, CONFIRMED) `docs/cli.md`: added `--include-confidential` to `query`'s and `ingest`'s existing flag tables (both already documented `--include-deprecated`/`--auto`). VERIFIED first (not just trusted the review) that `contradictions`, `adjudicate`, `suggest-relations`, `suggest-volatility` had **zero** existing sections in `docs/cli.md` at all (a broader pre-existing MVP-2/3 documentation gap predating this feature, not something this feature alone introduced) — added four new minimal `(MVP 3)`-tagged sections for them, each with a short read-only description plus a flag table covering every flag they actually accept (`--same-only`/`--all`/`--include-deprecated`/`--include-confidential` as applicable), matching the existing house style (`### openkos <verb>` + short paragraph + flag table)
- [x] C.10 `design.md`: appended "Known follow-ups (harden before cloud/export slice)" note — (a) repeated full-bundle `_iter_docs` walk per invocation across `lifecycle`/`sensitivity`/`lint.collect_docs` (perf, share one pass); (b) the walk-bypass leak is now mitigated for `query`/`answer()` specifically by C.4/C.5, but a bundle-wide observability signal surfacing `okf._walk_errors` remains a follow-up before the cloud/export slice. Neither implemented — recorded only, per instruction
- [x] C.11 Whole-tree CI-equivalent verify: `uv run pytest -q` (1657 passed, +8 net vs. the 1649 baseline: +10 new tests from C.1/C.4, -2 removed dead-field tests from C.7), `uv run mypy .` (Success, no issues in 117 source files), `uv run ruff check .` (all checks passed), `uv run ruff format --check .` (117 files already formatted)

**Spec check**: none of C.1-C.10 change `specs/sensitivity-aware-llm/spec.md`'s requirements — every fix strengthens fail-closed behavior strictly toward what the spec already requires (Fail-Closed Sensitivity Resolution, Confidential excluded from every `llm.chat` seam, Exclusion Not Redaction); no spec amendment needed.
