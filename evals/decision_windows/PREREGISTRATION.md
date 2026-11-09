# Pre-registration: overlap and a rolling subject list between decision-pass windows (#1738)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the section
"The owner confirms" and this file has merged to `main`, before the first model call.**
A change after that point is a new registration, not an edit to this one. It generates no
number itself: it fixes what will be generated, how it will be read, and what each outcome
means. Production code, prompts and defaults are unchanged by this file and by the run.
Every bar marked **(proposed)** is the author's proposal until the owner confirms it.

It reuses the decision-pass harness of `evals/transcript_decisions/` (windows, matching
rule, gold loading, the development-series allowlist, the shared-evidence recall credit)
and its registered conventions: `PREREGISTRATION-1348.md`, `-1364.md`, `-1364-IS1004.md`.
Both treatments are not in production; they live in `measure_decision_windows.py` only, as
a change to the windows the shipped pass is handed and to the user message it is sent.

## Question

The transcript decision pass asks each `_chunk_lines` window (`_CHUNK_TARGET = 4000`
characters) independently (#1738). A full AMI series is 1.6 to 2.8 times the 12,288-token
context, so segmentation is unavoidable, and each window has no knowledge of the others:

1. a decision proposed in one window and agreed in the next is split or lost;
2. the same decision restated later becomes a new item.

Two treatments are measured, separately:

- **`overlap`**: each window after the first starts with the last lines of the previous one
  (at most `OVERLAP_LINES = 6` whole lines and `OVERLAP_MAX_CHARS = 800` characters), and an
  item whose evidence line lies in that repeated head is dropped when the previous window
  already produced an item on the same line.
- **`rolling`**: every window after the first is sent, appended to its user message, the
  most recent `ROLLING_MAX_SUBJECTS = 30` distinct subjects admitted from earlier windows
  (each cut to 80 characters) under a fixed header (hash `47a4b2f6997419ef`), asking the
  model to list a decision again only if the window settles, changes or completes it.

A `SUPPORTED` verdict for a treatment licenses a separate production change with its own
OpenSpec change and ADR, behind an off-by-default key. It changes nothing here.

### What the shipped code does today (verified against the files)

- `concept._run_decision_pass` builds the windows with `_chunk_lines(text)` when the source
  fans out, else one window (`harness.pass_windows` reproduces this). `_chunk_lines` packs
  whole lines with no overlap and is lossless (`"\n".join(windows) == text`).
- `decision_pass.run_decision_pass` asks each window once (twice when the first reply is
  unusable), keeps the items whose evidence is quoted verbatim **in that window**, then runs
  `dedup_by_subject` (first occurrence wins per normalized subject) and cuts at
  `MAX_PASS_DECISIONS = 25`, once per source.
- Verbatim admission is checked against the window that produced the item, so with overlap
  an item whose evidence sits in the repeated head is admissible in the later window too.
  That is the mechanism the overlap dedup exists to undo.
- `transcript_decisions` is **off by default and NOT ADOPTED**: bar R failed on all three
  development series (`RESULTS-1361.md`, `-1364.md`, `-1364-IS1004.md`). This registration
  does not ask whether to adopt the pass. It asks whether, **inside the opt-in pass**, either
  treatment recovers more of the annotated decisions or removes restatements without
  inflating what the pass produces.

## Relation to #1737 (decision-pass consolidation)

Independent. #1737 acts on the pass's **output** (merging items that state one decision,
ranking, the cap). This acts on the pass's **input** (the windows and the prompt). Neither
needs the other's code: this harness imports only merged code (`transcript_decisions`'s
harness and scoring, `decision_pass`), runs its own control arm, and does not read #1737's
raw capture or results. The two registrations use the same gold, the same matching rule and
the same development series, so their numbers are comparable but not pooled.

The order in a pipeline would be windows (this), pass, consolidation (#1737). **The
interaction is not measured here.** If both come back `SUPPORTED`, the combination is a new
registration; if either does not, there is nothing to combine. A restatement the rolling list
removes is a restatement #1737's merge no longer has to find, so the two may overlap in what
they gain; this registration cannot say by how much.

## What this does not touch

- Production code, prompts, defaults. The system prompt is the shipped one (hash
  `6d3962b0dd75650c`); the pass runs as shipped including the re-ask of a failed window
  (`retry_after_cap=True`), the generation ceiling 8192 and the context window 12288.
- The exact-subject dedup and the cap of 25: they run in every arm, in the shipped order,
  after the treatment. The arm `shipped` is pinned by the self-test to equal what
  `run_decision_pass` returns (same prompts sent, same results).
- The matching rule, the gold files and the earlier registrations' bars R and P.
- Model choice: `qwen3:8b` only. Window size (`_CHUNK_TARGET`) is not varied; larger windows
  are a separate lever (ADR-0084) and this does not claim to replace it.

## Arms

| Arm | Windows | User message | Role |
|---|---|---|---|
| `shipped` | `_chunk_lines` | as shipped | control (production) |
| `overlap` | head of 6 lines / 800 chars, overlap dedup | as shipped | **candidate** |
| `rolling` | `_chunk_lines` | plus the subject list | **candidate** |
| `both` | head, overlap dedup | plus the subject list | reported, no bar; **not run** (owner item 7) |

Each arm is a separate sequence of model calls: the arms are **not** paired on shared
replies (unlike #1737), so every comparison is between two samples of runs. Within a run the
arms are interleaved per meeting, so a slow drift of the server hits them alike. Only
`overlap` and `rolling` against `shipped` decide a verdict; each has its own verdict, with no
pooling and no correction for there being two (stated, not adjusted).

### The treatments, exactly

- **Overlap head.** For each window after the first, the last `min(6, lines)` whole lines of
  the previous base window, dropping lines from the front until the head is at most 800
  characters. The window the model sees is head plus the shipped window; the shipped window
  is the lines it **owns**.
- **Overlap dedup.** The evidence line of an admitted item is located as the first line in
  the window's span (head plus owned lines) that quotes it verbatim; evidence that no single
  line holds has no position and is never deduplicated by position. An item of window i
  whose position lies in its head is dropped **only as far as window i-1 produced items on
  that same line, one for one**: two real decisions sharing one evidence line are not
  collapsed, and a decision the previous window missed survives (the purpose of the overlap).
  The exact-subject dedup then runs as shipped.
- **Rolling subjects.** Before each window after the first, the list is the admitted (verbatim)
  items of earlier windows, distinct by normalized subject, **most recent** 30, oldest first,
  each cut to 80 characters. Nothing is appended to window 1. The list is rebuilt from
  admissions, so a re-ask of the same window sees the same list.

## Data

**Development series only**, via the fail-closed allowlist `scoring.DEV_SERIES` (no flag
lifts it). The gold files are those of the earlier registrations, verified by SHA-256 before
the first call and refused on mismatch; the data stays in the thesis checkout and nothing from
it is copied here:

| Series | Gold file | SHA-256 | `decision` rows | Gold |
|---|---|---|---|---|
| ES2015 | `ES2015.decisions.adjudicated.csv` | `de3e6439e7e8fe79b623d1d58f72a009fb691ebe0ee96d01f1c105e18debad9c` | 27 | human |
| TS3009 | `TS3009.decisions.adjudicated.csv` | `40aa8312480462bcedddaa31a5f379d893ccf8235e3ed7dcf845c666cb46921a` | 26 | human |
| IS1004 | `IS1004.decisions.adjudicated.csv` | `1b84ae083f1547fc6f356663fc80df9aa2f2aa978196406e50ae2e5e9e3cae7d` | 27 | **model** (Claude Opus; `PREREGISTRATION-1364-IS1004.md`) |

(The digests are copied from the #1737 registration for the same files. The harness verifies
them at load, for the run, `--exposure` and `--report` alike, and exits non-zero with `REFUSED`
on a mismatch or on a file name that is not one of the three; the self-test covers both.) Rendered transcripts `<meeting>.md`
of the same checkout, 12 meetings (a to d of each series). `_is_meeting_shaped` must be true for
all of them or the run refuses. Unrecallable gold rows stay in every denominator, as before.

### Development versus held-out, stated plainly

- **Held-out.** The thesis keeps eleven other series for its final evaluation. This harness
  refuses them by construction and never reads them. The external evaluation that motivated
  #1738 (evolution questions 0.17 against 0.71 with human Decisions at the 12288 window) used
  that set; this repository does not reproduce it.
- **Tuning.** Nothing of either treatment has been run on any series. The head size, the list
  bound, the header, every bar and the exposure floor are fixed here before any output exists,
  and a different value is a new registration. There is no sweep of head size or list bound:
  sweeping them on three development series would be tuning on them.
- **What the author already knows, disclosed.** The pass was built and judged on these three
  series and its stored volumes are in the RESULTS files; the bars were set knowing them.
- **Consequence.** `SUPPORTED` is **development-series** evidence. A default would need a
  confirmation on the thesis's held-out set, run by its owner (item 8).
- **Synthetic fixture.** The model-free `--self-test` uses a synthetic 12-line transcript, gold
  and scripted replies written for it. It reads no thesis data.

## Repetitions

**n = 15 runs**, each covering all 12 meetings for every arm run (the floor of
`PREREGISTRATION-1364.md`; the harness refuses `--runs` below 15). Sampling is the server's
default; seeds are not controlled. A run counts only if it completes for every arm and every
meeting; an incomplete arm is reported INVALID by `--report`, never averaged. One Ollama job at
a time.

## Metrics

Per series per run (summed over the series' four meetings), then mean and min-max over the 15
runs, always `n of TOTAL`. Every arm is read **at the shipped cap of 25**, the way production
cuts it; the stored items are uncapped and in order, so the uncapped reading is available from
the same file. Credit and buckets are the earlier registrations' (`transcript_decisions/README.md`):
longest-common-run matching, shared-evidence credit, `compuesta` and `descartar` outside the denominator.

- **recalled** = gold `decision` rows credited; **recall** = recalled / `decision` rows.
- **strict precision** = recalled / Decisions produced. A Decision that restates a credited
  row, matches no row, or matches only a non-decision row counts against it. This is the
  issue's "precision with unannotated Decisions counted".
- **precision-on-annotated** = `matched_decision` / (`matched_decision` + `matched_non_decision`),
  the earlier bar P's quantity, kept as a guard.
- **Model calls per source**: `calls` per meeting-run (windows plus re-asks), and wall time.
  Windows per source are identical across arms by construction (the head adds characters, not
  windows), so a call difference comes only from re-asks.
- Window failures: meeting-runs whose `failure` is set, per arm.

## Bars

Evaluated **per series**, for each candidate against `shipped`, on the runs of each arm.
`z` is the unpaired difference of means over its standard error (`statistics.variance`).

| Bar | Rule |
|---|---|
| **K** recall kept (proposed) | mean recalled of the candidate >= mean recalled of `shipped` - 1.0 gold row. |
| **G** gain (proposed) | at least one of: (a) mean recalled >= `shipped` + 1.0 row AND z >= 2.0; (b) mean strict precision >= 1.25 x `shipped`'s AND K passes AND z >= 2.0. `vacuous` if `shipped` recalls nothing. |
| **V** volume (proposed) | mean Decisions produced <= 1.25 x `shipped`'s. A treatment may not buy recall by flooding. |
| **P** guard (proposed) | mean precision-on-annotated >= `shipped`'s - 0.05. `vacuous` if either arm has no annotated Decision. |
| **F** window failures (proposed) | failed meeting-run fraction <= `shipped`'s + 0.05. |
| **C** cost (proposed) | mean calls per meeting-run <= 1.15 x `shipped`'s. Wall time reported, not gated. |

**Exposure.** A boundary can only cost the pass a gold decision whose dialogue act sits at the
head of a window after the first. `--exposure` counts, model-free and before any call, the gold
`decision` rows with an act in the first 6 lines of a non-first shipped window, pooled over the
12 meetings. The floor is **6 rows** (proposed). Below it the treatments were never handed
anything the gold could mark either way, so the run is not started (there is nothing to
spend the GPU on) and the result is recorded as UNDETERMINED: "not measurable on these series".

### Verdict (fixed), per candidate

- **INVALID**: fewer than 15 complete runs for `shipped` or the candidate, or a required series
  missing. The numbers are shown, labelled, and are not the result.
- **NOT SUPPORTED**: any bar `fail` on any series, **including IS1004** (model gold; "series
  disagree" is still NOT SUPPORTED, as `PREREGISTRATION-1364-IS1004.md` decided).
- **UNDETERMINED**: no bar failed, but G is `vacuous` on some series or exposure is below the floor.
- **SUPPORTED**: every bar is `pass` (P may be `vacuous`) on all three series and exposure is at or above the floor.

`SUPPORTED` does not flip any default and does not touch `transcript_decisions`. It licenses
one thing: an OpenSpec change and ADR for that treatment inside the opt-in pass, off by
default, with the confirmation of item 8 before anything else.

## Expectations, stated before the run

- Honest prior: **overlap is likely to be small.** The gold is sparse (at most 14 `decision`
  rows in any one meeting), so only a handful of rows sit at a boundary, which is why
  exposure is a gate. A 6-line head gives the agreement line its proposal, but the model may
  still not restate it; the lift, if any, is a few rows.
- **Rolling is likely to cut volume and may cut recall.** Telling `qwen3:8b` what it already
  said invites it to say less, including what it should have said. The first-wins exact-subject
  dedup also drops a later item that **completes** an already-listed subject (it keeps the
  earlier, incomplete one), so the "settles or completes" half of the header cannot show up in
  the output unless the model picks a new subject. That is a limit of measuring inside the
  shipped dedup, and it would bias this toward NOT SUPPORTED on G; it is disclosed rather than
  patched, because changing the dedup is a production change.
- The cap rarely binds on ES2015 and IS1004 (about 15 and 11 Decisions per meeting against
  25), so V is mostly about TS3009.
- IS1004's gold is a model's. A result there is agreement with that model.

## Cost forecast (Ollama)

Inputs from stored `qwen3:8b` runs: the pass costs 933 s per 12-meeting run across the three
series (`RESULTS-1361.md`, `-1364.md`, `-1364-IS1004.md`; TS3009a includes a runaway window).
Overlap adds at most 800 characters to each window after the first (about +15% input
tokens); rolling adds at most 30 short lines (about +5%). Embedding is not used.

| Arm | Forecast, 15 runs |
|---|---|
| `shipped` | 15 x 933 s = **3.9 h** |
| `overlap` | about 4.1 h |
| `rolling` | about 4.1 h |
| **Total (default three arms)** | about **12 h**, ceiling **15 h** |
| `both` | not run (owner item 7; it would have added about 4.2 h) |

The GPU is reserved by the owner (item 10). If the first run alone (all 12 meetings, all arms)
projects past the ceiling at 15 runs, the run stops and reports short; that is an INVALID
result, not a smaller n.

## Exact commands

One Ollama job at a time. Record `git rev-parse HEAD` and `ollama list` first; the harness
code is the registering PR's merge commit with a clean tree. `G` is the directory holding the
three gold files, `T` the rendered transcripts, `OUT` a directory outside the repository.

```sh
M=evals/decision_windows/measure_decision_windows.py

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# model-free, before any GPU time: how many gold rows can a boundary affect?
uv run python $M --gold $G/ES2015.decisions.adjudicated.csv \
  --gold $G/TS3009.decisions.adjudicated.csv \
  --gold $G/IS1004.decisions.adjudicated.csv --transcripts $T --exposure

# the run (the three arms of the default; `both` is not run)
uv run python -u $M \
  --gold $G/ES2015.decisions.adjudicated.csv \
  --gold $G/TS3009.decisions.adjudicated.csv \
  --gold $G/IS1004.decisions.adjudicated.csv \
  --transcripts $T --runs 15 --model qwen3:8b --results-dir $OUT

# model-free from here: every bar and both verdicts
OLLAMA_HOST=127.0.0.1:1 uv run python $M \
  --gold $G/ES2015.decisions.adjudicated.csv \
  --gold $G/TS3009.decisions.adjudicated.csv \
  --gold $G/IS1004.decisions.adjudicated.csv \
  --transcripts $T --report $OUT/windows-*.json
```

The stored file holds, per arm and meeting-run, every uncapped item (title, subject, value,
evidence line, the gold ids it matched, the window and line position), windows, calls, failure
and wall time, plus the identity stamp (harness commit, model digest, hash of the system prompt
and of the rolling header). It holds no transcript and no gold annotation. `--report` recomputes
everything with no model, so a scoring bug found later is fixed and re-read without a new run
(a fix to a registered quantity is a disclosed deviation).

## Stopping rules and deviations

- No early stop on a result. The only stops are a crash (re-run), the exposure floor and the
  time ceiling above.
- A deviation (a re-run, a harness fix that does not change a registered quantity, a missing
  meeting-run) is listed in `RESULTS.md` with its reason. A change to the head size, the list
  bound, the header, a bar, the exposure floor or the arms is a new registration.
- `RESULTS.md` applies this file mechanically. It may add readings, not verdicts.

## Disclosed before the run

- No model has answered anything under either treatment and no Ollama call was made to write
  this file or the harness. The self-test exercises the windowing, the overlap dedup, the
  rolling list and its bound, the control pin to `run_decision_pass`, the scoring, the bars and
  the verdict rule with scripted replies. It was written first and observed failing, then
  implemented; six single-line mutations of the core (the head bound, the head length, the
  window-0 guard, the one-for-one budget, the volume bar, and the switch that enables the
  dedup) were each caught.
- The head size, the list bound and the header wording are judgement calls, not derived from
  data. Locating evidence by first matching line in the window's span can pick the wrong line
  when a short utterance repeats; such an item is then kept (never wrongly dropped by position
  alone beyond the one-for-one budget), which errs toward volume, not toward lost recall.
- The rolling list is built from verbatim-admitted items before any dedup; a subject the model
  rephrased is shown as it was said.
- The bars with no earlier analogue (G, V, F, C, the exposure floor, and the z >= 2.0 reading)
  are the author's picks; K echoes the one-row noise unit of the earlier registrations.
- Two candidates against one control with no multiplicity correction: each verdict is a
  separate registered rule, and two `SUPPORTED` would still need the held-out confirmation.

## What a `SUPPORTED` verdict does not establish

That the pass should be on by default, that the effect holds on a series outside these three,
that `qwen3:8b` is the right model, that Spanish behaves like the English transcripts, that a
larger window would not do as well, or that the treatments compose with #1737's consolidation.

## Limits (assumptions, labelled)

- Three series of four meetings each; one row of recall is about 0.037. IS1004's gold is a model's.
- One model, one machine, `think: false`.
- The gold is sparse, so strict precision is low for any extractor; the ratio against `shipped`
  carries the meaning.
- Unpaired arms: run-to-run swing of one arm against itself (`five-run arm swings wider than the
  effect` was seen at n = 5) is what the z >= 2.0 rule and n = 15 are there to absorb.

## The owner confirms

Confirmed on 2026-11-09. The owner delegated these items to the author's recommendations; every item is as proposed except the answers recorded here:

- **Item 7:** n = 15; the optional `both` arm is not run (saves about 4.2 h; it had no bar). The harness still contains the arm and its self-test, and `--arms` would accept it, but no documented command runs it; running it would be a deviation. The forecast is unchanged: its total already excluded `both`.
- **Item 8:** yes. A `SUPPORTED` verdict enables no default change and no new flag until a confirmation on the thesis's held-out series (outside this repository) has been registered and read.
- **Item 9:** the three SHA-256 values are the intended pins, and the harness verifies them at load and refuses to run or score (clear error, non-zero exit) on a mismatch.

1. **Overlap**: a head of 6 lines / 800 characters; dedup by evidence line, one for one against
   the previous window only; items without a single-line position never deduplicated by position.
2. **Rolling**: the most recent 30 distinct subjects, 80 characters each, appended to the user
   message of windows 2 onward, header hash `47a4b2f6997419ef` (wording in the harness,
   `ROLLING_HEADER`); the shipped first-wins dedup left unchanged even though it drops a
   completion of a listed subject.
3. **Arms**: `shipped`, `overlap`, `rolling` decide; `both` is reported only.
4. **The bars** marked proposed: K (1.0 row), G (1.0 row with z >= 2.0, or 1.25 x strict
   precision with z >= 2.0), V (1.25 x), P (-0.05), F (+5 points), C (1.15 x calls).
5. **The verdict rule**: any failure on any series, IS1004 included, is NOT SUPPORTED; vacuous G
   or exposure below the floor is UNDETERMINED.
6. **Exposure**: the floor of 6 pooled gold `decision` rows at a window head, and that a run
   below it is not started.
7. **n = 15** with the harness refusing fewer, and whether to include the optional `both` arm
   (about +4.2 h).
8. **Held-out**: whether a `SUPPORTED` verdict needs a confirmation on the thesis's held-out
   series (outside this repository) before any default or flag ships.
9. **Gold digests**: that the three SHA-256 values above (copied from #1737) are the intended
   pins, and whether the harness should verify them at load (it does not yet; `--report` and
   the run trust `scoring.load_gold` and the allowlist).
10. **GPU time**: the forecast (about 12 h, ceiling 15 h) and when the GPU is free (reserved by
    the owner). A cheaper variant (fewer runs, one candidate) would be a different registration.
