# Pre-registration: the roster-grounded body, with a retry and scored-body futility (#1689, third arm)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the
section "The owner confirms" and this file has merged to `main`, before the
first model call.** A change after that point is a new registration, not an
edit to this one. Production code and defaults are unchanged by this file and by
the run. A LICENSED verdict opens a separate production change with an OpenSpec
delta, its own tests and PR. Every number marked **(proposed)** is the author's
proposal until the owner confirms it. This stacks on the roster registration
(`PREREGISTRATION.md`, PR #1713) and its results (`RESULTS.md`, PR #1716) and
assumes both. Everything not listed under "What changes" is that registration,
unchanged.

## Why a new registration

The roster run (PR #1716) ended INCONCLUSIVE on its registered futility stop,
not on a result. After phase 1a, 2 of 10 compose replies did not parse (no
closing brace), and the futility rule counted those failed calls as *not passing
E4* against the planned 30: 29 of 30 passing bodies were needed, 8 of 10 done
left a best case of 28, so the run stopped. But E4 is defined over **scored
bodies** (at least 0.95 of them bounded). Measured on its own terms, 8 of 8
scored bodies passed E4 and nothing was out of reach. The stop guarded a
stricter bar than the one the verdict applies. That is a design flaw inherited
from the first body registration, not a property of the model, and it is the
only thing that ended the run. The model's behaviour in the 8 scored bodies (it
named exactly the roster labels) is encouraging but was not adjudicated.

## Disclosed before this registration (forking paths)

Seen **before** this registration was written, and therefore able to shape it:

- The whole of the roster `RESULTS.md`, including that the 8 scored bodies
  passed every deterministic bar and named no absent participant, and the
  observation that the model named exactly `PM, ID, UI, ME` and not "other
  members of the team".
- By the run's reader (disclosed in that RESULTS under "Deviations"), the 8
  scored bodies, their descriptions and the check outcomes: `score` was printed
  whole instead of only `futility_stop`. **Faithfulness was not read and no label
  was written** (`ADJUDICATION` was empty then and still is).
- The two failed replies (`ES2015a-es/r1`, `ES2015d-es/r2`) and their cache
  entries, read for the cause below.

That the roster prompt works on participants, at least in 8 of 8 bodies, is
therefore **not blind knowledge**: the author knows the prompt did what it was
meant to on those 8. What was not seen: any body of this run, any faithfulness
label, any outcome of the new seeds. The prompt is not tuned by this
registration (it is byte-identical); only the stopping rule and the retry are.
The 8 seen bodies are **not re-scored as they are**: every case below is drawn
at a fresh seed.

## The truncation: cause

Inspected in the chat cache of the roster run (10 entries, `outcome: ok`):

| Reply | `done_reason` | `eval_count` | prompt tokens | ceiling |
|---|---|---|---|---|
| ES2015d-es/r2 (failed) | `stop` | 71 | 7768 | 8192 |
| ES2015a-es/r1 (failed) | `stop` | 98 | 4839 | 8192 |
| the 8 scored | `stop` | 75 to 148 | 4820 to 6781 | 8192 |

Both failed replies are complete up to the last closing quote of the `body`
string and then end: the model emitted its end-of-sequence token one token
early, **omitting the final `}`**. They are not cut by the generation ceiling:
`done_reason` is `stop` (a ceiling hit reports `length`, which the client turns
into `OllamaGenerationCapped`), they used 71 and 98 of 8192 allowed tokens, and
prompt plus reply is far inside the 12288 context window. The ceiling the
harness sends (`MAX_GENERATION_TOKENS = 8192`, `screen_pipeline.py`) is
production's default (`DEFAULT_MAX_GENERATION_TOKENS = 8192`, `config.py`), so
raising it would change nothing and would not match production. The cause is a
sampling slip of the model on a small closing token, 2 of 10 times; whether the
rate is that high is exactly what this run measures.

Fail-closed extraction (`extract_json_object`) correctly refuses such a reply.
This registration does **not** repair the JSON; it re-asks.

## What changes

1. **Futility uses the denominators of the bars it guards.** E1, E2, E4 are
   shares of the *scored* bodies and E3 an allowance on them, so the stop
   evaluates each over the scored bodies. A failed call is counted **only** in
   E6 (failed calls at most 10% of planned, i.e. 3 of 30) and the INCONCLUSIVE
   floors (at least 20 scored bodies; at most 15% of planned failed, i.e. 4 of
   30). Defined by `stop_reading` in the harness: with `scored`, `failed`,
   `remaining = 30 - scored - failed`, every ending is tried in which `j` of the
   remaining cases are scored and all pass (the best case for any bar) and the
   rest fail; only endings with `scored + j >= 20` and total failures at most 4
   can end in a verdict.
   - No such ending exists (in effect, 5 or more failed calls): the reading is
     **INCONCLUSIVE**, certain.
   - Otherwise a bar that no such ending can pass (E1 restate, E2 linked, E3
     dates, E4 bounded, E6 failed calls) is out of reach: the reading is
     **REJECTED**, naming the bars.
   - Otherwise no stop.
   It still reads no faithfulness and no participant-check outcome, and it can
   only end a run towards REJECTED or INCONCLUSIVE, never LICENSED. The roster
   run's counts (8 scored, 2 failed, all passing) give *no stop* under it;
   under the inherited rule they stopped. This is pinned by the self-test.
2. **One retry of an unparseable compose reply, fresh seed.** If the first
   reply does not parse to `{description, body}` (`parse_compose_reply` is
   `None`), the same prompt is sent once more; a second failure is a failed call
   and no third call is made. The identical prompt makes the harness backend
   draw attempt seed `SEED_BASE + run + 1000` (`RETRY_SEED_STRIDE`), a fresh
   sample, as production's unset seed is. A call that *raises* (timeout, backend
   error) is not retried by this rule (the harness backend itself never re-sends
   a timeout). Production precedent, cited exactly: a failed window call is
   retried **exactly once** and then skipped (`_extract_window_or_skip`,
   `src/openkos/extraction/concept.py`, the `BackendError` handler, from line
   2467), and a single-pass extraction that returns nothing is retried once
   (`#524`, same file, from line 3473). Stated plainly, so this is not read as
   more than it is: production retries on a `BackendError` or an empty result;
   it does not retry on an unparseable JSON reply of this kind. The mechanism
   (one retry, then give up, fresh sample) is production's; the trigger is
   extended to this failure because it is the failure observed. Whether
   production should do the same is a question for the OpenSpec change if this
   run is LICENSED.
3. **Fresh seeds for all 30 cases.** `SEED_BASE` moves from 3000 to **5000**:
   first attempts 5001 to 5006, retries 6001 to 6006. The last run used 3001 to
   3006 (retry slots 4001 to 4006, never used); no seed is shared (pinned by the
   self-test). The cases (first six qualifying runs of each of five Spanish
   sources) are the same, so the bodies are comparable, but each is a new draw.
   Output goes to a **new directory** (`meeting-event-roster-retry`), so no
   earlier record is read as a result or skipped.
4. **E6 mean seconds per call** is measured per case including its retry (a
   retried case costs both attempts). Threshold unchanged (378.7 s).

## What does not change

Population (same 30 Spanish cases; placement re-scored model-free, expected
identical), the roster rule and the participant check (`roster_of`,
`participant_items`, `absent_participants`), the replay and its R1, R2, the
compose prompt (`EVENT_ROSTER_SYSTEM_PROMPT`, the `SPEAKERS IN THE SOURCE
(complete list)` line, byte-identical; the prompt hash in the stamp must equal
`4f93593ea5bea022`), model `qwen3:8b` at the same digest, E1 to E7 thresholds
(restate at least 0.90, linked at least 0.80, invented dates at most
`max(1, floor(scored / 20))`, bounded at least 0.95, failed calls at most 0.10
of planned and mean at most 378.7 s, faithful at least 0.90 with contested
counting against, yield at least 20 for `r1`), the two arms `r0` and `r1`, the
INCONCLUSIVE floors, and the adjudication procedure (a delegated reader, given
only `to_adjudicate`, applying the last arm's criterion, named in RESULTS; E5
read only for an arm whose deterministic bars and E7 hold).

## Verdicts, and what a futility stop means

Per arm, as registered: LICENSED, REJECTED, INCONCLUSIVE, NEEDS-ADJUDICATION.
Reading them together is item 9 of the roster registration, unchanged.

**A futility stop is itself the reading** (it was ambiguous last time, when
`decide` printed INCONCLUSIVE on the partial set): if `stop_reading` fires after
phase 1a, both arms read as its `reading` (REJECTED naming the bars, or
INCONCLUSIVE), phase 1b is not started, and `decide` (which requires the whole
set) is not run. Nothing is rescued by more runs.

## The closing rule

- **REJECTED** (both arms, by a bar or by a futility stop on a named bar, or
  `r0` REJECTED and `r1` REJECTED): the roster-grounded Event body is not
  licensed on this model, and the family closes. Issue #1689 is closed as not
  planned. The placement result stands. No further arm of this family is
  proposed.
- **LICENSED** (either arm): the next step is an OpenSpec change followed by the
  implementation, never an adoption by this file. **That change must first
  generalize the roster rule.** The roster here reads 2 to 4 capital letters or
  digits opening a line (`PM:`, `ID:`), the AMI form. It does not generalize as
  written to transcripts whose speakers are named ("Ana:", "Dr. Smith:"), which
  is the common production case; a source without such labels gets "none
  labelled" and the prompt then asks for no participants at all. This run
  measures the AMI form only and says nothing about named speakers.
- **INCONCLUSIVE** (a floor, or a stop reading INCONCLUSIVE): reported, not
  rescued. Whether to register once more is the owner's decision then, on a
  cause that is not a stopping-rule defect.

## n and the stopping rule

- Phase 0 (model-free): `--self-test`, `replay`, placement. CHECK-UNSOUND drops
  `r1`.
- Phase 1a: `compose --per-source 2` (10 cases), then `score --per-source 2`;
  read **only** `stop_reading` and the `r0_bodies` counts (the commands extract
  those keys; no body is printed).
- Phase 1b (only if `stop_reading.reading` is null): the remaining 20 cases.
- Hard cap, below. No other stopping rule; nothing is re-run, subset or extended
  after a number is seen.

## Cost forecast

Observed: about 21 s per call (21.1 s in the roster run, 21.05 s in the body
run), one model load of about 2 min, 216 s for 10 calls with the load. The
retry adds a call only for an unparseable reply, observed 2 of 10 (the
population rate is unknown; 4 of 30 retried is plausible, 12 would be extreme).

| Step | Calls | Low | Typical | High |
|---|---|---|---|---|
| Phase 1a: 10 cases | 10 to 14 | 0.06 h | 0.09 h | 0.23 h |
| Phase 1b: 20 cases | 20 to 28 | 0.11 h | 0.16 h | 0.47 h |
| **Total (with load)** | 30 to 42 | **0.20 h** | **0.28 h** | **0.75 h** |

(low = 15 s per call and 30 calls, typical = 21 s and 36 calls, high = 60 s and
42 calls, each plus the load). **Hard cap: 1 GPU hour** including the load and
every retry. The harness has no timer: the operator reads the clock between
cases (`compose` resumes by skipping cases already on disk) and stops before the
first case that would start after 1 h; the bars are then applied to what exists
and the floors report their n. One Ollama job at a time. The GPU is reserved by
the owner: ask before the first model call.

## Exact commands

```sh
git rev-parse HEAD
OUT="$HOME/Dev/Projects/openkos-eval-runs/meeting-event-roster-retry"; mkdir -p "$OUT"
H=evals/meeting_event_roster/measure_meeting_event_roster.py

# Phase 0 (no GPU)
OLLAMA_HOST=127.0.0.1:1 uv run python $H --self-test
OLLAMA_HOST=127.0.0.1:1 uv run python $H replay
OLLAMA_HOST=127.0.0.1:1 uv run python $H score --per-source 2 > "$OUT/score-0.json"   # placement only is read

# Phase 1a (GPU released by the owner)
uv run python $H compose --per-source 2
OLLAMA_HOST=127.0.0.1:1 uv run python $H score --per-source 2 > "$OUT/score-1a.json"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d['stop_reading']); print(d['r0_bodies'])" "$OUT/score-1a.json"

# Phase 1b (only if stop_reading.reading is null)
uv run python $H compose --per-source 6

# Model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python $H score > "$OUT/score-full.json"
# label every to_adjudicate body in ADJUDICATION, commit, then:
OLLAMA_HOST=127.0.0.1:1 uv run python $H decide
```

The stamp (harness commit, model digest, prompt hashes) is written under
`compose/qwen3_8b/stamp.json`; each record carries `attempts` and, for a retried
case, `first_reply`.

## Limits (assumptions, labelled)

- The roster here is the AMI speaker-label form only; see the closing rule.
- Spanish bodies only (machine translation), one corpus family, one model, one
  prompt, one sample per case, correlated cases (six runs per source), exact-title
  links, no calendar date in AMI so E3 mostly tests that none is invented.
- The retry raises the usable-body rate by design; it measures the pipeline *with*
  one re-ask, not the bare model. A retry also gives a second draw at the
  participants, so a body can be the second of two; `first_reply` is kept to
  report how often.
- The prompt is not tuned; a failure of E5 is a failure of this prompt.
- The 8 earlier bodies are known to have passed the deterministic bars (see the
  disclosure); this run does not rely on them, but a reader should not treat the
  fresh result as blind to that knowledge.

## The owner confirms

On 2026-10-29, before this file merged and before any result of this run
existed, the owner gave a standing instruction to take the recommended option
on open choices; under it every item below is confirmed unchanged, as
proposed, with adjudication by a delegated reader disclosed in RESULTS. The
owner did not review the items one by one; this is recorded so the
confirmation is not read as more than it is. The run waits for this file to
merge to `main`.

Nothing below is confirmed yet.

1. **The futility rule**: scored-body denominators for E1, E2, E3, E4; failed
   calls only in E6 (at most 3 of 30) and the floors (20 scored; at most 4 of 30
   failed); the reading INCONCLUSIVE when no ending clears the floors, REJECTED
   naming the bars otherwise; a stop is itself the reading and `decide` is not
   run on a partial set.
2. **The retry**: one retry of an unparseable compose reply on a fresh seed
   (attempt seed + 1000), a second failure final, no retry of a raised call;
   with the production precedent stated as the mechanism only, the trigger being
   an extension.
3. **The seeds and directory**: `SEED_BASE` 5000 (first attempts 5001 to 5006,
   retries 6001 to 6006), the same 30 cases, a new output directory
   `meeting-event-roster-retry`.
4. **The unchanged parts**: prompt (hash `4f93593ea5bea022`), check, population,
   arms, E1 to E7 thresholds, floors and adjudication procedure, exactly as in
   the roster registration.
5. **The closing rule**: REJECTED closes the family and #1689 as not planned;
   LICENSED opens the OpenSpec change and implementation with the roster rule
   generalized to named speakers first; INCONCLUSIVE is reported, not rescued.
6. **Disclosure**: the 8 bodies and their deterministic outcomes were seen
   before this registration; no faithfulness label exists; the fresh-seed run is
   to be read with that in mind.
7. **GPU time**: forecast 0.20 / 0.28 / 0.75 h, hard cap 1 GPU hour, operator
   clock between cases, and when the GPU is free (ask before the first model
   call).
8. **Adjudication**: a delegated reader disclosed in RESULTS (or the owner),
   given only the bodies, E5 read only for an arm whose deterministic bars and
   E7 hold.
