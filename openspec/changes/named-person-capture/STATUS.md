# Status: PAUSED after slice 1 — 2026-08-15

This change is **paused, not abandoned**. Slice 1 shipped; slices 2-4 are
blocked by their own measurement.

## What slice 1 decided

The Phase 0.2 gate ran and returned **REJECT** on the D2 capture-prompt
treatment. Two of the four reject conditions fired independently:

| Condition | Result |
| --- | --- |
| Subject recall drops below baseline | did not fire — both arms at 0.00 (see below) |
| Run latency ≥ 1.5× baseline | **FIRED** — 104.7s vs 54.6s (1.92×) |
| Merely-named person count does not increase | **FIRED** — 0 vs 0 after adjudication |
| A proposed name is absent from the source | did not fire |

Per design D2, a rejection ships nothing prompt-level. The rewrite stays in
`evals/named_person_volume/` as a reproducible monkeypatch. The derived
capacity `_PARTICIPANT_BACKSTOP = 8` (from `p_max = 3`, the floor binding) is
recorded but unused.

## Why the remaining slices are blocked rather than merely unstarted

Slice 1 measured something that removes slice 3's premise. On meeting-shaped
sources the pipeline retains **people and nothing else** — zero
`Decision`/`Event`/`Concept`/`Procedure` objects survive:

| fixture | retained | of which participants | subjects |
| --- | --- | --- | --- |
| `es-bare`, 6 runs, both arms | 3 | 3 | **0** |
| `ami-ts3005a`, baseline, 2 successful runs | 4 | 4 | **0** |
| `es-anchored` (`evals/participant_anchor`, #706, 3 runs) | 5 / 5 / 4 | 5 / 5 / 4 | **0** |

The last row is #706's own stored data, re-read at this angle. `es-anchored`'s
prose contains an explicit decision ("Que quede la decisión: el corpus de actas
… se incorpora al proyecto bajo convenio") and no `Decision` object survived any
run.

The two-lane budget (slice 3) exists to stop participants from crowding
subjects out of `_UNION_BACKSTOP`. Subjects are not being crowded out: the
backstop is 20 and only 3-5 objects were retained, so subjects are eliminated
long before any capacity limit binds. Building the lane now would fix a
competition that is not happening.

That defect is tracked separately and outranks this change.

## What is still valid here

- The owner rulings in `proposal.md` (always identify people; participants get
  their own lane; merely-named persons carry the full lifecycle) are unchanged.
- `exploration.md`'s mechanism map, `design.md`'s D1/D3/D5/D6/D7 and all three
  delta specs remain accurate and reusable.
- What is invalidated is D2's specific prompt rewrite, on its own measurement.

## Resuming

Re-enter after the subject-retention defect is understood. At that point
re-open `design.md` — the right mechanism for "always identify people" may look
different once it is known why subjects do not survive, since participants
currently reach the retained set through a deterministic re-admission path that
subjects have no equivalent of.
