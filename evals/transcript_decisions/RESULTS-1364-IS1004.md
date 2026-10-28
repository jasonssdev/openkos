# Results: #1364, the decision pass on IS1004

**Verdict: NOT ADOPTED.** `transcript_decisions` stays off by default. Bar R
fails; P, V, F and D pass. The pass recalled more than today's extraction on
IS1004 (0.183 against 0.030) but 0.153 short of the +0.25 gap, and it found
about 5 of 27 gold decisions. Every rule applied is the one fixed in
[`PREREGISTRATION-1364-IS1004.md`](PREREGISTRATION-1364-IS1004.md) (merged in
#1660 before any run, copying the bars of
[`PREREGISTRATION-1364.md`](PREREGISTRATION-1364.md)); no bar was moved.

**Read this with the deviation below: the gold is a model's adjudication
(Claude Opus, delegated by the owner), not a person's.** Every figure measures
agreement with that model's labels.

## Run

- Dates: 2026-10-28. Harness and code at `7933fd68` (the registration merge,
  clean tree). Gold `IS1004.decisions.adjudicated.csv`, SHA-256
  `1b84ae083f1547fc6f356663fc80df9aa2f2aa978196406e50ae2e5e9e3cae7d`, verified
  by the run script before the first call. Development series only.
- `qwen3:8b` (digest `500a1f067a9f`), generation ceiling 8192, context window
  12288, decision-pass prompt as frozen in #1348.
- `pass`: 15 runs with `--retry-after-cap` (as registered), **60 of 60
  meeting-runs completed**, exit 0, no uncaught error. `baseline`: 15 runs,
  **60 of 60 completed**, exit 0. Gate `_is_meeting_shaped` true in 120 of 120.
- One Ollama job at a time; the GPU was idle before each arm.
- Stored runs live outside the repository (`~/openkos-eval-runs/is1004-1364/`),
  holding only evidence lines, titles and `decision_id`s.

## Metrics (mean, min-max over 15 runs)

| Metric | `baseline` | `pass` |
|---|---|---|
| recall (of 27 `decision` rows) | 0.030 (0.000-0.148) | 0.183 (0.074-0.259) |
| gold decisions recalled, of 27 | 0.8 (0-4) | 4.9 (2-7) |
| Decisions per run | 1.6 (0-3) | 44.0 (40-50) |
| matched_decision / matched_non_decision | 0.7 / 0.0 | 4.9 / 0.2 |
| matched_compuesta / matched_descartar | 0.0 / 0.0 | 0.0 / 0.0 |
| unannotated | 0.9 | 38.9 |
| precision-on-annotated | 1.000 (1.000-1.000) | 0.955 (0.667-1.000) |
| would merge at ingest | 0 | 0 |
| meeting-runs with a `failure`, of 60 | 0 | 1 |
| LLM calls per run | 37.0 (37-37) | 28.2 (28-29) |
| wall time per run (s) | 1049.9 (764.2-1390.8) | 132.1 (107.8-179.0) |

## Bars

| Bar | Rule | Observed | Result |
|---|---|---|---|
| R | pass mean >= baseline mean + 0.25 (= 0.280), gap > larger spread | 0.183; gap 0.153 against spreads 0.185 and 0.148 | **FAIL** (both halves) |
| P | mean >= 0.60, no run < 0.50 | 0.955, lowest 0.667 | pass; not fully vacuous (see below) |
| V | <= 81, mean and every run | mean 44.0, highest 50 | pass |
| F | <= 3 of 60, zero uncaught | 1 of 60 (`IS1004c`, `decision_pass: unparseable reply`, after the retry), 0 uncaught | pass |
| D | <= 20% merge | 0% | pass |
| C | reported | pass 28.2 calls, 132 s; baseline 37.0 calls, 1050 s | reported |

## Combined reading with TS3009 (descriptive; no pooled bar)

| | ES2015 (#1361, human gold) | TS3009 (#1552, human gold) | IS1004 (this, model gold) |
|---|---|---|---|
| baseline recall | 0.119 | 0.110 | 0.030 |
| pass recall | 0.363 | 0.149 | 0.183 |
| gap | 0.244 (bar 0.25) | 0.039 | 0.153 |
| R | miss by 0.006 | fail | fail |
| V | pass | fail (83.8) | pass (44.0) |
| F | pass after retry | fail (11/60) | pass (1/60) |

By the registered rule, IS1004 failing R means not adopted on either series and
no combination question arises. The series disagree on V and F (TS3009 failed
both, IS1004 passes both), which the TS3009a runaway window explains rather than
a change in the pass; they agree on R: on every series the pass beat the
baseline but never by the registered margin once it left the series it was tuned
on. IS1004 does not rescue the pass, and TS3009 is not contradicted.

## Reading

- **R fails by 0.13.** The pass gains about 4 gold rows over the baseline
  (4.9 against 0.8), which is a real, repeatable improvement over a baseline that
  almost never recalls anything here, but the registered floor asks for +0.25.
- **The baseline is nearly empty on IS1004** (1.6 Decisions per run, 0.8 gold rows
  recalled), so the gap is mostly the pass's own low recall (0.183), not a strong
  baseline. The pass finds about one in five gold decisions.
- **Volume is not the problem here.** 44 Decisions per run against 83.8 on TS3009,
  in meetings of fewer words (21,245 against 28,894), and one parse failure in 60.
- **P has some exposure here.** The gold holds two `no_decision` rows and one
  `sin_soporte` row with evidence; the pass matched a `no_decision`/`sin_soporte`
  row in 0.2 of Decisions per run (precision down to 0.667 in the worst run),
  the baseline in none. Both arms pass the bar.
- **Ceiling.** The 2 registered unreachable rows (`IS1004b.d04`, `IS1004d.d07`,
  no evidence) cap recall at 25/27 = 0.926. The harness counts 3 of 27 rows
  unrecallable by construction (the two empty-evidence rows plus one whose acts
  fall under the 4-token floor; the harness does not name it). The registered
  denominator (27) was used for every bar.
- **Baseline matches not recomputed.** Baseline bodies are not persisted, so the
  rescore keeps the matches made at run time (24 stored items). They come from
  the same rule, since no rule changed between run and report.

## Deviations

- **Model-adjudicated gold** (registered): all conclusions are agreement with
  Claude Opus's labels, with no second annotator. A human-gold re-adjudication
  could move R either way; nothing here establishes which.
- No other deviation from the registration. `--retry-after-cap` was used for
  `pass` as registered. Single series, four meetings, 27 rows: one row is 0.037
  of recall.

## Time against forecast

Forecast about 6.1 h (`pass` 1.9 h, `baseline` 4.2 h), ceiling 8.4 h. Observed:
`pass` 0.55 h (33 min), `baseline` 4.37 h, together 4.9 h, plus a 13 min wait for
the GPU between arms. The `pass` arm was faster than the word-scaled forecast
because IS1004 has no runaway window, as the registration anticipated.

## Recommendation

Keep `transcript_decisions` off. Two development series now fail R by the
registered margin, and ES2015 missed it narrowly. The pass helps recall over the
current extraction on every series but not by 0.25, and it recovers about one
gold decision in five. Any further work (raising recall, or revisiting the bar
itself) needs its own registration. This closes the #1364 follow-up; no default
is changed.
