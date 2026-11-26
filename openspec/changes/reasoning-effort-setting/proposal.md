# Proposal: reasoning-effort-setting

Refs #1800.

## Intent

With `backend: openai-compatible`, OpenKOS cannot tell a reasoning model how
much to think, so the provider applies its own default effort. A smoke run on
2026-11-25 showed Google's `gemini-3.7-flash` and `gemini-3.8-flash` reaching
the packaged `max_generation_tokens` before answering, and larger hosted models
bill reasoning tokens at their default effort. The three providers'
OpenAI-compatible endpoints take a `reasoning_effort` request field, but the
accepted words differ.

Success: an opt-in `openkos.yaml` key sends the field on every chat request;
unset changes nothing.

## Scope

### In Scope

1. `config.py`: an optional `reasoning_effort`, default unset, a non-empty
   lowercase ASCII word; refused when `backend` is `ollama`.
2. `llm/openai_compatible.py`: send the field on chat requests only; map a 400
   that names it like `temperature` and `seed` (names the key, not retried).
3. `application/backends.py` hands the value to the `openai-compatible` client.
4. `openkos doctor` shows the configured value; template, `docs/cli.md`,
   CHANGELOG.

### Out of Scope

- A fixed set of accepted words, or per-model defaults: providers disagree and
  the list would rot.
- Ollama's native thinking controls.
- The desktop app.

## Approach

Mirror `temperature`/`seed`: a top-level request field with the `is not None`
-> omit rule, and the existing 400 mapping extended with the new key name. The
Ollama refusal sits in `parse_config` beside the other backend checks, so the
mistake surfaces when the config is read.

## Risks

Low: unset sends a byte-identical request, which a test pins. A provider that
rejects a word fails with the provider's own text and the key to change.
