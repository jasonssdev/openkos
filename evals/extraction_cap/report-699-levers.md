# The two #699 levers, measured — both REJECTED

`qwen3:8b`, union+judge, 8 runs per arm, 2026-08-15, on
`medium-10-reunion-plataforma` — the fixture #694 built with one subject
deliberately split across a window boundary.

**Reproduce it:**

```bash
uv run python -u evals/extraction_cap/run_cap_eval.py \
  --fixture medium-10-reunion-plataforma --runs 8 --union-judge on \
  --lever carry-titles --lever chunk:8000 \
  --output evals/extraction_cap/results/<your-file>.md
```

Owner ruling this was measured against: ship a lever when recall rises above
the noise band and precision does not fall outside its interval, each lever
judged alone. Neither cleared it. Production is untouched by either lever;
what shipped is this measurement, the harness axis that produced it, one
correctness fix the axis exposed, and 25 adjudications.

## The table

| metric | baseline | +carry-titles | +chunk8000 |
| --- | --- | --- | --- |
| subject recall | **0.80 ±0.12 [0.64–0.91]** | 0.76 ±0.13 [0.64–0.91] | 0.69 ±0.09 [0.64–0.82] |
| subject precision | **0.95 ±0.08 [0.82–1.00]** | 0.87 ±0.16 [0.57–1.00] | 0.95 ±0.06 [0.88–1.00] |
| mean produced | 9.38 | 10.00 | 8.00 |
| **redundant subject emissions / run** | **0.12** (max 1) | **0.62** (max 3) | **0.00** (max 0) |
| near-duplicates / run | 0.12 | 0.25 | 0.00 |
| out-of-scope / run | 0.00 | 0.38 | 0.14 |
| known facets / run | 0.38 | 0.38 | 0.29 |
| unjudged titles | 0.00 | 0.00 | 0.00 |
| distinct title sets | 7/8 | 6/8 | 5/7 |
| runs errored | 0 | 0 | **1 of 8** |
| n | 8 | 8 | 7 |

`redundant subject emissions` is the count of positions naming a subject the
same reply already named — the operational definition of #699's within-source
fragmentation, computed from the saved runs against the adjudicated ground
truth. It is the row this issue turns on, and it is the row that is not in
either arm's favour in the way the issue predicted.

## `carry-titles` — REJECTED, and it moves the wrong way

The lever tells each window which subjects earlier windows of the same source
already named, so a window straddling a subject boundary can attach to the
existing title rather than coin a variant. **It does the opposite.**
Redundant emissions go 0.12 → **0.62 per run, five times the baseline**, with
a worst run at three.

The mechanism is visible in the replies. Run 3 emitted `Respaldos` twice,
`Rotación de credenciales` twice, and `Modelo de embeddings` alongside
`Deriva del modelo de embeddings` — three subjects named twice in one reply.
Runs 4 and 5 each paired two names for the embeddings-drift subject the same
way. Showing the model a list of titles it has already produced does not make
it reuse them; it makes it **re-emit them, and coin near-variants beside
them**. That is a real finding about prompting, not a tuning miss: the list is
context, and context in the prompt is something this model reproduces.

Recall does not rise: 0.76 against 0.80, comfortably inside the noise. The
bar requires a rise, so this alone rejects the lever. Everything secondary
agrees — precision 0.87 against 0.95 with a floor of 0.57 against 0.82,
near-duplicates doubled, and 0.38 out-of-scope objects per run where the
baseline produced none in eight runs. Three of those four out-of-scope
objects came from one run, which also produced the sweep's only facet and its
only near-duplicate: the extra output is mentions promoted to objects.

**This is the fifth measured prompt treatment this repo has rejected**
(#613, #622, #715 slice 1, #713's first shape, now this one). The pattern is
consistent enough to state plainly: on this material, adding instructions
about *other candidates* to an extraction prompt reliably costs more than it
buys.

## `chunk:8000` — REJECTED, but it proves #699's mechanism

**It eliminates fragmentation completely**: redundant emissions 0.00 and
near-duplicates 0.00 across all seven responding runs, against 0.12 for both
at baseline. #699's causal claim is correct — the duplicates ARE a function of
window size, and doubling the window removes them at the root.

It fails on the other side of the trade. Recall falls 0.80 → 0.69, and that
movement is roughly three standard errors, unlike every other difference in
this sweep. Mean produced falls 9.38 → 8.00. Fewer, larger windows recover
fewer subjects — which is the finding the 4 KB window was chosen for in the
first place (#454's one-object-per-call attractor), re-confirmed from the
other direction.

**One run of eight died on a 600 s timeout.** That is #714's generation
ceiling, arriving exactly where a larger window predicts it: more text per
call, more output per call. The arm is reported over its seven survivors and
still loses — so the conclusion holds *a fortiori*, but the error is signal,
not noise, and a future attempt at this lever has to answer it. Excluding a
broken run and reading the average is how a treatment that breaks a source
comes out looking better; that trap was caught twice in the #714/#715 round
and is why the errored run is in the table rather than in a footnote.

## What the sweep corrected in the #694 oracle

The oracle report published this morning names `Duplicación de objetos por
procesamiento en trozos` as **missing from 5 of 5 runs**, "one designed subject
[that] never survives".

**That finding was wrong, and it was wrong because of an unworked
adjudication queue.** Line 87 of the source is Tomás saying *"Es sobre los
documentos duplicados en el corpus"* — and runs in all three arms of this
sweep emitted `Duplicación de documentos en el corpus` or `Duplicados en el
corpus`, the document's own phrasing, which the ground truth did not yet
admit. It is now aliased, and the subject scores as recovered.

The lesson is the one `small-04` already paid for: a fixture's numbers are
under-reported until a human has worked its queue, and a claim read off an
unworked one describes the annotation, not the extractor.

## Why the baseline moved between two sweeps of the same code

The #694 oracle measured recall **0.89 ±0.04 [0.82–0.91]** at n=5. This sweep
measures **0.80 ±0.12 [0.64–0.91]** at n=8, on the same fixture, the same
model and the same production bytes — the adjudications only added credit, so
they cannot explain a fall.

The honest reading is that **n=5 understated the spread**. The corpus README
already says 5 runs is not enough to conclude anything on this material and
asks for 15+; the oracle's own tight ±0.04 was a small sample landing close
together, and reading it as the precision of the measurement rather than as
one draw was a mistake this sweep corrects. Any future comparison against this
fixture should carry its own baseline arm in the same sweep — which is why
`--lever` always emits the untreated row first and never lets a treatment be
read against a stored number.

## 25 adjudications, and the rule that decided them

Every title in the queue was ruled by one mechanical test applied without
looking at which arm produced it: **does it appear in the same reply as
another name for the same subject?** Yes → near-duplicate. No → the run named
that subject once, and this is how it named it.

- **7 aliases** across four subjects, including the `Duplicación` pair above.
- **1 near-duplicate**: `Problema de escalabilidad de la latencia`, emitted
  beside `Latencia de búsqueda [vectorial]` in both arms that produced it.
- **6 facets**: four re-namings of the outage's temp-files/disk-alarm
  scaffolding, two of `Regeneración incremental de vectores`.
- **4 out of scope**: `Equipo de infraestructura`, `Área de cumplimiento`,
  `Manual de operación`, `Política de cumplimiento` — self-introductions and
  pointers, all mentions promoted to objects.

`Respaldos` was admitted as an alias with its reservations written into the
ground truth: it is the shortest phrasing in the file and generic enough that
another source could mean something else by it.

## The correctness fix this axis exposed

`_chunk_lines` took `target: int = _CHUNK_TARGET`. A signature default binds
**once, when the function is defined**, so reassigning the module constant did
nothing: a measurement arm patching `_CHUNK_TARGET` packed 4 KB windows while
labelling itself an 8 KB arm, and reported a full set of plausible numbers for
a treatment that never ran.

That is the inert-arm defect a reviewer caught in the #714 probe — but the
cause was in production, not in that probe, so every future harness would have
hit it too. Production now reads the constant at call time, pinned by
`test_chunk_target_is_read_at_call_time_not_bound_at_definition`, and this
harness's self-test fails if either lever goes inert or leaks past a run. It
was written as a failing test first, and it failed exactly as described
(8 windows treated, 8 untreated) before the fix.

## What survives for a future #699

1. **The mechanism is confirmed.** Window size causes the duplicates; 8 KB
   windows remove them entirely. #699's diagnosis was right.
2. **The cost is recall, and it is the #454 attractor.** Any lever that widens
   the window has to carry a recovery mechanism for the subjects the wider
   window stops producing, and has to answer #714's ceiling.
3. **Do not re-try the prompt-level approach in this shape.** Listing prior
   titles was measured amplifying the defect fivefold, with a mechanism
   (context reproduction) that a reworded clause is unlikely to escape. The
   full implementation is preserved on the annotated tag
   `experiment/699-carry-titles` for its exact prompt text.
4. **A deterministic post-extraction merge was never tested here** and is the
   one direction this sweep says nothing about — it operates after generation,
   so it cannot amplify what generation produces.
