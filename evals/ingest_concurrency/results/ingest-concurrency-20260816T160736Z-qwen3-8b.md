# ingest window fan-out concurrency — 4 windows (#739)

_Generated: 20260816T160736Z_ · model `qwen3:8b` · **15 runs per arm** · fixture `medium-10-reunion-plataforma`.

Generation ceiling `8192` · context window `12288` · host `http://127.0.0.1:11435` · **`OLLAMA_NUM_PARALLEL=4`**.

Concurrency 1 is the shipped serial loop, not a one-worker pool.

| concurrency | wall clock (s) | speedup | recall | precision |
| --- | --- | --- | --- | --- |
| 1 (serial) | 156.94 ±17.47 [125.95-185.34] n=15 | 1.00x | 0.42 ±0.11 [0.27-0.55] n=15 | 0.92 ±0.12 [0.71-1.00] n=15 |
| 2 | 124.08 ±20.55 [77.91-156.63] n=15 | 1.26x | 0.47 ±0.11 [0.27-0.55] n=15 | 0.89 ±0.11 [0.75-1.00] n=15 |
| 3 | 124.35 ±13.65 [83.28-139.38] n=15 | 1.26x | 0.43 ±0.13 [0.27-0.64] n=15 | 0.91 ±0.12 [0.75-1.00] n=15 |
| 4 | 124.63 ±17.43 [83.50-147.22] n=15 | 1.26x | 0.43 ±0.11 [0.27-0.55] n=15 | 0.91 ±0.13 [0.71-1.00] n=15 |

**Read the quality columns arm-against-arm, never against the #694 band.** That band (recall 0.80 ±0.12) scores the COMPLETE `extract_concept_union` — union merge, re-ask, participant capture and judge re-admission all recover subjects after the fan-out. This probe stops at the fan-out plus `_dedup_merged`, the only part concurrency touches, so absolute recall sits below that band on every arm including the shipped serial one. The serial arm is the control.

Adoptable only if the speedup lands outside the serial arm's own spread AND neither quality column moves against serial — `evals/title_first/`'s rule, and the one #728 failed.

