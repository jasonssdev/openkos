---
type: Decision
title: "ADR-0007: Volatility taxonomy and volatility-aware freshness windows"
description: A fixed static/slow/volatile taxonomy, a per-type default tier on the object registry, and an absent-by-default `volatility` frontmatter override drive lint's per-doc stale window instead of one global window.
status: Proposed
date: 2026-07-22
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-22T00:00:00Z
sensitivity: public
---

# ADR-0007: Volatility taxonomy and volatility-aware freshness windows

- **Status:** Proposed
- **Date:** 2026-07-22

## Context

`lint` v0 flags an inline `(as of YYYY-MM-DD)` stamp as stale using ONE global
window for every document (`lint.resolve_window`,
`config.DEFAULT_FRESHNESS_WINDOW = "7d"`; `check_stale_stamps` takes a single
`window` for all docs). That single knob is too coarse: knowledge does not
decay at a uniform rate. A `Place` or a `Decision` is a fixed historical fact
whose stamp should rarely if ever nag; a `Procedure` or a `Project` moves fast
and a week-old stamp is a genuine signal; a `Concept` or a `Person` sits in
between. Under one window the user must either accept noise on stable objects
or accept blindness on volatile ones — there is no setting that serves both.

The forces: knowledge volatility differs *by kind of object*, so the natural
place to encode a default is the object-type vocabulary (`model/types.py`'s
`REGISTRY`, already the single source of truth for per-type behaviour); yet a
per-type default alone cannot capture a specific object that is unusually
stable or unusually fast, so a per-object override is also needed. Slice 1 must
add all of this on lint's existing read path WITHOUT breaking its load-bearing
read-only / never-fail / deterministic (injected-clock) contract, without an
LLM, and without changing what `ingest` writes. The existing
`freshness: "snapshot"` flag is an orthogonal skip and stays as-is.

## Decision

We adopt a **fixed three-tier volatility taxonomy** — `static`, `slow`,
`volatile` — as the stable interface for how fast an object's knowledge
decays. Each tier maps to a stale window: `static` is never flagged, `slow`
defaults to `90d`, `volatile` defaults to `7d` (continuity with today's
global default for fast-moving types).

We attach a **per-type default tier** to `model/types.py`'s `ObjectType`
registry (a new `default_tier` attribute on the frozen dataclass):
`static` = {Place, Event, Decision, Source}; `slow` = {Concept, Entity,
Person, Organization}; `volatile` = {Procedure, Project}.

We add an **absent-by-default `volatility:` frontmatter field** — a
per-concept override, separate from `freshness`. `ingest` does NOT emit it
(`build_concept`/`build_source_concept` are unchanged), so ingest output stays
byte-stable and the field appears only where a human deliberately sets it.

Window resolution is a pure, never-raising function with a fixed precedence:
per-concept `volatility` → per-type default → global `freshness_window`
fallback. Every unknown, malformed, or absent input degrades DOWN this chain
(unknown tier value → per-type default; unknown/absent type → global window;
malformed config window → global default with a notice), never raises, and
`static` documents are never flagged. Per-tier windows are configured in
`openkos.yaml` under a new `volatility_windows:` map; the legacy global
`freshness_window` is retained as the ultimate fallback.

## Consequences

Easier: staleness becomes meaningful — a stable `Place` stops nagging while a
week-old `Procedure` is still caught; the taxonomy is three named knobs rather
than ten numeric per-type windows; the change is a pure additive read path
with no data migration, no LLM, and no writes, so it preserves every existing
lint guarantee; the registry stays the single source of truth for per-type
behaviour.

Harder: `volatility` is a new, *sticky* data-model field — once bundles embed
it and the registry encodes tiers, renaming the field or changing the tier set
is a migration, not a free edit (this is exactly why an ADR is warranted); the
fixed three-tier set is a deliberate expressiveness ceiling (no per-object
numeric window in this slice); a fourth concept (`freshness` snapshot,
`volatility`, per-type default, global window) now participates in "how stale
is this doc", which future contributors must keep straight — `freshness`
remains an orthogonal skip, `volatility` alone drives the window.

## Alternatives considered

- **Per-type default only, no per-object override**: rejected — a per-type
  default cannot express the one unusually stable or unusually volatile object
  within a type; the override is cheap (an absent-by-default frontmatter key)
  and removes a real expressiveness gap without touching ingest.
- **Direct numeric per-type windows, no named tiers**: rejected — encoding a
  concrete duration on each of the ten registry types couples policy (how long
  is "fresh") to the vocabulary, multiplies the config surface, and offers no
  shared vocabulary for a user to reason about or for later slices (S2's
  LLM-suggested windows) to target; named tiers are the stable interface,
  concrete durations are configurable behind them.
- **Keep the single global window**: rejected — it is the very coarseness this
  change exists to remove; no single value serves both stable and volatile
  objects.
