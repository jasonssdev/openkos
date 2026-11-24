# Pre-registration: are the vetoed hosted arms' extra objects useful? (#1783)

**Status: registered on merge to `main`.** No judge call may be sent before
that merge. A change after the merge is a new registration, not an edit to
this one. The owner approved the design on 2026-11-23. Where this file
departs from that design or adds to it, the reason is given under "Design
adjustments".

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
prompt, and makes no hosted call: every object comes from the cached #1767
records.

## Population

- **Arms:** the three V4-vetoed arms above, pool DA (ES2015a, IS1004b,
  TS3009c, ES2015d, IS1004d), runs 1 and 2, their #1767 records.
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
  a missing vector stops the build, nothing is embedded.
- **Control:** every windowed final object of `qwen3:8b` in the same ten
  meeting-runs, judged the same way (its "other entries" are its own).
- **Bodies:** records keep only `body_chars`, so the body is recovered by a
  cache-only replay of the production pipeline against the screen's chat
  cache, opened so that a write raises. Every call the record made must be
  served from the cache, and the replayed final list must equal the recorded
  one (type, title, description and body length, in order), or the build
  stops. Nine `qwen3:8b` records miss one call they never made: the meeting
  Event call (#1689) postdates them; it is optional, fails closed without a
  model, and the alignment check shows the final list did not move.

Counted from the cache (`extra_object_value.py population`, model-free, run
with `OLLAMA_HOST` pointing at a closed port):

| Side | Final objects | No window (excluded) | With counterpart (excluded) | **Population** | At 0.80 | At 0.90 | sha256 of sorted keys |
|---|---|---|---|---|---|---|---|
| `openai-gpt-5-mini` | 99 | 25 | 2 | **72** | 67 | 74 | `68973bcfaf90d58f86e4cfde87fc6c1b5a3fb4b88dcd3047bf068f09f39bf779` |
| `anthropic-claude-haiku-5-5` | 156 | 22 | 4 | **130** | 126 | 134 | `2717f18d5ad4d65f93034210f311220387253c82d95da940101644a0798a65a9` |
| `openai-gpt-6-luna` | 116 | 26 | 2 | **88** | 80 | 89 | `4536054f90f31766172e6b1ffd00c1dc0b7e8f503cf1030ca8335859b90d9a27` |
| control `qwen3:8b` | 50 | 9 | n/a | **41** | n/a | n/a | `6c4e564f055809b6b5f5e12cb6deb0a0716225bcfea31d0767d485545415919e` |

The counterpart rule removes only 8 of 298 windowed arm objects: across
models, the same subject is rarely written closely enough to reach 0.85. The
population is therefore almost every windowed object these arms wrote, and
the result reads as "the arms' objects against the reference's", not as a
narrow study of a few extras. The rule was fixed before these counts were
seen and is not retuned.

**Sample: all 331 items** (290 arm, 41 control). No subsampling; the counts
and hashes above are pinned in the harness, and `run` and `report` refuse any
other population.

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
- **Order.** The 331 sorted keys, shuffled once with `random.Random(1783)`;
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
   label. If more than 10% of the 331 items are unlabelled, the study is
   **DEGRADED** and no arm verdict is read.
2. **Agreement.** Cohen's kappa between the two judges over every item both
   labelled (arms and control pooled, three categories). If kappa is below
   **0.6**, or undefined, the study is **INCONCLUSIVE** and no arm verdict is
   read. The disagreement count is reported per side either way.
3. **Agreed items only.** Every share below counts only items both judges
   gave the same label. Agreed useful share = agreed `useful` / all agreed
   items of that side; agreed redundant share likewise.
4. **INSUFFICIENT** for an arm with fewer than 20 agreed items, or when the
   control has fewer than 20.
5. **Bootstrap.** Two-stage, 10,000 resamples, seed 42. For the useful-share
   difference (arm minus control): draw the five meetings with replacement,
   one draw shared by arm and control, then resample each side's items within
   each drawn meeting with replacement. A draw in which either side is empty
   is dropped and the number dropped is reported. For the arm's redundant
   share: the screen's `two_stage_bootstrap` (meetings, then items) over the
   arm alone. Percentile 95% intervals.

### Replacement rule for V4 (verbatim)

> An arm **PASSES** if its agreed useful share is not lower than the
> `qwen3:8b` control's by more than 0.10 (the bootstrap 95% lower bound of
> the difference, meetings then objects, seed 42, 10,000 resamples, is at
> least -0.10) **AND** the 95% upper bound of its agreed redundant share is
> at most 0.20. Otherwise it **FAILS**. Both bounds are inclusive.

A FAIL is stated with one of two readings, fixed now:

- **FAIL (inferior):** the interval excludes the bar: the useful-share
  difference's upper bound is below -0.10, or the redundant share's lower
  bound is above 0.20. The extra objects are shown to be worse.
- **FAIL (unresolved):** the bar lies inside the interval. The arm is not
  shown to be non-inferior; it is not shown to be worse either. See the power
  check below: at this n, this is the expected outcome even for an arm equal
  to the control.

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
- Rule INSUFFICIENT, or the study INCONCLUSIVE or DEGRADED: the registered V4
  veto stands, **VETOED (G1, V4 (replacement undecided))**.

**No verdict can flip at n = 2.** G1's lower bound fails for all three arms,
so replacing V4 alone leaves each one VETOED. Even without G1, every lift
interval contains 0, so the best reachable label would be NOT
DISTINGUISHABLE, not BETTER. What this study can change is the reason: a PASS
says V4 was not the binding problem for that arm and G1's wide interval at
n = 2 is.

### How the outcome is stated

Per arm, one line: `<arm>: V4 replacement <PASS | FAIL (inferior) | FAIL
(unresolved) | INSUFFICIENT>; #1767 verdict under the replacement: <re-read
verdict>`, with the agreed useful share against the control's, the
difference and its interval, the redundant share and its interval, the agreed
and disagreed counts. Once for the study: kappa, unlabelled count, and
DECIDED, INCONCLUSIVE or DEGRADED.

Consequences, none of which changes a default or a shipped veto:

- PASS on an arm: the V4 veto is recorded as not supported for that arm at
  this n. Whether future screens replace V4 with this rule is its own
  registration.
- FAIL (inferior): V4's veto stands with a measured cause.
- FAIL (unresolved), INSUFFICIENT, INCONCLUSIVE, DEGRADED: V4 is neither
  confirmed nor refuted; it stands as registered.

## Power check (model-free, before any label)

The rule was simulated with the harness's own `paired_share_delta`, the
registered population sizes spread over five meetings, both sides drawn at the
**same** true useful share (1,000 resamples per interval, 200 simulations per
cell; a forecast, not a result):

| Agreement | Arm n / control n | True useful share 0.8 | True useful share 0.6 |
|---|---|---|---|
| all items agreed | 72 / 41 (`gpt-5-mini`) | 0.14 | 0.07 |
| all items agreed | 130 / 41 (haiku) | 0.11 | 0.07 |
| all items agreed | 88 / 41 (luna) | 0.12 | 0.09 |
| 70% agreed | 50 / 29 | 0.13 | 0.03 |
| 70% agreed | 91 / 29 | 0.10 | 0.06 |
| 70% agreed | 62 / 29 | 0.08 | 0.07 |

The cells give P(useful lower bound >= -0.10). Under equality at 0.8 and 70%
agreement, the median lower bound is about -0.20 to -0.23. The 41-object
control is the binding side. For the redundant ceiling, an arm whose true
redundant share is 0.10 keeps its upper bound at or under 0.20 about half the
time (0.46 to 0.62).

**Read this before the result:** the approved rule passes an arm that is
exactly as useful as the reference only about one time in ten. A PASS is
strong evidence. A FAIL (unresolved) is close to the expected outcome
whatever the truth, and must not be read as "the extra objects are worse".
The margin and the ceiling are the owner's approved values and are kept.
Loosening them now, after the power is known but before any label, would be
a new owner decision and therefore a new registration.

## Cost and time

- **API spend: none.** No hosted call is made. The arms' objects come from
  the cached #1767 records.
- **Calls:** 331 items x 2 judges = 662 calls, plus at most one retry each.
- **GPU forecast** (indicative, from prior judge latency on this machine):
  `gpt-oss:20b` at the #1557 G1 calls' median of 9.2 s (267 cached calls, one
  window plus its entries per prompt, the same prompt size as here) and the
  label study's 8.43 s per call: about **51 min**. `gemma4:26b-a4b` at about
  1.9 s warm (Ask screen #1560; the #1571 judge-study cache, 162 calls,
  median 1.94 s): about **11 min**. Total about **1 h**, plus two warm-ups.
  Planning figure 2 h for retries and longer replies. Local GPU time is free
  but needs the owner's go-ahead.

## Procedure

After this file merges, from a checkout of `main`:

```sh
uv run python evals/concept_model_screen/extra_object_value.py population
uv run python evals/concept_model_screen/extra_object_value.py run
uv run python evals/concept_model_screen/extra_object_value.py report
```

`population` is model-free and must print the four pinned counts and hashes
(it exits non-zero otherwise). Everything is written under
`~/Dev/Projects/openkos-eval-runs/extra-object-value/` (`population.json`,
the judge cache, `stamp.json`, `labels.json`); the screen's runs directory is
only read. The rule is applied by `report`, not by hand.

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
- **n = 2 runs, five meetings.** The bootstrap rests on five meetings, and
  the power check above shows the rule is hard to pass at this n.
- **Objects without a window are not judged** (73 arm objects, 9 control),
  though V4 counts them. They are mostly participant and meeting-Event
  objects.
- **The control is the reference's whole windowed output, not its "extras".**
  It is the baseline for what share of a model's objects are useful; it is
  only 41 objects.
- **The redundancy context is the side's other titles only,** not their
  descriptions. A judge may miss a restatement it could see only in a
  description; this applies to arms and control alike, and the arms list
  more titles, which gives a redundant label more chances. That is the
  quantity being measured.
- **Paraphrase.** #1767 found the hosted arms quote almost no source line
  (unevidenced 0.97 to 1.00). The judge sees the excerpt and decides support
  itself; it does not use `final_unevidenced`.
- Nothing about private or non-English sources, other pipeline tasks, or a
  provider beyond a model name on 2026-11-23. No hosted model becomes a
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
6. **INSUFFICIENT below 20 agreed items** on either side, so that a rule is
   never read off a handful of objects.
7. **Shared meeting draw in the difference bootstrap, empty draws dropped and
   counted,** so the arm and control are compared on the same meetings and an
   empty side never scores as a share of 0.
8. **Kappa pooled over arms and control**, the "labelled set" of the
   approved design.
9. **The FAIL readings (inferior, unresolved)** do not change when an arm
   passes; they only state a FAIL honestly, given the power check.
10. **Bodies come from a cache-only replay**, because the records keep only
    their length.

## Disclosed before registration

- Read from the cache while preparing this file: the population counts and
  hashes above, the replay's nine unrecorded misses, and the population's
  object counts per type (for example 81 of haiku's 130 are Decisions). No
  label, reason or judge reply exists.
- No model was called: the population build ran with `OLLAMA_HOST` pointing
  at a closed port. The two judge digests and bge-m3's were read from the
  local Ollama's `/api/tags`, which runs no model.
- The power check above was computed after the counts were known and before
  any label; it changed no threshold.
