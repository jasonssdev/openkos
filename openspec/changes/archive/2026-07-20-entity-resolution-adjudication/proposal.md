# Proposal: Entity-Resolution Adjudication (Slice 2)

## Intent

Slice 1 shipped high-recall, deterministic candidate generation (`find_candidates` → `CandidateGroup`), which intentionally over-produces LOW-tier pairs (documented short-token false positives like "cats vs carts"). This slice adds the **precision layer**: a read-only LLM pass that adjudicates each candidate group using real content signal (title + body), producing a reviewable verdict + confidence + rationale. It answers slice 1's own documented false-positive gap. It stays local-first (local Ollama), proposes rather than decides (engine produces a REVIEW QUEUE), and honors OpenKOS principle 3 (reviewable decisions). It is slice 2 of a 2–3 change mini-chain; slice 3 (reversible destructive merge) will get its own explicit human checkpoint.

## Scope

### In Scope
- New `src/openkos/resolution/adjudication.py`: `Verdict` enum (SAME/DIFFERENT/UNCERTAIN), frozen ephemeral `AdjudicatedCandidate(candidate, verdict, confidence: float, rationale: str)`, and a config-free leaf function taking an injected `LLMBackend`.
- **Per-group** LLM adjudication (one call per `CandidateGroup`; one failing group must not sink the run), reusing `extraction/concept.py`'s pattern: 2-message prompt, fail-closed JSON extraction, per-item validate-returns-None, `OllamaError`-family propagates out of the leaf.
- Read-only consumption of member title + **full body** via `okf.load_frontmatter`, mirroring `retrieval/answer.py:_assemble_context`; a member unreadable at adjudication time degrades to skip.
- New read-only CLI verb `adjudicate` (config → `OllamaClient(model=cfg.model)` → inject), mirroring `query`'s 3-tier `OllamaError` degrade catch. Writes nothing, no confirm gate. Optional display-only `--same-only` filter (library never drops data).
- Deterministic unit tests via the existing `_FakeLLM` stub; real `OllamaClient` path stays thin.

### Out of Scope (Non-Goals)
- Destructive `merge`/`resolve`, tombstones, merge records, sensitivity recompute, un-merge — **slice 3**.
- Embeddings / vector candidate generation.
- Any change to slice-1 candidate generation, thresholds, or `find_candidates`.
- Any bundle/state write or persisted OKF type for the adjudication result.
- Batching (deferred) and content truncation/summarization (deferred tightening).

## Capabilities

### New Capabilities
- `entity-resolution-adjudication`: read-only LLM adjudication of slice-1 candidate groups into labeled verdict + confidence + rationale, plus the `adjudicate` CLI report verb.

### Modified Capabilities
- None. `entity-resolution` requirements are unchanged; adjudication consumes its output. (Its slice-2 non-goal note is now fulfilled — narrative only, no requirement change.)

## Approach

Approach A (per-group calls) mirrors `extract_concept`'s one-call-per-unit shape and per-unit degrade discipline. Each group's members are loaded read-only (title + full body); the prompt asks "are these the same real-world entity?" and returns a single JSON verdict object, fail-closed parsed and validated. All three verdicts are kept — UNCERTAIN is the honest degrade, DIFFERENT is never auto-dropped so a human can audit LLM-vs-mechanical disagreement. The leaf propagates `OllamaError`; the CLI catches `OllamaUnavailable` → `OllamaModelNotFound` → generic `OllamaError` with actionable messages.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/resolution/adjudication.py` | New | `Verdict`, `AdjudicatedCandidate`, adjudication leaf function |
| `src/openkos/cli/main.py` | Modified | New `adjudicate` verb + 3-tier degrade catch |
| `tests/unit/resolution/` | New/Modified | Fake-LLM adjudication tests; extend layering guard for `openkos.llm` import |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Prompt reliability on small local models | Med | Closed rubric + confidence + fail-closed validation, mirroring `concept.py` |
| Latency: N calls on large LOW-tier lists | Med | Per-group degrade; batching is a documented follow-up, not v1 |
| Full-body context-window pressure | Low | `DEFAULT_TIMEOUT=120s` tolerates slow calls; truncation deferred as a tightening |
| JSON reply shape/validation not yet pinned | Med | Pin exact `{verdict, confidence, rationale}` shape + rules in sdd-spec/design |

## Rollback Plan

Additive and read-only. Revert by removing `adjudication.py`, the `adjudicate` verb, and its tests. No bundle/state data is written, so there is nothing to migrate back; slice-1 `duplicates` and all existing verbs are untouched.

## Dependencies

- Slice 1 `resolution/` package (shipped, `ca68124`).
- Local Ollama + configured `cfg.model` (degrades with actionable message when absent).

## Success Criteria

- [ ] `adjudicate` renders each candidate group with a SAME/DIFFERENT/UNCERTAIN verdict, confidence, and rationale; writes nothing; no confirm gate.
- [ ] LOW-tier false positives (e.g. "cats vs carts") are labeled DIFFERENT/UNCERTAIN with a rationale.
- [ ] One failing group degrades that group only; the run continues.
- [ ] No model available → clear actionable stderr message, exit 1, zero writes.
- [ ] All verdict-mapping/parse/validate logic covered offline via `_FakeLLM`; real `OllamaClient` path stays thin.
- [ ] Layering guard still holds; `resolution → openkos.llm` import asserted allowed.
