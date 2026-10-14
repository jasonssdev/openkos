# `transcript_decisions` -- does the decision pass recover hand-annotated decisions? (#1348)

Measures the opt-in transcript decision pass (`extraction/decision_pass.py`,
config key `transcript_decisions`) on [AMI](https://groups.inf.ed.ac.uk/ami/corpus/)
meetings against a human gold set of decision sentences. Manual tool, not
pytest, not shipped. Deterministic scoring only: no LLM judge.

```bash
uv run python -u evals/transcript_decisions/run_transcript_decisions.py --self-test   # no model
uv run python -u evals/transcript_decisions/run_transcript_decisions.py \
    --gold <SERIES>.decisions.adjudicated.csv --transcripts <dir of MEETING.md> \
    --arm pass --runs 5 --model qwen3:8b
uv run python -u evals/transcript_decisions/run_transcript_decisions.py --arm baseline ...
uv run python -u evals/transcript_decisions/run_transcript_decisions.py \
    --gold <csv> --rescore            # recompute from results/*.json, no model
```

**Use `-u`**, as in the other harnesses.

## The data stays outside this repository

The gold CSV (columns used: `decision_id`, `meeting_id`, `evidence_text`,
`final_status`) and the rendered transcripts (`<meeting>.md`, speaker-turn
markdown) live in the maintainer's thesis checkout and are passed by flag. This
harness reads them and copies none of them: stored results hold the evidence
lines the pass produced, the produced titles, and the `decision_id`s they
matched -- never a transcript or a gold annotation.

## Development series only (no override)

`scoring.DEV_SERIES` is a fail-closed allowlist: ES2015, IS1004, TS3009. The
thesis's ADR-0005 holds eleven other series (including TS3005) out for its final
evaluation. This harness tunes and decides OpenKOS behaviour, so scoring a
held-out meeting here would spend that data on a tuning decision. Any meeting
outside the list, or whose id does not parse, is refused before anything is run;
there is deliberately no flag to lift it. (`evals/decision_extraction/` uses
TS3005 for an unrelated question; this harness does not follow it.)

## What a run does

For each meeting: check `concept._is_meeting_shaped` is true and **refuse to run
if it is not** (a gate that never fires would measure a false zero); build the
windows with the helpers the wiring uses (`fans_out`, `_chunk_lines`); call
`run_decision_pass`; match each produced Decision to the gold.

Arms: `pass` is the pass alone (matched through its evidence line); `baseline`
is today's extraction, `extract_concept_union` with the key off (matched through
each Decision's body, which on a transcript is a pasted excerpt). The baseline's
precision is flattered (a body quoting both a decision and a non-decision counts
as a decision hit), so compare **recall** across arms and read baseline precision
as an upper bound.

## Matching rule

A Decision matches a gold dialogue act when the two share a contiguous run of
word tokens that is **at least 4 tokens long** (`MIN_MATCH_TOKENS`) **and covers
at least 60% of the shorter side** (`MIN_MATCH_COVERAGE`). The rule is the
longest common contiguous run, not containment: AMI's dialogue acts and the
rendered transcript's turns cut the same words at different places, so neither
need contain the other. Full containment is the special case of 100% coverage.
It applies identically to both arms (a pass evidence line, a baseline body).
Tokens are casefolded words with speaker labels and `<gap>` tags removed, so
AMI's tokenised text ("it's a remote control .") equals the rendered transcript
("it's a remote control."). The 4-token floor exists because AMI acts include
"yeah" and "okay": shared by countless lines, they prove nothing; the coverage
condition stops four shared words of a long line from counting. The cost is that
a decision whose every act is shorter than 4 tokens cannot be recalled; those
are counted in the denominator and reported (`unrecallable`), so the floor costs
recall, never precision.

`--rescore` recomputes matches under the current rule from the stored evidence.
Baseline bodies are not persisted, so a stored baseline run keeps the matches it
was run with (a note on stderr says so); re-run the baseline arm to apply a
changed rule to it.

## Metrics (per series, mean and min-max over `--runs`)

- **recall** = gold `decision` rows matched by at least one Decision / gold
  `decision` rows.
- **Shared evidence (registered, see the pre-registration).** An adjudicated
  `compuesta` row may have child rows (`<parent>-1`, `<parent>-2`, ...) that carry
  the SAME evidence as the parent. Matching is by evidence, so one Decision
  quoting that line would match every child. `decision` rows of one meeting with
  identical normalized acts therefore form a group, and a group's recall credit
  is min(decision rows in it, DISTINCT produced Decisions of that run and
  meeting that matched it). Recall sums the credits; singleton rows behave as
  before. Groups of two or more rows are reported as `shared_evidence_groups`.
- Produced Decisions, split: `matched_decision`; `matched_non_decision` (a
  `no_decision` or `sin_soporte` row: a false positive against the gold);
  `matched_compuesta`; `matched_descartar`; `unadjudicated` (matched a row with a blank
  `final_status`); `unannotated` (matched nothing).
- **precision-on-annotated** = matched_decision / (matched_decision +
  matched_non_decision). `unannotated` is **not** a false positive: the gold
  covers only AMI's abstractive DECISIONS sentences, so a real decision the
  annotators did not write down lands there. It is reported separately and never
  folded into precision.
- `compuesta` rows (one sentence bundling several decisions) are counted apart
  and excluded from the recall denominator: one Decision cannot be right about
  several, so scoring them against single-decision output would be unfair either
  way.
- `descartar` rows (a truncated, broken or unintelligible sentence, or one that
  repeats a decision already annotated in the same meeting) are counted apart,
  excluded from the recall denominator, and a Decision that matches only such
  rows lands in `matched_descartar`: reported, never a false positive. Bucket
  precedence when a Decision matches several rows: decision > non_decision >
  compuesta > descartar > unadjudicated > unannotated. `compuesta` outranks
  `descartar` because a compuesta sentence does carry real decisions (in its
  children), while `descartar` marks nothing usable.
- **duplicate titles across meetings of a series**: distinct normalized titles
  produced in two or more meetings, and how many Decisions that would merge at
  ingest (`attach_at_ingest` merges on type + title). A proxy for the
  known attach-merge limit; it needs no gold.
- windows, LLM calls, meetings with a `DecisionPassResult.failure`, wall time.

## Unfinished adjudication

A blank `final_status` means "not adjudicated yet". By default the harness
**refuses** and names the blank rows. `--allow-partial` scores the adjudicated
rows only and stamps the output `PARTIAL -- NOT THE PRE-REGISTERED RESULT`, in
the report and in the stored file name. Matches are stored against every gold
row, whatever its status, so `--rescore` after adjudication finishes turns a
partial run into a complete one with no new model call (it stays PARTIAL while
any row is blank or any meeting has no run).

## Not measured here

Whether replacing the main pass's Decisions with the pass's is better end to end
(that is the wiring, covered by unit tests), and anything about the held-out
series.
