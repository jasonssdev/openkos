# openkos extraction cap-and-decay eval (#404)

_Generated: 2026-08-06T01:01:50+00:00_

Model: `qwen3:8b`. Runs per cell: **15**.

**There is no corpus-wide average anywhere in this report, deliberately.** `small-04-pre-build-skills` is the same lesson as `large-03-skills-vs-tools` in Spanish at ~45% of the length; both ground-truth files forbid averaging them. Read each fixture on its own, and read that pair side by side.

Verdict marks: `S` a genuine subject, `F` a known facet (decay), `D` a near-duplicate re-naming a subject already emitted, `X` explicitly out of scope, `?` unjudged. The `|` in a run's mark string is where the cap cut.

## `large-03-skills-vs-tools` — arm `baseline`

Ground truth: **7 genuine subjects**, 16 named facets, 0 out-of-scope exclusion(s), 1 near-duplicate pair(s).

| metric | value |
| --- | --- |
| mean produced (pre-cap) | 8.29 |
| mean retained (post-cap) | 4.86 |
| subject recall pre-cap | 0.79 |
| subject recall post-cap | 0.62 |
| **mean cap_cost (subjects lost to the cap)** | **1.14** |
| mean known facets produced (decay) | 1.57 |
| mean near-duplicates produced | 0.36 |
| mean out-of-scope produced | 0.00 |
| mean unjudged titles | 0.86 |
| distinct title sets | 14/14 |
| backend errors | 1 |

**Genuine subjects the cap discarded** (runs affected):

- `Brand Guidelines Skill` — 9/14 runs
- `PowerPoint Presentation Skill` — 4/14 runs
- `Model Context Protocol (MCP)` — 2/14 runs
- `BigQuery Integration` — 1/14 runs

### Position curve (does reply order track quality?)

| position | subject | facet | near-dup | out of scope | unjudged |
| --- | --- | --- | --- | --- | --- |
| 1 | 14 | 0 | 0 | 0 | 0 |
| 2 | 14 | 0 | 0 | 0 | 0 |
| 3 | 14 | 0 | 0 | 0 | 0 |
| 4 | 8 | 1 | 5 | 0 | 0 |
| 5 *(cap)* | 11 | 1 | 0 | 0 | 0 |
| 6 | 11 | 0 | 0 | 0 | 0 |
| 7 | 4 | 5 | 0 | 0 | 1 |
| 8 | 0 | 6 | 0 | 0 | 1 |
| 9 | 1 | 3 | 0 | 0 | 0 |
| 10 | 0 | 1 | 0 | 0 | 0 |
| 11 | 0 | 0 | 0 | 0 | 1 |
| 12 | 0 | 0 | 0 | 0 | 1 |
| 13 | 0 | 0 | 0 | 0 | 1 |
| 14 | 0 | 1 | 0 | 0 | 0 |
| 15 | 0 | 0 | 0 | 0 | 1 |
| 16 | 0 | 0 | 0 | 0 | 1 |
| 17 | 0 | 1 | 0 | 0 | 0 |
| 18 | 0 | 1 | 0 | 0 | 0 |
| 19 | 0 | 1 | 0 | 0 | 0 |
| 20 | 0 | 0 | 0 | 0 | 1 |
| 21 | 0 | 1 | 0 | 0 | 0 |
| 22 | 0 | 0 | 0 | 0 | 1 |
| 23 | 0 | 0 | 0 | 0 | 1 |
| 24 | 0 | 0 | 0 | 0 | 1 |
| 25 | 0 | 0 | 0 | 0 | 1 |

### Per-run detail

- run 1 (26.4s, produced 6, retained 5): S:Pre-built Skills, S:Skill Creator, S:Model Context Protocol (MCP), F:Marketing Campaign Analysis Skill, S:Brand Guidelines Skill, S:PowerPoint Presentation Skill
- run 2 (14.6s, produced 7, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Presentation Generation Workflow
- run 3 (9.1s, produced 4, retained 4): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:BigQuery Integration
- run 4 (15.7s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, D:Document Skills, F:Skill Creation Process, S:BigQuery Integration, S:Brand Guidelines Skill, F:Workflow Integration
- run 5 (16.3s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, ?:Skill Development Best Practices, ?:Workflow Automation
- run 6 (44.7s, produced 25, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, D:Document Skills, S:BigQuery Integration, S:Brand Guidelines Skill, S:Model Context Protocol (MCP), F:Skill Creation Process, F:Skill Validation, F:Skill Packaging, ?:SKILL.md File, ?:YAML Frontmatter, ?:Best Practices for Skill Creation, F:Skill Initialization, ?:Skill Execution, ?:Skill Assets, F:Skill Integration, F:Skill Customization, F:Skill Reusability, ?:Skill Modularity, F:Skill Documentation, ?:Skill Validation Scripts, ?:Skill Packaging Scripts, ?:Skill Initialization Scripts, ?:Skill Execution Scripts
- run 7 (23.2s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, D:Document Skills, S:BigQuery Integration, S:Brand Guidelines Skill, S:PowerPoint Skill, F:Skill Creation Process
- run 8: ERROR — OllamaUnavailable: Ollama not reachable at localhost:11434: timed out
- run 9 (8.9s, produced 4, retained 4): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP)
- run 10 (15.3s, produced 9, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:BigQuery Integration, S:Brand Guidelines Skill, S:PowerPoint Skill, F:Skill Creation Process, F:Skill Best Practices, S:Model Context Protocol (MCP)
- run 11 (15.8s, produced 7, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, S:PowerPoint Presentation Skill
- run 12 (24.1s, produced 9, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Skill Creation Process, F:Skill Validation, F:Workflow Integration
- run 13 (13.1s, produced 7, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, D:Document Skills, S:BigQuery Integration, S:Brand Guidelines Skill, F:Skill Creation Process
- run 14 (25.5s, produced 9, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, D:Document Skills, S:BigQuery Integration, S:Brand Guidelines Skill, F:Workflow Integration, F:Skill Creation Process, F:Skill Validation
- run 15 (14.0s, produced 5, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:BigQuery Integration, S:Brand Guidelines Skill

## `large-03-skills-vs-tools` — arm `t0.1`

Ground truth: **7 genuine subjects**, 16 named facets, 0 out-of-scope exclusion(s), 1 near-duplicate pair(s).

| metric | value |
| --- | --- |
| mean produced (pre-cap) | 7.36 |
| mean retained (post-cap) | 5.00 |
| subject recall pre-cap | 0.90 |
| subject recall post-cap | 0.71 |
| **mean cap_cost (subjects lost to the cap)** | **1.29** |
| mean known facets produced (decay) | 0.86 |
| mean near-duplicates produced | 0.00 |
| mean out-of-scope produced | 0.00 |
| mean unjudged titles | 0.21 |
| distinct title sets | 8/14 |
| backend errors | 1 |

**Genuine subjects the cap discarded** (runs affected):

- `Brand Guidelines Skill` — 12/14 runs
- `PowerPoint Presentation Skill` — 6/14 runs

### Position curve (does reply order track quality?)

| position | subject | facet | near-dup | out of scope | unjudged |
| --- | --- | --- | --- | --- | --- |
| 1 | 14 | 0 | 0 | 0 | 0 |
| 2 | 14 | 0 | 0 | 0 | 0 |
| 3 | 14 | 0 | 0 | 0 | 0 |
| 4 | 14 | 0 | 0 | 0 | 0 |
| 5 *(cap)* | 14 | 0 | 0 | 0 | 0 |
| 6 | 12 | 0 | 0 | 0 | 0 |
| 7 | 5 | 7 | 0 | 0 | 0 |
| 8 | 1 | 4 | 0 | 0 | 3 |
| 9 | 0 | 1 | 0 | 0 | 0 |

### Per-run detail

- run 1 (15.1s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Presentation Generation Workflow, ?:Skill Development Best Practices
- run 2 (16.2s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Presentation Generation Workflow, ?:Skill Development Best Practices
- run 3 (15.6s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Presentation Generation Workflow, ?:Skill Development Best Practices
- run 4 (14.0s, produced 7, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Presentation Generation Workflow
- run 5 (17.5s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, S:PowerPoint Presentation Skill, F:Skill Creation Process
- run 6 (17.2s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, S:PowerPoint Skill, F:Skill Creation Process
- run 7 (17.4s, produced 9, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, S:PowerPoint Skill, F:Skill Creation Process, F:Skill Validation
- run 8 (11.6s, produced 5, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration
- run 9: ERROR — OllamaUnavailable: Ollama not reachable at localhost:11434: timed out
- run 10 (18.2s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Marketing Campaign Analysis Skill, S:PowerPoint Presentation Skill
- run 11 (13.9s, produced 7, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Presentation Generation Workflow
- run 12 (11.5s, produced 5, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration
- run 13 (16.7s, produced 8, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, S:PowerPoint Skill, F:Skill Creation Process
- run 14 (14.4s, produced 7, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, F:Presentation Generation Workflow
- run 15 (14.9s, produced 7, retained 5): S:Pre-built Skills, S:Skill Creator, S:MCP Workflows, S:Model Context Protocol (MCP), S:BigQuery Integration, S:Brand Guidelines Skill, S:PowerPoint Skill

## `medium-08-sdk-skills` — arm `baseline`

Ground truth: **4 genuine subjects**, 18 named facets, 1 out-of-scope exclusion(s), 0 near-duplicate pair(s).

> **Twin-rule collision on this fixture.** `Building a Research Agent with the Claude Agent SDK` normalizes equal to the derived source title, so `_drop_source_title_twins` deletes it whenever another object survives. A miss on it is NOT an extraction failure — do not record it as one.

| metric | value |
| --- | --- |
| mean produced (pre-cap) | 5.80 |
| mean retained (post-cap) | 5.00 |
| subject recall pre-cap | 0.82 |
| subject recall post-cap | 0.77 |
| **mean cap_cost (subjects lost to the cap)** | **0.20** |
| mean known facets produced (decay) | 2.20 |
| mean near-duplicates produced | 0.00 |
| mean out-of-scope produced | 0.00 |
| mean unjudged titles | 0.33 |
| distinct title sets | 8/15 |
| backend errors | 0 |

**Genuine subjects the cap discarded** (runs affected):

- `Building a Research Agent with the Claude Agent SDK` — 2/15 runs
- `Human-in-the-Loop Guardrails` — 1/15 runs

### Position curve (does reply order track quality?)

| position | subject | facet | near-dup | out of scope | unjudged |
| --- | --- | --- | --- | --- | --- |
| 1 | 15 | 0 | 0 | 0 | 0 |
| 2 | 1 | 14 | 0 | 0 | 0 |
| 3 | 14 | 1 | 0 | 0 | 0 |
| 4 | 3 | 12 | 0 | 0 | 0 |
| 5 *(cap)* | 13 | 2 | 0 | 0 | 0 |
| 6 | 3 | 0 | 0 | 0 | 1 |
| 7 | 0 | 2 | 0 | 0 | 2 |
| 8 | 0 | 2 | 0 | 0 | 1 |
| 9 | 0 | 0 | 0 | 0 | 1 |

### Per-run detail

- run 1 (18.1s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP) Server, F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails
- run 2 (12.6s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP) Server, F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails
- run 3 (11.6s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP) Server, S:Human-in-the-Loop Guardrails, F:Learning-a-Tool Skill
- run 4 (19.4s, produced 7, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP) Server, F:Learning-a-Tool Skill, S:Research Agent, S:Human-in-the-Loop Guardrails, ?:Agent Definitions
- run 5 (10.6s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails
- run 6 (28.5s, produced 9, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP) Server, F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Researching MinerU, ?:Agent Implementation, ?:Agent Definitions
- run 7 (11.9s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP) Server, F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails
- run 8 (10.8s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), S:Human-in-the-Loop Guardrails, S:Research Agent Application
- run 9 (12.3s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), S:Human-in-the-Loop Guardrails, F:Researching MinerU
- run 10 (10.4s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails
- run 11 (16.8s, produced 8, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, ?:Setting Up the Research Agent Environment, F:Researching MinerU
- run 12 (10.3s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails
- run 13 (14.7s, produced 5, retained 5): S:Claude Agent SDK, S:Model Context Protocol (MCP), F:Orchestrator-Workers Pattern, F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails
- run 14 (14.8s, produced 5, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP) Server, F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails
- run 15 (20.3s, produced 8, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP) Server, F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, ?:Building a Research Agent, F:agent.py, F:Researching MinerU

## `medium-08-sdk-skills` — arm `t0.1`

Ground truth: **4 genuine subjects**, 18 named facets, 1 out-of-scope exclusion(s), 0 near-duplicate pair(s).

> **Twin-rule collision on this fixture.** `Building a Research Agent with the Claude Agent SDK` normalizes equal to the derived source title, so `_drop_source_title_twins` deletes it whenever another object survives. A miss on it is NOT an extraction failure — do not record it as one.

| metric | value |
| --- | --- |
| mean produced (pre-cap) | 8.62 |
| mean retained (post-cap) | 5.00 |
| subject recall pre-cap | 1.00 |
| subject recall post-cap | 0.75 |
| **mean cap_cost (subjects lost to the cap)** | **1.00** |
| mean known facets produced (decay) | 3.62 |
| mean near-duplicates produced | 0.00 |
| mean out-of-scope produced | 0.23 |
| mean unjudged titles | 0.77 |
| distinct title sets | 9/13 |
| backend errors | 2 |

**Genuine subjects the cap discarded** (runs affected):

- `Building a Research Agent with the Claude Agent SDK` — 13/13 runs

### Position curve (does reply order track quality?)

| position | subject | facet | near-dup | out of scope | unjudged |
| --- | --- | --- | --- | --- | --- |
| 1 | 13 | 0 | 0 | 0 | 0 |
| 2 | 0 | 13 | 0 | 0 | 0 |
| 3 | 13 | 0 | 0 | 0 | 0 |
| 4 | 0 | 13 | 0 | 0 | 0 |
| 5 *(cap)* | 13 | 0 | 0 | 0 | 0 |
| 6 | 13 | 0 | 0 | 0 | 0 |
| 7 | 0 | 10 | 0 | 0 | 0 |
| 8 | 0 | 2 | 0 | 2 | 5 |
| 9 | 0 | 7 | 0 | 1 | 0 |
| 10 | 0 | 0 | 0 | 0 | 2 |
| 11 | 0 | 0 | 0 | 0 | 1 |
| 12 | 0 | 0 | 0 | 0 | 1 |
| 13 | 0 | 1 | 0 | 0 | 0 |
| 14 | 0 | 1 | 0 | 0 | 0 |
| 15 | 0 | 0 | 0 | 0 | 1 |

### Per-run detail

- run 1 (30.2s, produced 15, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Sub-Agent Toolkit Assignments, ?:Progressive Learning Milestones, F:Notion MCP Server Integration, ?:Agent Definition and Configuration, ?:Environment Setup and Initialization, ?:Interactive Development Loop, F:Technical Implementation of `agent.py`, F:Live Case Study: Researching MinerU, ?:Execution Safety and Validation
- run 2 (18.7s, produced 9, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Sub-Agent Toolkit Assignments, ?:Technical Implementation (agent.py), F:Live Case Study: Researching MinerU
- run 3 (11.9s, produced 6, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application
- run 4 (17.2s, produced 9, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Sub-Agent Toolkit Assignments, ?:Technical Implementation (agent.py), F:Live Case Study: Researching MinerU
- run 5 (17.2s, produced 7, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Researching MinerU
- run 6: ERROR — OllamaUnavailable: Ollama not reachable at localhost:11434: timed out
- run 7 (19.7s, produced 9, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Sub-Agent Toolkit Assignments, ?:Technical Implementation (agent.py), F:Live Case Study: Researching MinerU
- run 8 (11.9s, produced 6, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application
- run 9 (18.8s, produced 9, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent, F:Sub-Agent Toolkit Assignments, F:Notion MCP Server, X:MinerU
- run 10 (13.0s, produced 6, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent
- run 11 (20.2s, produced 10, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent, F:Sub-Agent Toolkit Assignments, ?:Progressive Learning Milestones, F:Notion MCP Server Integration, ?:Agent Definition
- run 12 (17.0s, produced 8, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Sub-Agent Toolkit Assignments, F:Notion MCP Server Integration
- run 13: ERROR — OllamaUnavailable: Ollama not reachable at localhost:11434: timed out
- run 14 (18.1s, produced 9, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Sub-Agent Toolkit Assignments, X:MinerU, F:Notion MCP Server Integration
- run 15 (18.3s, produced 9, retained 5): S:Claude Agent SDK, F:Orchestrator-Workers Pattern, S:Model Context Protocol (MCP), F:Learning-a-Tool Skill, S:Human-in-the-Loop Guardrails, S:Research Agent Application, F:Sub-Agent Toolkit Assignments, X:MinerU, F:Notion MCP Server Integration

## Unjudged adjudication queue

Titles matching neither a ground-truth subject nor a named facet. This harness never guesses which they are — no fuzzy matching, by design, since a matcher generous toward the hypothesis measures nothing. Work this queue by hand:

1. A rephrasing of an existing subject → add it under `## Aliases` in that fixture's ground truth as `Canonical Title | the rephrasing`.
2. A facet of a subject → add it to `## Facets, not subjects`.
2b. Something the document merely MENTIONS and is not about → add it to `## Out of scope`. Kept apart from facets so a scope error never reads as decay.
3. A genuine subject nobody listed → add it under `## Genuinely distinct subjects` and update `**Count:**` (the parser rejects the file if the two disagree).

### `large-03-skills-vs-tools` — arm `baseline`

- `Skill Development Best Practices` — seen 1 time(s)
- `Workflow Automation` — seen 1 time(s)
- `SKILL.md File` — seen 1 time(s)
- `YAML Frontmatter` — seen 1 time(s)
- `Best Practices for Skill Creation` — seen 1 time(s)
- `Skill Execution` — seen 1 time(s)
- `Skill Assets` — seen 1 time(s)
- `Skill Modularity` — seen 1 time(s)
- `Skill Validation Scripts` — seen 1 time(s)
- `Skill Packaging Scripts` — seen 1 time(s)
- `Skill Initialization Scripts` — seen 1 time(s)
- `Skill Execution Scripts` — seen 1 time(s)

### `large-03-skills-vs-tools` — arm `t0.1`

- `Skill Development Best Practices` — seen 3 time(s)

### `medium-08-sdk-skills` — arm `baseline`

- `Agent Definitions` — seen 2 time(s)
- `Agent Implementation` — seen 1 time(s)
- `Setting Up the Research Agent Environment` — seen 1 time(s)
- `Building a Research Agent` — seen 1 time(s)

### `medium-08-sdk-skills` — arm `t0.1`

- `Technical Implementation (agent.py)` — seen 3 time(s)
- `Progressive Learning Milestones` — seen 2 time(s)
- `Agent Definition and Configuration` — seen 1 time(s)
- `Environment Setup and Initialization` — seen 1 time(s)
- `Interactive Development Loop` — seen 1 time(s)
- `Execution Safety and Validation` — seen 1 time(s)
- `Agent Definition` — seen 1 time(s)
