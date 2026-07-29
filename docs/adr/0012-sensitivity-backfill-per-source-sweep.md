---
type: Decision
title: "ADR-0012: Sensitivity backfill as an explicit per-Source sweep, not a silent migration"
description: An existing-bundle sensitivity gap is closed by an operator-run sweep with per-Source-closure coverage, compensated by a detection signal for the concepts it cannot reach.
status: Proposed
date: 2026-07-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-29T00:00:00Z
sensitivity: public
---

# ADR-0012: Sensitivity backfill as an explicit per-Source sweep, not a silent migration

- **Status:** Proposed
- **Date:** 2026-07-29

## Context

ADR-0009 made Source-to-descendant sensitivity propagation part of
`set-sensitivity`: raising a Source's `sensitivity` also raises every
provenance descendant found by `find_provenance_descendants`. That
propagation only runs when `set-sensitivity` is actually invoked on a
Source. A bundle that ingested content, or extracted descendants, before
propagation existed (issue #219) — or one whose Sources have simply never
had `set-sensitivity` run against them — is left with descendants sitting
below their Source's true sensitivity, with no code path that ever
notices or repairs it.

Two questions forced a decision. First: should this gap close itself, or
should an operator have to ask for it? Second: `find_provenance_descendants`
resolves one Source's closure at a time; a bundle can have several Sources,
and a descendant's `provenance` can cite concepts spanning more than one of
them. What happens to a descendant that is not fully inside any single
Source's closure?

## Decision

We close the gap with **`openkos backfill-sensitivity`**, a dedicated,
raise-only, bundle-wide command an operator runs explicitly — never an
automatic migration triggered by `ingest`, `status`, or any other existing
verb. Every `type: Source` concept in the bundle is treated as an
independent provenance-closure root; for each, the command resolves its
descendants exactly as `set-sensitivity`'s propagation does
(`bundle.provenance.resolve_source_raises`), and where two or more Sources'
closures overlap on the same descendant, the raise with the highest
resulting sensitivity wins (merge-by-max, pinned to
`okf.SENSITIVITY_ORDER.index(new_level)`, never the private `okf._rank`).
One bundle-wide preview, one confirmation, one `log.md` entry, and one
commit cover the whole sweep, mirroring `set-sensitivity`'s own Phase A /
Phase B shape and its fail-closed partial-write handling (naming every
path that already landed before a mid-sweep failure, matching the #233
fix).

A descendant that is a member of **no single Source's** closure — for
example, one whose `provenance` cites two ids that resolve to two
genuinely unrelated Sources — is never written by this sweep. Silently
accepting that limitation was rejected: instead, `lint` and `status` gain
a `multi-source-uncovered` finding that surfaces exactly the concepts this
sweep cannot reach, naming every cited concept id and its level, and
explicitly marking the finding as not covered by `backfill-sensitivity`.
Coverage is therefore always visible, even where it is incomplete.

## Consequences

Easier: an operator has one command to bring an existing bundle's
sensitivity fully up to date with its Sources, with the same review
discipline (preview, confirm gate, fail-closed writes) every other
mutating verb already uses; a bundle that never called `set-sensitivity`
on a given Source is no longer silently under-classified forever; the
`multi-source-uncovered` finding gives an operator a concrete, actionable
list instead of an invisible gap.

Harder: the sweep is bundle-wide only in this MVP — there is no
per-Source scoping flag, so an operator cannot ask it to touch just one
Source's descendants (`set-sensitivity` already serves that narrower
case). A descendant spanning two or more genuinely unrelated Sources is
never combined across them by this command; combining sensitivity across
multiple Sources for such a descendant is deferred to MVP-2/3 per
ADR-0009, so today it stays reported, not resolved, until a human acts on
the `multi-source-uncovered` finding directly (typically via
`set-sensitivity` on the descendant itself, or a future combining verb).
The command also does not run the unresolvable-provenance scan
`set-sensitivity` runs on its target Source: every Source cites its raw
ingest `resource`, which never resolves to a bundle id, so running that
scan bundle-wide would emit one WARNING per Source on every invocation,
including the no-op path. `lint`'s existing `dangling` finding already
covers that signal read-only; extending the WARNING itself to be more
selective is out of scope here (tracked separately, issue #232).

## Alternatives considered

- **Run the backfill automatically on every `ingest`/`status`/`lint`
  invocation.** Rejected: an automatic silent migration changes bundle
  data as a side effect of an unrelated read-only or ingest command,
  violating the review discipline every other mutating write in this
  codebase follows. An operator should decide when a bundle-wide sweep of
  descendant sensitivities happens, and see the preview before it does.
- **Treat a descendant citing two or more Sources as covered by the
  highest of the two Sources' levels, combining across Source closures.**
  Rejected for this MVP: ADR-0009 already deferred cross-Source combining
  to MVP-2/3, and doing it silently here — without the human confirmation
  a `set-sensitivity` write on that descendant would get — risks
  over-committing to a combining rule before the multi-source model is
  settled. Reporting via `multi-source-uncovered` keeps the door open
  without prematurely encoding a rule.
- **Silently accept the multi-source coverage limit with no detection
  signal.** Rejected: an invisible gap in sensitivity coverage is worse
  than a visible, named one. The `multi-source-uncovered` finding turns an
  accepted limitation into an actionable one.
- **Add a per-Source scoping argument to `backfill-sensitivity`.**
  Rejected as unnecessary scope for this MVP: `set-sensitivity <source-id>
  <level>` already re-triggers the exact same per-Source propagation for a
  single Source; a bundle-wide sweep exists specifically for the case
  where an operator wants every Source covered in one pass.
