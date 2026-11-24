# Results: hosted providers vs the local default on the concept screen (#1767)

**Verdict: no hosted arm is BETTER than `qwen3:8b`. One arm is WORSE, three
are VETOED. The registered "paraphrase confirmed" threshold for #1768 is met
on all four arms. Nothing is adopted and no default changes.**

Rules: [`HOSTED_PREREGISTRATION.md`](HOSTED_PREREGISTRATION.md), merged in
#1779 (`0d60391b`) before any hosted call. The rule was applied to the stored
scores, not re-tuned. Pool DA, runs 1 and 2 (n = 2), reference `qwen3:8b` from
its cached #1557 records. Run on 2026-11-23.

| Arm | Verdict | What decided it |
|---|---|---|
| `openai-gpt-4.1-mini` | **WORSE than local** | No veto holds against it. Window lift delta -0.0232 [-0.0536, -0.0006]; the upper bound is below 0 |
| `openai-gpt-5-mini` | **VETOED** | V4 volume 9.90 objects per meeting-run, outside [2.50, 7.50]; G1 precision delta lower bound -0.1167, below -0.05 |
| `anthropic-claude-haiku-5-5` | **VETOED** | V4 volume 15.60, outside [2.50, 7.50]; G1 lower bound -0.0833, below -0.05 |
| `openai-gpt-6-luna` | **VETOED** | V4 volume 11.60, outside [2.50, 7.50]; G1 lower bound -0.0538, below -0.05 |

No arm is INCOMPLETE: every arm has 0 provider-failure windows (82 of 82
windows answered), and no ceiling stopped a run. No arm is UNAVAILABLE.

## Primary and guardrail numbers

Window-level paired lift delta against `qwen3:8b` (two-stage bootstrap,
10,000 resamples, seed 42), G1 precision delta, and the vetoes.

| Arm | Window lift (mean) | Lift delta vs `qwen3:8b` [95%] | G1 precision | G1 delta [95%] | V1 failures | V4 objects / meeting-run | V5 verbatim |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` (ref) | +0.0319 | | 38 of 41 | | 2 of 82 | 5.00 | 0 of 50 |
| `openai-gpt-4.1-mini` | +0.0087 | -0.0232 [-0.0536, -0.0006] | 11 of 11 | +0.0732 [0.0000, +0.1905] | 0 of 82 | 2.70 | 0 of 27 |
| `openai-gpt-5-mini` | +0.0470 | +0.0151 [-0.0230, +0.0479] | 69 of 74 | +0.0056 [-0.1167, +0.1409] | 1 of 82 | **9.90** | 0 of 99 |
| `anthropic-claude-haiku-5-5` | +0.0706 | +0.0387 [-0.0096, +0.0772] | 127 of 134 | +0.0209 [-0.0833, +0.1428] | 0 of 82 | **15.60** | 0 of 156 |
| `openai-gpt-6-luna` | +0.0496 | +0.0178 [-0.0210, +0.0488] | 88 of 90 | +0.0509 [-0.0538, +0.1892] | 0 of 82 | **11.60** | 0 of 116 |

- Vetoes: V1 holds for every arm (at most the reference's 0.024 + 0.02 and at
  most 0.05; `openai-gpt-5-mini` has 1 of 82, an `unparseable` window). V5
  holds for every arm. V3 (Spanish title language) is not evaluated for any
  hosted arm, as registered. V4 and G1 fail where bolded or named above.
- `openai-gpt-4.1-mini` is the only arm that clears every veto, and it is
  WORSE: it extracts only 27 final objects over 10 meeting-runs (reference
  50), and the selector judge removed 40 of its candidates. The upper bound
  -0.0006 clears 0 by a hair; read it as "worse or equal at n = 2".
- The three vetoed arms would otherwise read NOT DISTINGUISHABLE on lift (all
  intervals contain 0). That is reported for completeness only: the rule
  checks vetoes first. Their higher point lifts come with 2 to 3 times the
  reference's object volume.
- Meeting-level paired delta (secondary, not decided on): 4.1-mini -0.0035
  [-0.0107, +0.0056], 5-mini +0.0294 [+0.0135, +0.0474], haiku +0.0212
  [+0.0071, +0.0387], luna +0.0210 [+0.0051, +0.0344].

## The two defects

Final objects flagged unevidenced (production's `_unevidenced_titles`
predicate), and final objects with another final object of the same
meeting-run at bge-m3 cosine >= 0.85. Difference against `qwen3:8b`, with a
bootstrap that draws the five meetings with replacement (same draw for both
arms).

| Arm | Unevidenced, n of TOTAL | Rate | Delta vs reference [95%] | Paraphrase | Near-duplicates, n of TOTAL | Delta vs reference [95%] | Duplicates |
|---|---|---|---|---|---|---|---|
| `qwen3:8b` (ref) | 5 of 50 | 0.100 | | | 0 of 50 | | |
| `openai-gpt-4.1-mini` | 19 of 27 | 0.704 | +0.6037 [+0.2608, +0.9778] | **confirmed** | 0 of 27 | +0.0000 [+0.0000, +0.0000] | not shown |
| `openai-gpt-5-mini` | 96 of 99 | 0.970 | +0.8697 [+0.8059, +0.9436] | **confirmed** | 0 of 99 | +0.0000 [+0.0000, +0.0000] | not shown |
| `anthropic-claude-haiku-5-5` | 156 of 156 | 1.000 | +0.9000 [+0.8333, +0.9778] | **confirmed** | 2 of 156 | +0.0128 [+0.0000, +0.0536] | not shown |
| `openai-gpt-6-luna` | 116 of 116 | 1.000 | +0.9000 [+0.8333, +0.9778] | **confirmed** | 0 of 116 | +0.0000 [+0.0000, +0.0000] | not shown |

**#1768 (paraphrase): the registered threshold is met.** "Paraphrase
confirmed" needs the unevidenced-rate difference's 95% lower bound above
+0.10, on at least one arm that is not INCOMPLETE or UNAVAILABLE. All four
arms clear it, and every lower bound is at least +0.26. Per the registration,
#1768 proceeds with this data. The three vetoed arms are not INCOMPLETE, so
they count; the confirmation does not depend on them, since the one
non-vetoed arm (4.1-mini) also clears it.

**Near-duplicates: not shown on any arm.** No lower bound is above 0. Haiku
has 2 of 156 pairs at 0.85; the other arms have none. The smoke test's
two-apatheia case did not recur at this n. Sensitivity thresholds (0.80,
0.90) were not computed.

## Spend, calls and the forecast

All four arms made 112 calls (forecast 102, from the local run-1 call count; the
extra 10 calls per arm were not itemized). The spend guard
never stopped a run; the ceilings were not approached. Spend is the harness
ledger, priced at the registered rates.

| Arm | Calls | Prompt tokens | Completion tokens | Spend USD | Forecast central / high USD | Ceiling USD |
|---|---|---|---|---|---|---|
| `openai-gpt-4.1-mini` | 112 | 395,491 | 17,681 | 0.186 | 0.18 / 0.25 | 1.50 |
| `openai-gpt-5-mini` | 112 | 396,752 | 190,062 | 0.479 | 0.45 / 1.04 | 2.00 |
| `anthropic-claude-haiku-5-5` | 112 | 605,596 | 156,180 | 0.139 | 0.06 / 0.08 | 0.50 |
| `openai-gpt-6-luna` | 112 | 395,242 | 126,328 | 0.103 | 0.13 / 0.27 | 0.50 |
| **Total** | 448 | | | **0.907** | 0.82 / 1.64 | 4.50 |

- Haiku spent above the forecast's high column (0.139 vs 0.083) and stayed at
  28% of its ceiling. It wrote about 1,394 tokens per call against the local
  reference's much shorter replies, which is where the forecast was wrong. Its
  prompt-token count was 1.53 times the OpenAI arms', against the assumed
  factor of 1.4.
- The reasoning arms' billed output is large: 1,697 tokens per call for
  `gpt-5-mini` and 1,128 for `gpt-6-luna`, against 158 for `gpt-4.1-mini`.

## Provider errors, failures and latency

- Provider-failure windows: 0 on every arm. No re-run of any meeting-run was
  needed, so the registered two re-runs were not used.
- Model failures (cached, count in V1): `openai-gpt-5-mini` 1 `unparseable`
  window; the reference has 2 `unparseable`. The others have none.
- Window outcomes: `qwen3:8b` 69 ok, 2 unparseable, 11 empty;
  `openai-gpt-4.1-mini` 81 ok, 1 empty; `openai-gpt-5-mini` 81 ok, 1
  unparseable; haiku 82 ok; luna 82 ok.
- Latency (reported, not gated). Median seconds per window call and mean
  seconds per meeting-run: `qwen3:8b` 33.4 and 292; 4.1-mini 2.9 and 34;
  5-mini 20.1 and 201; haiku 8.4 and 87; luna 14.1 and 134.
- Judged-out counts (selector judge): reference 13, 4.1-mini 40, 5-mini 122,
  haiku 59, luna 111.
- Counter `wrong_language_dropped`, reported only (V3 is not evaluated):
  4.1-mini 4, luna 16, the others 0.

## Deviations from the registration

None changes a number or a rule.

1. **Gitignored fixtures copied into the worktree.** `run` refuses to start
   unless 5 of 5 `examples/extraction-corpus/sources` files are present, even
   though no prose is sent. The first launch of the first arm stopped on that
   check ("1 of 5 present") before any hosted call. The four missing
   gitignored files were copied from the main checkout and the arm was
   started again. Nothing was sent twice, and none of them is committed.
2. **A malformed first `judge` invocation.** A shell word-splitting slip
   passed the four arm names as one string; the harness answered "no record
   for ..." and exited without any model call. The command was re-run
   correctly. It also made the first `score`, `replay` and `metrics` chain
   run over bad arguments; that chain was killed, its outputs deleted, and
   the whole chain was run again from the start.
3. **Calls above the forecast** (112 vs 102), under the call ceiling of 200
   the registration sets; not a departure from a rule.
4. **`qwen3:8b` replay.** `hosted_screen.py replay` re-derived the reference
   records from the chat cache (no model call) so they gain
   `final_unevidenced`, as registered. The cached chat entries were not
   touched.
5. The `decide` subcommand was not used, as registered. Vetoes V1, V4 and V5
   were checked by hand against the stored scores using the thresholds of
   `screen_metrics.veto_reasons` (reference 2 of 82, 5.00, 0 of 50).

## What this does not conclude

- Nothing about private or non-English sources: the prose fixtures were not
  sent, and V3 was not evaluated.
- Nothing about the pipeline's other tasks, only concept extraction on
  meeting transcripts (AMI, pool DA).
- Not a statement about a provider: a model name on 2026-11-23. Aliases move,
  and the arms are pinned by date, reproducible only from their caches.
- Power is low (n = 2, 37 scored windows, five meetings). NOT DISTINGUISHABLE
  would have meant "not resolved", not "equal". `openai-gpt-4.1-mini`'s WORSE
  rests on an upper bound 0.0006 below 0.
- The unevidenced rate measures quoting, not truth: a paraphrased object can
  be accurate. G1 precision on the hosted arms (0.95 to 1.00 except for
  `gpt-5-mini` at 0.93) says the paraphrases are mostly supported by the
  window. #1768 decides whether paraphrase is a defect worth fixing.
- Reasoning and thinking budgets are provider defaults, so a reasoning arm's
  result mixes the model with its default effort.
- The selector judge ran on each arm's own model; the judge-coupling effect
  (#1571) is not separated, and it may explain part of 4.1-mini's low object
  count (40 judged out).
- Hosted arms produced 2 to 3 times the reference's object volume (three
  arms). Whether more objects is a defect or a recall gain is the V4 veto's
  question, not settled here.
- No hosted model becomes a default, and nothing here weakens the local-first,
  no-mandatory-cloud principle. A BETTER arm would have earned a follow-up
  registration on pool F; none did.
