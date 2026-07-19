# Proposal: `add-query-answer` — cited answer library (MVP-1 query chain, no CLI)

## Intent

MVP-1's `openkos query` must answer a natural-language question from the compiled
bundle **with citations** (`docs/cli.md:76`), but the retrieval+answer engine
does not exist — `state/fts.py` (#1) and `llm/` (#2) are stable, tested, and
archived yet have no consumer. This change lands the derived-layer glue that
wires them: retrieve lexical hits → assemble concept bodies into context → ask
the injected LLM → return the answer plus its citations. Same discipline as
#1/#2: one small pure library module, synchronous, Protocol seam, canonical layer
untouched. The CLI command is deliberately deferred to thin change #4.

## Scope

### In Scope

- New `src/openkos/retrieval/` (derived layer) — `answer.py` + `__init__.py`
  marker. **No CLI command, no config wiring.**
- Public surface (design finalizes exact signatures):
  `answer(question, *, bundle_dir: Path, llm: LLMBackend, limit: int = 5) -> AnswerResult`;
  `AnswerResult(answer: str, citations: list[Citation])`; `Citation(concept_id, title)`.
- **Retrieve**: `state.fts.build_index(bundle_dir).search(question, limit=limit)` →
  ranked `FtsHit`s; default `limit=5`.
- **Assemble**: per hit, re-read `(bundle_dir / f"{concept_id}.md")` via
  `model/okf.load_frontmatter`, guarded like `state/fts.py`'s D2 pattern (a
  concept that vanished/corrupted after index build is skipped, not fatal).
  Cite every concept_id actually placed in context (title from frontmatter).
- **Answer**: build `system`+`user` `Message`s, call `llm.chat(...)`, return result.
- **Zero FTS hits**: return a canned no-match `AnswerResult` (empty citations)
  **without calling the LLM** — cheapest, and avoids ungrounded hallucination.
- **Errors propagate typed** (`FtsUnavailable`, `OllamaError` family) — not swallowed.

### Non-goals (deferred)

| Deferred | Note |
|---|---|
| `openkos query` Typer command | Thin change #4 |
| Config wiring (`read_config` + `OllamaClient` build) | #4 injects `llm`; `retrieval/` never imports `openkos.config` |
| Context truncation / token budget | MVP-1 bounds context by `limit` concepts only; no per-concept/total cap |
| Vector / semantic retrieval | Lexical (FTS5) only for MVP-1 |
| Filing the answer back as a concept (two-output rule) | Later change; #3 only produces the answer |
| Richer citation metadata (score, provenance, sensitivity) | Minimal `(concept_id, title)` for now |

## Capabilities

### New Capabilities
- `query-answer`: a pure library that answers a natural-language question from the
  compiled bundle with lexical retrieval + injected `LLMBackend`, returning the
  answer plus `(concept_id, title)` citations, with a no-LLM zero-hit path and
  guarded concept re-reads.

### Modified Capabilities
- None. `fts-state` and `llm-client` are consumed read-only; `ingest`/`forget`
  are untouched.

## Approach

- **Compose existing seams.** `retrieval/answer.py` orchestrates
  `state.fts` (retrieve) → `okf` (re-read/frontmatter) → `llm.chat` (answer). It
  MAY depend on `state/fts.py`, `bundle/`, `model/okf.py`, and `llm/` types.
- **Config-free by injection.** `answer()` accepts an already-built
  `llm: LLMBackend` (structural Protocol) — it MUST NOT import `openkos.config`,
  mirroring `llm/ollama.py`'s own discipline. Application wiring belongs to #4.
- **Fully unit-testable, zero network.** A trivial fake `chat()` object + a
  `tmp_path` bundle (the `test_fts.py` precedent) covers every path. Strict TDD,
  docstring-per-function, 90% branch.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/retrieval/answer.py` | New | `answer()`, `AnswerResult`, `Citation` |
| `src/openkos/retrieval/__init__.py` | New | Package marker |
| `src/openkos/state/fts.py`, `llm/`, `model/okf.py` | Reused | Read-only consumers |
| `tests/unit/retrieval/test_answer.py` | New | Retrieve/assemble/answer/zero-hit/guarded-read/error coverage |
| `docs/architecture.md` | Possibly | Stale `retrieval/lexical`+`context` tree lines — one-line correction; **design decides** (low priority) |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Unbounded concept bodies bloat context | Med | Bounded by `limit` concepts; truncation is a named non-goal |
| Concept file vanished/corrupted after index build | Med | Guarded re-read skips + notes it, mirroring `state/fts.py` D2 |
| Ungrounded LLM answer on zero hits | Med | Canned no-match result, LLM not called |
| `FtsUnavailable`/`OllamaError` surface raw | Low | Typed propagation; #4 CLI presents them |
| Review size | Low | One module + tests, no CLI/persistence — likely single PR; forecast at `sdd-tasks` |

## Rollback Plan

Purely additive: `git revert` / delete `src/openkos/retrieval/` and its tests. No
persisted state, no migration, no CLI surface, no config-schema change — the
module is dormant until change #4's `query` command calls it.

## Dependencies / Sequencing

- **Upstream**: `add-fts-state` (#1) and `add-ollama-client` (#2), both archived.
- **Unblocks**: change #4 (`openkos query` CLI), which injects `OllamaClient` and
  presents `AnswerResult`.

## Success Criteria

- [ ] `answer(q, bundle_dir=..., llm=fake, limit=5)` retrieves via FTS, assembles
      matched concept bodies, calls `llm.chat`, returns `AnswerResult` with a
      `Citation` per concept placed in context.
- [ ] Zero FTS hits → canned `AnswerResult` (empty citations), LLM **not** called.
- [ ] A hit whose file vanished/corrupted is skipped, not fatal.
- [ ] `retrieval/` imports no `openkos.config`; `llm` is injected.
- [ ] `FtsUnavailable`/`OllamaError` propagate typed, unswallowed.
- [ ] No CLI command; `fts-state`/`llm-client`/`ingest`/`forget` unchanged.
- [ ] `uv run pytest` green at 90%+ branch (no live Ollama); ruff/mypy clean.
