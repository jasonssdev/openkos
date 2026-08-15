# Ground truth — `medium-10-reunion-plataforma.md`

Source size: 12718 B

## READ THIS FIRST — this fixture is SYNTHETIC, and that is the point

Every other source in this corpus is real third-party material whose ground
truth was recovered by reading it and judging what it discusses. This one was
**written to have a known answer**, the way `evals/participant_anchor`'s
`es-anchored` and `es-bare` transcripts were.

That inverts the usual risk. On a found document the danger is that the
annotator missed a subject; here the danger is that the author wrote something
the annotation does not admit. The mitigation is that the content was composed
subject by subject, each one given its own bounded passage with an explicit
verbal marker (`queda decidido`, `el procedimiento que propongo tiene cuatro
pasos`, `es estructural, no es ruido del modelo`), and the facets below were
written deliberately as scaffolding rather than discovered afterwards.

**It is also the first fixture in this corpus that is a TRANSCRIPT.** Every
other one is prose. `small-04-pre-build-skills.md`'s ground truth closes by
noting that "extraction quality on non-English sources is unmeasured" — this
answers a narrower version of that: extraction quality on non-English
**meeting-shaped** sources, which is the regime where #713, #714 and #715 all
lived.

**It is NOT a size control against any other fixture**, and it is not a paired
variant of anything. It is an independent sample.

## Why 12 718 B specifically

The size is load-bearing, not incidental. `_is_meeting_shaped` returns true on
this content, so `_chunk_threshold_for` governs it at `_MEETING_CHUNK_THRESHOLD`
(12 000, #714) rather than at 18 000. At 12 718 B it therefore takes the
**chunked** path — four windows of roughly 3 900 / 3 900 / 4 000 / 940 — which
is the regime #699's within-source fragmentation lives in.

A fixture a few hundred bytes shorter would silently take the two-pass
whole-document path instead and measure a different pipeline. Anyone editing
this source must re-check that it still exceeds 12 000, or the numbers stop
describing the path they claim to.

**One subject is deliberately split across the window boundary.** `Deriva del
modelo de embeddings` is discussed in window 1 and returned to in the final
window (Elena's recap of what stayed open). That is the structural condition
#699 describes — two windows touching one subject, neither aware of the other —
and it is here so the defect is observable rather than hoped for.

## Genuinely distinct subjects

**Count: 11.** Seven topic subjects and four participants.

### Topic subjects (7)

- Event | Incidente de indisponibilidad del servicio
- Concept | Latencia de la búsqueda vectorial
- Concept | Deriva del modelo de embeddings
- Concept | Duplicación de objetos por procesamiento en trozos
- Decision | Cifrado de los respaldos en reposo
- Decision | Retención de los registros de acceso a noventa días
- Procedure | Procedimiento de rotación de credenciales

Each one, with the prose that carries it:

| subject | why it is a subject |
| --- | --- |
| Incidente de indisponibilidad del servicio | A bounded past event with duration, cause and impact: *"el martes pasado el servicio estuvo caído dos horas y cuarenta minutos"*, caused by *"ciento ochenta gigabytes de temporales acumulados"*. |
| Latencia de la búsqueda vectorial | A characterised problem with figures on both sides: *"la mediana de una consulta está en ochocientos milisegundos"* against *"ciento veinte milisegundos de mediana"* in January, cause named as *"comparación exhaustiva contra todos los vectores"*. |
| Deriva del modelo de embeddings | *"el modelo de embeddings que usamos cambió de versión en junio y nadie volvió a generar los vectores antiguos"*, with the consequence stated: *"las distancias entre un vector viejo y uno nuevo no significan lo mismo"*. |
| Duplicación de objetos por procesamiento en trozos | Raised as its own agenda item, with a concrete example and an explicit causal claim: *"cada trozo se procesa a ciegas respecto de los demás … es estructural, no es ruido del modelo"*. |
| Cifrado de los respaldos en reposo | An explicit decision: *"queda decidido: los respaldos del bundle se cifran en reposo y la llave se custodia en el gestor de secretos corporativo"*. |
| Retención de los registros de acceso a noventa días | An explicit decision: *"Queda decidido: los registros de acceso se conservan noventa días y después se eliminan"*. |
| Procedimiento de rotación de credenciales | Four ordered steps stated as a procedure, plus a revert rule and an ordering rule (*"primero los que solo leen y al final los que escriben"*). |

### Participants (4)

- Person | Elena Vidal
- Person | Marcos Iturra
- Person | Paula Cifuentes
- Person | Tomás Reyes

Every one states a role or affiliation in their own first turn — *"yo coordino
el equipo de infraestructura"*, *"ingeniero de datos, a cargo del almacenamiento
y de los respaldos"*, *"encargada de seguridad de la información"*,
*"desarrollador del motor de búsqueda"* — so none is a bare-name stub under
#668's rule.

**They are listed as subjects because #668 made them first-class objects** with
their own relations, sensitivity and lifecycle. A run that recovers them is
recovering knowledge the product intends to hold.

**Read the recall figure with that split in mind.** Participants are the
easiest class on a transcript — the capture pass exists specifically to find
them — so a run scoring 4/11 has found every person and no topic at all, which
is precisely the #715 failure. Aggregate recall alone cannot tell that apart
from a run that found four topics and no people. Until the scorer splits the
two, compare the recovered-subject list, not only the number.

## Facets, not subjects

Written deliberately as scaffolding or as detail belonging to a subject above.
An extractor emitting these is decaying, not enumerating.

- Orden del día
- Recapitulación de acuerdos
- Cierre de la reunión
- Puntos pendientes
- Archivos temporales de la reconstrucción
- Umbral de alerta de disco
- Paso 1 / Paso 2 / Paso 3 / Paso 4
- Generación de la credencial nueva
- Revocación de la credencial anterior
- Espera de veinticuatro horas
- Gestor de secretos corporativo
- Etiqueta de versión del modelo
- Regeneración incremental de vectores
- Registros agregados sin identificadores
- Conjunto de consultas de control
- Índice aproximado
- Problema de acumulación de archivos temporales
- Problema de almacenamiento y temporales
- Fallo en el monitoreo de discos
- Problema de alertas de disco
- Procedimiento de regeneración de vectores
- Procedimiento para la regeneración de vectores antiguos

The last six came out of the **2026-08-15 lever sweep** (#699), and they split
two ways that were already decided above:

- The four `temporales` / `disco` phrasings are re-namings of
  `Archivos temporales de la reconstrucción` and `Umbral de alerta de disco`,
  both already listed as facets of `Incidente de indisponibilidad del
  servicio`. The incident's cause and its missing alarm are how the incident is
  described, not two further things the meeting is about.
- The two `regeneración de vectores` phrasings are `Regeneración incremental de
  vectores` under another name — already ruled a facet of `Deriva del modelo de
  embeddings`, on the transcript's own statement that the drift *"tiene dos
  mitades: la limpieza … y la prevención"*.

Two of these are judgment calls, made here on purpose rather than left to a
scorer:

- **`Etiqueta de versión del modelo`** and **`Regeneración incremental de
  vectores`** are facets of `Deriva del modelo de embeddings`, not subjects.
  The transcript says so in as many words — *"la deriva del modelo de
  embeddings tiene dos mitades: la limpieza … y la prevención"* — so they are
  the two halves of one subject, not two subjects.
- **`Índice aproximado`** is a candidate solution to
  `Latencia de la búsqueda vectorial`, discussed only as a means. The meeting
  explicitly declines to decide on it (*"eso necesita una medición antes que una
  decisión"*), so it never becomes a thing the corpus knows.

## Aliases

Not pre-listed. This corpus's rule, set in `small-04-pre-build-skills.md`:
Spanish rephrasings are adjudicated from an actual queue, because guessing how
the extractor names a subject decides the question instead of observing it.
That rule binds harder here than anywhere else in the corpus — this source was
authored rather than found, so an author guessing his own document's aliases
would be scoring the extractor against his own phrasing twice.

Every line below came out of the **2026-08-15 baseline sweep** (5 runs,
`qwen3:8b`, union+judge), never guessed. The pattern across all six is one-way:
the extractor's phrasing is consistently SHORTER than the canonical title, and
in no case did it invent a subject the document does not discuss.

- Incidente de indisponibilidad del servicio | Incidente de caída del servicio | Incidente de la semana pasada
- Latencia de la búsqueda vectorial | Latencia de búsqueda | Latencia de búsqueda vectorial | Problema de latencia en búsqueda vectorial
- Deriva del modelo de embeddings | Modelo de embeddings | Cambio de modelo de embeddings | Cambio de versión en el modelo de embeddings
- Cifrado de los respaldos en reposo | Cifrado de respaldos | Cifrar los respaldos y custodiar la llave en el gestor de secretos | Decisión sobre cifrado de respaldos | Respaldos
- Retención de los registros de acceso a noventa días | Retención de registros de acceso | Retención de los registros de acceso | Decisión sobre la retención de los registros de acceso | Decisión sobre retención de registros de acceso
- Procedimiento de rotación de credenciales | Rotación de credenciales | Problema de rotación de credenciales
- Duplicación de objetos por procesamiento en trozos | Duplicación de documentos en el corpus | Duplicados en el corpus

### The 2026-08-15 additions, and the rule that decided each one

The lines above the `Duplicación` one gained phrasings from the #699 lever
sweep (24 runs, three arms). One mechanical rule separated alias from
near-duplicate, applied without looking at which arm produced the title:
**does it appear in the same reply as another name for the same subject?** If
yes it is a near-duplicate, because that reply spent two slots on one subject.
If no, the run named the subject once and this is simply how it named it.

`Duplicación de documentos en el corpus` is the adjudication that matters
most, and it is not a judgment call: line 87 of the source is Tomás saying
*"Es sobre los documentos duplicados en el corpus"* — the document's own
phrase for the subject the annotation calls `Duplicación de objetos por
procesamiento en trozos`. The #694 baseline report named this subject as
missing from 5 of 5 runs. **That finding was an artefact of an unworked
adjudication queue, not a recovery failure**, and it stands corrected here:
runs across all three arms of the lever sweep recover it under the
transcript's own wording.

`Respaldos` is admitted as an alias with reservations recorded. It is the
shortest phrasing in this file and generic enough that a different source
could mean something else by it — but the corpus rule is that aliases are
observed, not guessed, and it was emitted on a source where the only backup
arc is the encryption decision. Where it was emitted twice in one reply
(carry-titles run 3), the second emission is scored against the run, since
precision credits a subject once.

`Incidente de la semana pasada` is the document's own phrase for the outage
(Elena's agenda line), so it names the subject rather than describing it
loosely.

The three `embeddings` variants are aliased rather than split because each run
emitted **exactly one** of them — they are five samples of one naming decision,
not evidence of fragmentation. Contrast the backup pair below, which co-occurs.

## Near-duplicates

Pairs are written `Canonical Subject | the duplicate phrasing`. Adjudicated
from the same sweep.

- Cifrado de los respaldos en reposo | Problema de respaldos
- Cifrado de los respaldos en reposo | Problemas de respaldos
- Latencia de la búsqueda vectorial | Problema de escalabilidad de la latencia

`Problema de escalabilidad de la latencia` was adjudicated by the same
co-occurrence rule as the two backup lines, and it fails it in both arms that
produced it: baseline run 6 emitted it alongside `Latencia de búsqueda
vectorial`, and carry-titles run 6 alongside `Latencia de búsqueda`. Two
slots, one subject, one reply.

**These are near-duplicates, not aliases, and the distinction is observable
rather than stylistic.** In runs 2 and 5 the model emitted `Problema de
respaldos` (resp. `Problemas de respaldos`) **alongside** `Cifrado de
respaldos`, in the same reply — a second object re-naming a subject already
emitted, which is exactly what this section is for. Aliasing them would have
credited one subject twice and hidden the fragmentation.

That fragmentation is #699's shape, caught here on the fixture built to catch
it: the backup arc spans a window boundary, and the two windows each named it
their own way without knowing about each other.

Run 5 additionally emitted `Rotación de credenciales` **twice, verbatim**, in
one reply. It needs no line here — it is not a second *phrasing* — and the
scorer already handles it: precision credits a subject once, so the repeat
counts against the run rather than for it.

Note that the source also *contains* a near-duplicate pair as narrated content:
Tomás quotes *"latencia del índice"* and *"latencia en las consultas del
índice"* as an example of the defect he is describing. Those remain unlisted.
They are two strings inside a turn, never observed as emissions, and listing
them would credit this ground truth for a prediction it has not earned.

## Out of scope

Named in the transcript and not what it is about. Kept apart from facets so a
scope error never reads as decay.

- Equipo de infraestructura
- Área de cumplimiento
- Manual de operación
- Política de cumplimiento

All four are self-introductions or pointers: Elena *"coordino el equipo de
infraestructura"*, Paula *"vengo del área de cumplimiento"* and, for the last
two, where a written procedure will live (*"en el manual de operación"*) and
the rule a decision cites (*"la política dice que los datos personales en
reposo van cifrados"*). An extractor emitting them has turned a mention into
an object.

Note that three of the four came from ONE run — carry-titles run 5, the run
that also produced the sweep's only `F` and its only `D`. Its extra output is
mentions promoted to objects, not subjects nobody listed.

## Notes

The H1 is `Reunión de plataforma — revisión quincenal`, which names no subject
in the list above. That is deliberate: `large-03` and `small-04` both carry
titles that name several of their own subjects, which makes them twin probes;
this one is not a twin probe, so a twin-rule interaction cannot confound its
recall figure.

The title is also meeting-shaped in the #459 sense (`reunión`), and the content
is transcript-shaped in the #673 sense, so **both** halves of `_is_meeting_shaped`
fire here. A run measured on this fixture exercises the meeting branch of
`_build_messages` (no title in the user turn, `_LANGUAGE_ANCHOR` instead), the
participant-capture pass, and the judge re-admission conjunct.

Source language is Spanish throughout, with no English in the prose. Any English
title, description or body a run produces here is a language leak, not content
the source carries — the same property `evals/participant_language` relies on.
