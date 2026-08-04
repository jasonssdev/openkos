# openkos title-anchor A/B (issue #377, proposal D1)

_Generated: 2026-08-04T15:11:48+00:00_

Model: **`qwen3:8b`**. Runs per fixture per arm: **5**.

Labeled fixtures (ground truth from the reference bundle): `call-with-maria` (target 3), `notes-on-enchiridion` (target 2).
Unlabeled corpus sources: **16**. No target types are declared for these, so `type_acc` and `anti_enum` exclude them and only count-shaped metrics apply.

`_SYSTEM_PROMPT` is byte-identical across every arm. The only variable is the `SOURCE TITLE:` value in the user turn.

## Arms

| Arm | Meaning | Title sent |
| --- | --- | --- |
| `h1` | v0.2.1 -- `derive_source_title(raw)` (the document's own H1) | `call-with-maria`: "Call with Maria Salazar — 2026-07-14"; `notes-on-enchiridion`: "Reading notes — Epictetus, Enchiridion — 2026-07-05"; … +16 more |
| `stem` | v0.2.0 -- `titleize(path.stem)` (the filename) | `call-with-maria`: "call with maria 2026 07 14"; `notes-on-enchiridion`: "notes on the enchiridion 2026 07 05"; … +16 more |

## Per-arm summary

| Arm | multi_obj_rate | twin_rate | avg_objects | max | schema_valid | type_acc | anti_enum | avg_lat_s | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `h1` | **0.09** (8/90, 3 src) | **0.34** | 1.19 | 5 | 0.96 | 0.67 | 0.83 | 12.82 | 0 |
| `stem` | **0.12** (11/88, 7 src) | **0.13** | 1.32 | 5 | 0.98 | 0.50 | 0.71 | 9.73 | 2 |

- **multi_obj_rate**: fraction of runs producing >= 2 objects, with the raw event count and how many distinct sources contributed. THE primary signal -- extraction does not fail by shaving a fraction off every source, it fails by enumerating on far fewer of them.
- **twin_rate**: fraction of produced objects whose title merely restates the SOURCE TITLE this arm sent (proposal D4). The anchor's fingerprint, and independent of the rate: an arm can enumerate just as often and still collapse every title onto the heading. The `none` arm has no title to echo, so it reads 0.00 by construction, not by merit.
- **avg_objects**: reported for continuity, NOT the criterion. The distribution is bimodal (mode 1, tail to 3-5), so the mean barely moves when the rare event doubles: measured 1.22 vs 1.50 for arms whose rates were 0.08 and 0.17.

## Object count per source

| Source | `h1` | `stem` |
| --- | --- | --- |
| `call-with-maria` | 1/1/1/1/1 | 1/1/1/1/1 |
| `notes-on-enchiridion` | 3/3/3/3/3 | 3/3/3/3/3 |
| `01-what-is-claude-code` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/3/1 |
| `02-how-claude-code-works` *(unlabeled)* | 5/1/1/0/1 | 5/1/1/1/1 |
| `03-installing-claude-code` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/2/1 |
| `04-your-first-prompt` *(unlabeled)* | 1/1/1/1/0 | 1/4/1/1/1 |
| `05-workflow` *(unlabeled)* | 1/1/1/0/1 | 1/1/1/1/1 |
| `06-context-management` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/1/1 |
| `06-mcp-client` *(unlabeled)* | 1/1/5/4/1 | 1/1/1/5/1 |
| `07-code-review` *(unlabeled)* | 0/1/1/1/1 | 1/1/1/1/1 |
| `07-guardrails` *(unlabeled)* | 1/1/1/1/1 | 5/1/1 |
| `08-the-claude-file` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/1/1 |
| `09-subagents` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/1/1 |
| `10-mcp` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/1/1 |
| `11-hooks` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/1/1 |
| `call-with-maria-2026-07-14` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/1/1 |
| `mcp-launch` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/1/1 |
| `mcp-origin` *(unlabeled)* | 1/1/1/1/1 | 1/1/1/1/1 |

One cell per arm, showing every run's count in order (`1/1/1` means three runs of one object each).

## Per-fixture detail (raw [type:title] per run)

### `call-with-maria`

- Target: 3 -> {'Decision': 1, 'Person': 1, 'Concept': 1}

- `h1`:
    - run 1 (10.3s): [Person:Maria Salazar]
    - run 2 (2.5s): [Person:Maria Salazar]
    - run 3 (5.9s): [Person:Maria Salazar]
    - run 4 (6.0s): [Person:Maria Salazar]
    - run 5 (5.8s): [Person:Maria Salazar]
- `stem`:
    - run 1 (7.3s): [Event:Call with Maria 2026 07 14]
    - run 2 (6.7s): [Event:Call with Maria 2026 07 14]
    - run 3 (6.7s): [Event:Call with Maria 2026 07 14]
    - run 4 (6.7s): [Event:Call with Maria 2026 07 14]
    - run 5 (6.7s): [Event:Call with Maria 2026 07 14]

### `notes-on-enchiridion`

- Target: 2 -> {'Concept': 2}

- `h1`:
    - run 1 (6.8s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 2 (6.8s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 3 (6.1s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 4 (6.0s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 5 (5.9s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
- `stem`:
    - run 1 (6.8s): [Concept:Dichotomy of Control], [Concept:Stoicism vs. Epicureanism], [Concept:Apatheia]
    - run 2 (6.9s): [Concept:Dichotomy of Control], [Concept:Stoicism vs. Epicureanism], [Concept:Apatheia]
    - run 3 (5.9s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]
    - run 4 (6.9s): [Concept:Dichotomy of Control], [Concept:Stoicism vs. Epicureanism], [Concept:Apatheia]
    - run 5 (6.6s): [Concept:Dichotomy of Control], [Concept:Stoicism], [Concept:Apatheia]

### `01-what-is-claude-code`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (16.0s): [Concept:Claude Code]
    - run 2 (14.5s): [Concept:Claude Code]
    - run 3 (4.4s): [Concept:Claude Code]
    - run 4 (14.6s): [Concept:Claude Code]
    - run 5 (14.6s): [Concept:Claude Code]
- `stem`:
    - run 1 (5.6s): [Concept:Claude Code]
    - run 2 (14.6s): [Concept:Claude Code]
    - run 3 (14.6s): [Concept:Claude Code]
    - run 4 (6.7s): [Concept:Claude Code], [Concept:AI Agent], [Concept:Agentic Coding Tool]
    - run 5 (14.6s): [Concept:Claude Code]

### `02-how-claude-code-works`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (14.2s): [Concept:Agentic Loop], [Concept:Context Window], [Concept:Tools], [Concept:Permissions], [Concept:Claude Code]
    - run 2 (13.4s): [Concept:Claude Code]
    - run 3 (13.2s): [Concept:Claude Code]
    - run 4 (12.2s): [] (nothing extracted)
    - run 5 (13.3s): [Concept:Claude Code]
- `stem`:
    - run 1 (29.9s): [Concept:Claude Code], [Concept:Agentic Loop], [Concept:Context Window], [Concept:Tools], [Concept:Permissions]
    - run 2 (13.1s): [Concept:Claude Code]
    - run 3 (13.1s): [Concept:Claude Code]
    - run 4 (13.1s): [Concept:Claude Code]
    - run 5 (13.4s): [Concept:Claude Code]

### `03-installing-claude-code`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (4.0s): [Concept:Claude Code]
    - run 2 (2.6s): [Concept:Claude Code]
    - run 3 (3.2s): [Concept:Claude Code]
    - run 4 (3.2s): [Concept:Claude Code]
    - run 5 (2.4s): [Concept:Claude Code]
- `stem`:
    - run 1 (3.9s): [Concept:Claude Code]
    - run 2 (2.5s): [Concept:Claude Code]
    - run 3 (2.5s): [Concept:Claude Code]
    - run 4 (4.6s): [Concept:Claude Code], [Procedure:Installing Claude Code]
    - run 5 (2.4s): [Concept:Claude Code]

### `04-your-first-prompt`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (13.6s): [Procedure:Using Claude Code for Prompt-Based Tasks]
    - run 2 (11.9s): [Concept:Your First Prompt]
    - run 3 (12.1s): [Concept:Your First Prompt]
    - run 4 (12.3s): [Procedure:Using Claude Code for Prompt-Based Tasks]
    - run 5 (18.9s): [] (nothing extracted)
- `stem`:
    - run 1 (13.5s): [Concept:Claude Code Prompting Guide]
    - run 2 (8.4s): [Concept:Claude Code], [Concept:Auto-Accept vs. Approval], [Concept:Plan Mode], [Concept:Dark Mode Toggle Example]
    - run 3 (12.3s): [Procedure:Using Claude Code for the First Time]
    - run 4 (12.3s): [Procedure:Using Claude Code for Your First Prompt]
    - run 5 (1.9s): [Concept:Claude Code]

### `05-workflow`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (3.4s): [Concept:Workflow]
    - run 2 (2.0s): [Concept:Workflow]
    - run 3 (1.8s): [Concept:Workflow]
    - run 4 (27.3s): [] (nothing extracted)
    - run 5 (2.6s): [Concept:Workflow]
- `stem`:
    - run 1 (3.4s): [Concept:Explore, Plan, Code, and Commit Workflow]
    - run 2 (2.1s): [Concept:Explore, Plan, Code, and Commit Workflow]
    - run 3 (2.8s): [Concept:Explore, Plan, Code, and Commit Workflow]
    - run 4 (2.6s): [Concept:Explore, Plan, Code, and Commit Workflow]
    - run 5 (19.0s): [Concept:Explore, Plan, Code, and Commit Workflow]

### `06-context-management`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (17.2s): [Concept:Context Management]
    - run 2 (27.0s): [Concept:Context Management]
    - run 3 (30.2s): [Concept:Context Management]
    - run 4 (30.6s): [Concept:Context Management]
    - run 5 (30.6s): [Concept:Context Management]
- `stem`:
    - run 1 (17.2s): [Concept:Context Management]
    - run 2 (15.6s): [Concept:Context Management]
    - run 3 (15.6s): [Concept:Context Management]
    - run 4 (15.5s): [Concept:Context Management]
    - run 5 (15.6s): [Concept:Context Management]

### `06-mcp-client`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (11.9s): [Project:Building an MCP Client Inside a Chatbot]
    - run 2 (93.1s): [Procedure:Building an MCP Client Inside a Chatbot]
    - run 3 (23.5s): [Concept:MCP Client], [Concept:Model Context Protocol (MCP)], [Procedure:Building an MCP Client Inside a Chatbot], [Concept:MCP Host], [Concept:Tool Execution Pipeline]
    - run 4 (19.7s): [Concept:MCP Client], [Concept:Model Context Protocol (MCP)], [Procedure:Building an MCP Client Inside a Chatbot], [Project:MCP Chatbot Integration]
    - run 5 (63.4s): [Procedure:Building an MCP Client Inside a Chatbot]
- `stem`:
    - run 1 (51.7s): [Concept:MCP Client]
    - run 2 (2.9s): [Concept:MCP Client]
    - run 3 (2.6s): [Concept:MCP Client]
    - run 4 (14.9s): [Concept:MCP Client], [Concept:Model Context Protocol (MCP)], [Procedure:Building an MCP Client Inside a Chatbot], [Concept:MCP Host], [Concept:Tool Execution in MCP]
    - run 5 (47.7s): [Concept:MCP Client]

### `07-code-review`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (5.1s): [] (nothing extracted)
    - run 2 (9.1s): [Procedure:Code Review Workflow]
    - run 3 (9.3s): [Procedure:Code Review Procedure]
    - run 4 (7.5s): [Procedure:Code Review Workflow]
    - run 5 (9.3s): [Procedure:Code Review Workflow]
- `stem`:
    - run 1 (10.5s): [Procedure:Code Review Workflow]
    - run 2 (9.6s): [Procedure:Code Review Workflow]
    - run 3 (9.5s): [Procedure:Code Review Workflow]
    - run 4 (9.4s): [Procedure:Code Review Workflow]
    - run 5 (9.3s): [Procedure:Code Review Workflow]

### `07-guardrails`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (58.3s): [Concept:ADK Callbacks, Guardrails, and Instruction Tuning]
    - run 2 (54.4s): [Concept:ADK Callbacks, Guardrails, and Instruction Tuning]
    - run 3 (3.8s): [Concept:ADK Callbacks, Guardrails, and Instruction Tuning]
    - run 4 (53.9s): [Concept:ADK Callbacks, Guardrails, and Instruction Tuning]
    - run 5 (4.1s): [Concept:ADK Callbacks, Guardrails, and Instruction Tuning]
- `stem`:
    - run 1 (19.6s): [Concept:Guardrails in AI Agents], [Concept:Callback Systems in ADK], [Concept:Domain Blocking in AI Agents], [Concept:Callback Lifecycle in ADK], [Concept:ADK (Agent Development Kit)]
    - run 2 (120.0s): ERROR -- OllamaUnavailable: Ollama not reachable at localhost:11434: timed out
    - run 3 (2.9s): [Concept:Guardrails in AI Agents]
    - run 4 (120.0s): ERROR -- OllamaUnavailable: Ollama not reachable at localhost:11434: timed out
    - run 5 (2.5s): [Concept:Guardrails in AI Agents]

### `08-the-claude-file`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (5.4s): [Concept:CLAUDE.md]
    - run 2 (3.7s): [Concept:CLAUDE.md]
    - run 3 (2.2s): [Concept:CLAUDE.md]
    - run 4 (2.8s): [Concept:CLAUDE.md]
    - run 5 (2.8s): [Concept:CLAUDE.md]
- `stem`:
    - run 1 (4.3s): [Concept:CLAUDE.md]
    - run 2 (2.8s): [Concept:CLAUDE.md]
    - run 3 (3.1s): [Concept:CLAUDE.md]
    - run 4 (4.1s): [Concept:CLAUDE.md]
    - run 5 (3.2s): [Concept:CLAUDE.md]

### `09-subagents`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (11.9s): [Concept:Subagents]
    - run 2 (12.2s): [Concept:Subagents]
    - run 3 (12.3s): [Concept:Subagents]
    - run 4 (12.0s): [Concept:Subagents]
    - run 5 (12.1s): [Concept:Subagents]
- `stem`:
    - run 1 (12.8s): [Concept:Subagents]
    - run 2 (12.3s): [Concept:Subagents]
    - run 3 (11.9s): [Concept:Subagents]
    - run 4 (11.8s): [Concept:Subagents]
    - run 5 (12.0s): [Concept:Subagents]

### `10-mcp`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (17.0s): [Concept:Model Context Protocol]
    - run 2 (15.3s): [Concept:Model Context Protocol]
    - run 3 (15.3s): [Concept:Model Context Protocol]
    - run 4 (15.4s): [Concept:Model Context Protocol]
    - run 5 (15.7s): [Concept:Model Context Protocol]
- `stem`:
    - run 1 (16.6s): [Concept:Model Context Protocol]
    - run 2 (15.5s): [Concept:Model Context Protocol]
    - run 3 (15.7s): [Concept:Model Context Protocol (MCP)]
    - run 4 (14.5s): [Concept:Model Context Protocol]
    - run 5 (15.3s): [Concept:Model Context Protocol]

### `11-hooks`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (17.8s): [Concept:Hooks]
    - run 2 (16.2s): [Concept:Hooks]
    - run 3 (16.1s): [Concept:Hooks]
    - run 4 (16.1s): [Concept:Hooks]
    - run 5 (16.1s): [Concept:Hooks]
- `stem`:
    - run 1 (17.5s): [Concept:Hooks]
    - run 2 (16.2s): [Concept:Hooks]
    - run 3 (16.2s): [Concept:Hooks]
    - run 4 (16.1s): [Concept:Hooks]
    - run 5 (16.1s): [Concept:Hooks]

### `call-with-maria-2026-07-14`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (6.5s): [Person:Maria Salazar]
    - run 2 (6.1s): [Person:Maria Salazar]
    - run 3 (6.0s): [Person:Maria Salazar]
    - run 4 (5.5s): [Person:Maria Salazar]
    - run 5 (5.1s): [Person:Maria Salazar]
- `stem`:
    - run 1 (7.3s): [Event:Call with Maria 2026 07 14]
    - run 2 (6.7s): [Event:Call with Maria 2026 07 14]
    - run 3 (6.7s): [Event:Call with Maria 2026 07 14]
    - run 4 (6.6s): [Event:Call with Maria 2026 07 14]
    - run 5 (6.7s): [Event:Call with Maria 2026 07 14]

### `mcp-launch`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (1.3s): [Event:MCP Launching]
    - run 2 (1.1s): [Event:MCP Launching]
    - run 3 (1.1s): [Event:MCP Launching]
    - run 4 (1.1s): [Event:MCP Launching]
    - run 5 (1.1s): [Event:MCP Launching]
- `stem`:
    - run 1 (1.3s): [Event:MCP Launching]
    - run 2 (1.1s): [Event:MCP Launching]
    - run 3 (1.1s): [Event:MCP Launching]
    - run 4 (1.1s): [Event:MCP Launching]
    - run 5 (1.1s): [Event:mcp Launching]

### `mcp-origin`

- Target: none declared (unlabeled corpus source)

- `h1`:
    - run 1 (1.2s): [Event:MCP Origin]
    - run 2 (1.1s): [Event:MCP Origin]
    - run 3 (1.1s): [Event:MCP Origin]
    - run 4 (1.1s): [Event:MCP Origin]
    - run 5 (1.1s): [Event:MCP Origin]
- `stem`:
    - run 1 (1.2s): [Event:MCP Origin]
    - run 2 (1.1s): [Event:MCP Origin]
    - run 3 (1.1s): [Event:MCP Origin]
    - run 4 (1.1s): [Event:MCP Origin]
    - run 5 (1.1s): [Event:MCP Origin]

## Verdict

- `h1`: multi-object rate **0.09** (8/90 runs, across 3 source(s), max 5); twin_rate **0.34**; mean 1.19.
- `stem`: multi-object rate **0.12** (11/88 runs, across 7 source(s), max 5); twin_rate **0.13**; mean 1.32.

- Type-conditional probe: `named-entity` n=56, **every run produced exactly 1** (zero variance); `Concept/Entity` n=118, mean 1.42, max 5, 19 run(s) enumerated. **A hard cap on the named-entity side.** No run landing on one of the seven types phrased ONE-specific-named-X ever produced a second object, while the exempt side did. But the exempt side still produces one object most of the time, so the rubric wording explains those capped runs and NOT the collapse as a whole. Do not read this as the cause.

**The anchor moves extraction.** With `_SYSTEM_PROMPT` held
byte-identical across arms:

- It changes **what** the model produces: twin_rate `stem` 0.13 -> `h1` 0.34. Objects under `h1` restate the source's own heading instead of naming what the document contains -- the twin #377 describes.

Record this in `design.md` before any prompt edit. Note what it implicates: a title-framing change lives in `_build_messages`, not in the rubric -- which per the proposal is an ingest/extraction interface decision and returns through the ADR gate.
