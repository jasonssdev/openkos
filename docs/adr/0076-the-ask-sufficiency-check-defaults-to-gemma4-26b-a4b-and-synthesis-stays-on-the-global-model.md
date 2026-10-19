---
type: Decision
title: "ADR-0076: The Ask sufficiency check defaults to gemma4:26b-a4b, and synthesis stays on the global model"
description: The packaged per-task default for a new sufficiency task key is gemma4:26b-a4b on the ollama backend, on a pre-registered screen in which it won the check and nothing else; answer and citation stay on model:, and a cold ask now swaps chat models once.
status: Accepted
date: 2026-10-19
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-19T00:00:00Z
sensitivity: public
---

# ADR-0076: The Ask sufficiency check defaults to gemma4:26b-a4b, and synthesis stays on the global model

- **Status:** Accepted
- **Date:** 2026-10-19
- **Issues:** [#1560](https://github.com/jasonssdev/openkos/issues/1560)

## Context

`query` makes two chat calls: a pre-synthesis sufficiency check (one cheap call
that asks the model to quote the sentence that answers the question, or reply
`NONE`; #760) and the synthesis that writes the answer and
its citations. Both ran on the global `model:` (`qwen3:8b`). #1560 asked whether
another open-weight model should take either role, under a pre-registered
protocol (`evals/ask_model_screen/PREREGISTRATION.md`) whose decision D4 requires
adoption in a separate change, with its own ADR and a new task key.

The screen (results in PR #1584) found:

- **Sufficiency check**: `gemma4:26b-a4b` was selected and confirmed. Paired
  delta over `qwen3:8b` of +0.096 [+0.035, +0.163] on 96 items x 5 fresh seeds,
  no veto. Grounded refusals fell from 0.261 to 0.121, adjacent misses were 0
  for both, the median call took 1.88 s against 1.93 s, and there were no
  failures.
- **Answer and citation** (the synthesis call): no winner. Synthesis stays on
  `model:`.
- **Memory**: about 17.76 GiB at load, plus `bge-m3` at about 0.66 GB, inside
  the 24 GB budget of the 32 GB hardware floor.

ADR-0047 already packaged `gemma4:26b-a4b` as the default of the two judge roles
and fixed the memory layout: one chat model resident at a time, Ollama swapping
it by stage. The per-task mechanism (#513, #515) already carries the precedence,
the `ollama`-only gate, the null opt-out and the doctor's task-models check.

## Decision

1. A new task key, `sufficiency`, joins `TASK_MODEL_KEYS`. `DEFAULT_TASK_MODELS`
   packages `gemma4:26b-a4b` for it, through the existing resolver and on the
   `ollama` backend only. An explicit `models:` entry wins on any backend; an
   explicit null follows `model:`.
2. Every Ask surface (`openkos query`, the MCP `query` tool, the Python API
   `ask`) runs the check on `resolve_task_model(cfg, "sufficiency")` and the
   synthesis on `model:`. When both resolve to one tag, or the check is off, one
   client serves both.
3. `answer()` takes an optional `sufficiency_llm`. Omitted, the call is
   byte-identical to before.
4. The confidential local exemption holds only when every chat client that
   receives the context is local, because confidential bodies are sent to the
   check model as well. It fails closed.
5. A check model that is not installed degrades the check instead of failing
   the query: the answer is produced and a stderr notice names the model, its
   install hint and the opt-out. This differs from the judge roles, which fail
   their stage, because the check is a guard in front of an answer the operator
   still wants; an unreachable server stays fatal.
6. `query --json` names the check model as `llm.sufficiency_model`.
7. The sufficiency prompt and its hash are unchanged.

## Consequences

- Fewer false refusals of grounded questions at the same adjacent-miss rate,
  and no added warm latency.
- **On the 32 GB floor only one chat model is resident at a time (ADR-0047), so
  every Ask with the check on now loads two chat models in sequence:
  `gemma4:26b-a4b` for the check, then `qwen3:8b` for the synthesis. A cold ask
  therefore pays a model swap each time.** The screen's latency veto measured
  warm per-call latency only and did not measure this swap. The only evidence is
  indicative: stage-0 first-call times including load were about 19.5 s for the
  `gemma4` check and 9.6 s for `qwen3:8b`, against warm medians of about 1.9 s
  and 4.7 s. That is not a measurement of the swap, and the cost in a real
  session is open.
- The opt-out is one line: `models: {sufficiency: null}` runs the check on the
  synthesis model (no swap), or `sufficiency_check: false` removes the check.
- A fresh install has the judge model as a recommended pull for `query` as well
  as the judge verbs. If it is missing, `query` still answers and says the check
  did not run; `doctor` lists the task and its pull command.
- A workspace whose global `model:` is not `qwen3:8b` also moves its check,
  since a packaged per-task default outranks the global model.
- The chat model must be loadable alongside `bge-m3` on the user's machine; a
  machine that cannot hold a 26B mixture-of-experts model opts out per task.
- `llm.sufficiency_model` is an additive field of the `--json` report
  (`schema_version` stays `1`).

## Adoption bar (stated before measuring)

The screen did not measure the model swap, so this ADR is accepted only if an
end-to-end measurement, run after this section was committed, meets this bar.

- **Setup.** `openkos query` wall time (process start to exit) on a copy of the
  `es-built` workspace from #1560, at this branch's code. Arm (a): the check on
  `qwen3:8b` (`models: {sufficiency: null}`). Arm (b): the check on
  `gemma4:26b-a4b` (`models: {sufficiency: gemma4:26b-a4b}`). Synthesis is on
  `qwen3:8b` in both.
- **Sequence.** Unload the chat models and `bge-m3` (`ollama stop <model>`;
  the server keeps running), ask one cold question, then 5 more in a row: six
  fixed Spanish questions from `evals/query_grounding/`, the same in every
  sequence. n = 3 sequences per arm, arms interleaved (a, b, a, b, a, b).
  `ollama ps` is recorded after each sequence.
- **Bar.** Adopt only if all three hold:
  1. the median per-question time over the 5 warm questions of (b) is at most
     1.5x that of (a), medians pooled over the three sequences;
  2. the cold first question of (b) takes at most 30 s (median of three);
  3. both chat models fit resident inside the 24 GB budget (the sum of their
     `ollama ps` sizes), **or** no swap occurs per question. "No swap" is
     judged from `ollama ps` after each sequence showing both chat models
     resident, on the measuring machine (48 GB); it does not show the 32 GB
     floor, and is reported as such.
- **Otherwise** the change is not merged: the PR is closed with the data, and
  an issue records `qwen3:14b` (tied on sufficiency in round 2, smaller
  footprint) as the alternative to measure.

### Result (2026-10-19, after the bar was committed in e1f8cbfd)

Measured on one machine (48 GB, Apple Silicon, Ollama 0.32.9), n = 3
interleaved sequences per arm, 36 `openkos query` runs, all exit 0.

| | arm (a): check on `qwen3:8b` | arm (b): check on `gemma4:26b-a4b` | bar |
| --- | ---: | ---: | --- |
| warm median per question (15 per arm) | 14.24 s | 12.00 s | (b)/(a) = 0.84, needs <= 1.5: **pass** |
| cold first question (median of 3) | 22.97 s | 22.86 s | needs <= 30 s: **pass** |
| chat models resident after every question | `qwen3:8b` | `qwen3:8b` and `gemma4:26b-a4b` | no swap per question: **pass** |

The bar's third clause passes through its "no swap" branch, not through
the 24 GB fit. The `ollama ps` sum is 7.4 GB, but `ollama ps` under-reports
`gemma4` (985 MB shown); see the memory note above. By the loader's buffer
report the two chat models need about 24.1 GiB together (17.76 + 6.35), which
is over the 24 GB budget. So on this machine both stay resident and nothing
swaps; on the 32 GB floor, where one chat model is resident at a time, each
ask would still swap, and that remains unmeasured. The warm check is faster
on `gemma4:26b-a4b` (a median of about 1.9 s against 2.7 s in the screen),
which is why arm (b) is faster per question. **The bar passed, and this ADR
is accepted.**

## Alternatives considered

- **Keep `qwen3:8b` for the check (status quo).** Rejected: the confirmed gain
  is real and the check shares no state with synthesis that would force one
  model.
- **`qwen3:14b`.** Tied exactly at +0.078 in round 2, lost the registered name
  tie-break, and was 2.6x slower.
- **Move the synthesis to `gemma4:26b-a4b` too.** Rejected: answer and citation
  had no winner, and a swap-free layout is not worth an unmeasured regression.
- **Fail the query when the check model is missing, as the judge roles do.**
  Rejected: an answer the operator can still have should not be lost to a guard
  that is optional by configuration, and the degraded notice already exists
  (#764).
- **Fall back silently to `model:` when the check model is missing.** Rejected:
  the operator would believe the check ran on the model they were told.
- **Apply the default on every backend.** Rejected: the tag is an Ollama tag.
