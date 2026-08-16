# Title-first phase 1, measured — REJECTED, and the reason is worth more than the verdict

`qwen3:8b`, 6 runs per arm, 2026-08-16, on `medium-10-reunion-plataforma` (the
#694 oracle transcript, chunked path). Raw runs and the rendered table are in
`results/`.

**Reproduce:**

```bash
uv run python -u evals/title_first/run_title_first_probe.py --runs 6
uv run python evals/title_first/run_title_first_probe.py --self-test   # no model
```

## The bar, ruled before measuring

Adopt only if wall clock improves outside the noise band **AND** quality stays
inside the #694 oracle's: recall 0.80 ±0.12, precision 0.95 ±0.08.

## Result

| metric | baseline | title-first | oracle band |
| --- | --- | --- | --- |
| **wall clock (s)** | 165.7 ±24.0 | **87.0 ±7.5** | — |
| recall | 0.82 ±0.11 | **0.62 ±0.09** | 0.68–0.92 |
| precision | 0.95 ±0.08 | **0.62 ±0.13** | 0.87–1.00 |
| retained objects | 10.5 ±2.0 | 11.5 ±2.1 | — |
| errored runs | 0 | 0 | — |

**REJECTED.** Wall clock nearly halves — a 1.9x speedup, far outside the noise
— and quality falls out of the band on **both** axes. Recall lands below the
floor; precision lands two-thirds of the way to the floor from zero.

The baseline reproduced the oracle (recall 0.82 against 0.80 ±0.12, precision
0.95 against 0.95 ±0.08), so this is a treatment effect and not a drifted
baseline.

## Why it fails: the discarded body was the brake

The confound control is what explains the verdict. Title overlap between arms
is **0.29** — 10 shared titles of 35 distinct, with 19 treatment-only against 6
baseline-only. The arms did not do the same work faster; the treatment did
**different, larger** work.

The candidate counts say it plainly:

| | baseline | title-first |
| --- | --- | --- |
| candidates proposed per run | ~10.5 | **21–27** |

Phase 1 proposes roughly **2.3x more candidates**. And the titles it adds are
the enumeration-decay signature this corpus already has a vocabulary for —
conjunctive titles and over-broad containers:

- `Cifrado de respaldos y custodia de la llave` (5 runs)
- `Modelo de embeddings y generación de vectores` (3)
- `Cumplimiento de políticas de datos personales` (3)
- `Almacenamiento y respaldos`, `Infraestructura de la plataforma`,
  `Seguridad de la información` (1 each)

**The mechanism: writing the body is itself the restraint.** When proposing an
object costs a paragraph, the model commits and stays few; when it costs a
title, it enumerates. `_SYSTEM_PROMPT`'s anti-enumeration paragraph (#380,
pinned) asks for fewer-and-richer objects, and the full reply shape is what
gave that instruction teeth.

That reframes #728's own premise. The 81–100% "waste" is not purely waste:
part of it was the price the pipeline paid for restraint. A repair that
recovers the characters without replacing the brake buys latency with
precision.

This is the sixth prompt-shaped treatment this repo has measured and rejected
(#613, #622, #715 slice 1, #713's first shape, #699 carry-titles), and it fails
in the family's usual way — the instruction changed *what* the model proposed,
not only how it formatted it.

## What the sweep does establish

**The latency prize is real and large.** The treatment ran **48 chat calls**
(24 survey + 24 hydrate) against the baseline's 24, doubled the round trips,
re-sent every window — and still finished in 53% of the time. Generation
dominates wall clock on this material by a wide margin, exactly as #692's
character accounting implied. Any future lever that removes generated
characters without moving the candidate set should expect a win of this order.

Secondary numbers:

- **Framing objects killed before a body was written: 2–3 per run.** The
  recovery mechanism works exactly as #728 predicted; it is the collateral
  that fails.
- **Hydration losses: 0–1 per run** (3 of 24 hydration calls). A survivor the
  hydration call does not return is dropped and counted, never back-filled from
  its survey title — a candidate with an invented description is worse than an
  absent one. Small, but a loss channel the baseline does not have, and it
  would need closing before any adoption.

## Where a next attempt should start

Not with a wider gate or a different floor. With **restoring the commitment
cost**: have phase 1 write `title` + a one-line `description`, and hydrate only
the `body`.

The arithmetic still favours it — #728 measured the framing object's body at
roughly 5 600 characters against a description of one or two sentences, so the
body is nearly all of the recoverable tail — while the description keeps
proposing an object expensive enough to think about.

That is a different arm, not a tuning of this one, and it must clear the same
bar including the overlap control. Filed as a follow-up rather than run here:
the scope for this round was option 2 alone.

## Scope, stated honestly

- **Production is unchanged.** The lever lives in the probe and patches
  `_extract_once` for the duration of a run.
- **The arms are asymmetric by construction**: baseline is production code,
  treatment is production code with one function replaced. Everything else —
  chunking, dedup, the twin rule, the language gate, the re-ask, participant
  capture, the judge, the backstop — is byte-identical in both.
- **The lever does not recover every title-only kill.** Kills charged to
  `_drop_source_title_twins`, `_dedup_merged` and `_drop_wrong_language_titles`
  run on the merged list, and the twin rule's floor reads the whole set;
  applying it per window decides on a set no source ever emitted (#581). Only
  `_drop_framing_objects`, a stateless per-object predicate, moves.
- **One fixture.** `medium-10-reunion-plataforma` is the only meeting-shaped
  source in this repo with adjudicated title-level ground truth, and #728's bar
  is a quality bar. The fixtures the waste was originally measured on
  (`es-anchored`, `es-bare`, `ami-ts3005a`) cannot score recall or precision at
  all.
