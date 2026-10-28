# `decision_fidelity`: does a Decision keep its value, and is it really a commitment? (#1649)

Two extraction failure classes found downstream, measured at extraction (never
at query time), on **development material only**:

- **(a) value fidelity.** A Decision drops or misstates its concrete value (the
  final cost stored instead of the cap that was decided; a discussion stored
  without the choice that closed it).
- **(b) proposal versus commitment.** A discarded or still-open proposal is
  stored as a Decision, with no commitment language in its evidence.

This directory holds the **model-free parts and a DRAFT pre-registration**
([`PREREGISTRATION-1649.md`](PREREGISTRATION-1649.md)). No model has been run and
no sheet has been labelled. Part of the umbrella #1652; see the pre-registration
for the hypotheses, arms, decision rule, forecast and what this cannot show.

```bash
uv run python -u evals/decision_fidelity/build_sheets.py --self-test    # no model
uv run python -u evals/decision_fidelity/score_fidelity.py --self-test  # no model
```

| File | Role |
| --- | --- |
| `fidelity_core.py` | Pure scoring: eligibility allowlist, value check, commitment cue and guard, `score_run`, clustered bootstrap, the decision rule. Standard library only. |
| `fidelity_ami.py` | Reads the AMI archive and rendered transcripts; builds the three blind sheets; imports a filled sheet. |
| `build_sheets.py` | CLI for the sheets and the import, plus a synthetic archive for the tests. |
| `score_fidelity.py` | CLI for the pre-run gate (`stage0`) and for scoring stored arms (`score`). |
| `run_decision_fidelity.py` | The run driver: the `A0` and `A1` model arms, interleaved, resumable, stamped, stopped at the registered budget; writes the `runs-A0.json` and `runs-A1.json` that `score_fidelity.py` reads. The only file here that calls a model. |
| `prompt_value_clause.md` | The treatment text for the prompt arm: the one `value:` bullet of `decision_pass.md`, replaced. |

## Eligibility (fail-closed, no override)

The twelve development meetings (ES2015, IS1004, TS3009, letters a to d) carry
the gold and the arms. Six free-pool meetings (IS1002b to d, IS1005a to c) carry
only the calibration of the commitment cue. Everything else is refused before a
file is opened: every thesis evaluation or reserve series, and TS3012, which is
ineligible by epic #1556. `fidelity_core.ELIGIBLE` is the list; a stored run that
names another meeting is refused by `load_runs`.

## The data stays outside this repository

The AMI archive (`ami_public_manual_1.6.2.zip`, pinned by
`evals/decision_extraction/ami_public_manual_1.6.2.sha256`) and the rendered
transcripts are passed by flag. Stored results hold the produced titles,
descriptions and bodies, never a transcript. The sheets are working files for
the labeller and are not committed; the committed artifacts are the labels
(`value_gold.json`, `cue_labels.json`, `line_labels.json`), which carry anchors
by turn index and lines by token hash.

## The sheets

```bash
uv run python -u evals/decision_fidelity/build_sheets.py value-sheet --ami-zip <zip> --transcripts <dir> --out <dir>
uv run python -u evals/decision_fidelity/build_sheets.py cue-sheet   --ami-zip <zip> --transcripts <dir> --out <dir> --per-stratum 60
uv run python -u evals/decision_fidelity/build_sheets.py line-sheet  --runs runs-*.json --transcripts <dir> --out <dir>
uv run python -u evals/decision_fidelity/build_sheets.py import-value  --sheet value-sheet.json --filled filled.json --labeller <name> --out value_gold.json
uv run python -u evals/decision_fidelity/build_sheets.py import-labels --sheet cue-sheet.json   --filled filled.json --labeller <name> --out cue_labels.json
```

- **Value sheet.** One item per AMI abstractive `decisions` sentence of a
  development meeting that `summlink` ties to dialogue turns (71 of 77; the
  other 6 are listed, not sheeted), with the anchor turns and two neighbours on
  each side. The labeller writes the `forms` a Decision would use to state the
  decided value, the `distractors` (other values the same turns carry), a
  `kind`, and `exclude` when nothing checkable is decided.
- **Cue sheet.** Turns of the calibration meetings from three strata the
  labeller never sees (linked to a decision sentence, proposal-marked, cued but
  unlinked), kept in a separate `cue-strata.json`.
- **Line sheet.** The distinct evidence lines the arms produced, across both
  arms, with context and no arm or run on it.

Labels are `committed`, `discarded`, `open` or `none` (rubric on each sheet).

## The run driver

```bash
uv run python -u evals/decision_fidelity/run_decision_fidelity.py --self-test    # no model
uv run python -u evals/decision_fidelity/run_decision_fidelity.py --transcripts <dir> --preflight   # no model
uv run python -u evals/decision_fidelity/run_decision_fidelity.py --transcripts <dir> --wait-gpu
```

`--preflight` checks eligibility, the registered configuration, the sha256 pin of
every development transcript and the window count, and calls no model. The real
run stores one file per (arm, run, meeting) cell under `--out-dir` (default
`~/openkos-eval-runs/decision-fidelity/runs`, never inside the repository), so
the same command resumes after a stop; it halts at the registered 15 h of
accumulated model time and marks the arms `PARTIAL -- NOT THE PRE-REGISTERED
RESULT`. A resume refuses when the model digest, a prompt hash or a pinned source
file moved under the stored cells.

## What the scorer does

A produced Decision is matched to a gold item through its **quoted evidence
line** (the #1348 rule: a common run of at least 4 tokens covering 60% of the
shorter side), then scored on its **statement** (title, description and the
value line of the body), never on the quote. A value that survives only inside
the quote is counted apart as `evidence_only`: it is the failure being measured.

Per item the best verdict among its matching Decisions is kept: `correct` (a
form, no distractor), `mixed` (both), `wrong` (a distractor only), `missing`.
The commitment guard is deterministic, so it is applied offline to stored runs
(the `baseline+guard` and `prompt+guard` rows of the `score_fidelity.py`
report), not run as a model arm.

## Labels (`labels/`)

- `value_gold.json`: 71 items, one blind labeller (a Claude Opus reading), 3 marked `exclude`.
- `cue_labels.json`: 159 calibration turns (40 committed, 79 open, 35 none, 5 discarded).
  Two blind readers (Claude Opus, Claude Sonnet) agreed on 140 of 159 (raw agreement 0.881,
  Cohen's kappa 0.811); a third blind Opus reading, unaware of who said what, adjudicated the
  19 disagreements. Agreed items keep the shared label.
- `stage0.json`: the pre-run gate over those labels: 40 committed, 84 proposal-class,
  sensitivity 0.475, specificity 0.560, verdict **FAIL** (gate: sensitivity >= 0.85,
  specificity >= 0.50). Per the registration, the guard arm is not run.

Deviation (pre-registered in advance, see "Deviations"): the labels are written by Claude
models on the owner's delegation, not by the owner. Kappa describes two readings of one
rubric, not the owner's judgment. Kappa 0.811 is above the 0.6 downgrade threshold.
