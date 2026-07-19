---
type: Decision
title: "ADR-0001: Default extraction model settled by measurement"
description: Adopt qwen3:8b as the default derived-object extraction model, chosen by the model spike.
status: Accepted
date: 2026-07-19
tags:
  - openkos
  - adr
  - extraction
  - model
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-19T23:30:00Z
sensitivity: public
---

# ADR-0001: Default extraction model settled by measurement

- **Status:** Accepted
- **Date:** 2026-07-19

## Context

Derived-object extraction (`openkos ingest`) runs a local Ollama model to classify a source into a bounded list of OKF objects. The MVP-1 roadmap requires this default to be **settled by a measurement, not by argument** — run the same extraction against candidate 7–8B-tier families and see which returns schema-valid, correctly-typed output without over-enumerating, using `examples/good-life-demo/` as the target shape. `config.DEFAULT_MODEL` was provisionally `qwen3:8b`.

Constraints: local-first (models run on the user's machine via Ollama); a permissive license is preferred (AGENTS.md — "prefer boring, durable, permissively-licensed tools"); and the choice stays a config value the user can override either way.

The `evals/model_spike/` harness drives the real `extract_concept` pipeline over the two `good-life-demo` sources — whose correct derived objects are known ground truth — for three installed candidates (3 runs each): `qwen3:8b`, `mistral:7b`, `gemma4:e4b`. It scores schema-valid rate, type accuracy, an anti-enumeration (over-production) penalty, and latency.

Measured result (composite = equal-weight mean of the first three):

| Model | schema_valid | type_acc | anti_enum | avg_lat | composite |
| --- | --- | --- | --- | --- | --- |
| `qwen3:8b` | 1.00 | 0.75 | 0.83 | 6.4s | **0.86** |
| `mistral:7b` | 1.00 | 0.25 | 0.78 | 5.0s | 0.68 |
| `gemma4:e4b` | 1.00 | 0.50 | 0.51 | 7.3s | 0.67 |

## Decision

We adopt **`qwen3:8b`** as the default derived-object extraction model (`config.DEFAULT_MODEL`), settled by the `evals/model_spike/` measurement. It remains a config value; users may override it in `openkos.yaml`.

## Consequences

- The default now rests on a reproducible measurement rather than assertion. `evals/model_spike/report.md` records the run, and the harness can be re-run when new candidate models appear.
- `qwen3:8b` was the most **consistent** (identical output across all three runs) and most **conservative** candidate, aligning with the KOM stance of "prefer fewer, richer objects."
- The anti-enumeration prompt rule is **validated**: `qwen3:8b` does not flood the output with shallow `Person`/`Entity` stubs (anti_enum 0.83). The 7–8B tier's failure mode is under-extraction and wrong-type, not over-enumeration.
- **Known quality gap (accepted, deferred to MVP-2):** the composite scores shape, not quality. On the eyeball, no 7–8B candidate reliably extracts the `Decision` from the meeting fixture, and target concepts are missed (e.g. `Epicureanism`). Improving this — few-shot prompting, or a larger model — is future work, and the harness is the baseline to measure against.
- **License:** `qwen3:8b` and `mistral:7b` are Apache-2.0. `ollama show gemma4:e4b` also reports Apache-2.0, which is anomalous for the Gemma family (usually Google's Gemma Terms of Use); that tag's license should be verified against the vendor's official terms before relying on it.
- Future contributors: re-run `evals/model_spike/` before changing the default, and record a superseding ADR if a different model wins.

## Alternatives considered

- **`mistral:7b`** — Apache-2.0 and fastest, but the weakest quality: severe under-extraction (only `Apatheia` on the Enchiridion notes) and wrong types on the call fixture (composite 0.68). Rejected.
- **`gemma4:e4b`** — highest object count, but it over-produces and hallucinates (fabricated `Event`s, a merged "Stoicism vs Epicureanism" concept, an "Enchiridion" concept), giving the lowest anti-enumeration score (0.51); plus the license anomaly above. Rejected.
- **Keeping the default un-measured** — rejected: the roadmap explicitly requires the default to be settled by measurement.
