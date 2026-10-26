# Pre-registration: what to do when a whole-source call caps (#1607)

**Status: REGISTERED before any generation.** This file is merged to `main`
before the first model call. A change after that point is a new registration,
not an edit to this one. It generates no model number itself; it fixes what
will be generated, how it will be read, and what each outcome means. The
model-free exposure count it relies on (`count_exposure.py`) is already
committed and was run before this file was written; its output is in
"Exposure, measured now".

## Question

On the unchunked path of `extract_concept_union` (the `if not fans_out(...)`
branch, `src/openkos/extraction/concept.py`) a source is read by two whole-source
calls (`extracting pass 1/2`, `pass 2/2`). If either ends in
`OllamaGenerationCapped` the exception propagates and the whole source's
extraction is discarded. The chunked path retries a failed window once and
skips it (#1053); #714 named the unchunked asymmetry and lowered the meeting
threshold to 12,000 characters, which shrank the exposure without removing it.
Seen on `TS3012a`, `qwen3:8b`: two of two runs capped, 0 objects, with 4,678
prompt tokens of 12,288 and `eval_count` 8,192 of 8,192 (the reply, not a
thinking trace: `think` false, 0 thinking characters).

Which of three options does the evidence support?

1. **Fall back to the chunked path** for that source (windows plus the #1053
   retry-then-skip contract).
2. **Retry once with a different seed.**
3. **Keep all-or-nothing** and record it as a measured, accepted cost.

The ceiling is not on the table: it is doing its job (#422, #440), and a higher
one only lengthens the run-away.

It adopts nothing. No default, constant, prompt or production code changes
because of this file or its run. A winning option opens its own production
change with its own ADR (epic #1556).

## Rules inherited from the epic

Pre-register and merge before any run; tune only on allowed series; raw
generations stay outside the repository; a results PR adopts no default; a
verdict goes in `RESULTS.md` with every rate printed as `k of N`.

## Exposure, measured now (model-free)

`count_exposure.py` reads transcripts, prose sources and stored records. It
calls no model. Threshold fixed in its source before the counts were read:
a conditional cap rate is NEGLIGIBLE only if the Wilson 95% upper bound is
below **0.05** (one unchunked source lost in twenty); NOT_NEGLIGIBLE only if
the lower bound is at or above it; otherwise UNDETERMINED, which is a
finding, not a pass. The issue text already quoted "2 of 20" before this
threshold was written; the count below is a fresh, deduplicated one.

Run on 2026-10-26, `OLLAMA_HOST=http://127.0.0.1:1`:

- **Sources taking the unchunked path under production `fans_out`: 7 of 31**
  allowed sources. AMI: 3 of 26 (`IS1004a` 8,289 chars, `IS1005a` 9,172,
  `TS3012a` 11,174; each would cut into 3 windows). Prose: 4 of 5
  (`small-04` 7,628 chars / 2 windows, `medium-08` 13,044 / 4, `medium-09` 13,039
  / 4, `large-03` 16,948 / 5; `medium-10` fans out). Unchunked length:
  median 9,172 (AMI) and 13,041.5 (prose); max 16,948.
- **Stored whole-source cap rate** (deduplicated by generation: 169 distinct
  extraction records of 471 files, 302 duplicates): `qwen3:8b` 2 of 8
  (0.071..0.591, NOT_NEGLIGIBLE) and both are `TS3012a`, 2 of 2, so the rate
  is one source, not eight draws; `qwen3:8b` on llama.cpp 1 of 4
  (`prose-medium-09`, UNDETERMINED); `qwen3:8b` on MLX, `gemma4:26b-a4b`,
  `qwen3:14b`, `qwen3.6:27b`, `qwen3.6:35b-a3b` each 0 of 4 (UNDETERMINED:
  four draws cannot show a rate under 5%). 6 distinct unchunked sources are in
  the records.
- **Stored window cap rate** (first attempts, chunked records), the prior for
  option 1: `qwen3:8b` 3 of 474 (0.002..0.018); of those 3, the retry rescued
  2 and 1 was lost. `qwen3.6:27b` 0 of 86; `qwen3:8b` llama.cpp 0 of 86; MLX 0
  of 86; the others 0 of 45. Window caps (3 of 474) are far rarer than
  whole-source caps on the cell that caps (2 of 2), but the two are different
  populations, so the window rate is a prior, not a prediction.
- **Measured seconds** (non-cached): `qwen3:8b` capped whole-source call 260.7
  s (n=2), capped window call 254.4 s (n=4); completed unchunked source-run
  37.6 and 67.9 s (n=2, `IS1005a`); chunked source-run median 106 s (2
  windows, n=12), 220 s (3, n=15), 160 s (4, n=12), 218 s (5, n=11).

What this does and does not say. The problem is **not negligible on the one
cell that has events** and **cannot be called negligible or serious elsewhere**:
the records hold 8 `qwen3:8b` unchunked draws on 6 sources. That is why a
measurement is registered rather than option 3 asserted. Had the unchunked
exposure been 0 of 31, or the stored `qwen3:8b` rate 0 of 40, this file would
recommend option 3 and no run.

## Population

Allowed sources only. Meetings come from `screen_data.ALLOWED_MEETINGS`
(fail-closed, refuses every thesis evaluation and reserve series before a file
is opened); prose from `examples/extraction-corpus/sources` (all five present
or the run refuses). The 7 unchunked sources, re-derived at run time from
production `fans_out`, are the cells: `IS1004a`, `IS1005a`, `TS3012a`,
`prose-small-04-pre-build-skills`, `prose-medium-08-sdk-skills`,
`prose-medium-09-sdk-skills-notes`, `prose-large-03-skills-vs-tools`. If
`fans_out` classifies a different set at the run's commit, the run uses that
set and reports the difference.

Models, digests read from disk at the run and stamped into every record:

| Model | Role | Digest |
|---|---|---|
| `qwen3:8b` | primary: the model that caps | `500a1f067a9f` |
| `gemma4:26b-a4b`, `qwen3:14b`, `qwen3.6:27b`, `qwen3.6:35b-a3b` | secondary: does it generalize | as stamped |

Frozen configuration (the concept screen's, #1557): production
`extract_concept_union` (union judge path) unpatched except at the seams named
under "Arms"; `transcript_decisions` off; `concurrent` off; `think: false`;
`num_ctx` 12288, `num_predict` 8192; model-default sampling; per-call timeouts
from the stored stage-0 calibration (whole-source calls 3x the window
timeout, as in the screen). Run r is sent with `seed = r`; the k-th resend of
an identical prompt with `seed = r + 1000 k` (`screen_pipeline.RETRY_SEED_STRIDE`),
so a retry is a fresh sample, as it is in production where the seed is unset.
Ollama serializes: one job at a time, no `OLLAMA_*` setting changes, and the
run starts only when no other GPU job is using the server.

The motivating observations (`TS3012a`, `qwen3:8b`, seeds 1 and 2) are
reported but **excluded from every rate**: they were noticed because they
capped. Fresh seeds 11..20 are used.

## Arms and seams

All arms call the same `screen_pipeline.run_source` over the same
`ScreenBackend` and the same on-disk call cache (key = model, digest, messages,
seed, `num_ctx`), so identical prompts and seeds are identical generations.

| Arm | What runs | Seam |
|---|---|---|
| **A, current** | `extract_concept_union` as shipped | none; the screen's own path |
| **D, chunked baseline** | the same function with the source forced onto the chunked branch | `concept_mod.fans_out` patched to `True` for the call (all three module call sites; `ingest_service` imports its own name and is not on this path). Windows are production's `_chunk_lines` at the shipped `_CHUNK_TARGET` 4,000. |
| **B, fallback to chunked** (option 1) | run A; if it raises a generation-capped `BackendError`, call the function again with `fans_out` patched `True` on the same backend | harness-level; the second call re-enters production's chunked branch verbatim, including #1053 retry-then-skip. Its window prompts and seeds equal D's, so it replays D's cache entries; its cost is read from the recorded `wall_s` of those entries, not from elapsed time. |
| **C, seed retry** (option 2) | run A with the backend wrapped so that a capped `whole_source_extract` call is re-sent once, then the cap is raised if it recurs | harness-level wrapper over `ScreenBackend.chat`; other call kinds (judge, re-ask, participant) are not retried. The resend gets the next seed in the stride. |

**No production change is required for this measurement, and none is made.**
Why the seams are faithful: the unchunked branch has produced nothing when
pass 1 caps, and `_extract_once` is one `llm.chat` followed by pure parsing, so
a retry at the backend seam and a retry inside `_extract_once` give identical
outcomes; B re-enters the real chunked branch, so its behaviour is
production's, not a reimplementation. What the seams cannot observe: progress
messages, the `skipped_chunks` report field, and any design in which pass 1's
objects are kept when pass 2 caps (see "Not decided here"). If review prefers
an off-by-default flag so the arms run through the real function, the minimal
shape is a keyword on `extract_concept_union` whose default preserves today's
behaviour exactly, with no config key; it would be its own PR, merged before the
run, and this registration would then be amended before any generation.

Arm D is run only on cells where arm A produced at least one capped source-run
(every seed of that cell). B is evaluated only on A's capped source-runs.

### Integrity checks (before any result is read)

- **I1.** On every non-capped source-run, B and C final objects equal A's
  (same titles, types, descriptions in order). The wrappers act only on a cap,
  so a difference is a harness defect.
- **I2.** On every capped source-run, B's final objects equal D's for the same
  cell and seed.
- **I3.** Every record carries the model digest, seed per call, and the cache
  hit flag; any record missing them is excluded and counted.
- **I4.** `count_exposure.py --self-test` and the sweep pass at the run's
  commit.

A failed check voids the affected arm; nothing is decided from it.

## Metrics

Per arm, per cell, reported as `k of N`:

- **M1, sources lost**: source-runs ending in 0 objects because a cap raised.
- **M2, objects recovered**: on A's capped source-runs, the number of final
  objects B and C return.
- **M3, extra calls and wall-clock**: relative to A on the same source-run,
  summed from recorded call `wall_s` including cached entries; the capped
  call A already paid is common to every arm and is reported once.
- **M4, quality of recovered objects**: on AMI cells, the concept screen's
  window-level coverage lift (`screen_metrics.soft_cov`, bge-m3, gold =
  abstract + decisions + problems, foreign-meeting control), on final
  objects; on prose cells, the `extraction_cap` scorer's subject recall. Read
  against (i) D, the chunked baseline, and (ii) A's own uncapped source-runs of
  the same cell when any exist. When A caps on every seed of a cell there is no
  unchunked reference, and the cell is scored against gold only.
- **M5, hazard**: `E` = capped A source-runs on fresh seeds, `N1` = fresh A
  source-runs, split by cell and by pass (1 or 2), with the Wilson interval
  and `count_exposure.verdict`.
- **M6, window prior** (arm D): first-attempt window cap rate and windows lost
  after the retry, on the cells where D ran.

No precision judge (`gpt-oss:20b` G1) is run: recovery is judged on coverage and
counts. This is a stated limitation.

## n and stopping rule

- **Stage 1, `qwen3:8b`**: 7 cells x seeds 11..20 = 70 A source-runs, in two
  blocks (seeds 11-15, then 16-20).
  - After block 1, if `E` = 0 of 35, stage 1 stops: the stored hazard did not
    reproduce on fresh seeds.
  - Saturation: a cell that caps on 5 of its first 5 seeds takes no further
    seeds (further caps add cost, not information); it is reported `5 of 5`.
  - Arms D, B, C run on cells with at least one A cap, on the seeds A ran.
- **Stage 2, secondary models**: A only, the 7 cells x seeds 11, 12 = 14 source-
  runs per model. A model that caps at least once gets the stage-1 ladder on
  its capped cell(s), 5 seeds, within the budget below.
- No other stopping, no optional extension, no re-run with a changed bar.

## Decision rule (fixed before data)

Let `E` and `N1` be stage 1's counts for `qwen3:8b`; `S2` = capped source-runs
that C recovers (at least one object) / `E`; `S1` = capped source-runs that B
recovers / `E`; `L` = D's windows lost after retry / D's first-attempt windows,
on the cells where D ran. Intervals are Wilson 95%.

1. **Integrity.** Any of I1-I4 fails: harness defect. No decision; fix and
   register a replacement.
2. **Hazard.** `verdict(E, N1)` is NEGLIGIBLE: **option 3 holds.** Report the
   rate as the accepted cost. Stop.
3. **Assessable.** `E < 10` after the stopping rule: remedies are not
   assessable. **Option 3 holds by default**, reported as "hazard measured,
   remedies unmeasured"; no option is recommended.
4. **Option 2 viable** iff the lower bound of `S2` is >= 0.50.
5. **Option 1 viable** iff the lower bound of `S1` is >= 0.80 and the point
   estimate of `L` is <= 0.10.
6. **Choice.**
   - Neither viable: **option 3 holds**, with the measured loss rate.
   - Exactly one viable: it is the candidate.
   - Both viable: if on source-runs both recover, M4 favours one by more
     than 0.05 coverage lift (paired mean), it is the candidate; otherwise
     the one with the lower median M3 wall per capped event; a tie goes to
     option 2 (the smaller change).
7. **Scope.** If 80% or more of `E` comes from one cell, the candidate is
   reported as scoped to that cell's shape, and the ADR must weigh that.
8. **Adoption.** A candidate is not an adoption. The results PR changes no
   default; a production change and its ADR are separate.

What keeps option 3: a negligible hazard (2); too few events to assess a fix
(3); neither remedy clearing its bar (6); a failed integrity check (1, no
decision, status quo by default).

## Forecast

Unit seconds are measured (see "Exposure, measured now") except where marked.

- **Stage 1 expected.** *Assumption:* only `TS3012a` caps on fresh seeds, every
  seed, as in the stored 2 of 2. A: 60 uncapped x 68 s (the larger of the two
  measured completed source-runs) + 10 capped x 261 s = 6,690 s. D on `TS3012a`:
  10 x 220 s (the 3-window median) = 2,200 s. C: 10 retries, *assumed to cap
  again* at 261 s = 2,610 s. B: replays D from cache, no new generation.
  Stage 1 = 11,500 s = **3.19 h**.
- **Stage 2.** 14 source-runs x measured completed unchunked source-run median
  per model (gemma4 28.3 s, qwen3.6:35b-a3b 42.4 s, qwen3:14b 136.1 s,
  qwen3.6:27b 202.6 s; n=4 each) = 5,732 s = **1.59 h**.
- **Expected total: 17,232 s = 4.79 h.**
- **Ceiling with the saturation rule** (*assumption:* every cell saturates at 5
  seeds): A 35 x 261 s + D 5 x 1,304 s (the 7 cells' 2/3/3/3/4/4/5-window
  medians summed) + C 35 x 261 s = 24,790 s, plus stage 2 = **8.48 h**.
  Without the saturation rule the ceiling would be 15.4 h, which is why the
  rule exists.
- Embedding calls for M4 (bge-m3) are small and not forecast. If the expected
  total exceeds 8 h when measured per-model seconds are updated after block 1,
  stage 2 drops to seed 11 only; no other shortening.

Budget: local GPU only, no spend, no external service. Storage outside the
repository.

## Exact commands

Run from the repository root. Raw generations and records live in
`~/Dev/Projects/openkos-eval-runs/whole-source-cap/` and are never committed.

```
# 1. Exposure and priors (model-free; exists now)
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/whole_source_cap/count_exposure.py
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/whole_source_cap/count_exposure.py --self-test

# 2. Stage 1, block 1 (qwen3:8b; arm A on all 7 cells)
uv run python evals/whole_source_cap/run_whole_source_cap.py run --arm A \
  --models qwen3:8b --seeds 11 12 13 14 15

# 3. Arms D, B, C on the cells that capped, same seeds
uv run python evals/whole_source_cap/run_whole_source_cap.py run --arm D --capped-cells-only \
  --models qwen3:8b --seeds 11 12 13 14 15
uv run python evals/whole_source_cap/run_whole_source_cap.py run --arm B --models qwen3:8b --seeds 11 12 13 14 15
uv run python evals/whole_source_cap/run_whole_source_cap.py run --arm C --capped-cells-only \
  --models qwen3:8b --seeds 11 12 13 14 15

# 4. Block 2 and stage 2: the same, seeds 16..20; then arm A on the secondary models, seeds 11 12
# 5. Score and decide (model-free: embeddings are the only model, bge-m3, local)
uv run python evals/whole_source_cap/run_whole_source_cap.py score
uv run python evals/whole_source_cap/run_whole_source_cap.py decide
```

`run_whole_source_cap.py` is not in this PR. It lands in a second PR, merged
before step 2, with its own `--self-test`, and implements exactly the arms,
seams, integrity checks and decision rule above; a diff to this file in that
PR is a new registration. Its subcommands and flags are the ones shown.

## Not decided here

- **Pass 2 caps after pass 1 succeeded.** Today the whole source is discarded
  including pass 1's objects. Keeping pass 1's objects is a fourth option, not
  in the issue; M5 reports how often a cap lands in pass 2 so the question can
  be asked with data.
- Which model production users run. `qwen3:8b` is the default and the only one
  with events, so it is primary; the others bound generalization.
- Whether 5% is the right product line for "negligible". It is fixed above so
  the result cannot move it.
