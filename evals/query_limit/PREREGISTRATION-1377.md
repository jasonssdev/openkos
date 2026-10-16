# Pre-registration: #1377, is `openkos query --limit 5` the right default?

**Status: REGISTERED 2026-10-16, before any model run.** The rules below are
fixed for the registered run. A change after this point is a new registration,
not an edit to this one. The corpus and questions (`limit_corpus.py`) are
committed with this file and are part of what is registered.

## Question

`answer_question(limit=5)` hands the model at most five concepts. Does a larger
(or smaller) `k` improve answers enough to justify changing the default,
without hurting the questions that already work?

## What is measured

The shipped pipeline, unmodified: `retrieval.answer.answer()` with
`sufficiency_check=True` (so false refusals are observable), `limit = k`.
Nothing is patched; `k` is the only variable. Retrieval is `bge-m3`
hybrid (FTS5 + dense, RRF), chat model `qwen3:8b` at the shipped
`DEFAULT_CONTEXT_WINDOW` / `DEFAULT_MAX_GENERATION_TOKENS`.

Corpus: 32 synthetic concepts (`limit_corpus.py`): four rollout families
(Aurora 8 sites, Borealis 7, Cobalt 6, Delta 5), each concept carrying one
unique ticket code, plus six generic concepts. Sibling families are each
other's hard negatives: a question about one retrieves the others.

Questions: 19, in four groups.

| group | n | gold set | role |
| --- | ---: | --- | --- |
| `multi` | 6 | 6 to 8 concepts | the only questions that can fail at `k = 5` by construction |
| `small` | 4 | 2 to 5 concepts | `k = 5` suffices; a larger `k` can only add distraction |
| `single` | 6 | 1 concept | hard negatives: nine to ten near-identical siblings compete |
| `adjacent` | 3 | none | asks for a rollout that does not exist |

## Arms

`k` in {3, 5, 8, 10}. `k = 5` is the baseline. `k = 3` is a CONTROL, not a
candidate: it shows the metrics can move, and it is excluded from adoption.

Runs: **15 per arm and question** (19 questions x 15 runs = 285 queries per
arm, 1140 in total). Retrieval is deterministic and is also recorded per run,
so its stability is reported (`n of TOTAL` identical retrieved lists).

## Metrics

All scoring is deterministic and model-free.

- **Retrieval completeness** (`multi`): questions whose whole gold set is in
  the fused top-`k` (`trace.retrieved`), of 6. Also mean gold recall.
- **Fact recall**: fraction of a question's gold ticket codes present in the
  answer (case-insensitive, hyphen-insensitive). Per-run group score = mean
  over the group's questions; the arm statistic is the mean over the 15 runs,
  and its spread is the min-max of the 15 per-run scores.
- **False refusal**: a grounded question (`multi`, `small`, `single`; 16 x 15
  = 240 checks per arm) whose result has `no_match_cause != "none"`.
- **Adjacent mishandled**: an `adjacent` question (3 x 15 = 45 checks per arm)
  that was not refused AND whose answer contains any ticket code.
- **Cost**: median wall-clock seconds per query; median context characters
  (sum of `trace.context_blocks` text); blocks the context budget omitted or
  excerpted.

## Decision rule

A candidate `k'` in {8, 10} is adopted only if ALL of these hold against
`k = 5`:

- **R** retrieval completeness on `multi` is at least 4 of 6.
- **C** `multi` mean fact recall is at least +0.10 above `k = 5`, AND the gap
  exceeds the larger of the two arms' min-max spreads.
- **N1** `small` and `single` fact recall each no more than 0.05 below
  `k = 5`, and their pooled mean no more than 0.03 below.
- **N2** adjacent-mishandled rate at most `k = 5`'s plus 0.05.
- **N3** false-refusal rate at most `k = 5`'s plus 0.02.
- **B** median latency at most 1.5x `k = 5`'s, median context characters at
  most 2.0x `k = 5`'s, and no block the context budget omits in any grounded
  query.

If more than one `k'` passes, the SMALLEST is adopted. If none passes, `5` is
kept and this file plus the results become the recorded justification.

## What the treatment could fail

- `multi`: 6 questions, 90 checks per arm. At `k = 5` the best possible
  retrieval completeness is 0 of 6, so R and C are exposed to failure only
  through the larger arms.
- `small` + `single`: 10 questions, 150 checks per arm. These are the harm
  detectors; a larger `k` can only help them by accident.
- `adjacent`: 3 questions, 45 checks per arm. A coarse detector. If `k = 5`
  and `k'` both score 0 mishandled, N2 is reported as UNFALSIFIED on this
  corpus, not as proof of safety.

## Stated expectation

R and C are expected to pass for 8 and 10: the gold sets are unreachable at 5
by construction. The open questions are N1 (does an 8B model get distracted by
the sibling families) and B (does ten blocks of context cost more than 1.5x
latency on a serial Ollama). A pass here does NOT generalise to prose-heavy
bundles or other chat models; it licenses a default for the shape measured.

## Limitations, stated up front

One chat model, one embedding model, one synthetic corpus whose question
class (enumeration) is the one most favourable to a larger `k`. The `single`
and `small` groups are the counterweight, not a substitute for a real bundle.
