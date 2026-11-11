# Excerpt strategy reach comparison: results (#1759)

**Verdict: NOT SUPPORTED. Keep the shipped excerpt.** On 36 held-out questions written blind
to the diagnostic, the shipped excerpt (BM25 over 800-character windows) already holds the
evidence line for 32 of 36 at the shipped share. No arm reached the registered bar of shipped
+ 6. The best arm, hybrid-200, reached 34 of 36.

## Run identity

- Registration: `PREREGISTRATION.md`, merged in #1760 (dabcb145).
- Harness commit: dabcb145, detached worktree, clean tracked tree.
- Embedder: bge-m3 (`790764642607`), used only by the hybrid arms. No chat model was called.
- Questions file sha256 `beac8f02...c758c`, as registered. The file stays outside the
  repository because it quotes transcript lines.
- Run on 2026-11-11 at 02:27 UTC, plus a second run into its own directory with its own
  embedding cache. Stores: `excerpts-20261111T022732.json` and
  `rerun/excerpts-20261111T022843.json`, both under
  `~/Dev/Projects/openkos-eval-runs/excerpt-strategy/`.

## Pre-run check

On the old 30-question fixture, every arm reproduced the diagnostic table exactly: shipped 10
and 14, bm25-400 15 and 19, bm25-200 16 and 17, hybrid-400 18 and 20, hybrid-200 20 and 21.
The lean-marker arm had no diagnostic cell and reached 15 and 18. The shipped arm's text
equals the engine's `bound_sources` output.

## Readout

Oracle reach with the right Source chosen, n of 36 answerable questions:

| Arm | @2179 | @2906 | Gain | Lost | On-topic lines on controls |
|---|---:|---:|---:|---:|---:|
| shipped | 32 | 35 | 0 | 0 | 31 |
| bm25-400 | 31 | 31 | 2 | 3 | 39 |
| bm25-200 | 30 | 31 | 2 | 4 | 36 |
| hybrid-400 | 32 | 34 | 3 | 3 | 56 |
| hybrid-200 | 34 | 35 | 3 | 1 | 62 |
| bm25-400-lean | 31 | 33 | 2 | 3 | 41 |

- No arm passes the registered bar. The best gain over shipped is +2 (hybrid-200), against a
  bar of +6.
- hybrid-200 beats bm25-200 by 4, so it would have been the eligible hybrid. It also sits at
  the 2x premise-bait limit, 62 against 2 x 31; equal to the limit is not over it.
- No arm pulled a forbidden-value line into any control excerpt.
- Determinism: the second run matched within the registered tolerance.

## Reading

- **The diagnostic's low reach did not replicate on independent questions.** Shipped scored
  10 of 30 on the old fixture and 32 of 36 here, on the same 12 meetings, the same shares and
  the same algorithm. Every arm scores the same on the old fixture as in the diagnostic, so
  the gap comes from the questions, not the harness.
- **The likely cause is the old questions.** They were written from gold decision rows, for
  #1741. Their evidence was the annotated decision act, which is often a short or elliptical
  turn whose wording differs from a question about the decided value. The held-out questions
  ask about any fact, and each evidence line states its answer. That is closer to how a person
  queries a meeting, but it may also share more words with the question. One author wrote it,
  and it is not a sample of real user queries.
- **What this establishes:** on these questions, smaller windows and embeddings add at most 2
  questions at the shipped share. They cost more on-topic text in unanswerable cases, and the
  hybrid arms need a new derived cache.
- **What this does not establish:** the answer model's behaviour. Questions about decisions
  phrased the way the old fixture phrased them stay hard for the shipped excerpt, at 10 of 30.
  This result says that is not the general case for long Sources. It does not say that case is
  solved.
