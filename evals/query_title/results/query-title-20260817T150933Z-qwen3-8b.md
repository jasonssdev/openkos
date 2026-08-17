# `query_title` — subject-named or question-named? (#696)

Generation ceiling `8192` · context window `12288` · model qwen3:8b · 14 runs/probe · 170 filings.

> **HETEROGENEOUS POPULATION — 170 filings against 182 for today's 13 probes across 14 runs.** No probe failed. Earlier generations predate probes added later, so they contribute fewer rows; per-run measures (convergence) exclude any family a generation could not populate.

## Per arm

| arm | titled by question | residuals resolved | converged | FP exposure | FPs | regressions | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `baseline` | 91 of 170 | 0 | 8 of 16 | 27 | 0 | 0 | NO EFFECT -- nothing moved off the question |
| `clause` | 16 of 170 | 61 | 8 of 16 | 27 | 0 | 0 | SHIPPABLE at this bar |
| `scaffold` | 35 of 170 | 56 | 8 of 16 | 27 | 0 | 0 | SHIPPABLE at this bar |
| `clause+scaffold` | 13 of 170 | 64 | 8 of 16 | 27 | 0 | 0 | SHIPPABLE at this bar |

**Convergence is unmoved at 8 of 16 — every arm scores exactly what the baseline scores.** #696 states its harm as duplicate detection: two phrasings of one question filing as unrelated objects. No arm here fixes that. What they fix is the narrower complaint the issue opens with — that the permanent Concept ID is an interrogative sentence. Read the shippable verdicts against that bar, not against the harm statement.

## Produced titles

| shape | arm | rung | title |
| --- | --- | --- | --- |
| definitional | `baseline` | subject | Trazabilidad <br>`trazabilidad` |
| definitional | `clause` | subject | Trazabilidad <br>`trazabilidad` |
| definitional | `scaffold` | subject | Trazabilidad <br>`trazabilidad` |
| definitional | `clause+scaffold` | subject | Trazabilidad <br>`trazabilidad` |
| definitional | `baseline` | subject | Sistema RAG <br>`sistema-rag` |
| definitional | `clause` | subject | Sistema RAG <br>`sistema-rag` |
| definitional | `scaffold` | subject | Sistema RAG <br>`sistema-rag` |
| definitional | `clause+scaffold` | subject | Sistema RAG <br>`sistema-rag` |
| definitional | `baseline` | declarative | Las fuentes inmutables son la base del repositorio de conocimiento y nunca se reescriben <br>`las-fuentes-inmutables-son-la-base-del-repositorio-de-conocimiento-y-nunca-se-reescriben` |
| definitional | `clause` | declarative | Las fuentes inmutables son la base del repositorio de conocimiento y nunca se reescriben <br>`las-fuentes-inmutables-son-la-base-del-repositorio-de-conocimiento-y-nunca-se-reescriben` |
| definitional | `scaffold` | declarative | Las fuentes inmutables son la base del repositorio de conocimiento y nunca se reescriben <br>`las-fuentes-inmutables-son-la-base-del-repositorio-de-conocimiento-y-nunca-se-reescriben` |
| definitional | `clause+scaffold` | declarative | Las fuentes inmutables son la base del repositorio de conocimiento y nunca se reescriben <br>`las-fuentes-inmutables-son-la-base-del-repositorio-de-conocimiento-y-nunca-se-reescriben` |
| definitional | `baseline` | subject | MVP <br>`mvp` |
| definitional | `clause` | subject | MVP <br>`mvp` |
| definitional | `scaffold` | subject | MVP <br>`mvp` |
| definitional | `clause+scaffold` | subject | MVP <br>`mvp` |
| causal | `baseline` | question | ¿por qué es importante la trazabilidad en un sistema de conocimiento? <br>`por-qué-es-importante-la-trazabilidad-en-un-sistema-de-conocimiento` |
| causal | `clause` | clause | La trazabilidad es importante en un sistema de conocimiento <br>`la-trazabilidad-es-importante-en-un-sistema-de-conocimiento` |
| causal | `scaffold` | subject+ | Trazabilidad en un sistema de conocimiento <br>`trazabilidad-en-un-sistema-de-conocimiento` |
| causal | `clause+scaffold` | subject+ | Trazabilidad en un sistema de conocimiento <br>`trazabilidad-en-un-sistema-de-conocimiento` |
| causal | `baseline` | question | ¿por qué son importantes las fuentes inmutables? <br>`por-qué-son-importantes-las-fuentes-inmutables` |
| causal | `clause` | clause | Las fuentes inmutables son importantes <br>`las-fuentes-inmutables-son-importantes` |
| causal | `scaffold` | subject+ | Fuentes inmutables <br>`fuentes-inmutables` |
| causal | `clause+scaffold` | subject+ | Fuentes inmutables <br>`fuentes-inmutables` |
| relational | `baseline` | question | ¿qué relación hay entre la trazabilidad y la verdad contextual en sistemas RAG? <br>`qué-relación-hay-entre-la-trazabilidad-y-la-verdad-contextual-en-sistemas-rag` |
| relational | `clause` | clause | La trazabilidad y la verdad contextual están relacionadas en sistemas RAG <br>`la-trazabilidad-y-la-verdad-contextual-están-relacionadas-en-sistemas-rag` |
| relational | `scaffold` | subject+ | Trazabilidad y la verdad contextual en sistemas RAG <br>`trazabilidad-y-la-verdad-contextual-en-sistemas-rag` |
| relational | `clause+scaffold` | subject+ | Trazabilidad y la verdad contextual en sistemas RAG <br>`trazabilidad-y-la-verdad-contextual-en-sistemas-rag` |
| relational | `baseline` | question | ¿qué relación hay entre un MVP y las fuentes inmutables? <br>`qué-relación-hay-entre-un-mvp-y-las-fuentes-inmutables` |
| relational | `clause` | clause | La relación entre un MVP y las fuentes inmutables <br>`la-relación-entre-un-mvp-y-las-fuentes-inmutables` |
| relational | `scaffold` | subject+ | MVP y las fuentes inmutables <br>`mvp-y-las-fuentes-inmutables` |
| relational | `clause+scaffold` | subject+ | MVP y las fuentes inmutables <br>`mvp-y-las-fuentes-inmutables` |
| open | `baseline` | question | ¿qué decidimos sobre el almacenamiento? <br>`qué-decidimos-sobre-el-almacenamiento` |
| open | `clause` | clause | Decidimos que el ledger de merges vive fuera del frontmatter <br>`decidimos-que-el-ledger-de-merges-vive-fuera-del-frontmatter` |
| open | `scaffold` | question | ¿qué decidimos sobre el almacenamiento? <br>`qué-decidimos-sobre-el-almacenamiento` |
| open | `clause+scaffold` | clause | Decidimos que el ledger de merges vive fuera del frontmatter <br>`decidimos-que-el-ledger-de-merges-vive-fuera-del-frontmatter` |
| open | `baseline` | declarative | Bruno quedó como responsable de la migración <br>`bruno-quedó-como-responsable-de-la-migración` |
| open | `clause` | declarative | Bruno quedó como responsable de la migración <br>`bruno-quedó-como-responsable-de-la-migración` |
| open | `scaffold` | declarative | Bruno quedó como responsable de la migración <br>`bruno-quedó-como-responsable-de-la-migración` |
| open | `clause+scaffold` | declarative | Bruno quedó como responsable de la migración <br>`bruno-quedó-como-responsable-de-la-migración` |
| open | `baseline` | question | resumí la reunión de almacenamiento <br>`resumí-la-reunión-de-almacenamiento` |
| open | `clause` | question | resumí la reunión de almacenamiento <br>`resumí-la-reunión-de-almacenamiento` |
| open | `scaffold` | question | resumí la reunión de almacenamiento <br>`resumí-la-reunión-de-almacenamiento` |
| open | `clause+scaffold` | question | resumí la reunión de almacenamiento <br>`resumí-la-reunión-de-almacenamiento` |
| relational | `clause` | question | ¿qué relación hay entre un MVP y las fuentes inmutables? <br>`qué-relación-hay-entre-un-mvp-y-las-fuentes-inmutables` |
| definitional | `baseline` | declarative | Las fuentes inmutables son la base de un repositorio de conocimiento y nunca se reescriben <br>`las-fuentes-inmutables-son-la-base-de-un-repositorio-de-conocimiento-y-nunca-se-reescriben` |
| definitional | `clause` | declarative | Las fuentes inmutables son la base de un repositorio de conocimiento y nunca se reescriben <br>`las-fuentes-inmutables-son-la-base-de-un-repositorio-de-conocimiento-y-nunca-se-reescriben` |
| definitional | `scaffold` | declarative | Las fuentes inmutables son la base de un repositorio de conocimiento y nunca se reescriben <br>`las-fuentes-inmutables-son-la-base-de-un-repositorio-de-conocimiento-y-nunca-se-reescriben` |
| definitional | `clause+scaffold` | declarative | Las fuentes inmutables son la base de un repositorio de conocimiento y nunca se reescriben <br>`las-fuentes-inmutables-son-la-base-de-un-repositorio-de-conocimiento-y-nunca-se-reescriben` |
| causal | `baseline` | question | ¿por qué importan las fuentes inmutables? <br>`por-qué-importan-las-fuentes-inmutables` |
| causal | `clause` | clause | Las fuentes inmutables importan <br>`las-fuentes-inmutables-importan` |
| causal | `scaffold` | question | ¿por qué importan las fuentes inmutables? <br>`por-qué-importan-las-fuentes-inmutables` |
| causal | `clause+scaffold` | clause | Las fuentes inmutables importan <br>`las-fuentes-inmutables-importan` |
| open | `baseline` | declarative | La reunión de almacenamiento abordó la estructura para el almacenamiento del repositorio <br>`la-reunión-de-almacenamiento-abordó-la-estructura-para-el-almacenamiento-del-repositorio` |
| open | `clause` | declarative | La reunión de almacenamiento abordó la estructura para el almacenamiento del repositorio <br>`la-reunión-de-almacenamiento-abordó-la-estructura-para-el-almacenamiento-del-repositorio` |
| open | `scaffold` | declarative | La reunión de almacenamiento abordó la estructura para el almacenamiento del repositorio <br>`la-reunión-de-almacenamiento-abordó-la-estructura-para-el-almacenamiento-del-repositorio` |
| open | `clause+scaffold` | declarative | La reunión de almacenamiento abordó la estructura para el almacenamiento del repositorio <br>`la-reunión-de-almacenamiento-abordó-la-estructura-para-el-almacenamiento-del-repositorio` |
| relational | `clause` | clause | El MVP se centra en validar una idea con el menor esfuerzo posible <br>`el-mvp-se-centra-en-validar-una-idea-con-el-menor-esfuerzo-posible` |
