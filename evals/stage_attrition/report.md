# Where the subjects die (#715)

qwen3:8b, 3 fixtures × 3 runs, 2026-08-15. Every transforming stage of the
union pipeline recorded on entry and exit. Re-derivable from
`results/*.jsonl`.

## The answer inverts the question

**Nothing kills the subjects. They are never generated.**

`_extract_once` — the generation call itself — produces **exactly one
subject candidate per call on a meeting-shaped source, and it is the Event
named after the meeting**:

| fixture | extraction calls | subjects per call | what they were |
| --- | --- | --- | --- |
| `es-anchored` | 6 | 1, 1, 1, 1, 1, 1 | `Event: Reunión de coordinación del proyecto de memoria institucional` ×6 |
| `es-bare` | 6 | 1, 1, 1, 1, 1, 1 | `Event: Reunión semanal de operación` ×6 |
| `ami-ts3005a` | 6 | 1, **5**, 1, 1, 1, 1 | `Event: AMI meeting TS3005a` ×6, plus one call that also produced a Project, a Concept, a Decision and a Procedure |

12 of 12 calls on the two Spanish fixtures produced the framing Event and
nothing else. `es-anchored`'s prose contains an explicit decision — *"Que
quede la decisión: el corpus de actas de Vega Ingeniería se incorpora al
proyecto bajo convenio"* — and no `Decision` candidate was ever proposed for
it, in any of six calls.

## The stage tally, and why its headline is misleading

| stage | subjects killed | of seen |
| --- | --- | --- |
| `_drop_framing_objects` | 18 | 22 |

That is the only eliminating stage, and it is **working exactly as
specified**. Every one of those 18 deletions is one of three titles:

```
ami-ts3005a   ×6  Event  AMI meeting TS3005a
es-anchored   ×6  Event  Reunión de coordinación del proyecto de memoria institucional
es-bare       ×6  Event  Reunión semanal de operación
```

Three distinct framing titles, each seen once per extraction call (2 passes ×
3 runs). Deleting an object that merely names the source document is precisely
what #522/#533 measured and shipped this rule to do. It is not over-firing: it
never touched a single non-framing candidate.

## The mechanism, stated plainly

Two shipped rules meet on meeting-shaped sources and their intersection is
empty:

1. **`_SYSTEM_PROMPT`'s anti-enumeration paragraph** tells the model that *"a
   meeting transcript is fundamentally about the meeting itself (an Event) and
   any Decisions reached"*. The model obeys the first half and stops: it emits
   the Event.
2. **`_drop_framing_objects`** then deletes that Event, because an object
   naming the whole source is a framing stub.

Generation is instructed that the meeting IS the subject; the deterministic
filter holds that the meeting is NEVER a subject. Between them, a meeting
yields nothing — unless the model volunteers more than it was asked for, which
happened in exactly 1 of 18 calls.

## What this exonerates

#715 named three suspects. Two are cleared by this ledger:

- **`judge.select`** — cleared. On every run the judge received the
  post-framing-drop set, which on the Spanish fixtures contained zero
  subjects. It cannot drop what never reaches it. On `ami-ts3005a` run 1,
  where generation *did* produce 5 subjects, **4 of them survived to the
  retained set** (8 retained: 4 subject + 4 participant).
- **The survival asymmetry** (participants reach retention deterministically,
  subjects do not) — real, but not the cause here. AMI run 1 shows subjects
  reaching retention through the ordinary path whenever they exist.

## A refinement of #459

`_build_messages` omits a meeting-shaped title from the prompt entirely, and
`_build_messages`'s own docstring records why: on `TS3005b` extraction
collapsed to 1 object in 20/20 **chunked** runs under the title, and produced
8 in 5/5 runs with the line omitted.

That fix was measured on the chunked path. These fixtures take the
**unchunked two-pass path** (5.4 KB, 3.1 KB, 16.4 KB — all under
`_CHUNK_THRESHOLD` = 18 000), and the collapse to a single Event is present
there, with the title already omitted. Whatever the omission bought on chunked
sources, it does not carry to this path.

## What this probe does not answer

Why the model stops after the Event. The paragraph asks for "the Event and the
Decisions"; only the Event arrives. Whether that is the paragraph's wording,
the no-title branch removing the only in-language framing, the two-pass path,
or the model tier, is unmeasured here. Any treatment aimed at it needs its own
arm and its own reject rule — this repo has rejected four measured prompt
treatments (#563, #613, #622, #712 slice 1).

Both candidate fixes touch a prior measured decision: the anti-enumeration
paragraph is pinned verbatim (`tests/unit/extraction/test_concept.py:1488`,
measured via #380), and the framing rule was measured in #522/#533. Neither
should be changed without a ruling and a measurement.

## Method note

Two bugs in this probe were caught by its own `--self-test` before any model
call. The load-bearing one: every stage wrapper closed over the recorder's
event list at install time, while `reset()` rebound it to a fresh list — so the
wrappers appended to an orphan and the ledger reported empty. A full sweep
would have produced a blank ledger reading as *"no stage drops anything"*. The
self-test's first assertion is that the recorder observed anything at all,
which is why it surfaced.
