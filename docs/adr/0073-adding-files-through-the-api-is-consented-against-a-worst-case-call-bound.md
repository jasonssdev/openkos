---
type: Decision
title: "ADR-0073: Adding files through the API is consented against a worst-case call bound"
description: openkos.api adds source files through a free quote and a run bound to it, as judging does; the quote's number is an upper bound computed by extraction itself, the run holds each file to its share by degrading to a Source-only add, and a person's changed file stays refused.
status: Accepted
date: 2026-10-17
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-17T00:00:00Z
sensitivity: public
---

# ADR-0073: Adding files through the API is consented against a worst-case call bound

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

The desktop app must let a person add source files, and `openkos.api` has no ingest. The terminal's `openkos ingest` asks its cost question on a TTY; the folder watch adds files unattended under a call budget. Judging, the other operation that spends model calls for a person, is quoted for free and then run against the quote ([ADR-0059](0059-judging-is-a-quote-bound-application-surface-that-persists-per-chunk-and-never-merges.md)): the quote's `calls` is an upper bound the run never exceeds, enforced by a client that refuses the call past it.

An ingest's cost is not knowable from the document the way a judging chunk's is. The CLI's batch gate prints `estimate_extraction_calls`, which is explicitly the ordinary path: a window's retry, the judge's second attempt, the re-ask and the decision pass's re-ask all depend on what the model replies, and whether a source reads as a meeting depends on the title the Source ends up with, which moves both the chunking threshold and the meeting-only passes. A consent screen that says "about 3 calls" and then spends 8 has not been honest.

A file whose bytes changed after a file of that name was added is refused by a person's ingest (raw/ is immutable); only the unattended watch imports such bytes as a new version ([ADR-0041](0041-a-changed-source-imports-as-a-new-version-and-its-supersession-is-proposed.md)). The watch queues the refusal as a `watch_refusal` row, and the app needs to say what the person can do.

## Decision

- Adding files through the API follows judging's protocol: `quote_ingest` is free (no model call, no write; the extraction client is built only to read its model and locality) and returns a token; `run_ingest` adds only what the token covers, returns `IngestQuoteStale` when the model, the backend, the host's locality, the settings that move the bound or any file's bytes moved, and `RunRefused` without a token.
- The quote's number is an upper bound computed by extraction itself: `extraction.concept.max_extraction_calls` counts every mechanism at its maximum and takes the costlier of the two readings of the source's shape, beside the ordinary estimate so no other module re-derives a threshold. A test drives the real extractors with replies built to spend every retry and pins the bound against them.
- The run holds each file to its quoted share with a counting client whose refused call is a `BackendError`, so the extraction degrades as it does for an unreachable backend (a skipped window, an unjudged union, or a Source-only add with its extraction left as debt) rather than overspending or failing the run. Judging's ceiling raises an assertion because its bound is exact by construction; an ingest's workspace may move between quote and run, and a Source-only add with no spend is the honest outcome there.
- A person's ingest through the app keeps refusing a changed file, reported as `changed_after_import`; the remedy the app offers is a rename (or a new name), never a retry. A `watch_refusal` row resolves when its bytes land, as on the CLI.

## Consequences

**What becomes easier:**

- A client shows a number it can promise: "at most N model calls", with the model and whether its host is this machine, before anything is sent.
- The CLI, the watch and the app add a file through one use case, so raw/ immutability, `origin_key`, the sensitivity floor and the refusal-row resolution cannot drift between them.
- A future extraction mechanism that adds a call without updating the bound fails the bound's test instead of silently overspending a consent.

**What becomes harder, and what we accept:**

- The bound reads larger than what most runs spend (with the default settings a short note is quoted at 6 calls and usually spends 3). The wording says "at most" and "usually fewer".
- The bound depends on extraction internals and must be updated with them; the adversarial test is what keeps it honest.
- If the ceiling ever binds, a file is added with less extraction than it would otherwise get. It stays retryable debt, as any degraded extraction is.
- A changed file cannot be added from the app under its old name. That is the immutability rule the CLI already has.

## Alternatives considered

- **Quote the ordinary estimate, as the CLI's gate does.** Not a bound: the run could spend more than the person agreed to, which is exactly what ADR-0059's protocol exists to prevent.
- **Count against the estimate and stop the run when it is passed.** Stopping mid-extraction loses the calls already paid for that file and leaves nothing written.
- **Raise when the ceiling binds, as judging does.** An ingest's workspace can move between quote and run (a Source forgotten after a quote said "unchanged"); failing the run there is worse than a Source-only add that spends nothing.
- **Let the app import a changed file as a new version, as the watch does.** ADR-0041 kept that for the unattended path because nobody is there to rename; a person is, and a silent version is a consequential change they did not choose.
- **Expose the unattended budget to the app.** The budget is the daemon's (`budget.py` is runner-only by design); a person's consent is the quote.
