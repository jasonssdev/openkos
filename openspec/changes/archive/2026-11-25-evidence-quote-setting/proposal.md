# Proposal: evidence-quote-setting

Refs #1768 (tracking #1770).

## Intent

Concept extraction can carry a verified verbatim `evidence` quote, but nothing
asks the model for it. Two pre-registered A/Bs measured the instruction that
does: it is supported on `anthropic-claude-haiku-5-5` and `openai-gpt-6-luna`
(`evals/concept_model_screen/HOSTED_EVIDENCE_RESULTS.md`) and measured worse on
`qwen3:8b` and `gpt-4.1-mini` (`evals/concept_model_screen/EVIDENCE_RESULTS.md`).
The result splits by model, so it ships as an opt-in `openkos.yaml` key, off by
default.

Success: with `evidence_quote: true` the concept prompt is the registered
treatment, byte for byte; with the key false or absent, nothing changes.

## Scope

### In Scope

1. `config.py`: a boolean `evidence_quote`, default false, validated like
   `transcript_decisions`.
2. `extraction/evidence_quote.py`: the two registered splices, moved here from
   the eval harness; `concept.py` applies them to the concept system prompt
   when asked.
3. `application/ingest.py` threads `cfg.evidence_quote` to both extractors.
4. Docs (`docs/cli.md`, the shipped `openkos.yaml.template`, the hosted
   providers how-to) and CHANGELOG.

### Out of Scope

- Enabling it by default or for `openai-compatible` (that backend also serves
  local models, where it measured worse).
- The re-ask, decision-pass and participant-capture prompts.
- Any change to how `evidence` is parsed or verified.

## Approach

`concept._build_messages` takes `evidence_quote` and, when true, sends
`evidence_quote.treat(_SYSTEM_PROMPT)`; the parameter is threaded through the
single-call and chunked paths of `extract_concept` and `extract_concept_union`.
The splice reads the module's current `_SYSTEM_PROMPT`, and a test pins the
treated prompt to the registered sha256.

## Risks

Low: off is the shipped prompt byte for byte. A shipped-prompt edit changes the
treated hash, which the pinned test then reports as a needed re-registration.
