# Pre-registration: choosing the concept-extraction window size (#1558)

**Status: REGISTERED 2026-10-19, before any run.** The rules below are fixed
for this screen. A change after this point is a new registration, not an edit
to this one. This file is merged to `main` before the first scored call, so
its timestamp is public.

## Question

What window size should concept extraction cut a chunked source into?
Today `_CHUNK_TARGET` is 4,000 chars, with no overlap
(`src/openkos/extraction/concept.py`). It was set on `qwen3:8b` probes of one
thesis meeting (#454), and #699 measured `chunk:8000` only on one fixture
(`medium-10`, n = 8, recall 0.80 → 0.69).

This is a **screen**: it selects at most one window size for the primary
model. It adopts nothing. No default (`_CHUNK_TARGET`, `num_ctx`, any
threshold, any prompt) changes because of it. An adoption needs its own ADR
and its own pull request, and is written only if the selection rule below
selects a size for the primary model.

**No winner means 4,000 stays.** If no arm passes the selection rule, the
result is recorded as it is, and the screen is not re-run with tuned bars
(the #1269 rule 4).

The design is the first comment on #1557, sections 4.2 and 4.3, and #1558's
body. Where this file and the design differ, this file governs; every
difference is listed under "Deviations from the design".

## Models

| Model | Role | Digest |
|---|---|---|
| `qwen3:8b` | **primary** (the shipped default; #1557 kept it) | `500a1f067a9f` |
| `qwen3.6:27b` | **exploratory** second model | `bcbdbd4bcf86` |
| `gpt-oss:20b` | judge only (G1, judge coverage) | `f38aa0c53da5` |
| `bge-m3` | embeddings only | `790764642607` |

Digests are the first 12 hex characters of each tag's manifest sha256 as
`/api/tags` reports them, read at registration; every stored result carries
its own stamp. All are already pulled; none is pulled or removed.

- **Why `qwen3:8b` is the primary.** #1557's registered rule named only
  `qwen3:8b` for this stage (`evals/concept_model_screen/RESULTS.md`, "Top
  two for #1558").
- **Why `qwen3.6:27b` is exploratory.** It was the only candidate kept through
  #1557's round 1, and it fails #1557's G1 rule, so it qualifies on no
  registered ground. It is run because the #454 one-object attractor was
  measured only on `qwen3:8b`, and a larger model may use a larger window
  differently (design 4.2). **Its results are reported and never drive an
  adoption**, whatever they show.

Frozen configuration: production `extract_concept_union` (union judge path)
at this registration's merge commit, unpatched except for `_CHUNK_TARGET`;
`transcript_decisions` off; `concurrent` off; `think: false`;
`num_predict` 8192; `num_ctx` 12288 unless the rule below raises it for an
arm; model-default sampling. Run r is sent with `seed = r`; the production
retry of a failed window (attempt k) is sent with `seed = r + 1000k`. These
are #1557's settings, so #1557's cached calls are valid here (below).

## Arms

`_CHUNK_TARGET` ∈ {**4,000** (the baseline arm), **6,000**, **8,000**}, plus
**12,000** behind the feasibility rule below.

- The arm is applied by patching `concept._CHUNK_TARGET` for the duration of
  each source's run. `_chunk_lines` reads it at call time (#699's inert-arm
  fix), and every run checks that production reported exactly the arm's
  window count; a mismatch aborts the run.
- `_MEETING_CHUNK_THRESHOLD` (12,000) and `_CHUNK_THRESHOLD` (18,000) are
  unchanged: every item is above the meeting threshold, so every item is
  chunked in every arm.
- **Registered windows** (`windows.json`: sha256 per window, no text): the
  pool DA meetings and the three stage-0 meetings at every arm. The 4,000
  arm's DA hashes are #1557's registered hashes; the self-test checks it. A run
  whose windows differ is refused.

| Item | 4,000 | 6,000 | 8,000 | 12,000 |
|---|---|---|---|---|
| ES2015a | 4 | 3 | 2 | 2 |
| IS1004b | 9 | 6 | 5 | 3 |
| TS3009c | 11 | 7 | 6 | 4 |
| ES2015d | 9 | 6 | 5 | 3 |
| IS1004d | 8 | 5 | 4 | 3 |
| **DA total** | **41** | **27** | **22** | **15** |
| `medium-10` (es) | 4 | 3 | 2 | 2 |

## Items

- **Pool DA** (#1557's stage A pool, thesis development series only): whole
  meetings ES2015a, IS1004b, TS3009c, ES2015d, IS1004d. Same transcripts
  (thesis renderer `afg.corpus.render@7d479d2c692a`), same AMI archive
  (sha256 `b56e5babb2496b8795deeeda7e71178d7fbc9963f94276cf2a3f4b56ebbc9f9d`),
  same allowlist (`screen_data.ALLOWED_MEETINGS`, fail-closed). No thesis
  evaluation or reserve series is opened.
- **`medium-10-reunion-plataforma`** (es, synthetic meeting,
  `examples/extraction-corpus/`), the one prose-corpus fixture the window
  size can reach: the other four take the unchunked path at every arm. A run
  refuses to start unless 5 of 5 corpus sources are present.
- No free-pool confirmation: #1558 registers none (the design's stage B has
  no confirmation step).

## Reuse of #1557's cache

#1557 ran `qwen3:8b` and `qwen3.6:27b` on DA at 4,000 with seeds 1 and 2, and
`medium-10` with seed 1, at the same configuration and the same extraction
code (the only `src/` commit since #1557's registration merge `ebbde16f` is
`3fac5767`, which touches the merge ledger in
`src/openkos/application/lifecycle.py`). Its call cache is copied
into this screen's runs directory. Those runs are **replayed with
`--replay-only`**: a sender that refuses any uncached call, so a miss stops
the run before anything reaches Ollama. The reuse is valid only if every
replayed call is a cache hit and every stage's (title, window) list equals
#1557's stored record; otherwise the 4,000 arm is re-run fresh at every seed
and the incident is reported. Run 3 (and `medium-10` runs 2 and 3) are new.
The 4,000 arm keeps #1557's timeouts (146 s and 213 s per window call), so a
replayed timeout means the same thing.

## Metrics

All definitions are #1557's (`evals/concept_model_screen/PREREGISTRATION.md`)
unless stated.

- **Units U**: `"<title>: <description>"` of each final object (after the
  selector judge and the backstop). Bodies are excluded.
- **Primary: paired meeting-level coverage lift.** For meeting m and arm a,
  `lift(m, a) = soft_cov(G_m, U) - soft_cov(G_foreign(m), U)` over ALL the
  meeting's final objects and ALL its gold (abstract, decisions, problems;
  `actions` excluded), averaged over runs. The paired delta of an arm is
  `mean over m of (lift(m, a) - lift(m, 4000))`, same model. Window size
  changes the windows, so arms are paired by meeting, never by window
  (design 4.2).
- **Confidence interval**: #1557's two-stage cluster bootstrap with one item
  per meeting (so it resamples the five meetings), 10,000 resamples, seed 42,
  percentile 95% interval. With five meetings this is coarse: a lower bound
  above 0 needs a consistent sign across meetings.
- **Reported beside it, not decided on**: window-level lift (each arm against
  its own `summlink` assignment, unpaired across arms); the `cov_tau` curves
  own and foreign; per-section coverage; the per-stage loss table; prose
  subject recall on `medium-10` (exact and embedding); G1 precision and its
  paired pooled delta; judge coverage and its paired delta (unless it became
  the primary); latency.
- **Fragmentation (#699's count, two forms).**
  - On AMI: each final object is assigned to its nearest own-meeting gold
    sentence when the cosine is ≥ 0.60 (the thesis's tau); redundant
    emissions = objects beyond the first on the same gold sentence, summed
    over meeting-runs. The guard reads the rate redundant / final objects.
  - On `medium-10`, #699's own definition: positions naming a subject (by
    exact title, curated alias or listed near-duplicate) that the same output
    already named, on the raw window replies and on the final objects.
    Reported per run.
- **Feasibility of `num_ctx`.** Every call records `prompt_eval_count`,
  `eval_count`, `done_reason` and the `num_ctx` it was sent with. Reported per
  arm: calls whose prompt plus reply exceeds `num_ctx`, the maxima, and the
  `done_reason` counts.

## Validity check M1 (before any arm other than 4,000 is read)

#1557's validity checks passed at window level, but this screen's primary is
meeting level, where #1557's baseline lift was only 0.0045 to 0.0077. So:

- **M1.** On `qwen3:8b`'s 4,000 arm, n = 3, the bootstrap of the per-meeting
  lift must have a 95% lower bound > 0.
- **Fallback.** If M1 fails, the embedding lift is declared non-informative
  for this screen and the primary becomes **meeting-level judge coverage**:
  one `gpt-oss:20b` call per meeting-run labels each gold sentence
  `covered | partial | not` by the meeting's final units (#1557's prompt,
  score 1 / 0.5 / 0); paired by meeting; same bootstrap and rules. Judge
  coverage is computed for every arm in any case, so the fallback costs no
  extra decision point.
- The primary chosen on `qwen3:8b` also applies to `qwen3.6:27b`.

## Vetoes (every number fixed; each arm against the same model's 4,000 arm)

- **V1 failures, weighted by characters.** A window fails as in #1557
  (harness timeout, generation cap, backend error after the production retry,
  or no parseable JSON). The rate is the share of transcript characters in
  failed windows over all DA window-runs: a failed 8,000-char window loses
  twice what a 4,000-char one does. Bar: ≤ the 4,000 arm + 0.02 **and**
  ≤ 0.05 absolute. The unweighted window rate is reported.
- **V2 latency.** Reported only.
- **V3 title language.** On `medium-10`, the share of final titles whose
  `_title_language` is not the source's `_dominant_language`, pooled over
  runs. Bar: ≤ the 4,000 arm + 0.02.
- **V4 volume.** Mean final objects per DA meeting-run within
  [0.5, 1.5] × the 4,000 arm's (inclusive).
- **V5 verbatim.** Share of final descriptions sharing a run of 12 or more
  word tokens with the source. Bar: ≤ the 4,000 arm + 0.10.
- **F fragmentation.** The AMI redundant-emission rate. Bar: ≤ the 4,000
  arm + 0.10.

## Selection

Among the arms other than 4,000 that hold V1, V3, V4, V5 and F, the one with
the best point delta of the primary whose 95% lower bound is > 0 is selected
(a tie keeps the smaller window). None: 4,000 stays. The rule is applied to
`qwen3:8b` (the decision) and, labelled exploratory, to `qwen3.6:27b` (never a
decision).

## Timeouts and `num_ctx` per arm (stage 0)

Before an arm's first scored call, stage 0 times three calibration windows
for that (model, arm): in each stage-0 meeting (ES2015b, IS1004c, TS3009b;
development meetings outside DA, never scored) the arm's window that holds
the midpoint of #1557's stage-0 window, extracted once at seed 0 with a 900 s
bound after a warm-up call.

- **Window timeout** = max(#1557's 4,000 timeout for that model,
  max(60 s, 4 × the arm's stage-0 median)): never tighter than the 4,000 arm,
  so a larger window is never failed by a bound sized for a smaller one.
  Whole-source calls get 3 × that. A timed-out call is a failure and its
  production retry is not sent (#1557's rule).
- **`num_ctx`** = 12288 (shipped) unless the arm's largest stage-0
  `prompt_eval_count` exceeds 5,000 tokens; then 16384 for that arm, recorded.
  It is a feasibility constraint, not an arm (design 4.2). The pipeline's own
  planning window for whole-source prompt bounds stays 12288 in every arm, so
  the judge and participant-capture prompts are identical across arms; only
  what is sent to Ollama changes.
- **Feasible** = every stage-0 call returned `ok` with `done_reason: stop`
  and prompt plus reply within the arm's `num_ctx`.

## Procedure and budget

Ollama serializes: one generation job at a time, nothing else generating
while this screen runs, no `OLLAMA_*` setting changed.

1. `qwen3:8b` stage 0 at 6,000, 8,000 and 12,000.
2. `qwen3:8b` 4,000: replay seeds 1-2 (`medium-10` seed 1) from #1557's cache
   with `--replay-only`; then run seed 3 and `medium-10` seeds 2-3. Judge
   (G1 and coverage) for runs 1-3. Score the 4,000 arm alone: **M1 fixes the
   primary.**
3. `qwen3:8b` 6,000 and 8,000, n = 3, DA plus `medium-10`; judge.
4. `qwen3:8b` 12,000, n = 3, **only if** its stage 0 is feasible and the
   forecast for its three runs is ≤ 1.5 h. Otherwise it is recorded as not
   run, with the reason.
5. Score and decide `qwen3:8b`.
6. `qwen3.6:27b` (exploratory), n = 2, arms 4,000 (seeds 1-2 replayed;
   `medium-10` seed 2 new), 6,000 and 8,000; no 12,000. It runs **only if**
   the forecast for its uncached work, stage 0 included, is ≤ 4.0 h of model
   time; the forecast is made from its stage 0 when that runs, and from
   #1557's timings before it. Otherwise it is recorded as not run.

**Forecast.** For model m at arm N, one DA run plus one `medium-10` run is

```
D_m × (Σ_DA windows_N × t_m,N) / (41 × t_m,4000)  +  P_m × (windows_m10,N × t_m,N) / (4 × t_m,4000)
```

with `t_m,N` the arm's stage-0 median, `t_m,4000` #1557's stage-0 median
(36.5 s and 53.4 s), `D_m` #1557's mean DA seconds per run at 4,000 (1460 s
and 2927 s) and `P_m` its `medium-10` seconds (173 s and 446 s). If the
per-call time grew exactly with the window count's shrinkage, every arm-run
would cost what a 4,000 run does: about 0.45 h for `qwen3:8b` and 0.94 h for
`qwen3.6:27b`. That gives about 3.2 h for steps 1-3, 1.4 h for step 4 and
3.9 h for step 6, before stage 0 replaces the assumption.

## Reported in the results

Per model and arm: the primary delta and CI, both forms of the primary
(lift and judge coverage) whichever was chosen, failures (characters and
windows, by cause), latency, objects per meeting-run, verbatim share, title
language, `medium-10` subject recall, both fragmentation counts, G1 precision
as n of total with its paired delta, the `num_ctx` feasibility table, every
tau curve own versus foreign, per-section coverage, the per-stage loss table,
the cache-reuse check, the selection trace, and wall-clock against the
forecast. Every rate as n of TOTAL.

## Deviations from the design (and why)

- **G1 is reported, not gated.** #1557 found G1 underpowered at this
  pipeline's volume: 72 labelled objects gave a precision CI about 0.29 wide,
  so a -0.05 floor on its lower bound cannot be cleared by any arm. The vetoes
  that gate are #1557's V1, V3, V4 and V5, plus the fragmentation guard.
- **V1 is weighted by characters**, because a failed window loses more at a
  larger size; the window rate is still reported.
- **V3 reads `medium-10` only.** `small-04`, the other Spanish fixture, is
  unchunked at every arm, so the window size cannot reach it.
- **A meeting-level validity check (M1) with a judge-coverage fallback.** The
  design's validity checks were window-level; the primary here is not.
- **The exploratory model runs at n = 2 and without 12,000**, to fit the
  4-hour budget for a result that cannot drive a decision; its 4,000 arm is
  free at n = 2.
- **Stage 0 per arm**, and timeouts never tighter than the 4,000 arm's: the
  design fixed timeouts from 4,000-char windows only.
- **The fragmentation guard is defined on AMI gold sentences** (nearest gold,
  cosine ≥ 0.60) and given a bar; the design named the count but no bar.

## Expectations, stated before the run

- #454's one-object attractor predicts that fewer, larger windows give fewer
  objects; V4 may bind at 8,000 or 12,000.
- #699 saw recall fall at `chunk:8000` on `medium-10` (0.80 → 0.69, n = 8);
  the same fixture is reported here.
- Fewer boundaries should mean less fragmentation, so F is not expected to
  bind.
- At meeting level the own-versus-foreign gap is small, so M1 may fail and
  the judge-coverage fallback may become the primary.
- The prompt at 12,000 chars is near 5,000 tokens (the system prompt is about
  8,300 chars and a 4,000-char window measured 2,670-2,970 prompt tokens), so
  12,000 may run with `num_ctx` 16384.
