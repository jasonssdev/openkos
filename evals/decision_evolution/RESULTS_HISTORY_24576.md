# Results: `revision_history` at the 24576 window (#1702)

Registration: `PREREGISTRATION_HISTORY_24576.md` in this directory, confirmed by the
owner and merged in PR #1730 (commit `ee1268ce`) before any model call. Harness commit
stamped in every stored file: `ee1268ce`, clean tree. Embedder `bge-m3`
(`790764642607`). Prompt hashes: `answer/system` `e0ba5833e90054fa`,
`answer/sufficiency` `3e2b44fcf3e8477f`. No chat model was called: the registered
futility stop fired in phase 1, which runs the embedder only. Stored files
(`contexts.json`, `guardrail.json`) are in
`~/Dev/Projects/openkos-eval-runs/decision-evolution-h24/` (outside the repository).

## Verdict

| Axis | Verdict | Basis |
|---|---|---|
| History at 24576 (#1702) | **REJECTED on B1** (registered futility stop; neither arm was run) | `contexts`: `h24_on` delivers both gold values on 11 of 12 evolution questions, as many as `h24_off` (gain 0, minimum `FUTILITY_GAIN_MIN = 2`) |

`revision_history` stays default off.

## Phase 1, `contexts --axis history24576`

| Quantity | Evolution (n = 12) | Controls (n = 8) |
|---|---|---|
| Both gold values in the prompt, `h24_off` | 11 | 7 |
| Both gold values in the prompt, `h24_on` | 11 | 7 |
| History block delivered in the `h24_on` prompt | 12 | 8 |
| Blocks omitted for budget, `h24_on` / `h24_off` | 0 / 0 | 0 / 0 |

The two clauses of the stop read:

- Delivery: 12 of 12 evolution prompts carry a history block, above
  `HISTORY_DELIVERY_MIN = 5`. This clause did **not** fire. At 24576, unlike 12288
  (0 delivered of 67 omitted), the mechanism reaches the prompt.
- Gain: `h24_on` shows both gold values on 11 questions and `h24_off` on 11, a gain of
  0 against a minimum of 2. This clause **fired**.

The larger window already puts both meetings' positions in front of the model on 11
of 12 evolution questions without any history block, so the block adds no position
the model was not already shown. The earlier window gain (0.361 to 0.750, `RESULTS.md`)
came from retrieval reaching both meetings; the remaining evolution errors at 24576
are in the answer, not in what the prompt contains, and `revision_history` cannot
reach them.

## Guardrail

`guardrail --axis history24576`: 56 of 56 gold prompts identical between the arms
(G1 holds).

## Deviations

- The `decide` command prints the reason "futility stop: too few evolution prompts
  deliver history" for either clause. The clause that fired here is the gain clause,
  not delivery; the table above is the reading. The harness was not changed.

## Not measured

Answer quality and latency with history at 24576 (B0 to B6): the registration ends the
axis at the futility stop, so the arms were not run.
