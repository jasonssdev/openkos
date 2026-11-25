# Pre-registration: recall, precision and redundancy replace the V4 volume veto (#1796)

**Status: registered on merge to `main`.** No arm's recall, coverage,
near-duplicate rate or G1 delta may be computed before that merge; computing
any of them earlier voids this registration. A change after the merge is a new
registration, not an edit to this one. The owner decision this file rests on
was settled on 2026-11-25, before registration ("Owner decisions" at the end).

## Question

[`HOSTED_RESULTS.md`](HOSTED_RESULTS.md) (#1767) vetoed `openai-gpt-5-mini`,
`anthropic-claude-haiku-5-5` and `openai-gpt-6-luna` on V4: 9.90, 15.60 and
11.60 final objects per meeting-run against a window of [2.50, 7.50]. That
window is [0.5, 1.5] times the reference `qwen3:8b`'s own volume
(`PREREGISTRATION.md`, V4, #1557). It has no external grounding: it measures
resemblance to a weak extractor, not quality. [`EXTRA_OBJECT_RESULTS.md`](EXTRA_OBJECT_RESULTS.md)
(#1783) tried to settle whether the extra objects are useful with two LLM
judges and was INCONCLUSIVE (pooled kappa 0.309 against a floor of 0.6).

More objects are a problem only if they are wrong, redundant, or add nothing.
Each has a direct measure, and volume is a proxy for all three. This
registration asks:

> On the twelve development meetings, does each hosted arm cover more of the
> human-written gold than `qwen3:8b`, without losing precision and without
> being more redundant?

It replaces V4 as a veto **for the hosted arms in this question**. It does not
amend the #1557 registration, which stays as it was for the local screen.

## What is measured

All numbers come from stored records, stored G1 judge labels and the embedding
cache. No hosted model is called.

### Primary: recall against the human gold

- **Gold.** The screen's own: AMI's human abstractive summary, sections
  `abstract`, `decisions` and `problems` (`screen_data.GOLD_SECTIONS`; `actions`
  stays excluded, decision D5 of #1557), as `Corpus.meeting` loads it for each
  meeting.
- **Covered.** A gold sentence is covered by a meeting-run when its best cosine
  (bge-m3, `title: description` of the arm's **final** objects, unit vectors)
  against any of those objects is at least **tau = 0.70**. This is
  `run_concept_screen.meeting_scores` (`own_covered_tau`, `COVERAGE_TAU`); the
  harness checks its per-sentence hits against that field for every
  meeting-run and refuses to report on a disagreement, so there is one coverage
  definition, not two. A meeting-run with no final objects covers nothing.
- **Recall** of an arm is the pooled share: gold sentences covered, summed over
  the twelve meetings and runs 1 and 2, over gold sentences times runs.
- **Statistic.** Recall of the arm minus recall of the reference `qwen3:8b`.
- **Interval.** Paired two-stage bootstrap (`screen_metrics.two_stage_bootstrap`):
  meetings with replacement, then gold sentences with replacement within each
  drawn meeting; a sentence carries its hits in both arms across both runs, so
  arm and reference see the same draw; **10,000 resamples, seed 42**,
  percentile 95%.
- **Pool: DEV12**, `data.POOL_DEV12`: ES2015a-d, IS1004a-d, TS3009a-d.

### Why tau = 0.70

It is the screen's existing coverage operating point (`own_covered_tau`, in the
per-stage loss table of #1559 and in `meeting_scores` since #1557), not a value
chosen for this question or for any arm. Re-tuning it here would be a free
parameter. It was fixed before any arm's covered count was computed for this
registration. The prose-matcher threshold of 0.912 in `calibration.json` is a
different task (paraphrase matching on the prose fixtures) and is not used.

**The full curve, tau 0.50 to 0.90 in steps of 0.05**, is reported for every
arm and the reference as description. It decides nothing: only tau 0.70 enters
the rule. If the sign of the delta differs elsewhere on the curve, the results
say so; the verdict does not move.

### Guardrail 1: G1 precision (unchanged)

`run_concept_screen.score_g1` against the reference: the 95% lower bound of the
paired pooled precision delta (two-stage bootstrap over meetings, then windows)
must be at least **-0.05** (`screen_metrics.PRECISION_FLOOR`). The judge is the
local `gpt-oss:20b` at the digest the existing labels were made with. The floor
is not moved.

### Guardrail 2: near-duplicate ceiling

The within-run near-duplicate rate is `hosted_screen.near_dup_flags` at cosine
**0.85** (`hosted_screen.NEAR_DUP_THRESHOLD`, from `cut_redundancy.py`): an
object is flagged when another object of the same meeting-run reaches that
cosine. The rate is flagged objects over final objects, pooled per meeting.
The statistic is the arm's rate minus the reference's rate, with
`hosted_screen.rate_delta` (meetings drawn with replacement, the same draw for
both arms, 10,000 resamples, seed 42).

**The ceiling: the 95% upper bound of that difference is at most +0.05.**

Justification, fixed before any arm's near-duplicate rate was computed for this
registration: it is five points, the magnitude of G1's floor (-0.05), so the
two guardrails allow the same amount of harm on a rate (five points more
duplicated objects, five points fewer supported ones). It is an upper bound on
an interval, the same kind of test as G1's, so a noisy arm fails on width, as
G1 does; that cost is accepted rather than hidden. It was chosen from G1's
floor and from nothing measured on an arm; the 0.80 and 0.90 thresholds of
`cut_redundancy.py` are not computed.

### Reported only: volume

Objects per meeting-run, arm and reference, and their ratio. **It is not a
veto and not a guardrail.** Reasons: V4 was [0.5, 1.5] times the reference's own
volume (`PREREGISTRATION.md`, V4) and has no external grounding; #1783 could not
show that the extra objects are worse (INCONCLUSIVE); and the owner's decision
is that more objects are acceptable if they are not wrong or redundant
("Owner decisions").

### Why precision and redundancy bind

**Recall is non-decreasing in volume by construction.** A gold sentence's best
cosine is a maximum over the arm's objects, so adding an object can only raise
it (the self-test checks this). An arm can therefore gain recall by emitting
more, including wrong or repeated objects. Without guardrails, recall alone
would reward exactly what V4 punished. G1 catches the wrong objects and the
near-duplicate ceiling catches the repeated ones; recall cannot be read without
them.

## Arms

| Arm | Records | Notes |
|---|---|---|
| `openai-gpt-5-mini` | #1767 control prompt | control only; no `+evidence` exists |
| `anthropic-claude-haiku-5-5` | #1767 and #1783 control prompt | |
| `openai-gpt-6-luna` | #1767 and #1783 control prompt | |
| `anthropic-claude-haiku-5-5+evidence` | #1768, hosted evidence A/B | the `evidence_quote` instruction |
| `openai-gpt-6-luna+evidence` | hosted evidence A/B | the `evidence_quote` instruction |

Reference: `qwen3:8b`, runs 1 and 2 on DEV12, read from cache. Every arm is
compared with the reference, **not with its own control**; the control-versus-
`+evidence` comparison belongs to `HOSTED_EVIDENCE_PREREGISTRATION.md`. The six
labels are scored on the same records, read from
`records/<label>/<meeting>/r{1,2}.json` under the runs directory. `openai-gpt-4.1-mini`
is not an arm (WORSE in #1767).

## Verdict per arm

Evaluated in this order by `recall_screen.py report` (`decide_arm`):

1. **INCOMPLETE.** Any of: fewer than 24 meeting-runs for the arm or the
   reference; a hosted window that ended in a provider failure
   (`hosted_screen.provider_error_count`: `error`, or `not_called` on a chunked
   record; a record that does not say stays fail-closed); a missing G1 judge
   record for the arm or the reference; no interval computable. An INCOMPLETE
   arm has no verdict on recall.
2. **WORSE.** The recall delta's 95% upper bound is below 0.
3. **BETTER RECALL.** The recall delta's 95% lower bound is above 0 **and** both
   guardrails hold (G1 lower bound at least -0.05; near-duplicate upper bound at
   most +0.05).
4. **VETOED.** The recall delta's 95% lower bound is above 0 and a guardrail
   fails. The verdict names the guardrail(s), with the point and interval.
5. **NOT DISTINGUISHABLE.** Otherwise: the recall interval contains 0. Guardrail
   failures, if any, are listed beside it.

Boundaries: a lower bound of exactly 0 is not above 0; an upper bound of exactly
0 is not below 0; a G1 lower bound of exactly -0.05 and a near-duplicate upper
bound of exactly +0.05 hold. Only a recall gain can be vetoed: an arm with no
demonstrated gain has nothing to veto.

### What a verdict changes

**No default.** `DEFAULT_MODEL`, `DEFAULT_TASK_MODELS`, every prompt and every
backend default stay as they are; OpenKOS stays local-first and a hosted model
stays an opt-in backend. A BETTER RECALL arm is **recommended in
[`docs/cli.md`](../../docs/cli.md)** as a hosted option with the numbers that
earned it (a separate pull request after the results). WORSE, NOT
DISTINGUISHABLE, VETOED and INCOMPLETE arms are not recommended; the results
file records why. No verdict re-opens V4 for the local screen.

## Forecast

**Hosted calls expected: 0. Hosted spend: 0 USD.** Everything is read from
cache. **Ceilings: 0 calls and 0 USD**; any hosted call aborts the run, and
`recall_screen.py` contains no code path that builds a hosted client.

Presence of the inputs, listed without reading any score (2026-11-25):

| Label | Records (of 24) | G1 judge records (of 24) |
|---|---|---|
| `qwen3:8b` (reference) | 24 | **10** |
| `openai-gpt-5-mini` | 24 | **10** |
| `anthropic-claude-haiku-5-5` | 24 | 24 |
| `openai-gpt-6-luna` | 24 | 24 |
| `anthropic-claude-haiku-5-5+evidence` | 24 | 24 |
| `openai-gpt-6-luna+evidence` | 24 | 24 |

24 is twelve meetings times runs 1 and 2. Where G1 judge records exist for ten
of 24, they cover the five pool-DA meetings; the other **14 meeting-runs each,
for `qwen3:8b` and `openai-gpt-5-mini` (28 records)** over ES2015b, ES2015c,
IS1004a, IS1004c, TS3009a, TS3009b and TS3009d, do not exist. Without them G1
cannot be computed for any arm, because the reference is the comparison for all
of them.

- **A local run is needed**: `run_concept_screen.py judge` with `gpt-oss:20b`
  on the owner's GPU for those 28 meeting-runs (free in money; `IS1004a` is a
  whole-source meeting and holds no windowed G1 items). **The owner is asked
  before the run starts**, as for every GPU lane.
- **Embeddings.** The harness serves cached vectors only. Whether every gold
  sentence and final object is already in the cache was not checked (checking
  needs the embedding digest and a scoring pass). A miss is an error that
  names the count and stops; `--embed-missing` is the explicit opt-in to fill
  it with the local `bge-m3`, again after asking the owner.
- Nothing here needs the network or a key. The report never reads
  `OPENKOS_OPENAI_API_KEY`.

## Data boundary

Development series only: ES2015, IS1004 and TS3009 (pool DEV12). The harness
refuses any pool other than DEV12 and any meeting outside `data.POOL_DEV12`
(through `screen_data.require_allowed` first, then an exact match to the twelve).
**Never** the thesis evaluation series ES2002, ES2008, ES2014, ES2016, IS1003,
IS1006, IS1008, IS1009, TS3003, TS3005, TS3011; never the reserve series;
never pool F (including `F-noTS` and `F-IB`).

## Procedure

```sh
S=evals/concept_model_screen
# 0. After the owner agrees to the GPU lane: the 28 missing G1 judge records.
uv run python $S/run_concept_screen.py judge --pool DEV12 --runs 1 2 \
  --models qwen3:8b openai-gpt-5-mini
# 1. The registered report. Cached embeddings only; stops on a miss.
uv run python $S/recall_screen.py report --pool DEV12
#    If it stops on missing vectors and the owner agrees: add --embed-missing.
```

The report prints the markdown verdict table and writes
`scores/recall-screen-DEV12.json` under the runs directory. The results go to
`RECALL_RESULTS.md` in a separate pull request.

## Deviations

The procedure is the one above. A change to any registered quantity (tau, the
resample count or seed, the G1 floor, the near-duplicate threshold or ceiling,
the arm list, the pool, the verdict order) after merge is a **new
registration**. A departure in how the run is carried out (a retried command, a
cache that had to be filled, a record that was missing) is listed in
`RECALL_RESULTS.md` under "Deviations from the procedure" with what was done and
why; nothing is omitted because it did not matter. No such deviation is
recorded here yet: this section is a placeholder until the run.

## How the outcome is stated

`RECALL_RESULTS.md` gives, per arm: the recall of the arm and of the reference
as n of TOTAL, the delta with its interval, the G1 delta with its interval and
the per-meeting values beside the bound, the near-duplicate rates as n of TOTAL
with the delta and its interval, objects per meeting-run for both, the full
tau curve, the verdict, and for VETOED or INCOMPLETE the named reason. It
states the verdict in the words of this file and does not rank arms by a
number the rule does not use. A NOT DISTINGUISHABLE or guardrail failure caused
by interval width alone is described as "not shown at twelve meetings", not as
"no effect". Every limit below is repeated beside the verdicts.

## Not concluded

- **Power was not estimated.** Twelve meetings carry the bootstrap, and the
  intervals can be wide; NOT DISTINGUISHABLE and a guardrail failure on width
  are both possible for an arm that is in fact better.
- **Cosine coverage is not understanding.** An object near a gold sentence in
  bge-m3 space may paraphrase it, share its topic or only its vocabulary; the
  screen's own control (foreign gold) exists for that reason and is not part of
  this rule, which asks for coverage of the meeting's own gold.
- **Recall rewards volume** and is read only with the guardrails above.
- **One gold.** AMI's human abstractive summary is one annotator's account of
  each meeting; a useful object the summary did not mention counts for nothing.
- **Alias drift.** The hosted records were made on 2026-11-23 and 2026-11-24
  under provider aliases that can move; the study cannot separate that from the
  model.
- **Not the local default.** Nothing here changes `qwen3:8b` or recommends
  leaving it; hosted use stays opt-in.

## Owner decisions

Settled by the owner on 2026-11-25, before registration:

- **V4 is judged arbitrary and the reference weak.** The volume window
  anchored to `qwen3:8b` is not a quality criterion for the hosted arms
  ("if that means extracting more objects, then we will").
- **Replace V4 with recall plus guardrails, before the next release.** Recall
  against the human gold is primary; G1 precision and a near-duplicate
  ceiling guard it; volume is reported only. The result informs the release
  being prepared, which is held until this study finishes.
- **Pool DEV12, development series only**; pool F and every evaluation and
  reserve series stay untouched.

The tau (0.70, the screen's own) and the near-duplicate ceiling (+0.05, the
magnitude of G1's floor) were proposed by the author of this registration from
the arguments above and were not selected on any arm's data; the owner reviews
them in the pull request, and a change before merge is a change to this file.

## Disclosed before registration

- Read: the #1767 V4 volumes quoted in the question; the status of #1783; the
  stored files' names and presence (the table above); the harness code
  (`run_concept_screen.py`, `screen_metrics.py`, `hosted_screen.py`,
  `screen_data.py`).
- Not read or computed for this registration: any arm's covered count, recall,
  near-duplicate rate or G1 delta against `qwen3:8b` on DEV12. Earlier
  registrations published, on pool DA, soft coverage lift and (for the hosted
  arms) near-duplicate rates and G1 against their own controls; those are
  known to the owner and were not consulted to choose tau or the ceiling.
- No model was called; no hosted request was made for this file. The
  self-test of `recall_screen.py` is model-free and runs under
  `evals/run_self_tests.py` with `OLLAMA_HOST` poisoned.
