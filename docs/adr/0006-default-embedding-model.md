---
type: Decision
title: "ADR-0006: Default embedding model -- bge-m3, reliability as the prior filter"
description: Changing the default embedding model from qwen3-embedding:0.6b to bge-m3 because the former crashes the Ollama runner non-deterministically, pinning the 1024-dim contract in exchange.
status: Accepted
date: 2026-07-22
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-22T00:00:00Z
sensitivity: public
---

# ADR-0006: Default embedding model -- bge-m3, reliability as the prior filter

- **Status:** Accepted
- **Date:** 2026-07-22

## Context

The shipped default embedding model, `qwen3-embedding:0.6b`, makes
`openkos reindex` unusable on realistic bundles. Reindex embeds one vector
per whole document, and the model raises non-deterministic EOF crashes in its
Ollama runner (`do embedding request ... EOF`, HTTP 400) with **no stable
token threshold**: the same input size passes one run and crashes another,
and only the very large end (>=~15k tokens) is reliably fatal
(measurements #1522, #1523). Because there is no dependable size budget, no
amount of sub-batching or chunking can make this model reliable -- even
~2000-token inputs flaked. Under the same measurement, `bge-m3` had a **0%
failure rate from 8k to 100k characters**, truncating past its 8192-token
context rather than crashing.

The original reason `qwen3-embedding:0.6b` was chosen as the default was its
**Matryoshka** property -- nested/truncatable output dimensions that let
storage and search stay cheap at scale (see `docs/tech_stack.md`). That
advantage is real but it is a *quality/cost* optimisation, and it is
worthless if the model cannot embed a normal corpus without crashing.

This decision is hard to revert: `EMBED_DIM = 1024` is a three-place
contract -- `llm/base.py:29` (the constant), the vec0 DDL
`embedding float[1024]` in `state/vectorstore.py`, and `_validate_embedding_row`
in `llm/ollama.py`. Any future default that changes the dimension forces a
full corpus re-embed for **every** user via the model-tag gate, a heavy
migration. This ADR therefore both decides a technology and locks a durable
contract, so it passes the ADR gate on both counts.

## Decision

We adopt **`bge-m3`** as the default embedding model
(`DEFAULT_EMBEDDING_MODEL` in `config.py`), replacing
`qwen3-embedding:0.6b`. `bge-m3` is multilingual, permissively considered,
and emits **1024-dim** vectors, satisfying the existing `EMBED_DIM = 1024`
contract with no schema change. Existing stores migrate through the already
shipped model-tag re-embed gate (one forced full re-embed, self-healing);
users must `ollama pull bge-m3`, surfaced by `doctor`.

**Reliability is a prior, hard filter, not a spike input.** The model default
is decided in two ordered stages: first, exclude any candidate that is not
reliable (does not embed a realistic corpus without crashing); second, choose
on retrieval quality **only among the candidates that already passed the
reliability filter**. `qwen3-embedding:0.6b` fails stage one and is therefore
never eligible for the stage-two quality comparison, whatever its Matryoshka
advantage. The quality spike stays alive -- it now ranks reliable candidates,
`bge-m3` included.

## Consequences

Easier: `reindex` and `query` work out of the box on realistic bundles; the
default no longer crashes; `bge-m3`'s 1024-dim output needs no schema change
and no dimension migration.

Harder / given up: switching to `bge-m3` **gives up
`qwen3-embedding:0.6b`'s Matryoshka advantage** -- truncatable/nested
dimensions -- which was the original reason it was the default. `1024` is now
a hard contract across three places, and changing the default dimension later
forces a full corpus re-embed for every user. `bge-m3` at ~1.2 GB is heavier
than the smallest qwen3-embedding footprint, though far lighter than
qwen3-embedding ballooning at large context. Every existing store pays one
one-time forced re-embed on upgrade (expected, self-heals via the tag gate).

Future contributors must keep in mind: reliability gates the default before
quality does. A future model may only become the default after clearing the
reliability filter first, and any candidate that changes `EMBED_DIM` reopens
the vec0 dimension contract, not just a config constant.

## Alternatives considered

- **Keep `qwen3-embedding:0.6b`, fix reindex resilience alone**
  (retry + per-doc isolation, no default change): rejected as *sufficient*.
  Measurement #1523 shows the crash is non-deterministic with inputs
  >=~15k tokens reliably fatal, so resilience only reduces transient loss --
  a large document would be skipped every run and never embed. Resilience
  ships too, but as a complement, not the reliability guarantee.
- **Sub-batch or chunk under a token budget to keep qwen3-embedding**:
  rejected -- there is no stable token budget to design around
  (#1523: even ~2000-token inputs flaked; batch total is not the driver).
- **`qwen3-embedding:4b` (larger qwen)**: rejected for the default -- heavier
  pull and runtime, and not measured reliable; may be a first-class
  alternative, not the shipped default.
- **Treat Matryoshka/quality as decisive**: rejected -- it inverts the
  ordering. A truncatable dimension is worthless on a model that cannot embed
  the corpus without crashing. Reliability is the prior filter; quality
  decides only among already-reliable candidates.
