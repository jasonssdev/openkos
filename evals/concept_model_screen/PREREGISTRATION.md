# Pre-registration: choosing the concept-extraction model (#1557)

**Status: REGISTERED 2026-10-18, before any candidate run.** The rules below
are fixed for the screen and its confirmation. A change after this point is a
new registration, not an edit to this one. This file is merged to `main`
before the first candidate run, so its timestamp is public.

## Question

Which local chat model should the `extraction` task use for concept
extraction during ingest? Today it is `qwen3:8b` (ADR-0001, which compared
shape rather than quality).

This is a **screen**: it selects at most one model and names the top two for
the window-size stage (#1558). It adopts nothing. No default (`DEFAULT_MODEL`,
`DEFAULT_TASK_MODELS`, `_CHUNK_TARGET`, any prompt) changes because of it. An
adoption needs its own ADR, as ADR-0047 did for the judges.

**No winner means the baseline stays.** If no candidate passes the selection
rule, `qwen3:8b` stays, the result is recorded as it is, and the screen is not
re-run with tuned bars (the #1269 rule 4).

The full design is the first comment on #1557. Where this file and the design
differ, this file governs; every such difference is listed under
"Deviations from the design".

## Decisions taken in the issue

- **D1.** No latency gate. Latency is measured and reported.
- **D2.** The thesis's reserve series are not used.
- **D3.** `gpt-oss:20b` is the guardrail judge and is never a candidate (the
  harness refuses it as a candidate).
- **D4.** The top two models go to #1558.
- **D5.** AMI `actions` are excluded from the primary metric.

## Models

All already pulled in the local Ollama; none is pulled or removed. Digests are
the first 12 hex characters of each tag's manifest sha256 (what `/api/tags`
reports), read from disk at registration; every stored result also carries
its own stamp.

| Model | Role | Digest |
|---|---|---|
| `qwen3:8b` | baseline | `500a1f067a9f` |
| `gemma4:26b-a4b` | candidate | `001e5dafc3c7` |
| `qwen3:14b` | candidate | `bdbd181c33f2` |
| `qwen3.6:27b` | candidate | `bcbdbd4bcf86` |
| `qwen3.6:35b-a3b` | candidate | `a7eb95c53bcf` |
| `gpt-oss:20b` | judge only | `f38aa0c53da5` |
| `bge-m3` | embeddings only | `790764642607` |

Frozen configuration for every candidate: production `extract_concept_union`
(union judge path) at this registration's merge commit, unpatched;
`transcript_decisions` off (the default); `concurrent` off (the default);
`think: false`; `num_ctx` 12288 and `num_predict` 8192 (the shipped defaults);
model-default sampling. Run r is sent with `seed = r` (1..n); the production
retry of a failed window (attempt k) is sent with `seed = r + 1000k`, so it is
a fresh sample, as it is in production, and still reproducible. Only the
model tag varies.

## Data

- **AMI manual annotations 1.6.2** (CC BY 4.0), archive sha256
  `b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d`
  (the pin in `evals/decision_extraction/ami_public_manual_1.6.2.sha256`).
- **Transcripts**: the thesis renderer's speaker-turn markdown
  (`afg.corpus.render@7d479d2c692a`) and its turn index (`.jsonl`, character
  offsets plus dialogue-act ids per turn), copied out of the thesis checkout,
  which is read-only. The frontmatter is removed; the body is what the
  extractor sees. Every turn offset is checked against the body.
- **Allowlist** (`screen_data.ALLOWED_MEETINGS`, fail-closed, self-tested):
  ES2015, IS1004, TS3009 a-d; IS1002 b-d; IS1005 a-c; TS3012 a-d; IB4003,
  IB4005, IB4010, IB4011. Nothing else is opened: none of the eleven thesis
  evaluation series, none of the eighteen reserve series.
- **Stage A selection pool (DA)**: whole meetings ES2015a, IS1004b, TS3009c,
  ES2015d, IS1004d. 41 windows, 37 of them hold at least one assigned gold
  sentence (4, 9, 10, 7, 7). 97 gold sentences, 90 assigned to a window.
- **Prose fixtures** (secondary; V3): all five `examples/extraction-corpus/`
  fixtures. A run refuses to start unless 5 of 5 sources are present.
- **Confirmation pool (F)**: IS1002 b-d, IS1005 a-c, TS3012 a, b, d (69
  windows, 56 with gold) at window level; IB4003, IB4005, IB4010, IB4011 at
  meeting level (no `summlink`). TS3012c is not run (no `summlink`); it serves
  only as a foreign gold.
- **Stage 0 windows**: ES2015b window 2, IS1004c window 5, TS3009b window 7
  (1-based); development meetings outside DA, never scored.
- **Window list**: `windows.json` registers the sha256 of every window of
  every meeting above. A run whose windows differ is refused.

## Gold and its assignment

- Gold sentences G: the abstractive `abstract`, `decisions` and `problems`
  sentences (`actions` excluded, D5).
- **Assignment rule.** A sentence is assigned to the window holding the
  majority of its `summlink` dialogue acts (an act's window is the window
  containing its turn's first character); ties go to the earliest window; a
  sentence with no resolvable act is unassigned (it still counts at meeting
  level).
- **Foreign meeting** (the null control): same letter, the next series in the
  fixed cycle ES2015 → IS1004 → TS3009 → ES2015, and IS1002 → IS1005 →
  TS3012 → IS1002, skipping a letter that is not allowlisted; IB4003 → IB4005
  → IB4010 → IB4011 → IB4003. Only the foreign meeting's gold is read.

## Metrics

- **Units U**: `"<title>: <description>"` of each produced object. Bodies are
  excluded.
- `soft_cov(G, U)` = mean over g in G of the maximum bge-m3 cosine between g
  and any unit; 0 when U is empty (a failed or empty window covers nothing).
- **Primary: window-level coverage lift on the final output.** For a window w
  of meeting m: `lift(w) = soft_cov(G_w, U_w) - soft_cov(G_foreign(m), U_w)`,
  where G_w is the gold assigned to w and U_w the FINAL objects (after the
  judge and the backstop) that window w produced (attributed by object
  identity through the pipeline). Items are the windows with at least one
  assigned gold sentence. Per window, lift is averaged over runs; the paired
  delta is candidate minus baseline per window.
- **Reported beside it, not decided on**: meeting-level lift on all final
  objects (reask and participant-capture additions included) against all
  gold; the window-level lift on the raw replies; `cov_tau` own and foreign
  over tau 0.50-0.90; per-section soft coverage; the per-stage loss table.
- **Confidence intervals**: two-stage cluster bootstrap (meetings with
  replacement, then windows within each drawn meeting with replacement),
  10,000 resamples, seed 42, percentile 95% interval.
- **G1, source-grounded precision** (round 2 and confirmation only).
  `gpt-oss:20b` gets one window and the final objects that window produced and
  labels each `supported | partial | unsupported` (prompt in `screen_judge.py`;
  seed 0, one retry at seed 1000 on an unusable reply; a window unusable twice
  is excluded and counted). Precision = supported / all labelled. The paired
  statistic is the pooled precision difference (candidate minus baseline),
  bootstrapped as above.
- **Judge coverage** (finalists, reported only): one `gpt-oss:20b` call per
  meeting-run labels each gold sentence `covered | partial | not` by the
  meeting's final units; score covered = 1, partial = 0.5.

## Vetoes (every number fixed)

- **V1 failures.** A window fails when its call ended in the harness timeout,
  a generation cap, any other backend error after the production retry (the
  window was skipped), or a reply with no parseable JSON (an explicit `[]` is
  a valid empty reply, not a failure). Rate over all AMI window-runs of the
  round. Bar: rate ≤ baseline + 0.02 **and** ≤ 0.05 absolute.
- **V2 latency.** Reported only (D1): median first-attempt seconds per window
  call, seconds per meeting-run, total seconds.
- **V3 title language.** On `small-04` and `medium-10` (Spanish), the share
  of final titles whose production `_title_language` is a language other than
  the source's `_dominant_language`, pooled over runs. Bar: ≤ baseline + 0.02.
  The count of titles DROPPED by `_drop_wrong_language_titles` is reported.
- **V4 volume.** Mean final objects per AMI meeting-run within
  [0.5, 1.5] × the baseline's (inclusive).
- **V5 verbatim.** Share of final descriptions sharing a contiguous run of 12
  or more word tokens with the source. Bar: ≤ baseline + 0.10.
- **G1** (round 2 and confirmation): the 95% lower bound of the paired
  precision difference ≥ -0.05.

## Validity checks (before any candidate result is read)

Run on the baseline's round-1 output, after it is cached and before any
candidate's round 1 starts.

1. **Separation.** The baseline's window-level lift (bootstrap as above): the
   95% lower bound > 0 and the point ≥ 0.03.
2. **Planted deletion.** For every (meeting, window) that holds assigned gold
   and produced at least one final object, delete that window's final objects and recompute each gold
   sentence's best cosine against the meeting's remaining final units. Pass if
   the mean change for the sentences assigned to that window is ≤ -0.03 and
   below the mean change for sentences assigned to other windows.
3. **Matcher calibration (prose secondary only).** On the extraction-corpus
   adjudicated lists, (subject, alias) pairs are positives and (subject, facet)
   and (subject, out-of-scope) pairs negatives; the threshold is the lowest
   cosine reaching precision ≥ 0.95 (its recall is reported). The embedding
   subject recall then counts a subject when a title's best cosine to the
   subject or an alias reaches the threshold and exceeds its best cosine to any
   listed facet. Exact-match recall is reported beside it. If no threshold
   reaches 0.95, only exact-match recall is reported. This check never changes
   the primary metric.

**Fallback.** If check 1 or check 2 fails, the embedding lift is declared
non-informative and the primary becomes **window-level judge coverage**: per
window, `gpt-oss:20b` labels each assigned gold sentence covered / partial /
not by that window's final units (score 1 / 0.5 / 0); no foreign subtraction;
same pairing, bootstrap and rules. Every model in the round is then judged on
every window with gold.

Check 3 and stage 0 do not affect selection and may run before this file is
merged. Checks 1 and 2 need the baseline's round-1 run, which is made after
the merge.

## Procedure

**Stage 0 (calibration; no quality is read).**

- Abort test on `qwen3:8b`: T0 = three tiny calls on a warm model; D = one
  long generation, not aborted; the same long generation aborted by a 5 s
  client timeout; then T1 = a tiny call sent immediately. The server is
  recorded as stopping on abort when T1 < median(T0) + 0.25 × (D - 5 s).
- For each model: a warm-up call (loads it at `num_ctx` 12288), then the
  three stage-0 windows (seed 0, 900 s bound); then a `bge-m3` embedding call
  and `ollama ps` (memory with both resident). Two `gpt-oss:20b` G1 calls are
  timed.
- **Timeouts**, fixed from stage 0 before round 1: window calls
  `timeout_m = max(60 s, 4 × the model's median stage-0 window seconds)`;
  whole-source calls (unchunked prose extraction, the selector judge, re-ask,
  participant capture) `3 × timeout_m`. A timed-out call is a failure; the
  production retry of that call is NOT sent (it would cost another timeout),
  so the window is skipped exactly as production skips a window that failed
  twice. Before each model's first scored call a warm-up call loads it, so a
  cold load never lands inside a timed call.

**Round 1 (n = 1, seed 1).** All five models on pool DA plus the five prose
fixtures; the baseline first, then validity checks 1 and 2, then
`qwen3.6:35b-a3b`, `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`. A candidate is
dropped if it fails V1, V3, V4 or V5, or if the 95% upper bound of its paired
lift delta is < 0. Of the rest the top ⌈k/2⌉ by point delta are kept, at least
2 (k = 4 candidates, so 2 are kept).

**Round 2 (n = 3).** The survivors and the baseline add runs 2 and 3 (seeds 2
and 3; run 1 is reused from cache) on DA and the prose fixtures. G1 is run on
all three runs of these models; judge coverage is run for the report.

**Selection.** Among round-2 candidates holding V1, V3, V4, V5 (recomputed at
n = 3) and G1, the one with the best point delta whose 95% lower bound is > 0
is selected. None: the baseline stays.

**Top two for #1558** (D4): the two highest point deltas among the baseline
(delta 0 by definition) and the round-2 candidates that hold every veto and
G1; a tie keeps the baseline first.

**Confirmation (only if a model is selected).** The selected model and the
baseline, fresh runs on pool F at n = 3 (seeds 1-3), same configuration and
timeouts. Confirmed if the window-level point delta is > 0, V1 holds, and the
G1 lower bound is ≥ -0.05 on F. A sign flip is "not confirmed". The IB
meetings are scored at meeting level and reported, not decided on.

## Wall-clock forecast and the shortening rule

After stage 0, with s_m the model's median stage-0 window seconds and s_j the
median G1 call seconds:

- one DA run of a model = (41 windows + 3 × 5 meetings + 4 × 5 prose
  fixtures) × s_m window-equivalents;
- round 1 = Σ over five models; round 2 = 2 extra runs for the baseline and,
  before round 1 is known, the two slowest candidates;
- G1 and judge coverage = (3 models × 3 runs × 41 + 45) × s_j;
- confirmation = 3 runs × (69 + 3 × 9 + 45 + 3 × 4) × (s_baseline + s_slowest
  candidate) + 2 × 3 × 69 × s_j.

The forecast is redone after round 1 from the measured per-model seconds and
the actual survivors. **If the total exceeds 8 h**, these steps apply in
order, stopping at the first that brings the forecast to ≤ 8 h:

1. confirmation drops the IB meetings;
2. confirmation drops the TS3012 meetings;
3. confirmation runs n = 2 instead of 3;
4. round 2 runs the prose fixtures at n = 1 only (V3 is then read from run 1);
5. round 2 runs n = 2 instead of 3.

If the forecast still exceeds 8 h, the screen runs as reduced and the overrun
is reported. No other shortening is allowed.

Ollama serializes; one generation job runs at a time, and no `OLLAMA_*`
server setting is changed.

## Reported in the results

Per model: the lift delta and CI (window and meeting level), G1 precision as
n of total with its delta CI, failures by cause, latency, `ollama ps` memory,
objects per meeting, verbatim share, title language, prose subject recall
(exact and embedding), every tau curve own versus foreign, per-section
coverage; the validity-check outcomes; the halving trace; the selection
verdict; the top two; the per-stage loss table for #1559 (baseline and the
selected model, or the best round-2 candidate when none is selected); the
abort-test answer; wall-clock against forecast. Every rate as n of TOTAL and
both terms of every ratio.

## Per-stage loss table (#1559)

A rescore of the recorded stages of the real pipeline, no extra generation.
Stages on the chunked path: raw window replies; after the expansion strip and
`_dedup_merged`; after the deterministic drops (wrong language, framing,
source-title twins); after the re-ask and participant-capture additions;
after the 24-candidate ceiling; after the selector judge; after the 20-object
backstop (final). For each: meeting-level own and foreign soft coverage and
lift, own and foreign gold covered at cosine ≥ 0.70 (n of TOTAL), and
window-level lift. The difference between consecutive stages is that stage's
loss.

## Deviations from the design (and why)

- **Primary at window level on the final output.** The design pairs windows
  but leaves open which objects a window owns. The final objects are what
  ingest writes, so they are scored; the raw-reply window lift is reported
  beside it, so a model whose replies are good but whose pipeline loses them is
  visible.
- **No round-1 precision.** As in the design, G1 runs in round 2 only.
- **The cache key omits the openkos commit.** It holds the model digest, every
  message byte, the seed and the options. All scored runs are made at one
  commit (this registration's merge), which every result's stamp records.
- **Whole-source calls get 3 × the window timeout.** Their prompts are up to
  three times a window's; one bound for both would time out healthy judge
  and prose calls.
- **Prose fixtures: all five,** not four: V3 needs both Spanish fixtures.
- **Confirmation pool sized from the manifest** (69 + 45 windows, not the
  design's estimate of about 60), with the shortening ladder above.

## Expectations, stated before the run

- `qwen3.6:27b` is about 3x slower per window than `qwen3:8b` on the decision
  pass; it is the most likely cause of a forecast overrun.
- The larger models produced fewer items on the decision pass; V4 may bind.
- Topical credit is large (the thesis's cross-team control matched
  `qwen3:8b`'s real score at tau 0.60), so own-versus-foreign separation is the
  first thing that can fail.
