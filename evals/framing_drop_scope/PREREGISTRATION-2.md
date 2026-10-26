# Pre-registration 2: how often the framing drop deletes a subject (#1575)

**Status: REGISTERED before any generation.** This file is merged to `main`
before the first model call. A change after that point is a new registration,
not an edit to this one. It generates no number itself; it fixes what will be
generated, how it will be read, and what each outcome means.

## Question

`_drop_framing_objects` (`extraction/concept.py`) removes every
non-`Procedure` object whose title names a meeting, on a framing-shaped
source. Its target is the container: an object that names the gathering
(#522/#533). The first measurement (`RESULTS.md`, on the records already
stored) found 1 subject deleted of 379 meeting-titled objects (a `Decision`),
and one container stub that exempting `Decision` would let back, which is
itself contested. It concluded "not enough evidence": one observation, and a
leak count of one that rests on a hand reading. #1575 stays open "pending more
framing-shaped runs".

This registration asks, on records the first measurement never saw: **does
the old rule delete subjects often enough, and does a narrower rule leak few
enough container stubs, to justify changing the rule?** It adopts nothing.
Production code and defaults are unchanged by this file and by the run; a
verdict of ADOPT opens a separate production change with its own spec, tests
and PR.

## What the first measurement consumed

- The `concept-screen`-format record files under
  `~/Dev/Projects/openkos-eval-runs/` (outside the repository, never
  committed): `concept-screen/records`, `concept-screen/records-pre-dedup-fix`,
  `judge-study/records`, `window-screen/records`,
  `runtime-screen/concept-replay/records` and
  `runtime-screen/concept-replay/records-pre-dedup-fix`. Layout:
  `<dir>/<model with ":" as "_">/<source>/r<run>.json`, one file per
  (model, source, run), carrying `stages.raw`, `stages.dedup`,
  `stages.drops`, the model digest and the per-window outcomes.
- Produced by `evals/concept_model_screen/run_concept_screen.py run`, which
  calls production's `extract_concept_union` on the 4,000-character windows
  production's `_chunk_lines` cuts, with every stage captured
  (`screen_pipeline.run_source`). Models: `qwen3:8b`, `qwen3:14b`,
  `qwen3.6:27b`, `qwen3.6:35b-a3b`, `gemma4:26b-a4b`, plus `qwen3:8b` on
  llama.cpp and MLX. Sources: the five development meetings of pool DA
  (ES2015a, ES2015d, IS1004b, IS1004d, TS3009c) and the five
  extraction-corpus prose fixtures.
- The tracked `evals/stage_attrition/results/*.jsonl` (Spanish notes, AMI
  TS3005a).
- Transcripts are read from `concept-screen/inputs/transcripts/`, the AMI
  archive from `concept-screen/inputs/ami/`.

Every stored AMI record is from pool DA. The population this file generates
shares no source with it.

## Population to generate

Corpora: the AMI meetings of `screen_data.POOL_F_WINDOWED` and
`POOL_F_MEETING_ONLY`, thirteen meetings in no thesis set: IS1002b-d,
IS1005a-c, TS3012a, TS3012b, TS3012d, IB4003, IB4005, IB4010, IB4011. None is
one of the thesis's 11 evaluation series or 18 reserve series. The guard is
`screen_data.ALLOWED_MEETINGS`, a fail-closed allowlist that refuses every
other meeting before a file is opened, and `_t_population_split` in the
harness self-test pins that `NEW_POPULATION` is inside it and disjoint from
pool DA and from the evaluation series. The extraction-corpus prose fixtures
are not generated again (they are already stored, and are a Spanish and
English notes population, not a framing-shaped meeting one).

Path: `run_concept_screen.py run` as-is, which is production's
`extract_concept_union` at the commit this file merges at, unpatched,
`think: false`, `num_ctx` 12288, `num_predict` 8192, seed = run number. No
runner script is added: the existing `run` subcommand already takes these
pools, writes the same record shape, and skips a record that exists.

Models, all already pulled; none is pulled or removed; digests are verified
with `ollama list` before the first call and recorded in each model's
`stamp.json`:

| Model | Why | Digest (#1557) |
|---|---|---|
| `qwen3:8b` | production default; the primary stratum | `500a1f067a9f` |
| `qwen3.6:35b-a3b` | fastest, and the highest meeting-titled yield stored (4.2 per AMI record) | `a7eb95c53bcf` |
| `gemma4:26b-a4b` | in the stored records, 4.0 per AMI record | `001e5dafc3c7` |

`qwen3.6:27b` and `qwen3:14b` are not run: 27b measured 53 s per window and
613 s per record (about 1.7 h per run set alone), for 2.1 meeting-titled
objects per record, and neither is a production default.

Unit: a **meeting-titled object** on a framing-shaped source, taken from the
`dedup` stage (what the rule sees), exactly as the first harness counts it.
Every AMI transcript is framing-shaped (`run_one` refuses a source that is
not meeting-shaped).

## n and the stopping rule

- **Block 1:** runs 1 and 2, for each model, pool `F` then pool `F-IB`.
  That is 13 meetings x 2 runs x 3 models = 78 records.
- **Block 2:** runs 3 and 4, the same.
- After each block ends (never mid-block), count the meeting-titled objects
  of the new population, `N_new`.
- **Stop** at the end of the first block with `N_new >= 400`, or after
  block 2, whichever comes first. Block 3 does not exist: if block 2 ends
  with `N_new < 400`, the run stops anyway and the verdict below is applied to
  what exists.
- The decision rule is **never consulted to stop.** The only inputs to the
  stopping rule are `N_new` and the block count, so a favourable or an
  unfavourable partial result cannot end the run early.
- A record whose run failed or timed out is excluded, as the harness does,
  and reported as "n failed of TOTAL". It is not re-run. A source or model
  whose records mostly fail is reported as it is.
- A one-block run that hits the 12 h wall-clock cap (below) stops there and
  applies the same rules to what exists; `N_new < 300` then makes the verdict
  INCONCLUSIVE by construction.
- Sampling note: runs 1-4 of one (model, source) use different seeds, so
  their objects are correlated (the same meeting yields similar titles). The
  units are counted per object, as in the first measurement, and the table
  also reports distinct (source, type, title) triples so a repeated title is
  visible, not hidden.

## Classification rule (fixed now)

The class of an object is not a model output and not a function of the arm.
For every meeting-titled `Decision`, `Project` or other non-`Event`,
non-`Procedure` object, read its `type`, `title` and `description` as stored
in the `dedup` stage, before looking at which model or run it came from
(`measure_framing_scope.py --list-unadjudicated` prints them), and assign:

- **CONTAINER** when the title or description is about the gathering itself:
  its occurrence, agenda, attendees, schedule, or a heading over unnamed
  items ("Decisions Made in Previous Meeting" with no decision named).
- **SUBJECT** when the description names a specific decision, task, outcome,
  product or person that would exist and make sense if the word "meeting"
  were removed from the title ("Assignment of Next Meeting Tasks": the work
  due before the next meeting).
- **CONTESTED** when the description meets neither test cleanly, or meets
  both (it names an item and also wraps unnamed ones), or only restates the
  title. A contested object is not decided by anyone. It is read **both**
  ways, and the verdict below uses each reading where it is the strict one:
  - the **container reading** is least favourable to a narrowing (the object
    is a leak under every arm that keeps it, and a deletion that is not a
    false positive), so ADOPT is judged under it;
  - the **subject reading** is most favourable to a narrowing (the object is
    a false positive of the old rule), so KEEP is judged under it.

  The already-stored `Decisions Made in Previous Meeting` is registered as
  CONTESTED (`CONTESTED` in the harness). Every `Event` is a container by
  definition; every `Procedure` is a subject and never dropped (#413).
- Classification is committed to the harness (`ADJUDICATION`, and `CONTESTED`
  when applicable) **before** `--decide` is first run, so a verdict is never
  computed on a half-adjudicated store. An unadjudicated object makes
  `measure` stop (`SystemExit`), so a new type or title cannot be scored by
  default.

## Arms

Unchanged from the first measurement, each a predicate over the object's type
applied to the same meeting-titled objects: `before` (everything but
`Procedure`), `opt1` (also exempt `Decision`), `opt2a` (drop only `Event`),
`opt2b` (drop `Event` and `Project`), and `shipped` (production's own
function, which must equal `before` while the rule is unchanged; a mismatch
invalidates the run).

## Decision rule (fixed before any data)

Evidence from the new population is the fresh sample. The stored population is
where the false positive was *found*, so it cannot confirm itself: it enters
only pooled with a new observation.

Let, for an arm A, `removed(A)` be the false positives of `before` that A no
longer drops, and `leaks(A)` the container stubs A keeps. "Distinct titles"
counts distinct object titles among the removed ones.

1. **ADOPT A** (A first in the order `opt1`, `opt2b`, `opt2a`, the smallest
   change first) when, **under the container reading**, all of:
   - the new population holds at least one false positive A removes;
   - pooled over stored and new, A removes at least 2, with at least 2
     distinct titles;
   - pooled `leaks(A)` is at most pooled `removed(A)`. A deleted subject is
     silent data loss and a kept stub is visible and curatable (#459's
     asymmetry), so one deletion is weighed at least as heavy as one stub.

   ADOPT is a recommendation to open a production change for exempting what A
   exempts. It changes no default.
2. **KEEP the current rule (record it as sound)** when `N_new >= 300`
   meeting-titled objects and, **under the subject reading**, the old rule
   deleted **no** subject from the new population. The recorded claim is
   exactly that: no deletion in `N_new` fresh objects, with a one-sided 95%
   upper bound on the per-object rate of `3 / N_new` (rule of three; 0.75% at
   400). The stored false positive stays on the record as unreplicated. It is
   not a proof that the rate is zero.
3. **INCONCLUSIVE** in every other case: `N_new < 300`; or a new false
   positive appeared but the pooled counts do not satisfy rule 1 (for
   example, the leaks outweigh); in which case #1575 stays open, the counts
   are reported, and any further generation is a new registration.

Rules 1 and 2 are mutually exclusive: a false positive under the container
reading is a false positive under the subject reading, so the new population
cannot both contain one and contain none.

No other outcome changes the rule. A different threshold, a different arm, or
a re-read of a classification after seeing the verdict is a new
registration.

### What this can and cannot show (assumptions, labelled)

- Assumption: the per-object false-positive rate is near the stored one, 1 to
  2 of 379 (0.26%-0.53%). At `N_new = 400` that is 1.1-2.1 expected false
  positives, so P(at least one) is about 65%-88% (Poisson), and P(none) about
  12%-35%. A KEEP is therefore weak evidence at this n (upper bound 0.75%,
  above the stored point estimate), and ADOPT needs luck at the low end of
  the rate. The registration accepts that: its job is to replace "one
  observation" with a count, not to guarantee a verdict.
- Assumption: 2.1 (`qwen3:8b`), 4.2 (`qwen3.6:35b-a3b`) and 4.0
  (`gemma4:26b-a4b`) meeting-titled objects per AMI record, the stored
  means for pool DA. Expected `N_new` is about 134 per run set of 13
  meetings across the three models, 268 after block 1 and about 536 after
  block 2. Pool F and F-IB are different meetings from DA, so the means may
  not transfer; if they do not, `N_new` is simply whatever it is.
- The judge (L10) and everything after the `dedup` and `drops` stages are
  not replayed, as before: a leaked stub may still be removed downstream.
- Types and titles are what these three models emitted on these meetings.
  Pooling three models mixes their type habits; per-model counts are reported
  beside the pooled ones.

## Wall-clock forecast

Source of the rates: `evals/concept_model_screen/RESULTS.md` (qwen3:8b
round 1 `1223 s` and round 2 `1697 s` on the 41 windows of pool DA, so 29.8
and 41.4 s per window; the 35b and gemma rates are the round 1 record walls,
`497 s` and `771 s` over the same 41 windows). Windows per run set, from
`concept._chunk_lines` on the stored transcripts: pool F 69, pool F-IB 45,
total 114.

| Model | s per window | Hours per run set (114 windows) | Block (2 run sets) |
|---|---|---|---|
| `qwen3:8b` | 29.8 to 41.4 | 0.94 to 1.31 | 1.9 to 2.6 |
| `qwen3.6:35b-a3b` | 12.1 | 0.38 | 0.77 |
| `gemma4:26b-a4b` | 18.8 | 0.60 | 1.19 |
| **All three** | | | **3.8 to 4.6** |

One block is about 3.8 to 4.6 h; two blocks about 7.6 to 9.2 h. Assumptions:
the per-window rate carries from pool DA to the free pool (DA windows are
4,000 characters like these); the rate already includes the per-meeting
participant capture and the judge call the pipeline makes (RESULTS.md notes
about 20% for them); a model swap costs under 2 min per load and there are
six loads per block (not included, about 0.2 h). Hard cap: 12 h of
generation. The first block's measured seconds per window replace these
rates for the decision to run block 2 (never for the stopping rule, which is
`N_new` alone).

## Exact commands

One Ollama job at a time: every command below runs to completion before the
next starts, and nothing else uses the server meanwhile. Run from the main
checkout, which has all five extraction-corpus prose sources (`run` refuses to
start unless 5 of 5 are present). Record the commit first.

```sh
cd ~/Dev/Projects/openkos
git rev-parse HEAD && ollama list && ollama ps

# Block 1
uv run python evals/concept_model_screen/run_concept_screen.py run --models qwen3:8b --pool F --runs 1 2
uv run python evals/concept_model_screen/run_concept_screen.py run --models qwen3:8b --pool F-IB --runs 1 2
uv run python evals/concept_model_screen/run_concept_screen.py run --models qwen3.6:35b-a3b --pool F --runs 1 2
uv run python evals/concept_model_screen/run_concept_screen.py run --models qwen3.6:35b-a3b --pool F-IB --runs 1 2
uv run python evals/concept_model_screen/run_concept_screen.py run --models gemma4:26b-a4b --pool F --runs 1 2
uv run python evals/concept_model_screen/run_concept_screen.py run --models gemma4:26b-a4b --pool F-IB --runs 1 2

# Count N_new (model-free, needs no adjudication), then run Block 2 only if N_new < 400:
OLLAMA_HOST=127.0.0.1:1 uv run python evals/framing_drop_scope/measure_framing_scope.py --count

# Block 2: the same six commands with --runs 3 4

# Model-free from here; OLLAMA_HOST is a closed port so it stays a check
OLLAMA_HOST=127.0.0.1:1 uv run python evals/framing_drop_scope/measure_framing_scope.py --list-unadjudicated
# classify each listed object by the rule above into ADJUDICATION, commit, then:
OLLAMA_HOST=127.0.0.1:1 uv run python evals/framing_drop_scope/measure_framing_scope.py --decide
```

`--decide` stops at the first unadjudicated object, by design; `--count` and
`--list-unadjudicated` never do. The stopping rule uses only the `N_new` line
`--count` prints (meeting-titled objects of framing-shaped records of the new
population, after de-duplicating records with an identical raw stage).

## Outputs

`RESULTS-2.md` in this directory: the commit and digests, records run, failed
and excluded, `N_new` per model, the arm table for the stored, new and pooled
populations under both readings, every non-`Event` object the old rule removed
with its class and origin, distinct (source, type, title) triples, the
verdict, wall-clock against this forecast, and the limits above. Nothing
AMI-derived beyond titles and counts is committed. The verdict updates
#1575's "done when" and nothing else.
