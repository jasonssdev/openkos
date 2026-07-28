---
type: Decision
title: "ADR-0008: Human sensitivity override, and where lowering needs a flag"
description: An explicit human assignment may lower sensitivity; unattended paths require --allow-downgrade.
status: Proposed
date: 2026-07-27
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-27T00:00:00Z
sensitivity: public
---

# ADR-0008: Human sensitivity override, and where lowering needs a flag

- **Status:** Proposed
- **Date:** 2026-07-27

## Context

`sensitivity` (`public` / `private` / `confidential`) is written today by exactly
two paths: `ingest` stamps `cfg.default_sensitivity` verbatim, and `merge`
recomputes the survivor's value via `okf.combine_sensitivity`. No verb lets a
human correct one existing concept's value; a concept mis-stamped by the
workspace default can only be fixed by hand-editing frontmatter — unvalidated,
unlogged, uncommitted. Issue #185 asks for that verb.

ADR-0003 stands in the way of the obvious reading. It rejected survivor-wins
because it "can silently downgrade a confidential absorbed object into a public
survivor", and states that "a security field must fail toward more restrictive,
never less." Read as a universal rule, that sentence forbids the verb outright.
A future reader will apply it that way unless the scope is recorded.

The forces: correcting a wrong default is a legitimate downgrade and is the
whole point of the verb; AGENTS.md requires that "consequential changes stay
reviewable, not silently automatic"; and the confirm prompt — the mechanism that
makes a change reviewable — does not always run. `--auto` silences it for one
invocation, a non-interactive stdin makes it impossible to ask at all, and
config `review: false` silences it workspace-wide, for every
verb. On those paths a script downgrades an access-control field with no human
present at the moment it happens, which is structurally the `merge` case
ADR-0003 refused, not the reviewed case AGENTS.md permits.

## Decision

We scope ADR-0003's "never less" to **machine-chosen** values, and we adopt an
explicit human override for the human-chosen case.

ADR-0003's rule governs two things and only two: the automatic combine of two
derived values (`combine_sensitivity`), and the fail-closed ranking of dirty
input. It does not govern an explicit assignment a human states in argv. That is
the reviewable mechanism the principle asks for, not a violation of it.

Therefore `openkos set-sensitivity <concept-id> <level>` may lower a concept's
sensitivity. Raising and same-value assignment pass the standard confirm gate.
Lowering passes the standard gate **when the confirm prompt actually runs and is
accepted**. On every path where the prompt does not run — `--auto`, config
`review: false`, or a non-interactive stdin — lowering additionally requires an
explicit `--allow-downgrade`;
without it the verb refuses in Phase A with exit 1, no write, no commit, and a
message naming the flag. Friction is placed precisely, and only, where review is
absent.

Direction is classified against `SENSITIVITY_ORDER` through a new public
`okf.sensitivity_direction(current, target)`, which ranks `current` with
ADR-0003's fail-closed `_rank`. A missing, blank, or unrecognized current value
therefore ranks at or above `private`, so an assignment below it counts as a
lowering. The verb does not become a laundering path for malformed frontmatter.
The rank policy stays inside `model/okf.py`, the OKF seam; the CLI never learns
the ordering.

## Consequences

Easier: a wrong `default_sensitivity` stamp becomes correctable through a
validated, previewed, logged, auto-committed verb instead of a hand edit; the
scope of ADR-0003 is written down, so a future reader does not have to choose
between the verb and the ADR; `sensitivity_direction` gives any later verb one
place to ask about direction.

Harder: `--allow-downgrade` becomes load-bearing in the unattended contract.
Removing it later breaks every script that passes it, and loosening it silently
re-opens the unattended-downgrade path this decision closes. The rule is also
mode-dependent — the same lowering succeeds interactively and refuses under
`--auto` — so both arms need permanent test coverage, in particular the
`review: false` case and the dirty-current-value case. Because ADRs are
append-only, a reversal is recorded by a superseding ADR, not by editing this
one.

## Alternatives considered

- **Refuse every lowering.** Rejected: it destroys the verb's purpose. #185 asks
  for exactly the downgrade that corrects a wrong default, and the fallback is
  an unvalidated hand edit — strictly worse for the same security field.
- **Nothing extra; the standard gate is enough.** Rejected: it leaves the
  unattended paths open. `review: false` in particular disables the prompt for
  the whole workspace, so "the human confirmed" would be an assumption, not a
  fact.
- **A distinct typed confirmation phrase** (as `reconcile --confirm-phrase`).
  Rejected: it adds ceremony where a human is already reading a preview line
  that says `lowering 'confidential' -> public` and typing `y`, and it cannot
  cover the unattended path at all — it solves the case that is not the problem.
- **A per-object floor from `cfg.default_sensitivity`.** Rejected: that config
  key is an ingest stamp and an LLM gate, never a minimum. Making it a floor
  would be a new, unrequested policy and would block the correction case.
- **Extending `combine_sensitivity` to the verb.** Rejected: it folds two values
  into a max; this assigns one already-validated literal, and reusing it would
  make every human assignment silently monotonic — the outcome this ADR
  deliberately declines.
