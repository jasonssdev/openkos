# language-leak probe — arm `baseline` (#563)

_Generated: 20260813T052243Z_ · model `qwen3:8b` · **3 runs** · fixture 24650 chars / 7 windows.

| metric | value |
| --- | --- |
| **leak rate (en+mixed titles / all titles)** | **0.74** |
| **harmful-class rate (leaked AND not verbatim-quoted)** | **0.38** |
| **harmful-class rate AFTER the #618 gate** | **0.33** |
| total objects across runs | 117 |
| objects after gate across runs | 106 |
| leaked titles across runs | 86 |
| harmful titles across runs | 45 |
| gate-dropped titles across runs | 11 |
| harmful titles left after gate | 35 |
| mean objects per run | 39.0 |
| mean objects per run after gate | 35.3 |
| mean run latency | 471.1s |
| window errors across runs | 2 |

## Leaked titles per run (harmful class marked `!`)

- run 1: !Decision on Centralized Knowledge Storage; Knowledge Source Project; !Knowledge Recovery Project; !Onboarding Procedure for New Team Members; !Model Context Protocol Integration; !Mantenimiento de la documentación del engine setup en dos idiomas; Evaluation Pipeline; Procedimiento de Onboarding; !Decisión sobre la Documentación del Engine Setup; !Model Context Protocol (MCP); Knowledge Object Model; Storage Layer; Knowledge Source Project; !Mantener la documentación del engine setup en dos idiomas; !Procedimiento de onboarding para el equipo nuevo; Knowledge recovery system; !Model Context Protocol (MCP); Evaluation harness; Centralized knowledge storage; Knowledge source project; Evaluation pipeline; Knowledge object model; Storage layer; Retrieval; Knowledge engine; !Decision on Knowledge Source Bundle and Centralized Storage; Knowledge Source Project; !Evaluation Pipeline and Language Leakage Measurement; !Knowledge Recovery Project Phase Two; !Retrieval Re-ranking with Judge Ensemble; !New Team Onboarding Procedure
- run 2: !Procedimiento de onboarding para el equipo nuevo; Evaluation pipeline; !Model Context Protocol (MCP); Knowledge source project; !Mantenimiento de la documentación del engine setup bilingüe; !Procedimiento de onboarding para el equipo nuevo; !Definición de la fuente canónica y el storage centralizado; Knowledge Source Project; !Model Context Protocol (MCP); Evaluation Harness; Knowledge Object Model; Centralized Knowledge Storage; !Estructura del storage centralizado; !Integración del knowledge source project; Re-ranking del retrieval con el judge ensemble; !Procedimiento de onboarding para el equipo nuevo; !Modelo de knowledge object; !Decision on Knowledge Source Bundle and Centralized Storage; Knowledge Source Project; !Evaluation Pipeline and Language Leakage Harness; !Knowledge Recovery Project Phase Two; !Onboarding Procedure for New Team Members
- run 3: !Procedimiento de onboarding para el equipo nuevo; Knowledge Object Model; Evaluation Pipeline; !Model Context Protocol (MCP); Knowledge Recovery System; Knowledge Source Project; !Decisión sobre documentación del engine setup; !Procedimiento de onboarding para el equipo nuevo; !Model Context Protocol (MCP); Knowledge Object Model; Evaluation Pipeline; Knowledge Recovery System; !Mantener la documentación del engine setup bilingüe; !Procedimiento de onboarding para el equipo nuevo; Model Context Protocol; Knowledge source project; Evaluation harness; Knowledge object model; Evaluation pipeline; Knowledge recovery system; Knowledge engine; !Mantener la documentación del engine setup bilingüe; !Procedimiento de onboarding para el equipo nuevo; !Model Context Protocol (MCP); Evaluation harness; Knowledge object model; !Coordinación entre el centralized knowledge storage y el bundle; !Decision on Knowledge Source Bundle and Storage; Knowledge Source Project; !Evaluation Pipeline Project; !Knowledge Recovery Project Phase Two; !Retrieval Re-ranking Project; !Onboarding Procedure for New Team Members

## Gate-dropped titles per run

- run 1: Decision on Centralized Knowledge Storage; Onboarding Procedure for New Team Members; Decision on Bilingual Documentation; Decision on Knowledge Source Bundle and Centralized Storage; Evaluation Pipeline and Language Leakage Measurement; Retrieval Re-ranking with Judge Ensemble
- run 2: Decision on Knowledge Source Bundle and Centralized Storage; Evaluation Pipeline and Language Leakage Harness; Onboarding Procedure for New Team Members
- run 3: Decision on Knowledge Source Bundle and Storage; Onboarding Procedure for New Team Members
