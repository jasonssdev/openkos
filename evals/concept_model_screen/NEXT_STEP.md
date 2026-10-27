# Next step for #1579: what does the selector judge's cut still buy?

Status: decision document. It registers nothing, changes no code in `src/` and
no default. Epic #1556 rules apply to whatever follows: pre-register and merge
before any run, never adopt a default in a results PR, one ADR per adoption.
Every number below was read from the committed results files named beside it,
or from `cut_profile.py` (model-free, over the stored #1571 replay records).

## 1. What steps 1 and 2 established

Setting: the #1571 replays. Per record, the judge's input is the `ceiling`
stage (352 objects over 25 meeting-runs: 5 extractors x 5 meetings, round 1),
and the judge cuts a subset. Two arms: `self` (the extractor judges) and
`fixed` (`gemma4:26b-a4b`, the packaged judge).

**The cost, already measured** (`STAGE_LOSS_RESULTS.md`, `JUDGE_STUDY_RESULTS.md`):

- L10, judge selection: window lift -0.0161 [-0.0276, -0.0081] pooled; 38 of
  450 gold sentences lost; 5 of 5 models at the -0.005 bar.
- The backstop (L11) cost 0.0000 and cut 0 objects after the judge.
- A fixed judge still loses lift on every extractor; on `qwen3:8b` it loses
  more than the self judge (-0.0203 vs -0.0116).

**Step 1, support** (`CUT_PRECISION_RESULTS.md`): inconclusive by the
registered rule. Strict G1 precision, primary, seed 0:

| Arm | kept | cut | kept - cut [95% CI] |
|---|---|---|---|
| `self` | 153 of 165 (0.927) | 128 of 138 (0.928) | -0.0003 [-0.0681, +0.0689] |
| `fixed` | 147 of 160 (0.919) | 134 of 143 (0.937) | -0.0183 [-0.0963, +0.0510] |

Rule 1 (the cut buys precision) fails clearly. Rule 2 (cut too strict) misses
by the upper bounds, +0.0689 and +0.0510 against +0.05. The `fixed` label
depends on pooling `gemma4:26b-a4b` (without it, rule 2 alone). Limits: one
labeller, window-only source, ~14-point intervals, and "supported" cannot see
duplicates or vagueness.

**Step 2, redundancy** (`CUT_REDUNDANCY_RESULTS.md`): mixed or inconclusive,
leaning "distinct content". At cosine 0.85, near-duplicates of a kept object:

| Arm | cut | kept | cut - kept [95% CI] |
|---|---|---|---|
| `self` | 8 of 138 (0.058) | 9 of 214 (0.042) | +0.0159 [-0.0378, +0.0801] |
| `fixed` | 8 of 143 (0.056) | 4 of 208 (0.019) | +0.0367 [-0.0135, +0.1063] |

`self` meets rule 2; `fixed` misses by 0.006 on the upper bound. About 94% of
cut objects are not near-duplicates of a kept one. The seed signs flip.

**Net:** the cut costs coverage, and at this n it buys neither source support
nor deduplication. Neither step measured whether the cut objects are vague,
low-value, or clutter to a person.

## 2. What the cut might still buy, and what stored data can say

New, model-free profile (`cut_profile.py profile`, seed 0, 25 of 25 records
measurable in both arms). It describes, it does not test.

| Candidate benefit | Measurable model-free from stored data? | What the profile shows |
|---|---|---|
| Smaller bundle / less clutter | Size yes; whether the extra objects are clutter no | With the judge: 214 (`self`) and 209 (`fixed`) objects over 25 sources. Ceiling alone with the backstop of 20: 329. So the judge removes about a third (115 and 120 of 329). |
| Precision on a judged gold | No: AMI gold is coverage, not an object-level precision set | Step 1 is the only precision view. |
| Vague or shallow objects | Partly: body size is a stored field, vagueness is not | Cut objects are larger, not smaller: median body 441 chars cut vs 337 kept (`self`), 465 vs 331 (`fixed`). The "fewer, richer" prompt is not removing short objects. |
| Which classes are cut | Yes | Cut rate by type, `self` / `fixed`: Project 43 of 61 / 55 of 61; Event 14 of 25 / 18 of 25; Concept 23 of 60 / 28 of 60; Decision 57 of 129 / 41 of 129. Person 0 of 48, Organization 0 of 9, Procedure 0 of 18 (consistent with the deterministic re-admission of those types; I did not check which stage the record's `judge` list captures). |
| Cost downstream (curation, review queues) | Not from these records | Objects feed edge typing, contradiction and identity stages, which price per candidate pair or group; the 1 judge call per source is small beside them. Measuring it needs the downstream stages run on the two bundles. |
| Retrieval noise | No | `evals/retrieval_gold/` has no judgments yet and no AMI bundle. |
| Positional-cut safety | Yes, bound only | With no judge, the backstop would cut 23 of 352 objects, in 8 of 25 sources, by position. The `none` arm of #1571 has no backstop, so its lift is an upper bound for "remove the judge, keep the backstop". |

Two things in the profile matter for the owner. Decisions are cut at 44% and
32% (57 and 41 of 129), although the judge prompt says decisions rank ahead of
summaries. And Project objects are cut at 70% to 90%: if Projects are the
class the judge removes as "over-eager enumeration", that is a product choice
nobody has measured the value of.

## 3. Options

Per-source LLM calls: the judge is 1 call (at most `JUDGE_ATTEMPTS` = 2), about
3.4 s each on the fixed judge in #1571 (350 s over 102 new calls). Extraction
itself is windows + re-ask + participant capture.

| Option | Users | Calls per source | Downstream |
|---|---|---|---|
| A. Keep the judge as is | Bundles about a third smaller; ~38 of 450 gold sentences (8%) missing; most Projects and 3 in 10 to 4 in 10 Decisions cut | unchanged | unchanged |
| B. Judge becomes a cap only (select only when over some N) | Fewer cuts on small sources; same behaviour on large ones | 0 to 1 judge calls | between A and C |
| C. Drop-only judge (remove container, unsupported, duplicate; keep the rest) | Bundle size near the ceiling; recovers some of the 0.0161 | 1 (new prompt) | more objects reach edge typing and contradiction |
| D. Remove the judge; ceiling 24 and backstop 20 stay | About 329 vs 214 objects over 25 sources; positional cut in 8 of 25 sources | -1 (and -1 on retry) | most objects, positional loss on large sources |
| E. Opt-in: keep A as default, flag for D or C | No default change; power users choose | 0 or 1 | per user |

Adoption under the epic rules, for any of B to E: a pre-registration merged
before the first run; an arm comparison on development-series or free-pool
meetings only (never the 11 evaluation or 18 reserve series; TS3005 stays
excluded); raw generations kept outside the repo; a results PR that adopts
nothing; then a separate ADR plus a change to `DEFAULT_UNION_JUDGE`-adjacent
config in its own PR. The existing `extraction-union-judge` spec requires a
judge-selects contract, so B to E also need a spec delta.

## 4. Recommendation (DRAFT, not a registration)

Do not record the current rule as sound: it costs lift and shows no benefit on
the two axes tested. Do not loosen it either: the one axis that could justify
the cut, whether a person wants the cut objects as pages, is unmeasured.

**One measurement: a standalone-value label on the same objects.** Reuse the
step-1 machinery (`gpt-oss:20b`, the #1557 guardrail judge, one call per
window over the 303 windowed ceiling objects, cut and kept judged together)
with a different question per object: would a person curating this bundle keep
it as its own page (`keep`), fold it into another object (`merge`), or drop it
(`drop`)? Before any claim, add a control that can come back NO: run the same
labeller on the objects the deterministic dedup stage removed (known
duplicates) and require it to label most of them `merge` or `drop`; if it
does not, the rubric is vacuous and the study is void.

**Draft decision rule** (to be fixed in a pre-registration, thresholds open to
owner review). Per arm, on the paired two-stage bootstrap used in steps 1 and
2, with D = share(`drop` or `merge`) among cut minus among kept:

1. The cut buys clutter reduction: lower bound of D above +0.10, control
   passes. Outcome: record the lift cost as the price, with an ADR.
2. The cut removes keepable objects: upper bound of D below +0.10, and the
   share of cut objects labelled `keep` at least 0.70. Outcome: file a
   drop-only judge arm (option C) for pre-registration.
3. Otherwise inconclusive: no change, and the next question is the downstream
   cost, which needs a human or a pipeline run, not this labeller.

Report per type (Decision, Project, Concept, Event) as descriptive only.

**Forecast** from step 1's measured timings: same 216 window calls, 9.9 s per
call, 2137 s in judge calls, about 36 min wall time (upper estimate 1 h); no
extraction, no selector-judge call, no embedding. It needs Ollama exclusive to
the study. A cache-only companion, costing no model time, is the lift of the
"ceiling plus backstop of 20" arm from the cached embeddings, to turn the
option D upper bound into a number.

## 5. Decisions only the owner can make

- Is a smaller bundle worth lost gold content, and how much (the bar for
  rule 1 above)?
- Are Project objects, and the cut 32% to 44% of Decisions, content the engine
  should keep by default?
- Is an LLM-labelled value judgment acceptable evidence, or must this step
  wait for a human-labelled sample?
- Is an opt-in switch (option E) acceptable product surface, given the config
  keys it adds?
- Whether the downstream stages (edge typing, contradictions) should be priced
  on the two bundles before the label study.
