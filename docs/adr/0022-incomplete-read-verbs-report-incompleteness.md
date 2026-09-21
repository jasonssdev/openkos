---
type: Decision
title: "ADR-0022: An incomplete read verb reports incompleteness as data and as exit code 2"
description: A read verb that could not finish reports which checks did not run, and exits 2 rather than 0 or 1.
status: Proposed
date: 2026-09-21
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-21T00:00:00Z
sensitivity: public
---

# ADR-0022: An incomplete read verb reports incompleteness as data and as exit code 2

- **Status:** Proposed
- **Date:** 2026-09-21

## Context

`doctor` and `lint` are compute-then-render: the service gathers every check,
then the CLI prints. Seven reads inside those services are unguarded — four in
`doctor` (`okf.survey_bundle`, `bundle_ledger.scan_torn_writes`,
`bundle_ledger.scan_nesting_violations`, and the injected
`reset_point_available()` thunk) and three in `lint` (the late names-only
bundle walks behind `check_non_nfc_names`,
`check_state_dir_contains_no_markdown`, `check_dot_dir_markdown`). One
`OSError` from any of them destroys the whole report: `doctor` loses all
fifteen `CheckResult`s, `lint` loses eleven already-computed finding lists, and
the operator gets a traceback. Both gaps are pre-existing, verified against
`git show main:src/openkos/cli/main.py`; the read-core extraction chain
(#999–#1003) neither introduced nor fixed them. They are the *maintenance*
verbs, so they fail exactly on the workspaces that most need diagnosing.

Three forces shape the decision.

**A traceback today is nonzero.** The naive repair — wrap each read, keep the
existing exit rule — would newly exit **`0`** on a workspace broken enough that
three diagnostics never ran. `doctor`'s predicate is
`any(r.status == "fail" and r.critical for r in results)`, and a check that did
not run is not a `fail`. Trading an ugly traceback for a false "healthy" is a
silent regression in the one direction a health verb must never move, and it is
invisible in review because every existing test still passes.

**The distinction has to be data, not prose.** MVP 3's MCP adapter consumes
these same application services. An adapter that must parse `[NOT RUN]` out of
rendered text to learn that a diagnosis is partial is a second implementation
of the report, which is precisely what the application layer (ADR-0018) exists
to prevent.

**`0`/`1` has no room.** `1` already means "a critical check failed" — a known,
actionable diagnosis. "We could not tell you" is a different claim with a
different repair, and folding it into `1` would make a workspace with an
unreadable directory indistinguishable from one with an unreachable Ollama.

## Decision

We adopt a third outcome and a third exit code for read verbs.

A check that could not run reports `not-run` as a structured peer of
`pass`/`fail`/`skip` on the value the service returns, carrying the raised
reason. The vocabulary is shared, not per-verb: one leaf module,
`src/openkos/read_outcome.py`, owns the token's single spelling and the
`NotRun(label, reason)` record. `doctor` widens `CheckResult.status` with that
token; `lint` gains `LintReport.not_run`. Both verbs print completed and
not-run counts. The leaf lives outside `application/` because `LintReport` is
declared in `openkos/lint.py`, and `application/__init__.py` forbids any of
those modules from importing upward into `openkos.application`.

Exit codes become `0` healthy, `1` a critical check failed, `2` the report
could not be completed. A critical failure **dominates** an incomplete report:
`not-run` alongside a critical `fail` exits `1`, not `2`, because the known
failure is the more actionable signal. `lint`'s findings stay non-gating; only
an incomplete run makes it exit `2`.

## Consequences

Easier: an operator sees fourteen working diagnoses instead of a traceback, and
sees *which* one is missing and why. A script can distinguish "broken" from
"could not tell". The MVP 3 MCP adapter reads one word across both verbs
without parsing output. Each new containment is per-site and narrow, so the
services keep degrading only where the environment failed.

Harder, and accepted:

- **`2` is public surface from the day it ships.** Withdrawing it, or renaming
  the `not-run` token, is a breaking change for user scripts and for the MCP
  adapter. That irreversibility is why this is an ADR.
- **Every new check must now decide its own failure mode.** "Can this raise,
  and is that the environment's fault or the programmer's?" becomes part of
  adding a check. We deliberately do not catch `ValueError` where it signals a
  caller bug (`Path.relative_to` on a path outside the bundle) — containing it
  would convert a programming error into a soft "did not run".
- **A `dict`-keyed renderer can now miss a status.** The widened `Literal`
  makes a missing tag a silent `KeyError`, so the render map needs a drift
  guard test rather than review attention.
- **One more layering translation.** `application/*` may not import
  `openkos.vcs` (AST-enforced), so the injected git probe's `GitError` is
  translated to an application-owned exception at the CLI boundary, mirroring
  `application/lint.py::LintInputUnavailable`.
- **Callers treating any nonzero as failure see no change in kind.** They
  already saw nonzero here — a raise. `2` narrows the meaning rather than
  adding a new failure class.

## Alternatives considered

**Fold incompleteness into `1`.** Cheapest, and it loses the whole point: the
operator cannot tell a diagnosed failure from an undelivered diagnosis, and the
MCP adapter has nothing to branch on.

**Wrap the reads and leave the exit rule alone.** Rejected as the silent
regression above: a partially-run `doctor` would exit `0` and report health it
never measured.

**Let the renderer invent a "did not run" string.** Keeps the services
unchanged, but puts the distinction only in text. Every non-CLI caller would
have to parse it back out — the second implementation ADR-0018 exists to
prevent.

**Envelope every `LintReport` field in a `CheckOutcome[...]`.** The most
uniform shape, rejected on blast radius: it rewrites all fourteen fields and
every render site to express a fact that applies to three checks.

**A `lint`-only private vocabulary.** Smallest diff, rejected because it makes
an adapter learn two words for one fact across two verbs in the same layer.

**A single `except Exception` per service.** Rejected: it would swallow genuine
programming errors into a permanently green "did not run", the same widening
that was tried and reverted in #995 PR 4 when it named an in-memory
`ValueError` a read failure.
