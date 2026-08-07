# Exploration: union-of-2-runs + selector judge to replace the blind extraction cap (issue #456)

> Materialized from Engram `sdd/union-judge-extraction/explore` (observation #2512).

## Current State

`src/openkos/extraction/concept.py` is the whole extraction seam, config-free (never imports `openkos.config`; caller injects `LLMBackend`).

- `extract_concept(source_text, *, source_title, llm) -> ExtractionOutcome` (concept.py:603-688) is the ONE entry point. Pipeline: (a) below `_CHUNK_THRESHOLD` (18,000 chars, concept.py:459) one `_extract_once` call; above it, fan out one `_extract_once` call per `_chunk_lines` window (~4,000 chars, concept.py:485-514) and merge with `_dedup_merged` (concept.py:533-549, keyed on `(type, _normalize_title(title))`, keeps FIRST occurrence in chunk order); (b) `_drop_source_title_twins` (concept.py:356-419, #413's `_TWIN_EXEMPT_TYPE = "Procedure"` exemption at concept.py:344); (c) hard truncate to `_MAX_OBJECTS_PER_SOURCE = 6` (concept.py:422-456) keeping the first N — this is the blind cap issue #456 targets.
- `_extract_once` (concept.py:517-530) is the reusable unit: one `llm.chat(_build_messages(...))` call, `parsing.extract_json_items`, per-item `_validate`. This is exactly the primitive a second run would call again.
- `ExtractionOutcome` (concept.py:580-601) = `{objects, report}`; `ExtractionReport` (concept.py:552-577) = `{produced, retained, discarded_titles, chunks}` — `chunks` already exists as a fan-out counter from #455 and is the natural place a `judge_calls`/`ensemble_runs` counter would sit alongside it.
- `_SYSTEM_PROMPT` (concept.py:29-196) is long and load-bearing; the module docstring explicitly forbids growing it further — issue #456's evidence agrees ("A longer/restructured extractor prompt was A/B-tested and LOST on every metric").
- `_normalize_title` (concept.py:334-341) is the ONE title-normalization function shared by twin-drop and chunk-merge dedup — reusable as-is for union-merge dedup too.

Caller: `src/openkos/cli/main.py` — `_stage_derived_objects` (main.py:1845-2010+). Calls `extract_concept` exactly once (main.py:1965), inside a `Console(stderr=True).status(...)` block, wrapped in `try/except OllamaError` (main.py:1963-1972) that degrades to Source-only with `skip_reason="failed"`. This is the ONE seam where a second extraction call (or a judge call) would be invoked — currently single-shot, synchronous. `_extraction_cap_notice` (main.py:1980) already renders `report.produced > report.retained` to stderr; a union+judge report would extend this rendering, not replace its plumbing.

LLM seam: `src/openkos/llm/base.py` — `LLMBackend` Protocol is `chat(messages) -> str`, no batching/async. `src/openkos/llm/ollama.py`'s `OllamaClient.chat` (ollama.py:432-510) is a single synchronous `urllib.request` POST per call — no connection pooling or concurrency exposed. `openspec/config.yaml` states "core stays synchronous" as a non-negotiable (line 6). #455 added `temperature`/`seed` as constructor-level sampling pins (ollama.py:354-355, 448-451) — same client instance, same pinned sampling, so "two runs" == two sequential `llm.chat` calls, NOT literal parallelism. The issue's "negligible addition since extraction parallelizes" claim is NOT supported by the current synchronous core.

Judge-call precedent: `src/openkos/retrieval/answer.py` (`_SYSTEM_PROMPT` line 99, `_build_messages` line 335, `answer()` line 435, single `llm.chat` line 564) is the sibling module `extraction/concept.py`'s docstring already says it mirrors — system prompt + user turn, `llm.chat`, parse, fail-closed validate. A judge module follows the identical shape: its own `_JUDGE_SYSTEM_PROMPT`, `_build_judge_messages(source_text, candidates)` assembling the union as a closed candidate set, one `llm.chat` call, `parsing.extract_json_items`/`extract_json_object` for parsing, and its own `_validate_judge_reply`. `openkos.llm.parsing` (parsing.py:43-95) is the shared fail-closed JSON layer both reuse verbatim — no new parsing code needed.

Merge/dedup precedent: `_dedup_merged` (concept.py:533-549) already solves "same `(type, title)` from two calls" for chunk merges — but keeps the FIRST occurrence unconditionally, not the richer body. A union-of-2 merge needs a NEW comparison (prefer richer `body`/`description`); the function's *shape* is reusable, its tie-break rule is not.

Chunking interaction: chunking and the union are ORTHOGONAL fan-out axes today. A per-chunk union on a 40.8 KB source at ~10 chunks costs `10 × 2 = 20` extraction calls plus per-chunk judging; a source-level judge over the whole merged candidate set costs `chunks × 2 + 1`. The issue's "3 LLM calls per source" only holds for the unchunked path.

## Affected Areas

- `src/openkos/extraction/concept.py` — `extract_concept`, `_extract_once`, `ExtractionOutcome`/`ExtractionReport`, `_dedup_merged` (shape reusable, tie-break not), `_MAX_OBJECTS_PER_SOURCE` (becomes backstop), `_SYSTEM_PROMPT` (must NOT change — A/B-tested and lost)
- `src/openkos/cli/main.py` `_stage_derived_objects` (main.py:1845-2010) — sole caller; owns `OllamaError` catch/degrade UX and `_extraction_cap_notice`; needs a judge-failure degrade path alongside `blocked-by-sensitivity`/`failed`/`no-concepts-found`
- `src/openkos/llm/ollama.py` `OllamaClient.chat` — confirms synchronous single-call transport; no changes needed if the ensemble stays sequential
- `src/openkos/llm/parsing.py` — reusable verbatim for judge reply parsing
- `src/openkos/config.py` `Config`/`read_config` — `DEFAULT_*` constant + typed field + `is not None` fallback with a value guard is the knob pattern
- `tests/unit/extraction/test_concept.py` — `_FakeLLM` (line 30) and `_SequencedLLM` (line 1720, per-call differing replies, built for #454) already cover "2nd run differs", "judge fails", "judge returns garbage" — no new fixture infrastructure needed
- `evals/extraction_cap/run_cap_eval.py` — existing cap/decay harness against `examples/extraction-corpus/`, scores by title against hand-written ground truth; extend for before/after
- `evals/decision_extraction/` — the #454 chunking harness, source of the TS3005b 40.8 KB evidence

## Approaches

1. **Ensemble seam inside `extract_concept`** — it calls `_extract_once` twice, merges, judges internally.
   - Pros: caller unchanged except the new degrade path; one `ExtractionOutcome` stays the whole contract.
   - Cons: `extract_concept`'s contract balloons further; an eval harness measuring the RAW extractor has no opt-out.
   - Effort: Medium
2. **Wrapper the ingest caller invokes** — `extract_concept_ensemble(...)` calls `extract_concept` twice, then judges.
   - Pros: `extract_concept` untouched and independently measurable; additive and easy to gate.
   - Cons: each inner run is ALREADY capped before the judge sees anything, defeating the purpose — needs an uncapped variant anyway.
   - Effort: Medium-High
3. **Cap applied only once, after the judge** — a new orchestrating function composes `_extract_once` runs, per-run twin-drop, richer-body merge, judge, then the backstop cap LAST.
   - Pros: fixes approach 2's ordering defect; twin-drop stays per-run (validated rule); one source of truth for the backstop.
   - Cons: touches the internal composition of `extract_concept`'s pipeline.
   - Effort: Medium

## Recommendation

Approach 3. Keep `extract_concept` as-is for its single-run contract (used unchanged by `run_cap_eval.py`/`run_spike.py`). Add an orchestrator that runs `_extract_once` (same prompt/messages), twin-drops each run's own output, merges by `(type, normalize_title)` preferring the richer body (new tie-break), calls a NEW judge module (mirroring `retrieval/answer.py`'s prompt+parse+validate shape) with the full merged candidate list plus source text, then applies the backstop cap LAST. All new complexity lands in one new function/module, config-free, leaf-module convention.

## Risks

- **Synchronous-core vs. "parallel" claim**: real cost is sequential — the proposal must state wall-clock honestly rather than inherit the issue's parallel framing.
- **Chunking × union multiplication**: per-chunk squares fan-out; per-source risks judge context length on a ~9-10 chunk union (18-20 candidates plus source text). THE open design question.
- **Judge permissiveness (2.0-4.1 junk kept)**: keep-count prior vs. accepting junk affects the backstop cap's value.
- **Procedure exemption must be re-derived, not copy-pasted**: `_TWIN_EXEMPT_TYPE` (#413) is a deterministic RULE, not a prompt clause; concept.py records that a prompt-only version of a similar rule measurably made a defect WORSE via priming (5.5-5.6 probes).
- **Judge failure semantics undecided**: no code precedent for "second LLM call in the same pipeline fails or parses to garbage".
- **Test infrastructure is already adequate**: `_SequencedLLM` covers every new scenario with zero new fixture code, lowering apply-phase risk.

## Ready for Proposal

Yes. Open questions for the proposal: (1) per-chunk vs. per-source union scope, (2) judge failure fallback semantics, (3) the backstop cap's new value/role, (4) default-on vs. opt-in for the first release.
