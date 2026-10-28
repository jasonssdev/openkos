# Retrieval gold set v1: the baseline

Descriptive baseline of the shipped retrieval channels (`fts`, `dense`, `fused`) on
`bundle-v1`, scored with the model-free harness in this directory
([#1609](https://github.com/jasonssdev/openkos/issues/1609),
[#1652](https://github.com/jasonssdev/openkos/issues/1652)). No chat model is in any
number below. This is not a pre-registered experiment: the contrasts at the end are
exploratory and carry no multiplicity correction.

## Inputs

| Item | Value |
| --- | --- |
| Bundle | `bundle-v1`, built by production `ingest` |
| Questions | `questions.v1.json`, 56 (35 English, 21 Spanish; 16/9/10 and 11/5/5 single-hop/multi-hop/enumeration), 17 in the vocabulary-gap stratum, hash `22baff7b...0271d60d` |
| Run | channels `fts`, `dense`, `fused`; embedder `bge-m3`; top 10 per channel |
| Judgments | `judgments-v1.json` (committed here): 955 labels over 56 questions, grade 0: 638, grade 1: 181, grade 2: 136 |
| Pool | `full`: the union of the top 10 of the three channels |

Reproduce with no model call, from the repository root:

```bash
W=~/openkos-eval-runs/retrieval-gold
uv run python evals/retrieval_gold/run_retrieval_gold.py adjudicate \
    --sheet $W/sheet-v1.filled.md --key $W/sheet-v1.md.key.json \
    --second $W/sheet-v1-second.md --out $W/judgments-v1.json \
    --workspace $W/bundle-v1
uv run python evals/retrieval_gold/run_retrieval_gold.py score \
    --workspace $W/bundle-v1 --run $W/run-v1.json \
    --judgments $W/judgments-v1.json --out $W/scores-v1.json
uv run python evals/retrieval_gold/run_retrieval_gold.py compare \
    --base $W/scores-v1.json --base-channel dense --cand-channel fused \
    --metric recall@5 --subset vocab-gap
```

The `adjudicate` output regenerates the same labels as the committed file. The only
difference is the free-text `judged_by` field, which `adjudicate` fills with a generic
name and which was set by hand to the line below.

## Who judged (deviation from the registered design)

The registered design asks for people. **The judges were models, on the owner's
delegation:**

- **First judgment, all 955 labels:** four blind Claude Opus judges, one per question
  range (q01-14, q15-28, q29-42, q43-56). Each saw the blind sheet only (opaque
  candidate codes, no channel, rank or score).
- **Second judgment, agreement only:** one blind Claude Sonnet judge on a seeded sample
  of 12 questions (`--sample 12 --seed 4242`), 205 candidates.
- **The question set was authored by a Claude model, with no human review** (the
  owner's decision, recorded in the README). The author and the judges are the same
  model family, so a systematic misreading shared by the family is invisible to the
  agreement figures below.

Read every number as "relevance as three blind Claude judgments see it", not as a
human gold standard.

## Agreement

Over the 12 sampled questions and their 205 candidates:

| Statistic | Value |
| --- | --- |
| Raw agreement | 0.922 |
| Cohen's kappa | 0.842 |
| Linearly weighted kappa | 0.887 |
| Disagreements | 16 of 205 |

### The 16 disagreements and the rule

**Resolution rule: the first judge's label stands.** The second judge exists to measure
agreement, not to overrule. No disagreement was hand-resolved and none was merged, so
the scores below do not depend on the second judge. Whether flipping them would move a
conclusion was not tested.

| Question | Document | First | Second |
| --- | --- | --- | --- |
| q18 | `sources/02-hardware-guide` | 2 | 1 |
| q25 | `concepts/remote-control-design-with-kinetic-energy-and-solar-backup` | 2 | 1 |
| q27 | `decisions/approval-for-permanent-mount-on-pole-14` | 1 | 2 |
| q27 | `organizations/harbour-office` | 1 | 2 |
| q27 | `organizations/lifeboat-crew` | 1 | 2 |
| q27 | `organizations/sailing-school` | 1 | 2 |
| q27 | `organizations/town-s-harbour-committee` | 1 | 2 |
| q27 | `sources/13-city-council-permit` | 1 | 2 |
| q33 | `concepts/harbor-weather-network-station` | 0 | 1 |
| q33 | `concepts/hardware-components` | 0 | 1 |
| q40 | `concepts/backup-and-retention` | 0 | 1 |
| q40 | `concepts/respaldos` | 0 | 1 |
| q40 | `sources/18-backup-and-retention` | 0 | 1 |
| q56 | `concepts/remote-control-design-with-kinetic-energy-and-solar-backup` | 2 | 1 |
| q56 | `decisions/design-choice-for-lighting-on-the-remote-control` | 1 | 0 |
| q56 | `sources/es2015b-es` | 1 | 0 |

Twelve of the 16 sit in three questions (q27: 6, q40: 3, q56: 3). Nine are
grade-1-versus-2 splits; seven are relevant-versus-not, five in the second judge's
favour (q33, q40) and two the other way (q56). Recall treats grades 1 and 2 as
relevant, so the grade splits do not change recall; they change nDCG.

## Scores

`judged@10` is 1.000 for all three channels. **That is by construction, not a quality
signal:** the pool is the union of the channels' top 10, so every document a channel
returns in its top 10 was judged.

### All questions (n=56)

| Channel | recall@5 | recall@8 | recall@10 | mrr | ndcg@5 | ndcg@8 | ndcg@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fts | 0.491 | 0.599 | 0.662 | 0.752 | 0.592 | 0.615 | 0.637 |
| dense | 0.607 | 0.742 | 0.799 | 0.861 | 0.682 | 0.706 | 0.724 |
| fused | 0.568 | 0.749 | 0.807 | 0.881 | 0.681 | 0.737 | 0.760 |

### English (n=35)

| Channel | recall@5 | recall@8 | recall@10 | mrr | ndcg@5 | ndcg@8 | ndcg@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fts | 0.485 | 0.611 | 0.684 | 0.730 | 0.593 | 0.619 | 0.648 |
| dense | 0.580 | 0.741 | 0.785 | 0.836 | 0.700 | 0.724 | 0.737 |
| fused | 0.549 | 0.760 | 0.823 | 0.881 | 0.706 | 0.763 | 0.789 |

### Spanish (n=21)

| Channel | recall@5 | recall@8 | recall@10 | mrr | ndcg@5 | ndcg@8 | ndcg@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fts | 0.501 | 0.578 | 0.626 | 0.789 | 0.592 | 0.609 | 0.619 |
| dense | 0.653 | 0.744 | 0.821 | 0.902 | 0.652 | 0.677 | 0.701 |
| fused | 0.600 | 0.731 | 0.781 | 0.881 | 0.638 | 0.695 | 0.713 |

### Vocabulary-gap stratum (n=17)

| Channel | recall@5 | recall@8 | recall@10 | mrr | ndcg@5 | ndcg@8 | ndcg@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fts | 0.359 | 0.448 | 0.526 | 0.565 | 0.420 | 0.460 | 0.492 |
| dense | 0.581 | 0.747 | 0.763 | 0.804 | 0.619 | 0.673 | 0.676 |
| fused | 0.534 | 0.724 | 0.793 | 0.794 | 0.595 | 0.674 | 0.697 |

### Plain stratum (n=39)

| Channel | recall@5 | recall@8 | recall@10 | mrr | ndcg@5 | ndcg@8 | ndcg@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fts | 0.548 | 0.664 | 0.721 | 0.834 | 0.667 | 0.683 | 0.700 |
| dense | 0.619 | 0.740 | 0.814 | 0.885 | 0.709 | 0.721 | 0.745 |
| fused | 0.583 | 0.760 | 0.814 | 0.919 | 0.718 | 0.765 | 0.788 |

### By question kind

Single-hop (n=27):

| Channel | recall@5 | recall@8 | recall@10 | mrr | ndcg@5 | ndcg@8 | ndcg@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fts | 0.526 | 0.630 | 0.672 | 0.736 | 0.612 | 0.647 | 0.659 |
| dense | 0.651 | 0.785 | 0.827 | 0.812 | 0.668 | 0.708 | 0.723 |
| fused | 0.581 | 0.766 | 0.849 | 0.827 | 0.648 | 0.726 | 0.761 |

Multi-hop (n=14):

| Channel | recall@5 | recall@8 | recall@10 | mrr | ndcg@5 | ndcg@8 | ndcg@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fts | 0.496 | 0.670 | 0.761 | 0.907 | 0.686 | 0.715 | 0.746 |
| dense | 0.459 | 0.547 | 0.656 | 0.806 | 0.609 | 0.595 | 0.631 |
| fused | 0.483 | 0.709 | 0.760 | 0.893 | 0.646 | 0.715 | 0.739 |

Enumeration (n=15):

| Channel | recall@5 | recall@8 | recall@10 | mrr | ndcg@5 | ndcg@8 | ndcg@10 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| fts | 0.423 | 0.475 | 0.553 | 0.637 | 0.470 | 0.465 | 0.495 |
| dense | 0.667 | 0.848 | 0.880 | 1.000 | 0.775 | 0.808 | 0.812 |
| fused | 0.625 | 0.756 | 0.777 | 0.967 | 0.771 | 0.779 | 0.780 |

Kind sizes: single-hop 16 + 11 = 27, multi-hop 9 + 5 = 14, enumeration 10 + 5 = 15.

## Paired bootstrap contrasts

Candidate minus baseline, per-question differences, resampling questions with
replacement (`gold_core.paired_bootstrap`, seed 1609, 10,000 resamples, percentile 95%
interval). Both channels are read from the same scored run, so the pairing is by
question. A contrast is called distinguishable from zero only when its interval excludes
0.

### fused minus dense

| Metric | All (n=56) | Vocab-gap (n=17) |
| --- | --- | --- |
| recall@5 | -0.0391 [-0.0906, +0.0100] | -0.0473 [-0.1470, +0.0334] |
| ndcg@5 | -0.0014 [-0.0503, +0.0447] | -0.0244 [-0.1382, +0.0797] |
| recall@8 | +0.0068 [-0.0668, +0.0813] | -0.0236 [-0.1915, +0.1692] |
| ndcg@8 | +0.0310 [-0.0214, +0.0812] | +0.0014 [-0.1152, +0.1207] |
| recall@10 | +0.0086 [-0.0573, +0.0808] | +0.0295 [-0.1372, +0.2342] |
| ndcg@10 | +0.0367 [-0.0110, +0.0844] | +0.0209 [-0.0882, +0.1384] |

**Every interval includes 0.** Fused trails dense by 0.039 at recall@5 on all questions,
but the interval [-0.091, +0.010] does not exclude 0, so this baseline does not show
that fusion is worse than the dense channel alone at k=5. It does not show equality
either: with 56 questions the interval is about 10 points wide. No issue is opened.

### fused minus fts

| Metric | All (n=56) | Vocab-gap (n=17) |
| --- | --- | --- |
| recall@5 | +0.0771 [-0.0012, +0.1593] | +0.1745 [-0.0719, +0.3947] |
| ndcg@5 | +0.0882 [+0.0056, +0.1725] | +0.1749 [-0.0585, +0.3878] |
| recall@8 | +0.1507 [+0.0829, +0.2237] | +0.2758 [+0.1038, +0.4510] |
| ndcg@8 | +0.1223 [+0.0564, +0.1949] | +0.2141 [+0.0315, +0.3963] |
| recall@10 | +0.1451 [+0.0769, +0.2178] | +0.2662 [+0.1154, +0.4160] |
| ndcg@10 | +0.1235 [+0.0612, +0.1901] | +0.2050 [+0.0421, +0.3649] |

Fused beats the lexical channel alone at k=8 and k=10 on both subsets and at ndcg@5 on
all questions; at recall@5 the interval just includes 0 on all questions (lower bound -0.0012) and
spans it widely on the vocab-gap stratum.

### dense minus fts (context)

| Metric | All (n=56) | Vocab-gap (n=17) |
| --- | --- | --- |
| recall@5 | +0.1162 [+0.0208, +0.2126] | +0.2218 [-0.0468, +0.4579] |
| ndcg@5 | +0.0896 [-0.0098, +0.1956] | +0.1993 [-0.0654, +0.4472] |
| recall@8 | +0.1440 [+0.0286, +0.2634] | +0.2994 [-0.0079, +0.5755] |
| ndcg@8 | +0.0913 [-0.0102, +0.1987] | +0.2127 [-0.0637, +0.4702] |
| recall@10 | +0.1364 [+0.0222, +0.2537] | +0.2367 [-0.0458, +0.4918] |
| ndcg@10 | +0.0868 [-0.0106, +0.1898] | +0.1842 [-0.0761, +0.4252] |

Each table is 12 intervals, and dense minus fts is shown for context only. At a 5% level roughly one interval in twenty excludes 0 by chance, so a lone
marginal interval (for example ndcg@5 fused minus fts, lower bound +0.0056) is weak
evidence.

## Pooling caveats

- **The pool is the union of the channels' top 10, so recall is relative to the pool.**
  A relevant document that none of the three channels returned in its top 10 was never
  shown to a judge and counts as not relevant. recall@k is "of the relevant documents
  the pooled channels surfaced", not of every relevant document in the bundle.
- **Known pool misses.** The judges reported an answer outside the pool for two
  questions:
  - **q01:** the answer to the QoS 1 question is in an MQTT document that was not a
    candidate.
  - **q10:** the "no camera and no microphone" statement is in a document that was not a
    candidate.

  By definition a pool miss is outside every channel's top 10, so it does not change
  any channel's score here or the contrasts above. It caps absolute recall for q01 and
  q10 and penalizes any future technique that retrieves those documents, which is why a
  new candidate needs its own `judged@10` check and a re-pool when low. Other pool
  misses may exist that no judge noticed.
- **Judge-flagged ambiguous questions.** The judges marked these questions as ambiguous
  or underspecified: **q14, q25, q34, q42, q47, q49, q56**. They stay in the set (it is
  frozen and the hash pins it) and in every number above; 7 of 56 is 12.5% of the set.
  No sensitivity analysis without them was run.
- **Model judges, one family.** See "Who judged".
- **Small n.** 56 questions, 17 in the vocabulary-gap stratum and 14 to 15 per
  multi-hop or enumeration kind. Per-kind and per-stratum differences of a few points
  are inside the noise of this set.

## What the baseline says

Stated with the evidence above and no more: dense and fused both clearly beat the
lexical channel alone at depth 8 to 10; fused and dense are not distinguishable at any
tested k on this set; the largest point difference against fused (recall@5, -0.039) is
inside its interval; the multi-hop kind is the only one where the lexical channel has
the highest recall@5 and ndcg@5 (point estimates, n=14, not tested).
