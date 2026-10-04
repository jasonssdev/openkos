# Proposal: adopt-gemma4-judge-defaults

## Intent

Refs #1269. The owner's recorded "Adoption decision" on #1269: `gemma4:26b-a4b`
becomes the packaged model for the **contradiction** and **identity
(adjudication)** judge roles; every other role stays on `qwen3:8b`. The
evidence is the pre-registered, n=15 sweep in "Results, round 1" (0 of 150
wrong #1223 field shapes against 44 of 150; identity different-rate 1.00
against a 0.92 bar; `auto_merge` PASS where the baseline FAILs; revisions and
sufficiency vetoes held).

Success: a stock `ollama` workspace runs the two judge tasks on
`gemma4:26b-a4b` with no config; a missing judge fails only the stage that
named it, with that model's pull command; nothing else moves.

## Scope

### In Scope

1. `config.DEFAULT_TASK_MODELS` ships the judge tag for `adjudication` and
   `contradiction`; `edge_typing` stays listed with `None`.
2. `resolve_task_model` applies the packaged rung on the `ollama` backend
   only (a model tag means nothing to another provider).
3. `contradictions` and `adjudicate` name the resolved task model, not
   `cfg.model`, in their not-installed refusal and their partial-batch line,
   and report it as the run's model.
4. Docs, the `openkos.yaml` template and the canonical example, and ADR-0047
   (the adoption, the evidence, layout (a), the system requirement).

### Out of Scope

- Any prompt file or prompt hash (ADR-0043): unchanged.
- Label, Generate and Write roles: no winner this round.
- Pulling a model from `init` or `doctor`: `doctor` stays informational;
  `init` pulls nothing.
- A fallback from a missing judge to `model:`: refused (#515 decision 2).
- Re-measuring memory on other Ollama versions: the figure (about 21.5 GB with
  `bge-m3` at `num_ctx` 12288) was measured once, from the loader's buffer report.
- `suggest-relations`' and `suggest-volatility`'s not-installed wording, which
  still names `cfg.model` for their tasks; no packaged default moves them.

## Approach

The per-task mechanism (#513, #515) already carries the precedence, the null
opt-out, the curate gate's model disclosure, the per-stage availability map,
and the doctor's task-models check. The change fills the packaged map, gates
it on the backend, and fixes the two verbs that named the global model while
contacting the task model.

## Risks

- A workspace with a custom global `model:` also moves its judge roles
  (precedence: packaged per-task default over global). Opt-out: explicit null.
- The judge is about 20 GB in memory; it does not co-reside with `qwen3:8b`
  on the 32 GB floor. Layout (a), one chat model at a time, is the documented
  consequence.
