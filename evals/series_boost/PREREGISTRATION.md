# Pre-registration: boosting the series a question names (#1702)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the
section "The owner confirms" and this file has merged to `main`, before the
first model call.** A change after that point is a new registration, not an
edit to this one. It generates no number itself: it fixes what will be
generated, how it will be read, and what each outcome means. Production code
and defaults are unchanged by this file and by the run. Every bar marked
**(proposed)** is the author's proposal until the owner confirms it. The
decision it measures is [ADR-0085](../../docs/adr/0085-a-question-that-names-a-meeting-series-boosts-that-series-and-does-not-filter-to-it.md).

## Question

When a question names a meeting series, does ranking the documents of that
series ahead of the others (a **soft boost**, nothing removed) reduce answers
mixed across series without making answers worse, slower or emptier?

The owner decided on 2026-10-29 that series scoping is a boost and not a hard
filter, and that it is measured before it is switched on. A `LICENSED` verdict
licenses an OpenSpec change to implement the boost behind a default-off
setting. It does not turn anything on here.

## What this does not touch

- Production code, prompts, defaults. The boost is implemented in the harness
  (`measure_series_boost.py`) as a re-rank of the fused ranking, at the one
  place an OpenSpec change would put it: the list `fusion.fuse` returns, before
  `fusion.select_top` cuts it to the limit. `answer()` is otherwise called as
  shipped.
- `revision_history`, which stays at its default (off); it was REJECTED on its
  own axis (`evals/decision_evolution/RESULTS.md`).
- Model choice: `qwen3:8b` only.

## The signal under test

A series is identified from what retrieval already holds, with no model call:

1. A Source's **series key** is its frontmatter `title` with a trailing date
   removed (ISO `2026-03-10`, `10 de marzo de 2026`, `March 10, 2026`,
   `10 March 2026`) and the separators before it, then case- and
   accent-folded. A key needs at least 2 words.
2. Any other document takes the keys of the Sources in its `provenance`.
3. A question **names** a series when a key occurs in it as whole words (after
   the same folding).
4. Documents whose keys include a named series are moved ahead of the rest,
   each group keeping its order. A question that names no series is untouched.

**Not handled, and expected to be unrecognised:** a paraphrase or translation of
the series name (the English variants of the questions name the series in
English while the Source titles are Spanish, so the signal does not recognise
them); a series whose Sources have no common title prefix; a one-word series;
documents with no `provenance` (neutral concepts are not boosted); two series
named in one question (both are boosted).

## Arms

| Arm | Boost | `context_window` | Role |
|---|---|---|---|
| `off` | none (as shipped) | 24576 | baseline |
| `boost` | named series first | 24576 | candidate |
| `wrong` | the other series first | 24576 | phase 1 only, no chat: a misidentified series |

`context_window` is 24576, the value `openkos init` writes on a machine with
32 GiB or more ([ADR-0084](../../docs/adr/0084-a-larger-context-window-is-sized-at-setup-on-machines-with-32-gib-or-more.md))
and the owner's. It is the only window measured; nothing here says what the boost
does at 12288. Everything else is the packaged default (limit 8, sufficiency
check, query-aware excerpts, generation ceiling 8192). Seeds are the run number,
the same in both arms. Embeddings use `bge-m3`.

## Data

The synthetic bundle and the 20 questions of `evals/decision_evolution`
(committed; nothing private is read): 12 evolution questions and 8 control questions, a Spanish and an English variant of each base question, asked over
the Spanish bundle of two meeting series about one product. Of the 10 base
questions, 5 name a series (e1, e2, e3, c1, c2) and 5 do not (e4, e5, e6, c3,
c4; their subject exists in one series only). The signal is expected to
recognise the 5 Spanish questions that name a series and none of the others.

- **Treated questions:** those whose `boost` prompt differs from the `off`
  prompt, decided in phase 1 from prompts alone, before any chat call. Expected
  5 (the Spanish named ones).
- **Untreated questions:** the other 15, including the five English variants
  that name a series and are not recognised. They are the no-op controls: their
  `boost` prompt must be byte-identical to `off` (bar P1). Because it is
  identical, they are asked once and their `boost` cell is their `off` cell
  (`carry_cells`).

## Metrics

- **foreign blocks:** context blocks in a prompt whose id belongs to the other
  series (`doc_series`); model-free, phase 1.
- **contamination:** the share of answers citing a document of the other series.
- **judged-correct:** the blind judge's verdict `correct`, as in the
  decision-evolution registration, averaged over runs, then languages, then
  base questions.
- **empty answer:** an answer with no cited document (`no_answer` in
  `score_answer`).
- **mean seconds per answer:** wall-clock of one `answer()` call, sufficiency
  check included, failed calls excluded.

## Bars

The constants below are the ones in `measure_series_boost.py::BARS`; the
self-test fails if this file and the code disagree.

```
EXPOSURE_MIN = 3
FOREIGN_HEADROOM_MIN = 2
FOREIGN_DROP_MIN = 0.5
CONTAMINATION_RISE_MAX = 0.05
CONTROL_DROP_MAX = 0.1
LATENCY_MEAN_RATIO_MAX = 1.1
NEW_EMPTY_MAX = 0.0
FAILED_MAX = 0.15
UNJUDGED_MAX = 0.1
KAPPA_MIN = 0.6
```

All (proposed). **Phase 1 (embedder only, no chat):**

- **Exposure.** At least `EXPOSURE_MIN` treated questions. Otherwise
  **NO-EXPOSURE**.
- **Headroom.** At least `FOREIGN_HEADROOM_MIN` treated questions whose `off`
  prompt carries a foreign block. Otherwise **NO-HEADROOM**: there is nothing
  for the boost to remove.
- **P1, nothing else moves.** Every question the signal does not recognise has a
  byte-identical prompt in both arms.
- **P2, a wrong series reorders and never empties.** Under the `wrong` arm no
  recognised question ends with an empty context. This is the property that
  makes it a boost and not a filter.
- **C1, contamination in the prompt.** Across treated questions the foreign
  blocks fall by at least `FOREIGN_DROP_MIN` (a relative fall of one half).
  Failing P1, P2 or C1 ends the run **REJECTED** with no chat call (a futility
  stop: a model cannot be misled by a block it was never shown, and the
  reverse).

**Phase 2 (`qwen3:8b`), treated questions, `boost` against `off`:**

- **C2, answers do not mix more.** Contamination rise at most
  `CONTAMINATION_RISE_MAX`.
- **C3, controls not worse.** On treated control questions, judged-correct
  difference at least `-CONTROL_DROP_MAX`.
- **C4, evolution not worse.** The same on treated evolution questions.
- **C5, latency.** The `boost` mean seconds per answer is at most
  `LATENCY_MEAN_RATIO_MAX` times the `off` mean.
- **C6, no new empty answers.** The share of empty answers under `boost` exceeds
  the `off` share by at most `NEW_EMPTY_MAX`.

**Verdicts.** `INCONCLUSIVE` when an invariant failed (an `off` prompt was
re-ranked, a recognised question was never re-ranked, history was attached),
more than `FAILED_MAX` of the calls failed, more than `UNJUDGED_MAX` of the
answered cells have no verdict, or the second judge's kappa is below
`KAPPA_MIN`; reported, never rescued by more runs. Then `NO-EXPOSURE`,
`NO-HEADROOM`, then `LICENSED` when every bar is met, otherwise `REJECTED`
naming the failed bars.

## n and the stopping rules

- **n = 3 runs per arm** (seeds 1 to 3). `off`: 20 questions x 3 = 60 cells;
  `boost`: treated questions x 3 (expected 15). With 5 treated base questions
  (3 evolution, 2 control) the cluster count is tiny: **no interval is
  computed, and C3 and C4 are point differences against a margin.** A
  `LICENSED` verdict therefore means "no harm detected on a small synthetic
  set", not "no harm".
- Phase 1 first; no chat call is made if it ends the run. No other stopping
  rule. A failed or timed-out call is reported as "n failed of TOTAL" and not
  re-run.

## Judging

As in the decision-evolution registration: **blind Claude Opus judges** over
opaque sheets with the question, the gold in words, the answer and the cited
ids, no arm or run; **one blind Claude Sonnet judge** repeats a seeded sample of
40 cells for agreement only (kappa, gate `KAPPA_MIN`); the first label stands.
The judges share a family with the model that authored the gold, so a shared
misreading is invisible to the agreement figure. A carried cell is judged once,
as the `off` cell it is.

## Cost forecast

Rates from `evals/decision_evolution/RESULTS.md` (one machine, `qwen3:8b`,
window 24576): mean 40.4 s per answer, range observed 15.5 s (median at 12288)
to 55.9 s (p90 at 24576).

| Step | Cells | Low | Typical | High |
|---|---|---|---|---|
| `contexts` (embedder, 60 stub prompts) | 0 chat | 0.03 h | 0.05 h | 0.05 h |
| `off`, 20 q x 3 runs | 60 | 0.26 h | 0.67 h | 1.7 h |
| `boost`, treated q x 3 runs (expected 5 x 3) | 15 | 0.06 h | 0.17 h | 0.42 h |
| two model loads (about 2 min each) | | 0.07 h | 0.07 h | 0.07 h |
| **Total** | 75 | **0.4 h** | **1.0 h** | **2.2 h** |

**Hard cap: 3 GPU hours**, after which the run stops and is read on what exists
(missing cells make it INCONCLUSIVE). Judging uses no GPU. Ollama serializes, so
one job at a time. The GPU is reserved by the owner: ask before the first model
call.

## Exact commands

One Ollama job at a time. Record `git rev-parse HEAD` and `ollama list` first.
`OUT` defaults to `~/Dev/Projects/openkos-eval-runs/series-boost` (outside the
repository).

```sh
M=evals/series_boost/measure_series_boost.py

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# phase 1: embedder only. Read phase1_verdict; if it is not null, stop here.
uv run python $M contexts

# phase 2 (only when phase 1 did not end the run)
uv run python $M run

# judging (no model here): sheets, then the judges fill them, then ingest
uv run python $M judge-sheet
uv run python $M judge-sheet --second-sample 40
uv run python $M judge-ingest --sheet SHEET --key KEY --judge opus --out-file OUT/judgments-first.json
uv run python $M judge-ingest --sheet SHEET2 --key KEY2 --judge sonnet --out-file OUT/judgments-second.json

# model-free from here
OLLAMA_HOST=127.0.0.1:1 uv run python $M score
OLLAMA_HOST=127.0.0.1:1 uv run python $M decide
```

`score` prints every metric and applies no bar; `decide` applies them. The
identity stamp (harness commit, model digest, hash of the prompts sent) is
written into every stored file.

## Outputs and closing rule

`RESULTS.md` in this directory: the commit, model digests, cells run, failed and
unjudged, the phase 1 table, the phase 2 table, the verdict, the cost against
the forecast, and the limits below. Only counts are published.

- **LICENSED:** an OpenSpec change implements the boost behind a default-off
  setting; whether to enable it by default is a separate owner decision with its
  own measurement on real titles. ADR-0085 is accepted when that change merges.
- **REJECTED, NO-HEADROOM, NO-EXPOSURE or INCONCLUSIVE:** the boost stays off,
  nothing ships, and ADR-0085 is superseded by an ADR recording why (or the
  next registration changes the population, not the bars).

## Disclosed before scoring

- No model has answered anything for this registration, and `contexts` has not
  been run, so the author has seen no foreign-block count for any arm.
- The author has read `evals/decision_evolution/RESULTS.md`. It reports, at
  24576, contamination of 0.056 on evolution and 0.000 on controls, and 0.028
  and 0.125 at 12288 (cells citing a document of the other series). Those are
  low, so **the headroom stop may end this run as NO-HEADROOM**; the owner asked
  for the boost to be measured anyway.
- The signal was designed knowing that the bundle's Source titles have the shape
  `Name: date`, which the date-stripping rule handles by construction. Real
  titles come from the first heading or the file name (`source_title.py`) and
  may not; this registration does not measure that.
- The English questions name the series in English and the titles are Spanish;
  the author knew the signal would not recognise them and registered them as
  untreated no-op controls rather than adding a translation rule.
- Bars C3 to C6 are margins for a tiny sample, chosen by the author without
  looking at a result.

## Limits (assumptions, labelled)

- Synthetic, templated Spanish from one product and two series; 5 treated base
  questions. A real library has noisier titles and more than two series.
- The retrieval pool is 10 documents per channel (`POOL_FLOOR`), so the boost
  can only reorder about 20 candidates. A series with more matching documents
  than the limit leaves no room for the others in the top 8; with a small
  library that is the whole point, with a large one it approaches a filter.
- One model, one machine, one window (24576).
- Judges are models of one family.

## The owner confirms

On 2026-10-29, before this file merged and before any result of this run
existed, the owner chose the boost over a filter (the decision this file
measures) and gave a standing instruction to take the recommended option on
open choices; under it every item below is confirmed unchanged, as proposed.
The owner did not review the items one by one; this is recorded so the
confirmation is not read as more than it is. Phase 2 (the model calls) also
waits for the owner to release the GPU. The items:

1. The bars marked **(proposed)**: exposure 3; foreign-block headroom 2 and
   a one-half fall (C1); contamination rise 0.05 (C2); control and evolution
   drop 0.1 (C3, C4); mean latency ratio 1.1 (C5); no new empty answers (C6);
   the INCONCLUSIVE floors (15% failed, 10% unjudged, kappa 0.6).
2. The signal: Source title without its trailing date, matched as whole words in
   the question, inherited through `provenance`; and that paraphrases and
   translations are out of scope.
3. The design: the existing synthetic bundle and questions, the English named
   variants as untreated controls, `off` against `boost` at 24576, n = 3 runs.
4. The phase 1 stops (P1, P2, C1, exposure, headroom) ending the run before any
   chat call.
5. The judging protocol (blind Opus judges, Sonnet kappa sample, first label
   stands).
6. GPU time: forecast 0.4 to 2.2 h, typical 1.0 h, hard cap 3 h, and when the
   GPU is free.
7. The closing rule above.
