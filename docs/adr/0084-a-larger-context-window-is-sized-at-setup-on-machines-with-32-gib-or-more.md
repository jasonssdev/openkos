---
type: Decision
title: "ADR-0084: A larger context window is sized at setup on machines with 32 GiB or more"
description: Proposes that openkos init write context_window 24576 on machines with at least 32 GiB of memory, adopted by the owner against the registered latency bar because the synthetic evolution measurement raised judged-correct evolution answers from 0.361 to 0.750 at 2.7x the mean latency.
status: Proposed
date: 2026-10-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-29T00:00:00Z
sensitivity: public
---

# ADR-0084: A larger context window is sized at setup on machines with 32 GiB or more

- **Status:** Proposed
- **Date:** 2026-10-29

## Context

Issue #1703 asked whether `openkos init` should size `context_window` from the machine instead of always writing the default `12288`. At 12288 the history blocks of a retrieved set are still dropped to fit the budget, which hurts questions about how a fact changed over time.

#1702 measured a larger window on a synthetic evolution bundle with `qwen3:8b` (n=3, one Apple Silicon Mac). At 24576: evolution questions judged correct went from 0.361 to 0.750 (+0.389, bootstrap lower bound +0.139); controls went from 0.792 to 0.750; gold evidence delivered at 8 documents went from 18/54 to 33/54 with none regressed. The cost was latency: mean 14.9 s to 40.4 s (2.7x), p90 18.9 s to 55.9 s. The registered bar was 30 s and 2.0x, so the measurement's verdict is REJECTED on latency.

Memory is the other force. The KV cache is linear in the window (`qwen3:8b`: 7.2 GB at 12288 against 10 GB at 32768), so the 16 GB machines the product targets must not change. 32 GiB is ADR-0047's measured floor for the default model set.

## Decision

On machines with at least 32 GiB of total memory, `openkos init` writes `context_window: 24576` into `openkos.yaml`. Below 32 GiB, or when memory cannot be read, it writes `12288`. The owner adopted this on 2026-10-29 as a product decision against the registered latency bar; the measurement itself stays REJECTED, and this ADR records that the adoption overrides it.

The value is written once at setup. Existing workspaces are unchanged, nothing recomputes it at run time, and `DEFAULT_CONTEXT_WINDOW` (what an absent key resolves to) stays `12288`. The floor from `minimum_context_window` and the cap from a known model limit still apply.

## Consequences

- Answers on machines with 32 GiB or more take about 2.7x as long (14.9 s to 40.4 s mean in the measurement).
- The quality gain was shown only on a synthetic bundle with one model; it is not yet shown on real workspaces.
- At 12288 history blocks are still dropped for budget. The Source-demotion half of #1703 (sending derived pages instead of whole Source bodies) remains open and needs its own pre-registered measurement.
- Users can edit `context_window` at any time; the docs state the measured latency cost.
- The tier is one step, not a curve: other window sizes stay unmeasured.

## Alternatives considered

- **Keep 12288 and demote Sources first.** Avoids the latency cost but changes retrieval output and needs a measurement that has not run; it stays open under #1703 and can follow this decision.
- **A user-facing Settings choice.** Lets each person trade quality for speed, but the app shows no context-window setting today and a default still has to be chosen; deferred.
- **Intermediate tiers (16384, and 24576 only from 64 GiB).** Rejected: those values were never measured.
