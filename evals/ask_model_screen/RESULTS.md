# Results: choosing the Ask models (#1560)

**Verdict.**

- **sufficiency: `gemma4:26b-a4b`, selected and CONFIRMED.** On five fresh
  seeds its paired delta over `qwen3:8b` is **+0.096 [+0.035, +0.163]** on 96
  items, with no veto. Under the registration this is proposed for adoption
  in a separate PR with its own ADR and a new task key (D4). This PR adopts
  nothing.
- **answer: no winner, `qwen3:8b` stays.** Every candidate failed V3 (the
  share of answers in Spanish). The cause is one sentence of the answer
  prompt, which three of the candidates quote in English (#1583).
- **citation: no winner, `qwen3:8b` stays.** No candidate's lower bound was
  above 0.
- **Spanish questions over English bundles cost the baseline nothing
  measurable**: its cross-lingual penalty (`es-en` minus `es-es`) is within
  ±0.04 on every role, with intervals that include 0 (sufficiency
  +0.006 [-0.081, +0.094] on the confirmation runs). English questions were
  not measured (#1581).

The run followed [`PREREGISTRATION.md`](PREREGISTRATION.md), merged in #1582
(`a32a429c`) before any scored call. The harness was not changed after the
merge.

## Run

- Date: 2026-10-19, 13:07 to 16:11 EST, on one machine (Apple Silicon, Ollama
  0.32.9, `OLLAMA_NUM_PARALLEL` at the default). No other evaluation process
  used the server.
- Production `_context_holds_the_answer` and synthesis prompts were used,
  unpatched, with `num_ctx` 12288, `num_predict` 8192, `think` false and
  seed = run. Only the model tag varied. Model digests matched the
  registration.
- Raw generations, the contexts, the built workspace, run projections and
  comparison reports are outside the repository, in
  `~/Dev/Projects/openkos-eval-runs/ask-screen/`.

### Ollama time against the 4.0 h budget

| step | wall-clock |
| --- | ---: |
| build `es-built` (ingest) and contexts | 440 s |
| stage 0 (5 models x 5 items) | 364 s |
| round 1 (4 models x 96 items, both calls) | 3,258 s |
| round 2 (3 models, seed 2, both calls) | 2,663 s |
| confirmation (2 models x 5 seeds, sufficiency call) | 2,176 s |
| **total** | **8,901 s = 2.47 h** |

### The shortening rule

| forecast point | inputs | rung taken | forecast total |
| --- | --- | --- | ---: |
| registration (informational) | draft speeds | (2, 5) | 3.6 h |
| after stage 0 (binding) | stage-0 seconds per item; round 2 with `qwen3:14b` and `gemma4:26b-a4b` | **(2, 5)** | 3.79 h |
| after round 1 | round-1 seconds; survivors `gemma4:26b-a4b`, `qwen3:14b` | (2, 5), the floor | 3.13 h |
| after round 2 | the selected model, sufficiency call only | (2, 5), the floor | 2.65 h |

So round 2 ran at n = 2 (seed 2 added to seed 1) and the confirmation at n = 5
(seeds 11 to 15). The rule never had to drop below (2, 5).

## Stage 0

Seed 101, 5 evenly spaced `es-en` items, timeout 600 s, no quality decision.
Seconds are per call; the first call of each model includes its load. The
memory column is the sum of the buffers `llama-server` reported at the
model's load in the Ollama server log (weights, KV, recurrent state, output,
and the largest compute reservation per device).

| model | sufficiency median s | ratio | answer median s | ratio | V2 (<= 2.0x) | timeout | memory at load | + `bge-m3` 0.66 GB (M, <= 24 GB) |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |
| `qwen3:8b` | 2.25 | 1.00 | 4.66 | 1.00 | baseline | 60 s | 6.35 GiB | pass |
| `gemma4:26b-a4b` | 1.91 | 0.85 | 2.43 | 0.52 | pass | 60 s | 17.76 GiB | pass |
| `qwen3:14b` | 4.13 | 1.84 | 5.81 | 1.25 | pass | 60 s | 10.38 GiB | pass |
| `qwen3.6:27b` | 9.55 | 4.25 | 11.83 | 2.54 | **fails both calls** | n/a | 17.64 GiB | pass |
| `qwen3.6:35b-a3b` | 1.47 | 0.65 | 2.77 | 0.59 | pass | 60 s | 21.10 GiB | pass |

`qwen3.6:27b` left every role at stage 0 and entered no round. Each timeout is
`max(60 s, 4 x the stage-0 median answer call)`, which is 60 s for every
model. No call failed or timed out in any stage.

## Round 1 (n = 1, seed 1, 96 items)

Delta = candidate minus `qwen3:8b`, per item, pooled over the three sets, 95%
percentile bootstrap (10,000 resamples, seed 42).

| role | model | delta [95% CI] | `es-es` | `es-en` | `es-built` | vetoes | cut |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| sufficiency (base 0.854) | `gemma4:26b-a4b` | +0.062 [+0.000, +0.125] | +0.031 | +0.062 | +0.094 | - | kept |
| | `qwen3:14b` | +0.073 [+0.021, +0.125] | +0.031 | +0.094 | +0.094 | - | kept |
| | `qwen3.6:35b-a3b` | +0.052 [-0.010, +0.115] | +0.031 | +0.062 | +0.062 | S2 | dropped |
| answer (base 0.833, 36 anchored) | `gemma4:26b-a4b` | +0.000 [-0.111, +0.111] | +0.083 | +0.083 | -0.167 | V3 | dropped |
| | `qwen3:14b` | -0.083 [-0.222, +0.056] | +0.000 | +0.083 | -0.333 | V3 | dropped |
| | `qwen3.6:35b-a3b` | +0.000 [-0.139, +0.139] | +0.000 | +0.083 | -0.083 | V3 | dropped |
| citation (base 0.865) | `gemma4:26b-a4b` | +0.052 [-0.010, +0.125] | +0.125 | +0.031 | +0.000 | - | kept |
| | `qwen3:14b` | +0.031 [-0.042, +0.104] | +0.094 | +0.000 | +0.000 | - | kept |
| | `qwen3.6:35b-a3b` | +0.010 [-0.062, +0.083] | +0.125 | +0.000 | -0.094 | - | halving |

| model | sufficiency median s | answer median s | grounded refused (S1) | adjacent missed (S2) | answers in Spanish (V3) | `USED:` compliance (V4) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `qwen3:8b` | 2.72 | 4.75 | 0.212 | 0.000 | 0.917 | 1.000 |
| `gemma4:26b-a4b` | 1.90 | 2.83 | 0.121 | 0.000 | 0.781 | 0.979 |
| `qwen3:14b` | 4.94 | 6.57 | 0.106 | 0.000 | 0.771 | 1.000 |
| `qwen3.6:35b-a3b` | 2.05 | 3.22 | 0.106 | 0.067 | 0.771 | 0.958 |

`qwen3.6:35b-a3b` left every role: S2 on sufficiency (2 of 30 adjacent
questions judged answerable), V3 on answer, and the halving cut on citation.
`gemma4:26b-a4b` and `qwen3:14b` continued for sufficiency and citation.
Because citation rides the answer call, both ran both calls in round 2.

### Why V3 vetoed every answer candidate

V3 counts the dominant language of every answer, adjacent (unanswerable)
questions included. In round 1, on grounded questions the candidates answer in Spanish as
often as the baseline or more (`gemma4:26b-a4b` 66 of 66, `qwen3.6:35b-a3b`
63, `qwen3:14b` 60, `qwen3:8b` 61). On the 30 adjacent questions they do not
(`qwen3:8b` 27 of 30, `qwen3:14b` 14, `qwen3.6:35b-a3b` 11, `gemma4:26b-a4b`
9). `answer/system.md` gives an English example refusal ("the compiled bundle
does not cover this"). Every non-Spanish adjacent answer from
`gemma4:26b-a4b` (21 of 21) and `qwen3:14b` (16 of 16) contains that sentence
verbatim; `qwen3:8b` paraphrases it in Spanish. The veto is therefore
measuring a prompt defect as much as a model, but it is the registered rule
and it stands. #1583 tracks the prompt.

## Round 2 (n = 2, seeds 1 and 2)

| role | model | delta [95% CI] | `es-es` | `es-en` | `es-built` | vetoes | selection |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| sufficiency (base 0.839) | `gemma4:26b-a4b` | +0.078 [+0.021, +0.141] | +0.062 | +0.078 | +0.094 | - | **selected** |
| | `qwen3:14b` | +0.078 [+0.031, +0.130] | +0.062 | +0.078 | +0.094 | - | tie, second by name |
| answer (base 0.833) | `gemma4:26b-a4b` | +0.000 [-0.083, +0.083] | +0.083 | +0.042 | -0.125 | V3 | - |
| | `qwen3:14b` | -0.097 [-0.222, +0.014] | +0.000 | +0.000 | -0.292 | V3 | - |
| citation (base 0.865) | `gemma4:26b-a4b` | +0.042 [-0.026, +0.115] | +0.109 | +0.016 | +0.000 | - | lower bound <= 0 |
| | `qwen3:14b` | +0.016 [-0.047, +0.078] | +0.047 | +0.016 | -0.016 | - | lower bound <= 0 |

Guardrails: no failures; sufficiency medians 2.68 s (`qwen3:8b`), 1.90 s
(`gemma4:26b-a4b`) and 4.96 s (`qwen3:14b`); grounded refusals 0.235, 0.121
and 0.121; adjacent misses 0 for all three; `USED:` compliance 1.000, 0.974
and 1.000.

The two sufficiency candidates tie exactly (+0.078125, a net 7.5 of 96
item scores). The registered `select` breaks a tie by model name, which picks
`gemma4:26b-a4b`; it is also 2.6x faster than `qwen3:14b` on this call. Answer and citation select nothing, so no model is
selected for answer+citation.

## Confirmation (n = 5, seeds 11 to 15, sufficiency only)

| model | delta [95% CI] | `es-es` | `es-en` | `es-built` | failures | median s | grounded refused | adjacent missed | vetoes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `qwen3:8b` (base 0.821) | | | | | 0 | 1.93 | 0.261 | 0.000 | |
| `gemma4:26b-a4b` | **+0.096 [+0.035, +0.163]** | +0.100 | +0.094 | +0.094 | 0 | 1.88 | 0.121 | 0.000 | - |

`select` names `gemma4:26b-a4b` again, so it is **confirmed**. The gain is the
same on every set and comes from refusing fewer answerable questions (0.121
against 0.261) while missing no unanswerable one. The confirmation report's
"answers in Spanish" and "compliance" columns read 0 because no answer call
was made; they are not measurements.

## Absolute means by set (runs 1 and 2; `qwen3.6:35b-a3b` run 1)

| model | sufficiency es-es / es-en / es-built | answer | citation |
| --- | --- | --- | --- |
| `qwen3:8b` | 0.906 / 0.891 / 0.719 | 0.917 / 0.958 / 0.625 | 0.891 / 0.922 / 0.781 |
| `gemma4:26b-a4b` | 0.969 / 0.969 / 0.812 | 1.000 / 1.000 / 0.500 | 1.000 / 0.938 / 0.781 |
| `qwen3:14b` | 0.969 / 0.969 / 0.812 | 0.917 / 0.958 / 0.333 | 0.938 / 0.938 / 0.766 |
| `qwen3.6:35b-a3b` | 0.969 / 0.969 / 0.781 | 0.917 / 1.000 / 0.583 | 1.000 / 0.938 / 0.688 |

Every model is weaker on `es-built`, the bundle `openkos ingest` compiled:
its extraction kept 14 objects from three transcripts, 10 of them `Person`
objects, and 7 of the 12 anchored questions have no document gold there (see
the registration). That is the same for every model, so the comparison stays
paired.

## Cross-lingual penalty (`es-en` minus `es-es`, paired by question)

| model | sufficiency | answer | citation |
| --- | --- | --- | --- |
| `qwen3:8b` (runs 1-2) | -0.016 [-0.109, +0.062] | +0.042 [+0.000, +0.125] | +0.031 [+0.000, +0.078] |
| `qwen3:8b` (confirmation) | +0.006 [-0.081, +0.094] | | |
| `gemma4:26b-a4b` (runs 1-2) | +0.000 [+0.000, +0.000] | +0.000 [+0.000, +0.000] | -0.062 [-0.141, +0.000] |
| `gemma4:26b-a4b` (confirmation) | +0.000 [+0.000, +0.000] | | |
| `qwen3:14b` (runs 1-2) | +0.000 [-0.047, +0.047] | +0.042 [-0.125, +0.250] | +0.000 [-0.078, +0.062] |

No model has a clear cross-lingual loss, and no X1 veto fired. A Spanish
question over the English mirror is answered, gated and cited as well as
over the Spanish original.

## What this does not show

- The bundles are 14 short synthetic documents, plus one compiled bundle of
  three transcripts. The numbers rank models on this shape of question; they
  are not absolute quality figures.
- The answer metric checks named facts (names, meeting numbers), not prose.
- English questions were not measured (#1581).
- AMI material was not used: the registered design is synthetic, so the
  thesis's dev and free pools were not needed.

## Next

- Adoption: a separate PR proposes `gemma4:26b-a4b` for the sufficiency check
  under a new task key, with its own ADR (status Proposed), as the
  registration requires.
- #1583: make the refusal language-neutral, then re-measure the answer role.
- #1581: the English-question arm.
- Thesis (jasonssdev/mcd-afg#143): on this screen nothing displaces
  `qwen3:8b` as the answering model.
