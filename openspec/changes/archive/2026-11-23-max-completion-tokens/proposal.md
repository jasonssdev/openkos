# Proposal: max-completion-tokens

Refs #1766 (part of #1770).

## Intent

`OpenAICompatibleClient.chat` sends the generation ceiling as `max_tokens`.
Every current OpenAI reasoning model (o3, gpt-5-mini, gpt-5.5, gpt-6-luna)
rejects that field with HTTP 400 ("Use 'max_completion_tokens' instead"), and
the packaged default sets a ceiling, so the first call on such a model fails.
Separately, a `temperature` the server refuses (OpenAI reasoning models,
Anthropic's compatible endpoint) surfaces as a bare 400 that does not tell the
user which `openkos.yaml` key to change.

Success: the ceiling is sent as `max_completion_tokens`, and a server refusal
of `temperature` or `seed` names the config key and says to unset it.

## Verification of the switch (2026-11-23)

The switch is unconditional only if every backend the template names honours
the new field, so each was checked. Requests used a prompt that runs long and
`max_completion_tokens: 5`; the same request with `max_tokens: 5` is the control.

| Backend | Model | `max_completion_tokens: 5` | Control `max_tokens: 5` |
|---|---|---|---|
| llama.cpp `llama-server` 0.7.0 (build 11669) | qwen2.5:3b GGUF | 200, `finish_reason: length`, `completion_tokens: 5` (honoured) | same |
| MLX `mlx_lm.server` | mlx-community/Qwen2.5-0.5B-Instruct-4bit | 200, `finish_reason: length`, `completion_tokens: 5` (honoured) | same |
| Ollama `/v1` | qwen2.5:3b | 200, honoured (comment on #1766) | - |
| vLLM | not installed | supported: `ChatCompletionRequest` declares `max_completion_tokens` (vllm/entrypoints/openai protocol.py, main) and prefers it over `max_tokens`; not run locally | - |
| OpenAI (gpt-4.1-mini, o3, gpt-5-mini, gpt-5.5, gpt-6-luna), Anthropic compatible endpoint | - | 200 on every model (comment on #1766) | 400 on the four reasoning models |

llama-server and mlx_lm.server accept and honour both fields, so the switch
loses nothing locally. vLLM was confirmed from source only.

## Scope

### In Scope

1. `chat()` sends the ceiling as `max_completion_tokens` instead of `max_tokens`.
2. A 400 whose body names `temperature` or `seed` as unsupported or deprecated
   raises an `OpenAICompatibleError` naming that `openkos.yaml` key and saying to
   unset it, keeping the server's text. It is not retried.
3. The CHANGELOG entry.

### Out of Scope

- Model-name detection and per-provider adapters.
- Dropping `temperature`/`seed` automatically on a 400 (a silent change of the
  user's pinned setting; the error tells them instead).
- `OllamaClient`.

## Risks

- A server that predates `max_completion_tokens` and rejects unknown fields
  would now fail where it accepted `max_tokens`. None of the named backends do;
  unknown fields are ignored by llama.cpp and MLX in the checks above.
