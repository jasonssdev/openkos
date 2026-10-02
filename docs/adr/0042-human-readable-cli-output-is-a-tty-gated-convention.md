---
type: Decision
title: "ADR-0042: Human-readable CLI output follows one TTY-gated convention"
description: Verbs that talk to a human share one output shape — summary first, grouped text-prefixed notices, blank-line sections, wrapped prose — applied only on a terminal, through one helper module.
status: Accepted
date: 2026-10-01
tags:
  - openkos
  - adr
  - cli
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-01T00:00:00Z
sensitivity: public
---

# ADR-0042: Human-readable CLI output follows one TTY-gated convention

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

The CLI's output is accurate but hard to read exactly where the project depends on a human reading it ("human curates, engine maintains"). A real `ingest` run printed six notices of equal weight before the proposal the user had to review, then repeated every path of that proposal on one line after the user confirmed it. `curate` printed dozens of near-identical suggestion blocks with no separation, and the `query` sufficiency refusal was one paragraph of roughly 300 characters.

Earlier polish (#805, #567, #139, #383, #384, #190, #701) fixed specific verbs. Without a shared shape, every new verb, and the interoperability surface to come, would start inconsistent again.

Forces: piped, redirected and `NO_COLOR` runs must stay byte-clean and greppable; stdout carries data and stderr carries human messages; `--json` output, exit codes and row-oriented verbs (`pending`, `list`) are machine surfaces; `cli/observability.py` already gates progress on `sys.stderr.isatty()`.

## Decision

We adopt one convention for human-readable output, implemented once in `openkos/cli/output.py` and applied verb by verb:

1. **Summary first.** One line states the outcome; detail follows it. A line never repeats what the user already confirmed in the same run.
2. **Notices are grouped.** A notice is one line with a text prefix by kind: `note:` or `warning:`. The long explanation stays in `lint`, `status` and the docs.
3. **Sections are separated by one blank line** (notices, proposal, prompt, result), and each item of an interactive walk is a visually separated block with a stable layout: header, rationale, prompt.
4. **Prose is wrapped** to the terminal width.
5. **All of the above applies only when the stream is a terminal.** Rules 2 to 4 change the presentation of text, so a non-terminal stream gets the text it always got. No ANSI escape is ever emitted, so `NO_COLOR` has nothing to disable and meaning never depends on colour.
6. **Prefixes are text only.** No symbols, boxes or tables by default; the goal is hierarchy and whitespace, not decoration.

The convention does not touch streams (data stays on stdout, human messages on stderr), `--json`, exit codes, or row-oriented verbs, which stay one line per record.

Human-readable stderr text is not a supported parsing interface. Scripts read stdout, `--json` and exit codes.

Migration starts with the verbs where the human decides — `ingest`, `curate` and `adjudicate --apply`, `query` — and the remaining verbs adopt the helpers as they are touched.

## Consequences

- New verbs get the shape by calling the helpers instead of formatting by hand.
- Piped output changes only where a verb's text itself was wrong, never because of styling; each such change is called out in the CHANGELOG.
- Characterization and golden tests pin exact output, so each migration updates its goldens deliberately.
- A TTY run and a piped run now differ in whitespace and wrapping; tests that need the TTY shape must fake a terminal.
- No new dependency: width comes from the standard library.

## Alternatives considered

- **A section in `CONTRIBUTING.md` only.** Cheap, but the choice is project-wide and hard to reverse once users script against the text, so it belongs in the decision log; `CONTRIBUTING.md` points here.
- **Symbols and colour on a TTY (a tick, a warning sign, a coloured prefix).** Rejected for restraint and for terminals and fonts that render them badly; text prefixes read the same everywhere.
- **Tables or boxes for proposals and walks.** Rejected: they wrap badly, cost width, and make the piped and terminal shapes diverge.
- **Always-on styling.** Rejected: redirected output would change, breaking the greppable contract.
