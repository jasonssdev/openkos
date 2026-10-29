# Pre-registration: a re-ask when the filtered reply is empty (#1690)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the
section "The owner confirms" and this file has merged to `main`, before the
first model call.** A change after that point is a new registration, not an
edit to this one. It generates no number itself: it fixes what will be
generated, how it will be read, and what each outcome means. Production code
and defaults are unchanged by this file and by the run. A verdict that licenses
the arm opens a separate production change with an OpenSpec delta, its own
tests and PR. Every bar marked **(proposed)** is the author's proposal until
the owner confirms it.

## Question

The bounded re-ask (`_add_reask_subjects`, `src/openkos/extraction/concept.py`,
#584/#642) fires only when the final, filtered list holds exactly one object
(`len(results) == 1`). A reply that ends with **zero** objects never triggers
it. #1642's measurement (`evals/framing_floor/RESULTS.md`, PR #1688) shows this
is a live case: `ES2015a-es` ends empty in 8 of 15 runs (`qwen3:8b`), and on
those runs the only objects removed were meeting containers (0 of 12 restorable
objects were subjects). Restoring removals cannot help; the subjects were never
emitted.

This registration asks: **when a long source ends with zero objects, does one
re-ask with a prompt that has no anchor title recover real subjects, without
letting containers or wrong-language titles through, at a bounded cost?** It
adopts nothing.

## What this does not touch

- Production code, prompts and defaults. The anchor-free prompt is defined in
  the harness (`EMPTY_REASK_SYSTEM_PROMPT`), never in `src/`.
- `_MEETING_SHAPED_TITLE_RE`, the language gate, the twin rule and the framing
  drop. They are not arms; the re-ask's additions are passed through them.
- The English population, other models, and any non-empty list: the re-ask only
  fires on an empty one, so every non-empty run is unchanged by construction.
- The judge, the ceiling and the backstop, which do not run in this population
  (`selector_judge` off, ADR-0080). Numbers are **stage-local**: after the
  source-title twin rule, the same cut as #1642.

## Arms

Both arms are read from **one** recorded raw stage per run, replayed through
production's own functions in production's chunked-path order (strip,
`_dedup_merged`, wrong-language gate, framing drop, source-title twins) by the
framing-floor harness's `replay_arms`. Nothing is reimplemented.

| Arm | Rule | Risk |
|---|---|---|
| `a0` | The shipped pipeline: an empty list stays empty, no re-ask. | The defect: zero objects on a long source (8 of 15 on `ES2015a-es`). |
| `a1` | When `a0` is empty and the source has at least `_REASK_LOW_YIELD_THRESHOLD` (2000) characters, one re-ask call with the anchor-free prompt; its reply is passed through the same production filters and what survives is the list. | A container or a wrong-language title surviving the filters; an invented subject; the cost of one more call. |

`a1` equals `a0` whenever `a0` is non-empty, and `summarize` counts a violation
(`invariant_violations`) that makes the run INVALID.

### The anchor-free prompt (`a1`)

`_build_reask_messages` names the kept object (`ALREADY KEPT (do not repeat):
<title>`), which does not exist on an empty list. The harness prompt keeps what
production's re-ask prompt established and changes only what the empty case
requires:

- It states that the first pass returned no usable object, discarded for naming
  the gathering itself or for a wrong-language title, and asks whether the body
  develops a distinct subject in its own right.
- It keeps `[]` as a correct and expected answer, with the same tie-break
  towards `[]` when unsure.
- It forbids returning the gathering itself under any name (meeting, session,
  discussion, agenda, attendees) and asks for a subject named by what it is
  about, not by the occasion.
- The user turn carries `_LANGUAGE_ANCHOR` and the source text, with no title
  and no kept-title line. The anchor is production's own text and is the
  configuration the meeting-shaped extraction turn uses (#522); the empty case
  is where wrong-language titles were dropped.
- The source text is bounded by production's `_bounded_prompt_source` with
  production's `_OPTIONAL_CALL_RESERVE_TOKENS`, as the re-ask does (#866).

The prompt is frozen by this registration. Tuning it after seeing a reply is a
new registration.

## Data

**Population (development series only).** The five Spanish machine
translations of pool DA: `ES2015a-es`, `ES2015d-es`, `IS1004b-es`, `IS1004d-es`,
`TS3009c-es`, from `~/openkos-eval-runs/retrieval-gold/translations/` (private
corpus, never committed; only counts, titles of added objects and verdicts are
published). The harness refuses any other source. Each file is passed whole,
title = the file stem, as #1642 ran. Model `qwen3:8b` (`DEFAULT_MODEL`) at
production defaults.

- **Stored runs (read-only).** The framing-floor records, runs 1 to 15 of every
  source (75 records, under `~/Dev/Projects/openkos-eval-runs/framing-floor`).
  Their raw stages give `a0`; they are not regenerated. `a0` is empty on 8 of
  the 15 `ES2015a-es` records and on none of the other 60.
- **Fresh runs.** `ES2015a-es` runs 16 to 30 (15 records), generated for this
  registration with the framing-floor harness's `run` path
  (`extract_concept_union`, `selector_judge` off, `transcript_decisions` off).
  They are the only runs this registration's B0 has not already seen.
- **Re-ask trials.** Every run whose `a0` is empty gets **3 independent
  re-ask samples** (seeds `run + 1000 x sample`, so no re-ask replays a
  generation seed). A trial is one (run, sample).

Only `ES2015a-es` is regenerated because the other four sources produced 0
empty lists in 60 stored runs: the re-ask cannot fire there. They stay in the
population so pooled rates are honest, and their count of re-ask calls is zero
by construction.

## Metrics

All counts are over the registered population; a trial is one re-ask sample.

- **zero (`a0`)**: runs whose replayed `a0` list is empty.
- **a1 expected zeros**: for each empty run, the share of its samples that
  still end empty, summed. A failed or unparseable call counts as still empty,
  as it does in production.
- **added**: objects an `a1` trial keeps after the production filters. Each is
  classified from type, title and description only (never its run or sample),
  before `decide` is first run, into:
  - **subject**: a specific idea, decision, artifact, procedure, person,
    organization, place, event or project that would make sense without the
    word "meeting" in the title;
  - **container**: about the gathering itself (occurrence, agenda, attendees,
    schedule, a heading over unnamed items); every `Event` that only describes
    the meeting is a container;
  - **wrong-language**: a title not in the source's language;
  - **contested**: neither or both. Counted against the arm, as in #1642 and
    #1575.
  The classification is committed to `ADJUDICATION` first; an unclassified
  added object stops `decide`.
- **slipped**: added objects classified container, contested or
  wrong-language.
- **cost**: re-ask calls made, failed calls, and wall-clock seconds per call.

## Bars

Applied in this order. One arm, one verdict.

- **B0, the defect exists** (precondition). `a0`'s zero rate on `ES2015a-es`,
  over runs 1 to 30, is at least **0.20** (reused from #1642 (proposed)). If
  not, the verdict is **NO-DEFECT** and nothing is licensed. Runs 1 to 15
  already read 8 of 15 = 0.533 (see "Disclosed before scoring"); the fresh runs
  are what this bar can still test.
- **B1, benefit.** The expected zeros of `a1` are at most **half** of `a0`'s
  zero count (proposed). At least half of the empty lists must end with a
  subject.
- **B2, safety.** The slipped objects across all trials number at most
  `max(1, floor(trials / 20))` (proposed: one per twenty re-ask calls). This is
  the allowance for "containers or wrong-language titles slipping through the
  filters", judged on the contested-against-the-arm reading.
- **B3, subject quality.** At least **half** of the added objects classify as
  subject (proposed), and at least one object was added. The owner adjudicates.
- **B4, cost.** The share of failed re-ask calls is at most **0.10**, and the
  mean seconds per call is at most the mean wall-clock of a full `ES2015a-es`
  extraction run in the same population (proposed). The call is bounded by
  construction (at most one per empty list, so at most the empty rate in extra
  calls); this bar checks that the call is not a runaway (#828) and costs less
  than the extraction it rescues.

Verdicts:

- **NO-DEFECT**: B0 not met.
- **INCONCLUSIVE**: B0 met but fewer than **6** empty runs exist, or more than
  15% of the records failed or timed out, or an empty run has no trial, or an
  invariant was violated. Reported, never rescued by more runs of the same
  population.
- **LICENSED**: B0 to B4 all met. This opens a production change with an
  OpenSpec delta for the re-ask trigger and the empty-case prompt; it is not a
  recommendation to change a default.
- **REJECTED**: B0 met, a bar failed. The failed bar is named.

## n and the stopping rule

- Memory rule: n = 15 is the floor for noisy extraction arms. `ES2015a-es` has
  30 runs (stored 1 to 15, fresh 16 to 30) and about 16 empty ones are
  expected at the stored rate, with 3 samples each.
- **Phase 1 (cheap, no generation).** Re-ask the 8 empty stored `ES2015a-es`
  runs, 3 samples each: 24 calls.
- **Futility stop, after phase 1.** If more than **75%** of phase-1 trials end
  empty (`score --max-run 15`, field `futility_stop`), stop: verdict
  **REJECTED on B1** (at 24 trials, at least 19 empty means half is out of
  reach). This is the only stop that reads an arm number, and it can only end
  a run towards REJECTED, never towards LICENSED. Phase 2 is never started.
- **Phase 2.** Generate `ES2015a-es` runs 16 to 30, then re-ask the new empty
  runs, 3 samples each.
- No other stopping rule. The decision rule is never consulted to stop early,
  extend, or re-run. A record or call that failed or timed out is not re-run;
  it is reported as "n failed of TOTAL".
- Samples of one run share a source and are correlated; the tables report
  distinct (type, title) pairs among the added objects so a repeated title is
  visible.

## Cost forecast

Sources of the rates: the stored `ES2015a-es` records (15 runs, 3109 s in
total, mean 208 s, range 97 to 379 s; #1642's note gives 100 to 725 s across
`ES2015a-es` and `ES2015d-es`), and the re-ask's measured per-call range:
roughly 40 s for a clean short reply to 225 s for a runaway reply at the
8192-token ceiling (#828). A re-ask is one whole-source call (15 456
characters, which the bound keeps whole in the 12 288-token window), not one
call per window.

| Step | Calls or runs | Low | Typical | High |
|---|---|---|---|---|
| Phase 1: 8 empty runs x 3 samples | 24 calls | 0.3 h | 0.6 h | 1.5 h |
| Phase 2a: generate `ES2015a-es` runs 16 to 30 | 15 runs | 0.4 h | 0.9 h | 3.0 h |
| Phase 2b: about 8 new empty runs x 3 samples | about 24 calls | 0.3 h | 0.6 h | 1.5 h |
| **Total** | | **1.0 h** | **2.1 h** | **6.0 h** |

Plus one model load (about 2 min). **Hard cap: 8 GPU hours**, after which the
run stops and the bars are applied to what exists (B0 and INCONCLUSIVE then
report their n). `score` and `decide` cost no GPU. Ollama serializes (a
default server does not run two jobs faster), so this is one job at a time. The
GPU is reserved by the owner: ask before the first model call.

## Exact commands

One Ollama job at a time; every command runs to completion before the next
starts. Record the commit and `ollama list` first.

```sh
git rev-parse HEAD && ollama list && ollama ps

# Phase 1: re-ask the stored empty ES2015a-es runs (no generation)
uv run python evals/reask_on_empty/measure_reask_on_empty.py reask \
  --sources ES2015a-es --runs 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15

# Futility check: read ONLY futility_stop
OLLAMA_HOST=127.0.0.1:1 uv run python evals/reask_on_empty/measure_reask_on_empty.py \
  score --max-run 15

# Phase 2 (only if phase 1 did not stop)
uv run python evals/reask_on_empty/measure_reask_on_empty.py generate \
  --sources ES2015a-es --runs 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30
uv run python evals/reask_on_empty/measure_reask_on_empty.py reask \
  --sources ES2015a-es --runs 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30

# Model-free from here; OLLAMA_HOST is a closed port so it stays a check
OLLAMA_HOST=127.0.0.1:1 uv run python evals/reask_on_empty/measure_reask_on_empty.py score
# classify every added_titles entry into ADJUDICATION, commit, then:
OLLAMA_HOST=127.0.0.1:1 uv run python evals/reask_on_empty/measure_reask_on_empty.py decide
```

`score` prints counts and the added objects to classify; it applies no bar.
`decide` stops at the first unadjudicated added object. Stored re-ask replies
are re-filtered through production's functions at score time, so the filters
are never baked into a record. The stamp (harness commit, model digest and the
hash of the prompt sent) is written under `reask/<model>/stamp.json`.

## Outputs

`RESULTS.md` in this directory: the commit, the model digest, records run,
failed and excluded, the zero table for `a0` and `a1`, every added object with
its class, distinct pairs, the verdict, the cost in calls and seconds against
the forecast, and the limits below. Nothing from the private corpus beyond
titles of added objects, counts and verdicts is committed. The verdict updates
#1690 and nothing else.

## Disclosed before scoring

- Seen before this registration: the framing-floor results for runs 1 to 15
  (`a0` empty on 8 of 15 `ES2015a-es` records, 0 of 60 elsewhere) and the titles
  of the objects removed on those runs. B0 on runs 1 to 15 is therefore already
  known to be met; fresh runs 16 to 30 are what it can still test.
- No re-ask reply exists for any run. No re-ask call was made, and the
  anchor-free prompt was not tried on any source before it was frozen here.
- The model-free replay of the population was run once, to check the harness
  reads the stored records: it reports 8 empty `a0` of 15 on `ES2015a-es` and
  75 records, 0 failed, matching the framing-floor result.
- The harness was developed test-first; its self-test is model-free
  (`evals/run_self_tests.py`) and was mutation-checked once: removing the
  framing drop from the additions filter turns three checks red.

## Limits (assumptions, labelled)

- Assumption: `qwen3:8b` at production defaults is the model that matters.
  Other models may empty a different set of sources. Not run.
- Stage-local. The replayed `a0` is empty in 8 of 15 `ES2015a-es` runs while the
  recorded production `final` stage was empty in 7 of 15 (a later production
  stage can add an object the replay does not model). This registration uses
  the replay, as #1642 did.
- The defect is confined to one source (`ES2015a-es`); a different Spanish set
  could give a different B0. The re-ask cannot be tested on the other four, where
  nothing is empty, so a licensed verdict would rest on one meeting.
- One corpus family; a machine-translated Spanish (gemma4:26b-a4b) of AMI
  English, development series only. The evaluation and reserve series are
  untouched.
- The three samples of one run are correlated, so the effective n for B1 is the
  number of empty runs, not the number of trials. The bars read both.
- Adjudication is a human judgment on type, title and description. Whoever does
  it is named in RESULTS; a re-read after the verdict is a new registration.
- The prompt is one wording. A rejected verdict rejects this wording on this
  source, not every possible second ask.
- Production would run the re-ask after the whole pipeline (including stages
  this population does not exercise). The additions are filtered here by the
  chunked-path filters only; a production change must re-check the order.

## The owner confirms

The owner confirmed every item below unchanged on 2026-10-29, before this
file merged and before any result existed: the bars as proposed, the futility
stop, the frozen `EMPTY_REASK_SYSTEM_PROMPT` wording, 3 samples per empty run
and 15 fresh runs, the GPU run with its 8-hour cap, and adjudication by a
delegated reader disclosed in RESULTS. The items are kept as they were
proposed.

1. The bars marked **(proposed)**: B0 reused from #1642 (0.20 on `ES2015a-es`);
   B1 at half; B2 at `max(1, floor(trials / 20))`; B3 at half subjects; B4 at
   0.10 failed calls and mean seconds not above a full extraction run; the
   INCONCLUSIVE floor of 6 empty runs and 15% failed records.
2. The futility stop (more than 75% of phase-1 trials still empty ends the run
   as REJECTED on B1).
3. The anchor-free prompt wording in `EMPTY_REASK_SYSTEM_PROMPT`, including
   its instruction not to return the gathering and its use of the language
   anchor, since it is frozen once the file merges.
4. 3 samples per empty run and 15 fresh `ES2015a-es` runs, rather than a
   different n or regenerating the other four sources.
5. GPU time: the forecast (1.0 to 6.0 h, typical 2.1 h) and the 8 h hard cap,
   and when the GPU is free.
6. Who adjudicates the added objects (the owner, per #1690, or a delegated
   agent that is disclosed in RESULTS).
