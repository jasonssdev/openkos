# Pre-registration: a hosted-only `evidence` instruction A/B (#1768, second registration)

**Status: registered on merge to `main`.** No treatment call may be sent
before that merge, and none before the harness fix for the `not_called`
miscount (#1790, PR #1791) has merged too: the runs wait for both. A change
after the merge is a new registration, not an edit to this one. The owner
decisions this file rests on were settled on 2026-11-24, before registration
("Owner decisions" at the end); the two questions raised by the power analysis are under "Questions the
owner settled before merge".

## Question

[`EVIDENCE_RESULTS.md`](EVIDENCE_RESULTS.md) (#1788) decided DO NOT SHIP for
the instruction as a shared prompt: on the local default `qwen3:8b` it doubled
the object volume and raised the unevidenced rate (5 of 50 to 29 of 101). It
also showed what the instruction does on a hosted model: on
`anthropic-claude-haiku-5-5` it cut the unevidenced objects from 156 of 156 to
22 of 154 (delta -0.857 [-0.905, -0.769]), and it failed only the G1 precision
guardrail, on an interval, not on a loss (point -0.0149, lower bound -0.0864
against a floor of -0.05).

This registration asks one narrower question:

> On the hosted arms alone, does asking for a verbatim `evidence` quote fix
> the unevidenced defect without costing precision, coverage, failures or
> volume?

The answer is the basis for a **hosted-only, opt-in** use of the instruction.
It is **not** a mechanism. Whether such a use is a per-backend prompt, a
config key, a spec change or nothing is decided only after the results, with
its own spec and, if warranted, ADR. A SUPPORTED outcome here selects no
mechanism and changes no shipped prompt or default; a NOT SUPPORTED outcome
closes #1768's prompt arm. The local result of #1788 stands untouched: nothing
here measures `qwen3:8b`, and nothing here weakens the local-first,
no-mandatory-cloud principle.

## Treatment (unchanged from #1768)

The control is the shipped `extraction/system` prompt and the treatment is the
registered one of [`EVIDENCE_PREREGISTRATION.md`](EVIDENCE_PREREGISTRATION.md):
the same two splices (`evidence_prompt.treatment_prompt`), 9,019 characters,
sha256 `9dbb82be4ede1522fa83cd7544677735edb374c52ce65dda838aad1d4230e58b`.
`run --prompt-arm evidence` still refuses to send a treatment whose hash is
not that one. There is no reason to change it: the hosted effect it produced
on haiku is the one this study wants to confirm, and editing the text after
seeing a result would make the second registration a search. The re-ask and
participant-capture prompts are not changed, as before. A different prompt is
a different registration.

## Arms

| Arm | Control (cached) | Treatment label | Endpoint |
|---|---|---|---|
| haiku | `anthropic-claude-haiku-5-5`, #1767 (DA) and #1783 (seven new meetings) | `anthropic-claude-haiku-5-5+evidence` | `https://api.anthropic.com`, `claude-haiku-5-5` |
| luna | `openai-gpt-6-luna`, #1767 (DA) and #1783 (seven new meetings) | `openai-gpt-6-luna+evidence` | `https://api.openai.com`, `gpt-6-luna` |

Only these two, by owner decision. `openai-gpt-4.1-mini` is excluded: #1767
found it WORSE than the local default, and in #1768 it supplied a quote on
13 of 30 objects, four of them invented. `openai-gpt-5-mini` is excluded on
cost (its #1767 spend was 0.479 USD, 3.5 times haiku's and 4.7 times luna's). No local
arm is run.

Everything except the pool and the arm list is #1767's and #1768's: runs 1
and 2 (n = 2), the selector judge on, the arm's own model for it, re-ask and
participant capture, `qwen3:8b`'s stage-0 timeouts (`--timeout-from
qwen3:8b`), `max_completion_tokens` 8192, no seed, temperature or reasoning
field (`--no-seed`), prose fixtures not run, the windows hash-checked against
`windows.json` before every run. A treatment is a new record label, so no
control record, cached call or ledger is touched.

## Pool

**Pool DEV12**, the twelve development meetings of three series (ES2015a-d,
IS1004a-d, TS3009a-d), `rcs.sources_for("DEV12")`: pool DA first, then the
seven meetings of #1783. **Pool F, the thesis evaluation series and the
reserve are not touched**, and `hosted_evidence_ab.py` refuses any other pool.

### Why not DA

DA gives comparability with #1768 and cached controls, and it is what made
G1 unresolvable. The G1 guardrail is the one that killed haiku, so power is
estimated for it, from the #1768 data (next section). On DA, a treatment that
cost nothing at all would clear the G1 guardrail about one time in three
(haiku) or one in two (luna). DEV12 raises that to 55% and 73%. It cannot
reach the conventional 80% (see the power section); it is the largest pool
the thesis allows.

### What DEV12 costs, and what it reuses

- **Controls need no new call.** Both hosted controls exist on all twelve
  meetings (pool DA of 2026-11-23, the seven others of 2026-11-24), 24 of 24
  meeting-runs each. The two dates differ; the alias drift this implies is
  disclosed under "Not concluded".
- **Haiku's pool-DA treatment is reused, not re-run.** The ten
  `anthropic-claude-haiku-5-5+evidence` meeting-runs of #1768 stay on disk
  (`run` skips an existing record). Only the seven new meetings are sent.
  Those ten records were seen before this registration, including their G1
  result (point -0.0149); this is the one place the new decision reads data
  that already shaped the hypothesis. It is reported as a stratum apart (see
  Metrics) and the rule below reads the pooled twelve.
- **Luna's treatment is new on all twelve meetings**; no luna treatment
  exists.
- **The G1 judge must run on the seven new meetings for both arms, control
  and treatment**, because no G1 judgement exists for them. It is local
  (`gpt-oss:20b`), free in money; the judge's digest and prompts are checked
  against the stamp as before.
- `IS1004a` (8,289 characters) is a whole-source meeting: no window call, so
  it contributes objects, unevidenced counts and volume but no window lift or
  G1 precision. Its `not_called` windows are not provider failures (#1790,
  see "Completeness").

## Power for the G1 guardrail

**What failed in #1768.** G1 precision is `supported / judged`, window by
window, `gpt-oss:20b` as judge. Haiku's control scored 127 of 134 and the
treatment 125 of 134: a point delta of **-0.0149**, which is inside the
floor. The registered test is the 95% **lower** bound of a two-stage cluster
bootstrap (meetings, then windows) at least -0.05, and the lower bound was
**-0.0864**, 0.0715 below the point. The interval is that wide because five
meetings carry it, and one of them moved: per meeting (control to treatment,
supported over judged) ES2015a 9/9 to 9/9, IS1004b 34/36 to 35/36, TS3009c
27/30 to 29/33, **ES2015d 23/24 to 18/21**, IS1004d 34/35 to 34/35. Four
meetings are flat or up by a point; one lost 0.10. With the control at 0.948,
a bootstrap over five clusters has a lower bound well below the point
whenever one cluster loses. So the registered verdict "G1 fails" meant "not
shown to hold at n = 2", as `EVIDENCE_RESULTS.md` said, not "precision fell".
`gpt-4.1-mini`'s G1 rested on 13 judged objects and is the same kind of
result.

**Estimate.** A model-free simulation of the registered statistic, from the
stored #1768 judge records and no model call. For each arm it builds the
per-window `(supported, judged)` pairs of the control (haiku also of its
treatment), then simulates a study: control and treatment supported counts are
drawn binomially around the arm's own control precision, the treatment shifted
by a true delta `d`, and the **registered** bootstrap (meetings with
replacement, then windows; 1,000 resamples; seed 7) gives the 95% lower
bound. 1,000 simulated studies per cell, so each probability is good to about
+/-0.03. DA keeps its five meetings; DEV12 keeps the five and adds six
windowed meetings drawn with replacement from them (a stand-in: the seven new
meetings have no G1 judgement, and IS1004a has no windows). Treatment and
control are drawn independently, which is conservative. This is a planning
estimate under assumptions, not a measurement; the simulation script is not
committed.

Probability that the registered G1 guardrail holds (lower bound at least
-0.05), by the true precision delta `d`:

| Pool | Arm | d = 0 | d = -0.015 (haiku's #1768 point) | d = -0.03 | d = -0.05 (the floor) |
|---|---|---|---|---|---|
| DA (5 meetings) | haiku | 0.31 | 0.15 | 0.05 | 0.01 |
| DA | luna | 0.48 | 0.23 | 0.09 | 0.03 |
| **DEV12** | haiku | **0.55** | 0.23 | 0.07 | 0.01 |
| **DEV12** | luna | **0.73** | 0.33 | 0.10 | 0.01 |
| DEV12, haiku's five observed DA meetings fixed at their #1768 numbers | haiku | 0.60 | 0.38 | 0.18 | 0.06 |

Median bootstrap half-width (point minus lower bound), same cells at
`d = 0`: DA haiku 0.067, DA luna 0.052, DEV12 haiku 0.047, DEV12 luna 0.038.

Reading it:

1. **The guardrail is specific but not sensitive.** If the instruction truly
   costs 0.05 of precision, DEV12 flags it 99% of the time (1% pass). If it
   costs nothing, haiku clears it 55% of the time and luna 73%; if it costs
   what haiku showed in #1768 (0.015), 23% and 33%. Expect G1 to fail on
   interval width alone, again, about half the time for an arm that is
   harmless.
2. **Doubling the pool is the most that is available.** Eighty percent power
   at `d = 0` needs a half-width of about 0.035 (SE about 0.018). DEV12 gets
   haiku to 0.047 and luna to 0.038; haiku would need about 20 windowed
   meetings, luna about 13, and only 11 windowed development meetings exist
   outside the forbidden series. More runs add items inside the same meetings
   and were not simulated; the interval is driven by the meetings.
3. **At least one of the two arms** clears G1 with probability about 0.88 if
   both are harmless, about 0.49 if both lose 0.015, about 0.64 and 0.34 on
   DA. The other guardrails and the unevidenced bound must also hold, so
   these are ceilings on the chance of SUPPORTED, not forecasts of it.

**Decision: pool DEV12.** Same procedure as #1768, same statistic, same
guardrail value; twice the data.

## The guardrail stays at -0.05

G1's floor is **-0.05**, unchanged from #1557 and #1768, applied to the 95%
lower bound of the pooled precision delta against the arm's own control
(`score --reference <control>`). It is not weakened here. The interval
result above shows the rule is hard to pass for a harmless instruction;
that is a reason to read a failure carefully (the results will report the
point and the per-meeting deltas next to the bound), not a reason to move the
bar after seeing the data. Whether to register a different bar (a wider
margin, or a point-estimate condition) is a design question, recorded under
"Questions the owner settled before merge" with the numbers; the owner kept
-0.05 (owner decision 6), so no other bar is registered here.

## Metrics

Per arm, treatment against its own control, pooled over the two runs and
the twelve meetings, as in `EVIDENCE_PREREGISTRATION.md`:

- **Primary: unevidenced rate**, n of TOTAL, delta treatment minus control with
  the meeting-level bootstrap (`hosted_screen.rate_delta`: meetings drawn
  with replacement, the same draw for both arms, 10,000 resamples, seed 42).
  The controls are at 369 of 371 (haiku) and 249 of 249 (luna), so there is
  room for a large effect and the bound is not close to a ceiling.
- **Verified-quote share** and **fabricated-quote share** (`not_in_source`,
  also over the objects that supplied the field; `too_short` counted apart),
  as registered in #1768.
- **Reported, not decided on:** window outcomes by cause, calls, tokens and
  spend, wall time, judged-out counts, objects added by re-ask and
  participant capture, and **two strata**: the five pool-DA meetings
  (comparable with #1768; haiku's are the same records) and the seven new
  meetings (data nobody has seen), each with its own unevidenced delta and G1
  delta. The strata do not decide anything; they let a reader see whether the
  pooled result depends on the five meetings already seen.

## Guardrails (each against the arm's own control)

Identical to #1768, and implemented by the same `evidence_ab.guardrail_failures`:

- **Window lift non-inferiority:** lower bound of the paired window lift
  delta (two-stage bootstrap, 10,000 resamples, seed 42) at least -0.03.
- **G1 precision:** lower bound at least **-0.05**.
- **V1 failures:** treatment failed-window rate at most the control's + 0.02
  and at most 0.05 absolute.
- **V4 volume, as registered:** objects per meeting-run within [0.5, 1.5] x
  the arm's own control. #1783 tried to replace V4 and came out
  INCONCLUSIVE (kappa 0.309), so V4 stands. On the control's 24 meeting-runs
  the arm's own volume is haiku 15.46 (window 7.73 to 23.19) and luna 10.38
  (5.19 to 15.56), to be read from the score file, not from this line.
- **V5 verbatim copying:** at most the control's rate + 0.10.
- V3 is not evaluated: no prose fixtures are run.

## Completeness

As #1767 and #1768: a provider failure is not a result about the model.

- **Provider failure:** a hosted window that ended in `error`, or in
  `not_called` **on a chunked record** (one where a window call was expected).
  A record that does not say whether it was chunked stays fail-closed. Not
  cached; delete the affected meeting-run records and run again (cached calls
  replay free), at most **two** such re-runs per arm.
- **The `IS1004a` miscount.** `IS1004a` is extracted in one whole-source call,
  so its three windows read `not_called` although nothing failed; the
  harness counted that as provider failure and #1783's `population` flagged
  all arms INCOMPLETE. That is fixed separately (#1790, PR #1791). Both PRs
  must merge before any run, and the report this registration adds
  (`hosted_evidence_ab.py`) applies the same rule on its own
  (`provider_failures`), so it does not depend on that merge for its answer.
- **Model failure:** `timeout`, `capped`, `unparseable`. Cached, counted in
  V1, not re-sent.
- If the first 10 calls of an arm all end in provider failure, stop the arm
  and report it **UNAVAILABLE on <date>**.
- An arm is **INCOMPLETE** if, after the re-runs, a provider-failure window
  remains, a spend or call ceiling stopped the run (meeting-runs missing), a
  control or treatment record lacks the per-object flags, or the score file is
  not for pool DEV12. Its numbers are reported and flagged; it cannot
  qualify.

## Outcome rule (fixed before any treatment call)

Applied by `hosted_evidence_ab.py report` (`decide`) to the stored records and
scores; not re-tuned after the data.

An arm **qualifies** if it is complete, **every guardrail above holds**, and
the unevidenced delta's 95% upper bound is at most **-0.10** (the interval
excludes a reduction smaller than 0.10).

- **SUPPORTED** if at least one arm qualifies. The outcome names the
  qualifying arms; a follow-up may cover only those. It is the basis for a
  hosted-only opt-in design and nothing more.
- **INCOMPLETE** if no arm qualifies and at least one arm is incomplete (it
  could still have).
- **NOT SUPPORTED** otherwise. The result records why, per arm: which
  guardrail failed (with the point, the interval and the per-meeting values),
  or which bound missed -0.10.

An arm that is complete but fails a guardrail is named whether or not the
outcome is SUPPORTED. SUPPORTED does not ship, enable or default anything.

## Cost forecast and ceilings

Total budget: **under USD 1.00** of new spend, by owner decision, per-arm
ceilings below. Prices as confirmed in #1767 and used in #1768 (2026-11-23):
both arms **$0.10 in / $0.50 out per 1M tokens**, standard tier, no caching or
batch discount; **re-confirm them on the provider pages before the run** and
set `--price-in` and `--price-out` from what is read.

Derivation, from the ledgers:

- **Haiku.** Only the seven new meetings (14 meeting-runs) are sent. The
  same meetings cost USD 0.1955 and 160 calls as the control in #1783
  (859,582 prompt, 219,153 completion tokens). #1768's treatment on DA cost
  0.134 against its control's 0.139 (ratio 0.96, completion tokens 159,173
  against 156,180). Central 0.19; high 0.29 (x1.5, the margin #1768's
  forecast used for the output of a longer reply). New calls about 146 (the
  participant-capture calls replay from the control's cache).
- **Luna.** All twelve meetings, 24 meeting-runs, no reusable treatment. The
  control ledger is 272 calls and USD 0.2400 (960,738 prompt, 287,942
  completion tokens). Treatment: input +5%, output +20% for central (0.27);
  input +10%, output x2 for high (0.39), since luna is a reasoning arm and its
  billed output is the uncertain part. Calls about 248 (the participant-capture
  calls replay).
- Every treatment call is new (the system prompt differs); nothing is assumed
  to replay except the participant-capture calls and haiku's existing ten
  records.

| Arm | Control ledger (USD) | Central new | High new | `--max-usd` | `--max-calls` | New-spend ceiling |
|---|---|---|---|---|---|---|
| `anthropic-claude-haiku-5-5+evidence` | 0.334 (DEV12; 0.195 of it the seven new meetings) | 0.19 | 0.29 | **0.50** | **300** | **0.366** |
| `openai-gpt-6-luna+evidence` | 0.240 | 0.27 | 0.39 | **0.55** | **330** | **0.55** |
| **Total** | | **0.46** | **0.68** | | | **0.916** |

**The spend ledger is cumulative and persists across runs.** Haiku's
`+evidence` ledger already stands at 102 calls and USD 0.1338 from #1768, so
its flags are the ceiling on the cumulative figure: `--max-usd 0.50` and
`--max-calls 300` leave 0.366 USD and 198 calls of new spend, which is what
the "New-spend ceiling" column counts. Luna's ledger starts at zero. The two
new-spend ceilings add to 0.916, under the 1.00 budget, and the central
forecast is under half of it. A re-run after a provider failure continues the
same ledger, so it is inside the ceiling, not on top of it. A run that hits a
ceiling stops and the arm is INCOMPLETE; nothing is raised without a new
registration.

Local cost, free in money, on the owner's GPU (authorized): the `gpt-oss:20b`
G1 judge over the four labels (control and treatment per arm), which judges
only what is missing: the seven new meetings for three labels and all twelve
for luna's treatment (the pool-DA judgements of the two controls and of
haiku's treatment are cached), and `bge-m3` embeddings for scoring. Not timed
here; no `qwen3:8b` extraction.

## Procedure

The registration head's worktree needs the four gitignored
`examples/extraction-corpus/sources` files copied in (`run` refuses to start
without 5 of 5, though no prose is sent); they are never committed. Keys are
read only from `OPENKOS_OPENAI_API_KEY`, exported for one hosted arm at a time
as in #1767 and #1768, and never written, echoed or logged.

```sh
S=evals/concept_model_screen
# 0. Controls: cache-only replay of all twelve meetings, no model call
#    (adds final_evidence to the 2026-11-23 controls).
OLLAMA_HOST=http://127.0.0.1:9 uv run python $S/hosted_screen.py replay \
  --models anthropic-claude-haiku-5-5 openai-gpt-6-luna --runs 1 2 \
  --timeout-from qwen3:8b --pool DEV12
# 1. Hosted treatments, one at a time. Haiku skips its ten existing
#    meeting-runs and sends the seven new meetings; its ledger is cumulative.
uv run python $S/run_concept_screen.py run --models anthropic-claude-haiku-5-5 \
  --pool DEV12 --runs 1 2 --prompt-arm evidence \
  --backend openai-compatible --base-url https://api.anthropic.com \
  --served-model claude-haiku-5-5 --digest anthropic:claude-haiku-5-5:<date> \
  --timeout-from qwen3:8b --no-seed \
  --max-calls 300 --max-usd 0.50 --price-in 0.10 --price-out 0.50
uv run python $S/run_concept_screen.py run --models openai-gpt-6-luna \
  --pool DEV12 --runs 1 2 --prompt-arm evidence \
  --backend openai-compatible --base-url https://api.openai.com \
  --served-model gpt-6-luna --digest openai:gpt-6-luna:<date> \
  --timeout-from qwen3:8b --no-seed \
  --max-calls 330 --max-usd 0.55 --price-in 0.10 --price-out 0.50
# 2. G1 judge: four labels; existing judge records are skipped.
uv run python $S/run_concept_screen.py judge --pool DEV12 --runs 1 2 \
  --models anthropic-claude-haiku-5-5 anthropic-claude-haiku-5-5+evidence \
  openai-gpt-6-luna openai-gpt-6-luna+evidence
# 3. One score per pair, paired against that pair's control.
for c in anthropic-claude-haiku-5-5 openai-gpt-6-luna; do
  uv run python $S/run_concept_screen.py score \
    --name "evidence-hosted-ab-${c}" --models "$c" "$c+evidence" \
    --reference "$c" --pool DEV12 --runs 1 2 --g1 --prose-runs
done
# 4. The registered metrics, guardrails and outcome.
uv run python $S/hosted_evidence_ab.py report --runs 1 2
```

The results go to `HOSTED_EVIDENCE_RESULTS.md`, with every deviation from
this procedure listed, the two strata, and the per-meeting G1 values next to
each bound.

## Not concluded

- **Power.** Even on DEV12 a harmless arm fails G1 on interval width roughly
  half the time (haiku) to a quarter of the time (luna). A NOT SUPPORTED
  caused by G1 alone means "not shown to hold at 11 windowed meetings", not
  "precision fell". The simulation is a planning estimate that assumes
  independent arms, binomial item noise and that the seven new meetings look
  like the five seen ones.
- **Seen data.** Haiku's five pool-DA meetings, with their treatment and G1
  result, are already known; the strata are the guard against reading the
  pooled number as fresh evidence.
- **Alias drift.** Controls were run on 2026-11-23 (DA) and 2026-11-24 (the
  seven new meetings); haiku's DA treatment on 2026-11-23, its new ones and
  all of luna's on the run date. A provider alias can move under a fixed
  name; part of any delta can be the provider, not the instruction, and the
  study cannot separate them. Digests carry each date.
- **Luna has no treatment data.** Its unevidenced reduction, fabricated-quote
  share and volume under the instruction are unknown; its G1 and V4 are
  forecast only from its control. A reasoning arm's result mixes the model
  with its default effort.
- **A verified quote proves a source line was copied, not that it supports the
  object.** G1 guards support, with the width above; the quote's relevance is
  not judged.
- **Not the local default.** Nothing about `qwen3:8b`, private or non-English
  sources (no prose fixtures, V3 not evaluated) or the pipeline's other tasks.
- The selector judge runs on each arm's own model and reads the longer
  replies; its interaction with the instruction (#1571) is not separated. The
  re-ask and participant-capture prompts do not ask for the field.

## Questions the owner settled before merge

Both were put to the owner with the numbers below and settled on 2026-11-24,
before merge, as recommended (owner decisions 6 and 7).

1. **G1's bar.** Kept at -0.05 as registered. The numbers above say a
   harmless arm passes it 55% (haiku) and 73% (luna) of the time on DEV12.
   For information only, the same simulation with the floor at -0.075 gives,
   for `d = 0`, -0.015, -0.03 and -0.05: haiku 0.90, 0.65, 0.34, 0.08 and
   luna 0.97, 0.76, 0.43, 0.12; at -0.10: haiku 0.99, 0.92, 0.74, 0.35 and
   luna 1.00, 0.96, 0.80, 0.45. Moving the bar is a change to the registered
   rule and should be a deliberate decision made before a call is sent, not a
   response to the result. The recommendation is to keep -0.05 and read a G1
   failure with its per-meeting values; the alternative is to accept that the
   guardrail will probably decide this study by width.
2. **Haiku's reused DA treatment.** Reusing the ten #1768 records saves about
   0.14 USD and keeps the DA stratum comparable, at the cost of mixing data
   that shaped the hypothesis into the pooled test. The harness cannot re-send
   them (a re-send replays the same cache), so the alternative is to decide
   on the seven new meetings alone, which nobody has seen, at a lower power
   that was not simulated (six windowed meetings instead of eleven). Recommended:
   reuse, with the two strata reported.

## Owner decisions

Settled on 2026-11-24, before registration.

1. **Arms:** `anthropic-claude-haiku-5-5` and `openai-gpt-6-luna` only.
   `openai-gpt-4.1-mini` is excluded (WORSE in #1767); `openai-gpt-5-mini` is
   excluded on cost.
2. **Budget:** under USD 1.00 in total, per-arm ceilings derived from the
   #1767 and #1768 ledgers (above).
3. **Local GPU:** used for the G1 judge (`gpt-oss:20b`) and embeddings.
4. **Pool:** forbidden are pool F and the thesis evaluation and reserve
   series; DA or DEV12 delegated to this registration's power analysis, which
   chose DEV12.
5. **Product mechanism:** decided only after the results.
6. **G1's bar stays at -0.05**, accepting that a harmless arm fails it by
   width about half the time on haiku; a G1 failure is read with its
   per-meeting values.
7. **Haiku's ten #1768 DA treatment records are reused**, with the DA stratum
   and the seven new meetings reported apart.

## Disclosed before registration

- The control records, their cached replies and #1768's stored aggregates and
  judge records were read while preparing this file. The #1768 haiku
  treatment records (pool DA) and their G1 labels were read for the power
  estimate. No luna treatment output exists.
- The cache-only replay of step 0 was run for both controls over DEV12, with
  Ollama's host poisoned and every call answered from the chat cache: 24 of
  24 meeting-runs per arm, no mismatch, no record without the per-object
  flags, and no model, local or hosted, called, no key used. It wrote the
  replayed records under `hosted/replay/` in the runs directory, outside the
  repository. Controls over the twelve meetings: haiku 369 of 371 final
  objects unevidenced, luna 249 of 249.
- The G1 power simulation was run on the stored judge records and calls no
  model. It is not committed.
- Harness support added with this registration, each with a model-free
  self-test: `hosted_evidence_ab.py` (`report` for the two hosted pairs over
  DEV12, the outcome rule, `provider_failures` with #1790's rule, the
  registered treatment hash) and `hosted_screen.py replay --pool` (default DA,
  unchanged behaviour). `evidence_ab.py` is not changed, so the #1768 report
  and its self-test keep their meaning.
