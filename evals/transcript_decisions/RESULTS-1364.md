# Results: #1364, the decision pass on an unseen development series (TS3009)

**Verdict: NOT ADOPTED.** `transcript_decisions` stays off by default. Bars R, V
and F fail; P is vacuous; D passes. The pass recalled about as few gold decisions
as today's extraction (0.149 against 0.110), at about 15 times the Decisions, and
11 of its 60 meeting-runs failed, every one of them on TS3009a. Every
rule applied below is the one fixed in
[`PREREGISTRATION-1364.md`](PREREGISTRATION-1364.md) (merged in #1551 before any
run); no bar was moved.

## Run

- Dates: 2026-10-17 to 2026-10-18. Harness at `7830a11b` (the registration
  merge), clean tree, identical to `main` at the time.
- `qwen3:8b` (digest `500a1f067a9f`), decision-pass prompt hash
  `6d3962b0dd75650c`, AMI TS3009 a-d, gold `TS3009.decisions.adjudicated.csv`
  (29 rows: 26 `decision`, 2 `compuesta`, 1 `no_decision`).
- `pass`: 15 runs, **60 of 60 meeting-runs completed**, exit 0, no uncaught error.
  `baseline`: 15 runs, **60 of 60 completed**, exit 0. Gate
  `_is_meeting_shaped` true in 120 of 120.
- Compute time: `pass` 2.64 h of model time, `baseline` 5.73 h.

## Metrics (mean, min-max over 15 runs)

| Metric | `baseline` | `pass` |
|---|---|---|
| recall (of 26 `decision` rows) | 0.110 (0.038-0.269) | 0.149 (0.077-0.231) |
| gold decisions recalled, of 26 | 2.9 (1-7) | 3.9 (2-6) |
| secondary recall, of the 19 recallable rows (not a bar) | 0.151 | 0.204 |
| Decisions per run | 5.5 (3-11) | 83.8 (77-88) |
| runs with more than 81 Decisions, of 15 | 0 | 11 |
| matched_decision / matched_non_decision | 2.5 / 0.0 | 3.9 / 0.0 |
| matched_compuesta | 0.3 | 0.9 |
| unannotated | 2.7 | 79.0 |
| precision-on-annotated | 1.000 | 1.000 |
| would merge at ingest | 0 | 0 |
| meeting-runs with a `failure`, of 60 | 0 | 11 |
| LLM calls per run | 46.0 (46-46) | 39.2 (38-40) |
| wall time per run (s) | 1374.8 (1234.3-1474.8) | 633.1 (195.5-1004.8) |

## Bars

| Bar | Rule | Observed | Result |
|---|---|---|---|
| R | pass mean >= baseline mean + 0.25 (= 0.360), gap > larger spread | 0.149; gap 0.039 against spreads 0.231 and 0.154 | **FAIL** (both halves) |
| P | mean >= 0.60, no run < 0.50 | 1.000, lowest 1.000 | pass, **vacuous** (see below) |
| V | <= 81, mean and every run | mean 83.8; 11 of 15 runs above 81, highest 88 | **FAIL** |
| F | <= 3 of 60, zero uncaught | 11 of 60, 0 uncaught | **FAIL** |
| D | <= 20% merge | 0% | pass |
| C | reported | pass: 39.2 calls, 633 s; baseline: 46.0 calls, 1375 s | reported |

## Reading

- **R fails by a wide margin.** The gap is 0.039, about one gold row (0.038). The
  pass finds the same few decisions the baseline does. On ES2015 it recalled
  0.363 against 0.119; here the same prompt and model do not repeat that.
  The ES2015 result was on the series the prompt was revised on, which is the
  concern #1364 was filed for.
- **F is one meeting.** All 11 failures are `TS3009a` (11 of its 15 runs); b, c
  and d never failed. Each is `OllamaGenerationCapped` at about 490-516 s: the
  model reached the 8192-token generation ceiling and the one retry (#1361) hit it
  too. That is a runaway generation on a specific window, not a
  random 1-in-20 parse failure, and a retry of the same request does not leave
  it. Not tuned or changed here (a cap or prompt change needs its own
  registration).
- **V fails because the pass over-produces.** 83.8 Decisions per run, 79.0 of
  them matching no gold row. Unannotated is not a false positive (the gold covers
  only AMI's abstractive decision sentences), so this is not a precision claim,
  but it is the volume bar V exists to limit.
- **P and the gold's blind spot.** The gold holds one `no_decision` row and no
  `sin_soporte` rows, so a false positive against the gold was nearly impossible for
  either arm; 1.000 for both is a bar with almost no exposure, not evidence of
  precision.
- **The ceiling is lower than registered.** The registration counted 6
  unrecallable `decision` rows (empty evidence) and a cap of 20/26. The harness
  counts 7 of 26: it also finds `TS3009d.d02`, whose two acts are 3 tokens each
  and fall under the 4-token match floor. The cap is 19/26 = 0.731. The
  registered denominator (26) was used for every bar; the 19-row recall is
  reported above only.

## Caveats

- One series, four meetings, 26 `decision` rows: one row is 0.038 of recall.
  This is a single-series result, as ES2015 was; IS1004 is not adjudicated and
  remains unmeasured.
- Nothing here separates a prompt problem from a `qwen3:8b` problem on TS3009a.
- No `sin_soporte` and no `descartar` rows exist in the TS3009 gold.
- Stored runs (`evals/transcript_decisions/results/`, not tracked) hold only
  evidence lines, titles and `decision_id`s, as the harness always has.

## Recommendation

Keep `transcript_decisions` off. With ES2015 (R missed by 0.006 after the retry)
and now TS3009 (R missed by 0.22, plus V and F), the pass does not carry to a
series it was not tuned on. Further work belongs in its own registration: the
TS3009a runaway window (F), and whether the pass's volume (V) can be bounded
without losing the recall it has. IS1004 stays the remaining follow-up of #1364
once adjudicated. No default is changed here.
