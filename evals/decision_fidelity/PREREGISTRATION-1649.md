# Pre-registration: Decision fidelity, concrete values and commitment (#1649)

**Status: Registered when this file merges to `main`; no model run before the merge and
before the gold is labelled (#1556: pre-register, and merge the pre-registration, before any
run).** From the merge, everything below binds; a change after it is a new registration, not an
edit to this one, and any deviation is disclosed in `RESULTS-1649.md`. The owner-confirm items at
the end are confirmed. The model-free
harness (`fidelity_core.py`, `fidelity_ami.py`, `build_sheets.py`, `score_fidelity.py`) is
committed with this file and has been exercised only by its `--self-test` and unit tests.
No sheet has been labelled and no model has run at the time of writing. Part of #1652; refs #1649.

## Question

On the transcript decision pass (`extraction/decision_pass.py`), (a) how often does a stored
Decision state the value the meeting decided, and (b) how often is a discarded or still-open
proposal stored as a Decision? Do two cheap treatments move those numbers without costing
decision recall?

Both are measured **at extraction**, on stored Decisions, never at query time. This measurement
adopts nothing: it changes no production code, default or prompt. A verdict of ADOPT names a
production change that would be a separate pull request with its own spec.

## Why, and what the evidence is not

The issue reports, from a downstream QA smoke run (ES2015, 14 point-fact questions, OpenKOS 0.5.5),
about 3 of 9 failures at extraction: a Decision stored the final cost instead of the cap, or a
discussion without the choice that closed it (a), and a discarded proposal stored as a Decision (b).
That run is evidence for the problem, not a test set: n = 14, one series, a baseline tuned on those
questions. **Nothing here is tuned on the 14 questions or scored against them.** The rejected
alternative recorded in the issue (append the surrounding transcript span to each Decision) turns the
compiled answer into chunk retrieval and is not pursued unless this work fails.

## Which fixture measures (a) and (b)

Checked, in the repository and its pinned data:

| Candidate | Why it does not measure (a) or (b) |
| --- | --- |
| `evals/transcript_decisions/` gold (thesis adjudicated CSV) | Scores whether a decision *sentence* was recalled. Not in this repository, and its rows carry no decided-value labels or commitment labels. Kept as the recall yardstick idea only; its matching rule is reused here. |
| `evals/decision_extraction/` | Type coverage; its ground truth is TS3005, a thesis evaluation series, ineligible. |
| `evals/decision_revisions/`, `decision_granularity/` | Hand-written library-domain fixtures about pairing and splitting; no value or commitment labels. |
| `evals/retrieval_gold/` | Measures retrieval; judgments do not exist yet; not an extraction yardstick. |

So **a new, small labelled fixture is needed**, built on AMI development material and AMI's own
independent annotation:

- **(a) value gold.** AMI's abstractive `decisions` sentences of the 12 development meetings
  (ES2015, IS1004, TS3009, letters a to d), each tied by AMI's `summlink` to the dialogue turns it
  was written from. 77 sentences; 71 are anchored to at least one turn, 6 are not and are listed,
  not scored. The sentences are human-written before this project existed, and several are exactly
  the pattern in the issue ("The control will cost a maximum of twelve Euro and fifty cents",
  against "Product costing is 1.20 Euros" in the next meeting of the series). A labeller adds, per
  sentence, the surface `forms` of the decided value, the `distractors` the same turns carry, and a
  `kind`; a sentence with nothing checkable is `exclude` (still counted for recall).
- **(b) line labels.** A labeller marks each distinct evidence line the arms produce `committed`,
  `discarded`, `open` or `none`, reading the line with three turns of context each side.
- **Cue calibration (pre-run).** The commitment cue is calibrated on six **free-pool** meetings
  (IS1002b, c, d; IS1005a, b, c; in no thesis set) that no arm reads, so the lexicon is frozen on
  text the arms never see.

**Eligibility, fail-closed with no override:** the 12 development meetings carry gold and arms; the 6
free-pool meetings carry only calibration; TS3012 is ineligible (epic #1556); every thesis evaluation
or reserve series is refused. `fidelity_core.ELIGIBLE` is the list and `load_runs` refuses a stored
run that names another meeting.

## Treatments (two, at most)

**T_P, prompt clause (the only arm that needs the model).** Replace the single `- value:` bullet of
`extraction/decision_pass.md` with `prompt_value_clause.md` (committed with this file). It keeps the
shipped words and adds: keep the concrete figure, date or name as said; when a limit or target and an
actual figure are both mentioned, state the decided one and say which kind it is; when a discussion
ends in a choice, state the choice. A unit test pins that it replaces exactly one bullet and extends
it. Targets (a).

**T_G, commitment guard (deterministic, costs no model time).** A Decision is dropped (in production:
demoted to a Concept) when its quoted evidence has no commitment language, as defined by
`fidelity_core.COMMIT_PATTERNS` (will/'ll with a subject, going to, let's, go with, decided/agreed/
settled, must/has to/need to, we are using...) outside a question sentence. A Decision with no
evidence has shown no commitment and is dropped. The lexicon is frozen by this registration before
any labelled line is seen. Targets (b). Because it is deterministic it is applied **offline** to the
stored baseline and prompt runs, which gives a 2 x 2 (`A0`, `A0+G`, `A1`, `A1+G`) for the price of two
model arms.

Why these two and not others: both are cheap and orthogonal (one touches only the prompt text, the
other only admission), and the guard is exactly the "commitment language in the evidence" check the
issue names. Not registered: a per-Decision LLM judge for value or commitment (an extra call per
Decision on a single-GPU budget, a new component, and a second thing to validate); the
transcript-span alternative (rejected in the issue). The issue's "stated value appears in the cited
evidence" check is used here as a **measurement** (`evidence_only`), not a treatment.

## Arms and runs

| Arm | What | Model runs |
| --- | --- | --- |
| `A0` baseline | production `run_decision_pass` over `decision_pass.md` unchanged | 15 |
| `A1` prompt | the same with T_P | 15 |
| `A0+G`, `A1+G` | the guard applied to the stored `A0`, `A1` runs | none |

- **Unit of a run:** all 12 development meetings, windows built by the helpers the production wiring
  uses (`pass_windows`: `fans_out`, `_chunk_lines`; 101 windows per run). 15 runs per LLM arm, the
  floor the project's own memory sets for a few-run arm; runs interleaved `A0, A1, A0, A1...` so
  drift hits both.
- **Model and configuration:** `qwen3:8b` (the production default), digest and prompt hashes in the
  stamp; frozen as #1564: generation ceiling 8192, context window 12288, `chat_timeout` 600 s,
  production `retry_after_cap`. No sampling seed is set (run-to-run variance is real variance).
- **Sources:** the 12 rendered development transcripts pinned by sha256 in
  `evals/retrieval_gold/bundle_sources.json`; the AMI archive pinned by
  `ami_public_manual_1.6.2.sha256`.
- **Seeds:** sheet shuffles, stratum sampling and bootstrap use seed 1649; 10000 resamples.
- **Baseline pin.** The baseline is the unmodified prompt and `decision_pass.py` at the `main` commit
  recorded in the stamp before the first run. ADR-0080 (`selector_judge`, off by default) is being
  implemented in parallel; it does not touch the decision pass, but the pin is checked rather than
  assumed: both arms run in one checkout, and if `extraction/decision_pass.py`, its prompt, or
  `llm/ollama.py` differ at the run commit from the registration commit, RESULTS says so and the
  affected comparison is re-run, not patched.
- **The run driver** (a thin wrapper that loads `A0` or `A1` prompt, calls `run_decision_pass` per
  meeting and writes `runs-*.json` in the schema `fidelity_core.load_runs` reads) is committed in a
  separate change before the first run, with its own `--self-test`; this file fixes only what it
  must do.

## Procedure, in order

1. **Merge this file and the harness.**
2. **Label the value sheet and the cue sheet** (`build_sheets.py value-sheet`, `cue-sheet
   --per-stratum 60`), blind to any model output; the cue and line sheets are labelled **twice**
   independently and disagreements are adjudicated by a third reader (see the deviation below).
   Commit `value_gold.json` and `cue_labels.json`.
3. **Stage 0 (no model).** `score_fidelity.py stage0`. The guard is run only if it passes:
   at least 20 `committed` and at least 20 `discarded`-or-`open` labelled lines, **sensitivity
   >= 0.85** (committed lines that carry the cue) and **specificity >= 0.50** (proposal-class lines
   that do not). Fewer lines is UNDERPOWERED and a miss is FAIL; both mean the guard is not run, and
   that is a result. The gate is read before any arm runs, and its three numbers are recorded.
4. **Run `A0` and `A1`**, 15 each.
5. **Build the line sheet** from both arms' stored runs (distinct evidence lines, arm-blind), label it
   twice, adjudicate, commit `line_labels.json`.
6. **Score** with `score_fidelity.py score`, which prints the contrasts and verdicts below.

## Hypotheses and metrics

Let `d` be treatment minus baseline, with a 95% paired-cluster bootstrap interval `[lo, hi]`.

- **M1, decision recall** = gold items (anchored `decisions` sentences, valued or not) matched by at
  least one Decision, over all gold items. Cluster: the gold item.
- **M2, value fidelity** = valued items whose best matching Decision is `correct`, over valued items
  that were recalled. A Decision is scored on its statement (title, description, value line), never
  on the quoted evidence; `correct` needs a registered form and no distractor. Cluster: the gold item.
- **M3, uncommitted rate** = labelled Decisions whose evidence is `discarded` or `open`, over all
  labelled Decisions. Cluster: the meeting (a Decision has no gold item).

**H_P (prompt).** `A1` raises M2 and does not lose M1. **H_G (guard).** `A0+G` lowers M3 and does not
lose M1. The null is no effect. Margins, chosen before any data:

- **Non-inferiority on M1: `lo > -0.05`.** Five points of recall is about three or four of the 71 items
  per run; a prior pass run recalled about 0.31 of its gold, so five points is a visible cost and a
  larger one would erase the gain the pass exists for.
- **Superiority on M2: `lo > 0` and `d >= +0.10`.** Ten points is about one more value in ten
  recalled Decisions; a smaller gain is not worth a prompt change.
- **Superiority on M3: `hi < 0` and `d <= -0.05`.** Five points fewer proposals stored as Decisions.

**Descriptive only (no verdict hangs on them):** `value_hit_rate` (correct over all valued items, not
only recalled ones, so a treatment cannot look better by recalling fewer), `evidence_only` (the value is
in the quote but not the statement), `mixed`, `wrong`, `discarded` and `none` counts, and per-meeting
rates. The cluster bootstrap treats the gold item (or the meeting) as the unit and averages runs inside
it, which is deliberately conservative: it does not let 15 runs masquerade as 15 independent items.

## Decision rule (fixed)

`score_fidelity.compare` and `fidelity_core.decide_prompt` / `decide_guard` implement this; the unit
tests pin each branch.

- **Prompt: ADOPT** if M1 is non-inferior and M2 is superior. **REJECT** if M1 is clearly inferior
  (`hi < -0.05`) or the M2 interval cannot reach the bar (`hi < +0.10`). Otherwise **INCONCLUSIVE**.
- **Guard: NOT RUN** unless Stage 0 PASSED. **INCOMPLETE** if any produced evidence line is unlabelled.
  Otherwise **ADOPT** if M1 is non-inferior and M3 is superior; **REJECT** if M1 is clearly inferior or
  the M3 interval cannot reach the bar (`lo > -0.05`); else **INCONCLUSIVE**.
- The two treatments are decided **independently**, each against `A0`. Adopting both requires each to
  pass its own contrast. `A1+G` against `A0` is reported, never decided: no interaction is claimed.
- **Fewer than 15 runs in an arm** stamps the report `PARTIAL -- NOT THE PRE-REGISTERED RESULT`.
- INCONCLUSIVE and NOT RUN are legitimate outcomes and are reported as such. No power calculation is
  made: how many valued items the labeller finds is not known until the sheet is labelled, and the
  interval, not a prior power figure, decides.

## GPU forecast

Only `A0` and `A1` need the model: 2 arms x 15 runs x 101 windows = 3030 window calls. The stored
decision pass runs on ES2015 (33 windows) took 132 to 367 s a run (RESULTS-1348), so about 4 to 11 s per window;
a capped window costs about 250 s (#1564).

| Stage | Forecast |
| --- | --- |
| Labelling, Stage 0, line sheet, scoring | no GPU |
| 30 run-sets x 101 windows, no caps (4 to 11 s) | 3.4 h to 9.3 h |
| Cap tail: each 1% of the 3030 calls that cap (30 calls x ~250 s) | +2.1 h per percentage point |
| **Total** | **about 5.5 to 12 h for 1% of calls capping; stop and report short at 15 h** |

Ollama serializes; nothing else may use it during the run. The window count is exact (measured on the
rendered transcripts); the per-window times are the stored runs', not a measurement of this prompt.

## Deviations, stated in advance

- **The labels are written by a Claude model, not the owner**, on the owner's standing delegation. The
  cue and line sheets are labelled twice independently and disagreements resolved by a third reader
  blind to which labeller said what; agreement (Cohen's kappa on committed versus not) is reported. The
  value sheet is labelled once and the labeller's `exclude` list is shown to the owner. If kappa on
  committed versus not is below 0.6, the guard's verdict is downgraded to exploratory in RESULTS.
  Agreement is measured against a model, so it describes two readings of one rubric, not the owner's
  judgment.
- The cue lexicon was written by the registrant after reading the failure patterns in the issue and
  looking at no labelled data.

## What this cannot show

- **Answer quality.** Nothing here runs `query`. A Decision that states the right value is necessary,
  not sufficient, for a correct compiled answer (retrieval, #1646 to #1648, is separate).
- **The default extraction path.** The decision pass is opt-in (`transcript_decisions`, off by default).
  The downstream failures came from a default run, whose Decisions have no verbatim evidence line, so
  neither treatment can be applied or measured there. A verdict covers the pass only.
- **Held-out series, other models, other languages.** One model, English transcripts, development series
  only; the commitment lexicon is English.
- **The cost of demotion.** The guard is scored as a drop; what the demoted Concept is worth is not
  measured.
- **Selection in M2.** It is conditional on recall; `value_hit_rate` and the M1 margin bound the effect
  but do not remove it.
- **Gold size.** 71 anchored sentences, of which only those with a checkable value count for M2; the
  intervals may be wide and the AMI summary writers' choice of what to record limits what is gold.
- **Truth.** OpenKOS preserves representations; a `committed` line is one the participants settled in the
  transcript, not a correct or implemented decision.

## Owner-confirm items (confirmed)

All six confirmed 2026-10-27 under the owner's standing delegation to take recommended defaults.

1. **Scope to the opt-in pass: yes.** The default extraction path has no evidence line to check; a
   second registration could cover it.
2. **Margins as written** (`-0.05` recall, `+0.10` value, `-0.05` proposals).
3. **15 runs per LLM arm.**
4. **Labelling scheme as written:** cue and line sheets labelled twice by Claude, value sheet once.
5. **Production guard = demote to a Concept**, scored here as a drop. The demotion is to be confirmed
   again before any production change.
6. **Label the gold after merge, before any run, blind to outputs.**
