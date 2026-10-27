# Pre-registration: llama.cpp vs Ollama on the same weights, with power (#1590)

**Status: registered when this file merges, before any measured call.** A
change after the merge is a new registration, not an edit. Follow-up to
#1586 (`evals/runtime_screen/RESULTS.md`). Part of #1556.

## The question

#1586 found `llama.cpp` faster than Ollama on the same GGUF bytes (prefill
1.21x; decode 1.11x on `qwen3:8b`) and kept Ollama because one guardrail
failed: G1 precision delta -0.010, interval [-0.185, +0.113], against a -0.05
floor, on 36 judged objects. That interval was too wide to say anything. The
weights are byte-identical, so any real difference comes from the engine
(sampler, kernels, KV-cache quantization).

**Does `llama.cpp` serve `qwen3:8b`'s concept extraction equivalently to
Ollama, tested with enough power to be able to say yes?**

This registration decides that and nothing else. It does not change a
default, does not open an ADR, and does not measure end-to-end time (see
"What this does not decide").

## Fixed inputs

| item | value |
| --- | --- |
| Host | Apple M4 Pro, 48 GB (`hw.memsize` 51,539,607,552) |
| Ollama | 0.32.9, the operator's server (flash attention on, `q8_0` KV, other settings default) |
| `qwen3:8b` | `ollama list` ID `500a1f067a9f`; full digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`; Q4_K_M |
| Weights file | `~/.ollama/models/blobs/sha256-a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f`, 5,225,374,496 B. `llama-server` reads these exact bytes; no download |
| llama.cpp | `llama-server` 0.7.0, `version: 0.7.0 (build 11669, commit 620d2225c)`, `/opt/homebrew/bin` (Homebrew). Nothing is installed |
| llama-server flags | #1586's, unchanged: `-c 12288 -np 1 -ngl 999 -fa on -ctk q8_0 -ctv q8_0 --reasoning off -a qwen3:8b --temp 0.6 --top-k 20 --top-p 0.95 --min-p 0 --repeat-penalty 1`, bound to `127.0.0.1:8081` |
| Judges | `gpt-oss:20b` for G1 and `bge-m3` for coverage, on Ollama, exactly as #1557 and #1586 used them |
| Harness | `evals/concept_model_screen/run_concept_screen.py`, unchanged, at the merge commit of this PR (prompts and pipeline unchanged since the stored runs' stamp commit `360a6631`: `git diff 360a6631 origin/main -- src/openkos/extraction src/openkos/llm/prompts` is empty, and the four extraction prompt hashes equal the stored stamp's) |

The `ollama list` digests are read-only facts about the machine today; the
runner refuses to start if `qwen3:8b` no longer shows ID `500a1f067a9f`.

## Design

**Windows.** The 110 production extraction windows of the five development
meetings (pool DA: 41 windows, 37 with gold) and the nine free-pool meetings
with `summlink` (pool F: 69 windows, 56 with gold): **93 windows with gold**.
The thesis evaluation series and their reserves are not touched: the
harness's fail-closed allowlist (`screen_data.ALLOWED_MEETINGS`) refuses any
other meeting, and the three stage-0 calibration meetings and the four
meeting-only IB meetings are not in the design.

**Arms and seeds.** A seed is the run number (the retry of a call adds 1000).

| arm | runs | status |
| --- | --- | --- |
| Ollama `qwen3:8b` | 1, 2, 3 | DA runs 1 and 2 and F runs 1 to 3 are **stored** (cache replay, no call). **New:** DA run 3 (41 window-runs) |
| llama.cpp `qwen3:8b` | 5, 6, 7 | all new (330 window-runs) |

The two arms use **disjoint seeds** on purpose. #1586 gave both engines the
same run numbers; the per-window SD of the llama-minus-Ollama difference
there (0.032 over two-run means) was only 0.64x the SD two independent runs
would give (0.050), so matched seeds probably made the engines look more
alike than independent sampling does. Here no sampling stream is shared.

The llama.cpp records are copied to a second label (`qwen3:8b@llama.cpp-eq`)
as runs 1 to 3, because the harness's `score` pairs models by run number.
Only the label and run field change; seeds and generations do not.

## Metrics, margins, decision rule

### M1, the primary: window coverage lift (TOST)

Per window, the mean lift over the arm's first k runs, llama.cpp minus
Ollama (`dlift`, the quantity #1557 and #1586 use). Pooled mean over the 93
windows; two-stage bootstrap (meetings, then windows within them; 10,000
resamples, seed 42, the harness's method).

- **Margin: +/-0.016.** Ollama's own mean window lift is 0.0319 (#1586, 37
  windows). The lift is the benefit extraction has over a foreign meeting; an
  engine may not move it by more than half of it. This is also above the
  measured noise floor: two Ollama runs of this model differ per window with
  SD 0.0713 (#1586's stored runs 1 and 2, 37 windows; run means 0.0306 and
  0.0331), which at the registered design is a standard error of the pooled
  difference of 0.0052, so a margin of 0.016 is 3.1 standard errors.
- **Equivalent** when the 90% bootstrap interval lies inside (-0.016, +0.016).
  **Different** when it lies wholly outside. Otherwise inconclusive.

### M2, the guardrail: G1 precision (non-inferiority)

Share of final objects `gpt-oss:20b` labels `supported` (partial and
unsupported count against), llama.cpp minus Ollama, pooled over windows and
runs, two-stage bootstrap by meeting then window.

- **Margin: -0.10, one-sided.** Only a loss is a harm. #1586's -0.05 floor
  was written to select between models; for one engine on identical weights
  it cannot be powered with this corpus (see below), and 0.10 is the smallest
  round margin the available objects can test. It is about 3.2 times the
  measured Ollama run-to-run gap (precision 0.944 and 0.913 on runs 1 and 2,
  0.031) and larger than the gap between #1586's two llama.cpp runs (0.842
  and 1.000, on 19 and 17 objects, which shows how little 20 objects say).
  **This is a weaker bar than the one #1586 failed; the owner should decide
  that it is the right bar** (open question 2 in the PR).
- **Non-inferior** when the one-sided 95% lower bound is above -0.10.
  **Inferior** when the one-sided 95% upper bound is below -0.10. Otherwise
  inconclusive. G1 is also reported against -0.08 and -0.05; those are not
  decision bars.

### V1, the veto: window failures

Failed windows (`timeout`, `capped`, `error`, `unparseable`, `not_called`)
over window-runs. A llama.cpp rate more than 0.02 above Ollama's (#1557's
`CONCEPT_FAILURE_MARGIN`) is NOT EQUIVALENT. #1586 saw one llama.cpp runaway
in 82 window-runs.

### Decision

| outcome | when |
| --- | --- |
| **EQUIVALENT** | M1 equivalent, M2 non-inferior, V1 holds, and the full design (k = 3, 330 llama.cpp window-runs) completed |
| **NOT EQUIVALENT** | M1 different, or M2 inferior, or V1 fires (any k) |
| **INCONCLUSIVE** | everything else, including a design that stopped at k = 2 |

A demonstrated difference outranks an incomplete design.

### Reported, never decisive

Median window-call time (llama.cpp / Ollama; #1586: 31.9 s / 33.4 s), objects
per window-run, the Ollama run-to-run SD in pools DA and F, and the
sufficiency arm carried over from #1586 (`qwen3:8b` +0.013 [-0.012, +0.044]
at 95%, so inside +/-0.05 at any level; `gemma4:26b-a4b` identical on all
480 calls). That last item is a reading of existing data under a margin
chosen now, not a measurement of this run.

## Sample size (assumptions labelled)

Normal approximation. Nothing below is a result.

1. Per-window run noise: `SIGMA_RUN = 0.0713 / sqrt(2) = 0.0504`. **Assumes**
   run noise is independent, of equal size in both engines, and that there is
   no engine-by-window interaction.
2. With k runs per arm, one window's difference has SD
   `0.0504 * sqrt(2/k)`: **0.0412 at k = 3**, 0.0504 at k = 2.
3. Design effect for windows nested in meetings: **1.5.** #1586's two-stage
   interval was 1.085x the naive one (variance ratio 1.18); 1.5 is the
   allowance above that. This is an assumption from one data set.
4. TOST at alpha 0.05 with power 0.8 and a true difference of zero needs
   `n = DEFF * (SD * (z_0.95 + z_0.90) / margin)^2`
   `= 1.5 * (0.0412 * 2.9264 / 0.016)^2 = 85` windows at k = 3. **93 are
   available**, giving power 0.843. At k = 2 it needs 128, giving power 0.607,
   which is why k = 2 cannot yield EQUIVALENT.
5. Sensitivity (k = 3, 93 windows): a true difference of 0.004 gives power
   0.73; a design effect of 2.0 gives 0.68. The registered power is therefore
   a best case, not a guarantee, and an INCONCLUSIVE outcome is a possible
   honest result.
6. G1: about 0.62 final objects per window-run (Ollama, DA and F), so about
   200 objects per arm at k = 3. With p = 0.92 and the same design effect the
   standard error of the difference is 0.033 and the power to show
   non-inferiority at -0.10 is 0.914. At -0.05, **547 objects per arm** would
   be needed, so -0.05 cannot be powered here.

`run_equivalence.py plan` prints these numbers from the same constants the
tests pin.

## Feasibility on this machine (checked before registering)

- `llama-server` is installed at 0.7.0, build 11669. Nothing is installed.
- The `qwen3:8b` blob exists with the registered size; `llama-server` loaded
  these same bytes in #1586.
- Memory: one chat model resident at a time. Resident sizes from #1586:
  `qwen3:8b` 8.28 GiB on Ollama, 7.90 GiB on llama.cpp. The runner refuses to
  start unless `ollama ps` is **empty**, refuses at any later phase if Ollama
  holds a model it does not own (it owns `qwen3:8b`, `bge-m3`, `gpt-oss:20b`),
  and unloads only those, with `ollama stop`, before starting `llama-server`.
  The harness's embedding calls load `bge-m3` (1.2 GB) beside `llama-server`,
  as in #1586. No other model is loaded in either engine at any time.
- Disk: 1.1 TiB free; the runs directory is a 51 MB copy plus records.
- The harness refuses to start without the five gitignored
  `examples/extraction-corpus` sources, so the run starts from a checkout that
  has them (the main checkout does).
- On the day of registration the GPU is busy with an unrelated job, so no
  model was called and nothing was started to test this; preflight is what
  checks the rest tonight.

## Forecast and budget

From #1586's measured timings: Ollama 2,920 s over 82 window-runs (35.6 s),
llama.cpp 2,725 s (33.2 s); the judge call is about 6.5 s.

| step | seconds | hours |
| --- | ---: | ---: |
| llama.cpp, 330 window-runs (runs 5 to 7, pools DA and F) | 10,956 | 3.04 |
| Ollama, DA run 3, 41 window-runs | 1,460 | 0.41 |
| G1 judge, 578 window-runs x 0.45 judged windows x 6.5 s | 1,691 | 0.47 |
| coverage scoring, model loads, copies | 900 | 0.25 |
| contingency, +10% | 1,501 | 0.42 |
| **total, k = 3** | **16,508** | **4.59** |
| total, k = 2 (no Ollama DA run 3, no llama.cpp run 7) | 10,175 | 2.83 |

**Budget: 5.0 h of measured model time.**

## Order, stopping rule, shortening

1. This file merges. Nothing below runs before.
2. `setup` (copy of the stored #1557 runs directory; the source is only read),
   then `preflight --hash` must say `ok`.
3. Phases, each recorded in `state.json` and skipped when done:
   `llama-5-6`, `judge-score-k2`, `ollama-da-3`, `llama-7`, `judge-score-k3`.
4. **The one time rule.** `ollama-da-3`, `llama-7` and `judge-score-k3` (the
   third replicate, forecast 1.76 h) start only if the measured time so far
   plus that cost is within 5.0 h. Otherwise the run stops at k = 2 and the
   analysis reports k = 2 (INCONCLUSIVE unless a difference is demonstrated).
5. **No interim look and no early stop** for efficacy or futility. The
   statistics are computed once, by `decide`, after the last phase.
6. **A failure is not retried with changed settings.** A harness abort (5
   consecutive errors), a failed judge or score, or a refusal ends the run;
   `run` can be restarted only to resume the same registered phases. What is
   complete is analysed at its k.
7. Any deviation from this file is listed in the results.

## Exact commands

Run from a checkout of the merge commit that has the extraction-corpus
sources. The first two need no model and are safe while the GPU is busy
(`OLLAMA_HOST` poisoned, as CI does):

```sh
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/runtime_equivalence/run_equivalence.py --self-test
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/runtime_equivalence/run_equivalence.py plan
```

Tonight, once `ollama ps` is empty (the real `OLLAMA_HOST`):

```sh
uv run python -u evals/runtime_equivalence/run_equivalence.py setup
uv run python -u evals/runtime_equivalence/run_equivalence.py preflight --hash
nohup uv run python -u evals/runtime_equivalence/run_equivalence.py run \
  > ~/Dev/Projects/openkos-eval-runs/equivalence-1590-run.log 2>&1 &
# resume after an interruption: the same `run` command
uv run python -u evals/runtime_equivalence/run_equivalence.py decide
```

The runs directory is `~/Dev/Projects/openkos-eval-runs/equivalence-1590/`
(records, judgments, caches, scores, `state.json`, logs, `results-1590.json`).
Only numbers enter the repository, in a `RESULTS-1590.md`.

What `run` executes, all through the existing harness:

```sh
# llama.cpp: llama-server started and stopped by the runner (PID kept), then
run_concept_screen.py --runs-dir D run --models qwen3:8b@llama.cpp --pool {DA,F} --runs 5 6 \
  --backend openai-compatible --base-url http://127.0.0.1:8081 --served-model qwen3:8b \
  --digest sha256:a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f --timeout-from qwen3:8b
run_concept_screen.py --runs-dir D judge --models qwen3:8b qwen3:8b@llama.cpp-eq --pool {DA,F} --runs 1 2
run_concept_screen.py --runs-dir D score --name eq-{DA,F}-k2 --models qwen3:8b qwen3:8b@llama.cpp-eq --pool {DA,F} --runs 1 2 --g1
# third replicate, if the time rule allows
run_concept_screen.py --runs-dir D run --models qwen3:8b --pool DA --runs 3
... run --models qwen3:8b@llama.cpp ... --runs 7, then judge and score with --runs 1 2 3
```

## What this does not decide

- **End-to-end benefit** (#1590 item 2: at least 15% less time to ingest a
  meeting or answer an Ask question, on the 32 GB floor emulation) is a
  separate registration. Median window-call time is reported here as a
  by-product only.
- **Delivery** (#1590 item 3: documenting llama.cpp as a supported opt-in;
  an ADR superseding ADR-0057) happens only if this and the end-to-end test
  both pass, and the ADR is the maintainer's to merge.
- **`gemma4:26b-a4b`.** #1586 already got identical output on all 480
  sufficiency calls; this test covers the extraction model only.
- **MLX**, out of scope as in #1590.
- **The floor's page cache and GPU limit**, as in #1586; one host, one
  version of each runtime; a default Ollama install uses f16 KV and #1586's
  comparison is against the operator's `q8_0` server, as here.
