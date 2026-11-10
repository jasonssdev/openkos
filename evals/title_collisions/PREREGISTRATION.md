# Pre-registration: cross-series title collisions of the shipped decision pass (#1736)

**Status: PROPOSED. It becomes REGISTERED when the owner has confirmed the section
"The owner confirms" and this file has merged to `main`, before the first model call.**
A change after that point is a new registration, not an edit to this one. It generates no
number itself: it fixes what will be generated, how it will be read, and what each outcome
means. Production code, prompts and defaults are unchanged by this file and by the run.

It reuses the decision-pass harness of `evals/transcript_decisions/` (meeting loading, the
development-series allowlist, the `_is_meeting_shaped` gate, `run_meeting`, the shared
`load_records` and `refuse_duplicate_records`) and the identity stamp of
`evals/harness_stamp.py`. It reads no gold and scores no recall: it counts titles.

## Question

Ingest attaches an extraction candidate to an existing concept of the same type and
normalized title, and compares nothing else (`build_attach_lookup` in
`application/ingest.py`). Question 1 of #1736, whether that attach crosses meeting series, is
answered yes by a characterization test (`tests/unit/application/test_ingest_attach_homonyms.py`).

**Question 2 (this registration):** how often does the real shipped decision pass
(`extraction/decision_pass.py`; the title is the value statement, `compose_title`) produce
Decisions whose `(type, normalized title)` key collides across different AMI meeting series?

The answer decides whether a series guard on attach is warranted (a series key on the Source,
promoted from `evals/series_boost`, with its own pre-registered cost measurement). It changes
nothing here.

## The key is the engine's

The harness imports `normalize_key` from `openkos.resolution.normalize` (the function
`build_attach_lookup` calls at `application/ingest.py`, `groups.get((okf_type,
normalize_key(title)), ())`) and builds the key as `(type, normalize_key(title))`. It does not
reimplement the normalization, so the measurement tracks the shipped key. An item whose
normalized title is empty is skipped, as the engine's lookup skips it. Every pass item is a
`Decision` (`to_result`), so the type component is constant for the run; the counting function
still compares types and the self-test pins that a different type does not collide.

## Subject

- The shipped decision pass, as production runs it: model `qwen3:8b`, the shipped system prompt,
  the shipped windows (`harness.pass_windows`), the shipped exact-subject dedup and cap of 25,
  the re-ask of a failed window on (`retry_after_cap=True`), generation ceiling and context
  window as configured defaults.
- **12 meetings**: ES2015a-d, TS3009a-d, IS1004a-d (the three development series, four
  meetings each), via the fail-closed allowlist `scoring.DEV_SERIES`. The rendered transcripts
  stay in the private thesis checkout; nothing from it is copied into this repository.
- **n = 10 runs.** Each run extracts every meeting once. Sampling is the server's default;
  seeds are not controlled. One Ollama job at a time. The harness refuses `--runs` below 10.

## Measures (per run, then reported across runs)

- **Primary: cross-series collisions** = pairs of Decisions from different series sharing the
  engine's `(type, normalized title)` key. Every unordered pair of items in a key group is one
  pair, so three series on one key give three pairs; a collision is counted once per pair.
- **Within-series cross-meeting collisions**: the same, for pairs from the same series but
  different meetings. **Count only; not judged correct or wrong**, because a decision restated
  in a later meeting of the same project may legitimately attach.
- A pair of items inside one meeting is in neither bucket.
- **Total Decisions** per run, and **failed meeting-runs** (a record whose `failure` is set) per
  run, out of 12.
- **Every colliding title** is listed in the report, per run, with its series and meetings.

## Decision rule (fixed)

On the non-excluded runs, with `mean` the mean cross-series collisions per run:

- **EXPOSED** if `mean >= 1.0`: a series guard is warranted; open a follow-up.
- **RARE** if `mean > 0` and `mean < 1.0`: report; a guard is optional; the owner decides.
- **NOT EXPOSED** if the count is 0 in all 10 runs.

A run where more than 2 of its 12 meeting-runs fail is **excluded and disclosed**, as is a run
that is missing or holds fewer than all 12 meetings. If **more than 3** runs are excluded
(a missing run counts as excluded), the result is **INCONCLUSIVE**; so is a result with no
usable run. The self-test pins each boundary (0, 0.9, 1.0 and the exclusion counts).

## Disclosed before the run

- **The series are not independent projects in content.** AMI series ES2015, TS3009 and IS1004
  are all the same remote-control design scenario, so cross-series overlap of decision
  statements (the same casing, battery or button choice) is a plausible and honest exposure,
  not an artifact. The measurement is of exactly that exposure.
- **Cross-series collisions are counted as wrong attaches**, because different series are
  different teams and projects: a Decision from one should not be merged into another's. That is
  a stated reading, not a judgement made per title. A reader who disagrees can use the listed
  titles.
- Within-series collisions are reported and not scored, for the reason above.
- The unit of exposure is one ingest of 12 meetings in one workspace. A collision here is a
  collision the engine's attach would act on **if** those meetings were ingested into one
  bundle, in whichever order. This does not measure what a real user's corpus holds.
- A failed meeting-run still contributes the items its other windows produced (up to 2 failures
  per run are tolerated); this slightly lowers the total and so the count.
- One model, one machine, `think: false` as configured; the title is a model's wording, so the
  rate belongs to `qwen3:8b` and may not carry to another model.
- No model has answered anything and no Ollama call was made to write this file or the harness.
  The self-test is model-free; it was written first and observed failing, then the counting was
  implemented. Four single-line mutations (the cross-series predicate, the 1.0 boundary, the
  failure bound, and a reimplemented key in place of the engine's) were each caught.

## Ceiling and checkpoints

- **Wall-clock budget: 4 h** (`--budget-hours`, default 4). Forecast: the pass costs about
  933 s for 12 meetings (`evals/transcript_decisions/RESULTS-*.md`), so 10 runs are about
  2.6 h. If the elapsed time plus the last run's duration would pass the budget, the run stops
  before the next one and the report counts the missing runs as excluded; that is a short
  result, not a smaller n.
- **Checkpoint after each run**: a `-PARTIAL` file with every record so far, then a final store
  `collisions-<stamp>-qwen3-8b.json` holding all records.
- **The report reads only the final store.** The #1754 lesson: a glob that also matches the
  `-PARTIAL` checkpoint counts each record twice. `--report` loads through the shared
  `load_records`, which refuses (non-zero exit, `REFUSED`) any input holding one
  (arm, run, meeting) more than once; the report command below names only the final store.

## Exact commands

One Ollama job at a time. Record `git rev-parse HEAD` and `ollama list` first; the harness code
is the registering PR's merge commit with a clean tree. `T` is the directory holding the rendered
transcripts `<meeting>.md` (in the thesis checkout), `OUT` is
`~/Dev/Projects/openkos-eval-runs/title-collisions/`, outside the repository.

```sh
M=evals/title_collisions/measure_title_collisions.py
OUT=~/Dev/Projects/openkos-eval-runs/title-collisions

# model-free, anywhere
OLLAMA_HOST=127.0.0.1:1 uv run python $M --self-test

# the run
uv run python -u $M --transcripts $T --runs 10 --model qwen3:8b --out $OUT

# model-free from here: the counts, every colliding title and the verdict
OLLAMA_HOST=127.0.0.1:1 uv run python $M --report $OUT/collisions-*-qwen3-8b.json
```

The stored file holds, per meeting-run, the produced titles, windows, calls, failure and wall
time, plus the identity stamp (harness commit, model digest, hash of the system prompt). It
holds no transcript text and no gold. `--report` recomputes everything with no model.

## Stopping rules and deviations

- No early stop on a result. The only stops are a crash (re-run) and the time ceiling.
- A deviation (a re-run, a harness fix that does not change a registered quantity, a missing
  meeting-run) is listed in the results note with its reason. A change to the meetings, the
  number of runs, the key, the decision rule or the budget is a new registration.
- The results note applies this file mechanically. It may add readings, not verdicts.

## What a verdict does not establish

That a guard's cost is acceptable (a separate registration against the 7 of 7 correct exact
attaches), that homonyms of the same type inside one series are handled (they need a signal
beyond series, such as body similarity, and probably their own issue), or that a real user's
corpus collides at this rate.

## The owner confirms

1. **Subject and size**: `qwen3:8b`, 12 meetings, 10 runs, shipped pass as it runs in
   production.
2. **The decision rule**: EXPOSED at mean >= 1.0, RARE above 0, NOT EXPOSED at 0 in all 10 runs;
   exclusion above 2 failed meeting-runs; INCONCLUSIVE above 3 excluded runs.
3. **Cross-series pairs are wrong attaches; within-series pairs are counted, not judged.**
4. **Budget**: 4 h wall clock, and when the GPU is free (it is reserved by the owner).
