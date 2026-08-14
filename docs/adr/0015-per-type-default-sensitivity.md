---
type: Decision
title: "ADR-0015: Per-type default sensitivity as a floor-relative offset"
description: Why a Person is born one level above the workspace sensitivity floor.
status: Accepted
date: 2026-08-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-08-14T00:00:00Z
sensitivity: public
---

# ADR-0015: Per-type default sensitivity as a floor-relative offset

- **Status:** Accepted
- **Date:** 2026-08-14

## Context

Sensitivity has been one workspace-wide scalar since ADR-0003: `default_sensitivity`
sets a single floor, and `combine_sensitivity` raises a derived object only when a
SOURCE it inherits from is more sensitive. Object TYPE has never entered that
calculation. A `Person` extracted from an ordinary meeting transcript is therefore
born at exactly the same level as a `Procedure` extracted from the same file.

People are the highest-risk objects a bundle holds. They carry names, roles,
affiliations, and — through relations — a social graph that no single source
document states outright. The risk is a property of the object class, not of any
one source, and the existing machinery has no way to express that.

Two constraints shape the answer. First, the default must be RELATIVE: an
operator who sets a `public` floor for a public knowledge base and one who sets a
`confidential` floor for a client engagement mean different things by "one level
more careful about people", and an absolute level would silently override the
second operator's stricter choice or under-serve the first. Second, whatever
mechanism ships must make adding `Organization` a configuration change, not a
code change — a per-type policy hard-coded as a `Person` constant is a policy
that only its author can extend.

## Decision

We add `type_sensitivity_defaults`, a workspace configuration mapping an OKF type
to a non-negative **offset above the workspace floor**, shipping as `{Person: 1}`.

A derived object's birth level is:

    combine_sensitivity(base, raise_by(default_sensitivity, offset))

where `base` is the inheritance the object already had — the Source's resolved
sensitivity on the `ingest` path, the cited concepts' high-water-mark on the
`query --save` path — and `raise_by` walks `SENSITIVITY_ORDER` upward, clamped at
`confidential`.

The offset applies to the CONFIGURED FLOOR, never to `base`. The type default is
therefore a floor-relative MINIMUM, not a bonus: it can only raise an object that
inheritance left at or below the floor plus the offset, and ADR-0003's
high-water-mark still wins outright whenever a source is more sensitive than that.

Entries are validated EAGERLY at `read_config`, not degraded: an unknown type key,
a non-integer offset, or an offset that is inert at every possible floor fails the
config load. This follows the `models:` precedent rather than the `type_tiers:`
one, on the grounds that a silently-wrong SECURITY default produces a run that
looks completely ordinary.

Because a `confidential` object is excluded from `query`, `contradictions`, and
`suggest-relations` against a non-local backend (issue #569), the write paths
disclose, at write time, how many objects were born above the floor by type
default and what that exclusion means. The exclusion is the intended effect; the
silence about it would not be.

This applies at BIRTH only. There is no migration and no backfill of concepts
already on disk, in either direction.

## Consequences

Easier: the workspace can express "be more careful about people" once, in
configuration, and every birth path honours it identically; adding `Organization`
is one line; the mechanism composes with merge, lint, `set-sensitivity`, and the
retrieval filter without touching any of them, because all four are already
type-blind and rank-based.

Harder: bundles ingested before and after this change will hold `Person` concepts
at different levels with no visible marker distinguishing them, and reconciling
that is a manual `set-sensitivity` sweep. A `Person` born `confidential` on a
`private` workspace silently leaves non-local retrieval — the write-time advisory
is the only thing standing between that and a confusing empty result set. And the
default is socially hard to reverse: once bundles ship with Persons at a higher
level, lowering the shipped default would look like a security regression even
where it is merely a correction.

## Alternatives considered

- **An absolute per-type level** (`{Person: confidential}`): rejected — it
  overrides a stricter operator floor in one direction and ignores a laxer one in
  the other, and ruling 1 asks for relative.
- **A hard-coded `PERSON_SENSITIVITY_BONUS` constant, no config seam**: rejected —
  adding `Organization` would then be a code change, and a per-type security policy
  that only its author can extend is not a policy.
- **Applying the offset to the inherited value** (`raise_by(base, offset)`):
  rejected — it double-raises, so a `private` Source on a `public` workspace births
  a `confidential` Person, which is neither what the operator configured nor what
  ADR-0003's inheritance means.
- **Lazy validation, degrading a malformed entry to no default**: rejected — the
  failure is invisible. Every Person in the bundle is then born at a level nobody
  chose, and nothing in the output says so.
- **Backfilling existing Person concepts**: rejected as out of scope — a bulk
  sensitivity rewrite is ADR-0012's territory and deserves its own decision, not a
  side effect of changing a default.
- **Rejecting an over-range offset at runtime instead of at config load**:
  rejected — the same silent-security-failure argument; a config error should
  surface when the config is read.
