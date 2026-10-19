# Local runtime screen (#1586, #1587): design note

Is Ollama the right local runtime on Apple Silicon, or would `llama.cpp`
(`llama-server`) or MLX (`mlx_lm.server`) serve the same models faster, in
less memory or with cheaper swaps, without changing what they output? The
rules are in [`PREREGISTRATION.md`](PREREGISTRATION.md); results, once run,
go in `RESULTS.md`.

```
uv run python -u evals/runtime_screen/run_runtime_screen.py --self-test
uv run python -u evals/runtime_screen/run_runtime_screen.py perf --runtime ollama --label qwen3:8b --model-ref qwen3:8b --out <dir>
uv run python -u evals/runtime_screen/run_runtime_screen.py perf --runtime llama.cpp --label qwen3:8b --model-ref qwen3:8b --port 8081 --server-argv '["llama-server", "-m", "<gguf>", "--host", "127.0.0.1", "--port", "8081", ...]' --out <dir>
uv run python -u evals/runtime_screen/run_runtime_screen.py swap --runtime ollama --host 127.0.0.1:11500 --first qwen3:8b --second gemma4:26b-a4b --out <dir>
uv run python -u evals/runtime_screen/run_runtime_screen.py floor --host 127.0.0.1:11500 --workspaces <ws-a> <ws-b> --project <repo> --out <dir>
uv run python -u evals/runtime_screen/run_runtime_screen.py embed-check --port 8083 --out <dir>
```

## Two stages, in order

1. **Performance** (`perf`, `swap`): load time, prefill and decode tokens/s
   at production context (about 10,000 prompt tokens in a 12,288 window),
   resident memory summed over the server's process tree, and the time to
   switch between `qwen3:8b` and `gemma4:26b-a4b` with one resident at a
   time. The gate is `runtime_core.stage1_verdict`.
2. **Output equivalence**, only for a runtime that passes stage 1: the
   existing harnesses, replayed through the production
   `OpenAICompatibleClient` against that runtime's server:
   - `ask_model_screen`: `--stage generate --backend openai-compatible
     --base-url <url> --served-model <what the server loads> --digest <weights>`,
     then `--stage compare` against the cached Ollama rows;
   - `concept_model_screen`: `run --backend openai-compatible ...
     --timeout-from qwen3:8b`, then `judge` and `score --g1`.

   Both keep Ollama as their default; `--model` becomes a label
   (`qwen3:8b@llama.cpp`), and the weights digest keys the cache, since no
   Ollama reports one. The rules are `runtime_core.ask_equivalence` and
   `runtime_core.concept_equivalence`.

`floor` is #1587: #1585's end-to-end `openkos query` protocol against a
second Ollama server that holds one model, or a 24 GB budget, at a time.

## Measurement choices

- **Streaming timings.** Prefill ends at the first token, so the time to
  first token gives prefill tokens/s and the rest gives decode tokens/s. The
  same arithmetic is applied to every runtime; the servers' own reports
  (Ollama's durations, `llama-server`'s `timings`) are kept as a cross-check.
  Each repetition's prompt starts with a different nonce, so no prompt cache
  can serve it.
- **Resident memory** is `vmmap -summary`'s resident total, not the physical
  footprint: a runtime that maps its weight file (Ollama, `llama.cpp`) holds
  it as clean file-backed pages, which the footprint does not count, while
  MLX copies weights into GPU buffers, which it does. The footprint and the
  loader's buffer report (ADR-0047's method, Ollama and `llama.cpp` only)
  are recorded beside it.
- **Same weights where possible.** Ollama's blobs are GGUF files, so
  `llama-server` is pointed at the identical bytes. MLX needs its own 4-bit
  build, so for MLX stage 2 measures quantization and runtime together.

## Limits

- One host (48 GB). The 32 GB floor's page cache and GPU working-set limit
  are not reproduced; see the registration.
- `mlx_lm.server` serves no embeddings: an MLX deployment would still need a
  second runtime for `bge-m3`.
