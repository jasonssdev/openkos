# Ground truth — `small-04-pre-build-skills.md`

Source size: 7693 B

## READ THIS FIRST — this is a paired variant, not an independent fixture

This file is the **same lesson** as `large-03-skills-vs-tools.md`, written in
Spanish and condensed to roughly 45% of its length. The two are not independent
samples, and treating them as such would count the same evidence twice.

The H1s give it away:

    large-03:  # Pre-built Skills, Skill Creator, and MCP Workflows
    small-04:  # Pre-built Skills, Skill Creator y Workflows con MCP

and the structure matches section for section — `Document Skills and Example
Skills` / `Dos categorías de skills`, `Initialize / Package / Validate Skill
Script` / `init_skill.py / package_skill.py / validate_skill.py`, `Connecting to
BigQuery with MCP` / `Integración con BigQuery mediante MCP`, and so on.

**It is CONFOUNDED as a size control.** Holding content constant while varying
size is exactly how one would isolate the size effect #404 claims ("the defect
scales with size"). This pair cannot do that, because size and LANGUAGE vary
together. A difference between the two runs cannot be attributed to length when
the prompt is in English and one of the documents is not.

**Scoring rule.** A result here is NOT independent confirmation of a result on
`large-03-skills-vs-tools.md`. Report them as a pair, never as two data points
in the same average.

**Open question this raises.** Extraction quality on non-English sources is
unmeasured. The entire classification rubric, the tie-break chain, the
anti-enumeration paragraph and the multiplicity test are English-only, and this
is the first Spanish source in any fixture set. Whether the defect profile
differs is not known and is not a question this corpus was assembled to answer.

## Genuinely distinct subjects

**Count: 7** — the same seven as `large-03-skills-vs-tools.md`.

- Concept | Pre-built Skills
- Concept | Skill Creator
- Concept | MCP Workflows
- Concept | Model Context Protocol (MCP)
- Concept | BigQuery Integration
- Concept | PowerPoint Presentation Skill
- Concept | Brand Guidelines Skill

The compression did not drop a subject. Each one still owns a comparable share
of the document:

| subject | EN (17 KB) | ES (7.7 KB) |
| --- | --- | --- |
| PowerPoint Skill | 10.0% | 12.6% |
| Brand Guidelines Skill | 12.4% | 9.5% |
| Pre-built Skills | 7.2% | 9.9% |
| Skill Creator | 9.6% | 19.3% |

That matters for what this fixture tests. The expected answer is the same 7 in
both versions, so a run producing fewer here than on `large-03` is evidence
about compression or language, not about the subjects being absent.

## Facets, not subjects

Steps, components and section scaffolding, not knowledge objects. An extractor
emitting these is decaying, not enumerating.

- Introducción / Conclusión
- Dos categorías de skills
- Estructura del SKILL.md
- Capacidades de la Skill de PowerPoint
- Scripts bajo demanda
- Propósito
- Proceso de creación
- init_skill.py
- package_skill.py
- validate_skill.py
- Configuración del servidor
- Verificando la conexión (Paso 1, Paso 2)
- Buenas prácticas aplicadas
- Archivos de entrada
- Objetivo
- Recursos incluidos
- Skill 1 / Skill 2 / Skill 3
- Flujo de ejecución (Paso 1–4)
- Resultado

This fixture is the density probe of the corpus: 44 headings in 7.7 KB, the
highest ratio of the three. The English original spreads comparable material
over more than twice the length, so this version hands the model many more
candidate headings per kilobyte — the shape most likely to trigger enumeration.

## Near-duplicates

None identified.

`Model Context Protocol (MCP)` against `MCP Workflows` was examined and
rejected for the same reason as in `large-03-skills-vs-tools.md`: the protocol
and the workflows built on it are separate things.

## Notes

The source title `derive_source_title` produces here is
`'Pre-built Skills, Skill Creator y Workflows con MCP'`, which names three of
the seven subjects outright. Same twin probe as its English pair:
`_drop_source_title_twins` should suppress an object that merely restates the
whole document, while the three subjects the title names must still survive
individually.
