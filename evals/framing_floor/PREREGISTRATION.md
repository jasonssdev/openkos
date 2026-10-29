# Pre-registration: a floor under the framing drop (#1642)

**Status: PROPOSED. It becomes REGISTERED when this file merges to `main`,
before the first model call.** A change after that point is a new
registration, not an edit to this one. It generates no number itself: it fixes
what will be generated, how it will be read, and what each outcome means.
Production code and defaults are unchanged by this file and by the run. A
verdict that licenses an arm opens a separate production change with its own
spec, tests and PR. The owner confirmed every bar marked **(proposed)**
unchanged on 2026-10-28, before this file merged and before any result existed.

## Question

`_drop_framing_objects` (`extraction/concept.py`) removes every non-`Procedure`
object whose own title matches `_MEETING_SHAPED_TITLE_RE` on a framing-shaped
source. It has no single-object floor, deliberately: on meeting material the
model's first object was the container itself (49 of 59 AMI runs at position
1, 27 of 49 stored collapses were exactly the container, #522), it won the
retained prefix at every cap value (#533), and a reply containing only it must
yield `[]`, "honest, where keeping the container stub would store framing as
knowledge".

#1642 reports the same rule emptying a Spanish source. Three real runs of
`ES2015a-es` on `6594e6be` (default models, `--auto`): 2 of 3 ended with zero
objects. In both, the wrong-language gate removed only English titles and
`_drop_framing_objects` then removed the two Spanish survivors
(`Reunión de desarrollo del nuevo producto`, `Reunión de planificación del
proyecto`); the third run kept topical Spanish titles and stored 3 objects.

This registration asks: **how often does a Spanish meeting end with nothing,
and does a floor recover it without bringing back what #522/#533 removed on
purpose?** It adopts nothing.

## Why the rule has no floor, and what that implies

The floor was refused because the case it would act on is the one #522
measured: a reply that collapsed to the container. Any floor therefore trades
the same two outcomes the rule was written to choose between, an empty source
(honest, silent loss of whatever else the source held) against a stored
container stub (visible and curatable, but framing stored as knowledge). The
sole-object re-ask (#584) fires on exactly one surviving object; whether a
floor-kept stub would trigger it is not measured here (see Limits).

## What this does not touch

- The English population. It is not re-generated. #1575's records are
  replayed for the guard only.
- Any change to `_MEETING_SHAPED_TITLE_RE`, the language gate or the prompt.
  They are not arms; see "Arms not registered".
- The judge, the re-ask, the ceiling and the backstop. Their input depends on
  which arm ran upstream and their reply to a changed input is never
  generated, so every number here is **stage-local**: after the source-title
  twin rule, the last deterministic drop (the same cut as #1572/#1575).

## Arms

All arms are replayed from **one** recorded raw stage per run, through
production's own functions in production's chunked-path order (strip,
`_dedup_merged`, `_drop_wrong_language_titles`, `_drop_framing_objects`,
`_drop_source_title_twins`). Nothing is reimplemented. The arms are
harness-side; no `src/` behavior changes.

| Arm | Rule | Risk (what it brings back) |
|---|---|---|
| `a0` | The shipped pipeline. | Zero objects on a Spanish meeting whose survivors are meeting-shaped (#1642). |
| `a1` | If `a0` ends empty, the framing drop is not applied; its removals are restored. Same philosophy as `_drop_source_title_twins`' and the language gate's own single-object floors. | The container stub #522/#533 removed: framing stored as knowledge, on exactly the replies that collapsed to it. |
| `a2` | If `a0` ends empty, the wrong-language gate is not applied; the framing drop still is. | Language leak (#618): English-titled objects, a permanent wrong-language Concept ID, on a Spanish source. Topical, not framing. |

Every arm equals `a0` whenever `a0` is non-empty. `invariant_holds` checks it
on every case; a violation makes the run INVALID, because a floor that changes
a source that survived is not a floor.

## Arms not registered (and why)

- Narrowing `_MEETING_SHAPED_TITLE_RE` or exempting a type (`Decision`,
  `Project`, `Event`): that is #1575's territory (opt1, opt2a, opt2b), already
  measured on the English population, still open. The 2 survivors in #1642 are
  `Reunión de ...` titles, which `Event`/`Project` exemptions would keep or
  drop depending on a type this issue does not record.
- A title-language anchor or a prompt change: #563 rejected the anchor (leak
  0.69 to 0.63, inside noise) and a prompt change is a different mechanism that
  changes upstream generation, which a replay cannot see.
- Keeping the survivor through the re-ask and re-applying the drop after it:
  the re-ask is a model call, so it is not replayable and would need a
  non-default production flag. It is named as the follow-up if `a1` clears
  every bar but B2.

## Data

**Spanish (generated).** The Spanish machine translations of pool DA, the
thesis's development series: `ES2015a-es`, `ES2015d-es`, `IS1004b-es`,
`IS1004d-es`, `TS3009c-es`, from `~/openkos-eval-runs/retrieval-gold/translations/`
(private corpus, never committed; only counts, titles of restored objects and
verdicts are published). The harness refuses any other source. Each file is
passed whole, as ingest reads it, with title = the file stem (`ES2015a-es`),
which is how #1642 ran. The model is `qwen3:8b`, production's default
(`DEFAULT_MODEL`), at production defaults: `extract_concept_union` with
`selector_judge` off (ADR-0080) and `transcript_decisions` off, via the
screen's `run_source`, which records every stage from the real run.
Windows per source (`_chunk_lines`, measured): 4, 9, 10, 9, 12 = 44.

**English (stored, model-free).** The record files `framing_drop_scope`
consumes (`concept-screen`, `judge-study`, `window-screen` and
`runtime-screen` records, plus the five extraction-corpus prose fixtures),
de-duplicated by (model, source, raw hash), restricted to records whose
source was framing-shaped. This is the population #522/#533/#1575 measured; it
includes the Spanish prose fixture with a meeting heading. TS3005a/b, the
sources #522/#533 named, are outside `screen_data.ALLOWED_MEETINGS`
(evaluation or reserve series) and are not read.

## Metrics

All are counts over replayed cases (a case is one record), per arm:

- **zero**: the arm's final candidate list is empty.
- **fires**: the arm's list differs from `a0`'s (only possible when `a0` is
  empty).
- **restored**: objects in the arm's list that `a0` did not keep. Each is
  classified from its type, title and description only (never its arm, model or
  run), before `decide` is first run:
  - **container**: about the gathering itself (occurrence, agenda, attendees,
    schedule, a heading over unnamed items). Every `Event` is a container.
  - **subject**: a specific decision, task, outcome, product or person that
    would make sense without the word "meeting" in the title.
  - **contested**: neither or both. Counted as a container (the reading least
    favourable to a floor), as #1575 does under ADOPT.
  The classification is committed to `ADJUDICATION` first; an unclassified
  restored object stops `decide`.

Primary: the **zero-object rate** on the Spanish population, `a0` against each
arm, paired on the same recorded runs.

## Bars

Applied in this order. A verdict is per arm.

- **B0, the defect exists** (precondition). `a0`'s zero rate is at least
  **0.20 on `ES2015a-es`** (n = 15) and at least **0.10 pooled** over all
  Spanish records (proposed). If not, the verdict is **NO-DEFECT**: nothing is
  licensed, #1642 is recorded as not reproducing at n = 15, and the issue
  stays open for a different mechanism. (Three runs gave 2 of 3; at n = 15 a
  true rate of 0.5 clears 0.20 with probability above 0.99.)
- **B1, benefit.** The arm's pooled zero count is at most **half** of `a0`'s
  (proposed).
- **B2, English guard.** On the stored English population, the objects the arm
  restores and that classify as container (contested included) number at most
  `max(1, floor(n_english / 100))` (proposed: one per hundred framing-shaped
  records). This is the bar for "does not reintroduce framing objects on the
  sources #522/#533 measured". It is judged on the container reading.
- **B3, restored quality.** On the Spanish population, at least **half** of the
  objects the arm restores classify as subject (proposed). If most restored
  objects are containers, the floor is storing framing as knowledge, whatever
  it does to the zero rate.

Verdicts: **LICENSED** (`a1` clears B0 to B3), **LICENSED-PENDING-OWNER**
(`a2` clears B0 to B3; see below), **REJECTED** (B0 met, a bar failed),
**NO-DEFECT** (B0 not met). Never a recommendation to change a default: a
LICENSED arm opens a production change with a spec delta for the framing
requirement.

`a2` always carries a product decision the bars cannot settle: it stores
English-titled objects on a Spanish source. B3 measures whether those objects
are topical, not whether a leaked title is better than no object. The harness
therefore never returns plain LICENSED for `a2`; the owner decides.

## n and the stopping rule

- Memory rule: n = 15 is the floor for noisy extraction arms (one arm spanned
  0.25 against itself at n = 5). Every Spanish source gets **15 runs**
  (seeds 1 to 15): 75 records.
- **Phase 1:** `ES2015a-es`, runs 1 to 15 (60 windows).
- **Futility stop, after phase 1.** Compute only `a0`'s zero rate on
  `ES2015a-es`. If it is below 0.20, stop: verdict NO-DEFECT. No arm number
  is consulted for this.
- **Phase 2:** the other four sources, runs 1 to 15 (the remaining 600
  windows). Never started if phase 1 stops.
- No other stopping rule. The decision rule is never consulted to stop early,
  extend, or re-run.
- A record whose run failed or timed out is excluded, reported as "n failed of
  TOTAL", and not re-run. If more than 15% of records fail, B0 to B3 are
  reported but the run is marked INCONCLUSIVE.
- Runs of one source are correlated (the same meeting yields similar titles).
  The tables also report distinct (source, type, title) triples among the
  restored objects, so a repeated title is visible.

## Cost forecast

Source of the rates: `evals/concept_model_screen/RESULTS.md` and #1575's
forecast, `qwen3:8b` at 29.8 to 41.4 s per window including the participant
capture (the judge is off here, which makes this conservative). Windows:
`4 + 9 + 10 + 9 + 12 = 44` per run set, 660 for 15 runs.

| Phase | Windows | Hours (29.8 s) | Hours (41.4 s) |
|---|---|---|---|
| 1: `ES2015a-es` x 15 | 60 | 0.5 | 0.7 |
| 2: four sources x 15 | 600 | 5.0 | 6.9 |
| **Total** | **660** | **5.5** | **7.6** |

Plus one model load (about 2 min). Hard cap: **10 GPU hours**, after which the
run stops and the bars are applied to what exists (B0's pooled part then
reports its n). The English guard and `score`/`decide` cost no GPU: model-free,
minutes. Ollama serializes (a default server does not run two jobs faster), so
this is one job at a time and the #1651 cross-encoder run waits behind it.

## Exact commands

One Ollama job at a time; every command runs to completion before the next
starts. Run from a checkout that has the extraction-corpus prose sources.
Record the commit and `ollama list` first.

```sh
git rev-parse HEAD && ollama list && ollama ps

# Phase 1
uv run python evals/framing_floor/measure_framing_floor.py run \
  --sources ES2015a-es --runs 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15

# Futility check: read ONLY a0's lead zero rate (lead_zero_a0 / lead_n)
OLLAMA_HOST=127.0.0.1:1 uv run python evals/framing_floor/measure_framing_floor.py score

# Phase 2 (only if phase 1 did not stop)
uv run python evals/framing_floor/measure_framing_floor.py run \
  --sources ES2015d-es IS1004b-es IS1004d-es TS3009c-es \
  --runs 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15

# Model-free from here; OLLAMA_HOST is a closed port so it stays a check
OLLAMA_HOST=127.0.0.1:1 uv run python evals/framing_floor/measure_framing_floor.py score
# classify every restored_titles entry into ADJUDICATION, commit, then:
OLLAMA_HOST=127.0.0.1:1 uv run python evals/framing_floor/measure_framing_floor.py decide
```

`score` prints counts and the restored objects to classify; it applies no bar.
`decide` stops at the first unadjudicated restored object.

## Outputs

`RESULTS.md` in this directory: the commit, the model digest, records run,
failed and excluded, the zero/fires/restored table per arm for each population,
every restored object with its class, distinct triples, the verdict per arm,
wall-clock against the forecast, and the limits below. Nothing from the
private corpus beyond titles of restored objects, counts and verdicts is
committed. The verdict updates #1642 and nothing else.

## Disclosed before scoring

- The only observations seen are the three runs quoted in #1642 (titles and
  counts). No record from this registration exists and none was scored.
- The English guard is model-free on already-stored records and was **not run**
  while this file was written, so its bar is not fitted to its result.
- The harness was developed test-first; its self-test is model-free
  (`evals/run_self_tests.py`) and was mutation-checked once: disabling
  the "a floor acts only when `a0` is empty" guard turns the self-test red.

## Limits (assumptions, labelled)

- Assumption: `qwen3:8b` at production defaults is the model that matters;
  the survivors depend on which titles a model emits, so other models may
  differ. Not run.
- Stage-local. A kept stub may still be removed downstream, and exactly one
  kept object triggers the sole-object re-ask (#584), whose effect is not
  replayable. If `a1` keeps one object, a re-ask call that production would
  make is not counted.
- The Spanish population is a machine translation (gemma4:26b-a4b) of AMI
  English; a human Spanish meeting may differ. It is also one corpus family.
- The classification is a human (or delegated) judgment on type, title and
  description; if delegated it is disclosed in RESULTS as #1575 did, and a
  re-read after the verdict is a new registration.
- Replay is production's functions on the recorded raw stage, not the recorded
  `drops` stage. `score` reports `invariant_violations` (an arm changing a
  source whose `a0` was non-empty), but it does not compare `a0` with a stored
  `drops` stage; that cross-check, which `drop_ablation.py` makes, is not
  repeated here.
