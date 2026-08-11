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

## Aliases

Alternate phrasings that name a subject above, for the exact-only matcher in
`evals/extraction_cap/`. Each line reads `Canonical Title | alias [| alias]`.

- PowerPoint Presentation Skill | PowerPoint Skill

Inherited from `large-03-skills-vs-tools.md`, where it was adjudicated off a
real run. It carries over because the subject list here is *the same seven* —
the judgment is about what names that subject, not about which document it was
observed in. Withholding it here would manufacture a difference between the
pair that is an artifact of which file happened to be measured first.

Spanish rephrasings are NOT pre-listed in advance. This is the corpus's only
non-English source and how the extractor names these subjects in Spanish is
unmeasured; guessing the aliases in advance would decide that question instead
of observing it. They get adjudicated from a real run's queue, like this one
was. The lines below are that observation — every one came out of the
2026-08-07 prompt-A/B sweep's adjudication queue (10 runs per arm), never
guessed.

- Pre-built Skills | Skills preconstruidas en Claude | Skills preconstruidas | Skills Preconstruidas de Anthropic | Pre-built Skills in Claude AI
- MCP Workflows | Workflows con MCP | Workflows empresariales | Workflow Empresarial | Flujo de trabajo empresarial
- BigQuery Integration | BigQuery | Integration with BigQuery | MCP Integration with BigQuery | BigQuery Integration with MCP
- Model Context Protocol (MCP) | MCP | MCP (Machine Control Protocol) | MCP (Multi-Cloud Platform) | MCP (Multi-Component Processing) | MCP (Machine Context Protocol) | MCP (Machine Connection Protocol) | MCP (Model Communication Protocol)

`Workflows empresariales` (and its casings) is the document's own frame for
the combine-skills-and-MCP arc — judged the `MCP Workflows` subject, not a
generic facet. `BigQuery` bare mirrors the same alias adjudicated in
`large-03-skills-vs-tools.md`.

**The `MCP (...)` block records a defect, deliberately.** On this Spanish
source the model recovers the MCP subject but INVENTS the acronym's expansion
— six distinct false expansions across 13 emissions, versus zero on the two
English fixtures. They are aliased because recall measures subject COVERAGE
and the subject WAS recovered; the hallucinated-title defect is real but is a
different failure, tracked in its own issue, and leaving these unjudged would
have misread it as "extraction misses MCP on Spanish sources". No metric in
`evals/extraction_cap/` sees title fidelity today.

Since #423's fix, the pipeline strips a parenthetical expansion the source
does not contain (`concept._strip_ungrounded_expansions`), so the canonical
post-fix emission for this subject is bare `MCP` -- aliased above. The
fabricated variants stay listed so PRE-fix stored runs keep rescoring the
same; the pipeline can no longer emit them.

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

Added in the second adjudication pass (2026-08-07 prompt-A/B sweep). The
extractor answers this Spanish source mostly in ENGLISH — the whole facet
family below mirrors `large-03-skills-vs-tools.md`'s English decay tail, plus
the workflow-step spellings observed here:

- Marketing Analysis Skill
- Marketing Analysis Workflow
- Skill Packaging
- Skill Validation
- Skill Initialization
- Skill Integration
- Skill Customization
- Skill Development
- Skill Development Workflow
- Skill Deployment
- Skill Configuration
- Skill Execution
- Skill Automation
- Skill Reusability
- SKILL.md
- SKILL.md Structure
- YAML Frontmatter
- Presentation Generation
- Automated Presentation Generation
- Data Analysis Workflow
- Workflow Execution Process
- Workflow Automation
- Workflow Integration
- Workflow Integration with Skills and MCP
- Workflow Integration with BigQuery
- Example Skills

`Example Skills` is the second of the document's `Dos categorías de skills` —
a category inside the `Pre-built Skills` arc, same judgment as its Spanish
heading. `Marketing Analysis Skill` mirrors large-03's
`Marketing Campaign Analysis Skill` facet call: carried in from a previous
lesson, modified in one step, held by the `BigQuery Integration` arc.

This fixture is the density probe of the corpus: 44 headings in 7.7 KB, the
highest ratio of the three. The English original spreads comparable material
over more than twice the length, so this version hands the model many more
candidate headings per kilobyte — the shape most likely to trigger enumeration.

## Near-duplicates

Pairs are written `Canonical Subject | the duplicate phrasing`.

- Pre-built Skills | Document Skills

**This section read "None identified." until the second adjudication pass, and
that was the same mistake `large-03-skills-vs-tools.md` already corrected.**
`Document Skills` appeared in 1 run of the 2026-08-07 sweep here; the English
pair had already adjudicated it a near-duplicate of `Pre-built Skills` (the
document itself equates them), and the two ground truths of one paired lesson
must not disagree about the same pair.

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
