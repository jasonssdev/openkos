# Pre-registration: #1364, the decision pass on the second development series (IS1004)

**Status: REGISTERED 2026-10-27, before any run on IS1004.** The rules below
are fixed for those runs. A change after this point is a new registration, not
an edit to this one. This file is merged to `main` before the first run, so its
timestamp is public.

This is the remaining follow-up of #1364. It repeats
[`PREREGISTRATION-1364.md`](PREREGISTRATION-1364.md) (#1551, TS3009) on the
other development series; arms, configuration and bars are copied from it, not
re-decided. The TS3009 result is in [`RESULTS-1364.md`](RESULTS-1364.md)
(#1552): not adopted.

## Question

Does the transcript decision pass (`transcript_decisions`) recover
hand-annotated decisions, and stay within the #1348 bars, on IS1004, the one
development series left?

## Data

AMI IS1004 a-d only (development series, `scoring.DEV_SERIES`; thesis ADR-0005).
No evaluation or held-out series is read, here or in any step of this
registration.

Gold: `IS1004.decisions.adjudicated.csv` from the thesis checkout, **pinned by
SHA-256 `1b84ae083f1547fc6f356663fc80df9aa2f2aa978196406e50ae2e5e9e3cae7d`**
(37 rows: 27 `decision`, 5 `compuesta`, 2 `no_decision`, 2 `sin_soporte`,
1 `descartar`; the `decision` rows per meeting are a 3, b 6, c 7, d 11). The
hash is verified before the first run and again before results are written; a
different hash voids the run. The data stays in the thesis checkout, read-only;
nothing from it is copied here. Transcripts: the rendered `IS1004{a,b,c,d}.md`
of the same checkout.

### Deviation: the gold is model-adjudicated, not human-adjudicated

Unlike the ES2015 and TS3009 gold, **this adjudication was not made by a
person.** The owner authorized it and delegated it; every row's `adjudicator`
is `claude-opus` (Claude Opus). There is no second annotator and no
inter-annotator agreement. Consequence, stated before the run: every figure
here measures **agreement with a model's labels**, not with a human's, and
inherits that model's judgments and blind spots (what counts as a decision,
how a bundled sentence is split, which row is `sin_soporte`). The pass and the
baseline are extracted by `qwen3:8b`; the adjudicator is a different, larger
model, so the gold is not the extractor grading itself, but it is also not
independent human ground truth. Any claim drawn from this series is labelled
that way; it cannot by itself overturn a human-gold result.

### Unreachable rows, known before any run

- Four rows carry no AMI evidence text: `IS1004b.d04` (`decision`),
  `IS1004d.d05` (`descartar`), `IS1004d.d07` (`decision`), `IS1004d.d12`
  (`sin_soporte`). Matching is by evidence, so nothing can match them.
- Two of the 27 `decision` rows, **`IS1004b.d04` and `IS1004d.d07`, can
  therefore never be recalled.** They stay in the recall denominator (27), as
  the unrecallable rows did in #1348 and #1551. Recall is capped at 25/27 = 0.926
  by construction. The harness may count further rows as `unrecallable` (a
  decision whose every act is under the 4-token floor, as `TS3009d.d02` was);
  they are reported, the registered denominator (27) is used for every bar, and
  a secondary recall over the recallable rows is REPORTED, never used for a bar.
- The `descartar` and `sin_soporte` rows with empty evidence are outside the
  recall denominator and cannot be matched, so they cannot produce a false
  positive either. Bar P's exposure is the two `no_decision` rows and the
  `sin_soporte` row that have evidence; the results state how many false
  positives each arm could have made.
- Split children of `compuesta` rows (`b.d02-1..2`, `c.d01-1..2`,
  `c.d02-1..5`, `d.d01-1..2`, `d.d06-1..2`) are `decision` rows that share one
  evidence text with their parent and siblings; the shared-evidence credit rule
  of `README.md` (min of rows in the group and distinct matching Decisions)
  applies unchanged.

## Arms and frozen configuration

Copied from #1551:

- `pass`: **15 runs**, `--arm pass`, key on (the decision pass alone, matched
  through its evidence line), including the one-retry per failed window.
- `baseline`: **15 runs**, `--arm baseline` (today's extraction, key off),
  re-run on IS1004; no earlier baseline transfers.

Both arms: `qwen3:8b` (Ollama digest `500a1f067a9f`), decision-pass prompt hash
`6d3962b0dd75650c`, baseline prompt hashes as stamped in the stored runs, the
harness's generation ceiling (8192) and context window (12288), harness code at
this registration's merge commit. The matching rule and scoring are those of
`README.md`. No prompt, cap or matching-rule change. n = 15 is the floor on this
harness; a smaller arm is not a result.

**One flag differs from the harness default, to keep the configuration
frozen.** Since #1564 the harness skips the re-ask after a generation cap
unless `--retry-after-cap` is passed; the TS3009 run (#1551) retried, as
production does. The `pass` arm is run with `--retry-after-cap` so both series
measure the same procedure. The change since #1551 to `decision_pass.py`
(#1565) only records why a call failed; it does not alter requests or parsing.

Procedure, each arm (one Ollama job at a time, never two arms concurrently):

```bash
uv run python -u evals/transcript_decisions/run_transcript_decisions.py \
  --gold <IS1004.decisions.adjudicated.csv> --transcripts <thesis transcripts dir> \
  --arm pass --runs 15 --model qwen3:8b --retry-after-cap --results-dir <dir>
uv run python -u evals/transcript_decisions/run_transcript_decisions.py \
  --gold <same> --transcripts <same> --arm baseline --runs 15 --model qwen3:8b \
  --results-dir <dir>
```

A run counts only if it completes. Completed runs are reported as n of 60
meeting-runs per arm; an arm with fewer than 15 complete runs is re-run, never
averaged in its incomplete form.

## Decision rule

The #1348/#1361/#1551 bars, unchanged. No bar is moved, including R: the fixed
+0.25 floor is kept.

- **R** `pass` mean recall >= `baseline` mean recall + 0.25, AND the gap exceeds
  the larger of the two arms' min-max spreads.
- **P** precision-on-annotated mean >= 0.60, no run below 0.50. Exposure is
  small (see above); a vacuous pass is reported as such.
- **V** at most 81 Decisions, mean and every run.
- **F** `failure` set in at most 3 of 60 meeting-runs, and zero uncaught errors.
- **D** at most 20% of Decisions would merge at ingest.
- **C** calls and wall time reported, both terms.

### What decides "default on", and how the two series combine

The rule of #1551 is unchanged: a recommendation to flip `transcript_decisions`
to on requires ALL of R, P, V, F, D to pass on the series measured **and** the
other development results not to contradict it. TS3009 has already failed R, V
and F (#1552), and ES2015 missed R (#1361). Therefore:

- **If any bar fails on IS1004:** not adopted on either series; the key stays
  off; recorded as it is.
- **If every bar passes on IS1004:** this alone does **not** flip the default.
  TS3009 is a standing contradiction on a human-adjudicated gold, and this gold
  is model-adjudicated. The result is reported as "passes on IS1004, fails on
  TS3009, series disagree", the default stays off, and any flip needs a new
  registration (for example of the TS3009a runaway window and of volume V) and
  the maintainer's decision.
- No bar is pooled across series and no averaged verdict is created. The
  results may show the series side by side as a descriptive reading only.

This measurement changes no default.

## GPU forecast

From the TS3009 run (RESULTS-1364): `pass` 2.64 h and `baseline` 5.73 h of model
time, 8.4 h together, for 28,894 transcript words. IS1004 has 21,245 words
(0.735 of TS3009), so scaled by words: `pass` about 1.9 h and `baseline` about
4.2 h, **about 6.1 h in total, one Ollama job at a time**. Ceiling: 8.4 h (the
TS3009 total, which includes the `pass` arm's TS3009a runaway time). The results
report the observed time against this forecast. IS1004 has no known runaway
window, so `pass` may run faster than the scale suggests; that is not a reason
to cut runs.

## Expectations, stated before the run

- Baseline recall is low, as on ES2015 (0.119) and TS3009 (0.110).
- IS1004 is small (4 meetings, 27 `decision` rows): one gold row is 0.037 of
  recall; differences under that are noise by construction.
- Given TS3009, the expectation is that R and V fail again. This is a
  confirmation attempt on a second series, not a search for a pass.
