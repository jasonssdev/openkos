# Results: a re-ask when the filtered reply is empty (#1690)

Registration: `PREREGISTRATION.md` (merged in #1692, commit `84bca364`), run
unchanged. Run at commit `7210773614fb906722e7d4661d71423978f6ffec` (clean
tree, stamp `dirty: false`). Model `qwen3:8b`, digest
`500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, production
defaults. Prompt hashes in the stamp: `reask-empty-system` `dfa4403292f64844`,
`language-anchor` `165db5afd66cf667`. Raw records stay outside the repository.

## Verdict

**REJECTED**, as `decide` prints it, on **B1** (benefit) and **B3** (subject
quality). The futility stop fired after phase 1, so phase 2 (fresh runs 16 to
30) was never started, exactly as registered. Nothing is licensed: no
production change, no OpenSpec delta.

## What happened

Phase 1 re-asked the 8 empty stored `ES2015a-es` runs, 3 samples each: 24
calls. **All 24 replies were `[]`** (2 generated tokens each, finish reason
`stop`, no thinking, no failed call). The anchor-free prompt recovered zero
objects, so 24 of 24 trials (1.00) ended empty against the 0.75 futility
threshold.

## Records and counts

| | |
|---|---|
| records scored | 75 of 75, 0 failed, 0 excluded |
| `a0` empty (replayed) | 8 of 75; 8 of 15 on `ES2015a-es` (0.533) |
| re-ask trials | 24 (8 runs x 3 samples), 0 failed, 0 missing |
| `a1` empty after re-ask | 24 of 24; expected zeros 8.0 (equal to `a0`'s 8) |
| objects added | 0 (0 subject, 0 slipped) |
| invariant violations | 0 |

## Bars

| Bar | Rule | Value | Result |
|---|---|---|---|
| B0 defect exists | `a0` zero rate on `ES2015a-es` at least 0.20 | 0.533 (8 of 15, runs 1 to 15 only) | holds |
| B1 benefit | `a1` expected zeros at most half of `a0`'s 8 (at most 4) | 8.0 | **fails** |
| B2 safety | slipped at most `max(1, floor(24 / 20))` = 1 | 0 | holds (vacuously: nothing was added) |
| B3 subject quality | at least half of added are subjects, and at least one added | 0 added, share 0.0 | **fails** |
| B4 cost | failed calls at most 0.10; mean seconds per call at most mean extraction run | 0.0 failed; 0.69 s per call vs 207.8 s per run | holds |

B0 was tested only on the stored runs 1 to 15 (already known to meet it; see
the registration's "Disclosed before scoring"). The fresh runs that could still
have tested it were not generated, because the futility stop ended the run.
B2 holds only because there was nothing to slip; it says nothing about the
filters' safety under a prompt that does emit objects.

## Cost against forecast

| | Forecast | Actual |
|---|---|---|
| Phase 1 (24 calls) | 0.3 / 0.6 / 1.5 h | about 29 s wall-clock including a 4.1 s warm-up |
| Phase 2a and 2b | 0.4 to 4.5 h | not run (futility stop) |
| Total GPU | 1.0 / 2.1 / 6.0 h (cap 8 h) | about 0.01 h |

The calls are cheap because a reply of `[]` is two tokens and the 4814-token
prompt prefix is identical across samples of one source (one 12.9 s call, the
rest under one second). The per-call mean (0.69 s) is far below the
forecast's 40 s for a clean reply, and is not a runaway.

## Adjudication (disclosed)

No object was added by any trial, so there was nothing to classify and the
`ADJUDICATION` registry stays empty. The registration allowed a delegated
reader; none was needed. No private-corpus title appears in this file.

## Deviations

None from the registered commands. The futility check was run with
`score --max-run 15` and its output was read in full rather than only the
`futility_stop` field (the harness prints one JSON object, and the redirect
did not hide it); the numbers did not influence any choice, since the stop
applies whenever `futility_stop` is true. The final `score` and `decide` ran on
the phase-1 data only.

## Reading

The repeated `[]` is a single prompt wording on a single source and model: the
model, asked again without the anchor and told `[]` is correct, answers `[]`
every time (8 of 8 runs, 3 of 3 samples each). Per the registration, a
rejected verdict rejects this wording on this source, not every possible second
ask. A re-read of the stored replies or a re-tuned prompt is a new
registration. The 75% futility threshold was reached at its maximum (100%), so
this is not a near miss.
