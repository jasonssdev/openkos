# Participant-pass language measurement — #713

`qwen3:8b`, 3 runs per fixture per arm, 2026-08-15. Two independent sweeps, the
second run against the shipped code with the arms inverted to ablation; both
produced the same rates.

Raw: `results/participant-language-20260815T151013Z-qwen3-8b.jsonl` (first
sweep, splice arms) and `results/participant-language-20260815T151345Z-qwen3-8b.jsonl`
(second sweep, ablation arms).

## Result

| fixture | arm | candidates | fields | harmful | rate | `en` | `es` | `mixed` | `neutral` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `es-anchored` | no anchor | 12 | 24 | 18 | **0.75** | 18 | 0 | 0 | 6 |
| `es-bare` | no anchor | 9 | 18 | 0 | 0.00 | 0 | 18 | 0 | 0 |
| `es-anchored` | **anchored (shipped)** | 15 | 30 | 0 | **0.00** | 0 | 24 | 6 | 0 |
| `es-bare` | anchored (shipped) | 9 | 18 | 0 | 0.00 | 0 | 18 | 0 | 0 |

Not a single `es` field survived the unanchored arm on `es-anchored` — 18
English, 6 neutral, zero Spanish, from a source with no English in it.

## The bodies are translations, not summaries

#713's central claim, and the reason a leaked body outranks a leaked title.
Side by side with the fixture's own prose:

| returned body (no anchor) | the source turn |
| --- | --- |
| *"I teach the distributed systems course in the Department of Informatics, and today we are joined by two master's students and the people from Vega Ingeniería."* | *"yo dicto el ramo de sistemas distribuidos en el Departamento de Informática, y hoy nos acompañan dos tesistas y la gente de la empresa Vega Ingeniería"* |
| *"I am a student of the master's in data science and I am in charge of the ingestion pipeline since March."* | *"Yo soy estudiante del magíster en ciencia de datos y estoy a cargo del pipeline de ingesta desde marzo."* |
| *"From Vega Ingeniería we can contribute the corpus of minutes that we have digitized. I direct the data area there and can sign the agreement this week."* | *"Desde Vega Ingeniería podemos aportar el corpus de actas que tenemos digitalizado. Yo dirijo el área de datos allá y puedo firmar el convenio esta semana."* |

Clause for clause. This is the stored content of a `Person` object — personal
data restated by a model rather than quoted, and what `query` would cite back.

Descriptions leaked the same way: `Chair of the meeting`, `Pipeline Manager`,
`Thesis student in information retrieval`.

## Cause

`_build_participant_capture_messages` was the **only** extraction call in the
pipeline that omitted `_LANGUAGE_ANCHOR`:

> Write every "title", "description" and "body" in the same language as the
> SOURCE TEXT below.

Its docstring justified the omission explicitly — *"this narrower follow-up
question needs the title as its own reference point (what meeting this is), not
a language anchor — the source text itself still carries the source's
language."*

The measurement falsifies that last clause. #522 had already measured the same
shape from the other side: removing the only source-language text from a user
turn produced English output in 28 of 30 runs, and the anchor exists because of
it. The participant pass carries a Spanish TITLE, which was evidently not
enough.

## Why this is not the prompt direction that lost twice

#563's named-language anchor moved title leakage 0.69 → 0.63 for nearly double
the latency and was rejected. #613's direction guard cost 0.82 → 0.29 forward
accuracy and was rejected. Both added a NEW rule and asked an 8B model to carry
it.

This adds no rule. It restores an instruction the same module already ships on
every other extraction call, whose absence here was an unexamined assumption
rather than a measured decision. The result is categorical — 0.75 → 0.00 over 48
fields across two sweeps — and it costs nothing: candidates rose from 4 to 5 per
run on the fixture that leaked, and latency did not increase.

**Scope note.** The ruling for #713 named the shippable shape as a
*deterministic post-extraction treatment* clearing a zero-false-positive bar.
This is a prompt change, so it is outside the letter of that shape, and the
deviation is recorded here rather than glossed. The reasoning: a post-extraction
gate would have to detect a translated body and then drop it, which loses the
participant — the #690 failure — whereas the anchor prevents the bad content
from being produced. The zero-FP condition is met by a wide margin either way.

## Why `es-bare` never leaked

Reproduced, both arms, both sweeps: 0.00 harmful. It is the shorter, role-free
transcript — its speakers state no roles or affiliations, so there is far less
role prose for the model to paraphrase, and what it returns stays close to the
turns. The arms differ only where there was something to translate.

That contrast is why the fixture pair matters: a single leaking fixture would
not have shown that the defect is triggerable rather than universal, which
#713's own text flagged as worth pinning down before assuming a cause.

## Bounds

One model, two Spanish fixtures, 3 runs per arm per sweep. Not measured on other
source languages, and not on the general extraction pass, which already carries
the anchor on its meeting-shaped branch and was not changed.
