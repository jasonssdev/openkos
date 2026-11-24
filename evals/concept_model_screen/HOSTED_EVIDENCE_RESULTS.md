# Results: a hosted-only `evidence` instruction A/B (#1768, second registration)

**Outcome: SUPPORTED, by both arms.** Both `anthropic-claude-haiku-5-5+evidence`
and `openai-gpt-6-luna+evidence` are complete, hold every guardrail against
their own control, and have an unevidenced-delta upper bound far below -0.10.
This is the basis for a hosted-only, opt-in design and nothing more: it selects
no mechanism, ships nothing, enables nothing and changes no default or shipped
prompt. The local result of [`EVIDENCE_RESULTS.md`](EVIDENCE_RESULTS.md) (#1788:
DO NOT SHIP on `qwen3:8b`) is untouched; nothing here measures `qwen3:8b`.

Rules: [`HOSTED_EVIDENCE_PREREGISTRATION.md`](HOSTED_EVIDENCE_PREREGISTRATION.md),
merged in #1792 (`8b91a488`), with the `not_called` harness fix (#1791) merged,
before any treatment call. The rule was applied by `hosted_evidence_ab.py
report` to the stored records and scores, not re-tuned. Pool DEV12 (twelve
development meetings), runs 1 and 2 (n = 2), 24 meeting-runs per label. The
treatment is the #1768 prompt (sha256 `9dbb82be...e58b`, enforced by `run`).
Treatments ran on 2026-11-24 (haiku's ten pool-DA meeting-runs are the #1768
records of 2026-11-23, reused as registered). Controls are the cached #1767
(pool DA) and #1783 (seven new meetings) records.

Neither arm is INCOMPLETE: 24 of 24 meeting-runs per arm, 0 provider-failure
windows under #1790's rule, no ceiling stopped a run, every record carries the
per-object flags, the score files are for pool DEV12. No re-run was needed. No
arm is UNAVAILABLE.

## Decision

| Arm | Unevidenced delta [95%] | Upper bound vs -0.10 | Guardrails | Verdict for the arm |
|---|---|---|---|---|
| `anthropic-claude-haiku-5-5+evidence` | **-0.8437 [-0.8858, -0.7805]** | -0.7805, holds | all five hold | Qualifies |
| `openai-gpt-6-luna+evidence` | **-0.7806 [-0.8174, -0.7455]** | -0.7455, holds | all five hold | Qualifies |

`decide` returns `SUPPORTED`, `qualifying` = both arms, no notes. A follow-up
may cover either arm.

## The primary metric and the quote shares

Final objects, pooled over the two runs and twelve meetings, n of TOTAL.
Delta = treatment minus control, meeting-level bootstrap (twelve meetings with
replacement, same draw for both arms, 10,000 resamples, seed 42).

| Arm | Unevidenced control | Unevidenced treatment | Delta [95%] | Verified quote | `not_in_source` | `too_short` | Supplied the field |
|---|---|---|---|---|---|---|---|
| haiku | 369 of 371 (0.995) | 56 of 371 (0.151) | -0.8437 [-0.8858, -0.7805] | 315 of 371 (0.849) | 3 of 371 (0.008) | 1 | 319 of 371 (0.860) |
| luna | 249 of 249 (1.000) | 61 of 278 (0.219) | -0.7806 [-0.8174, -0.7455] | 217 of 278 (0.781) | 14 of 278 (0.050) | 2 | 233 of 278 (0.838) |

- Every control supplied 0 quotes (the prompt never asked). `too_short` is
  derived as supplied minus verified minus `not_in_source`.
- Fabricated (`not_in_source`) share over the objects that supplied the
  field: **haiku 3 of 319 (0.009), luna 14 of 233 (0.060)**. Luna invents a
  quote about six times as often as haiku. The rule does not read this
  number; it is reported because "quotes a line that is not in the source" is
  the failure the field exists to remove, and 6% is not negligible.
- The unevidenced rate falls from about 1.0 to 0.15 (haiku) and 0.22 (luna),
  but not to zero: 56 and 61 final objects still carry no verified quote.
- The effect does not come with a volume change on haiku (371 final objects in
  control and in treatment) and is mild on luna (249 to 278).

## Guardrails (each against the arm's own control)

| Arm | Window lift delta [95%] (need lower >= -0.03) | G1 precision (control, treatment) | G1 delta [95%] (need lower >= -0.05) | V1 failed windows (control, treatment; need <= control + 0.02 and <= 0.05) | V4 objects per meeting-run (control, treatment; need 0.5x to 1.5x) | V5 verbatim (need <= control + 0.10) |
|---|---|---|---|---|---|---|
| haiku | -0.0069 [-0.0198, +0.0060] holds | 285 of 309, 292 of 311 | **+0.0166 [-0.0368, +0.0696]** holds | 6 of 202, 6 of 202 (0.030) holds | 15.46, 15.46 holds | 1 of 371, 0 of 371 holds |
| luna | +0.0104 [-0.0067, +0.0293] holds | 187 of 193, 221 of 227 | **+0.0047 [-0.0391, +0.0482]** holds | 6 of 202, 6 of 202 (0.030) holds | 10.38, 11.58 holds | 0 of 249, 1 of 278 holds |

- V3 is not evaluated: no prose fixtures are run.
- G1 passed on both arms; the points are positive. Registered power (55% for
  haiku, 73% for luna at a true delta of 0) made a failure by width about as
  likely as a pass for a harmless arm, so a pass is the more informative
  outcome here, but the lower bounds (-0.0368 and -0.0391) are 0.013 and 0.011
  above the floor, not far above it.
- V1: the 6 failed windows are the three windows of each `IS1004a` meeting-run
  (two runs), a whole-source meeting with no window call. They read `not_called`
  in all four labels, control and treatment alike. They are not provider
  failures (#1790) and not model failures; the failed-window rate is
  identical in each pair, so the V1 comparison is unaffected. The V4 windows
  are each arm's own: haiku 7.73 to 23.19, luna 5.19 to 15.56 (0.5x to 1.5x
  of the control's 15.46 and 10.38).
- Window outcomes by cause, all four labels: 196 `ok`, 6 `not_called`
  (`IS1004a`), no `error`, `timeout`, `capped` or `unparseable` window.

## G1 per meeting (owner decision 6)

Supported over judged windows' objects, control to treatment, runs 1 and 2
pooled. `IS1004a` has no windows, so no G1.

| Meeting | haiku | luna |
|---|---|---|
| ES2015a (DA) | 9/9 to 9/9 | 8/8 to 10/10 |
| IS1004b (DA) | 34/36 to 35/36 | 21/21 to 23/23 |
| TS3009c (DA) | 27/30 to 29/33 | 20/21 to 21/23 |
| ES2015d (DA) | 23/24 to **18/21** | 19/20 to 17/19 |
| IS1004d (DA) | 34/35 to 34/35 | 20/20 to 32/32 |
| ES2015b | 29/32 to 29/31 | 17/17 to 20/20 |
| ES2015c | 27/30 to 27/27 | 18/19 to 22/23 |
| IS1004c | 27/32 to 29/29 | 14/16 to 24/25 |
| TS3009a | 23/23 to 26/26 | 11/11 to 15/15 |
| TS3009b | 30/32 to 35/38 | 23/23 to 19/19 |
| TS3009d | 22/26 to 21/26 | 16/17 to 18/18 |

- Haiku: seven of eleven windowed meetings are flat or better; four are lower:
  `TS3009c` (0.900 to 0.879), `TS3009b` (0.938 to 0.921), `TS3009d` (0.846 to
  0.808) and `ES2015d` (0.958 to 0.857). `ES2015d` is the meeting that carried
  the failed bound in #1768; it is the same record and the same number here,
  now diluted by six more windowed meetings.
- Luna: nine of eleven are flat or better; `ES2015d` (0.950 to 0.895) and
  `TS3009c` (0.952 to 0.913) are lower, by one and two objects.
- `ES2015d` and `TS3009c` are the two meetings where both arms lost precision.

## The two strata (owner decision 7)

Reported apart, decide nothing. The five pool-DA meetings are the ones
#1768 already showed (haiku's treatment records there are the same records);
the seven new meetings are data nobody had seen when the rule was fixed.

| Stratum | Arm | Unevidenced control to treatment | Delta [95%] | G1 control to treatment | G1 delta [95%] |
|---|---|---|---|---|---|
| DA (5) | haiku | 156 of 156 to 22 of 154 | -0.8571 [-0.9045, -0.7686] | 127/134 to 125/134 | -0.0149 [-0.0864, +0.0363] |
| New (7) | haiku | 213 of 215 to 34 of 217 | -0.8340 [-0.8919, -0.7371] | 158/175 to 167/177 | +0.0406 [-0.0319, +0.1200] |
| DA (5) | luna | 116 of 116 to 28 of 125 | -0.7760 [-0.8214, -0.7327] | 88/90 to 103/107 | -0.0152 [-0.0912, +0.0337] |
| New (7) | luna | 133 of 133 to 33 of 153 | -0.7843 [-0.8442, -0.7330] | 99/103 to 118/120 | +0.0222 [-0.0256, +0.0906] |

- Haiku's DA stratum reproduces #1768 exactly (-0.8571, G1 -0.0149
  [-0.0864, +0.0363]), as it must.
- The effect on the unevidenced rate is the same size on the data nobody had
  seen (-0.834 and -0.784) as on the DA meetings (-0.857 and -0.776); the
  pooled result does not depend on the five seen meetings.
- G1 is where the strata differ: on DA alone both arms' lower bounds are below
  -0.05 (-0.0864 and -0.0912), on the seven new meetings both are positive
  with lower bounds near -0.03. The pooled pass therefore leans on the new
  meetings. Each stratum interval is on 5 or 6 windowed meetings and is wide.
  The stratum intervals use the same two-stage bootstrap as the registered
  statistic, computed from the stored judge records (see Deviations).

## Calls and spend

The ledger is cumulative and persists; haiku's `+evidence` ledger stood at
102 calls and USD 0.1338 from #1768 before this run (prompt 542,054,
completion 159,173 tokens), luna's at zero. Spend is the harness ledger at the
registered prices (0.10 in / 0.50 out per 1M tokens).

| Arm | Calls cumulative (ceiling) | New calls | Prompt / completion tokens, new | Spend cumulative USD (ceiling) | New spend USD (new-spend ceiling) | Forecast central / high new | Control ledger USD |
|---|---|---|---|---|---|---|---|
| haiku `+evidence` | 248 (300) | 146 | 770,591 / 218,440 | 0.3201 (0.50) | **0.1863** (0.366) | 0.19 / 0.29 | 0.3342 |
| luna `+evidence` | 258 (330) | 258 | 916,833 / 367,597 | 0.2755 (0.55) | **0.2755** (0.55) | 0.27 / 0.39 | 0.2400 |
| **Total** | | 404 | | | **0.4618** (0.916) | 0.46 / 0.68 | |

- New spend is 0.462 USD, 46% of the 1.00 budget and 50% of the summed
  new-spend ceilings, on the central forecast for both arms (haiku 0.1863
  against 0.19, luna 0.2755 against 0.27). Calls: haiku 146 as forecast
  (about 146), luna 258 against about 248.
- No ceiling was reached. The 14 haiku meeting-runs of the seven new meetings
  cost 0.1863 against 0.1955 for the same meetings as the control in #1783.
- Prices were not re-read on the provider pages (Deviations, 1).

## Latency

Recorded meeting-run wall time (the pipeline's own timer, hosted calls plus
local steps).

| Arm | Control total (mean per meeting-run) | Treatment total (mean per meeting-run) | Median window call, control to treatment |
|---|---|---|---|
| haiku | 2,093 s (87.2 s) | 1,868 s (77.8 s) | 8.53 s to 8.02 s |
| luna | 2,721 s (113.4 s) | 3,115 s (129.8 s) | 10.76 s to 12.58 s |

- Haiku's treatment total mixes the ten reused 2026-11-23 meeting-runs (776 s)
  with the 14 new ones (1,093 s, 78 s each); its controls were run on two
  different dates too. Luna's treatment is 14% slower per meeting-run and
  17% slower per window call, with 12% more objects. These are one run each on
  a shared network and are not decided on.
- Local judge: about 1 h 25 min of `gpt-oss:20b` over the missing records
  (the pool-DA judgements of the controls and of haiku's treatment were
  cached). No model other than the judge and `bge-m3` ran locally.

## Other counters (reported, not decided on)

| Counter | haiku control, treatment | luna control, treatment |
|---|---|---|
| Judged out by the selector | 140, 138 | 242, 224 |
| Dropped before the judge | 53, 53 | 64, 88 |
| Wrong-language dropped | 0, 0 | 39, 9 |
| Added by participant capture | 33, 33 | 27, 19 |
| Added by re-ask | 0, 0 | 0, 0 |
| Window lift, window mean | +0.0801, +0.0732 | +0.0481, +0.0585 |

Luna's treatment dropped 30 fewer wrong-language objects (39 to 9) and
dropped 24 more before the judge (64 to 88); this study does not explain either.

## Deviations from the procedure

None changes a rule, a number the rule reads or the outcome.

1. **Prices not re-confirmed on the provider pages.** The registration asks
   for it; the runner did not open the pages and used the registered prices
   (0.10 / 0.50 per 1M tokens for both), confirmed in #1767 on 2026-11-23. The
   ledger USD figures are only as good as those prices; the call and token
   counts do not depend on them.
2. **Digest date.** `--digest` carries `2026-11-24` for both arms. Haiku's ten
   reused records carry `2026-11-23`; `run` skipped them and did not rewrite
   their digest.
3. **Sequential arms and one judge process.** Haiku, then luna, then a single
   `judge` over the four labels, then the two `score` calls and `report`, each
   started after the previous finished; the registration did not say they could
   overlap.
4. **Stratum numbers and per-meeting G1 come from an analysis script that is
   not committed.** The registered `report` prints the pooled numbers and not
   the strata or the per-meeting G1 the registration asks to report; the runner
   computed them from the stored records and judge records by calling the
   harness's own `hosted_screen.rate_delta`, `screen_metrics.two_stage_bootstrap`
   and `evidence_ab.evidence_counts`, with the registered resamples and seed.
   The pooled and DA-stratum haiku values reproduce the report and #1768 to
   four decimals.
5. **Gitignored fixtures copied into the worktree** (the four ignored files
   under `examples/extraction-corpus/sources`), as the procedure says; none is
   committed.
6. **Step 0 replay re-run** as written, cache-only with `OLLAMA_HOST` poisoned;
   24 of 24 meeting-runs per arm, no model call, no key used.
7. **No re-run after a provider failure was needed**, so the two permitted
   re-runs per arm were not used.

## Not concluded

- **A mechanism or a default.** SUPPORTED means "worth designing a hosted-only,
  opt-in use". Whether that is a per-backend prompt, a config key, a spec
  change or nothing is decided after these results, with its own spec and, if
  warranted, ADR. Nothing ships.
- **Anything about `qwen3:8b`**, the local-first default, private or non-English
  sources (no prose fixtures, V3 not evaluated), or the pipeline's other tasks.
- **That G1 is safe.** It passed on both arms by lower bounds 0.011 and 0.013
  above the floor, on 11 windowed meetings; the registered power was 55% and
  73% for a harmless instruction, and the two DA-only intervals fail. A
  different pool could read differently. Precision on the meetings that
  already lost (`ES2015d`) did not recover.
- **That quotes are trustworthy for luna.** 6% of the quotes luna supplied are
  not in the source, against under 1% for haiku. The unevidenced metric counts
  verified quotes, so those quotes do not lower it, but they are the failure a
  quote exists to prevent.
- **A verified quote proves a source line was copied, not that it supports the
  object.** G1 guards support; the quote's relevance is not judged.
- **Alias drift.** Controls were run on 2026-11-23 (DA) and 2026-11-24 (the
  seven new meetings); haiku's DA treatment on 2026-11-23, its new ones and
  all of luna's on 2026-11-24. Luna's DA treatment against a control run a day
  earlier mixes any provider change with the instruction; the study cannot
  separate them.
- **Seen data.** Haiku's five pool-DA meetings, with their treatment and G1
  result, shaped the hypothesis; the strata above are the guard, and the effect
  on the unevidenced rate reproduces on the seven unseen meetings.
- **The selector judge's interaction with the instruction** (#1571): it ran on
  each arm's own model and read the longer replies; this study does not
  separate the two. The re-ask and participant-capture prompts do not ask for
  the field, so the 33 and 19 objects participant capture added are not
  covered by the quote.
- **Reasoning budgets and sampling** are provider defaults; luna is a
  reasoning arm and its result mixes the model with its default effort.
- **The latency comparison**: one run per label, two dates for haiku.
- #1768 stays open: the mechanism decision lands in its own change.
