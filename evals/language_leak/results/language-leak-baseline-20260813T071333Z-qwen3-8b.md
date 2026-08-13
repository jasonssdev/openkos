# language-leak probe — arm `baseline` (#563)

_Generated: 20260813T071333Z_ · model `qwen3:8b` · **3 runs** · fixture 24650 chars / 7 windows.

| metric | value |
| --- | --- |
| **leak rate (en+mixed titles / all titles)** | **0.75** |
| **harmful-class rate (pure `en` AND not verbatim-quoted)** | **0.20** |
| **harmful-class rate AFTER the #618 gate** | **0.11** |
| **harmful left after the #622 bigram extension** | **0** |
| extension drops across runs | 10 |
| extension FALSE POSITIVES across runs | 0 |
| total objects across runs | 107 |
| objects after gate across runs | 95 |
| leaked titles across runs | 80 |
| harmful titles across runs | 21 |
| gate-dropped titles across runs | 12 |
| harmful titles left after gate | 10 |
| mean objects per run | 35.7 |
| mean objects per run after gate | 31.7 |
| mean run latency | 520.1s |
| window errors across runs | 3 |

## Leaked titles per run (harmful class marked `!`)

- run 1: Reunión de trabajo sobre el knowledge object model; Decisión sobre la fuente canónica y el storage centralizado; Procedimiento de onboarding para el equipo nuevo; Evaluation pipeline; Model Context Protocol (MCP); Knowledge source project; Harness; Storage layer; Evaluation Pipeline; Procedimiento de Onboarding; Decisión sobre la Documentación del Engine Setup; Decisión sobre el Storage Centralizado; Knowledge Object Model; Model Context Protocol; Mantenimiento de la documentación bilingüe del setup del knowledge engine; Re-ranking del retrieval con el judge ensemble; Capacitación del equipo nuevo con un procedimiento de onboarding; Acuerdos concretos sobre el storage layer; Migración del knowledge recovery system al nuevo formato de bundle; Conexión del evaluation harness para medir la calidad del retrieval; Model Context Protocol (MCP); Knowledge Object Model; Evaluation Pipeline; Knowledge Recovery System; Centralized Knowledge Storage; Knowledge Source Project; Evaluation Harness; Judge Ensemble; !Knowledge Base; !Decision on Knowledge Source Bundle and Centralized Storage; Knowledge Source Project; !Evaluation Pipeline Project; !Knowledge Recovery Project; !Retrieval Re-ranking Project; !Onboarding Procedure for New Team Members
- run 2: Procedimiento de onboarding para el equipo nuevo; Evaluation harness; Mantenimiento de la documentación del engine setup en dos idiomas; Definición de la fuente canónica y el storage centralizado; Procedimiento de onboarding para el equipo nuevo; Model Context Protocol (MCP); Mantenimiento de la documentación bilingüe del engine setup; Uso del bundle como fuente canónica y el storage centralizado como consumidor de snapshots; Procedimiento de onboarding para el equipo nuevo; Knowledge recovery system; Model Context Protocol (MCP); Evaluation harness; Centralized knowledge storage; Knowledge source project; Re-ranking del retrieval; Knowledge object model; Pipeline de evaluation; !Decision on Knowledge Source Bundle; Knowledge Source Project; !Evaluation Pipeline and Language Leakage Measurement; !Knowledge Recovery Project Phase Two; !Onboarding Procedure for New Team Members
- run 3: !Decision on Centralized Knowledge Storage; Knowledge Source Project; !Onboarding Procedure for New Team Members; !Knowledge Object Model and Evaluation Pipeline; !Decision on Model Context Protocol Integration; !Knowledge Recovery System Integration; !Decision on Evaluation Harness for Retrieval; Mantenimiento de la documentación del engine setup en dos idiomas; Procedimiento de onboarding para el equipo nuevo; Model Context Protocol (MCP); Evaluation harness; Knowledge source project; Knowledge object model; Storage layer; Mantenimiento de la documentación bilingüe del engine setup; Fuente canónica del bundle y uso de snapshots derivados; Procedimiento de onboarding para el equipo nuevo; !Decision on Knowledge Source Bundle and Storage; Knowledge Source Project; !Evaluation Pipeline Project; !Knowledge Recovery Project Phase Two; !Retrieval Re-ranking Project; !New Team Onboarding Procedure

## Gate-dropped titles per run

- run 1: Decision on Knowledge Source Bundle and Centralized Storage; Onboarding Procedure for New Team Members
- run 2: Decision on Knowledge Source Bundle; Evaluation Pipeline and Language Leakage Measurement; Onboarding Procedure for New Team Members
- run 3: Decision on Centralized Knowledge Storage; Onboarding Procedure for New Team Members; Decision on Bilingual Documentation; Knowledge Object Model and Evaluation Pipeline; Decision on Model Context Protocol Integration; Decision on Evaluation Harness for Retrieval; Decision on Knowledge Source Bundle and Storage

## Extension drops per run (#622; false positives marked `FP!`)

- run 1: Knowledge Base; Evaluation Pipeline Project; Knowledge Recovery Project; Retrieval Re-ranking Project
- run 2: Knowledge Recovery Project Phase Two
- run 3: Knowledge Recovery System Integration; Evaluation Pipeline Project; Knowledge Recovery Project Phase Two; Retrieval Re-ranking Project; New Team Onboarding Procedure
