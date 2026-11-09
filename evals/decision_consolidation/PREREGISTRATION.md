# Pre-registration: consolidating the decision pass's output per meeting, and measuring `MAX_PASS_DECISIONS` (#1737)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the section
"The owner confirms" and this file has merged to `main`, before the first model call.**
A change after that point is a new registration, not an edit to this one. It generates no
number itself: it fixes what will be generated, how it will be read, and what each outcome
means. Production code, prompts and defaults are unchanged by this file and by the run.
Every bar marked **(proposed)** is the author's proposal until the owner confirms it.

It reuses the decision-pass harness of `evals/transcript_decisions/` (windows, matching
rule, gold loading, the development-series allowlist, the shared-evidence recall credit)
and its registered conventions: `PREREGISTRATION-1348.md`, `-1364.md`, `-1364-IS1004.md`.
The treatment is not in production; it lives in `measure_decision_consolidation.py` only,
as a step applied to the pass's output, the way `evals/source_demotion/` wraps the one
function it measures.

## Question

The transcript decision pass over-generates. An external, pre-registered evaluation of
0.7.0 on 11 AMI series scored its Decisions at precision 0.149 and recall 0.308 against a
human reference (#1737). Three questions follow, and they are separate:

1. Does **merging** the items that state one decision in different words, instead of the
   exact-subject dedup alone, produce a smaller set that still recalls the gold decisions?
2. Does **ranking** the survivors (so the cap keeps the strongest, not the earliest) help?
3. Where should the per-source cap sit? `MAX_PASS_DECISIONS = 25` is marked UNMEASURED in
   `decision_pass.py`; this measures it.

A `SUPPORTED` verdict licenses a separate production change with its own OpenSpec change
and ADR, behind an off-by-default key. It changes nothing here.

### What the shipped code does today (verified against the files, so the premise is right)

- `run_decision_pass` (`extraction/decision_pass.py`) asks each window independently,
  admits the items whose evidence is quoted verbatim, then runs `dedup_by_subject` (first
  occurrence wins per normalized subject) and cuts with `[:MAX_PASS_DECISIONS]`. Both steps
  run once **per source**, so the cap is per meeting, not per series.
- The cut is **positional**: it keeps the first 25 in window order, so on a meeting that
  exceeds the cap the Decisions of the later windows are the ones dropped, whatever their
  quality. That is a property of the cap today, independent of any treatment.
- The gold is sparse per meeting: at most 14 `decision` rows in any one meeting of the three
  development series (TS3009c, counting its five split children; the next densest has 11),
  against a cap of 25. The stored volumes are 60.0 (ES2015),
  83.8 (TS3009) and 44.0 (IS1004) Decisions per 4-meeting run (`RESULTS-1361.md`,
  `RESULTS-1364.md`, `RESULTS-1364-IS1004.md`): roughly 15, 21 and 11 per meeting, so the
  cap is likely to bind on some TS3009 meetings and few others. If that holds, the volume
  problem is mostly **not** the cap, and only the merge step can reduce it. This is stated
  as an expectation, not a finding.
- `transcript_decisions` is **off by default and NOT ADOPTED**: bar R failed on all three
  development series (`RESULTS-1361.md`, `-1364.md`, `-1364-IS1004.md`). This registration
  does not ask whether to adopt the pass. It asks whether, **inside the opt-in pass**,
  consolidation removes the over-generation without costing the recall the pass has.

The external figures (0.149 / 0.308) come from a different, held-out series set under the
thesis's own metric (one-to-one bge-m3 alignment at 0.70). This repository cannot read
those series (below), so it does not reproduce those numbers; it measures the same
behaviour on the series it may read, and reports the thesis metric beside its own as a
secondary reading.

## What this does not touch

- Production code, prompts, defaults. The pass runs as shipped, including the one re-ask of
  a failed window (`retry_after_cap=True`, as `PREREGISTRATION-1364-IS1004.md`), with the
  shipped prompt (hash `6d3962b0dd75650c`), the generation ceiling 8192 and the context
  window 12288. Only the cap is lifted, once, to capture the raw set (below).
- **Windowing.** Overlap or a rolling summary of earlier decisions is #1738, a separate
  question. The windows here are the shipped `_chunk_lines` windows in every arm.
- The exact-subject dedup: it still runs first, inside `run_decision_pass`, in every arm.
  The merge acts on what survives it. (The issue says "instead of"; running the merge on
  top of the exact dedup is the same set in the limit, differs only in which of two
  same-subject items is the representative, and keeps the control byte-identical to
  production.)
- The matching rule, the gold files and the bars R/P of the earlier registrations.
- Model choice: `qwen3:8b` only, for the pass and for the guard.

## Design: one raw capture, every arm cut from it

For each meeting and run the pass is asked **once**, with `MAX_PASS_DECISIONS` lifted to
100000 for that call (`raw_cap`, restored on exit; the self-test pins it). The result is the
**raw set**: the shipped pass's output with its cap removed, in window order. Every arm below
is a deterministic function of the raw set and the consolidation, so the arms are **paired**
on the same model replies and differ only by the treatment. The `shipped` arm is the first
25 raw items, which the self-test pins equal to what `run_decision_pass` returns with the
cap in place.

### The consolidation (the treatment), exactly

Applied to the raw set of one meeting, in window order:

1. Embed `"<subject>: <value>"` of every item with `bge-m3` (the text the thesis metric
   embeds), unit-normalised.
2. Keep a list of clusters; the first member is the **representative**, always the earliest
   item. For each item, find the nearest existing representative by cosine.
3. If that cosine is at least `MERGE_COSINE = 0.75` **(proposed)**, ask the **guard** once:
   a chat call to the same model with the fixed prompt `decision_consolidation/same_decision`
   (hash `015e92802c6e3cd5`) showing both items' subject, value and evidence line, answering
   `{"same": true|false}`. The prompt says to answer true only for the same thing decided
   and the same choice, and false for a different thing, a changed or refined or reversed
   choice, or doubt.
4. Only an explicit `{"same": true}` absorbs the item into that cluster. `false`, an
   unparseable reply, or a backend error leaves it a new cluster (**fail closed**). The
   guard is asked at most once per item, never against absorbed members, so comparisons do
   not chain and the extra calls are bounded by the number of raw items.
5. The merged set is the representatives in window order (`merged_uncapped`). A merged
   Decision keeps the representative's title, body and evidence line; an absorbed item's
   evidence line is **not** carried, so a merge that absorbs the item that matched a gold
   row, while the representative did not, costs recall. That is deliberate: it is what the
   production body (one quote) would do, and it is what bars K and M measure.

Ranking (applied to clusters): most members first, then the earliest. There is no language
cue and no lexicon: the ranking reads no evidence text, because a language-specific pattern
cannot ship in a multilingual engine and a cap measured under it would not transfer.

## Arms

All derived from the same raw capture, per meeting-run.

| Arm | Merge | Order | Cap | Role |
|---|---|---|---|---|
| `shipped` | exact-subject dedup only | window order | 25 | control (production) |
| `rank_only` | none | ranked (all supports are 1, so this is window order) | 25 | decomposition, no bar |
| `merge_only` | consolidation | window order | 25 | decomposition, no bar |
| `consolidate` | consolidation | ranked | 25 | **candidate** (every bar) |
| `raw_uncapped` | none | window order | none | reference for bar M |
| `merged_uncapped` | consolidation | window order | none | reference for bar M |
| `ranked@K`, `positional@K` | consolidation | ranked / window order | K in 8, 10, 12, 15, 20, 25, 40, none | the cap sweep |

Only `consolidate` against `shipped` decides the verdict. The decomposition arms and the cap
sweep are reported and bind nothing except the cap recommendation below. Eight cap values and
two orders are a family of descriptive curves, not a family of tests.

## Data

**Development series only**, via the fail-closed allowlist `scoring.DEV_SERIES` (there is no
flag to lift it). The gold files are pinned by SHA-256, verified before the first call and
refused on mismatch; the data stays in the thesis checkout and nothing from it is copied here:

| Series | Gold file | SHA-256 | `decision` rows | Gold |
|---|---|---|---|---|
| ES2015 | `ES2015.decisions.adjudicated.csv` | `de3e6439e7e8fe79b623d1d58f72a009fb691ebe0ee96d01f1c105e18debad9c` | 27 | human |
| TS3009 | `TS3009.decisions.adjudicated.csv` | `40aa8312480462bcedddaa31a5f379d893ccf8235e3ed7dcf845c666cb46921a` | 26 | human |
| IS1004 | `IS1004.decisions.adjudicated.csv` | `1b84ae083f1547fc6f356663fc80df9aa2f2aa978196406e50ae2e5e9e3cae7d` | 27 | **model** (Claude Opus; see `PREREGISTRATION-1364-IS1004.md`) |

Rendered transcripts `<meeting>.md` of the same checkout, 12 meetings (a to d of each
series). The gate `_is_meeting_shaped` must be true for all of them or the run refuses (a
false gate would measure a false zero). The unrecallable `decision` rows (empty evidence, or
every act under the 4-token floor) stay in every denominator as in the earlier registrations;
the harness reports them per series.

### Development versus held-out, stated plainly

- **Held-out.** The thesis keeps eleven other series for its final evaluation (its ADR-0005).
  This harness refuses them by construction and never reads them, here or at any step. The
  external 0.149 / 0.308 came from that set.
- **Tuning.** Nothing of this treatment has been run on any series. The merge gate, the guard
  prompt, the cap grid and every bar are fixed in this file before any output of
  the treatment exists, and a different value is a new registration. There is no gate sweep:
  sweeping the gate on the three development series would be tuning on them.
- **What the author already knows, disclosed.** The three series were used to build and judge
  the pass itself (its prompt was revised on ES2015, #1358), and their pass volumes are in the
  stored RESULTS. The bars below were set knowing those volumes. The treatment, as opposed to
  the pass, is new to this data.
- **Consequence.** A `SUPPORTED` verdict is **development-series** evidence. It does not
  establish the effect on a series the pass was never tuned near. Any default would need a
  confirmation on the thesis's held-out set, run by its owner under that project's protocol,
  not by this repository (owner's item 8).
- **Synthetic fixture.** The model-free `--self-test` uses a small synthetic transcript, gold
  and scripted replies written for it. It reads no thesis data.

## Repetitions

**n = 15 runs**, each covering all 12 meetings (the floor `PREREGISTRATION-1364.md` set on
this harness; fewer is not a result and the harness refuses `--runs` below 15). Sampling is
the server's default; seeds are not controlled (the earlier registrations did not either).
Runs are interleaved across series, so a slow drift of the server hits every series alike.
A run counts only if it completes; an incomplete results file is refused by `--report` and
the arm is re-run, never averaged. One Ollama job at a time.

## Metrics

All per series per run (summed over the series' four meetings), then mean and min-max over
the 15 runs, always `n of TOTAL`. Credit and buckets are the earlier registrations' (README
of `transcript_decisions/`): the longest-common-run matching rule (4 tokens, 60% of the
shorter side), the shared-evidence credit, `compuesta` and `descartar` rows outside the
denominator.

- **recalled** = gold `decision` rows credited (the shared-evidence credit); **recall** =
  recalled / `decision` rows.
- **strict precision** = recalled / Decisions produced. A Decision that restates an already
  credited row, matches no row, or matches only a non-decision / `compuesta` / `descartar`
  row counts against it. This is the issue's "precision with unannotated Decisions counted"
  and is the primary precision here. It is not the earlier registrations' bar P, which
  excludes `unannotated` by design (the gold is incomplete); that one is kept as a guard.
- **precision-on-annotated** = `matched_decision` / (`matched_decision` +
  `matched_non_decision`), unchanged.
- Decisions produced; guard calls and guard failures; windows; pass calls and wall time;
  consolidation wall time (embedding plus guard calls).
- **Secondary, no bar:** the thesis's metric at tau 0.70 (`thesis_metric.py`: bge-m3,
  one-to-one minimum-cost assignment within each meeting) for `shipped` and `consolidate`,
  from `--report --thesis-metric`. It uses the embedding model only.

## Bars

Evaluated **per series**, on the paired runs, for `consolidate` against `shipped`. Values
marked **(proposed)** are the author's picks; those with an earlier analogue say so.

| Bar | Rule |
|---|---|
| **Q** strict precision **(proposed)** | mean strict precision of `consolidate` >= 1.5 x that of `shipped`, AND `consolidate` >= `shipped` in at least 13 of the 15 paired runs. Undefined (shipped recalls nothing) is `vacuous`. |
| **K** recall kept (proposed) | mean recalled of `consolidate` >= mean recalled of `shipped` - 1.0 gold row. One row is 0.037 of recall on a 27-row series, the noise unit of the earlier registrations. |
| **M** merging does not lose gold (proposed) | mean of (recalled of `raw_uncapped` - recalled of `merged_uncapped`) <= 0.5 row per run. Isolates the merge from the cap and the ranking. |
| **V** volume | `consolidate` Decisions per run <= 2.0 x the series' `decision` rows, mean and every run (**proposed**; #1348's V was 3.0 x, the stored volumes are 2.2 x, 3.2 x, 1.6 x). |
| **P** guard, as before | precision-on-annotated of `consolidate`: mean >= 0.60 and no run below 0.50 (#1348's P, unchanged). The number of `no_decision` / `sin_soporte` rows it could have failed is reported; `vacuous` if no run has an annotated Decision. |
| **F** guard-call failures (proposed) | at most 5% of guard calls fail (an error or an unparseable reply), pooled in the series. `vacuous` if the guard was never asked. Uncaught errors abort the run. The pass's own `failure` rate is identical in every arm (same raw capture) and is reported, not gated. |
| **C** cost (proposed) | mean guard calls per meeting-run <= mean windows per meeting-run: the step may at most double the model calls the pass itself makes. Calls and seconds are reported for both terms regardless. |

**M's exposure, and why it can make the result UNDETERMINED.** A merge can only be caught by
the gold if it absorbs an item that matched a `decision` row. M therefore reports its
exposure: of the absorbed items, how many matched a `decision` row. With fewer than **10**
such items pooled over the series' runs, M cannot be falsified (a pass would be vacuous: the treatment was never handed anything the gold could mark wrong) and is `vacuous`. Likewise Q is `vacuous` when `shipped` recalls
nothing.

### Verdict (fixed)

Over the three series, no bar pooled and no averaged verdict:

- **INVALID**: fewer than 15 runs, a required series missing, or a meeting-run missing. The
  numbers are shown, labelled, and are not the result.
- **NOT SUPPORTED**: any bar `fail` on any series, **including IS1004**. If ES2015 and TS3009
  (human gold) pass and IS1004 (model gold) fails, the result is recorded as "series
  disagree" and is still NOT SUPPORTED, as `PREREGISTRATION-1364-IS1004.md` decided for the
  same situation.
- **UNDETERMINED**: no bar failed but Q or M is `vacuous` on some series. The merge was never
  exposed to the gold; this is not a pass.
- **SUPPORTED**: every bar is `pass` (P, F may be `vacuous`) on all three series.

`SUPPORTED` does not flip any default and does not touch `transcript_decisions` (still NOT
ADOPTED). It licenses one thing: an OpenSpec change and ADR for the consolidation step inside
the opt-in pass, off by default, with the confirmation of item 8 before anything else.

### The cap recommendation (question 3), also fixed

For each order (`ranked`, `positional`) and each series, the mean recalled at every cap of the
grid and with no cap. The recommended `MAX_PASS_DECISIONS` is the **smallest grid cap whose
mean recalled is within 0.5 gold row of no cap in every series**; if none of the finite caps
qualifies, "none in the grid". It is a recommendation: lowering or raising the constant is a
separate production change, and the constant stays 25 until then. It is read on the
consolidated set (`ranked@K`, `positional@K`); the unconsolidated cap curve is the `shipped`
arm at 25 against `raw_uncapped`, which the same report shows.

## Expectations, stated before the run

- Honest prior: **Q is likely to fail.** At equal recall it asks for at least a third of the
  Decisions to disappear (1.5 x). Restatement across windows is only one source of
  over-generation; the rest is decisions that are real but not in a sparse gold, and
  non-decisions, which a merge does not touch. If the guard merges only a few percent of
  items, the result is NOT SUPPORTED on Q, and that is a clean answer.
- The cap is unlikely to matter on ES2015 and IS1004 (about 15 and 11 Decisions per meeting
  against 25). On TS3009 it should bind on some meetings; there the order (`ranked` against
  `positional`) is the only place the ranking can show.
- `qwen3:8b` as its own guard may agree too readily or refuse too readily; F bounds failures,
  not judgement. Judgement is bounded only through the gold (K, M), which is why M's exposure
  is reported.
- IS1004's gold is a model's. A result there is agreement with that model, as before.

## Cost forecast (Ollama)

Inputs from stored `qwen3:8b` runs: the pass costs 33.6, 39.2 and 28.2 calls per 4-meeting run
on ES2015, TS3009 and IS1004 (101.0 together) and 168, 633 and 132 s (933 s together, TS3009a
including its runaway window). A guard call is a short prompt and a one-line reply with
`think: false`; assumed 2 to 6 s.

| Stage | Calls | Forecast |
|---|---|---|
| Pass, 15 runs x 12 meetings | about 1,515 chat | 15 x 933 s = **3.9 h** |
| Embedding, one batch per meeting-run | 180 embed | negligible |
| Guard, at most one per raw item | at most about 3,500 chat; plausibly 1,000 to 2,000 | **0.6 to 3.5 h** |
| **Total** | about 2,500 to 5,000 chat, 180 embed | **about 4.5 to 7.4 h**, ceiling **9 h** |

The raw set is uncapped, so TS3009 raw sets may exceed the 25 per meeting the stored runs were
cut at; the guard bound follows the raw count. The GPU is reserved by the owner (item 9).
If the first run alone (all 12 meetings) projects past the ceiling at 15 runs, the run stops
and reports short rather than extending the budget; that is an INVALID result, not a smaller n.

## Exact commands

One Ollama job at a time. Record `git rev-parse HEAD` and `ollama list` first; the harness
code is the registering PR's merge commit with a clean tree. `G` is the directory holding the
three gold files, `T` the rendered transcripts, `OUT` a directory outside the repository.

```sh
M=evals/decision_consolidation/measure_decision_consolidation.py

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# the run: pass + embedder + guard, one job, results rewritten after every run
uv run python -u $M \
  --gold $G/ES2015.decisions.adjudicated.csv \
  --gold $G/TS3009.decisions.adjudicated.csv \
  --gold $G/IS1004.decisions.adjudicated.csv \
  --transcripts $T --runs 15 --model qwen3:8b --results-dir $OUT

# model-free from here: every bar, the verdict, the cap sweep
OLLAMA_HOST=127.0.0.1:1 uv run python $M \
  --gold $G/ES2015.decisions.adjudicated.csv \
  --gold $G/TS3009.decisions.adjudicated.csv \
  --gold $G/IS1004.decisions.adjudicated.csv \
  --report $OUT/consolidation-*.json

# secondary reading only: the thesis metric (embedding model, no chat)
uv run python $M --gold ... --report $OUT/consolidation-*.json --thesis-metric
```

The stored file holds, per meeting-run, the raw items (title, subject, value, evidence line,
the gold ids it matched), the clusters, the call log and the timings, plus the identity stamp
(harness commit, model digest, hash of both prompts sent). It holds no transcript and no gold
annotation. Every arm and bar is recomputed from it by `--report` with no model, so a scoring
bug found later is fixed and re-read without a new run (a fix to a registered quantity is a
disclosed deviation).

## Stopping rules and deviations

- No early stop on a result. The only stops are a crash (the run is re-run) and the time
  ceiling above (INVALID).
- A deviation (a re-run, a harness fix that does not change a registered quantity, a missing
  meeting-run) is listed in `RESULTS.md` with its reason. A change to the gate, the guard
  prompt, a bar, the cap grid or the arms is a new registration.
- `RESULTS.md` applies this file mechanically. It may add readings, not verdicts.

## Disclosed before the run

- No model has answered anything under this treatment and no Ollama call was made to write
  this file. The self-test exercises the scoring, the bars, the clustering, the raw capture
  and one scripted meeting with a fake chat model and a fake embedder.
- `MERGE_COSINE = 0.75` is a judgement call. It is not derived from data: the earlier probes
  that tried to separate paraphrase from stranger pairs on embeddings alone found no clean
  threshold (`evals/query_identity/`), which is why the model guard exists, and why the gate
  is only a cost filter for the guard. A gate that is too high hides merges (Q fails); one too low only costs calls (C).
- Greedy single-representative clustering is a simplification. A decision restated twice with
  drift can be missed, and the earliest item is not always the best-worded one. Both would
  lower Q, not raise it.
- The guard sees the extracted statements and their evidence lines, not the transcript.
- `ranked` uses support, then window order, with no language cue. The issue's "explicitness
  of agreement" is therefore not ranked on, by the owner's decision; the ranking is
  deterministic and model-free, not a model judgement of quality, and it is unmeasured. With
  singleton supports `rank_only` equals window order, so the `ranked` against `positional`
  contrast shows only where merging produced support above 1.
- The bars with no earlier analogue (Q, K, M, F, C and the exposure floor of 10) are the
  author's picks. V is tighter than #1348's on purpose.

## What a `SUPPORTED` verdict does not establish

That the pass should be on by default, that the effect holds on a series outside these three,
that `qwen3:8b` is the right guard, or that Spanish (the gold's language) behaves like the
English transcripts. It does not establish a window overlap or a rolling summary (#1738).

## Limits (assumptions, labelled)

- Three series of four meetings each; one row of recall is about 0.037. IS1004's gold is a
  model's.
- One model, one machine, one embedder, `think: false`.
- The gold is sparse (AMI's abstractive decision sentences), so strict precision is low for
  any extractor; the ratio against `shipped` is the quantity that carries meaning.
- Embedding vectors come from a local Ollama; a different `bge-m3` build moves the gate.

## The owner confirms

Confirmed on 2026-11-09. The owner delegated these items to the author's recommendations; every item is as proposed except the answers recorded here:

- **Item 3 (ranking): no English cue.** Rank by support, then window order only. A language-specific regex cannot ship in a multilingual engine (the owner's corpora are Spanish), so a cap measured under an English cue would not transfer to production. The harness no longer carries the cue anywhere (it was used only by the ranking, so no readout or bar changed).
- **Item 8 (held-out): yes.** A `SUPPORTED` verdict enables no default change and no new flag until a confirmation on the thesis's held-out series (outside this repository) has been registered and read.
- **Item 10 (thesis-metric secondary reading): yes, run it.**

1. **The treatment**: merge on top of the exact dedup, nearest representative only, one guard
   call per candidate, fail closed; representative is the earliest; absorbed evidence is not
   carried.
2. **`MERGE_COSINE = 0.75`** and the guard prompt (hash `015e92802c6e3cd5`): proposed, not
   measured.
3. **The ranking**: support, then the English cue regex, then window order. Or no cue (support
   and order only)?
4. **The bars** marked proposed: Q (1.5 x and 13 of 15), K (1.0 row), M (0.5 row, exposure
   floor 10), V (2.0 x), F (5%), C (guard calls <= windows). In particular whether Q should be
   1.5 x (about a third of the Decisions must go) or milder.
5. **The verdict rule**: any failure on any series, IS1004 included, is NOT SUPPORTED; vacuous
   Q or M is UNDETERMINED.
6. **The cap rule**: smallest grid cap within 0.5 row of no cap in every series, grid
   8, 10, 12, 15, 20, 25, 40, none, both orders.
7. **n = 15**, and the harness refusing fewer.
8. **Held-out**: whether a `SUPPORTED` verdict needs a confirmation on the thesis's held-out
   series (outside this repository) before any default or flag ships.
9. **GPU time**: the forecast (about 4.5 to 7.4 h, ceiling 9 h) and when the GPU is free (it is
   reserved by the owner). The pass arm dominates; a cheaper variant (fewer runs) would be a
   different registration.
10. Whether the thesis-metric secondary reading is wanted (it adds an embedding-only step).
