# The subject-generation clause: REJECT, on a blocker rather than on merit (#715)

qwen3:8b, 3 fixtures x 3 runs x 2 arms, 2026-08-15. Re-derivable from
`results/stage-attrition-20260815T043927Z-qwen3-8b.jsonl` with `--rescore`.

## Baseline: the defect, reproduced nine times out of nine

| fixture | retained | subjects |
| --- | --- | --- |
| `es-anchored` | 4 / 4 / 4 | **0 / 0 / 0** |
| `es-bare` | 3 / 3 / 3 | **0 / 0 / 0** |
| `ami-ts3005a` | 4 / 4 / 1 | **0 / 0 / 0** |

Nine runs, zero subjects, every fixture. This is a cleaner reproduction than
the 21 runs #715 was filed on, and it was produced under the shipped prompt
with nothing patched.

## The treatment

An ADDITIVE clause spliced immediately after the stated multiplicity test
(design D3's adjacency), asking for the second half of what the pinned
anti-enumeration paragraph already promises:

> When the source is a meeting, call, or interview transcript, BOTH halves of
> that instruction are required: the gathering itself AND each distinct subject
> the participants worked through -- every decision reached, every problem
> raised, every topic resolved, every procedure agreed. A working transcript
> normally develops SEVERAL such subjects, and a reply naming only the
> gathering has not read the transcript for its content.

The verbatim-pinned paragraph (#380) keeps every pinned byte and
`_drop_framing_objects` (#522/#533) is untouched -- the two levers ruled out.
The clause COMPLETES that paragraph rather than negating it: the paragraph
already asks for "the Event and the Decisions" and only the Event ever
arrives.

## Verdict: REJECT

| Condition | Result |
| --- | --- |
| Subject retention does not increase | did not fire — 0/9 → **1/6** |
| Latency >= 1.5x baseline | did not fire — 73.6s → 76.4s (1.04x) |
| Participant retention degrades | did not fire — 3.33 → 3.67 per run |
| A retained subject is not supported by the source | did not fire — **zero fabrications**, adjudicated below |
| The treatment cannot complete a fixture the baseline completed | **FIRED** — `ami-ts3005a`, 3 of 3 runs |

Per the standing ruling a REJECT ships the measurement only. Production is
untouched. This is the fifth measured prompt treatment this repo has rejected
(#563, #613, #622, #712 slice 1).

## What fired, and why it is not a verdict on the clause

Every `ami-ts3005a` treatment run died the same way:

```
OllamaGenerationCapped: Ollama stopped generation at the configured
max_generation_tokens ceiling (8192) before the reply finished
```

The baseline completed all three runs of that fixture (49.2s / 19.5s /
142.6s). Asking for several objects instead of one lengthens the reply, and a
16 KB transcript then blows the ceiling. **That is #714**, reproduced here
harder than the issue reported it: #714 measured 1 run in 3, this arm fails 3
of 3.

So the clause is **blocked, not refuted**. Re-measure it after #714 lands; do
not re-open the ruling that produced it.

## The fifth condition was added after the sweep

The first four conditions all read clear, and the gate printed
`SHIPPABLE`. They average COMPLETED runs only -- so a fixture the arm can no
longer process at all leaves the averages looking *better*, its slow crowded
runs simply stop being counted. AMI's baseline mean was dragged up by a 142.6s
run; the treatment's mean excludes AMI entirely.

Condition 5 scores per FIXTURE, not per run: one flaky run is noise, a fixture
that never completes is a source the arm cannot process. It is
mutation-confirmed in `--self-test` against its exact target line, and the
stored sweep was re-scored through `--rescore` rather than re-run, so the new
condition was scored against the same evidence and not a fresh sample.

## Adjudication — the one run that fired

`es-bare` run 1 retained 9 objects (6 subject / 3 participant). Every subject
is grounded in the transcript's prose:

| candidate | source evidence | verdict |
| --- | --- | --- |
| `Decision: Decisión sobre la medición y la ventana de contexto` | *"Decisión: se fija la ventana de contexto y se repite la medición sobre el mismo corpus"* | REAL |
| `Decision: Decisión sobre el respaldo del bundle` | *"el respaldo del bundle. Está sin correr desde la semana pasada"* | REAL |
| `Concept: Citas incorrectas en las respuestas` | *"encontré dos que apuntaban al documento equivocado"* | REAL |
| `Concept: Latencia en el índice` | *"La latencia bajó a la mitad después de sacar las palabras funcionales"* | REAL — and **absent from this fixture's hand-written `known_subjects`** |
| `Concept: Problema con preguntas largas` | *"Con las largas. La pregunta arrastra tanto texto..."* | REAL, but the SAME subject as the citation row, fragmented (#699) |
| `Procedure: Procedimiento para la medición y el respaldo del bundle` | both halves exist separately | composite — joins two topics the source does not treat as one |

Zero fabrications. The clause produces real content when it fires; what it
does not do is fire reliably (1 of 6 completed runs).

`Concept: Latencia en el índice` is worth keeping in view: a genuine subject
the fixture's own hand-written list missed. `known_subjects` is documented as
"not a recall target" for exactly this reason, and this run is the first
evidence that the caveat was load-bearing.

## What this does not answer

Why the clause fires on 1 of 6 completed runs rather than reliably. The
retained-set jump is 0 → 6 objects when it does fire, which is not a marginal
effect; the variance is in whether generation takes the instruction at all.
Separating "the model ignores the clause" from "the two-pass path re-rolls
until one pass complies" needs the per-call ledger this probe already records,
read across many more runs than three.
