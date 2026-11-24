# Pre-registration: are the vetoed hosted arms' extra objects useful? (#1783)

**Status: registered on merge to `main`.** No judge call may be sent before
that merge. A change after the merge is a new registration, not an edit to
this one. The owner settled the design on 2026-11-23 ("Owner decisions" at
the end). Where this file departs from the first approved design or adds to
it, the reason is given under "Design adjustments".

## Question

The hosted-provider screen (#1767, [`HOSTED_RESULTS.md`](HOSTED_RESULTS.md))
vetoed `openai-gpt-5-mini`, `anthropic-claude-haiku-5-5` and
`openai-gpt-6-luna` on V4 volume: 9.90, 15.60 and 11.60 final objects per
meeting-run against a window of [2.50, 7.50]. That window is [0.5, 1.5] x the
reference's own volume (`qwen3:8b`, 5.00), carried over from #1557
([`PREREGISTRATION.md`](PREREGISTRATION.md), "V4 volume"), where it guarded
against small local models emitting noise. It measures resemblance to the
reference, so it cannot tell extra noise from extra recall.

This study asks whether the objects these arms extract that the reference
did not are worth keeping, and fixes a replacement for V4 before any label
exists. It is a **measurement**. It adopts nothing, changes no default and no
prompt.

#1767 ran on pool DA alone: five meetings, which the power check below shows
is too few to resolve the rule. The owner therefore expanded the study to
**all twelve thesis development meetings** (pool DEV12: ES2015a-d, IS1004a-d,
TS3009a-d; thesis ADR-0005, the development/evaluation split). Pool DA's
records are reused from the cache. The seven other meetings need **new
extraction runs**, local and hosted, registered under "Extraction runs"
below. Pool F, the thesis evaluation series and the reserve are not touched:
pool F stays reserved as the confirmation pool, and the screen's allowlist
refuses the evaluation series.

## Population

- **Meetings:** pool DEV12 (`screen_data.POOL_DEV12`): pool DA (ES2015a,
  IS1004b, TS3009c, ES2015d, IS1004d) unchanged, then the seven new ones
  (ES2015b, ES2015c, IS1004a, IS1004c, TS3009a, TS3009b, TS3009d). Runs 1
  and 2.
- **Arms:** the three V4-vetoed arms above. Pool DA: their #1767 records.
  The seven new meetings: the new runs below.
- **Objects:** each record's final objects (`stages.final`). An object
  without a source window (`window` is null: participant capture, the
  meeting Event, the re-ask) is excluded and counted, because the judge must
  see the source window and these have none.
- **Counterpart rule (fixed before any label):** an arm object has a
  counterpart when the bge-m3 cosine of its `title: description` against any
  `qwen3:8b` final object of the **same meeting and run index** is at least
  **0.85**. Objects with a counterpart are excluded. 0.85 is the screen's
  registered same-subject cosine (`cut_redundancy.py`, and the #1767
  near-duplicate rate); 0.80 and 0.90 are reported for sensitivity only.
  Vectors are read from the screen's embedding cache, opened read-only, under
  bge-m3 digest `7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab`;
  a missing vector stops the build, nothing is embedded. The new meetings'
  objects are embedded before `population` runs (see "Procedure"); that
  embedding is the screen's own `EmbedStore` on the local bge-m3.
- **Control:** every windowed final object of `qwen3:8b` in the same
  meeting-runs, judged the same way (its "other entries" are its own).
- **Bodies:** records keep only `body_chars`, so the body is recovered by a
  cache-only replay of the production pipeline against the screen's chat
  cache, opened so that a write raises. Every call the record made must be
  served from the cache, and the replayed final list must equal the recorded
  one (type, title, description and body length, in order), or the build
  stops. Nine pool-DA `qwen3:8b` records miss one call they never made: the
  meeting Event call (#1689) postdates them; it is optional, fails closed
  without a model, and the alignment check shows the final list did not move.
- **Record checks** (`record_problems`), any failure stops the build: every
  control record carries the registered `qwen3:8b` digest
  `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`; every
  hosted record's digest is `<provider>:<served-model>:<date>`, dated
  2026-11-23 on pool DA (the #1767 runs) and no earlier than that on the new
  meetings; no record holds a seed-0 call (see "Stage-0 windows").

### Pool DA, pinned now

Counted from the cache (`extra_object_value.py`'s population build,
model-free, run with `OLLAMA_HOST` pointing at a closed port):

| Side | Final objects | No window (excluded) | With counterpart (excluded) | **Population** | At 0.80 | At 0.90 | sha256 of sorted keys |
|---|---|---|---|---|---|---|---|
| `openai-gpt-5-mini` | 99 | 25 | 2 | **72** | 67 | 74 | `68973bcfaf90d58f86e4cfde87fc6c1b5a3fb4b88dcd3047bf068f09f39bf779` |
| `anthropic-claude-haiku-5-5` | 156 | 22 | 4 | **130** | 126 | 134 | `2717f18d5ad4d65f93034210f311220387253c82d95da940101644a0798a65a9` |
| `openai-gpt-6-luna` | 116 | 26 | 2 | **88** | 80 | 89 | `4536054f90f31766172e6b1ffd00c1dc0b7e8f503cf1030ca8335859b90d9a27` |
| control `qwen3:8b` | 50 | 9 | n/a | **41** | n/a | n/a | `6c4e564f055809b6b5f5e12cb6deb0a0716225bcfea31d0767d485545415919e` |

The counterpart rule removes only 8 of 298 windowed pool-DA arm objects:
across models, the same subject is rarely written closely enough to reach
0.85. The population is therefore almost every windowed object these arms
wrote, and the result reads as "the arms' objects", not as a narrow study of
a few extras. The rule was fixed before these counts were seen and is not
retuned.

### DEV12, pinned at run time

The new meetings' counts cannot be known before their runs exist. They are
pinned mechanically instead:

- The first `population` build after extraction writes
  `population-pin.json` (objects and sha256 of the sorted keys, per side)
  under the study's output directory. It writes it only when every record
  check passes, the pool-DA part equals the table above, and extraction is
  complete or declared over (`--final`, see "INCOMPLETE").
- `run` and `report` refuse a population whose pool-DA part differs from the
  table above or whose whole differs from that pin.
- The results file records the DEV12 counts and hashes as pinned.

**Sample: every item.** No subsampling at any size.

## Extraction runs (the seven new meetings)

### Windows

Built by the screen's own `Corpus.meeting` (production `_chunk_lines` at
its default `_CHUNK_TARGET`, `screen_data.build_windows`), the parameters
pool DA was registered with. Pool DA's entries in `windows.json` are
unchanged, and so are the stage-0 meetings' (ES2015b, IS1004c, TS3009b),
which were already registered and still match. The four meetings that had no
entry were added; the screen refuses a run whose windows differ:

| Meeting | Windows | Transcript chars | Gold sentences | In `windows.json` before |
|---|---|---|---|---|
| ES2015b | 11 | 38,944 | 21 | yes (stage 0) |
| ES2015c | 9 | 34,357 | 19 | **added** |
| IS1004a | 3 | 8,289 | 18 | **added** |
| IS1004c | 10 | 36,343 | 29 | yes (stage 0) |
| TS3009a | 6 | 23,342 | 10 | **added** |
| TS3009b | 11 | 42,686 | 22 | yes (stage 0) |
| TS3009d | 10 | 37,272 | 23 | **added** |
| **Seven new** | **60** | **221,233** | | |
| Pool DA, for scale | 41 | 150,456 | | |

### Stage-0 windows

ES2015b window 1, IS1004c window 4 and TS3009b window 6 are the
`STAGE0_WINDOWS` that fixed the #1557 timeouts. `STAGE0_WINDOWS` was kept
out of pool DA so that a calibration call could never be reused as a scored
one. That property still holds. Stage 0 sent its calls at seed 0 (run 0). A
scored run sends seed `run + attempt x 1000`, so seed 1, 2, 1001 and so on,
never 0. The seed is in every cache key, the hosted arms' too, which omit it
from the request (`--no-seed`). So no stage-0 reply can be served to a
scored run, and the record checks refuse any record that holds a seed-0
call. What the windows share with stage 0 is their text and the timeouts
derived from them (`qwen3:8b`'s 146 s per window call, 438 s per
whole-source call). Neither is re-derived, and neither enters a usefulness
label. No meeting is dropped.

### Settings (as #1767)

- **Control `qwen3:8b`**, local, digest
  `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` (the
  digest the local Ollama lists today, and the one in its #1557 stamp and
  records; the record checks refuse another). Seeds `run + attempt x 1000`,
  its registered timeouts, selector judge on.
- **Hosted arms**: `openai-gpt-5-mini` and `openai-gpt-6-luna` at
  `https://api.openai.com`, `anthropic-claude-haiku-5-5` at
  `https://api.anthropic.com`, through the production OpenAI-compatible
  client. Same settings as #1767: `stream: false`, `max_completion_tokens`
  8192, no `temperature`, no `seed` (`--no-seed`), no reasoning-effort field,
  `--timeout-from qwen3:8b`, the client's bounded 429/5xx retry, the selector
  judge on the arm's own model, prose fixtures never sent. The key comes from
  `OPENKOS_OPENAI_API_KEY` in the environment, exported for one arm at a
  time; it is never written to a record, a cache entry, a stamp or the
  ledger.
- **Prompts.** The four extraction prompts at the registration head hash to
  the values in all four arms' stamps (`extraction/system` `6514c14bc12ec1d8`,
  `reask` `abd2691d6f73732b`, `participant_capture` `be7e5009a40ede2d`,
  `judge` `6bb30c750944f78e`). The cache-only replay of all 40 pool-DA records
  at the head reproduces every recorded final list, so the pipeline the new
  runs use is the one #1767 measured.
- **One known difference.** The new `qwen3:8b` runs make the meeting-Event
  call (#1689), which the pool-DA `qwen3:8b` records predate; the hosted
  arms' #1767 records already include it. Event objects have no window, so
  they are never judged, but they appear in the control's "other entries"
  titles on the new meetings.
- **Dates.** The hosted runs on the new meetings happen on a later date than
  the pool-DA runs (2026-11-23). A provider alias can move under a fixed name,
  so an arm's DEV12 objects mix two dates of the same model name. The date is
  in each record's digest, and the report lists it per arm.

### Budget (owner ceiling: $3.00 total)

Forecast from the #1767 ledger actuals (two DA runs per arm), scaled by the
new meetings' size: 1.470 x the transcript characters (a proxy for input
tokens) and 1.463 x the windows (calls: 81 per run against 56). High is twice
central; haiku already spent 1.7 times its #1767 high forecast.

| Arm | #1767 spend (2 DA runs) | Forecast central | High | **Ceiling** | One-call overshoot bound | Calls forecast | Call ceiling |
|---|---|---|---|---|---|---|---|
| `openai-gpt-5-mini` | 0.479 | 0.705 | 1.410 | **1.68** | 0.0225 | 162 | 250 |
| `anthropic-claude-haiku-5-5` | 0.139 | 0.204 | 0.408 | **0.68** | 0.0066 | 162 | 250 |
| `openai-gpt-6-luna` | 0.103 | 0.151 | 0.302 | **0.60** | 0.0066 | 162 | 250 |
| **Total** | 0.721 | **1.060** | **2.119** | **2.96** | 0.0357 | 486 | 750 |

- The guard checks before each call, so an arm stops at most one call past
  its ceiling. The overshoot bound is one call at the 8,192-token generation
  ceiling with a prompt of twice the context window (24,576 tokens). The
  ceilings plus the bounds come to **$2.9956**, within $3.00.
- **The ledger is cumulative.** Each arm's ledger already holds its #1767
  spend (112 calls; $0.479312, $0.1386496, $0.1026882). The `--max-usd` and
  `--max-calls` flags therefore carry the prior figure plus the ceiling.
  `extra_object_value.py budget` prints each arm's exact command line, and
  refuses if a ledger has moved since registration:

  | Arm | `--max-calls` | `--max-usd` | `--price-in` / `--price-out` |
  |---|---|---|---|
  | `openai-gpt-5-mini` | 362 | 2.159312 | 0.25 / 2.00 |
  | `anthropic-claude-haiku-5-5` | 362 | 0.818649 | 0.10 / 0.50 |
  | `openai-gpt-6-luna` | 362 | 0.702688 | 0.10 / 0.50 |

- Prices are the #1767 confirmed standard-tier rates (`hosted_screen.ARMS`).
  Prompt caching and batch discounts are not assumed.

### INCOMPLETE and UNAVAILABLE (as #1767)

- **Provider failure**: a window whose calls ended in `error` or
  `not_called` after the client's retry. It is never a model result, and it
  is not cached. Delete the affected meeting-run's record and run the arm
  again; cached calls replay free. At most **two** such re-runs per arm.
- **Model failure** (`timeout`, `capped`, `unparseable`): cached, kept, not
  re-sent.
- An arm is **INCOMPLETE** when, after its re-runs, any provider-failure
  window remains, a ceiling stopped its run, or any of its 28 DEV12
  meeting-runs has no record. `population` refuses to pin while that holds,
  unless `--final` declares extraction over. An INCOMPLETE arm's objects are
  still judged and its numbers reported, but its rule is **INCOMPLETE** and
  decides nothing, so its V4 veto stands.
- If an arm's first 10 new calls all end in provider failure, stop it and
  report it **UNAVAILABLE on <date>**. It is then INCOMPLETE.
- The control is local and must complete: a missing `qwen3:8b` record stops
  the build.

## Judges

Two local judges, neither a tested arm here:

| Judge | Ollama digest | Why |
|---|---|---|
| `gpt-oss:20b` | `f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe` | The screen's G1 judge (decision D3 of #1557), and the label study's judge (#1579) |
| `gemma4:26b-a4b` | `001e5dafc3c77684c2307ebc6ab8e336e10c9b18eca52acf547d72fc83c3ca8c` | See below |

Choice of the `gemma4` model: `gemma4:26b-a4b` is the only `gemma4` tag the
repository has measured as a judge and adopted. It is `DEFAULT_JUDGE_MODEL`
(`src/openkos/config.py`), selected for the contradiction and identity
judges on the #1269 sweep ([ADR-0047](../../docs/adr/0047-the-contradiction-and-identity-judges-default-to-gemma4-26b-a4b-and-the-engine-runs-one-chat-model-at-a-time.md)),
and confirmed for the Ask sufficiency check on the #1560 screen
([ADR-0076](../../docs/adr/0076-the-ask-sufficiency-check-defaults-to-gemma4-26b-a4b-and-synthesis-stays-on-the-global-model.md),
`openspec/changes/adopt-gemma4-sufficiency-default/`). It is also the fixed
selector judge of the #1571 judge study, pinned at this same digest
(`judge_study.JUDGE_DIGEST`). `gemma4:31b`, `gemma4:12b` and `gemma4:e4b`
have no judge measurement in the repository. It was a #1557 candidate
extractor, but no record judged here was produced by it, and the #1767
selector judge ran on each arm's own model.

`run` reads each digest from the local Ollama's `/api/tags` and refuses a
judge whose digest differs. Both judges run on the same transport as the G1
judge (`screen_pipeline.ollama_sender`: production `OllamaClient`, the
shipped generation ceiling and context window, `think` off in the cache key),
600 s timeout per call. `gpt-oss:20b` labels every item first, then
`gemma4:26b-a4b`, so one model is resident at a time.

## Judge input

One call per item per judge. The system prompt, verbatim
(sha256 `0981f896c894c34c4c64a7bd2f3cc98c46af815bb48508322c3acfb41b5c7bc3`):

```text
You review one candidate entry for a personal knowledge base built from meeting transcripts. If kept, the entry becomes a page of its own. You are given the source excerpt the entry was extracted from, the titles of the other entries extracted from the same meeting, and the entry itself.

Give the entry exactly one label, checking them in this order:
1. "redundant": the entry fragments or restates one of the other entries listed. Its subject is a part of, a rewording of, or the same as another entry's subject, so it would be folded into that entry rather than kept as its own page.
2. "useful": otherwise, if the entry is a distinct concept that the excerpt supports, and a reader of the knowledge base would want it as a page of its own.
3. "trivial": otherwise. The entry is not worth a page of its own: it is generic, obvious or too small to stand alone, or the excerpt does not support it.

Judge only from the excerpt and the listed entries. Do not use outside knowledge. Do not reward length or polish: a short entry can be useful and a long one trivial.

Reply with ONLY a JSON object with the label and one short sentence giving the reason:
{"label": "useful", "reason": "..."}
```

The user message (`extra_object_value.judge_messages`):

```text
SOURCE EXCERPT:
<the text of the window the object was extracted from>

OTHER ENTRIES FROM THE SAME MEETING (titles):
- <title of every other final object of the same side, meeting and run, windowed or not>
(or "- (none)")

ENTRY:
Type: <type>
Title: <title>
Description: <description>
Body:
<body>
```

- **Blind to arm.** Nothing in the message names the model or side.
- **Order.** The population's sorted keys, shuffled once with `random.Random(1783)`;
  both judges see the same order. Arm and control items are interleaved.
- **Parsing, fail-closed.** Production `parsing.extract_json_object`. A
  reply is a label only when it holds a JSON object whose `label` is one of
  `useful`, `redundant`, `trivial` (case-folded) and whose `reason` is a
  non-empty string. Otherwise the call is retried once with seed 1000 (seed 0
  first), as the G1 judge does; a second failure leaves the item without a
  label from that judge, and it is counted.

The labels, as the prompt defines them, in precedence order:

- **redundant:** fragments or restates another entry of the same side,
  meeting and run (its subject is a part, a rewording or the same as another
  entry's), so it would be folded into that entry.
- **useful:** otherwise, a distinct concept the excerpt supports that a
  reader would want as its own page.
- **trivial:** otherwise: supported but generic, obvious or too small to
  stand alone, **or not supported by the excerpt**.

## Analysis (fixed now)

1. **Unlabelled.** An item is unlabelled when either judge left it without a
   label. If more than 10% of the population's items are unlabelled, the study is
   **DEGRADED** and no arm verdict is read.
2. **Agreement.** Cohen's kappa between the two judges over every item both
   labelled (arms and control pooled, three categories). If kappa is below
   **0.6**, or undefined, the study is **INCONCLUSIVE** and no arm verdict is
   read. The disagreement count is reported per side either way.
3. **Agreed items only.** Every share below counts only items both judges
   gave the same label. Agreed useful share = agreed `useful` / all agreed
   items of that side; agreed redundant share likewise.
4. **INSUFFICIENT** for an arm with fewer than 20 agreed items. The control
   does not decide, so its size does not make an arm insufficient.
5. **Bootstrap.** Two-stage, 10,000 resamples, seed 42, percentile 95%
   intervals: the screen's `two_stage_bootstrap` (draw the twelve meetings with
   replacement, then the side's items within each drawn meeting with
   replacement), over one side at a time, for its agreed useful share and its
   agreed redundant share.
6. **The control is descriptive.** The `qwen3:8b` control's agreed useful and
   redundant shares are reported with the same intervals, and so is the
   arm-minus-control useful difference (meetings drawn once for both sides,
   then each side's items; a draw with an empty side is dropped and counted).
   None of these enters a rule.
7. **INCOMPLETE** for an arm that the extraction rules above leave
   incomplete: reported, judged, and deciding nothing.

### Replacement rule for V4 (verbatim)

> An arm **PASSES** if the bootstrap 95% **lower** bound of its agreed useful
> share is at least **0.70** **AND** the 95% **upper** bound of its agreed
> redundant share is at most **0.20** (bootstrap: meetings then objects,
> seed 42, 10,000 resamples; both bounds inclusive). Otherwise it **FAILS**.
> The `qwen3:8b` control is judged and reported with its interval as a
> descriptive reference; it does not decide.

A FAIL is stated with one of two readings, fixed now:

- **FAIL (inferior):** the interval excludes the bar: the useful share's
  upper bound is below 0.70, or the redundant share's lower bound is above
  0.20. The arm's objects are shown to fall short of the bar.
- **FAIL (unresolved):** the bar lies inside the interval. The arm is not
  shown to meet the bar; it is not shown to fall short either.

### Re-reading the #1767 verdicts

Each vetoed arm's #1767 verdict is re-read with V4 replaced by this rule, in
the registered #1767 order (vetoes, then the lift interval), V1, V5 and G1
unchanged:

| Arm | V1 | V5 | G1 lower bound (floor -0.05) | Lift delta [95%] |
|---|---|---|---|---|
| `openai-gpt-5-mini` | holds | holds | -0.1167, **fails** | +0.0151 [-0.0230, +0.0479] |
| `anthropic-claude-haiku-5-5` | holds | holds | -0.0833, **fails** | +0.0387 [-0.0096, +0.0772] |
| `openai-gpt-6-luna` | holds | holds | -0.0538, **fails** | +0.0178 [-0.0210, +0.0488] |

- Rule PASS: the verdict becomes **VETOED (G1)**.
- Rule FAIL: **VETOED (G1, V4-replacement)**.
- Rule INSUFFICIENT or INCOMPLETE, or the study INCONCLUSIVE or DEGRADED: the registered V4
  veto stands, **VETOED (G1, V4 (replacement undecided))**.

**No #1767 verdict can flip.** G1's lower bound fails for all three arms, so
replacing V4 alone leaves each one VETOED. Even without G1, every lift
interval contains 0, so the best reachable label would be NOT
DISTINGUISHABLE, not BETTER. The result informs V4 for future screens: a
PASS says V4 was not the binding problem for that arm and G1's wide interval
at n = 2 on pool DA is. G1 and lift were measured on pool DA only, and this
study does not re-measure them on the new meetings.

### How the outcome is stated

Per arm, one line: `<arm>: V4 replacement <PASS | FAIL (inferior) | FAIL
(unresolved) | INSUFFICIENT | INCOMPLETE>; #1767 verdict under the replacement: <re-read
verdict>`, with the agreed useful share and its interval, the redundant share
and its interval, the agreed and disagreed counts, and, as description only,
the arm-minus-control useful difference. Once for the study: kappa,
unlabelled count, DECIDED, INCONCLUSIVE or DEGRADED, the control's agreed
shares with their intervals, the DEV12 pin, and each hosted arm's run dates
and spend.

Consequences, none of which changes a default or a shipped veto:

- PASS on an arm: the V4 veto is recorded as not supported for that arm at
  this n. Whether future screens replace V4 with this rule is its own
  registration.
- FAIL (inferior): V4's veto stands with a measured cause.
- FAIL (unresolved), INSUFFICIENT, INCOMPLETE, INCONCLUSIVE, DEGRADED: V4 is neither
  confirmed nor refuted; it stands as registered.

## Power check (model-free, before any label)

Simulated with the harness's own `share_interval` and `arm_rule` over the
twelve DEV12 meetings (1,000 resamples per interval, 200 simulations per
cell, seed 1783; a forecast, not a result). Each item is useful with the true
share shown, redundant with 0.10, trivial otherwise.

- **Sizes.** The new meetings' object counts are unknown until they are run,
  so each arm's items per meeting are its pool-DA rate per window-run (72,
  130 and 88 judged objects over 82 window-runs: 0.88, 1.59, 1.07) times the
  meeting's windows times two runs. That gives about 178 items for
  `gpt-5-mini`, 323 for haiku and 216 for luna.
- **Agreement.** Every item agreed, or 70% agreed.
- **Meeting spread.** Either none (every meeting at the true share), or a
  per-meeting true share drawn around it with a standard deviation of 0.10.

Each cell gives P(PASS), then P(useful lower bound >= 0.70) and P(redundant
upper bound <= 0.20):

| Meeting spread | Agreement | Arm (agreed n) | True useful 0.80 | True useful 0.70 |
|---|---|---|---|---|
| none | all agreed | `gpt-5-mini` (178) | **0.64** (0.65, 0.88) | **0.01** (0.01, 0.89) |
| none | all agreed | haiku (323) | **0.96** (0.96, 1.00) | **0.01** (0.01, 0.99) |
| none | all agreed | luna (216) | **0.81** (0.81, 0.95) | **0.01** (0.01, 0.96) |
| none | 70% agreed | `gpt-5-mini` (125) | **0.42** (0.47, 0.69) | **0.01** (0.01, 0.71) |
| none | 70% agreed | haiku (223) | **0.81** (0.81, 0.95) | **0.01** (0.01, 0.94) |
| none | 70% agreed | luna (155) | **0.52** (0.56, 0.79) | **0.01** (0.01, 0.85) |
| sd 0.10 | all agreed | `gpt-5-mini` (178) | **0.44** (0.46, 0.86) | **0.01** (0.01, 0.86) |
| sd 0.10 | all agreed | haiku (323) | **0.67** (0.67, 0.99) | **0.04** (0.04, 0.99) |
| sd 0.10 | all agreed | luna (216) | **0.50** (0.51, 0.94) | **0.01** (0.01, 0.93) |
| sd 0.10 | 70% agreed | `gpt-5-mini` (125) | **0.33** (0.34, 0.78) | **0.01** (0.01, 0.74) |
| sd 0.10 | 70% agreed | haiku (223) | **0.52** (0.53, 0.94) | **0.01** (0.01, 0.95) |
| sd 0.10 | 70% agreed | luna (155) | **0.44** (0.46, 0.85) | **0.01** (0.01, 0.84) |

**Read this before the result:**

- **Twelve meetings help but do not make the rule easy.** An arm whose true
  useful share is 0.80 passes 33% to 96% of the time, depending on its size,
  the agreement and the spread between meetings. On five meetings the same
  arm passed 20% to 53% of the time, with no spread modelled.
- The useful bound binds; the redundant ceiling now mostly holds (0.69 to
  1.00 at a true 0.10).
- **`gpt-5-mini` is the weakest case.** It is the smallest arm and passes 33%
  to 64% of the time at a true 0.80.
- An arm at exactly the bar (0.70) passes at most 4% of the time, as a
  lower-bound rule should.
- A PASS is strong evidence. A FAIL (unresolved) remains a common outcome for
  an arm that truly meets the bar, and must not be read as "the objects are
  worse".

**Earlier checks, kept for the record.** On pool DA alone, the absolute rule
passed an arm with true useful 0.80 and redundant 0.10 only 20% to 53% of
the time (no meeting spread). The first approved rule (the arm's useful share
no more than 0.10 below the control's) passed an arm equal to the control 3%
to 14% of the time, because the 41-object control set the width. Those two
results are why the owner replaced the rule and then widened the meetings
(see "Design adjustments").

## Cost and time

- **API spend:** at most the ceilings above, **$2.96, bounded at $2.9956
  with the one-call overshoot**, against an owner ceiling of $3.00. Central
  forecast $1.06.
- **Hosted wall time:** at the #1767 means per meeting-run (201 s, 87 s and
  134 s), scaled by 1.46, about 1.7 h for the three arms, run one at a time.
- **Local GPU** (indicative, from prior latency on this machine):
  - `qwen3:8b` extraction on the 14 new meeting-runs: 120 window-runs at its
    pool-DA mean of 35.6 s per window-run (292 s per meeting-run over 8.2
    windows), about **71 min**.
  - bge-m3 embeddings of the new objects: a few minutes.
  - Judges. Estimated population about 820 items: about 717 arm items (as in
    the power check) plus about 101 control (41 plus 0.5 per window-run over
    120 new window-runs). That is about 1,640 calls.
    - `gpt-oss:20b`: about **125 min**, at the #1557 G1 calls' median of
      9.2 s (267 cached calls) and the label study's 8.43 s per call.
    - `gemma4:26b-a4b`: about **26 min**, at about 1.9 s warm (Ask screen
      #1560; the #1571 judge-study cache, 162 calls, median 1.94 s).
  - **Total about 3.7 h.** Plan on 6 h for retries, longer replies and model
    loads.
  - Local GPU use starts only after the #1768 A/B finishes (owner decision).

## Procedure

After this file merges, from a checkout of `main`, once the #1768 A/B has
released the GPU. The four gitignored prose fixtures must be present in the
checkout (the screen's `run` refuses to start otherwise, though no prose is
sent), as in #1767.

1. **Local extraction (control).** Pool DA's records exist and are skipped:

   ```sh
   uv run python evals/concept_model_screen/run_concept_screen.py run \
     --models qwen3:8b --pool DEV12 --runs 1 2
   ```

2. **Hosted extraction**, one arm at a time, with that provider's key
   exported for that arm only. Print the registered command lines, which
   refuse a moved ledger, then run each with `<date of run>` filled in:

   ```sh
   uv run python evals/concept_model_screen/extra_object_value.py budget
   export OPENKOS_OPENAI_API_KEY=...   # this arm's provider only
   uv run python evals/concept_model_screen/run_concept_screen.py run \
     --models openai-gpt-5-mini --pool DEV12 --runs 1 2 \
     --backend openai-compatible --base-url https://api.openai.com \
     --served-model gpt-5-mini --digest openai:gpt-5-mini:<date of run> \
     --timeout-from qwen3:8b --no-seed \
     --max-calls 362 --max-usd 2.159312 --price-in 0.25 --price-out 2.00
   ```

   Then apply the INCOMPLETE rules: at most two re-runs of affected
   meeting-runs per arm.

3. **Embeddings** of the new final objects on the local bge-m3 (the
   registered digest; the subcommand refuses another):

   ```sh
   uv run python evals/concept_model_screen/extra_object_value.py embed
   ```

4. **Population**, model-free; it pins DEV12 on its first clean build (add
   `--final` only when an arm stays INCOMPLETE after its re-runs):

   ```sh
   uv run python evals/concept_model_screen/extra_object_value.py population
   ```

5. **Judges**, then the **report**:

   ```sh
   uv run python evals/concept_model_screen/extra_object_value.py run
   uv run python evals/concept_model_screen/extra_object_value.py report
   ```

The study's outputs are written under
`~/Dev/Projects/openkos-eval-runs/extra-object-value/`: `population.json`,
`population-pin.json`, the judge cache, `stamp.json` and `labels.json`. Steps
1 to 3 write into the screen's runs directory: the screen's own records,
chat cache, ledgers and embedding cache, as every screen run does. Pool DA's
entries there are never rewritten, because `run` skips an existing record.
The rule is applied by `report`, not by hand.

## Limitations

- **An LLM's "useful" stands in for a human's.** The issue proposed human
  adjudication; the owner chose two local judges instead. Agreement between
  two models is not correctness, and kappa >= 0.6 only says they agree.
- **The judges may be weaker than the arms they judge.** `gpt-oss:20b` and
  `gemma4:26b-a4b` are smaller local models than the hosted arms.
- **`gpt-oss:20b` is an OpenAI open-weight model, and two of the three arms
  are OpenAI hosted models.** Neither is a tested arm here, but a same-vendor
  style preference cannot be ruled out. `gemma4:26b-a4b` (Google) has no
  same-vendor arm, and the decision uses only the items both judges agree on.
- **n = 2 runs, twelve meetings, three series.** The bootstrap rests on
  twelve meetings from three meeting series, so the meetings are not
  independent draws. The power check above shows that even an arm well above
  the bar can fail to clear it.
- **Two dates per hosted arm.** Pool DA was run on 2026-11-23, the new
  meetings later. A provider can change a model behind a fixed name; the
  study cannot tell that apart from a meeting effect.
- **The development meetings are the thesis's tuning set.** They were already
  used to tune this screen (#1557 stage 0 and pool DA), which is why they may
  be used here. A result on them is not a result on held-out data, and pool F
  is not used to check it.
- **Objects without a window are not judged** (on pool DA: 73 arm objects, 9
  control), though V4 counts them. They are mostly participant and meeting-Event
  objects.
- **The control is the reference's whole windowed output, not its "extras".**
  It was 41 objects on pool DA (about 100 expected on DEV12), so it is a
  descriptive reference, not a decision baseline. The 0.70 floor is an absolute bar the owner set, not a measured
  property of the reference.
- **The redundancy context is the side's other titles only,** not their
  descriptions. A judge may miss a restatement it could see only in a
  description; this applies to arms and control alike, and the arms list
  more titles, which gives a redundant label more chances. That is the
  quantity being measured.
- **Paraphrase.** #1767 found the hosted arms quote almost no source line
  (unevidenced 0.97 to 1.00). The judge sees the excerpt and decides support
  itself; it does not use `final_unevidenced`.
- Nothing about private or non-English sources, other pipeline tasks, or a
  provider beyond a model name on the dates its records carry. No hosted model becomes a
  default, and nothing here weakens the local-first principle.

## Design adjustments

Each is an addition to, or a reading of, the approved design, with its
reason:

1. **Window-less objects are excluded** (approved design: final objects
   with no counterpart). The judge must see the source window and these have
   none; sending the whole transcript instead would make their prompts many
   times longer than everyone else's.
2. **"Same meeting-run" pairs by run index**, as the screen pairs runs
   everywhere else.
3. **An unsupported entry is labelled `trivial`, and the labels are checked
   in the order redundant, useful, trivial.** The approved set has no
   `unsupported` label; folding it into `trivial` keeps the set and counts
   such an object against the arm, which is the conservative direction for a
   rule about usefulness. The order makes the labels mutually exclusive.
4. **One retry on an unusable reply**, seed 1000, as the G1 judge
   (`run_concept_screen.judge_call`) does; still fail-closed and counted.
5. **DEGRADED above 10% unlabelled**, the label study's (#1579) threshold:
   kappa over a set that silently lost many items would not describe the run.
6. **INSUFFICIENT below 20 agreed items** on the arm, so that a rule is
   never read off a handful of objects. (With the absolute rule the control
   no longer decides, so its size no longer makes an arm insufficient.)
7. **Shared meeting draw in the descriptive difference bootstrap, empty draws
   dropped and counted,** so the arm and control are compared on the same
   meetings and an empty side never scores as a share of 0.
8. **Kappa pooled over arms and control**, the "labelled set" of the
   approved design.
9. **The FAIL readings (inferior, unresolved)** do not change when an arm
   passes; they only state a FAIL honestly, given the power check.
10. **Bodies come from a cache-only replay**, because the records keep only
    their length.
11. **The relative rule was replaced by an absolute one (owner decision,
    2026-11-23).** The first approved rule passed an arm only if its agreed
    useful share was no more than 0.10 below the `qwen3:8b` control's. The
    power check showed it would pass an arm exactly as useful as the control
    only 3% to 14% of the time, because the 41-object control made the
    interval wide. The control could not be enlarged on pool DA: only two
    `qwen3:8b` runs are cached there. The
    rule now bounds the arm's own shares (useful lower bound >= 0.70,
    redundant upper bound <= 0.20), and the control is reported only as a
    descriptive reference. The redundant ceiling, the bootstrap, the FAIL
    readings, kappa, DEGRADED, INSUFFICIENT and the "no verdict can flip"
    statement are unchanged. The change was made before any label existed.
12. **The population was widened from pool DA to DEV12 (owner decision,
    2026-11-23),** because the absolute rule still passed an arm with a true
    useful share of 0.80 only 20% to 53% of the time on five meetings. DEV12
    is every meeting of the three development series. It adds the seven
    meetings outside pool DA and no other pool. This needs new local and
    hosted extraction runs on those seven meetings, registered above. Pool
    DA's pins, records and windows are unchanged.
13. **DEV12 is pinned at run time,** because its counts do not exist before
    its runs. The pool-DA part stays pinned now and is checked on every
    build.
14. **The hosted ceilings are passed as cumulative flags,** because the
    screen's spend ledger persists across runs and already holds each arm's
    #1767 spend. `budget` computes them and refuses a moved ledger.
15. **The stage-0 meetings are kept,** with no stage-0 reply reusable (the
    seed-0 check); see "Stage-0 windows".
16. **`embed` was added** so that `population` can stay model-free and
    read-only on the embedding cache.
17. **`population --final`** marks the point at which an INCOMPLETE arm is
    accepted as INCOMPLETE, so that a population is never pinned
    mid-extraction.

## Owner decisions

Settled by the owner on 2026-11-23, before registration:

- **The absolute V4 replacement rule** above, replacing the relative rule
  (Design adjustment 11).
- **No human adjudication.** Labels come only from the two local judges; the
  decision uses only the items both agree on.
- **The two local judges** `gpt-oss:20b` and `gemma4:26b-a4b`, at the digests
  pinned above.
- **Twelve development meetings** (pool DEV12: ES2015a-d, IS1004a-d,
  TS3009a-d, thesis ADR-0005), with new extraction runs on the seven outside
  pool DA (Design adjustment 12). **Pool F is untouched** and stays reserved
  as the confirmation pool; no evaluation or reserve series is used.
- **A $3.00 total ceiling** for the new hosted runs, enforced per arm as
  registered under "Budget".
- **Local GPU use** for the `qwen3:8b` extraction, the embeddings and the
  judge run, **after the #1768 evidence A/B finishes**, so the two studies do
  not share the GPU.

## Disclosed before registration

- Read from the cache while preparing this file: the population counts and
  hashes above, the replay's nine unrecorded misses, and the population's
  object counts per type (for example 81 of haiku's 130 are Decisions). No
  label, reason or judge reply exists.
- No model was called: the population build ran with `OLLAMA_HOST` pointing
  at a closed port. The two judge digests and bge-m3's were read from the
  local Ollama's `/api/tags`, which runs no model.
- Also read: the seven new meetings' transcripts, windows and gold-sentence
  counts (model-free, through the screen's corpus loader); the three hosted
  ledgers; the four arms' extraction stamps.
- No new meeting has an extraction record. No hosted call was made for this
  file.
- The power checks above were computed after the counts were known and
  before any label. The first one led the owner to replace the relative rule
  with the absolute one (Design adjustment 11). The second, on pool DA, led
  the owner to widen the meetings (Design adjustment 12). The third, on
  DEV12, changed no threshold.
