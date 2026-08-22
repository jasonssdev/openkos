# The private-corpus arm, 2026-08-21

`--source ~/openkos-e2e-028/raw/transcription2.md`, `qwen3:8b`, 3 runs.
The source is NOT committed (real names and addresses); only the
per-section verdicts are recorded here, never its text or its objects.

```
## transcription2.md -- UNADJUDICATED (3 ok runs)
   - 8 checkable section(s); flagged per run: 7, 6, 7
   - uncovered share of checkable text per run: 98.0%, 31.3%, 97.6%
   - no adjudicated expectations for this file -- this arm measures how LOUD the signal is here, not whether it is right

   section                                        flagged  expectation
   ## Información de la reunión                     100%  (unadjudicated)
   ## Resumen                                        67%  (unadjudicated)
   ### Definición del alcance técnico               100%  (unadjudicated)
   ### Metodología y herramientas tecnológicas       67%  (unadjudicated)
   ### Validación mediante datos reales              67%  (unadjudicated)
   ## Próximos pasos                                100%  (unadjudicated)
   ## Detalles                                       67%  (unadjudicated)
   ## Notas de Gemini                               100%  (unadjudicated)

   uncovered share of checkable text per run: 98.0%, 31.3%, 97.6%
```

# The same arm, widened to 8 runs and swept over `overlap`, 2026-08-22

Same source, same discipline: shares and counts only, never its text and
never its objects. `qwen3:8b`, 8 runs, generation ceiling 8192,
`union_judge` on. The 8 runs produced 8, 9, 9, 9, 5, 8, 9 and 6 objects.

`overlap`, uncovered share of checkable text per run, by threshold:

```
B = 0.05   3%,  3%,  3%,  3%,  3%,  3%,  3%,  3%
B = 0.10   5%,  5%,  3%,  3%,  5%,  5%,  3%,  3%
B = 0.15   5%,  5%,  3%,  3%,  5%,  5%,  3%,  5%
B = 0.20   5%,  5%,  3%,  3%, 24%,  5%,  3%, 24%
B = 0.25   5%, 24%,  3%,  3%, 24%,  5%,  3%, 24%
B = 0.30  24%, 92%,  5%,  5%, 92%,  5%, 24%, 92%
B = 0.40  92%, 92%, 92%, 24%, 92%, 92%, 92%, 92%
B = 0.50  92%, 92%, 92%, 24%, 92%, 92%, 92%, 92%
```

`quote`, the refuted baseline, on those SAME 8 runs:

```
          100.0%, 30.4%, 28.0%, 30.4%, 98.0%, 100.0%, 30.4%, 97.6%
```

Runs 7 and 8 produced the same object count (8) and scored 30.4% and 97.6%
under `quote` — a 67-point spread at equal output, which is what
verbatim-quoting luck looks like when it is charted as coverage.

Regenerating this block needs the source, which is not here. Against your own
copy the ladder is one invocation -- `--rescore <your sweep>.json` with one
`--overlap-threshold` per rung -- and a `--source` sweep is never written to
`results/`, so that sweep file lives outside the repo and only the shares
above come back into it.

This arm is still UNADJUDICATED, for both predicates: nobody has recorded
which of these sections SHOULD have produced an object, so a high share here
cannot be told apart from a run that genuinely extracted badly. It measures
loudness. The 3-run block above is the earlier, narrower sample of the same
source and is kept rather than replaced.
