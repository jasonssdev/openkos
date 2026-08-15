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
| The treatment errors on runs the baseline completed | **FIRED** — `ami-ts3005a`, 3 errored runs vs baseline 0 |

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

Condition 5 counts errored RUNS per fixture. Its first version asked whether a
fixture failed OUTRIGHT, and the review caught that as wrong for this exact
failure: **#714 is intermittent** — originally 1 run in 3 — so an
all-or-nothing test reads clear on the regime the issue actually reports,
while the fixture's surviving runs keep flattering conditions 1-3. A treatment
that breaks a source two times in three has broken it.

Both regimes are mutation-confirmed in `--self-test`: reverting the counter to
all-or-nothing leaves the outright case rejecting and the intermittent case
passing, which is the failure the guard exists to prevent.

The stored sweep was re-scored through `--rescore` rather than re-run, both
when condition 5 was added and when it was corrected — so the condition changed
against the same evidence and not against a fresh sample.

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

---

# Re-measured after #714 — ACCEPT, and shipped

`qwen3:8b`, `--arm both --runs 3`, three fixtures, 9 runs per arm,
2026-08-15. Raw: `results/stage-attrition-20260815T144627Z-qwen3-8b.jsonl`.

The earlier REJECT was **blocked, not refuted**: it fired on condition 5 alone,
because every `ami-ts3005a` treatment run died on the 8192 generation ceiling
(#714). With #714 shipped, that fixture chunks and the run completes, so the
same clause could finally be scored on the evidence it was always meant to be
scored on.

| metric | baseline | treatment |
| --- | --- | --- |
| runs retaining a subject | 3/9 | **7/9** |
| subjects per run | 0.78 | **1.89** |
| participants per run | 2.89 | 3.22 |
| latency | 93.5s | 95.7s (**1.02x**) |
| errored runs | 0 | 0 |

All five gate conditions cleared. Compare the rejected slice-1 treatment, which
cost **1.92x** latency for 0 subjects: this is a different result, not a
rerun of the same one.

### The participant aggregate hides a per-fixture dip

2.89 → 3.22 is an average, and the average improves because `ami-ts3005a`
contributes participants the baseline arm dropped. Per fixture, `es-anchored`
goes the other way: baseline retains 5 / 4 / 4 participants, treatment 4 / 4 / 4
— run 1 loses one (`Organization: Vega Ingeniería`).

One participant on one run of one fixture is within this probe's run-to-run
variance and does not change the verdict, but the summary statistic the ACCEPT
cites cannot see it, and a reader should not have to reconstruct it from the
ledger. This is the same failure shape recorded for A/B gates that average
completed runs: an aggregate is blind to a regime its components disagree
about.

## #714 did part of the work on its own

The baseline's 3 of 9 are **all** `ami-ts3005a`, which now takes the chunked
path: 3, 2 and 2 subjects on the shipped prompt, where the pre-#714 baseline
measured 0 across every run of that fixture. The one-object-per-call attractor
(#454) explains it — a whole-document call collapses to the framing Event,
which `_drop_framing_objects` then correctly deletes, leaving nothing; per
window, each window contributes its own subject.

The two fixes therefore cover **different regimes**, and neither subsumes the
other:

- long meeting sources (> the meeting threshold): chunking alone recovers
  subjects;
- short ones (`es-anchored` 5 450 chars, `es-bare` 3 134) stay on the
  two-pass path where chunking cannot help, and there the baseline is still
  **0 subjects in 6 of 6 runs** while the treatment retains them.

## Adjudication of every retained subject

Condition 4 is explicitly not automated, so all 16 retained instances were read
against their sources. **Zero fabrications.**

| fixture | subject | source evidence | verdict |
| --- | --- | --- | --- |
| `ami-ts3005a` ×3 | `Event: Discussion on Remote Control Design` | *"it's time uh for some discussion … what kind of ideas do you have to design a new remote control"* (line 127) | REAL — an announced segment, not the meeting container |
| `ami-ts3005a` ×3 | `Event: Drawing Activity` / `Drawing Exercise` | *"our first exercise, because I'm uh going to ask you to draw your favourite animal"* (line 39) | REAL |
| `es-anchored` | `Decision: Incorporación del corpus de actas de Vega Ingeniería al proyecto` | *"Que quede la decisión: el corpus de actas de Vega Ingeniería se incorpora al proyecto bajo convenio"* | REAL — **the exact decision #715's issue text named as never surviving** |
| `es-anchored` | `Concept: Evaluación del motor de recuperación de información` | *"mi tesis cubre justamente la evaluación del motor"* | REAL |
| `es-anchored` | `Procedure: Procedimiento de operación para la reconstrucción del índice de búsqueda` | *"Sugiero que quede automático y documentado en el procedimiento de operación"* | REAL |
| `es-anchored` | `Project: Proyecto de memoria institucional` | appears ONLY in the source title | **title-derived** — see below |
| `es-bare` ×3 | `Decision: Decisión sobre el respaldo del bundle` | *"el respaldo del bundle. Está sin correr desde la semana pasada"* | REAL |
| `es-bare` ×3 | `Decision: Decisión sobre la ventana de contexto` / `sobre la medición del índice` | *"Decisión: se fija la ventana de contexto y se repite la medición"* | REAL |
| `es-bare` | `Procedure: Procedimiento para medir el índice` | *"Yo dejo el script de medición en el repositorio"* | REAL |

### The one exception, and why it did not block

`Project: Proyecto de memoria institucional` is not invented — the fixture's
title is *"Reunión de coordinación del proyecto de memoria institucional"* — but
the string appears in **no turn of the prose**. It is a title-derived object,
the class `_drop_source_title_twins` exists to remove, and it survived because
the candidate is a **fragment** of the title rather than the normalized title
itself, which that filter's equality test cannot see.

That is a pre-existing gap in the twin rule, not a defect this clause
introduced, and it is grounded in the source, so condition 4 ("a retained
subject is not supported by the source") does not fire. Filed separately.

## Still not answered

The clause fires on 7 of 9 runs, not 9 of 9. The two it misses are both
`es-anchored`, whose baseline is also 0 — so nothing regressed, but the
variance in whether generation takes the instruction at all is unexplained and
is the same open question the first measurement left.
