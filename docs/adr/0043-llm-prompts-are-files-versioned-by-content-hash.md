---
type: Decision
title: "ADR-0043: LLM prompts are files in one folder, versioned by content hash"
description: System prompts live one per file under src/openkos/prompts/ grouped by task, are read through one loader, and are identified by a derived content hash pinned by a registry test.
status: Accepted
date: 2026-10-02
tags:
  - openkos
  - adr
  - llm
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-02T00:00:00Z
sensitivity: public
---

# ADR-0043: LLM prompts are files in one folder, versioned by content hash

- **Status:** Accepted
- **Date:** 2026-10-02

## Context

The engine's system prompts were Python string constants spread across eight modules. Only two carried an identity (`SUBJECT_PROMPT_VERSION`, `JUDGE_PROMPT_VERSION`, each `sha256(prompt)[:16]`, used to key a persisted cache). Eval results record an arm name and a timestamp, not the prompt text they measured, and a prompt edit buried in a Python diff is easy to miss in review.

Prompt changes are adopted only after a measured A/B, so the text is a first-class, reviewable artifact. Forces: tests and evals import the existing module constants and `.replace()` them, and rely on the default bytes never changing; the wheel must ship the files; the core takes no new dependency; some prompts splice in values that code also uses (a derived vocabulary, a shared attribution keyword, another fragment).

## Decision

1. Each prompt is one file, `src/openkos/prompts/<task>/<name>.md`, grouped by task. Fragments spliced into a prompt are their own files. User turns, built from data at call time, stay in code.
2. One leaf loader, `openkos.llm.prompts.load_prompt(prompt_id)`, reads a file through `importlib.resources`, the way `templates/` is read. It returns the file's exact bytes decoded as UTF-8: no newline stripping, no normalisation. `.gitattributes` marks the folder `-text` so a checkout never rewrites line endings.
3. A prompt that must embed a value owned by code uses a `{{name}}` placeholder, filled by keyword arguments to `load_prompt`. The loader fails if a placeholder is left unfilled or an argument is unused. It is substitution only: no conditionals, no loops, no dependency.
4. The version of a prompt is its content hash, `sha256(text)[:16]`, derived by `prompts.prompt_hash` and never hand-bumped. History is git's job.
5. A registry test pins the hash of every prompt file in one table, and fails on an unregistered file or a missing one, so a prompt change is never silent: the pull request must update the pin deliberately, which flags that the change needs its measurement.
6. The existing module-level constants (`contradiction._SYSTEM_PROMPT` and the rest) stay, now assigned from the loader, so importers and eval `.replace()` arms keep working. The two cache-keying version constants keep their values.
7. The first migration is byte-identical: it moves text only, and every rendered prompt hashes the same before and after.

## Consequences

- A prompt edit is a diff in one markdown file, visible and reviewable on its own; the pin makes it a deliberate act.
- Evals can later stamp prompt id and hash into stored results (a separate change).
- Inline design comments that sat inside a Python string cannot live in the file; they move to a comment above the constant that loads it.
- A prompt file must keep its exact trailing bytes (several end without a newline, one ends with a blank line); editors that add a final newline change the hash and fail the pin, which is the intended signal.
- The loader is a stdlib-only leaf under `llm/`, importable from extraction, resolution and retrieval without a new layering edge.

## Alternatives considered

- **Keep constants, add a hash per module.** Smallest change and gives identity, but leaves prompts scattered and mixed into code.
- **Hand-maintained version numbers per prompt.** Rejected: humans forget to bump; a derived hash cannot drift.
- **A templating engine (Jinja).** Rejected: prompts are static text with a few named insertions; a dependency buys nothing.
- **Prompt text in `openkos.yaml` or user-overridable prompts.** Out of scope: it would make prompts a public config surface and break the measured-default guarantee.
