# Function words must not decide a containment (#755)

Population: **549** distinct stored titles, **150426** pairs compared. Deterministic, stdlib-only -- no model, no GPU.

| metric | count |
| --- | --- |
| delta pairs (verdict changed) | 38 |
| newly matched | 37 |
| newly lost | 1 |
| **exposed** (adjudicated) | 37 |
| recovered duplicates | 36 |
| **false positives** | 0 |
| false positives REMOVED | 1 |
| regressions | 0 |
| cross-type (production never compares) | 1 |
| unadjudicated | 0 |

**Verdict:** SHIPPABLE at this bar

## Recovered duplicates

- 'Building a Multi-Agent Research Application Using Claude Agent SDK' || 'Building a Multi-Agent Research Application with Claude Agent SDK'
- 'Building a Multi-Agent Research Application Using Claude Agent SDK' || 'Building a Research Agent with Claude Agent SDK'
- 'Building a Multi-Agent Research Application Using Claude Agent SDK' || 'Building a Research Agent with the Claude Agent SDK'
- 'Building a Multi-Agent Research Application Using Claude Agent SDK' || 'Using the Claude Agent SDK for Research Applications'
- 'Building a Multi-Agent Research Application with Claude Agent SDK' || 'Building a Research Agent with the Claude Agent SDK'
- 'Capacitación del equipo nuevo con un procedimiento de onboarding' || 'Procedimiento de Onboarding para el Equipo Nuevo'
- 'Decision on Bundle and Storage Centralized' || 'Decisión sobre el Storage Centralizado y el Bundle'
- 'Decision on Bundle and Storage Centralized' || 'Decisión sobre el Storage Centralizado'
- 'Decision on Bundle and Storage Centralized' || 'Decisión sobre el bundle y el storage centralizado'
- 'Decision on Centralized Knowledge Storage' || 'Decisión sobre el Storage Centralizado'
- 'Decision on Knowledge Source Bundle and Centralized Storage' || 'Decisión sobre el Storage Centralizado y el Bundle'
- 'Decision on Knowledge Source Bundle and Centralized Storage' || 'Decisión sobre el Storage Centralizado'
- 'Decision on Knowledge Source Bundle and Centralized Storage' || 'Decisión sobre el bundle y el storage centralizado'
- 'Decision on Knowledge Source Bundle and Centralized Storage' || 'Decisión sobre el centralized knowledge storage'
- 'Decisión sobre retención de registros de acceso' || 'Retención de los registros de acceso'
- 'El bundle sigue siendo la fuente canónica' || 'Fuente canónica del bundle'
- 'Fuente canónica del bundle y uso de snapshots derivados' || 'Uso del bundle como fuente canónica'
- 'Fuente canónica del bundle y uso del storage centralizado' || 'Uso del bundle como fuente canónica'
- 'Fuente canónica del bundle' || 'Usar el bundle como fuente canónica'
- 'MCP (Model Context Protocol)' || 'Protocolo de Contexto del Modelo'
- 'Model Context Protocol (MCP)' || 'Protocolo de Contexto del Modelo'
- 'Model Context Protocol Integration' || 'Protocolo de Contexto del Modelo'
- 'Pre-built Skills and Skill Creator with MCP' || 'Pre-built Skills, Skill Creator, and MCP Workflows Documentation'
- 'Pre-built Skills and Skill Creator with MCP' || 'Pre-built Skills, Skill Creator, and MCP Workflows'
- 'Pre-built Skills, Skill Creator and Workflows with MCP' || 'Pre-built Skills, Skill Creator, and MCP Workflows Documentation'
- 'Pre-built Skills, Skill Creator, and MCP Workflows Documentation' || 'Workflow Creation with Skills and MCP'
- 'Pre-built Skills, Skill Creator, and MCP Workflows' || 'Workflow Creation with Skills and MCP'
- 'Problema de acumulación de archivos temporales' || 'Problema de los archivos temporales'
- 'Production Security with Human-in-the-Loop Guardrails' || 'Production Security: Implementing Human-in-the-Loop Guardrails'
- 'Re-ranking del Retrieval' || 'Retrieval Re-ranking Project'
- 'Re-ranking del Retrieval' || 'Retrieval Re-ranking with Judge Ensemble'
- 'Re-ranking del retrieval con el judge ensemble' || 'Retrieval Re-ranking with Judge Ensemble'
- 'Technical Implementation of Agent Application' || 'Technical Implementation of the Agent'
- 'Technical Implementation of the Agent' || 'Technical Implementation using `agent.py`'
- 'Workflow Automation using Pre-built Skills and Skill Creator' || 'Workflow Automation with Skills'
- 'Workflow Automation using Skills' || 'Workflow Automation with Skills'

## False positives removed

- 'Integración del knowledge source project' || 'Knowledge Object Model'

## Cross-type -- excluded, production compares same-type only

- 'Knowledge Object Model and Evaluation Pipeline' || 'Reunión de revisión del knowledge object model y pipeline de evaluation'
