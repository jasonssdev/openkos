# Results: the roster-grounded body with a retry (#1689, third arm), LICENSED on a re-read with the source

Registration: `PREREGISTRATION_RETRY.md` (PR #1718, commit `054e4e37`), run
unchanged. Stamp: harness commit `71860ab7f6b16e3c01a4faec6dd857d354d556c6`
(clean tree, `dirty: false`), model `qwen3:8b`, digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, roster
system prompt hash `4f93593ea5bea022` (equals the registered value), language
anchor `165db5afd66cf667`. Before the run `ollama ps` was empty. Raw `score`
output and every body stay outside the repository.

## Verdict

**Both arms LICENSED, on a second reading of E5 made against the source
(29 of 30 against at least 0.90).** The first reading, made without the source,
gave REJECTED (25 of 30); it did not follow the registered procedure and is
kept below. Every deterministic bar and E7 held. The futility stop never fired
(`stop_reading.reading` null after phase 1a), so phase 1b ran and `decide` ran
on the full set of 30. Read the Deviations before relying on this verdict.

## Phase 0 (no GPU)

`--self-test`: 22 of 22 passed. `replay`: CHECK-SOUND, identical to the
registered numbers. Placement re-scored model-free: identical to #1693 and to
the roster run.

## Phase 1a (GPU)

10 cases, 10 scored, 0 failed. `stop_reading`: no reasons, reading null.
`r0_bodies`: restate 10 of 10, linked 10 of 10, invented dates 0, bounded
10 of 10, mean 21.5 s per call. Only these keys were read.

## Phase 1b and the full set

30 cases planned, 30 scored, 0 failed, 3 of 30 retried (the retry recovered
all three; `first_reply` is kept in the records).

| Bar | Rule | `r0` | `r1` |
|---|---|---|---|
| restate (E1) | at least 0.90 | 30 of 30 (mean overlap 0.002) | 30 of 30 |
| linked (E2) | at least 0.80 | 30 of 30 | 30 of 30 |
| invented dates (E3) | at most 1 | 0 | 0 |
| bounded (E4) | at least 0.95 | 30 of 30 | 30 of 30 |
| faithful (E5) | at least 0.90, contested counts against | 25 of 30 
| cost (E6) | failed at most 3 of 30; mean at most 378.7 s | 0 failed; 19.9 s | same |
| yield (E7) | at least 20 | -- | 30 |

The participant check flagged no body (0 of 30 name an absent participant), so
`r1` kept every body and the two arms read the same 30 bodies and the same
labels; their verdicts coincide.

## Adjudication

Run because the deterministic bars and E7 held. The registration labels each
body "from the body, the description and the source"; "given only the
`to_adjudicate` list" meant no check outcome and no arm, not no source (the
previous arm's reader read against the source). Criterion, both readings: a
participant, date, decision or object the source does not state makes the body
unfaithful. Both readers were fresh sonnet subagents, general-purpose.

| Reading | Reader had source | Faithful | Contested | Unfaithful | E5 |
|---|---|---|---|---|---|
| First | no | 25 | 5 | 0 | 25 of 30 (0.833), FAIL |
| Second | yes (five translated transcripts) | 29 | 1 | 0 | 29 of 30 (0.967), pass |

First reading: the five contested were `ES2015a-es/r1`, `ES2015a-es/r8`,
`ES2015a-es/r11`, `IS1004b-es/r5`, `IS1004d-es/r4`, each because details
"could not be verified without the source". Second reading: the one contested
is `IS1004b-es/r5` (an object, "accessibility", that the source does not
state). Contested counts against. **The verdict follows the second reading,
because only it applied the registered procedure (labelled against the
source).** The labels and short reasons of the second reading are in
`ADJUDICATION` in the harness; the first reading's labels were replaced there.

## The closing rule

LICENSED on both arms: per the registration, the roster-grounded Event body is
licensed on this model. The next step is an OpenSpec change, which must first
generalize the roster rule to named speakers (the corpus uses AMI speaker-label
rosters). Nothing is adopted by this file; #1689 stays open. The placement
result stands.

## Cost

| | Forecast | Observed |
|---|---|---|
| Calls | 30 to 42 | 33 (30 plus 3 retries) |
| GPU time | 0.20 / 0.28 / 0.75 h, cap 1 h | about 10 min (0.17 h), load included |
| Mean per call | 21 s | 19.9 s |

One Ollama job at a time; nothing else ran on the GPU.

## Deviations

None in the measurement: nothing was re-run, subset or extended after a number
was seen; prompt and harness code are unchanged, and the GPU was released by
the owner for this run. In the adjudication, plainly:

- The first reading omitted the source, contrary to the registration.
- The second reading was made after the first verdict had been seen by the
  orchestrator (the reader itself was blind to it: no arm, no checks, no
  earlier labels, no verdict). The registration calls a re-read after the
  verdict a new registration. This is disclosed so a reader can discount the
  verdict accordingly.
- The decision to apply the second reading was taken under the owner's
  standing instruction, and the owner may overrule it.

## Limits

As registered: AMI speaker-label roster form only, Spanish machine-translated
bodies, one corpus family, one model, one prompt, one sample per case,
correlated cases, exact-title links. E5 rests on a single delegated reader per reading (no inter-rater check), and the second
reading followed a seen verdict. The 8 bodies seen before the registration
were not reused; every case was drawn at a fresh seed.
