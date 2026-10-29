# Pre-registration: a roster-grounded meeting Event body (#1689, second arm)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the
section "The owner confirms" and this file has merged to `main`, before the
first model call.** A change after that point is a new registration, not an
edit to this one. Production code and defaults are unchanged by this file and by
the run. A verdict that licenses an arm opens a separate production change with
an OpenSpec delta, its own tests and PR. Every bar marked **(proposed)** is the
author's proposal until the owner confirms it. This stacks on the registration
(PR #1711) and its results (PR #1712); it assumes both.

## Question

The last arm composed the cap-exempt Event body from the source and the subject
titles, and was **REJECTED on E5** (`evals/meeting_event_body/RESULTS.md`):
faithful 5 of 28. Placement (P1, P2) held and stands. Every deterministic bar
held. The 23 unfaithful bodies fail for one reason: they name participants the
source does not state (19 "other members of the team" or "designers, managers";
4 role labels the source never uses: "Product Manager", "President",
"Executive Member", "User of Interface"), although each source declares a fixed
speaker roster (PM, ID, UI, ME).

**Does handing the model the source's speaker roster, and forbidding any other
participant, produce a body the adjudicator finds faithful? And would a
model-free check that rejects a body naming a participant the source lacks make
the stored bodies faithful at an acceptable yield?**

It adopts nothing and tunes nothing else.

## What this does not touch

Everything the last registration did not: production code, prompts and
defaults; the placement rule (one Event outside the cap, only when a subject
survives); the cases; the deterministic checks (restate, linked, dates,
bounded) and E1 to E6 thresholds, **unchanged**. Only the compose prompt (the
roster) and the new check are new. Stage-local numbers, Spanish bodies only,
English through placement only, exactly as before.

## The participant check (model-free)

Defined in `measure_meeting_event_roster.py`, frozen here.

- **Roster of a source** (`roster_of`): the labels that open a line as
  `LABEL:` (two to four capitals or digits), on at least two lines, in order of
  first appearance. A label seen once is a stray. The five sources yield
  `PM, ID, UI, ME` (in their order of first appearance).
- **Participants a body names** (`participant_items`): in each sentence that
  contains a participation **verb** (`participaron`, `participó`,
  `participaban`, `asistieron`, `asistió`, `took part`, `attended`,
  `participated`; never the noun "participantes"), the enumeration after the
  verb, split on commas, semicolons, parentheses and "y" / "e" / "and" / "&",
  with the leading article removed.
- **Names an absent participant** (`absent_participants`): at least one listed
  item that is not a whole-word, case-insensitive substring of the source. A
  roster label always is (it opens the speaker's lines); a name or role label
  the source states is; "otros miembros del equipo", "gerentes" and a role
  label the source never uses are not. A body with no participation sentence
  names nobody and passes.
- The check reads the **body** only. It does not judge description, dates or
  objects; those stay with E3 and E5.

### Phase 0: replay against the 28 recorded bodies (no model, no new label)

`replay` applies the check to the 28 scored bodies of the last arm and compares
it with their **recorded** adjudication (`ADJUDICATION` in
`measure_meeting_event_body.py`). Nothing is adjudicated again.

- **R1, catches.** At least **0.90** of the unfaithful bodies are flagged
  (21 of 23) (proposed).
- **R2, false rejects.** At most **1** of the 5 faithful bodies is flagged
  (proposed).
- CHECK-SOUND: R1 and R2 hold. CHECK-UNSOUND: either fails; arm `r1` is then
  not evaluated, `r0` still runs. INCONCLUSIVE: no unfaithful body to catch.

**Observed on the author's run of `replay` (registered numbers, model-free):
flagged 23 of 23 unfaithful, 0 of 5 faithful wrongly flagged: CHECK-SOUND.**
These numbers are a fit, not a test: the author read the 28 bodies while
writing the check, and its first draft matched the noun "participantes" and
wrongly flagged one faithful body (ES2015a r11); the verb list above is the
fix. The check was not otherwise tuned. The test of the check is its
behaviour on the fresh bodies of phase 1.

## Arms

| Arm | Rule |
|---|---|
| `r0` | The roster prompt alone. Every scored body is judged. |
| `r1` | The same bodies behind the participant check: a body naming an absent participant is rejected (no Event stored). Judged on the survivors. |

Both arms read **the same 30 calls**; `r1` costs no extra generation.

### The compose call

As the last arm (one call per case, `qwen3:8b`, source bounded by
`_bounded_prompt_source` with `_OPTIONAL_CALL_RESERVE_TOKENS`, seed
`3000 + run`, above every earlier seed). Changes, all in
`EVENT_ROSTER_SYSTEM_PROMPT` and the user turn:

- The user turn adds `SPEAKERS IN THE SOURCE (complete list): PM, ID, UI, ME`
  (`none labelled` when the roster is empty).
- The prompt says that list is complete; participants are named only by those
  labels, exactly as listed (a name the source gives for a speaker may follow
  its label in parentheses); a label is never expanded into a job title; no
  "other members", no "the team", no group or role outside the list; the
  description names no participant.
- Every other rule (language, date only if stated, no transcript, exact
  subject titles, at most 1200 characters) is as before.

Tuning it after a reply is a new registration.

## Data

The same 30 Spanish cases as the last arm (first 6 qualifying runs of each of
`ES2015a-es`, `ES2015d-es`, `IS1004b-es`, `IS1004d-es`, `TS3009c-es`), so the
results are comparable. Placement is re-scored model-free on both populations
(expected identical: Spanish 67 qualifying, English 231). The private corpus
is never committed.

## Bars

Applied in order, per arm. One verdict per arm.

- **P1, P2.** Placement displaced, bad cardinality, overflow: 0 on both
  populations (proposed; unchanged).
- **E1 to E4, E6.** Unchanged from the last registration: restate at least
  0.90, linked at least 0.80 (`min(2, n)` titles), invented dates at most
  `max(1, floor(scored / 20))`, bounded at least 0.95, failed calls at most
  0.10 of planned and mean seconds per call at most 378.7 (proposed, reused
  unchanged; E1 to E4 are measured on the arm's own bodies, `r1` on its
  survivors).
- **E5, faithful.** At least **0.90** of the arm's bodies adjudicated faithful
  (`r0`: every scored body; `r1`: the survivors); contested counts against
  (proposed, unchanged).
- **E7, yield (`r1` only).** At least **20** bodies survive the check, the
  scored-bodies floor of the sibling registration (20 of 30 planned, 0.67)
  (proposed). Below it the Event would be absent in over a third of the
  meetings where a subject survived; the arm is REJECTED, not INCONCLUSIVE.

**INCONCLUSIVE floors** (unchanged): fewer than 20 bodies scored, or more
than 15% of the planned calls failed or returned no usable body. Reported,
never rescued by more runs.

Verdicts per arm: LICENSED (P1, P2, E1 to E6, and E7 for `r1`), REJECTED (a
named bar fails), INCONCLUSIVE (floor), NEEDS-ADJUDICATION (E1 to E4 and, for
`r1`, E7 hold, so only E5 can decide). Reading them together:

- `r0` LICENSED: the prompt alone suffices; the check is not needed.
- `r0` REJECTED, `r1` LICENSED: the prompt plus the check is licensed.
- Both REJECTED: the Event body is not licensed on this model; the placement
  result stands and no further arm of this family is proposed without a new
  registration.

A LICENSED arm licenses only an OpenSpec change and a production
implementation of the cap-exempt Event; never an adoption or a default change.

## n and the stopping rule

- **Phase 0**: model-free (placement, `replay`). CHECK-UNSOUND drops `r1`.
- **Phase 1a**: `compose --per-source 2` (10 cases), then
  `score --per-source 2` and read **only** `futility_stop`.
- **Futility stop**: the last registration's, unchanged: some deterministic
  bar (restate, linked, bounded, dates) is out of reach of the planned 30
  however the remaining cases go; failed calls count as not passing. It ends
  the run towards REJECTED for both arms, never towards LICENSED. It reads no
  faithfulness and no check outcome. Phase 1b is then not started.
- **Phase 1b**: the remaining 20 cases, then the whole 30 are scored.
- No other stopping rule; nothing is re-run, subset or extended after a number
  is seen. A failed call is reported as "n failed of 30". The six runs of a
  source are correlated; per-source counts are reported.

## Cost forecast

Observed in the last arm: 30 calls (28 usable), mean **21.05 s** per call,
644 s in all (0.18 h), plus one model load of about 2 min. This prompt is a few
lines longer and the reply about as long.

| Step | Calls | Low | Typical | High |
|---|---|---|---|---|
| Phase 0 (replay, placement) | 0 | 0 | 0 | 0 |
| Phase 1a: 10 cases | 10 | 0.05 h | 0.07 h | 0.17 h |
| Phase 1b: 20 cases | 20 | 0.10 h | 0.14 h | 0.33 h |
| **Total** | 30 | **0.15 h** | **0.21 h** | **0.5 h** |

(low = 15 s, typical = 25 s, high = 60 s per call, about three times the
observed mean). **Hard cap: 1 GPU hour** including the load, after which the
run stops and the bars are applied to what exists (the floors then report
their n); the last arm used 0.18 h of a 3 h cap. One Ollama job at a time. The
GPU is reserved by the owner: ask before the first model call.

## Exact commands

```sh
git rev-parse HEAD

# Phase 0 (no GPU)
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_roster/measure_meeting_event_roster.py replay
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_roster/measure_meeting_event_roster.py score --per-source 2   # placement only is read here

# Phase 1a (GPU released by the owner)
uv run python evals/meeting_event_roster/measure_meeting_event_roster.py compose --per-source 2
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_roster/measure_meeting_event_roster.py score --per-source 2   # read ONLY futility_stop

# Phase 1b (only if 1a did not stop)
uv run python evals/meeting_event_roster/measure_meeting_event_roster.py compose --per-source 6

# Model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_roster/measure_meeting_event_roster.py score
# label every to_adjudicate body in ADJUDICATION, commit, then:
OLLAMA_HOST=127.0.0.1:1 uv run python evals/meeting_event_roster/measure_meeting_event_roster.py decide
```

Model-free check: `uv run python evals/meeting_event_roster/measure_meeting_event_roster.py --self-test`
(also run by `evals/run_self_tests.py`). The stamp (harness commit, model digest,
hash of the prompts sent) is written under `compose/<model>/stamp.json`.

## Adjudication

As before, only E5 is a judgment, read only for an arm whose deterministic
bars (and E7) already hold. Each scored body is labelled `faithful`,
`unfaithful` or `contested` from the body, the description and the source,
keyed `"source/rN"`, **before `decide` is first run**. The reader is given
only the `to_adjudicate` list (no check outcome, no arm), and applies the last
arm's criterion unchanged: a participant, date, decision or object the source
does not state makes the body unfaithful. Whoever adjudicates is named in
RESULTS; a delegated reader is disclosed. A re-read after the verdict is a new
registration.

Disclosed here, because it limits what `r1` can show: the check encodes the
dominant reason the last arm's bodies were labelled unfaithful, so on `r1`
the survivors are close to faithful on participants by construction. `r1`'s E5
mostly tests whether other kinds of unfaithfulness (a date, an object, a
decision, a claim) remain. `r0`'s E5 tests whether the prompt alone prevents
the invention.

## Outputs

`RESULTS.md` beside this file: commit, model digest, cases scored, failed and
unrecovered, the replay numbers, per-bar results per arm with per-source
counts, the adjudication and who made it, the verdicts, the cost against the
forecast, and the limits. Bodies stay outside the repository; only counts,
Event titles and verdicts are committed. The verdict updates #1689 and nothing
else.

## Disclosed before scoring

- Seen: the whole of the last arm's RESULTS, and (by the author, to design the
  check) the 28 composed bodies and their recorded labels. No body of this arm
  exists.
- The replay numbers above were computed; the check had one revision after its
  first draft, described in Phase 0. No other number of this registration was
  looked at.
- No compose call has been made and the roster prompt was not tried on any
  source.
- The self-test was written first and observed failing (RED) for every pure
  function before it existed. Mutation checks, each turning it red: the noun
  matched as a participation verb, dropping the list split, never reporting
  an absent participant, the gate ignoring the check, removing the yield bar,
  and removing the whole-word guard (the first version of the test let this
  last mutation survive; a test with a prefix item, "Design" against
  "Designer", closed it).
- The thresholds are the last registration's, reused. New ones (R1, R2, E7,
  the 1 h cap) are round numbers chosen before any fresh result.

## Limits (assumptions, labelled)

- The check is a lexical proxy for "the source states this participant": a
  body can name a plausible participant that happens to occur elsewhere in the
  source (a name used for someone outside the meeting) and pass. It reads
  enumerations after a participation verb only; a participant named in a
  sentence without one ("la reunión contó con X") is not seen.
- It is fit to 28 bodies of one model on five sources; its generalisation is
  what phase 1 shows, on the same sources.
- Spanish bodies only, one corpus family (AMI machine translation), `qwen3:8b`,
  one sample per case, correlated cases, exact-title links; AMI carries no
  calendar date, so E3 mostly tests that none is invented.
- The roster is the speaker labels. A source without line-initial speaker
  labels gets `none labelled`; the prompt then asks for no participants at
  all. Such sources are not in this population.
- A deterministic alternative not tested here: the harness could write the
  participants sentence itself from the roster and let the model write only
  the rest. It removes the model from the failing step but also fixes the
  body's shape; it is a candidate if both arms are REJECTED.
- A rejected body (`r1`) is not re-asked; the Event is simply absent. Whether
  production should re-ask once is a design question for the OpenSpec change.

## The owner confirms

1. **The participant check**: the roster rule (`LABEL:` opening a line on at
   least two lines), the participation verbs (never the noun), the
   enumeration split, and "absent" meaning not a whole-word case-insensitive
   substring of the source; the check reads the body only.
2. **Phase 0 bars**: R1 (flags at least 0.90 of the unfaithful recorded
   bodies), R2 (at most 1 of 5 faithful wrongly flagged), and that CHECK-UNSOUND
   drops `r1` but still runs `r0`. The replay reuses the last arm's recorded
   labels and adjudicates nothing new.
3. **The arms**: `r0` (roster prompt) and `r1` (the same bodies behind the
   check), one set of 30 calls, the same 30 cases.
4. **The roster prompt** wording in `EVENT_ROSTER_SYSTEM_PROMPT` and the
   `SPEAKERS IN THE SOURCE (complete list)` line, frozen once this file merges.
5. **The bars**: E1 to E6 and the INCONCLUSIVE floors reused unchanged; E5 at
   0.90 with contested against; the new E7 (at least 20 surviving bodies,
   REJECTED not INCONCLUSIVE below it).
6. **The futility stop**: unchanged, deterministic bars only, no faithfulness
   read after phase 1a.
7. **GPU time**: forecast 0.15 / 0.21 / 0.5 h, hard cap 1 GPU hour, and when
   the GPU is free (ask before the first model call).
8. **Adjudication**: a delegated reader disclosed in RESULTS (or the owner),
   given only the bodies, applying the last arm's criterion, with E5 read only
   for an arm whose deterministic bars and E7 hold.
9. **The reading of the two arms**: `r0` LICENSED makes the check unnecessary;
   `r0` REJECTED with `r1` LICENSED licenses prompt plus check; both REJECTED
   closes this family until a new registration.
