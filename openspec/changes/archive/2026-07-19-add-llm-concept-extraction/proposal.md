# Proposal: LLM Concept/Entity Extraction (MVP-2 vertical slice)

## Intent
`openkos ingest` is a deterministic null compiler: it only ever emits one `Source` per invocation, never an LLM-derived object. MVP-2's centerpiece (roadmap.md:63) is LLM-assisted extraction + type classification. Start it SAFELY as the smallest reviewable vertical slice: at most one derived object, a 2-value vocabulary, fully gated by the existing human review, fail-closed on any model failure. This proves both extraction AND classification without inheriting the ontology, entity-resolution, or offline-dependency risks the design spike flagged.

## Scope
### In Scope
- LLM-driven extraction of **at most one** derived `Concept`-or-`Entity` per ingest, produced IN ADDITION to the existing `Source`.
- Type classification over the closed vocabulary `{Concept, Entity}`, exercising the "prefer specific over Entity fallback" rule (knowledge-object-model.md:176).
- Net-new structured-output prompt (2-message `chat()`, mirrors answer.py) requesting a documented JSON shape, plus **fail-closed** parsing/validation (JSON parse, object shape, `type` in allowed-set, non-empty title/description, derivable slug).
- Graceful degrade to today's Source-only behavior with a stderr note on ANY LLM failure (unavailable/timeout/error) or invalid/garbage output — ingest never crashes or blocks.
- Review-gate integration: extraction always RUNS; `--auto` skips only the confirm prompt, not extraction. Both objects shown in ONE preview.
- Provenance + sensitivity inheritance: derived object records `provenance:["sources/<slug>"]` and inherits the Source's sensitivity (trivial single-source high-water-mark).
- Idempotent re-ingest: an existing derived Concept/Entity is left UNTOUCHED.
- Section-aware generalization of `bundle/index.py` inserter (`section` param).
- Full test surface with a fake `LLMBackend` (reuse `_FakeLLM`, test_answer.py:41-50).

### Out of Scope (Non-goals)
- Multiple derived objects per ingest.
- The other 9 canonical types.
- Entity resolution / merge / reclassification on re-ingest.
- Typed relationship graph.
- Sensitivity high-water-mark across MULTIPLE sources.
- MVP-2 hybrid retrieval.

## Approach
Slot extraction into `ingest()` Phase A prepare block (main.py:295-353), after `build_source_concept`, before preview. New extraction function (fake-injectable seam) + new `okf.build_concept()` builder with its own validation gate. On success, extend the SAME index/log diff so one confirm gate reviews both files; Phase B gains one create-only write (content before catalog). Any `OllamaError` family / validation failure sets `derived=None` and degrades.

## Product rationale (tied to spike design risks)
- **#1 rigid ontology** → minimal emergent 2-value vocab, enforced by validation not by trusting the prompt; drift fails closed to zero extraction.
- **#2 entity resolution** → one rich object per ingest by construction; merge/dedup deferred.
- **#3 extraction fidelity** → mandatory provenance + same human review gate + fail-closed parsing.

## Risks
| Risk | Likelihood | Mitigation |
|------|-----------|--------------|
| No structured-output/JSON-parsing precedent in codebase (net-new surface) | High | Design parser from scratch, fail-closed at every step, exhaustive fake-LLM tests |
| Local-model output nondeterministic / malformed | High | Fail-closed validation + degrade-to-Source-only + fake-LLM test matrix |
| First change where ingest depends on the LLM — offline/unavailable behavior | Med | Catch `OllamaError` family locally, degrade with stderr note, exit 0 |
| Re-ingest silently overwrites user-edited Concept | Med | Idempotent: leave existing derived object untouched |

## Rollback
Additive slice: revert the ingest extraction call, `okf.build_concept`, and the `section` param. Existing Source-only path is untouched and is the degrade target, so partial rollback is inherently safe.

## Success Criteria
- [ ] Valid extraction writes Source + one Concept/Entity with correct provenance/sensitivity; index+log show both.
- [ ] Invalid type, malformed JSON, `extract:false`, and LLM-unavailable all degrade to Source-only, exit 0.
- [ ] `--auto` still runs extraction (no confirm prompt).
- [ ] Re-ingest leaves an existing derived object untouched.
