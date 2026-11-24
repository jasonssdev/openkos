# Results: are the vetoed hosted arms' extra objects useful? (#1783)

**Verdict: the study is INCONCLUSIVE. The two judges agree too little
(pooled kappa 0.309, below the registered 0.6), so no arm verdict is read.
Every arm's V4 replacement rule is therefore INCONCLUSIVE, and each #1767
verdict stands as registered: VETOED (G1, V4 (replacement undecided)).
Nothing is adopted, no default and no shipped veto changes.**

Rules: [`EXTRA_OBJECT_PREREGISTRATION.md`](EXTRA_OBJECT_PREREGISTRATION.md),
merged in #1787 (`cb80fc80`) before any label or any new hosted call. The
rules were applied by `extra_object_value.py report`, not by hand and not
re-tuned. Population: pool DEV12 (twelve development meetings), runs 1 and 2.
Pool DA's records are the #1767 ones (2026-11-23); the seven new meetings were
run on 2026-11-24 (local control 2026-11-23 to 2026-11-24). Pool F, the
evaluation series and the reserve were not touched.

| Arm | V4 replacement | #1767 verdict under the replacement |
|---|---|---|
| `openai-gpt-5-mini` | **INCONCLUSIVE** (study-level, kappa 0.309) | VETOED (G1, V4 (replacement undecided)) |
| `anthropic-claude-haiku-5-5` | **INCONCLUSIVE** (study-level, kappa 0.309) | VETOED (G1, V4 (replacement undecided)) |
| `openai-gpt-6-luna` | **INCONCLUSIVE** (study-level, kappa 0.309) | VETOED (G1, V4 (replacement undecided)) |

Per registration ("How the outcome is stated"): V4 is neither confirmed nor
refuted for any arm; it stands as registered. The arms are also formally
INCOMPLETE under the registered rule (see "Deviations", item 1), which would
have decided nothing on its own either.

## Study-level numbers

| Quantity | Value |
|---|---|
| Items judged | 745 (173 + 300 + 187 arm items, 85 control) |
| Unlabelled (either judge) | 0 of 745 (DEGRADED needs more than 10%: not triggered) |
| Cohen's kappa, pooled over arms and control, three labels | **0.3088** (registered floor 0.6: **not met**) |
| Raw agreement | 63.8% |
| Study status | **INCONCLUSIVE** |
| Judges | `gpt-oss:20b` (`f38aa0c5...`), `gemma4:26b-a4b` (`001e5dafc...`), digests checked by `run`; judge prompt sha256 prefix `0981f896c894c34c` |

Disagreement is systematic, not noise. `gemma4:26b-a4b` labels roughly twice
as many items `redundant` as `gpt-oss:20b` on every arm, and almost all
disagreements are `gpt-oss` useful against `gemma4` redundant:

| Side | Items | `gpt-oss:20b` useful / redundant / trivial | `gemma4:26b-a4b` useful / redundant / trivial | Agreed | Disagreed | Kappa on this side (descriptive) |
|---|---|---|---|---|---|---|
| `openai-gpt-5-mini` | 173 | 137 / 35 / 1 | 88 / 85 / 0 | 108 | 65 | 0.245 |
| `anthropic-claude-haiku-5-5` | 300 | 233 / 67 / 0 | 144 / 155 / 1 | 191 | 109 | 0.290 |
| `openai-gpt-6-luna` | 187 | 136 / 50 / 1 | 90 / 96 / 1 | 115 | 72 | 0.249 |
| control `qwen3:8b` | 85 | 34 / 49 / 2 | 18 / 64 / 3 | 61 | 24 | 0.412 |

Arms only (control excluded), pooled kappa is 0.267. The disagreement is
concentrated in the `useful` / `redundant` boundary: of the 246 arm items the
judges split, 214 are `gpt-oss` useful and `gemma4` redundant, 30 the other
way round and 2 involve `trivial`.

## Agreed shares, reported as description only

No arm rule was read (the study is INCONCLUSIVE). These are the registered
quantities, computed by the harness's own two-stage bootstrap (twelve meetings
then objects, 10,000 resamples, seed 42, percentile 95%), for the record. They
are **not** a PASS or FAIL for any arm, and the control does not decide.

| Side | Agreed n | Agreed useful [95%] | Agreed redundant [95%] | Agreed trivial [95%] |
|---|---|---|---|---|
| `openai-gpt-5-mini` | 108 | 0.7407 [0.6061, 0.8545] | 0.2593 [0.1455, 0.3939] | 0.0000 [0.0000, 0.0000] |
| `anthropic-claude-haiku-5-5` | 191 | 0.7016 [0.5730, 0.8115] | 0.2984 [0.1885, 0.4270] | 0.0000 [0.0000, 0.0000] |
| `openai-gpt-6-luna` | 115 | 0.6696 [0.5495, 0.7900] | 0.3217 [0.2019, 0.4400] | 0.0087 [0.0000, 0.0407] |
| control `qwen3:8b` (reference) | 61 | 0.2295 [0.1017, 0.3922] | 0.7377 [0.5686, 0.8793] | 0.0328 [0.0000, 0.1250] |

- Every arm clears the 20-agreed-item floor (108, 191, 115), so INSUFFICIENT
  does not apply; INCONCLUSIVE does.
- The arm-minus-control useful difference is not printed by `report` when the
  study is INCONCLUSIVE and was not computed. The control's shares are above.
- The control is the reference's whole windowed output (85 objects over 24
  meeting-runs), judged `redundant` for 74% of agreed items. It is a
  descriptive reference, not a baseline: it is judged against its own other
  titles, which are the same few windows' objects.
- What the numbers do not say: the agreed subset is the 61% to 64% of items on
  which both judges concur. Because `gemma4:26b-a4b` is the stricter judge on
  `redundant`, the agreed set is not a random sample of the population.

## Population (pinned)

`population-pin.json`, written by the first clean `population --final`
build (harness commit `cb80fc80`, clean). The pool-DA part equalled the
registered table (the build refuses otherwise).

| Side | Final objects | No window (excluded) | With counterpart (excluded) | **Population** | At 0.80 | At 0.90 | Meeting-runs | sha256 of sorted keys |
|---|---|---|---|---|---|---|---|---|
| `openai-gpt-5-mini` | 250 | 74 | 3 | **173** | 164 | 176 | 24 | `9ad996196174a40a38fda0b072b18295748647e2290424a7bd7d069a24285a25` |
| `anthropic-claude-haiku-5-5` | 371 | 62 | 9 | **300** | 290 | 309 | 24 | `19dfe571c0beaae5e72f8a949ef64249afdc562724bca999964f253c34df90b4` |
| `openai-gpt-6-luna` | 249 | 56 | 6 | **187** | 175 | 191 | 24 | `ea6f76f9c8206465771bd361526823e0abb89f234854d5d2a40f7e26346fd462` |
| control `qwen3:8b` | 141 | 56 | n/a | **85** | n/a | n/a | 24 | `9bff0ae845d8b6ee7b391776ca8721cd4409d9044c9d4629cc45e6b9e302a200` |

The registered forecast was about 717 arm items and 101 control items; the
population is 660 arm items and 85 control items. The cache-only replay left
the same 9 unrecorded optional calls (the meeting Event call missing from nine
pool-DA `qwen3:8b` records), as registered. Hosted arms' run dates: 2026-11-23
(pool DA) and 2026-11-24 (new meetings), the two dates recorded in each
record's digest.

## Re-reading the #1767 verdicts

Order as registered: V1 and V5 hold, G1's lower bound fails for all three
arms. The replacement rule is INCONCLUSIVE for all three, so the third bullet
of the registered table applies:

| Arm | G1 lower bound (floor -0.05) | Re-read verdict |
|---|---|---|
| `openai-gpt-5-mini` | -0.1167, fails | VETOED (G1, V4 (replacement undecided)) |
| `anthropic-claude-haiku-5-5` | -0.0833, fails | VETOED (G1, V4 (replacement undecided)) |
| `openai-gpt-6-luna` | -0.0538, fails | VETOED (G1, V4 (replacement undecided)) |

No verdict flipped, as the registration said none could.

## Spend, calls and wall time

The three hosted ledgers are cumulative; the table subtracts the #1767 figures
(112 calls; $0.479312, $0.1386496, $0.1026882). The spend guard never stopped
a run.

| Arm | New calls | Call ceiling | New prompt tokens | New completion tokens | New spend USD | Spend ceiling USD | Cumulative ledger (calls, USD) | Hosted wall time |
|---|---|---|---|---|---|---|---|---|
| `openai-gpt-5-mini` | 160 | 250 | 568,579 | 269,589 | 0.6813 | 1.68 | 272, 1.1606 | about 62 min |
| `anthropic-claude-haiku-5-5` | 160 | 250 | 859,582 | 219,153 | 0.1955 | 0.68 | 272, 0.3342 | about 20 min |
| `openai-gpt-6-luna` | 160 | 250 | 565,496 | 161,614 | 0.1374 | 0.60 | 272, 0.2400 | about 23 min |
| **Total** | **480** | 750 | | | **1.0142** | 2.96 | | about 1.75 h |

Spend came in at 96% of the central forecast ($1.060) and 34% of the $3.00
owner ceiling. Calls were 160 per arm against a forecast of 162. Wall times
are the sums of the per-meeting-run times the harness printed.

Local GPU wall time, indicative (forecast about 3.7 h, plan 6 h):

| Step | Wall time |
|---|---|
| `qwen3:8b` extraction, 14 new meeting-runs | about 68 min (sum of per-run times 4,107 s) |
| bge-m3 embeddings, 998 texts | about 1 min |
| Both judges, 745 items each, one `run` (`gpt-oss:20b` first, then `gemma4:26b-a4b`; `gemma4` took the last 20 to 25 minutes) | 2 h 42 min (02:20:53 to 05:03:26) |
| **Total** | about 4 h 10 min (extraction 68 min, embeddings 1 min, judges 2 h 42 min) |

## Extraction outcomes

- All 14 new meeting-runs of every arm (and of the control) have a record; no
  ceiling stopped any run; no arm was UNAVAILABLE; no hosted window failed (every hosted window `ok`, every hosted record
  `failure None`). No re-run was made.
- Control `qwen3:8b` windows, new meetings: ok, empty, and 3 `unparseable`
  windows (TS3009b both runs, TS3009d run 2), cached and kept as model
  failures.

## Deviations from the registration

None changes a number or a rule.

1. **IS1004a is flagged as provider failure by the harness, wrongly.** After
   the extraction, `population` reported all three arms and the control
   INCOMPLETE: "IS1004a r1 and r2: 3 provider-failure windows". IS1004a is a
   3-window, 8,289-character source, which the production pipeline extracts in
   one whole-source call (`whole_source_extract`, then participant capture,
   selector judge and one other call; all succeeded in all eight records).
   With no window-level call the window outcome is `not_called`, which
   `hosted_screen.PROVIDER_ERROR_OUTCOMES` counts as a provider error. The
   same classification applies to the local control, which has no provider.
   No call failed. A re-run reproduces the same records from cache
   deterministically, so no re-run was made (the registration allows "at most
   two") and `population --final` was used to pin. Consequences: the
   registered text makes each arm INCOMPLETE, which `report` records under
   `incomplete`; the INCONCLUSIVE study status takes precedence and decides
   the outcome anyway. IS1004a's objects carry no window, so none of them is
   in the judged population, and the window-less count includes them. A
   follow-up could narrow `PROVIDER_ERROR_OUTCOMES` for whole-source records;
   that would be a new registration.
2. **Gitignored fixtures copied into the worktree.** `run` refuses to start
   unless the four gitignored prose sources are present; they were copied from
   the main checkout. No prose was sent (hosted runs refuse `--prose`), and
   none is committed.
3. **`budget` was run before the arms and refuses afterwards.** It printed the
   three registered command lines before any hosted call; once the ledgers
   moved it correctly refuses, so the cumulative spend above is read from the
   ledger files. The hosted runs used the registered flags with
   `<date of run>` = 2026-11-24 (the hosted runs started just after midnight
   on that date).
4. **Run sequentially.** Local extraction, then each hosted arm, then the
   judges; nothing ran concurrently.
5. **Judge reports.** `report` prints no arm intervals when the study is
   INCONCLUSIVE. The intervals, per-judge distributions and per-side kappa in
   this file come from the harness's own `share_interval` and `cohen_kappa`,
   called on the pinned population and the stored labels, as description.

## What this does not conclude

- **Not "the extra objects are useful", and not "they are noise".** The two
  judges disagree on 36% to 38% of arm items. The registration says a study
  below kappa 0.6 reads no arm verdict, and none is read.
- The disagreement has a direction: `gemma4:26b-a4b` calls `redundant` what
  `gpt-oss:20b` calls `useful`. Whether an object that restates another entry
  is "folded in" or "its own page" is exactly where the two models, and
  probably a human, differ. A human adjudication (declined by the owner for
  this study) or a different judge pair would be a new registration.
- `gpt-oss:20b` (OpenAI open weights) judged two OpenAI hosted arms; its useful
  shares are the highest on those. The decision rule only uses agreed items,
  but the same-vendor concern in the registration is not resolved by this run.
- Nothing about V4 as a rule: it neither stands nor falls on this evidence
  beyond "stands as registered". Nothing about G1 or the lift, which were not
  re-measured on the new meetings.
- Twelve meetings from three series, n = 2 runs, two provider dates per arm;
  a provider alias can move under a fixed name and the study cannot separate
  that from a meeting effect.
- No hosted model becomes a default, and nothing here weakens the local-first,
  no-mandatory-cloud principle.
