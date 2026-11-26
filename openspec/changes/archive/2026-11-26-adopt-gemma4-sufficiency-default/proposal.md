# Proposal: adopt-gemma4-sufficiency-default

## Intent

Refs #1560. The Ask model screen (pre-registered in
`evals/ask_model_screen/PREREGISTRATION.md`, results in PR #1584) selected and
confirmed `gemma4:26b-a4b` for the **sufficiency check** of `query` and for no
other Ask role. Paired delta over `qwen3:8b` on 96 items x 5 fresh seeds
(confirmation): +0.096 [+0.035, +0.163], no veto; grounded refusals 0.121 against
0.261, adjacent misses 0 for both, median call 1.88 s against 1.93 s, no
failures. Answer and citation (the synthesis call) had no winner, so synthesis
stays on the global `model:`. The registration requires adoption in a separate
PR, with its own ADR and a NEW task key (decision D4); this is that PR.

Success: a stock `ollama` workspace runs the sufficiency check on
`gemma4:26b-a4b` and synthesis on `model:` with no configuration; a check model
that is not pulled degrades the check, never the query; nothing else moves.

## Scope

### In Scope

1. `sufficiency` joins `config.TASK_MODEL_KEYS`; `DEFAULT_TASK_MODELS` packages
   `DEFAULT_JUDGE_MODEL` for it (resolved on the `ollama` backend only, by the
   existing `resolve_task_model`).
2. `answer()` takes an optional `sufficiency_llm`; the check uses it, synthesis
   always uses `llm`. Omitted, behaviour is byte-identical.
3. Every Ask surface (CLI `query`, MCP `query`, API `ask`) builds the check
   client from `resolve_task_model(cfg, "sufficiency")` when the check is on,
   reusing the synthesis client when both resolve to one tag.
4. The confidential local exemption holds only when every chat client that
   receives the context is local.
5. A check model that is not installed fails the check open (`degraded`), with
   a notice naming the model, its pull command and the opt-out.
6. `query --json`'s `llm` section names `sufficiency_model`.
7. ADR-0076, docs, the `openkos.yaml` template and the canonical example.

### Out of Scope

- The synthesis call (answer and citation): no winner, stays on `model:`.
- Any prompt file or prompt hash (ADR-0043).
- A fallback from a missing check model to `model:`: the check is skipped and
  said so; the model is never silently substituted.
- Measuring the cost of the model swap a cold ask now pays (see Risks).

## Approach

The per-task mechanism (#513, #515, #1269) already carries precedence, the null
opt-out, the doctor's task-models check and the `ollama`-only gate. The change
adds one key and one packaged value, then threads a second chat client through
the one call site (`run_query`) that every surface shares.

## Risks

- On the 32 GB floor only one chat model is resident at a time (ADR-0047), so an
  Ask with the check on loads two chat models in sequence. The screen's latency
  veto measured warm per-call latency only; the swap was not measured. Stage-0
  first-call times including load were about 19.5 s (gemma4 check) and 9.6 s
  (qwen3:8b) against warm medians of about 1.9 s and 4.7 s: indicative only.
  Opt out with `models: {sufficiency: null}` or `sufficiency_check: false`.
- A workspace with a custom global `model:` also moves its check, since a
  packaged per-task default outranks the global model. Same opt-out.
- Rollback: remove the `sufficiency` entry from `DEFAULT_TASK_MODELS`; the
  `sufficiency_llm` parameter and the key stay inert.
