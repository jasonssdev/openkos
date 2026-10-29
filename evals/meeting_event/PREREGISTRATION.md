# Pre-registration: keep the meeting Event beside its subjects (#1689)

**Status: PROPOSED. It becomes REGISTERED when this file merges to `main`,
before the first record is scored.** A change after that point is a new
registration, not an edit to this one. It generates no number itself: it fixes
what will be computed, how it will be read, and what each outcome means.
Production code and defaults are unchanged by this file and by the run. A
verdict that advances an arm opens a later measurement or a separate
production change with its own spec, tests and PR. Every bar marked
**(proposed)** awaits the owner (see "The owner confirms").

## Question

`_drop_framing_objects` (`extraction/concept.py`) removes every non-`Procedure`
object whose own title matches `_MEETING_SHAPED_TITLE_RE` on a meeting-shaped
source. It has no floor and no exception, so the meeting itself never survives
as an object, even when the reply also carries the subjects the meeting
discussed. That was right for the defect it was built against: the container
sat at position 1 in 49 of 59 AMI runs, won the retained prefix at every cap
value (#533), and was the only object in 27 of 49 stored collapses (#522).

It also removes a representation that is legitimate on its own: the meeting as
a bounded `Event` alongside its subjects. For meeting-centred use (a corpus of
transcripts is exactly that) it is what answers "what was decided in that
meeting?", and today no object can. The framing-floor measurement (#1642,
`evals/framing_floor/RESULTS.md`) covered only replies that ended empty, and
found that case is a model collapse a kept container does not fix. This
registration asks the other case:

**On replies where subjects survive, how often is there a meeting `Event` the
framing drop removed, and does keeping exactly one, after the subjects,
displace nothing?**

It adopts nothing.

## What this does and does not measure

Measured, by replay of recorded raw stages: which objects survive, in which
order, how many are added, and what type and title the added object has.

**Not measurable by replay, and out of scope here:** the Event's *body*. The
stored records carry each object's type, title, description and body length
(`body_chars`), not its body text, and the body is exactly what #1689 makes
the Event worth keeping for: its date, its participants, and its links to the
subjects. Whether a generated Event body would carry those, rather than
restate the source, needs a **generation arm** (a model call per case) that
this registration does not run. A verdict here can therefore only advance an
arm to that later measurement; it can never license adoption.

Also not touched: `_MEETING_SHAPED_TITLE_RE`, the language gate, the prompt, the
re-ask, the judge, the ceiling, the backstop, and the empty-reply case (#1642).
Every number is **stage-local**: after the source-title twin rule, the last
deterministic drop (the same cut as #1572/#1575/#1642).

## Arms

Both arms are replayed from one recorded raw stage per run, through
production's own functions in production's chunked-path order (strip,
`_dedup_merged`, `_drop_wrong_language_titles`, `_drop_framing_objects`,
`_drop_source_title_twins`). Nothing is reimplemented except the arm itself.
The arms are harness-side; no `src/` behavior changes.

| Arm | Rule |
|---|---|
| `a0` | The shipped pipeline. |
| `a1` | `a0`, plus at most **one** meeting `Event`, appended **after** the subjects, only when `a0` is non-empty. |

**Candidates.** The objects of type `Event` that the framing drop removed and
that the language gate and the source-title twin rule would have kept: nothing
that another rule already removes can come back. Other types are not
candidates (the issue asks for a meeting `Event`; a `Project` or `Decision`
with a meeting-shaped title stays dropped).

**Selection rule (deterministic, source-faithful).** Among candidates, keep the
one with the highest *title grounding*: the share of the title's words (3 or
more letters, lower-cased) that occur in the source text. A tie goes to the
earlier reply position. The rule uses the title and the source only; it never
reads a description or a model name. It is a proxy for "named by what the
source actually says", not a quality judgment; the owner adjudicates what it
kept (B3).

**Placement.** After the retained subjects, never before. The alternative in
the issue, exempting the Event from the cap, changes where the cap counts and
is not replayed; B4 states when it would be needed instead.

## Metrics

Counts over replayed cases (a case is one record), per population:

- **a0 non-empty**: the denominator for the opportunity rate.
- **qualifies**: `a0` is non-empty and at least one candidate exists (`a1`
  differs from `a0`).
- **displacement violations**: cases where `a1` does not start with exactly
  `a0`, in order.
- **cardinality violations**: cases where `a1` adds a number of objects other
  than one (when `a0` is non-empty and a candidate exists) or zero (otherwise).
- **cap cut**: qualifying cases where `a0` already holds the cap (6,
  `_MAX_OBJECTS_PER_SOURCE`) or more, so an appended Event would fall past it.
- **kept objects**, each classified from type, title and description only
  (never model or run), before `decide` is first run, into `ADJUDICATION`:
  - **container**: the gathering itself (an occurrence, an agenda, attendees, a
    schedule, a heading over unnamed items). A dated `Event` of one meeting is
    a container, which is what this arm wants to keep.
  - **subject**: a specific decision, task, outcome, product or person that
    would make sense without the word "meeting" in the title. Keeping one is a
    mis-filed subject.
  - **contested**: neither or both. Counts against the arm (as subject).

Distinct (type, title) pairs among the kept objects are reported next to the
counts, so a repeated title is visible.

## Bars

Applied in this order, over both populations. One verdict, for `a1`.

- **B0, opportunity** (precondition). On each population, `qualifies` is at
  least **0.10** of `a0 non-empty` (proposed). If not, the verdict is
  **NO-OPPORTUNITY**: there is too little to keep, and #1689 is recorded as
  not worth a generation arm.
- **B1, no displacement.** Displacement violations: **0** on both populations
  (proposed). This is the #533 effect: the subject set and its order are
  unchanged versus `a0`.
- **B2, cardinality.** Cardinality violations: **0** on both populations
  (proposed). The kept Event count is exactly 1 per qualifying case and 0
  otherwise.
- **B3, the kept object is a meeting container.** At least **0.90** of the
  kept objects, pooled over both populations, classify as container (proposed;
  contested counts against). Owner adjudication, not the harness, settles it.
- **B4, reach under the cap.** On each population, cap cut is at most **0.25**
  of `qualifies` (proposed). Above it, "append after the subjects" would lose
  the Event to the cap in too many cases, and the cap-exempt placement would be
  needed.

Verdicts:

- **ADVANCE-TO-GENERATION**: B0 to B4 hold. Licenses only the later generation
  arm that measures the Event body.
- **ADVANCE-WITH-CAP-EXEMPTION**: B0 to B3 hold, B4 fails. Licenses the same
  generation arm with the cap-exempt placement.
- **REJECTED**: B0 holds, and B1, B2 or B3 fails.
- **NO-OPPORTUNITY**: B0 fails.

Never a recommendation to change a default, and never an adoption. B1 and B2
hold by construction of `a1`; they are pinned as bars so a future change of the
arm cannot silently break them, and the self-test mutation-checks the guard
behind them.

## n and the stopping rule

- **Spanish:** the 75 records of the framing-floor run (5 sources x 15 runs,
  `qwen3:8b`, production defaults), stored under
  `~/Dev/Projects/openkos-eval-runs/framing-floor`. Development series only
  (pool DA translations); the harness refuses any other source.
- **English:** the stored framing-shaped records `framing_drop_scope` replays
  (254 in #1688), restricted to framing-shaped sources, de-duplicated by
  (model, source, raw hash). TS3005a/b stay outside it (evaluation or reserve).
- No new generation, so no sampling plan and no sequential stop. The whole
  population is scored once. A record that failed is excluded and reported as
  "n failed of TOTAL", not re-run. The decision rule is never consulted to
  extend, subset or re-run.
- Runs of one source are correlated (the same meeting yields similar titles);
  the distinct-pair count is reported for that reason.

## Cost forecast

**Zero model calls and zero GPU hours.** `score` and `decide` replay stored
records through deterministic functions on the CPU: minutes. No Ollama is
started; the commands run with a closed `OLLAMA_HOST` so that stays a check.
Adjudicating the kept objects is a reading task (tens of distinct titles, as in
#1688). The later generation arm is *not* costed or licensed here; it gets its
own registration with its own forecast.

## Exact commands

```sh
git rev-parse HEAD

OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event/measure_meeting_event.py score
# classify every kept_titles entry into ADJUDICATION, commit, then:
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event/measure_meeting_event.py decide
```

`score` prints counts and the kept objects to classify and applies no bar.
`decide` stops at the first unadjudicated kept object. Model-free check:
`uv run python evals/meeting_event/measure_meeting_event.py --self-test`
(also run by `evals/run_self_tests.py`).

## Outputs

`RESULTS.md` in this directory: the commit, the records scored, failed and
excluded, the counts per population, every kept object with its class,
distinct pairs, the bar-by-bar result and the verdict, and the limits below.
Nothing from the private corpus beyond titles of kept objects, counts and the
verdict is committed. The verdict updates #1689 and nothing else.

## Disclosed before scoring

- No record has been scored for this registration. The harness was developed
  against synthetic cases only.
- Prior knowledge that could shape the bars: #1688 published that the objects
  the framing drop removes include many `Event` containers (all 25 restored
  English objects and the Spanish `a1` restorations were `Event`), so
  candidates are known to exist in the *empty-reply* subset. How often one
  accompanies surviving subjects is the unknown B0 measures, and it was not
  looked at. One stored Spanish record was opened to confirm the schema (its
  first raw object, an `Event`); no count was taken.
- The thresholds (0.10, 0.25, 0.90) are round numbers chosen before scoring,
  not fitted. B1 and B2 are exact.
- The harness was developed test-first (RED observed before the arm existed)
  and mutation-checked once: removing the "only when `a0` is non-empty" guard
  turns the self-test red.

## Limits (assumptions, labelled)

- **The body is not measured** (above). A kept Event whose body restates the
  source, or whose links point nowhere, would pass every bar here.
- Assumption: title grounding picks a *faithful* Event. It is a lexical proxy;
  a model may reconstruct a container title from content (#522), which grounds
  well and is still an invention of the title. B3 adjudicates what it kept.
- Cardinality "one per source" is assumed from the issue. How a *re-ingest* of
  the same meeting reconciles with an existing Event is a separate open
  question of #1689 and is not replayable.
- Stage-local. A kept Event may be affected downstream: the sole-object
  re-ask (#584) fires on exactly one surviving object, and `a1` can change that
  count. The ceiling, judge and backstop are not replayed.
- Qualification is measured on `qwen3:8b` titles at production defaults and on
  one corpus family (AMI and its machine Spanish translation, gemma4:26b-a4b).
  Other models and human-written meetings may differ.
- The English population includes records from several experiments, so its
  prevalence is not a rate for any single production workload.
- The classification is a human (or delegated) judgment on type, title and
  description; if delegated it is disclosed in RESULTS, and a re-read after the
  verdict is a new registration.

## The owner confirms

Left for the owner; none is decided here.

1. **The bars**: B0 (0.10 opportunity per population), B1/B2 (exact, zero
   violations), B3 (0.90 container share, contested against), B4 (0.25 cap
   cut). Confirm, or give different numbers before this merges.
2. **The selection rule**: highest title grounding, earlier position on a tie.
   Confirm, or name another deterministic rule.
3. **Candidate type**: `Event` only. Confirm that a meeting-shaped `Project`
   or `Decision` must stay dropped.
4. **Placement**: appended after the subjects (measured here) versus exempt
   from the cap (not replayable; only reached through B4).
5. **Cardinality**: one meeting `Event` per source. Confirm, and say whether a
   multi-meeting source (a long transcript with several sessions) should keep
   one per meeting instead.
6. **Whether to fund the generation arm** if a verdict advances, since the
   body content is what makes the Event useful and is what this cannot measure.
7. **Adjudication**: who classifies the kept objects (the owner, or a
   delegated reader disclosed in RESULTS).
