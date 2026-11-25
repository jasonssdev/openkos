# Results: recall, precision and redundancy replace the V4 volume veto (#1796)

**Outcome, per arm, in the words of the registration: `openai-gpt-6-luna` with
`evidence_quote` is BETTER RECALL. `openai-gpt-5-mini`,
`anthropic-claude-haiku-5-5` and `anthropic-claude-haiku-5-5+evidence` are
VETOED (G1 precision lower bound). `openai-gpt-6-luna` without evidence is NOT
DISTINGUISHABLE (recall lower bound exactly +0.0000, which is not above 0).**
No default changes. The one arm the registration supports recommending in
`docs/cli.md` is `openai-gpt-6-luna` with `evidence_quote: true`.

Rules: [`RECALL_PREREGISTRATION.md`](RECALL_PREREGISTRATION.md), merged in
#1797 (`d5c21e07`) before any arm's recall, near-duplicate rate or G1 delta was
computed. The rules were applied by `recall_screen.py report --pool DEV12`, not
by hand and not re-tuned: tau 0.70, paired two-stage bootstrap, 10,000
resamples, seed 42, G1 floor -0.05, near-duplicate ceiling +0.05. Population:
pool DEV12 (twelve development meetings), runs 1 and 2, 24 meeting-runs per
label, 239 gold sentences per run (478 sentence-runs). Reference: `qwen3:8b`.
Hosted records are those of #1767, #1783 and #1768 (2026-11-23 and 2026-11-24);
the 28 missing G1 judge records were made on 2026-11-25. **0 hosted calls, 0 USD.**

## Verdicts

Recall is gold sentences covered (best cosine at least 0.70) over 478
sentence-runs. Deltas are arm minus `qwen3:8b`, 95% percentile intervals.

| Arm | Recall, arm | Recall, reference | Recall delta [95%] | G1 delta [95%] (need lower >= -0.05) | Near-duplicate delta [95%] (need upper <= +0.05) | Verdict |
|---|---|---|---|---|---|---|
| `openai-gpt-5-mini` | 65 of 478 (0.136) | 24 of 478 (0.050) | +0.0858 [+0.0248, +0.1453] | -0.0154 [-0.0914, +0.0684] fails | -0.0407 [-0.1077, +0.0128] holds | **VETOED** (G1) |
| `anthropic-claude-haiku-5-5` | 98 of 478 (0.205) | 24 of 478 (0.050) | +0.1548 [+0.0735, +0.2361] | -0.0306 [-0.1004, +0.0498] fails | -0.0406 [-0.1065, +0.0110] holds | **VETOED** (G1) |
| `openai-gpt-6-luna` | 60 of 478 (0.126) | 24 of 478 (0.050) | +0.0753 [+0.0000, +0.1492] | +0.0160 [-0.0387, +0.0934] holds | -0.0487 [-0.1101, +0.0000] holds | **NOT DISTINGUISHABLE** |
| `anthropic-claude-haiku-5-5+evidence` | 101 of 478 (0.211) | 24 of 478 (0.050) | +0.1611 [+0.0776, +0.2446] | -0.0140 [-0.0972, +0.0740] fails | -0.0460 [-0.1098, +0.0000] holds | **VETOED** (G1) |
| `openai-gpt-6-luna+evidence` | 76 of 478 (0.159) | 24 of 478 (0.050) | +0.1088 [+0.0367, +0.1795] | +0.0206 [-0.0476, +0.1000] holds | -0.0495 [-0.1110, +0.0000] holds | **BETTER RECALL** |

Reasons printed by the harness:

- `openai-gpt-5-mini` (VETOED): G1 precision, lower bound -0.0914 below -0.05.
- `anthropic-claude-haiku-5-5` (VETOED): G1 precision, lower bound -0.1004 below -0.05.
- `openai-gpt-6-luna` (NOT DISTINGUISHABLE): recall delta +0.0753 [+0.0000, +0.1492]. The registered boundary is that a lower bound of exactly 0 is not above 0.
- `anthropic-claude-haiku-5-5+evidence` (VETOED): G1 precision, lower bound -0.0972 below -0.05.
- `openai-gpt-6-luna+evidence` (BETTER RECALL): recall delta +0.1088 [+0.0367, +0.1795], both guardrails hold.

Order applied: INCOMPLETE (none: 24 of 24 meeting-runs for every label, no
provider-failure window, no missing G1 record, 0 unparsed G1 windows), WORSE
(none: no recall upper bound below 0), BETTER RECALL, VETOED, NOT DISTINGUISHABLE.

## What the numbers describe

Stated as description. The verdicts above are the registered rule's output;
nothing below changes one.

- **Every arm covers 2.5 to 4.2 times what the reference covers.** Recall point
  estimates are 0.126 to 0.211 against the reference's 0.050 (24 of 478): 60 to
  101 sentence-runs covered against 24. The recall interval excludes 0 for
  every arm except plain `openai-gpt-6-luna`, whose lower bound is +0.0000.
- **Every arm has fewer near-duplicates than the reference.** All five
  near-duplicate deltas are negative (-0.0407 to -0.0495). More objects did
  not mean more redundancy. Pooled rates, flagged over final objects at cosine
  0.85: reference 8 of 141 (0.057); `openai-gpt-5-mini` 4 of 250 (0.016);
  `anthropic-claude-haiku-5-5` 6 of 371 (0.016); `openai-gpt-6-luna` 2 of 249
  (0.008); `anthropic-claude-haiku-5-5+evidence` 4 of 371 (0.011);
  `openai-gpt-6-luna+evidence` 2 of 278 (0.007). The upper bounds sit
  between +0.0000 and +0.0128, far under the +0.05 ceiling.
- **The G1 vetoes come from wide intervals, not from lower point precision.**
  The point deltas are -0.0154, -0.0306, +0.0160, -0.0140 and +0.0206, a range
  of -0.031 to +0.021. The intervals are 0.14 to 0.17 wide at twelve meetings,
  so the lower bound reaches -0.0914, -0.1004 and -0.0972 for the three vetoed
  arms. Per the registration this is described as "not shown at twelve
  meetings", not as "lower precision". The two arms that hold G1 (`luna`, and
  `luna+evidence`) hold it with lower bounds of -0.0387 and -0.0476, close to
  the floor, not far above it.
- **Volume is reported only.** Objects per meeting-run against the reference's
  5.88: `openai-gpt-5-mini` 10.42, `anthropic-claude-haiku-5-5` 15.46,
  `openai-gpt-6-luna` 10.38, `anthropic-claude-haiku-5-5+evidence` 15.46,
  `openai-gpt-6-luna+evidence` 11.58. Under the retired V4 window of [2.50, 7.50]
  (as quoted in the registration) every arm would have been vetoed.
- **Recall is non-decreasing in volume by construction.** A gold sentence's
  best cosine is a maximum over the arm's objects. That is why G1 and the
  near-duplicate ceiling bind and why recall is not read alone. Here the
  near-duplicate guardrail holds for all five arms, and G1 holds only for the
  two `openai-gpt-6-luna` arms. `openai-gpt-6-luna+evidence` is the one arm
  with a recall interval above 0 that holds both.
- **Absolute recall at tau 0.70 is low for every arm.** The best arm covers
  0.211 of the gold sentence-runs and the reference 0.050. Most of the human
  abstractive summary is not matched at 0.70 by any arm. The next section gives
  the whole curve.

## Recall curve, tau 0.50 to 0.90 (description only)

Recall at tau 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90. Only tau
0.70 enters the rule.

| Label | 0.50 | 0.55 | 0.60 | 0.65 | **0.70** | 0.75 | 0.80 | 0.85 | 0.90 |
|---|---|---|---|---|---|---|---|---|---|
| reference `qwen3:8b` | 0.709 | 0.515 | 0.289 | 0.157 | **0.050** | 0.021 | 0.006 | 0.004 | 0.000 |
| `openai-gpt-5-mini` | 0.918 | 0.755 | 0.523 | 0.291 | **0.136** | 0.038 | 0.013 | 0.002 | 0.000 |
| `anthropic-claude-haiku-5-5` | 0.962 | 0.851 | 0.667 | 0.402 | **0.205** | 0.073 | 0.017 | 0.002 | 0.000 |
| `openai-gpt-6-luna` | 0.908 | 0.778 | 0.531 | 0.285 | **0.126** | 0.052 | 0.015 | 0.004 | 0.000 |
| `anthropic-claude-haiku-5-5+evidence` | 0.964 | 0.864 | 0.667 | 0.418 | **0.211** | 0.088 | 0.019 | 0.000 | 0.000 |
| `openai-gpt-6-luna+evidence` | 0.923 | 0.803 | 0.567 | 0.316 | **0.159** | 0.054 | 0.017 | 0.004 | 0.000 |

Every arm is above the reference from tau 0.50 to 0.80. At tau 0.85 the
reference's 0.004 is about two sentence-runs of 478; the arms read 0.002
(`openai-gpt-5-mini`, `anthropic-claude-haiku-5-5`), 0.004 (`openai-gpt-6-luna`,
`openai-gpt-6-luna+evidence`) and 0.000 (`anthropic-claude-haiku-5-5+evidence`),
that is at or below the reference. At tau 0.90 every label is 0.000. The sign
of the difference therefore does not hold at the two highest thresholds; those
counts are one or two sentences, and the verdict does not move.

## Per-meeting values and what was not printed

The registration asks for the per-meeting G1 values beside the bound. The
harness prints and stores the pooled point and the interval, not per-meeting
values, and none were computed for this file. This is listed under
"Deviations" as an item of the report, not of the procedure. The decisions
above use only the pooled intervals the rule names.

## G1 judge records (registered step 0)

The 28 missing G1 judge records (`qwen3:8b` and `openai-gpt-5-mini`, DEV12
non-DA meetings ES2015b, ES2015c, IS1004a, IS1004c, TS3009a, TS3009b and
TS3009d, runs 1 and 2) were made with the local `gpt-oss:20b` on 2026-11-25,
about 14:25 to 14:42 local time, on the owner's GPU, authorized before the run.
Result: 28 of 28 meeting-runs judged, 0 unparsed windows (log
`rc_judge.log`). IS1004a is a whole-source meeting and holds no windowed G1
items (0 windows in both runs for both labels). The report then read 0 unparsed
G1 windows for every arm.

## What a verdict changes

Per the registration, the following and nothing else.

- **No default changes.** `DEFAULT_MODEL`, `DEFAULT_TASK_MODELS`, every prompt
  and every backend default stay as they are. OpenKOS stays local-first;
  `qwen3:8b` stays the local default; a hosted model stays an opt-in backend.
- **Recommended in `docs/cli.md`** (a separate pull request): `openai-gpt-6-luna`
  with `evidence_quote: true`, with these numbers: recall +0.1088 [+0.0367,
  +0.1795] over `qwen3:8b`, G1 delta +0.0206 [-0.0476, +0.1000], near-duplicate
  delta -0.0495 [-0.1110, +0.0000], 11.58 objects per meeting-run. This is the
  only arm the study supports recommending.
- **`anthropic-claude-haiku-5-5` is not supported by this study**, with or
  without evidence: its G1 lower bound fails (-0.1004 and -0.0972). The
  earlier [`HOSTED_EVIDENCE_RESULTS.md`](HOSTED_EVIDENCE_RESULTS.md) (#1768)
  supported `evidence_quote` for haiku against its own control (SUPPORTED, G1
  delta +0.0166 [-0.0368, +0.0696]). The two statements answer different
  questions: that study asks whether the instruction makes haiku's objects
  better evidenced than haiku's own control; this one asks whether haiku covers
  more gold than `qwen3:8b` without losing G1 precision against it. Both hold
  at once.
- `openai-gpt-5-mini`, `anthropic-claude-haiku-5-5`,
  `anthropic-claude-haiku-5-5+evidence` (VETOED) and `openai-gpt-6-luna` (NOT
  DISTINGUISHABLE) are not recommended.
- **No verdict re-opens V4 for the local screen.** This study replaced it as a
  veto for the hosted arms in this question only.

## Data boundary

Pool DEV12, the development series (ES2015, IS1004, TS3009) only. The harness
refuses any other pool and any meeting outside `data.POOL_DEV12`. The
evaluation series, the reserve series and pool F were not touched. Nothing
AMI-derived is committed.

## Deviations from the procedure

None known. The procedure was the two steps registered: step 0, the 28 G1 judge
records with `gpt-oss:20b` after the owner agreed to the GPU lane; step 1, the
`report --pool DEV12` run on cached records, G1 labels and embeddings, with no
`--embed-missing` and no hosted call. Every registered quantity (tau, resample
count and seed, G1 floor, near-duplicate threshold and ceiling, arm list, pool,
verdict order) is as registered. The one gap is reporting, not procedure: the
per-meeting G1 values the registration lists are not in this file (see above).

## What this does not conclude

- **Not "hosted beats local".** It is recall at one operating point against a
  weak reference (`qwen3:8b` covers 0.050 at tau 0.70), under guardrails that
  twelve meetings can pass or fail on width.
- **A veto on width is not a measured loss.** Three arms fail G1 because their
  lower bounds are under -0.05; their point deltas are within a few points of
  zero. Not shown at twelve meetings is not a finding of lower precision.
- **Plain `openai-gpt-6-luna` is not shown to differ**, at a lower bound of
  exactly 0; that is a boundary of the rule, not evidence of no effect.
- **Power was not estimated.** NOT DISTINGUISHABLE and a G1 failure by width
  are both possible for an arm that is in fact better.
- **Cosine coverage is not understanding.** An object near a gold sentence in
  bge-m3 space may paraphrase it, share its topic or only its vocabulary.
- **One gold.** AMI's human abstractive summary is one annotator's account; a
  useful object the summary did not mention counts for nothing.
- **Alias drift.** The hosted records were made on 2026-11-23 and 2026-11-24
  under provider aliases that can move; the study cannot separate that from the
  model.
- **Not ranked.** The rule does not rank arms and this file does not; the
  verdict per arm is the output.
