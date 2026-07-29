---
type: Decision
title: "ADR-0010: Re-ingest resolves sensitivity as a raise-only high-water mark"
description: openkos ingest --regenerate resolves a Source's sensitivity as combine_sensitivity(on_disk, cfg.default_sensitivity); the resolved value feeds both the Source document and the derived-object stamp; an unreadable existing Source aborts the ingest.
status: Accepted
date: 2026-07-28
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-28T00:00:00Z
sensitivity: public
---

# ADR-0010: Re-ingest resolves sensitivity as a raise-only high-water mark

- **Status:** Accepted
- **Date:** 2026-07-28

## Context

Issue #229: re-ingest was a silent declassification path. `openkos ingest`'s
`regenerate=True` branch (`main.py:1667-1676`, pre-fix) built the Source
concept from `cfg.default_sensitivity` unconditionally and never read the
existing document on disk. `write_atomic(concept_path, concept_content)`
(`main.py:1794`) then overwrote the on-disk Source with that freshly built
document — so a level a human had explicitly raised via `set-sensitivity`
was reset on the next re-ingest, with no `--allow-downgrade` and no prompt,
routing entirely around the gate ADR-0008 exists to enforce. The stamp read
back for newly extracted derived objects (`main.py:1683-1684`) was taken
from that same freshly built, wrongly-downgraded document, so new derived
objects inherited the wrong value too. Existing derived objects were
unaffected — Phase B writes them `write_exclusive` (create-only,
`main.py:1804`) — but the Source document itself, and any newly staged
derived object, were not. One root cause, two symptoms; the original issue
report described only the second.

## Decision

We adopt high-water-mark resolution for a re-ingest's Source sensitivity:
`resolved = okf.combine_sensitivity(on_disk_value, cfg.default_sensitivity)`,
computed once, before `okf.build_source_concept` is called, so the single
resolved value flows unchanged through every downstream consumer — the
bytes written to `concept_path` and the `stamp_sensitivity` passed to
`_stage_derived_objects` for any derived object newly staged on that same
re-ingest. A re-ingest can therefore only raise or preserve a Source's
sensitivity, never lower it.

When the Source concept is absent on the regenerate path (the post-`forget`
case `main.py` already special-cases), resolution skips the read entirely
and uses `cfg.default_sensitivity` directly — `None` is never passed into
`combine_sensitivity`, since `okf._rank(None)` floors at `private`, which
would wrongly raise a `public` workspace default to `private` with nothing
on disk to justify it.

When the existing Source's on-disk frontmatter cannot be read (`OSError`) or
cannot be parsed (including `yaml.YAMLError`, which is neither `OSError`
nor `ValueError`), the ingest aborts with exit 1 and writes nothing.
Degrading to the config default in that case would write a *lower* level
over an unreadable classification — the exact silent declassification this
decision exists to remove.

The extraction gate's `workspace_floor` parameter — `_stage_derived_objects
(workspace_floor=..., stamp_sensitivity=...)` — keeps tracking
`cfg.default_sensitivity` literally, unrelated to the resolved or on-disk
value. Feeding the resolved value into `workspace_floor` would make
`blocks_llm_send(workspace_floor)` short-circuit extraction whenever a
Source had been raised to `confidential`, silently disabling extraction —
violating `sensitivity-aware-llm` Requirement 4, which this decision leaves
unchanged.

The resolved level is always named in the re-ingest preview, with a
trailing clause distinguishing the three causes (preserved from the
existing Source, raised by the workspace default, or unchanged) — a
sensitivity write is never silent, matching the precedent ADR-0009 set for
`set-sensitivity`'s propagation preview.

## Consequences

Easier: re-ingest — a bulk mechanical verb intended to be idempotent and
safe to run repeatedly — can no longer be used, even accidentally, to
reverse a deliberate `set-sensitivity` correction. The high-water-mark rule
also means raising a workspace's `default_sensitivity` now correctly lifts
an under-classified Source on its next re-ingest, closing a gap the
rejected read-and-reuse alternative would have left open.

Harder: re-ingest gains one additional disk read (the existing Source's
frontmatter) and one additional failure mode (unparseable existing
frontmatter aborts the whole ingest rather than degrading). An operator who
genuinely wants re-ingest to apply a *lowered* `default_sensitivity` to an
already-raised Source must run `set-sensitivity --allow-downgrade`
explicitly first — re-ingest deliberately gains no equivalent flag, since it
is a bulk mechanical verb, not a deliberate reclassification tool.

## Alternatives considered

- **Read-and-reuse (keep the on-disk value verbatim, ignore the config
  default entirely).** Rejected: neither option can lower a Source, so
  read-and-reuse buys no downgrade-prevention benefit over the high-water
  mark — but it also ignores a workspace default that has been *raised*
  since the Source was last written, leaving that Source sitting below the
  very `workspace_floor` that gates its own LLM send during future
  extraction attempts.
- **`ingest --allow-downgrade`, mirroring `set-sensitivity`'s flag.**
  Rejected: duplicates ADR-0008's gate in the wrong verb. `set-sensitivity
  --allow-downgrade` already exists as the one sanctioned, explicit,
  single-concept downgrade path; a bulk re-ingest verb should not grow a
  second, easier-to-trigger-by-accident door to the same outcome.
- **Post-render frontmatter merge (`load_frontmatter` → mutate dict →
  `dump_frontmatter`) instead of resolving before `build_source_concept`.**
  Rejected: `dump_frontmatter` re-sorts keys alphabetically on every call,
  so this would still be byte-safe, but it is a second render for zero
  benefit over passing `sensitivity=resolved` through the existing
  `build_source_concept` parameter, and it invites body/lede drift between
  the two render passes.
- **String substitution on the rendered frontmatter.** Rejected outright:
  string surgery on a security-classification field.

## Does not supersede

- **ADR-0003** (`combine_sensitivity`, `okf._rank` fail-closed ranking) —
  this decision consumes that primitive as-is; it does not restate or
  revise it.
- **ADR-0008** (`set-sensitivity --allow-downgrade` as the sole sanctioned
  downgrade path) — unchanged and unedited; re-ingest deliberately gains no
  parallel downgrade flag, per "Alternatives considered" above.
- **ADR-0009** (Source sensitivity propagation to provenance descendants at
  `set-sensitivity` time) — unchanged and unedited; propagation is a
  set-time concern across the whole provenance closure, while this decision
  is an ingest-time, single-Source, create-only-for-derived-objects
  concern. The two compose without conflict.
