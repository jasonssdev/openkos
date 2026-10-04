---
type: Decision
title: "ADR-0047: The contradiction and identity judges default to gemma4:26b-a4b, and the engine runs one chat model at a time"
description: The packaged per-task default for the contradiction and adjudication roles is gemma4:26b-a4b on the ollama backend, on a pre-registered sweep in which it won both roles; every other role stays on qwen3:8b, a missing judge fails only the stage that named it, and the 32 GB floor runs one chat model resident at a time.
status: Proposed
date: 2026-10-04
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-04T00:00:00Z
sensitivity: public
---

# ADR-0047: The contradiction and identity judges default to gemma4:26b-a4b, and the engine runs one chat model at a time

- **Status:** Proposed
- **Date:** 2026-10-04
- **Issues:** [#1269](https://github.com/jasonssdev/openkos/issues/1269)

## Context

`qwen3:8b` has been the single chat model for every role. #1269 asked whether
any other open-weight model should take a role, under a pre-registered
protocol: n=15 per arm at production settings, per-role bars fixed before any
run, a latency budget, and a 24 GB memory budget that fits the 32 GB hardware
floor. The owner recorded the results and the decision as comments on the
issue ("Pre-registration: numeric bars, latency budget, run plan", "Results,
round 1", "Adoption decision").

What the sweep measured:

- **Label** (`edge_typing`) and **Generate** (`extraction_cap`): no candidate
  cleared its bars (all four Label candidates dropped at the blind-share bar,
  whose exposure defect is recorded on the issue; every Generate candidate
  dropped at the per-fixture recall floor). `qwen3:8b` stays. **Write** was
  not run, since no Generate survivor existed.
- **Judge**: `gemma4:26b-a4b` met every bar for the contradiction and identity
  roles. On the #1223 field shapes it was wrong on 0 of 150 cells against 44 of
  150 for `qwen3:8b`; on identity hard negatives its different-rate was 1.00
  against a 0.92 bar, with every control at 1.00 and transitivity violations at
  0; `auto_merge` passed where the baseline failed; the `decision_revisions`
  B1-B8 and `query_sufficiency` vetoes held. Median latency per run was 84 s
  for adjudication and 131 s for contradictions, within budget.
- **Memory**: `gemma4:26b-a4b` is about 20.4 GB estimated with `bge-m3`
  (`ollama ps` on Ollama 0.32.9 does not count gemma4's weights, so the figure is
  on-disk weights plus reported allocation, not a direct reading). It does not
  co-reside with `qwen3:8b` inside 24 GB.

The per-task mechanism already exists (#513, #515): `DEFAULT_TASK_MODELS`,
`resolve_task_model`, the `models:` override, the curate gate's model
disclosure, and the doctor's task-models check. #650 emptied the packaged map
because a 15.6 GB pull made the out-of-the-box path the broken one, and
recorded that a per-task default must be justified on a fixture. This is the
first default that is.

## Decision

1. `gemma4:26b-a4b` is the packaged default for the `contradiction` and
   `adjudication` tasks, through `DEFAULT_TASK_MODELS`. Every other task,
   `query` included, follows the global `model:` (`qwen3:8b`).
2. **The packaged per-task defaults apply on the `ollama` backend only.** A
   model tag means nothing to another provider, so on `openai-compatible` the
   packaged rung is skipped and the judge roles follow `model:`. An explicit
   `models:` entry is honored on every backend.
3. **A missing judge model fails the stage that named it, never falls back.**
   The refusal names the resolved judge tag and its `ollama pull` hint, as #515
   decided. A silent fallback to `model:` would keep producing verdicts
   attributed to a model the operator never ran. `contradictions` and
   `adjudicate` report the resolved task model, not the global one.
4. **`openkos doctor` recommends, it does not require.** The task-models check
   stays informational: it prints `[FAIL]` with the pull command for a missing
   judge and exits 0, because every other verb works without it. `init` pulls
   nothing.
5. **The operator can decline the default** with an explicit null
   (`models: {contradiction: null}`), which follows `model:`, or name another
   model.
6. **Memory layout (a): one chat model resident at a time.** On the 32 GB floor
   the engine does not hold `qwen3:8b` and the judge together. Ollama swaps the
   chat model between stages, so the cost of a mixed run is a model load at each
   switch, not a second resident model. This is a documented system requirement:
   the judge roles need a machine that can hold a 26B mixture-of-experts model
   (about 20 GB) plus `bge-m3`, and a machine that cannot opts out per task.
7. Prompt files and their content hashes (ADR-0043) are unchanged; the adoption
   moves which model answers, not what it is asked.

## Consequences

- Contradiction and identity judgement improve on the measured bars, including
  the #1223 field-shape errors that no prompt change fixed.
- A fresh install now has one more recommended pull (the judge model, a
  multi-gigabyte download) before `curate`'s Identity and Contradictions stages
  and the `contradictions` and `adjudicate` verbs work. Those stages fail
  individually with the pull hint; ingest, query and the other stages are
  unaffected. The README, `docs/cli.md` and the template say so.
- A workspace whose global `model:` is not `qwen3:8b` also moves its two judge
  roles, since a packaged per-task default outranks the global model by the
  existing precedence. The opt-out is one explicit null per task.
- Mixed runs pay model-load time at each switch between the judge and
  `qwen3:8b`. The measured latency budget covered per-run medians with the model
  already loaded; the swap time was recorded separately on the issue and is not
  part of those medians.
- The gemma4 memory figure is an estimate. It should be confirmed with a direct
  measurement on the target machine; this ADR does not claim it was.
- The self-preference hypothesis (a judge from the same family as the
  generator) was not measured and remains open.
- Label and Generate had no winner; the next round re-registers the blind-share
  metric as a conditional rate before it is used again.

## Alternatives considered

- **Make `gemma4:26b-a4b` the global `model:`.** Rejected: Generate had no
  winner, and a global switch would move extraction off the model
  `evals/extraction_cap/` tuned on.
- **`gemma4:12b`.** It also met every judge bar, with a smaller footprint but
  190 s and 305 s median runs against 84 s and 131 s. The owner adopted
  `gemma4:26b-a4b`.
- **Fall back to `model:` when the judge is not pulled.** Rejected for the
  silent-misattribution reason above.
- **Make `doctor` fail (exit 1) on a missing judge.** Rejected: a missing
  per-task model fails only its own stage, and a non-zero exit would be a false
  alarm for a workspace that is fine for every other verb.
- **Apply the default on every backend.** Rejected: the tag is an Ollama tag.
- **Keep the judge co-resident with `qwen3:8b`.** Rejected: it exceeds the 24 GB
  budget on the 32 GB floor.
