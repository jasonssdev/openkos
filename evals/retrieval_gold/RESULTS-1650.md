# Index the questions a Decision answers: results of #1650

The one-shot run of [`PREREGISTRATION-1650.md`](PREREGISTRATION-1650.md) (doc2query on
Decisions), on the v1 development fixture
([#1650](https://github.com/jasonssdev/openkos/issues/1650),
[#1652](https://github.com/jasonssdev/openkos/issues/1652)). Generation used `qwen3:8b`;
every retrieval run used `bge-m3` only.

**Verdict: A2 is not adopted.** The primary condition P fails: on the frozen
vocabulary-gap stratum A2 lowers nDCG@5 by 0.035 (95% interval -0.075 to +0.005), against a
bar of a gain of at least +0.05 with a lower bound above 0. The
guards G2 and G3 also fail. The control C1 lowers the same metric by more than A2 does, so
there is no semantic effect to credit. Two registered checks are not complete (the
faithfulness spot check needs blind reviewers, and the pool is under-judged for the new
arms); neither can change the verdict on P, and both are stated below.

## Inputs

| Item | Value |
| --- | --- |
| Harness commit | `201f7635` (`origin/main` after #1674), clean |
| Bundle / questions | `bundle-v1`; `questions.v1.json`, 56 questions, hash `22baff7b...0271d60d`; `judgments-v1.json` |
| Generator | `qwen3:8b`, digest `500a1f06...b8b41`, through the production `OllamaClient`; P1 prompt hash `4f9dfef641df36ac` (primary), P2 `b57b36f779a8551d` (exploratory) |
| Embedder | `bge-m3` |
| Arms | A0 shipped, A1, A2 (primary), A3, C1, over one build and one embedding of each question; P1 and P2 each |

## Step 0: the vocabulary-gap stratum

Frozen before any generation (coverage at most 0.5, the registered rule):

| | |
| --- | --- |
| Population (a judged grade 1 or 2 Decision in the gold) | 20 questions |
| No gold Decision | 36 questions |
| **Stratum** | **18 questions** (floor 12: met) |
| Ids | q03 q11 q17 q18 q23 q25 q26 q27 q30 q31 q34 q41 q46 q47 q49 q50 q55 q56 |
| sha256 | `fcecf78253ccd072ab09790fc335efd0436948e75b94d2d19a3eeb85bac0fc04` |

The two population questions outside the stratum are q39 and q40 (G1). The stratum is 18
of 20: the v1 questions copy the Decision's vocabulary less than the registration feared.
It is a different set from the question file's own 17-question `vocab_gap` flag, which is
reported separately by `score`. The hash was passed to `generate --stratum-sha256` and is
recorded in the generated file.

## Generation, cost and leak audit

| | P1 | P2 |
| --- | --- | --- |
| Decisions | 37 | 37 |
| Calls (including retries) | 40 | 40 |
| Decisions with questions | 36 | 35 |
| Failed parse after the one retry | 1 | 2 |
| Questions kept | 108 | 105 |
| Dropped (length, no `?`, language, duplicate) | 0, 0, 0, 0 | 0, 0, 0, 0 |
| p90 per Decision | 4.37 s (bar 6 s) pass | 4.09 s (bar 6 s) pass |
| Total generation | 96.5 s = 0.58% of ingest wall time 16,624 s (bar 10%) pass | 96.9 s = 0.58% pass |
| Mean `eval_count` per call | 49 | 49.5 |
| Leak audit, near copies of a gold question (Jaccard 0.8) | 0 of 108 (0.000%, bar 2%) pass | 0 of 105 pass |

**K (cost bars): pass.** The forecast of 3 to 5 s per Decision held (median 2.5 s). The one
or two Decisions that failed to parse have no questions in any arm, which is the registered
handling.

## Primary and exploratory results (P1)

`fused` channel, nDCG@5 on the frozen stratum, paired bootstrap, seed 1609, 10,000
resamples (`stratum-compare`, 18 questions):

| Arm | Difference from A0 | 95% interval |
| --- | --- | --- |
| **A2 (primary)** | **-0.0348** | [-0.0745, +0.0049] |
| A1 (exploratory) | -0.0294 | [-0.0825, +0.0251] |
| A3 (exploratory) | -0.0029 | [-0.0109, +0.0032] |
| C1 (control) | -0.0706 | [-0.1240, -0.0189] |

Arm means on the stratum, `fused` (A0 in the first column; the difference from A0 in
brackets):

| Metric | A0 | A1 | A2 | A3 | C1 |
| --- | --- | --- | --- | --- | --- |
| nDCG@5 | 0.6837 | 0.6543 [-0.029] | 0.6490 [-0.035] | 0.6809 [-0.003] | 0.6131 [-0.071] |
| recall@5 | 0.5028 | 0.4460 [-0.057] | 0.4627 [-0.040] | 0.5028 [+0.000] | 0.4217 [-0.081] |
| recall@8 | 0.6838 | 0.5924 [-0.091] | 0.6221 [-0.062] | 0.6758 [-0.008] | 0.5612 [-0.123] |
| MRR | 0.9074 | 0.9028 [-0.005] | 0.9722 [+0.065] | 0.8981 [-0.009] | 0.9352 [+0.028] |
| nDCG@8 | 0.7052 | 0.6624 [-0.043] | 0.6594 [-0.046] | 0.6970 [-0.008] | 0.6174 [-0.088] |
| nDCG@5, English (n=11) | 0.7747 | 0.7680 | 0.7410 | 0.7700 | 0.6923 |
| nDCG@5, Spanish (n=7) | 0.5408 | 0.4757 | 0.5043 | 0.5408 | 0.4887 |

The `dense` channel alone is unchanged by A2, A3 and C1 (stratum nDCG@5 0.706 in each);
A1 changes it (0.532), because its question vectors are extra rows in the same table.

A2 raises MRR on the stratum (+0.065) while lowering recall: the third list puts a
Decision first more often and costs the rest of the top 5 and top 8.

## The decision rule

| # | Condition | A2 | Result |
| --- | --- | --- | --- |
| P | Stratum nDCG@5 gain at least +0.05, lower bound above 0 | -0.0348 [-0.0745, +0.0049] | **fail** |
| C | C1's gain under half of A2's | A2 has no gain; C1 -0.0706 | **not creditable** (no semantic effect to separate) |
| G1 | Decision-gold questions outside the stratum: nDCG@5 falls by at most 0.02 (n=2) | 0.9386 to 0.9252: -0.0134 | pass (n=2) |
| G2 | All questions: nDCG@5 falls by at most 0.01 | 0.6806 to 0.6339: -0.0467 | **fail** |
| G3 | No-gold-Decision questions (n=36): recall@8 falls by at most 0.03 | 0.7680 to 0.6664: -0.1016 | **fail** |
| G4 | `delivered` at `fused@8` over the stratum does not fall | 3 of 18 to 3 of 18 (delta 0); `delivered_any` 13 to 12 | pass |
| G5 | Leak audit passes; faithfulness at least 27 of 30; `judged@10` at least 0.90 | leak pass; faithfulness not done; `judged@10` of `fused` 0.696 | **incomplete** (see below) |
| K | Cost bars | p90 4.37 s, 0.58% | pass |

`all` questions, `fused`: recall@5 0.5681 to 0.5295; recall@8 0.7492 to 0.6641; MRR 0.8810
to 0.8750. `delivered` over all 54 questions with a passage: A0 6, A1 5, A2 7, A3 6, C1 4
delivered (`delivered_any`: 30, 27, 29, 30, 27).

### G5, not complete

- **Pool bias.** The judgments were pooled from the baseline channels. `judged@10` of the
  `fused` top 10 is 1.000 for A0 (A3 0.995), but **0.696 for A2, 0.786 for
  A1 and 0.661 for C1**: 30% of A2's top 10 is unjudged and counts as not relevant. The
  registration says to re-pool (judge only the new candidates) before reading any number
  when `judged@10` is under 0.90; **the numbers above were read without re-pooling**, which
  is a deviation. The bias pushes against the arms that retrieve new documents, A2, A1 and
  C1, so the A2 estimate is a lower bound on what a re-pooled score could be. A re-pool
  would need blind relevance judges for the new candidates and has not been done.
- **Faithfulness (27 of 30).** Not run. The registration asks for two blind reviewers; the
  session that ran the generation saw the outputs and cannot be one. The 30-item blind sheet
  (seed 1650) is at `~/openkos-eval-runs/retrieval-gold/r1650/faithfulness-sheet.md`, with
  the key beside it. **Only 9 of the 108 P1 questions are Spanish** (3 Spanish Decisions),
  so "at least 10 per language" is impossible; all 9 are in the sheet, with 21 English.
  Because P already fails, the result does not change the verdict.

## P2 (exploratory, prompt that tells the model not to copy distinctive words)

| Arm | Stratum nDCG@5 difference from A0 | 95% interval |
| --- | --- | --- |
| A2 | -0.0380 | [-0.0754, +0.0004] |
| A1 | -0.0390 | [-0.1110, +0.0349] |
| A3 | +0.0044 | [-0.0088, +0.0226] |
| C1 | -0.0655 | [-0.1041, -0.0267] |

P2 does not rescue the mechanism. A3 (questions in the FTS text only) is the only arm near
zero, with an interval that includes zero in both directions. The all-question nDCG@5 for
P2 A2 is 0.6260 against 0.6806, and recall@8 for no-gold-Decision questions 0.6664 against
0.7680. These are exploratory and could not have been adopted.

## Reading

- **The mechanism did not help on this fixture, and its cost is not small.** A third list
  of Decisions costs recall elsewhere: -0.10 recall@8 on questions with no gold Decision
  (A2 and C1 identically, to four decimals), which the registration named as the known risk.
- **The control matters.** C1, with meaningless questions, loses more (-0.071) than A2
  (-0.035) on the stratum. Whatever lift A2 gets back over C1 (+0.036) is a loss avoided and
  not a gain over the shipped pipeline.
- **n.** The stratum has 18 questions and the interval for A2 reaches +0.005. A true gain of
  the registered size is not excluded by 18 questions in general, but the point estimate is
  negative, the guards fail with margin, and the registration is one-shot.
- **Scope of the evidence.** Only 20 of 56 questions have a gold Decision at all, 18 of them
  are in the stratum, and only 3 of the 37 Decisions are Spanish.

## Deviations and notes

- The faithfulness check and the re-pool are not complete (above).
- Commands that default to `questions.draft.json` were given `--questions
  evals/retrieval_gold/questions.v1.json`.
- The numbers outside `stratum-compare` (guards, per-language, delivered) come from a short
  model-free script over the stored score and delivered files, not from a registered command.
  `delivered` was run with the default assembler (query-aware excerpts off), the setting of
  the baseline in `RESULTS-v1-baseline.md`; `main` now ships query-aware excerpts by default
  (ADR-0082), which does not change the rankings.
- Language per Decision in the faithfulness sheet is `extraction.concept._dominant_language`.
- Adoption would still need the production ADR and storage decision; none is proposed.

## Reproduce

```bash
W=~/openkos-eval-runs/retrieval-gold
uv run python evals/retrieval_gold/prereg_tools.py gap-stratum \
    --questions evals/retrieval_gold/questions.v1.json \
    --bundle $W/bundle-v1/bundle --judgments $W/judgments-v1.json --out $W/r1650/gap-stratum.json
uv run python evals/retrieval_gold/generate_questions.py generate \
    --bundle $W/bundle-v1/bundle --out $W/r1650/generated-p1.json --prompt p1 \
    --manifest $W/bundle-v1/gold-build/manifest.json \
    --stratum-sha256 fcecf78253ccd072ab09790fc335efd0436948e75b94d2d19a3eeb85bac0fc04 --wait-gpu
uv run python evals/retrieval_gold/generate_questions.py cost \
    --generated $W/r1650/generated-p1.json --manifest $W/bundle-v1/gold-build/manifest.json
uv run python evals/retrieval_gold/prereg_tools.py leak-audit \
    --questions evals/retrieval_gold/questions.v1.json --generated $W/r1650/generated-p1.json
uv run python evals/retrieval_gold/run_retrieval_gold.py run \
    --questions evals/retrieval_gold/questions.v1.json --workspace $W/bundle-v1 \
    --doc2query $W/r1650/generated-p1.json --out $W/r1650/d2q.json
# per arm a0 a1 a2 a3 c1: score, then compare on the frozen stratum
uv run python evals/retrieval_gold/run_retrieval_gold.py score \
    --questions evals/retrieval_gold/questions.v1.json --workspace $W/bundle-v1 \
    --run $W/r1650/d2q-a2.json --judgments $W/judgments-v1.json --out $W/r1650/scores-a2.json
uv run python evals/retrieval_gold/prereg_tools.py stratum-compare \
    --stratum $W/r1650/gap-stratum.json --base $W/r1650/scores-a0.json \
    --cand $W/r1650/scores-a2.json --channel fused --metric ndcg@5
```
