# `retrieval_stability` — the near-identical-question citation probe (#648)

Issue #648: on a bundle spanning two unrelated domains (an English Claude
Code/MCP course + Spanish meeting transcripts), two near-identical questions
returned different citation sets, and the MORE SPECIFIC one returned the
worse set — `¿qué es MCP y para qué sirve?` dropped
`concepts/model-context-protocol` entirely and pulled in
`concepts/producto-mínimo-viable-mvp` and an unrelated transcript.

The probe builds a deterministic two-domain bundle in that register,
reindexes it through the PRODUCTION pipeline (`state.reindex` → real FTS5 +
sqlite-vec stores, `bge-m3` embeddings), and runs the production
`retrieval.answer.answer()` (stub chat LLM — retrieval only; the citations
are the measurement) over paraphrase FAMILIES of the same question.

```bash
python evals/retrieval_stability/run_retrieval_stability_probe.py
```

Requires a local Ollama serving `bge-m3`. Zero chat-model calls; a full run
is under a minute.

## What it measured (bge-m3, 2026-08-13/14)

**The failure shape reproduced deterministically.** Pre-fix
(`runs-20260813T235449Z`), the raw pipeline on the 12-doc corpus:

- `¿qué es MCP?` cited the Spanish MVP concept at slot 2 and
  `sources/transcription1` at slot 4 — 2 cross-domain intrusions on a
  4-variant family totalling **6**;
- `¿qué es MCP y para qué sirve?` ranked **`producto-minimo-viable-mvp`
  FIRST** — the issue's exact "more specific is worse" shape;
- the all-English family (`context window`) was perfect: cross-LANGUAGE
  questioning is the trigger, not paraphrase per se.

**Hypothesis 1 (pool too tight) — FALSIFIED.** Pool floors 10, 20, and 30
produced identical citation sets in every family. The intrusions are not a
truncation artifact; they are genuinely ranked high in both channels.

**Root cause: the QUESTION's function words.** FTS5 ships no Spanish
stopword list, so `qué`/`es`/`para` in a Spanish-phrased question about an
English-domain subject lexically match every Spanish document — the raw
FTS channel ranked `producto-minimo-viable-mvp` first for `¿qué es MCP?`.
The dense channel contributes a milder same-language pull; the lexical one
is the dominant term.

**The candidate — content-words-only FTS query — measured and SHIPPED**
(`runs-20260813T235616Z`, raw vs candidate on identical stores): drop the
ES/EN function words (the #618 gate's own vocabulary,
`extraction.concept.LANGUAGE_FUNCTION_WORDS`) from the FTS query, keeping
the raw question when nothing would remain (fail-open); the dense channel
keeps the full question.

| family | raw intrusions | content-words intrusions | retention |
| --- | --- | --- | --- |
| mcp (4 variants) | 6 | **3** | 4/4 → 4/4 |
| context-window (2) | 0 | 0 | 2/2 → 2/2 |

- `¿qué es el Model Context Protocol?` became exactly the canonical
  domain-A set (0 intrusions, was 2);
- `¿qué es MCP?`'s MVP intrusion fell from slot 2 to slot 5, the canonical
  rose to slot 2;
- the "MVP first" inversion on the `y para qué sirve` variant is fixed
  (canonical family doc first); its residual intrusion is `sirve` — a
  content VERB, deliberately outside the function-word lists;
- the mvp family's top slots are identical; its tail-slot changes on a
  12-doc corpus are filler either way (slots 3–5 must be filled from a
  universe of 12) and carry no signal.

Canonical retention was 8/8 in both arms on this corpus — the e2e's
dropped-canonical was reproduced as a rank inversion rather than a full
drop, which the fix corrects at the top of the list where it matters.

Production ships the identical mechanism in
`retrieval/answer.py::_fts_query_terms` (unit + wiring tests pin it), so
re-running this probe now measures the shipped state: the `raw` and
`content-words` arms coincide (`runs-20260814T000252Z` confirms).

## What was NOT changed

- `pool.POOL_FLOOR` stays 10 — measured as a no-op lever here.
- The dense channel still embeds the full question. Language clustering in
  the embedding space remains a milder, unresolved pull; a re-ranking or
  query-translation treatment would need its own measurement.
- `k=5` (the display limit) is untouched.
