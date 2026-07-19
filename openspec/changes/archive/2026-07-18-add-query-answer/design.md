# Design: `add-query-answer` — cited answer library (MVP-1 query chain, no CLI)

## Technical Approach

One new derived-layer module `src/openkos/retrieval/answer.py` (+ `retrieval/__init__.py`
marker), **no CLI, no config**. Public `answer()` composes three archived seams
end-to-end: `state.fts.build_index(bundle_dir).search(...)` (retrieve) →
per-hit guarded `okf.load_frontmatter` re-read (assemble) → `llm.chat(messages)`
(answer) → `AnswerResult(answer, citations)`. `llm: LLMBackend` is injected
(structural Protocol), so `retrieval/` never imports `openkos.config` — mirroring
`llm/ollama.py`'s own leaf discipline. Core is synchronous; typed exceptions
(`FtsUnavailable`, `OllamaError` family) propagate to the future CLI (#4). Fully
unit-testable with a fake `chat()` and a `tmp_path` bundle (the `test_fts.py`
precedent), zero network.

## Architecture Decisions

| # | Decision | Alternatives rejected | Rationale |
|---|---|---|---|
| **D1** | **`answer()` owns index lifecycle per-call**: `with fts.build_index(bundle_dir) as index: hits = index.search(question, limit)`, all work inside the `with`. | Accept a pre-built `FtsIndex` param; cache across calls. | `FtsIndex` is rebuild-per-run, in-memory, and a context manager by design (fts.py:81). For a library backing a **single-shot** `openkos query` (one question per process), per-call build keeps the public surface to one function and never leaks an open connection to the caller. Cost: O(bundle) rebuild per call — acceptable for MVP-1 bundle sizes; a batch/interactive caller wanting a shared index is YAGNI, deferred. |
| **D2** | **Guarded per-hit re-read** mirrors fts.py:173-182: for each hit `try: text=(bundle_dir/f"{cid}.md").read_text(); meta,body=okf.load_frontmatter(text)`; on `OSError/UnicodeDecodeError` **or** any parse exception → **skip that hit, continue**. Only successfully read concepts enter context and citations. | Fail the whole call on one bad file; pre-check existence. | A concept can vanish/corrupt between index build and re-read (concurrent `forget`); crashing on it is wrong. Same TOCTOU-safe stance fts.py already takes. |
| **D3** | **Zero-context short-circuit** returns a canned no-match `AnswerResult` **without calling the LLM** — triggered by BOTH zero FTS hits AND all-hits-skipped (D2 emptied the context). | Call LLM with empty context (hallucination risk); raise. | Cheapest path and avoids an ungrounded answer. Local-first honesty: no context → say so, don't guess. |
| **D4** | **Citations = every concept_id placed in context**, one `Citation(concept_id, title)` each, in hit-rank order. **Title** = frontmatter `title` (str); fallback to `concept_id` when missing/empty. | Surface `FtsHit.score`, `sensitivity`, or `provenance` paths. | `docs/cli.md:76` requires citing the concepts/sources used; concept_id **is** identity and Source docs are just `type: Source` concepts, so citing every in-context concept_id satisfies it. `title or concept_id` guarantees a non-empty human label. Richer metadata is a named non-goal. |
| **D5** | **Prompt = 2 messages** (`system` instructions + `user` context+question); local-first grounding rules baked into system text (see below). | Single user message; few-shot examples. | Matches the `Message` shape #2 built; system/user split is the ergonomic literal the CLI already expects. |

**ADR gate — verdict: zero ADRs.** Evaluated against BOTH config conditions.
(1) *Decides a pattern/interface/tradeoff?* Yes — the config-free injection seam and
single-module retrieve→answer layout. (2) *Hard-to-reverse?* **No** — this is a purely
additive, dormant module (`git revert` deletes `retrieval/`), no persisted state, no
schema/migration, no public CLI until #4. Both conditions must hold; (2) fails. Matches
the `add-fts-state` / `add-ollama-client` precedent (both zero ADRs). When in doubt, none.

## Prompt assembly (concrete)

**System** (stable):
```
You are OpenKOS, a local-first knowledge assistant. Answer the question using ONLY
the numbered CONTEXT concepts below — do not use outside knowledge. Cite the concepts
you rely on by their concept id. If the context does not contain enough information to
answer, say so plainly rather than guessing; an honest "the compiled bundle does not
cover this" is the correct answer when the context is insufficient.
```

**User** = context blocks + question. Each retrieved concept renders as a delimited,
labeled block so the model can attribute:
```
CONTEXT:

[concept_id: {concept_id} — {title}]
{body}

[concept_id: {concept_id} — {title}]
{body}

QUESTION:
{question}
```

**Zero/degraded-context no-match string (stable, D3):**
```
No matching concepts were found in the compiled bundle for this question.
```
Returned as `AnswerResult(answer=<that string>, citations=[])`, LLM not called.

## Data Flow / Sequence

```
caller ─ answer(question, bundle_dir=…, llm=…, limit=5)
  │
  ├─ with fts.build_index(bundle_dir) as index:        # FtsUnavailable → propagate
  │     hits = index.search(question, limit)            # never raises; [] on no match
  │     for hit in hits:                                # D2 guarded re-read
  │         try: text = (bundle_dir/f"{hit.concept_id}.md").read_text()
  │              meta, body = okf.load_frontmatter(text)
  │         except (OSError, UnicodeDecodeError, Exception): continue   # skip
  │         title = str(meta.get("title") or "") or hit.concept_id
  │         context.append(block); citations.append(Citation(cid, title))
  │
  ├─ if not context:  return AnswerResult(NO_MATCH, [])  # D3 — no LLM call
  │
  ├─ messages = [system, user(context, question)]
  ├─ reply = llm.chat(messages)                          # OllamaError family → propagate
  └─ return AnswerResult(reply, citations)
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/retrieval/__init__.py` | New | Package marker (derived layer) |
| `src/openkos/retrieval/answer.py` | New | `answer()`, `AnswerResult`, `Citation`, `NO_MATCH` const |
| `tests/unit/retrieval/test_answer.py` | New | Retrieve/assemble/answer/zero-hit/all-skipped/partial-skip/error coverage |
| `src/openkos/state/fts.py`, `llm/`, `model/okf.py` | Reused | Read-only consumers — no change |
| `docs/architecture.md` | **Defer** | See Doc drift below |

## Interfaces

```python
# src/openkos/retrieval/answer.py
@dataclass(frozen=True)
class Citation:
    concept_id: str
    title: str

@dataclass(frozen=True)
class AnswerResult:
    answer: str
    citations: list[Citation]

def answer(question: str, *, bundle_dir: Path,
           llm: LLMBackend, limit: int = 5) -> AnswerResult: ...
```

## Testing Strategy (strict TDD, ≥90% branch, no network)

| Layer | What | How |
|---|---|---|
| Unit | happy path: retrieve → assemble → `chat` → `AnswerResult` with a `Citation` per in-context concept | `tmp_path` bundle (2-3 concepts) + `class _FakeLLM: def chat(self, messages): return "…"` |
| Unit | prompt shape: system grounding text present; user has one labeled block per hit + question | fake captures `messages`, assert structure |
| Unit | zero FTS hits → `NO_MATCH`, `citations==[]`, **`chat` never called** | fake records call count; query with no lexical match |
| Unit | all hits unreadable → zero-context short-circuit (same as zero-hit) | delete/corrupt files after build, or fake index |
| Unit | partial unreadable → bad hit skipped, good hits cited, `chat` called | one valid + one vanished concept |
| Unit | title fallback: concept with no `title` → citation title == concept_id | frontmatter without `title` |
| Unit | `FtsUnavailable` (build) and `OllamaError` (chat) propagate typed, unswallowed | fake `llm.chat` raises; assert `pytest.raises` |
| Unit | `retrieval/` imports no `openkos.config` | `ast` scan (fts/ollama precedent) |

## Threat Matrix

**N/A** — no shell, subprocess, routing, VCS/PR automation, or executable-file
classification. Injection-adjacent surfaces are contained: the raw question is
neutralized for SQLite by `fts._quote_query`; the LLM prompt is built by list
assembly (never used to execute anything); the model reply is returned verbatim to
the caller, never run. Concept bodies are trusted local bundle content.

## Migration / Rollout

No migration. Purely additive; the module is dormant until #4's `query` command
constructs an `OllamaClient` and calls `answer()`. `git revert` removes
`src/openkos/retrieval/` and its tests. No config-schema change.

## Doc drift — defer (no change)

`docs/architecture.md:70,72` name `retrieval/lexical.py` (FTS5) and `retrieval/context.py`
(context assembly + citations). Both are part of the **mature target tree**, which
`architecture.md:105` explicitly labels "not a scaffold to create empty." FTS already
landed in `state/fts.py` (change #1's deliberate, spec-recorded drift), and this change
lands `retrieval/answer.py`, matching neither aspirational name. Correcting only the
`lexical.py` line while `vector/`, `hybrid/`, and `context.py` stay aspirational would
be inconsistent noise. Defer the tree reconciliation to a dedicated docs pass once the
retrieval package shape settles; keep this change minimal (module + tests only).

## Open Questions

- [ ] None blocking. Index lifecycle (D1), guarded read (D2), zero-context (D3),
      citations/title (D4), and prompt shape (D5) all resolved. Context truncation /
      token budget remains a named MVP-1 non-goal (bounded by `limit` concepts).
