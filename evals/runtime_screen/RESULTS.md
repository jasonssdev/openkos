# Results: the local runtime (#1586) and the sufficiency swap on the floor (#1587)

**Verdict.**

- **#1586: Ollama stays the default. No runtime is proposed.**
  - **`llama.cpp`: faster but not equivalent.** It passed stage 1 on
    prefill: 1.21x Ollama's on `qwen3:8b`, with no regression. On the #1560
    sufficiency arm it is equivalent on both models. `gemma4:26b-a4b` scored
    exactly what Ollama scored on all 96 items, because it ran the same GGUF
    bytes. On the #1557 replay, the window lift is unchanged (dlift
    +0.0000 [-0.0116, +0.0107]) and V1, V3, V4 and V5 hold. It fails one
    bar, G1: the precision delta is -0.010, and its lower bound is -0.185,
    against a -0.05 floor. That interval is as wide as the one that failed
    `qwen3.6:27b` in #1557 (-0.174), on 36 judged units. The failure reflects
    the test's power as much as the runtime, but it is the registered rule,
    and it stands.
  - **MLX: faster but not equivalent.** It passed stage 1 on prefill (1.18x)
    and on co-residence: both chat models plus `bge-m3` fit in 23.56 GiB.
    `qwen3:8b` is equivalent on both harnesses. `gemma4:26b-a4b` (a different
    4-bit build, with unquantized KV) showed a clear loss on the sufficiency
    arm: -0.019 [-0.044, -0.002], from more grounded refusals (0.148 against
    0.121). MLX also serves no embeddings.
- **#1587: ADR-0076's bar passes with one chat model resident.** The
  warm-median ratio is 0.96 and the cold first question takes 23.88 s, where
  the bar allows 1.5 and 30 s. The 24 GB-budget emulation (configuration B)
  did not hold, because Ollama kept all three models resident. B is
  therefore void, not a pass. No superseding ADR is opened.

The run followed [`PREREGISTRATION.md`](PREREGISTRATION.md), which merged in
#1588 (`30ff7774`) before any measured call. The harness was not changed
after the merge. Deviations are listed under "Deviations".

## Run

- Date: 2026-10-19, 18:43 to 22:29 EST. The host is an Apple M4 Pro with
  48 GB; Ollama reports 37.4 GiB of GPU memory.
- Versions:
  - Ollama 0.32.9: the operator's server, with flash attention and `q8_0`
    KV;
  - `llama.cpp` 0.7.0 (build 11669);
  - `mlx-lm` 0.32.2 with `mlx` 0.32.4.
- No other evaluation or `openkos query` process ran during the session,
  and `ollama ps` was empty before each step. Only one runtime server ran
  at a time. Every server was bound to 127.0.0.1 and stopped by the PID
  this session started.
- Weights match the registration:
  - Ollama's blobs were read in place by `llama-server`.
  - The MLX builds were downloaded, and their sha256 was verified against
    the Hub.
  - The fallback GGUFs were downloaded and verified but never needed,
    because both Ollama blobs loaded in `llama-server`.
- Raw data, server logs, the replay caches and the weights (39 GB) are
  outside the repository, in `~/Dev/Projects/openkos-eval-runs/runtime-screen/`.
  Only numbers are recorded here.

### Time against the 4.0 h budget

| step | wall-clock |
| --- | ---: |
| stage 1 (perf: 3 runtimes x 2 models; MLX `gemma4` re-run; embed check; swaps) | 893 s |
| #1587 (configurations A and B) | 1,360 s |
| stage 2, MLX, #1560 arm | 2,346 s |
| stage 2, `llama.cpp`, #1560 arm | 1,970 s |
| stage 2, `llama.cpp`, #1557 replay | 3,307 s |
| stage 2, MLX, #1557 replay | 2,998 s |
| G1 judge and scoring (`gpt-oss:20b`, `bge-m3` on Ollama; twice) | 489 s |
| **total** | **13,363 s = 3.71 h** |

The forecast after stage 1 was 3.73 h, so no shortening step was taken.
The binding decision point was the one the registration names.

## Stage 1: performance

Medians over 3 repetitions. Prefill and decode are client-side streaming
rates on a 9,000-word prompt (9,652 to 10,023 tokens) with a 512-token
reply, at a 12,288-token context. The servers' own reports agree within 4%
(Ollama 300.1 / 31.23 and 625.0 / 32.29; `llama.cpp` 359.7 / 34.48 and
681.3 / 44.41). Resident memory is `vmmap`'s resident total over the
server's process tree. Load time runs from an unload (Ollama) or a process
spawn (the others) to the first reply, with the weights in the OS file
cache. The first load is listed separately.

| runtime | model | load s (median; first) | prefill tok/s | decode tok/s | resident GiB | footprint GiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Ollama | `qwen3:8b` | 1.07; 3.85 | 298.5 | 31.17 | 8.28 | 2.74 |
| `llama.cpp` | `qwen3:8b` | 0.92; 3.06 | 359.8 | 34.48 | 7.90 | 2.70 |
| MLX | `qwen3:8b` | 1.50; 4.59 | 351.8 | 34.79 | 7.40 | 7.00 |
| Ollama | `gemma4:26b-a4b` | 9.81; 9.81 | 613.7 | 32.22 | 20.06 | 3.07 |
| `llama.cpp` | `gemma4:26b-a4b` | 1.71; 4.88 | 679.4 | 44.41 | 17.80 | 1.70 |
| MLX | `gemma4:26b-a4b` (unquantized KV) | 3.84; 4.20 | 739.1 | 41.17 | 15.50 | 15.10 |

Ollama's resident total includes its `ollama serve` process, 0.36 to 0.38
GiB. Its `qwen3:8b` runner alone is 7.90 GiB, the same as `llama.cpp`
serving the identical blob. Its `gemma4:26b-a4b` runner is 19.7 GiB against
`llama.cpp`'s 17.8 GiB on the same model blob. Ollama also loads the
model's vision projector (1.19 GB) and its draft model (0.46 GB), which
`llama-server` was not given. The physical footprint leaves out mapped
weights, which is why the resident total decides. The loader report
(ADR-0047's method) is 6.35 GiB for Ollama's `qwen3:8b`, matching #1560.
It is not available elsewhere: Ollama's `gemma4` engine and `llama-server`
at default verbosity do not log a complete report.

### Swap, with one chat model resident at a time

Time from a request to the other model to its first reply (tiny request;
3 cycles of `qwen3:8b` -> `gemma4:26b-a4b` -> `qwen3:8b`).

| runtime | to `gemma4:26b-a4b`, s | to `qwen3:8b`, s | median of 6 | both chat models + `bge-m3` |
| --- | --- | --- | ---: | ---: |
| Ollama (second server, `OLLAMA_MAX_LOADED_MODELS=1`) | 10.66, 3.34, 3.35 | 1.39, 1.12, 1.12 | 2.36 | 29.01 GiB |
| `llama.cpp` (router, `--models-max 1`) | 3.81, 2.36, 2.37 | 1.19, 1.18, 1.15 | 1.77 | 26.36 GiB |
| MLX (model named per request) | 5.18, 5.70, 5.79 | 1.45, 1.51, 1.46 | 3.34 | **23.56 GiB** |

`ollama ps` after each switch showed only the requested model.

### Gate (on `qwen3:8b`)

| runtime | decode | prefill | memory | load | swap / co-residence | verdict |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `llama.cpp` | 1.11x | **1.21x** | 0.95x | 0.86x | 0.75x; 26.36 GiB (over 24) | **pass** (prefill) |
| MLX | 1.12x | **1.18x** | 0.89x | 1.40x (+0.4 s) | 1.42x; **23.56 GiB** (within 24) | **pass** (prefill, co-residence) |

Reported, not gating, for `gemma4:26b-a4b`: `llama.cpp` decodes 1.38x
faster and prefills 1.11x faster. MLX decodes 1.28x faster, prefills 1.20x
faster and uses 0.77x the memory, but only with KV quantization off (see
"Deviations").

## Stage 2: output equivalence

### #1560 sufficiency arm (96 items, seeds 11 to 15)

Delta = runtime minus Ollama on the same model and seeds, per item, pooled
over the three sets, 95% bootstrap (10,000 resamples, seed 42).

| model | runtime | delta [95% CI] | `es-es` | `es-en` | `es-built` | grounded refused (S1, reported) | adjacent missed (S2) | failures (V1) | median s (V2) | equivalent |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `qwen3:8b` (base 0.821) | Ollama | | | | | 0.261 | 0.000 | 0 | 1.93 | |
| | `llama.cpp` | +0.013 [-0.012, +0.044] | +0.006 | +0.031 | +0.000 | 0.242 | 0.000 | 0 | 1.81 | **yes** |
| | MLX | +0.033 [+0.004, +0.071] | +0.069 | +0.031 | +0.000 | 0.212 | 0.000 | 0 | 2.44 | **yes** |
| `gemma4:26b-a4b` (base 0.917) | Ollama | | | | | 0.121 | 0.000 | 0 | 1.88 | |
| | `llama.cpp` | +0.000 [+0.000, +0.000] | +0.000 | +0.000 | +0.000 | 0.121 | 0.000 | 0 | 1.55 | **yes** |
| | MLX | **-0.019 [-0.044, -0.002]** | -0.031 | -0.006 | -0.019 | 0.148 (S1 fires) | 0.000 | 0 | 1.23 | **no: clear loss** |

`gemma4:26b-a4b` on `llama.cpp` produced the same verdict as Ollama on
every one of the 480 calls. Both runtimes ran the same GGUF bytes at the
same sampling settings. `qwen3:8b` on the same bytes did not match call for
call (its delta is not zero), but showed no loss.

### #1557 window-level replay (`qwen3:8b`, pool DA runs 1-2, prose run 1)

Window dlift = runtime minus Ollama, 37 windows with gold in 5 meetings,
two-stage bootstrap (10,000 resamples, seed 42). The bars are #1557's.

| | Ollama | `llama.cpp` | MLX |
| --- | ---: | ---: | ---: |
| window lift (mean) | 0.0319 | 0.0319 | 0.0272 |
| **window dlift [95% CI]** (upper bound >= 0) | | +0.0000 [-0.0116, +0.0107] | -0.0047 [-0.0297, +0.0211] |
| meeting dlift | | +0.0050 [-0.0030, +0.0121] | +0.0045 [-0.0179, +0.0224] |
| V1 failures (<= base + 0.02, <= 0.05) | 2 of 82 | 2 of 82 | 2 of 82 |
| V3 wrong-language titles, es fixtures (<= base + 0.02) | 0 of 16 | 0 of 21 | 0 of 14 |
| V4 objects per meeting-run ([2.5, 7.5]) | 5.0 | 4.9 | 6.1 |
| V5 verbatim (<= base + 0.10) | 0 of 50 | 0 of 49 | 0 of 61 |
| **G1** precision (supported / partial / unsupported) | 38 / 1 / 2 of 41 (0.927) | 33 / 3 / 0 of 36 (0.917) | 36 / 0 / 0 of 36 (1.000) |
| **G1 dprecision [95% CI]** (lower bound >= -0.05) | | -0.010 **[-0.185**, +0.113] | +0.073 [+0.000, +0.192] |
| prose subject recall, exact | 26 of 33 | 25 of 33 | 19 of 33 |
| whole-source prose failures | 0 | 1 | 0 |
| median s per window call | 33.4 | 31.9 | 29.9 |
| **equivalent** | | **no (G1)** | **yes** |

`llama.cpp`'s one whole-source failure (`medium-09`, run 1) was a runaway.
The server's 12,288-token context filled at 7,842 generated tokens after a
4,446-token prompt, and the client reported `finish_reason: length`. The
same call has no failure in Ollama's cached run.

## #1587: the sufficiency swap on the floor

#1585's protocol: 3 interleaved sequences per arm, each one cold question
then 5 warm, `openkos query` at `31b987a2`. Arm (a) runs the check on
`qwen3:8b`; arm (b) runs it on `gemma4:26b-a4b`. The server was a second
Ollama on `127.0.0.1:11500`.

| configuration | arm | cold s (3 sequences) | cold median | warm median (15) | warm ratio b/a | bar |
| --- | --- | --- | ---: | ---: | ---: | --- |
| A: `OLLAMA_MAX_LOADED_MODELS=1` | (a) | 26.13, 22.79, 22.96 | 22.96 | 19.34 | | |
| | (b) | 37.05, 23.61, 23.88 | **23.88** | 18.58 | **0.96** | **pass** |
| B: `OLLAMA_GPU_OVERHEAD` = 37.4 - 24 GiB | (a) | 23.28, 23.62, 23.35 | 23.35 | 16.16 | | |
| | (b) | 29.28, 25.99, 24.76 | 25.99 | 12.65 | 0.78 | **void** |

- **A enforced the condition.** After every question of both arms, only
  `qwen3:8b` was resident. Every query reloaded `bge-m3` and the chat
  models, so arm (b) paid one more load per question than arm (a) and still
  had the lower warm median.
- **B did not enforce it.** After 17 of 18 arm-(b) questions, all three
  models were resident. Ollama's estimate for `gemma4:26b-a4b` is too small
  (`ollama ps` lists it at 985 MB, as #1557 also saw), so the overhead never
  forced an eviction. B is therefore #1585's co-resident measurement again,
  not the floor's.
- **What the floor would add**, as an estimate and not a measurement: with
  `bge-m3` resident, the floor's arm (a) pays no load per warm question,
  and its arm (b) pays two swaps. From the swap table, that is about 3.3 s
  (to `gemma4`) plus 1.1 s (back to `qwen3:8b`). Against #1585's warm
  medians (14.24 s and 12.00 s), that gives about 16.4 s / 14.24 s = 1.15.
  This is still under 1.5, but it assumes the floor's page cache keeps both
  weight files, which a 32 GB machine may not.

By the registration's letter, #1587 passes only if both configurations
pass. A passed; B could not be measured as registered. No failure was
observed, so no superseding ADR is opened. ADR-0076 stands with the caveat
above.

## Operational facts

| | Ollama | `llama.cpp` | MLX |
| --- | --- | --- | --- |
| install | app or `brew install ollama` | `brew install llama.cpp` (20 MB) | `uv tool install mlx-lm` (Python 3.12 environment) |
| model format and download | its registry; blobs are GGUF | GGUF from the Hub, or Ollama's blobs read in place | safetensors MLX builds from the Hub (`mlx-community`); no Q4_K_M equivalent |
| several models | scheduler, `OLLAMA_MAX_LOADED_MODELS` | router mode (`--models-preset`, `--models-max`), one child process per model | one model per server; a request naming another model swaps it |
| concurrency default | `OLLAMA_NUM_PARALLEL` 1 | `-np -1` (auto slots that split `-c`; set `-np 1` for one 12,288 context) | `--decode-concurrency` / `--prompt-concurrency` |
| thinking off | `think: false` per request | `--reasoning off` at server start | `--chat-template-args '{"enable_thinking": false}'` at server start |
| reasoning when on | `message.thinking` | `reasoning_content` (`--reasoning-format`) | not measured |
| with thinking off | none | no `reasoning_content`, no `<think>` in 6 of 6 replies | no `<think>` in 6 of 6 replies |
| `bge-m3` embeddings | native | `llama-server --embeddings` on Ollama's blob: 1024 dimensions, cosine to Ollama's vectors 0.9999998 to 0.9999999 on 3 sentences | **none**: a second runtime would be needed |
| KV quantization | `OLLAMA_KV_CACHE_TYPE` | `-ctk/-ctv` | `--kv-bits`; **not implemented for `gemma4`** (rotating cache) |

## Deviations

- **MLX `gemma4:26b-a4b` ran with unquantized KV.** `mlx_lm` raised
  `NotImplementedError: RotatingKVCache Quantization NYI` on every bench
  request. The registered fallback (unquantized, recorded) was applied, and
  that model's perf was re-run (96 s). Its loads in the refused run were
  valid, but the whole step was repeated.
- **The MLX #1557 replay ran last, not second.** Its first attempt exited at
  once, before any model call, because the worktree lacked the git-ignored
  `examples/extraction-corpus` prose sources. They were copied in from the
  main checkout before `llama.cpp`'s replay, and the MLX replay ran after it.
- **#1587 configuration B** could not apply its condition (above).
- The loader report could not be read for `llama.cpp` or Ollama's `gemma4`
  (above). The resident total, the registered decider, is complete.

## What this does not show

- The 32 GB floor's page cache and GPU working-set limit. Load and swap
  times here are lower bounds.
- A default Ollama install, which uses f16 KV; every comparison here is
  against the operator's `q8_0` server.
- "No clear loss" over 96 items and 37 windows is a weak test: a small real
  loss can pass it.
- One host, one OS version, one version of each runtime.

## Next

- No ADR. The registered rule names no adopt-candidate, so ADR-0057 stands.
- What a later decision could use from this run:
  - `llama.cpp` serves Ollama's own GGUF files, prefills about 1.2x faster,
    decodes `gemma4:26b-a4b` 1.38x faster and swaps faster. It matched
    Ollama call for call on `gemma4:26b-a4b`. Its only failed bar, G1, has
    a 0.30-wide interval. ADR-0057 names the embedded `llama.cpp` option;
    this run measures that option's performance, not its ownership costs.
    A better-powered G1 (more windows, or the free pool) would be the test
    that could change this verdict.
  - MLX would need its own embedding runtime, and its `gemma4` build loses
    sufficiency quality.
