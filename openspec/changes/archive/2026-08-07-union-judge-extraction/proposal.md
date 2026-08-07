# Proposal: Union-of-Runs + Selector Judge Replaces the Blind Extraction Cap

## Intent

`_MAX_OBJECTS_PER_SOURCE = 6` (concept.py:422) truncates validated output by reply POSITION. #454 showed it binding after chunk merge, discarding genuine subjects while keeping facets ahead of them. That is a selection defect; a bigger number does not fix it. #456 measured a selector judge over a candidate union keeping mean 5.9-10.3 objects where ground truth holds up to 7 genuine subjects.

## Scope

### In Scope
- New orchestrating function in `extraction/`: `_extract_once` runs -> per-run twin-drop -> richer-body merge -> judge -> backstop cap LAST.
- New judge module: own prompt/parse/validate, mirroring `retrieval/answer.py`; reuses `llm/parsing.py`.
- `ExtractionReport` gains run/judge bookkeeping beside `chunks`; `main.py` gains a judge-failure degrade path.
- Config knob (`DEFAULT_*` + typed field + guarded fallback).
- Before/after measurement on `evals/extraction_cap/run_cap_eval.py` and the AMI harness.

### Out of Scope
- Growing `_SYSTEM_PROMPT` (A/B-proven regression). All new prompt surface is the judge's.
- Async/parallel extraction — "core stays synchronous" stands.
- Changing `extract_concept`'s single-run public contract (evals keep measuring the raw primitive).
- Retuning `_CHUNK_THRESHOLD`; judging edges or resolution.

## Decisions

| # | Decision | Rationale / trade-off |
|---|---|---|
| 1 | **Union only at or below `_CHUNK_THRESHOLD`.** Chunked sources get judge-only over the merged set (no second pass). | Chunking IS already the multiplicity mechanism (#455); above the threshold the problem is selection, not generation. Avoids squaring fan-out and a 2x-larger judge prompt. Trade-off: chunked sources get no second-run recall. Bound the judge prompt with a deterministic pre-judge candidate ceiling. |
| 2 | **Fail-closed degrade.** Failed/unparseable/empty judge reply (incl. `OllamaError` from the judge call only) -> return the merged union truncated by the backstop, flagged in the report. Never raise, never discard extraction work. | Matches main.py's OllamaError degrade and concept.py's per-item validation degradation. Ingest never breaks. Trade-off: silent-ish quality drop, so it MUST be reported to stderr. |
| 3 | **Backstop cap = 12, applied once, after judge selection.** | Must not bind on any measured genuine set (7 unchunked; ~9 on TS3005b) or it re-creates the defect. Guards the 41/61-object pathological replies. Trade-off: accepts judge permissiveness (2.0-4.1 junk) — human curation is the non-negotiable filter. |
| 4 | **Default ON, opt-out config flag.** | Ships the measured win; the flag is the one-line rollback. If the eval gate is neutral or negative, flip the default constant to `False` instead of reverting. |

## Capabilities

### New Capabilities
- `extraction-union-judge`: candidate union construction, judge selection contract, judge-failure semantics, backstop role.

### Modified Capabilities
- `ingestion`: "Bounded, Deduplicated Derived-Object Staging" still says a hard cap of 5 (spec.md:566-597) while code is 6 — the delta MUST reconcile the cap to a post-judge backstop, and add the judge-failure degrade path and its stderr notice.

## Cost (honest, synchronous)

| Source | Today | After |
|---|---|---|
| `<= 18,000` chars | 1 call | 3 calls: 2 sequential extractions + 1 judge (~2x extraction wall-clock + ~2s) |
| `> 18,000` chars (N chunks) | N calls | N + 1 calls (~+2s) |

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/extraction/concept.py` | Modified | Orchestrator, richer-body merge, cap becomes backstop, report fields |
| `src/openkos/extraction/` (new module) | New | Judge prompt/parse/validate |
| `src/openkos/cli/main.py` | Modified | Judge-failure degrade + notice |
| `src/openkos/config.py` | Modified | Opt-out knob |
| `tests/unit/extraction/test_concept.py` | Modified | `_SequencedLLM` already covers every new scenario |
| `evals/extraction_cap/`, `evals/decision_extraction/` | Modified | Before/after gate |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Judge prompt rules prime the model badly (5.5-5.6 precedent) | Med | Prefer deterministic post-filters; twin-drop stays per-run code, not prompt text |
| Procedure exemption (#413) lost at the judge | Med | Re-derive it deterministically, never copy the prompt clause |
| 2x latency on every small source | High | Opt-out flag; state cost in docs/stderr |
| Judge junk inflates written objects | Med | Backstop 12 + human curation |

## Rollback Plan

1. Flip the config default to `False` — union and judge are bypassed, `extract_concept`'s single-run path is byte-identical to today.
2. Full revert is additive-only: delete the judge module and orchestrator, restore the `main.py` call site. No data migration — derived objects are reconstructible and `raw/` is untouched.

## Dependencies

- `qwen3:8b` via Ollama for the eval gate; adjudicated ground truth in `examples/extraction-corpus/`.

## Success Criteria

- [ ] `run_cap_eval.py` before/after: genuine subjects retained increases, known facets retained does not increase.
- [ ] AMI harness: no regression in OKF types reached on chunked transcripts.
- [ ] Judge failure, garbage reply, and differing-run scenarios covered by unit tests; ingest exits 0 in each.
- [ ] Backstop 12 does not bind on any ground-truth fixture.
- [ ] `_SYSTEM_PROMPT` byte-identical.
