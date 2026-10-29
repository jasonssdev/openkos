# Pre-registration: the cap-exempt meeting Event and its body (#1689)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the
section "The owner confirms" and this file has merged to `main`, before the
first model call.** A change after that point is a new registration, not an
edit to this one. It generates no number itself: it fixes what will be
computed and generated, how it will be read, and what each outcome means.
Production code and defaults are unchanged by this file and by the run. A
verdict that licenses the arm opens a separate production change with an
OpenSpec delta, its own tests and PR. Every bar marked **(proposed)** is the
author's proposal until the owner confirms it.

## Question

The model-free replay (`evals/meeting_event/RESULTS.md`, PR #1693) ended
**ADVANCE-WITH-CAP-EXEMPTION**: a kept meeting `Event` appended after the
subjects is lost to the object cap in 153 of 231 English qualifying cases
(10 of 67 Spanish), so keeping it under the cap would displace subjects, and
the Event's *body* could not be measured because the stored records carry
body lengths, not body text. This registration asks the two things that
verdict left open:

**(1) Does placing at most one meeting `Event` outside the cap, when a
non-framing subject survives, displace nothing? (2) Is there an Event body
worth keeping: dated only by the source, naming its participants only as the
source does, linking to the subjects it produced, and not a restatement of
the source?**

It adopts nothing.

## What this does not touch

- Production code, prompts and defaults. The compose prompt is defined in the
  harness (`EVENT_COMPOSE_SYSTEM_PROMPT`), never in `src/`.
- `_MEETING_SHAPED_TITLE_RE`, the language gate, the twin rule, the framing
  drop, the sole-object re-ask (#584), the judge, the ceiling and the
  backstop. The Event is placed after all of them, so none sees it.
- The Event *selection* rule (highest title grounding, earlier position on a
  tie) and its candidate type (`Event` only), both from #1691.
- The empty-reply case (#1642, #1690) and the re-ingest question (how a second
  ingest of the same meeting reconciles with an existing Event). Not replayable
  and not generated here.
- Every number is **stage-local**: after the source-title twin rule.

## Why a generation arm, and only where it is needed

The question is answered in the cheapest order that is still honest:

1. **Placement is a replay.** `a2` is a pure function of the stored raw stage
   (the same replay as #1691); it needs no model. It is run on both
   populations.
2. **The model's own Event body is a replay too.** The framing-floor harness
   stored every chat reply in `framing-floor/cache/chat`, keyed by the call
   key each record carries. The dropped Event's body as the model wrote it
   (`e0`) is recovered by re-parsing the cached reply with production's own
   `_validate`. No model is called.
3. **Only a body the model has not yet written needs generation.** `e1` is one
   bounded call per case that composes the Event given the source and the
   titles of the retained subjects. It is reached only if `e0` is
   insufficient, because the model wrote `e0` without knowing it would be
   kept or what the subjects were, so `e0` is expected to fall short on
   linking; whether it is also a transcript dump is what the cache answers.

## Arms

| Arm | Rule |
|---|---|
| `a0` | The shipped pipeline: no meeting Event; objects capped at six. |
| `a2` | `a0`'s first six objects, plus at most **one** meeting `Event` as a seventh object **outside the cap**, only when `a0` is non-empty (a non-framing subject survived). |
| `e0` | The selected Event's body as the model wrote it, recovered from the cache. |
| `e1` | The selected Event's description and body composed by one call (below). |

`a2` equals `a0` whenever `a0` is empty or no candidate exists. The Event is
placed after the sole-object re-ask and after the cap, so it never changes
whether the re-ask fires (11 Spanish and 12 English qualifying cases have
exactly one surviving subject).

### The compose call (`e1`)

One chat call per selected case, source bounded by production's
`_bounded_prompt_source` with `_OPTIONAL_CALL_RESERVE_TOKENS`, as the re-ask
does (#866). The user turn carries `_LANGUAGE_ANCHOR`, the meeting title
(**fixed** by the selection rule, so the model never chooses it), the titles
of `a0`'s retained subjects, and the source text. The system prompt is frozen
in `EVENT_COMPOSE_SYSTEM_PROMPT`. It asks for a JSON object with a one-sentence
`description` and a `body` of at most 1200 characters of prose, no transcript
and no quotation, that states in order: when the meeting took place only if the
source says so (never a guessed date), who took part only by the names or role
labels the source uses, and what it produced, naming each listed object by its
exact title. It forbids copying source sentences, adding facts the source does
not state, and naming an object not in the list. Tuning it after a reply is a
new registration.

Links are measured as the **exact title** of a retained subject appearing in
the body. OKF links are bundle-relative paths (§6.1) whose final slugs do not
exist at extraction time; resolving a title to a path is a production concern,
not measured here (see Limits).

## Data

**Placement population (model-free).** The same two populations as #1691: the
75 framing-floor Spanish records (`qwen3:8b`, 5 sources x 15 runs, production
defaults, `~/Dev/Projects/openkos-eval-runs/framing-floor`) and the 254
de-duplicated English framing-shaped stored records. Development series only
(pool DA); TS3005a/b stay outside it.

**Body population (Spanish only).** The five Spanish machine translations of
pool DA (`ES2015a-es`, `ES2015d-es`, `IS1004b-es`, `IS1004d-es`, `TS3009c-es`,
`~/openkos-eval-runs/retrieval-gold/translations/`, private, never
committed; the harness refuses any other source). For each source, the **first
6 qualifying runs in run order** (a run qualifies when `a0` is non-empty and a
candidate Event exists): 30 cases, no other choice. The same 30 cases are
read by `e0` and composed by `e1`, so the arms are paired. One sample per case
(`e1` seed = 2000 + run, above every generation seed).

English bodies are **not** measured: the English population is assembled from
several earlier experiments' records and this harness does not join them to
their chat caches. English enters only through placement, which is where it
mattered (B4).

## Metrics

Per case, from the body, the source and the titles of `a0`'s retained
subjects (all deterministic, computed by the harness):

- **restate**: the share of the body's 6-word n-grams that occur verbatim in
  the source (`ngram_overlap`). The case passes at 0.50 or less (proposed).
- **linked**: the body names at least `min(2, n)` of the `n` distinct retained
  subject titles, case-insensitively.
- **invented date**: a date-shaped token in the body (ISO, d/m/y, "12 de mayo
  [de 2005]", "May 12[, 2005]", a bare year) that does not occur in the source
  text. A source with no date admits no date in the body.
- **bounded**: the body is between 80 and 1500 characters.
- **faithful** (adjudicated, below): every fact in body and description,
  including participants and any date, is stated by the source, and no object
  is invented.
- **cost** (`e1` only): failed calls and seconds per call.

`a2` placement counts per population: **qualifies**, **displaced** (the capped
`a0` is not a prefix of `a2`, in order), **bad cardinality** (`a2` adds other
than one object when `a0` is non-empty and a candidate exists, or other than
zero otherwise), **overflow** (`a2` holds more than seven objects), and
**beyond cap** (the Event is the seventh object), reported not barred.

## Bars

Applied in this order. One verdict per phase.

- **P1, no displacement.** `a2` displaced: **0** on both populations
  (proposed).
- **P2, cardinality.** `a2` bad cardinality and overflow: **0** on both
  populations (proposed). P1 and P2 hold by construction of `a2`; they are
  pinned as bars so a later change of the arm cannot break them silently, and
  the self-test mutation-checks the guards behind them.
- **E1, not a restatement.** At least **0.90** of the scored bodies pass
  `restate` (proposed).
- **E2, linked to its subjects.** At least **0.80** pass `linked` (proposed).
- **E3, no invented date.** Bodies with an invented date: at most
  `max(1, floor(scored / 20))` (proposed: one per twenty).
- **E4, bounded.** At least **0.95** pass `bounded` (proposed).
- **E5, faithful.** At least **0.90** of the scored bodies are adjudicated
  faithful; contested counts against (proposed).
- **E6, cost** (`e1` only). Failed calls at most 0.10 of the planned calls,
  and the mean seconds per call at most the mean wall-clock of a full
  extraction run in the stored population (378.7 s over 75 records)
  (proposed). This checks the call is not a runaway (#828) and costs less than
  the extraction it adds to.

**INCONCLUSIVE floors.** `e0`: fewer than 27 of the 30 bodies recoverable from
the cache (0.90). `e1`: fewer than 20 bodies scored, or more than 15% of the
planned calls failed or returned no usable body. Reported, never rescued by
more runs.

Verdicts:

- **Phase `natural` (`e0`, no GPU).** NATURAL-SUFFICES: P1, P2 and E1 to E5
  hold. NATURAL-INSUFFICIENT: P1 and P2 hold, a bar of E1 to E5 fails (named).
  INCONCLUSIVE: the recovery floor is not met. REJECTED: P1 or P2 fails.
  NEEDS-ADJUDICATION: E1 to E4 hold, so only E5 can still decide; `decide`
  stops there until `ADJUDICATION` is complete.
- **Phase `composed` (`e1`, GPU).** LICENSED: P1, P2 and E1 to E6 hold.
  REJECTED: a bar fails (named). INCONCLUSIVE: the floor is not met.
  NEEDS-ADJUDICATION as above.

LICENSED and NATURAL-SUFFICES each license only an OpenSpec change and a
production implementation of the cap-exempt Event (with the body source the
verdict names: the model's own body, or a composing call); never an adoption
and never a change of a default.

## n and the stopping rule

- **Phase 0, `natural`.** Model-free; all 30 cases and both placement
  populations are scored once. If it is NATURAL-SUFFICES, the GPU is never
  started.
- **Phase 1a, `composed`, 10 cases** (the first 2 qualifying runs of each
  source). Then `score --phase composed --per-source 2` prints
  `futility_stop`.
- **Futility stop.** A deterministic bar is out of reach of the planned 30
  cases however the remaining ones go: restate (needs 27 passes), linked (24),
  bounded (29), or invented dates (more than one). A failed call counts as not
  passing. At 10 cases that means, for example, 3 or fewer linked passes or 2
  invented dates. This is the only stop that reads an arm number and it can
  only end a run towards **REJECTED**, never towards LICENSED. Phase 1b is
  never started.
- **Phase 1b.** The remaining 20 cases, then the whole 30 are scored.
- No other stopping rule. The decision rule is never consulted to extend,
  subset or re-run. A call that failed is not re-run; it is reported as "n
  failed of 30". The six runs of one source share a source and its vocabulary,
  so they are correlated; the effective n for E5 is nearer the five sources
  than the 30 cases, and the tables report per-source counts.

## Cost forecast

Sources of the rates: 734 uncached calls in the stored framing-floor records
(mean 34.7 s per window call, prompt 3.3k tokens on average and 7.8k at most,
about 36 tokens per second of generation, maximum 247 s), the 75-record mean
extraction run of 378.7 s (97 to 725 s), and the re-ask's measured whole-source
call (roughly 40 s for a short reply to 225 s for a runaway, #828). A compose
call sends the whole source (15 to 45 thousand characters, bounded to the
12 288-token window) and generates about 400 tokens.

| Step | Calls | Low | Typical | High |
|---|---|---|---|---|
| Phase 0, `natural` + placement | 0 | 0 | 0 | 0 |
| Phase 1a: 10 cases | 10 | 0.06 h | 0.17 h | 0.7 h |
| Phase 1b: 20 cases | 20 | 0.11 h | 0.33 h | 1.4 h |
| **Total (if phase 0 is insufficient)** | 30 | **0.2 h** | **0.5 h** | **2.1 h** |

Plus one model load (about 2 min). **Hard cap: 3 GPU hours**, after which the
run stops and the bars are applied to what exists (the INCONCLUSIVE floor then
reports its n). `score` and `decide` cost no GPU. Ollama serializes (a default
server does not run two jobs faster), so this is one job at a time. The GPU is
reserved by the owner: ask before the first model call.

## Exact commands

One Ollama job at a time; every command runs to completion before the next
starts. Record the commit first.

```sh
git rev-parse HEAD

# Phase 0 (no GPU): placement on both populations, e0 from the cache
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_body/measure_meeting_event_body.py \
  score --phase natural
# if E1-E4 all hold, classify every to_adjudicate body into ADJUDICATION, commit, then
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_body/measure_meeting_event_body.py \
  decide --phase natural
# NATURAL-SUFFICES ends here. Otherwise ask the owner for the GPU.

# Phase 1a (only if natural is insufficient and the GPU is released)
uv run python evals/meeting_event_body/measure_meeting_event_body.py compose --per-source 2
# Futility check: read ONLY futility_stop
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_body/measure_meeting_event_body.py \
  score --phase composed --per-source 2

# Phase 1b (only if phase 1a did not stop)
uv run python evals/meeting_event_body/measure_meeting_event_body.py compose --per-source 6

# Model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_body/measure_meeting_event_body.py \
  score --phase composed
# classify every to_adjudicate body into ADJUDICATION, commit, then:
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_body/measure_meeting_event_body.py \
  decide --phase composed
```

`score` prints the counts, the per-case checks and the bodies to adjudicate,
and applies no bar. Model-free check:
`uv run python evals/meeting_event_body/measure_meeting_event_body.py --self-test`
(also run by `evals/run_self_tests.py`). The stamp (harness commit, model
digest and the hash of the prompts sent) is written under
`compose/<model>/stamp.json`.

## Adjudication

Only E5 is a judgment, and it is read only when E1 to E4 already hold (a body
that fails a deterministic bar cannot be rescued by being faithful, so reading
it would be wasted effort). Each scored body is labelled `faithful`,
`unfaithful` or `contested` from the body, the description and the source
text, by key `(phase, "source/rN")`, **before `decide` is first run**, never
seeing which arm or run it came from. A reader who finds a participant, a
date, a decision or an object that the source does not state labels it
unfaithful. Whoever adjudicates is named in RESULTS; a delegated reader is
disclosed as in #1693; a re-read after the verdict is a new registration.

## Outputs

`RESULTS.md` in this directory: the commit, the model digest (phase 1), the
cases scored, failed and unrecovered, the placement table per population, the
per-bar results with per-source counts, the adjudication and who made it, the
verdict, the cost in calls and seconds against the forecast, and the limits
below. Nothing from the private corpus beyond counts, titles of Events and
verdicts is committed; bodies stay outside the repository. The verdict updates
#1689 and nothing else.

## Disclosed before scoring

- Seen before this registration: #1693's counts (placement, B0 to B4 and the
  kept titles) and that no Event body was measured.
- A smoke run of `score --phase natural` was made on the real data to check
  the loaders. Only these fields were read: the placement counts (identical to
  #1693: Spanish 67 qualifying, 10 beyond the cap; English 231 qualifying, 153
  beyond the cap; zero displacement and cardinality violations) and the
  recovery counts (**30 of 30 Event bodies recoverable from the cache**). No
  body check (`restate`, `linked`, dates, `bounded`) was read.
- One cached reply was opened to confirm the cache schema. Its first object
  was an `Event` whose body began with verbatim transcript lines. That is one
  of 746 cached replies, but it is the reason `e0` is expected to fail E1 and
  why the registration makes the cache replay a cheap first step rather than
  assuming the model's own body suffices.
- No compose call has been made and the compose prompt was not tried on any
  source before it was frozen here.
- The thresholds are round numbers chosen before scoring, not fitted. E6's
  reference and the call-failure share follow #1690.
- The harness was developed test-first (RED observed for every pure function
  before it existed) and mutation-checked: removing the "only when `a0` is
  non-empty" guard, removing the cap slice and loosening the futility boundary
  each turn the self-test red.

## Limits (assumptions, labelled)

- **Spanish only for bodies**, one corpus family (a machine translation of
  AMI by gemma4:26b-a4b), `qwen3:8b`, 5 sources. AMI transcripts carry no
  calendar date, so E3 mostly tests that a date is *not* invented, and the
  benefit of a stated date is untested. Human-written meetings may differ.
- Links are exact titles, not bundle-relative paths; whether production can
  resolve a title to the slug the writer assigns is a separate design question.
- The deterministic checks are proxies: 6-gram overlap misses a paraphrased
  copy, and a body can name every subject title and still say nothing. E5
  catches invention, not usefulness.
- `e0` bodies come from windows that may carry only a part of the meeting
  (the source is fanned out into windows); they are the model's, written
  without knowledge of the final subjects.
- Placement is stage-local. The Event as a seventh object may interact with
  downstream consumers that assume at most six objects per source (the
  ingest report counts, curation, `index.md`); the replay does not exercise
  them and the OpenSpec change must.
- One sample per case; the cases of one source are correlated.
- Cardinality "one meeting Event per source" is assumed from the issue; a
  long transcript with several sessions may warrant one per session.
- Adjudication is a human (or delegated) judgment; the registry is keyed per
  case, so a different reading of one body moves E5 by one.

## The owner confirms

On 2026-10-29, before this file merged and before any result existed, the
owner gave a standing instruction to take the recommended option on open
choices; under it every item below is confirmed unchanged, as proposed, with
adjudication by a delegated reader disclosed in RESULTS. The owner did not
review the items one by one; this is recorded so the confirmation is not read
as more than it is.

1. **The bars** marked **(proposed)**: P1/P2 (zero violations), E1 (0.50
   overlap, 0.90 of bodies), E2 (names `min(2, n)` retained titles, 0.80), E3
   (invented dates at most one per twenty scored), E4 (80 to 1500 characters,
   0.95), E5 (0.90 faithful, contested against), E6 (failed calls at most 0.10,
   mean seconds not above a full extraction run), and the INCONCLUSIVE floors
   (27 of 30 recoverable bodies; 20 scored and 15% failed for `e1`).
2. **The order**: cache replay of the model's own body first, GPU compose only
   if it is insufficient, and NATURAL-SUFFICES ending the work with no GPU.
3. **The placement**: one meeting `Event` as a seventh object outside the cap,
   after the re-ask and the cap, only when a subject survives; the selection
   rule and `Event`-only candidates unchanged from #1691.
4. **The compose prompt** wording in `EVENT_COMPOSE_SYSTEM_PROMPT` (fixed
   title, date only if the source states it, participants only as the source
   names them, subjects named by exact title, at most 1200 characters), since
   it is frozen once this file merges.
5. **The cases**: the first 6 qualifying runs of each of the five Spanish
   development translations (30 cases, 1 sample each), Spanish bodies only;
   English through placement only.
6. **The futility stop** after phase 1a (10 cases), ending the run as REJECTED
   when a deterministic bar is out of reach.
7. **GPU time**: the forecast (0.2 to 2.1 h, typical 0.5 h), the 3 h hard cap,
   and when the GPU is free (it is reserved by the owner; this registration
   asks before the first model call and spends nothing in phase 0).
8. **Adjudication**: who labels the bodies (the owner, or a delegated reader
   disclosed in RESULTS), and that E5 is read only when E1 to E4 hold.
