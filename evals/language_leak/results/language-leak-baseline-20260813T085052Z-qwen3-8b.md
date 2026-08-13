# language-leak probe — arm `baseline` (#563)

_Generated: 20260813T085052Z_ · model `qwen3:8b` · **3 runs** · fixture 24650 chars / 7 windows.

| metric | value |
| --- | --- |
| **leak rate (en+mixed titles / all titles)** | **0.72** |
| **harmful-class rate (pure `en` AND not verbatim-quoted)** | **0.12** |
| **harmful-class rate AFTER the #618 gate** | **0.09** |
| **harmful left after the #622 bigram extension** | **0** |
| extension drops across runs | 11 |
| extension FALSE POSITIVES across runs | 2 |
| total objects across runs | 106 |
| objects after gate across runs | 102 |
| leaked titles across runs | 76 |
| harmful titles across runs | 13 |
| gate-dropped titles across runs | 4 |
| harmful titles left after gate | 9 |
| mean objects per run | 35.3 |
| mean objects per run after gate | 34.0 |
| mean run latency | 632.1s |
| window errors across runs | 4 |

## Leaked titles per run (harmful class marked `!`)

- run 1: Procedimiento de onboarding para el equipo nuevo; Protocolo de contexto del modelo (Model Context Protocol); Pipeline de evaluación; Knowledge source project; Harness de evaluación; Setup y uso del knowledge engine; Mantener la documentación del engine setup bilingüe; Decisión sobre la documentación del engine setup; Decisión sobre el centralized knowledge storage; Procedimiento de onboarding para el equipo nuevo; Modelo de knowledge object; Evaluation pipeline; Model Context Protocol (MCP); Mantenimiento de la documentación bilingüe del engine setup; Fuente canónica del bundle y uso del storage centralizado; Procedimiento de onboarding para el equipo nuevo; Model Context Protocol (MCP); Evaluation Harness; Knowledge Object Model; Knowledge Recovery System; Centralized Knowledge Storage; !Decision on Knowledge Source Bundle and Storage; Knowledge Source Project; !Evaluation Pipeline Project; !Knowledge Recovery Project Phase Two; !Retrieval Re-ranking Project; !Onboarding Procedure for New Team Members
- run 2: Procedimiento de onboarding para el equipo nuevo; Knowledge Object Model; Evaluation Pipeline; Model Context Protocol (MCP); Knowledge Recovery System; Knowledge Source Project; Evaluation Harness; Mantenimiento de la documentación del engine setup en dos idiomas; Procedimiento de onboarding para el equipo nuevo; Model Context Protocol (MCP); Knowledge Source Project; Evaluation Harness; Knowledge Object Model; Evaluation Pipeline; Judge Ensemble; Knowledge Recovery System; Decisión sobre la documentación del engine setup; Decisión sobre el storage centralizado y el bundle; Procedimiento de onboarding para el equipo nuevo; Evaluation pipeline; Model Context Protocol; Knowledge object model; Mantenimiento de la documentación del engine setup en dos idiomas; Fuente canónica del bundle y uso de snapshots derivados; Procedimiento de onboarding para el equipo nuevo; Reunión de revisión del knowledge object model y pipeline de evaluation; !Decision on Knowledge Source Bundle; Knowledge Source Project; !Evaluation Pipeline Project; !Knowledge Recovery Project; !Onboarding Procedure
- run 3: Procedimiento de onboarding para el equipo nuevo; Pipeline de evaluación; Harness de evaluación; Decisión sobre la documentación del engine setup; Decisión sobre el storage centralizado y el bundle; Procedimiento de onboarding para el equipo nuevo; Model Context Protocol (MCP); Knowledge Object Model; Evaluation Pipeline; Knowledge Source Project; Mantenimiento de la documentación del engine setup en dos idiomas; Fuente canónica del bundle y uso del storage centralizado; Procedimiento de onboarding para el equipo nuevo; !Decision on Knowledge Source Project; Knowledge Source Project; !Knowledge Recovery Project; !Onboarding Procedure; !Re-ranking Procedure

## Gate-dropped titles per run

- run 1: Decision on Knowledge Source Bundle and Storage; Onboarding Procedure for New Team Members
- run 2: Decision on Knowledge Source Bundle
- run 3: Decision on Knowledge Source Project

## Extension drops per run (#622; false positives marked `FP!`)

- run 1: Evaluation Pipeline Project; Knowledge Recovery Project Phase Two; Retrieval Re-ranking Project
- run 2: FP!Language Leakage Measurement; Evaluation Pipeline Project; Knowledge Recovery Project; Onboarding Procedure
- run 3: Knowledge Recovery Project; Onboarding Procedure; FP!Language Leakage Measurement; Re-ranking Procedure
