# Pre-registration: the local runtime (#1586) and the sufficiency swap on the floor (#1587)

**Status: registered when this file merges, before any measured run.** A
change after the merge is a new registration, not an edit. Part of #1556.

Two questions share this registration and one measuring session:

1. **#1586.** Would `llama.cpp` (`llama-server`) or MLX (`mlx_lm.server`)
   serve the shipped models faster, in less memory or with cheaper swaps than
   Ollama, without changing what the models output?
2. **#1587.** ADR-0076 put the sufficiency check on `gemma4:26b-a4b`. Its
   adoption bar (#1585) passed on the 48 GB development machine, where both
   chat models stayed resident. What does a question cost when only one chat
   model can be resident, as on the 32 GB floor?

Nothing here changes a default. A runtime that passes every rule below is
proposed in a separate PR with its own ADR, superseding or amending
ADR-0057; that PR is left for the owner and is not merged by the measuring
session. The same holds for a #1587 failure: the superseding ADR is opened
and left for the owner.

## The host, and the 32 GB floor

The host is an Apple M4 Pro with 48 GB of unified memory; Ollama reports
37.4 GiB of it as GPU memory. The product floor is 32 GB, with ADR-0047's
24 GB budget for the models. Every memory number is therefore read against
the 24 GB budget, not against the host. Two effects of the floor cannot be
reproduced on this host and are reported as limits, not measured:

- **The page cache.** On 48 GB, the weight files of both models stay in the
  OS file cache, so every load and swap here reads from memory, not from the
  SSD. On 32 GB they compete with the models themselves. Load and swap times
  here are lower bounds for the floor.
- **The GPU working-set limit** macOS sets on a 32 GB machine is lower than
  this host's. Only the 24 GB budget is emulated (#1587, configuration B).

## Runtimes and weights

Versions: Ollama 0.32.9 (the operator's server: `OLLAMA_FLASH_ATTENTION=1`,
`OLLAMA_KV_CACHE_TYPE=q8_0`, every other setting default); `llama.cpp` 0.7.0
(Homebrew, build 11669, commit `620d2225c`); `mlx-lm` 0.32.2 with `mlx`
0.32.4 (`uv tool install mlx-lm`).

| model | Ollama | llama.cpp | MLX |
| --- | --- | --- | --- |
| `qwen3:8b` | tag `qwen3:8b`, Q4_K_M, blob `sha256:a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f` (5,225,374,496 B) | **the same Ollama blob, byte-identical**, read in place | `mlx-community/Qwen3-8B-4bit` @ `545dc4251c05440727734bcd94334791f6ab0192` (affine 4-bit, group 64; `model.safetensors` `sha256:f2d29621aab300336ad645567ff38c42aac755513006ef4e8a579cf7ef5256d8`, 4,607,835,174 B) |
| `gemma4:26b-a4b` | tag `gemma4:26b-a4b`, Q4_K_M, blob `sha256:a8bdd07a49b895083bb1420bb895ad9ab3e3b8f7d5cc42c7582ca0fa765eede2` (17,074,419,072 B) | **the same Ollama blob**, read in place | `mlx-community/gemma-4-26b-a4b-it-4bit` @ `0d77464eeb233a2da68ebf9d7dc4edaac7db956d` (3 shards, `sha256:683f420e…`, `feab2873…`, `bc607486…`; 15,341,205,776 B) |
| `bge-m3` | tag `bge-m3`, F16 blob `sha256:daec91ff…` | the same blob (identical to `gpustack/bge-m3-GGUF` `bge-m3-FP16.gguf`) | none: `mlx_lm.server` serves no embeddings |

**Why Ollama's own blobs for llama.cpp.** Ollama's model blobs are GGUF
files. Serving the identical bytes isolates the runtime (kernels, chat
template, sampler) from the quantization, which is the question. The
fallback, used only if `llama-server` cannot load a blob, is the
downloaded file closest to Ollama's quantization, recorded in the results:

- `Qwen/Qwen3-8B-GGUF` @ `7c41481f57cb95916b40956ab2f0b139b296d974`,
  `Qwen3-8B-Q4_K_M.gguf`, `sha256:d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`;
- `unsloth/gemma-4-26B-A4B-it-GGUF` @ `c099eb48e663fd284577b04978a94ffccb261841`,
  `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf`, `sha256:f2c28b3dc4776931ac6f879e11f203dec637ea0f14267a86ec8f6165f63f293f`.

MLX has no Q4_K_M; its 4-bit affine build is the closest, and it is a
different quantization, so stage 2 measures quantization and runtime
together for MLX. Weights live outside the repository, in
`~/Dev/Projects/openkos-eval-runs/runtime-screen/models/`.

### Server configuration

Each runtime keeps its defaults except for the settings that make the
comparison like-for-like with the Ollama measurements of #1556:

| setting | Ollama | llama.cpp | MLX |
| --- | --- | --- | --- |
| context | `num_ctx` 12288 per request | `-c 12288` | none to set (the cache grows) |
| slots | `OLLAMA_NUM_PARALLEL` default | `-np 1` (default `-1` = auto would split the context) | default |
| flash attention, KV | on, `q8_0` | `-fa on -ctk q8_0 -ctv q8_0` | `--kv-bits 8`; if the server refuses it for a model, unquantized, recorded |
| thinking | `think: false` | `--reasoning off` | `--chat-template-args '{"enable_thinking": false}'` |
| sampling, `qwen3:8b` | Modelfile: temp 0.6, top-k 20, top-p 0.95, repeat penalty 1 | `--temp 0.6 --top-k 20 --top-p 0.95 --min-p 0 --repeat-penalty 1` | `--temp 0.6 --top-k 20 --top-p 0.95 --min-p 0` |
| sampling, `gemma4:26b-a4b` | Modelfile: temp 1, top-k 64, top-p 0.95 | `--temp 1 --top-k 64 --top-p 0.95 --min-p 0` | `--temp 1 --top-k 64 --top-p 0.95 --min-p 0` |
| bind | the operator's server | `--host 127.0.0.1` | `--host 127.0.0.1` |

The client sends what production sends: `max_tokens` 8192 and the seed
(the openai-compatible backend sends no context and no thinking flag, so
those are server settings). One runtime server runs at a time; Ollama has
no model loaded (`ollama ps` empty) while another runtime is measured.

## Stage 1: performance (`run_runtime_screen.py perf` and `swap`)

Per runtime and model, in this order: Ollama, llama.cpp, MLX; `qwen3:8b`,
then `gemma4:26b-a4b`.

- **Load** (3 times): Ollama: `ollama stop`, then a tiny request at
  `num_ctx` 12288, timed to its reply. Others: from spawning the server to
  the reply of the same tiny request. The median decides; the first is
  reported.
- **Prefill and decode** (3 repetitions, seeds 1 to 3): a system line, then
  9,000 synthetic words (about 10,000 `qwen3:8b` tokens; deterministic,
  salted per repetition so no prompt cache can serve it) and a request to
  summarize, with a 512-token ceiling. Streamed: prefill tokens/s is the
  prompt tokens over the time to the first token, decode tokens/s the
  remaining tokens over the rest. The server's own report (Ollama's
  durations, llama.cpp's `timings`) is recorded as a cross-check. Medians
  decide.
- **Memory**, after the third repetition: the resident size summed over the
  server's process tree by `vmmap -summary` (it counts mapped weight files,
  which the physical footprint does not); the footprint and, for Ollama and
  llama.cpp, the loader's buffer report (ADR-0047's method) are reported.
- **Swap** (`swap`), with one model resident at a time: Ollama on a second
  server (`OLLAMA_MAX_LOADED_MODELS=1`, the same store and settings as the
  operator's, on `127.0.0.1:11500`); llama.cpp as a router with
  `--models-max 1`; MLX by naming the other model in the request. After
  `qwen3:8b` is warm, 3 cycles of: a tiny request to `gemma4:26b-a4b`, then
  one to `qwen3:8b`, each timed to its reply. The median of the 6 decides.
- **Both resident**: the two models' resident sizes from `perf`, summed,
  plus `bge-m3`'s 0.66 GiB, against 24 GB.

### Gate (`runtime_core.stage1_verdict`), on `qwen3:8b`, the default model

A runtime **passes** when it has **no regression** and **at least one
gain** against Ollama:

| axis | gain | regression |
| --- | --- | --- |
| decode tokens/s | >= 1.15x | < 0.90x |
| prefill tokens/s | >= 1.15x | < 0.90x |
| resident memory | <= 0.85x | > 1.10x |
| load time | none | > 2.0x **and** more than 10 s slower |
| swap | median swap <= 0.5x, or both models + `bge-m3` resident within 24 GB where Ollama's are not | none |

`gemma4:26b-a4b`'s rows are reported. A runtime that cannot load a model
records why; one that cannot load `qwen3:8b` fails stage 1.

## Stage 2: output equivalence (only for a runtime that passes stage 1)

Both harnesses are replayed unchanged, through the new
`--backend openai-compatible` path, against the server configured above.
The baselines are the cached Ollama generations from #1560 and #1557 (same
Ollama version and digests, same seeds), so stage 2 spends no Ollama time
on chat. The new rows are labelled `<tag>@<runtime>` and keyed on the
weights digest.

- **#1560, the sufficiency arm**: `qwen3:8b` and `gemma4:26b-a4b`, the
  sufficiency call only, all 96 items, seeds 11 to 15 (the confirmation
  seeds), timeout 60 s (#1560's), compared by `--stage compare` with each
  model's own Ollama rows as the baseline.
  **Equivalent** (`runtime_core.ask_equivalence`) when the pooled paired
  delta's 95% upper bound is not below 0 (no clear loss, X1's form), and
  none of V1 (failures), V2 (latency <= 2x), S2 (adjacent misses <= baseline
  + 0.02) or X1 fires. S1 (grounded refusals not above the baseline) has a
  margin of zero: it was written to select a better model, and an equal
  runtime would trip it on about half of all runs. A rise in grounded
  refusals already lowers the primary delta, so S1 is reported, not gating.
- **#1557, the window-level coverage-lift replay**: `qwen3:8b` (the
  extraction model) on pool DA (the five dev meetings), runs 1 and 2, plus
  the five prose fixtures at run 1, at #1557's `qwen3:8b` timeouts (146 s per
  window, 438 s whole-source), then G1 by `gpt-oss:20b` on Ollama and
  `score --g1` against the cached `qwen3:8b` rows.
  **Equivalent** (`runtime_core.concept_equivalence`) when the window dlift's
  95% upper bound is not below 0 (#1557's round-1 drop rule), and V1, V3, V4
  and V5 hold and G1's lower bound is >= -0.05, all at #1557's registered
  values.

The AMI material stays where #1557 put it; runs read a copy of that runs
directory under `~/Dev/Projects/openkos-eval-runs/runtime-screen/`, and only
numbers enter the repository.

## Verdict per runtime

| outcome | when |
| --- | --- |
| **adopt-candidate** | passes stage 1, and stage 2 is equivalent on both harnesses for every model it measured, including `gemma4:26b-a4b` (the shipped judges, ADR-0047, and the sufficiency check, ADR-0076, run on it) |
| **faster but not equivalent** | passes stage 1, fails stage 2 |
| **no gain** | fails stage 1 |

If both runtimes are adopt-candidates, the one with the larger `qwen3:8b`
decode ratio is proposed. An adopt-candidate is proposed in a separate ADR
PR, left open for the owner; otherwise Ollama stays and nothing changes.

## Operational facts recorded (no rule reads them)

Install path, model download and format, the concurrency setting and its
default (`--parallel` / `--decode-concurrency` / `OLLAMA_NUM_PARALLEL`), how
each server reports a reasoning segment (whether `reasoning_content` or a
`<think>` tag appears with thinking off), and the `bge-m3` embedding path:
`llama-server --embeddings` on the same blob, checked for 1024 dimensions and
cosine agreement with Ollama's vectors on three sentences
(`embed-check`). MLX has no embedding server.

## #1587: the sufficiency swap on the floor

The #1585 protocol, unchanged: on copies of #1585's two workspaces
(`es-built`, arm (a) with `models: {sufficiency: null}`, arm (b) with
`gemma4:26b-a4b`), 3 interleaved sequences per arm, each one cold question
(all three models unloaded) followed by 5 warm, `openkos query` end to end
at `main` after #1585 (`31b987a2`), `ollama ps` recorded after each. The
server is a second Ollama on `127.0.0.1:11500` (same binary, store and
settings as the operator's; the operator's server is not touched and has no
model loaded during the run), in two configurations, run in this order:

- **A, as #1587 states it:** `OLLAMA_MAX_LOADED_MODELS=1`. This also evicts
  `bge-m3` for every chat call, in both arms, which the floor would not do.
- **B, the 24 GB budget:** `OLLAMA_GPU_OVERHEAD` set to the server's
  reported GPU memory minus 24 GiB, `OLLAMA_MAX_LOADED_MODELS` default. The
  scheduler then keeps `qwen3:8b` with `bge-m3` (about 7 GiB) and
  `gemma4:26b-a4b` with `bge-m3` (about 18.4 GiB), but not both chat models
  (about 24.8 GiB). This is the floor's memory, not its page cache.

**Bar** (ADR-0076's, `runtime_core.floor_verdict`): arm (b)'s warm median
per question at most 1.5x arm (a)'s, and arm (b)'s cold first question
(median of 3) at most 30 s. **#1587 passes only if both configurations
pass.** A failed question (non-zero exit) voids the configuration, and it is
reported, not retried. On a failure, a superseding ADR is opened as #1587
names (the `sufficiency` default to `null`, or to `qwen3:14b` if it fits
next to `qwen3:8b`, or a memory-aware default) and left for the owner.

## Order, gates and budget

1. This file merges. Nothing below runs before.
2. No other evaluation or `openkos query` process runs on the machine, and
   `ollama ps` is empty (checked before each step; the session waits).
3. Stage 1 for Ollama, llama.cpp, MLX; `embed-check`; the swaps.
4. #1587, configurations A then B.
5. The forecast below is recomputed from stage 1, then stage 2 runs for the
   runtimes that passed, in the order of their `qwen3:8b` decode ratio.

**Budget: 4.0 h of measured model time** (every step above, wall-clock).

| step | forecast |
| --- | ---: |
| stage 1 (3 runtimes x 2 models, swaps, embed check) | 0.4 h |
| #1587 (2 configurations x 36 questions) | 0.6 h |
| stage 2 per runtime: #1560 arm, 2 models x 5 seeds x 96 items (#1560's confirmation: 2,176 s for both models on Ollama) | 0.6 h |
| stage 2 per runtime: #1557 replay, DA runs 1-2 + prose run 1 (#1557: 2,920 s on Ollama), G1, scoring | 1.0 h |
| **total with one runtime in stage 2 / with two** | **2.6 h / 4.2 h** |

Stage-2 parts are rescaled by each runtime's measured `qwen3:8b` decode
ratio. **Shortening rule**: while the forecast exceeds 4.0 h, take these
steps in order, and record each one taken:

1. The #1557 replay at run 1 only (prose run 1 kept): its part x 0.55.
2. The #1560 arm at seeds 11 to 13: its part x 0.6.
3. Stage 2 only for the runtime with the larger `qwen3:8b` decode ratio; the
   other is reported as "passed stage 1, stage 2 not run".

A runtime whose server fails mid-run (the harnesses abort after 5
consecutive errors) is reported as failed for that harness; it is not
retried with changed settings.

## What this cannot show

- The floor's page cache and GPU limit (above).
- A default Ollama install uses f16 KV and no forced flash attention; the
  comparison is against the operator's server, the one every #1556 number
  was measured on.
- The CIs over 96 items and 37 windows are wide: "no clear loss" is a weak
  test of equivalence, and a small real loss can pass it.
- One host, one OS version, one version of each runtime.
