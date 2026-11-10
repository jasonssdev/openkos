# Pre-registration: the Source fallback on long AMI Sources (#1741, second registration)

**Status: PROPOSED. It becomes REGISTERED when this file has merged to `main`, before the
first model call. The model-free readout below stops it at NO-REACH-PRE: no model is called
(see `RESULTS-long.md`).**
A change after that point is a new registration, not an edit to this one. It generates no
number itself: it fixes what will be generated, how it will be read, and what each outcome
means. Production code, prompts and defaults are unchanged by this file and by the run.
Every item marked **(author's recommendation)** is a choice the author could not settle from
the code or the first registration, made in the most conservative direction and listed for
the owner at the end.

This is the second registration of the same treatment. The first, `PREREGISTRATION.md`
(merged in #1748, results in `RESULTS.md`), stopped at **NO-EXPOSURE**: its fixture Sources
were about 4 KB, fit their share of the window, and the shipped engine answered from them
(3 of 36 `loss` cells refused, `FIRE_MIN` 6). #1741 reports the other regime: a Source that is
a full AMI meeting transcript, much longer than its share of the window, where the query-aware
excerpt can miss the fact and the sufficiency check refuses. This registration measures that
regime. **The treatment, the arms, the trigger, the bound, the provenance representation, the
bars B0 to B6, the stop order and the verdict rules are those of `PREREGISTRATION.md`,
unchanged** (a sourced answer opens with the unconditional banner
`[answered from sources, not compiled objects]` and cites Source line ranges); read it first. Only what the long regime requires is adapted, below.

The implementation is `measure_source_fallback_long.py`, a sibling that imports
`measure_source_fallback.py` and replaces its fixture through one seam (`HOOKS`, defined in
the first module and behaviour-preserving for the first registration: its self-test is
unchanged and green). No arm, bar or readout is reimplemented.

## What changes against the first registration

| | First (#1748) | This registration |
|---|---|---|
| Sources | 10 synthetic Spanish transcripts, about 4 KB | **12 full AMI meeting transcripts** (ES2015, IS1004, TS3009, meetings a to d), 8.3 KB to 42.7 KB |
| Point-fact questions | 12 (8 clusters), authored/derived in a synthetic chain model | **30 clusters: the rows are selected from the adjudicated gold, the questions are hand-authored in English** |
| Unanswerable | 10 adjacent grounding questions + 4 authored absent-fact | **8 authored English absent-fact questions**, machine-checked against the transcripts |
| Gold in the Source | substring of a marker | the gold row's own evidence act, found in the transcript by the shipped matching rule |
| Exposure | refusals at least `FIRE_MIN` of the `loss` cells | the same, plus a **model-free precondition on Source size** and a **model-free reach gate**, both checked before any model call |

## Data

Nothing is committed from the private thesis repository. The harness reads, from paths given
on the command line, the adjudicated gold CSVs (`ES2015`, `IS1004`, `TS3009`
`.decisions.adjudicated.csv`, pinned by the SHA-256 of `evals/decision_windows/`, a different
hash refuses the run) and the rendered transcripts (`<meeting>.md`). The development-series
allowlist of `evals/transcript_decisions/scoring.py` applies: only these three series are
read. The thesis question bank is for held-out series and is not touched.

**Sources.** Each of the 12 meetings is one `Source` document `sources/<meeting>`, titled
`AMI meeting <meeting>`, its body the rendered transcript after the frontmatter.

**Questions (rows selected from the gold, questions hand-authored in English).** Selection,
from the gold rows alone:

1. A row is eligible when it is a `decision` row, not a split child (`-N` suffix), has an
   object (`final_object`) and a content (`final_content`), has at least `MIN_VALUE_STEMS` value
   stems of its Spanish content (below), its object is shared by no other gold row of its
   meeting, and its evidence has an act of at least 4 tokens that the shipped matching rule
   finds in its own transcript (so the Source holds the fact by the gold's own evidence).
2. Per meeting, the `PER_MEETING` eligible rows first in SHA-256 order of
   `1741-long:<decision_id>` are taken (**author's recommendation**: a fixed salted hash, not a
   choice by hand and not the first rows, which would bias to early topics). IS1004c has none
   (every row is a split child) and TS3009a and TS3009d have fewer than three: **30 questions**.
   This selection is unchanged from the draft that used Spanish questions.

**The questions are in English and authored by hand (owner's choice, 2026-11-10).** The
draft derived a Spanish question from the gold object (`En la reunion <meeting>, que se
decidio sobre <final_object>?`) over English transcripts. Its model-free readout was an oracle
reach of 0.133 at the fallback share and 0.033 at the shipped share: the BM25 excerpt has almost
no lexical signal across languages, so that measured the language mismatch, not #1741's failure
mode. The owner replaced it with English questions. For each of the 30 selected rows one
English point-fact question was written from the row's subject and the meeting, reading the
transcript region around the gold evidence so the question is answerable from the Source, and
never saying the decided value. They live in the code as the table `QUESTIONS_EN`, keyed by
`decision_id` and hashed into the fixture (question text only). The gold value and evidence
are never committed; they are read from the private repository at run time.

**Correctness is scored on an English gloss, kept out of the repository.** For each question
there is a short English gloss of the decided value, written from the Source's own evidence
line and used only for scoring. The glosses paraphrase private gold annotations and this
repository is public, so they are **not versioned**: they live in a gitignored local file
`evals/source_fallback/local/glosses.json`, `{decision_id: gloss}`, read through
`--glosses PATH` (that path is the default). Without the file, `exposure` still computes the
size precondition, the oracle reach and the core hash, and reports the leak check and the full
hash as `skipped: no glosses file`; `run`, `fallback`, `score`, `decide` and `report` refuse.
Anyone with the private gold can reproduce the selection, the questions, the reach and the
core hash; the leak check, the scoring and the full hash need the author's local file. The gloss holds the value alone (not the subject). Its scored stems are
those of the gloss: lower-cased, accents removed, English function words dropped, words of
four or more letters cut to `STEM_LEN` characters, digit tokens and 2 to 3 letter capitalised
acronyms (`TV`) kept whole. An answer **hits** the value when at least `STEM_MATCH_SHARE` of
the stems appear in it (a word stem as a token prefix, a digit or acronym stem as a whole
token). A gloss must yield at least `MIN_GLOSS_STEMS` stem. An answer is **correct** when it
hits the value, is not a refusal, **cites the gold meeting and cites no other meeting** (the
analogue of `series_clean`). A cited `decisions/<meeting>-dNN` page counts as its meeting.
**foreign** is any citation of another meeting.

**Leak check (model-free, needs the glosses file).** No question may contain a scored stem (a stem equal for words,
an equal whole token for digits and acronyms). The self-test and `exposure` refuse a leaking
question, a selected row with no question, a gloss with no stem, and a question for a row that
is not selected. The check caught one leak while authoring (a gloss that said "technology" for
a question that said "technology"), fixed in the gloss.

The Spanish value stems of the content are kept only for the eligibility rule above (so the
selection is the registered one); they are not scored.

**Conditions.** As before, over two copies of one bundle:

- **`loss` (30 questions):** the 12 Sources only. Extraction lost every fact; the transcript
  holds it.
- **`intact` (30 questions, ids `q01i-en`, ...):** the 12 Sources plus one `Decision` page per
  eligible gold decision row (not only the 30 asked), its title the object, its body the
  content, `provenance` its Source. A perfect extraction, the upper end; the fallback can only
  fire there on a false refusal. Readout, as before. The Decision pages stay in the gold's
  Spanish while the questions are English, so retrieval in `intact` is cross-lingual.

**Unanswerable (8, `ua01` to `ua08`), asked over the `loss` bundle.** English questions about a topic
the transcripts discuss and for which they state no value: a manufacturer, an advertising
channel, a launch date, units to sell, training hours, a competitor chosen as price
reference, a battery brand, a marketing budget. Each carries two regexes, registered in the
code and hashed into the fixture: the topic must match a line of some Source (the question is
on topic) and a regex for a stated value of that kind must match **no** topic line. Both are
checked model-free by `exposure`; a violation stops the run. The 10 adjacent grounding
questions of the first registration are dropped: they are about RAG topics over a bundle of
remote-control meetings and would be trivially refused, a regime the first registration
already measured.

Cells: 68 items by 3 runs (seeds 1 to 3) = **204 base cells** (90 `loss`, 90 `intact`, 24
unanswerable). The `fallback` arm is derived from the `base` cell as before.

## The exposure floor and the precondition

Three registered checks, in this order, before any model call (the first two again after pass 1):

1. **Precondition on Source size (model-free, `exposure`).** The prompt budget is the shipped
   one measured by construction (`fallback_budget`: window 12288, reply reserve 8192, the
   overhead rule), the most generous over the 30 questions. A Source **exceeds its share** when
   its body is longer than `budget / FALLBACK_SOURCES`, the wider of the two arms' shares. The
   precondition passes when at least `EXCEED_MIN` of the 12 Sources exceed it. A fixture that
   fails it is **NO-EXPOSURE before any model call**, and `run` refuses to start.
2. **Reach gate (model-free, `exposure`).** With the right Source chosen, the share of the
   answerable questions whose query-aware excerpt at the fallback share holds the gold evidence
   (the oracle reach, retrieval aside) must be at least `ORACLE_REACH_MIN = 0.5`. Below it,
   `exposure` reports `NO-REACH-PRE` and `run` and `fallback` refuse to start: if the excerpt
   does not hold the evidence for half the questions even with the right Source, nothing the
   fallback does downstream can be measured. The floor is distinct from `REACH_MIN`, the
   post-pass-1 bar. It is half: a floor under a half would let a majority of questions
   be unanswerable from the excerpt (**author's recommendation**). **This gate was added at the
   owner's direction after the Spanish readout (0.133, 0.033) had been seen**, so it is not a
   pre-outcome choice in the strict sense; it is model-free, and no model had been called.
3. **`FIRE_MIN`, after pass 1 (unchanged rule, new number).** Fewer than `FIRE_MIN` refused
   `loss` cells is NO-EXPOSURE, read before any fallback call. `FIRE_MIN` is a sixth of the 90
   `loss` cells, the first registration's 6 of 36 (**author's recommendation**); `exposure`
   refuses a fixture whose question count implies a different number.

Measured on the real fixture, model-free (`exposure`): budget 8719 chars, fallback share 2906
(3 Sources), shipped share 2179 (4 Sources, the `select_top` cap). Source sizes in characters:
IS1004a 8289, ES2015a 13475, TS3009a 23342, IS1004d 29364, ES2015d 32978, ES2015c 34357,
IS1004b 34540, IS1004c 36343, TS3009d 37272, ES2015b 38944, TS3009c 40099, TS3009b 42686;
median 34448, **12 of 12 exceed their share** (the smallest by 2.9 times, the median by
11.9 times). The precondition passes. The **oracle reach** of the 30 English questions is
**0.467 at the fallback share (14 of 30) and 0.333 at the shipped share (10 of 30)**. The
gate fails: **NO-REACH-PRE**. The English readout was taken once, on the questions as first
written, before any revision, and the owner decided not to revise them. Per-question hits are
in `RESULTS-long.md`.

**Fixture hashes**, recorded before the run and refused if either differs. The **core hash**
(`core_hash()`: the item ids, conditions, bundles and texts; the gold question rows, their
Spanish stems and the authored questions; the absent-fact questions and regexes; every
`Decision` page row; the SHA-256 of every Source body) needs no glosses:
`7d799c74462ebaefb7ae5284700d0c8c0711646fc16497a0193b89c5192a8209`. The **full hash** (`fixture_hash()`: the core hash plus every gloss and its
scored stems) needs the local glosses file:
`22ff6ef42eb7438ec547e505bc407f1220a5aee05ffab04695fc2b2d7bb3ec7f`. The code carries the same
constants; the self-test fails if this file and the code disagree.

## Bars

The constants of `measure_source_fallback.py::BARS` apply unchanged, except the exposure floor.
The self-test fails if this file and the code disagree.

```
FALLBACK_SOURCES = 3
FIRE_MIN = 15
REACH_MIN = 0.5
HEADROOM_MAX = 0.6
BENEFIT_MIN = 0.25
SPAN_SHARE_MIN = 0.8
FALSE_ANSWER_RISE_MAX = 0.0
PRECISION_MIN = 0.7
CONTAMINATION_RISE_MAX = 0.05
LATENCY_MEAN_RATIO_MAX = 1.25
FAILED_MAX = 0.15
```

Constants of this registration:

```
PER_MEETING = 3
MIN_VALUE_STEMS = 2
MIN_GLOSS_STEMS = 1
ORACLE_REACH_MIN = 0.5
STEM_LEN = 5
STEM_MATCH_SHARE = 0.5
EXCEED_MIN = 0.75
CAP_HOURS_RUN = 2.0
CAP_HOURS_FALLBACK = 1.0
```

B0 to B6 as in `PREREGISTRATION.md`: B0 headroom, B1 benefit with a positive cluster-bootstrap
lower bound (30 clusters, seed 1741), B2 the citation supports the answer (the cited excerpt
holds the gold **evidence act**, language-free, instead of a marker), B3 abstention protected
and strict (`FALSE_ANSWER_RISE_MAX = 0.0`: one new false answer in the 24 unanswerable cells
rejects), B4 precision, B5 no more mixing (here: citing a meeting other than the asked one),
B6 cost. **Verdicts and stop order:** `INCONCLUSIVE`, then `NO-EXPOSURE`, then `NO-REACH`,
then `NO-HEADROOM`, then `SUPPORTED` or `REJECTED` naming the failed bars. No neutral verdict.

## n and the stopping rule

n = 3 runs. Before pass 1 the reach gate must pass. Pass 1 (`run`) runs the shipped `answer()` for every cell and the model-free plan
of each fallback; `futility` is then read and **only** then: `NO-EXPOSURE` (fewer than
`FIRE_MIN` refused `loss` cells), `NO-REACH` (fewer than `REACH_MIN` of the fired `loss` cells
hold the gold evidence in the assembled fallback prompt). Pass 2 (`fallback`) only if no stop
fired. No other stopping rule; failed calls are reported "n failed of TOTAL", not re-run.

## Cost forecast and the wall-clock cap

From the first run, measured: 114 base cells in 10 min 31 s, **5.5 s per cell**, with mean
latency 5.35 s (`RESULTS.md`). The prompt is bounded by the same window in both fixtures, so a
long-Source cell is not a longer prompt but a fuller one (the short fixture also overflowed the
budget); the scale is the share of cells that answer instead of refusing, a refusal costing a
second or two and an answer 8 to 25 s. Scaled, unmeasured:

| Step | Cells | Low | Typical | High |
|---|---|---|---|---|
| pass 1: 204 shipped answers at 5.5 s times 1.0, 1.5, 2.5 | 204 | 0.31 h | 0.47 h | 0.79 h |
| pass 1: indexing 2 bundles of about 370 KB with `bge-m3` | 0 chat | 0.03 h | 0.08 h | 0.15 h |
| pass 2: re-check (1 to 3 s) and synthesis (8 to 28 s) on fired cells (at most 204) | at most 204 | 0.10 h | 0.25 h | 1.00 h (capped) |
| model loads | | 0.03 h | 0.07 h | 0.07 h |
| **Total** | | **0.5 h** | **0.9 h** | **2.0 h** |

If a futility stop fires, only pass 1 runs: about 0.6 h typical.

**Wall-clock cap: 3.0 h in all**, enforced by the harness: `run` starts no new cell after
`CAP_HOURS_RUN = 2.0` h and `fallback` none after `CAP_HOURS_FALLBACK = 1.0` h (passing `--cap-hours`
is a deviation and is recorded in `RESULTS-long.md`). Cells left over are missing, which is
INCONCLUSIVE; the verdict is read on what exists. Ollama serializes; one job at a time. The
GPU is reserved by the owner: ask before the first model call.

## Exact commands

`GL` is the glosses file (default `evals/source_fallback/local/glosses.json`). `G` is the directory holding the three `*.decisions.adjudicated.csv` and `T` the directory
holding the rendered `<meeting>.md` (the private thesis repository's
`data/processed/decisions` and `data/interim/transcripts`; nothing is copied). `OUT` defaults to
`~/Dev/Projects/openkos-eval-runs/source-fallback-long`, outside the repository. Record
`git rev-parse HEAD` and `ollama list` first.

```sh
M=evals/source_fallback/measure_source_fallback_long.py
A="--gold-dir $G --transcripts $T --glosses $GL"

# model-free, anywhere (no private data)
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# model-free, needs the data: the registered precondition, the hash, the sizes
OLLAMA_HOST=127.0.0.1:1 uv run python $M exposure $A

# pass 1, then read futility ONLY
uv run python $M run $A
OLLAMA_HOST=127.0.0.1:1 uv run python $M futility $A

# pass 2, only if futility is null
uv run python $M fallback $A

# model-free from here: reads only <OUT>/cells.json, the one final store
OLLAMA_HOST=127.0.0.1:1 uv run python $M report $A
```

`run` and `fallback` refuse to start unless `exposure` would pass (precondition, hash,
`FIRE_MIN`, absent-fact checks). `score`, `decide` and `report` read only the final store,
refuse a store with a duplicate `(item, run)` cell or one written for another fixture hash
(the guard of #1754, which refused duplicate records so a `-PARTIAL` checkpoint could not be
double counted; this store is one file updated in place, so the guard is on its cells).
`report` runs `score` then `decide`. The identity stamp (harness commit, model digest, hash of
the prompts) is written into the store.

## Outputs

`RESULTS-long.md` in this directory, as in `RESULTS.md`: the commit, model digests, cells run,
failed and fired, the table of every bar, the per-cluster rates, the fire rate and fallback
outcomes, the cost against the forecast, the verdict and the limits. The verdict updates #1741
and nothing else.

## Disclosed before scoring

- No model has answered any question under these arms. Nothing was run on GPU. The only
  model-free run on the real data is `exposure`.
- **The reach gate was written after the Spanish readout.** The Spanish draft's oracle reach
  (0.133 and 0.033) was seen first; the English questions and the 0.5 floor were then
  chosen by the owner. The English readout was taken once, before any revision of the
  questions; no question was changed after it. The questions were authored by the harness
  author, who also knew the BM25 excerpt rule, so lexical overlap with the transcripts is
  not blind.
- **The glosses are not versioned.** They are the author's paraphrase of private gold and
  live in a gitignored local file. A reader without it can verify the questions, the
  selection, the reach and the core hash, but not the leak check, the scoring or the full
  hash, and cannot audit a gloss.
- **Intact is cross-lingual.** Its Decision pages are the gold's Spanish, the questions English.
- **Correctness is a stem proxy.** The English gloss stems are matched in the answer; polarity is not read (a gloss naming a feature is hit by
  an answer that says the feature was rejected); glosses are the author's paraphrase of the evidence
  line, and a synonym in the answer scores a miss. The same limit applied to the first
  registration's markers.
- **Total loss is the best case**, as before: every Decision is removed in `loss`.
- **The fallback differs from the base mostly by share.** In `loss` only `Source` documents
  exist, so the shipped context holds up to 4 Sources (the `select_top` cap) at 2179
  characters each and the fallback 3 at 2905: 1.33 times wider. A gain, if any, comes from the
  re-check and a different cut of at most three Sources, not from a new corpus.
- **Three Sources of twelve**, `FALLBACK_SOURCES = 3` unchanged and not tuned.
- **B3 has 24 cells** (8 questions, 3 runs) and no margin: one new false answer rejects. The
  absent-fact questions are the hard case; the regex checks show only that no topic line
  states a value of that kind, not that the transcript is silent on the question.
- One model, one window, one machine; the cap is 3 h; the three series are the development
  series of the thesis, and the held-out series are untouched.

## What a `SUPPORTED` verdict does not establish

That it holds for questions not authored by the harness author, at another window, model or sufficiency model, on a partially lossy
library, on held-out meetings, or that an answer is true (OpenKOS preserves representations,
not truth). It establishes that, on 12 full AMI transcripts and 30 gold-derived point facts, a
bounded second read of the Sources recovered facts the extraction had lost, cited the
lines that hold them, and did not fabricate on 8 unanswerable questions.

## The owner confirms

Decided 2026-11-10: 1. the questions are **English and hand-authored** (not Spanish and
derived), kept exactly as first written; 2. the **reach gate** `ORACLE_REACH_MIN = 0.5` is
added, before any model call; 3. the result is **NO-REACH-PRE**, recorded in
`RESULTS-long.md` with no model run. Items 2 to 9 of the earlier draft (value score as a gloss
stem proxy, the selection, the `intact` condition, the absent-fact set, `FIRE_MIN = 15` and
the size precondition, the caps, the bars, the GPU forecast) stand as the author's
recommendations and are moot for a run that does not start.
