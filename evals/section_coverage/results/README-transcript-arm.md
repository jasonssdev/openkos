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

# The under-fire arm on the same private source, 2026-08-23

`--source ~/openkos-e2e-028/raw/transcription2.md`, `qwen3:8b`,
`--leave-one-out`, run TWICE at 5 runs each. Same discipline as every arm
above: counts and verdicts only, never the source's text and never its
objects.

Per section, the objects that verbatim-quote it are deleted and the
predicate is asked again. `BLIND` counts a section that was covered, lost
every object quoting it, and was STILL called covered.

**Observation 1** (5 runs producing 10, 6, 9, 10 and 9 objects):

```
   predicate     trials   NAMED   BLIND  hit rate
   overlap@0.2        5       0       5      0.0%
   overlap@0.25       5       0       5      0.0%
```

**Observation 2**, an independent 5 runs producing 6, 17, 12, 2 and 13
objects, and the first to carry the exclusion counts:

```
   predicate     trials   NAMED   BLIND  hit rate  (skipped: already uncovered)
   overlap@0.2        6       0       6      0.0%  (0 over 5 ok run(s))
                 excluded: 34 unquoted, 10 unscorable, 0 total-removal
   overlap@0.25       6       0       6      0.0%  (0 over 5 ok run(s))
                 excluded: 34 unquoted, 10 unscorable, 0 total-removal
```

Two independent 5-run observations, both unanimous: at both rungs of the
measured window the signal named **none** of the constructed losses. In the
same runs it flagged `## Información de la reunión` and `## Notas de Gemini`
on every run of observation 1 — both of which produced objects.

## Read the exclusions before the trials

Observation 2's denominator is the finding underneath the finding. Across 5
runs the arm reached **6 scorable section-trials out of 50 section-runs**:
34 were excluded because no object quoted the section verbatim, and 10
because `overlap` could not check them at all.

So the small `trials` number is not this source having few sections — it is
discursive extraction rarely quoting verbatim, which is the same fact that
refuted `quote`. It caps how much this arm can ever say about a transcript,
and it is why the two observations are reported side by side rather than
pooled into one larger-looking number.

Observation 1 predates the exclusion counts and cannot be re-derived: its
run was never stored, on this arm's own privacy rule. It is kept as an
independent replication of the verdict, not as an audited denominator.

## `quote` was not selected here, and could not have been

An earlier draft of this file claimed the table showed `quote` as
`NOT SCORABLE`. It does not, and the claim was false of these runs while
being true of the code — the one direction a results file must never drift.

Both invocations pass `--overlap-threshold` with no `--predicate`, and
`select_predicates` scores only the swept columns on that branch, so `quote`
was never selected and no row for it could be printed. The arm does refuse
it — `leave_one_section_out` raises on any predicate whose `covers_by_quoting`
is set, because it covers by the same `evidence_line` rule the arm
attributes by — and since this round the CLI refuses the whole invocation
rather than printing a table of `NOT SCORABLE` rows. That refusal is proved
in the probe's self-test, not here.
