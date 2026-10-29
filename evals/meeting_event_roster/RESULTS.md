# Results: a roster-grounded meeting Event body (#1689, second arm)

Registration: `PREREGISTRATION.md` (PR #1713, stacked on #1711 and #1712; owner
confirmations recorded in its section "The owner confirms", commit
`031b89aa0bd3540f14b90a28f2c17bd3e4d6c004`), run unchanged at that commit
(clean tree; the stamp reports `dirty: false`). Model: `qwen3:8b`, digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`
(`ollama list` id `500a1f067a9f`). Prompt hashes: roster system prompt
`4f93593ea5bea022`, language anchor `165db5afd66cf667`. Before the run
`ollama ps` was empty. Raw `score`/`decide` output and every body stay outside
the repository.

## Verdict

**Both arms INCONCLUSIVE, as printed by `decide`: `r0` INCONCLUSIVE, `r1`
INCONCLUSIVE** (floor: 8 bodies scored, 20 required). **The registered
futility stop fired after phase 1a** (`futility_stop: true`), which ends the
run towards REJECTED for both arms and never towards LICENSED; phase 1b was
not started. Nothing is licensed. E5 was not adjudicated for either arm, as
registered (no deterministic-bar-holding arm reaches it).

## Phase 0 (no GPU)

`replay` (with `OLLAMA_HOST=127.0.0.1:1`): 28 recorded bodies, 23 unfaithful
all caught (R1 1.00 against 0.90), 0 of 5 faithful wrongly flagged (R2 0
against at most 1): **CHECK-SOUND**, identical to the registered numbers.
Placement re-scored model-free: Spanish 75 records, 67 qualifying, 10 beyond
the cap; English 254 records, 231 qualifying, 153 beyond the cap; displaced 0,
bad cardinality 0, overflow 0 on both. P1 and P2 hold, identical to #1693.

## Phase 1a (GPU)

10 cases planned (2 per source), 10 calls made, 8 scored, 2 failed. The two
failures are `ES2015a-es/r1` and `ES2015d-es/r2`: their replies carry no
closing brace (cut off after the last object title), so they did not parse and
count as failed. The call log reports no transport failure for either.

| Bar (phase 1a, 10 of 30 planned) | Rule | Value |
|---|---|---|
| restate (E1) | at least 0.90 | 8 of 8 (mean overlap 0.008) |
| linked (E2) | at least 0.80 | 8 of 8 |
| invented dates (E3) | at most 1 | 0 |
| bounded (E4) | at least 0.95 | 8 of 8 |
| mean seconds per call (E6) | at most 378.7 | 21.1 s |
| absent participant (the check) | -- | 0 of 8 bodies name one; yield 8 |

**Why the futility stop fired.** E4 needs ceil(0.95 x 30) = 29 passing bodies
out of the planned 30. With 2 of the first 10 failed (counted as not passing)
and 8 passing, even 20 more perfect bodies reach 28. The bar is out of reach,
so the registered stop applies. Restate and linked were still reachable; only
bounded was not. The stop reads no faithfulness and no check outcome.

`decide` over the stored 10 (the whole registered set, 8 scored) prints
INCONCLUSIVE for both arms because 8 scored is below the floor of 20. The
INCONCLUSIVE floor says "reported, never rescued by more runs"; the futility
stop says "towards REJECTED, never LICENSED". Both are reported; neither
licenses anything.

## Per-arm reading (item 9)

- `r0`: not LICENSED. The prompt alone is not shown to suffice.
- `r1`: not LICENSED. The check has no yield problem in phase 1a (8 of 8
  survive), but with no E5 read it licenses nothing.
- Reading together: neither arm is licensed; the Event body is not licensed on
  this model by this registration. Per the registration, no further arm of
  this family is proposed without a new registration.

An observation, not a finding: in the 8 scored bodies the model named exactly
the roster labels (PM, ID, UI, ME) in a participation sentence, and not one
"other members of the team" as the previous arm did (19 of 28 bodies). Two
of 10 replies were truncated. Faithfulness was not read.

## Cost against the forecast

| | Forecast | Observed |
|---|---|---|
| Calls | 30 (10 in 1a) | 10 made, phase 1b not started |
| GPU time | 0.15 h low, 0.21 h typical, 0.5 h high, cap 1 h | about 216 s (0.06 h) including the model load, 1a only |
| Mean per call | 25 s typical | 21.1 s |

One Ollama job at a time; nothing else ran; Ollama was not restarted.

## Adjudication (disclosed)

None. The delegated reader (the agent that ran this measurement) labelled no
body: the registration reads E5 only for an arm whose deterministic bars hold,
and the futility stop ended the run first. `ADJUDICATION` is unchanged.

## Deviations and oddities

- The reader printed the whole `score --per-source 2` output rather than only
  `futility_stop`, so the 8 scored bodies, their descriptions and the check
  outcomes were seen after phase 1a, not just the stop field. No decision used
  them: the stop is a deterministic count, and no label was written. Recorded
  so the limit on what was read is visible.
- The first `decide` call ran before any label existed (none were needed); it
  printed INCONCLUSIVE for both arms.
- The registration says to ask the owner for the GPU; the owner's instruction
  to this run stated the GPU was free.
- Nothing was re-run, subset or extended after a number was seen.

## Limits

As registered: Spanish bodies only, one corpus family, one model, one prompt,
one sample per case, correlated cases, exact-title links; 8 scored bodies are
too few to say anything about faithfulness, and the roster rule reads 2 to 4
capital speaker labels (the AMI form), so it does not generalize as written to
transcripts whose speakers are named; any production use needs its own roster
rule. The futility stop was triggered by two truncated replies, not by a
content failure; whether a larger reply budget or a re-ask would have changed
that is untested and would be a new registration.

## What the verdict licenses

Nothing. The placement result (P1, P2) and the replay result (CHECK-SOUND, a
fit on recorded bodies) stand. A follow-up would be a new registration, for
example the deterministic alternative named in the preregistration (the harness
writes the participants sentence from the roster), or a re-ask once on an
unparsable reply; neither is proposed here.
