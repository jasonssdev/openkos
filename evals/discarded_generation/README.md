# `discarded_generation` — what extraction generates and throws away (#692)

Issue #692 observes that extraction generates every candidate in full — type,
title, description and body — and that the judge and the deterministic gates
discard candidates only *after* that text exists. On a local model, generated
tokens are the wall clock, so a discarded candidate is time the user waited
for nothing. Its end-to-end evidence was 13 objects written and thrown away
against 9 kept, on a source that took 4m28s.

```bash
uv run python -u evals/discarded_generation/run_discarded_generation_probe.py --self-test
uv run python -u evals/discarded_generation/run_discarded_generation_probe.py --runs 5
```

**This probe measures the waste and stops there.** The owner ruling for this
round is explicit: measure, do not implement the two-phase extraction #692
proposes. A lever gets built after its measurement exists, never beside it —
the #613/#622/#706 precedent, where three prompt-level treatments were
measured and rejected after looking obviously right.

## The two numbers

Every candidate has a **head** (type and title) and a **tail** (description
and body). A title-first phase 1 would still generate every head, so:

| number | what it is |
| --- | --- |
| **discarded share** | generated candidate text belonging to candidates the run did not retain — the total waste |
| **recoverable share** | the tail half of that — the only part two-phase would avoid generating |

`recoverable` is always the smaller of the two, and the gap between them is
not a rounding detail: it is the part of the waste that a two-phase pipeline
would still pay. Reporting only the first would overstate what the proposed
fix buys.

## Why characters, not tokens

The number that decides anything here is a **ratio**, and a chars-per-token
factor cancels out of a ratio. Measuring in characters therefore removes a
tokenizer dependency, an assumed conversion factor, and a whole class of
argument about which factor is right — while changing no conclusion. The
absolute scale is rendered as approximate tokens once, through a single
constant, for readers who think in tokens; no ratio in the report touches it.

## What it does NOT claim

The recoverable share is an **upper bound on the generation half only**. A
two-phase pipeline adds a second round trip per source, re-sends the window
for the surviving candidates, and pays prompt processing again. None of that
is modelled here. This probe answers "how much is thrown away"; "what would it
cost to stop throwing it away" belongs to whatever follow-up the numbers
justify.

## It rides the #715 recorder rather than copying it

The stage ledger comes from `evals/stage_attrition/`, imported, not copied.
That probe already wraps every pipeline stage, already refuses to run when a
stage it patches has been renamed — a patched-into-nothing stage reads as a
no-op and would exonerate itself — and now snapshots each candidate's
generated size alongside its title. A second recorder would have to re-derive
which candidates a stage dropped, and would drift from the one #715's evidence
was produced with: two ledgers under one name, with no way to tell which
produced which number.

## Accounting rules worth knowing before reading a figure

- **Only minting stages count toward the generated total.** A filtering stage
  contributes nothing, or every candidate would be counted once per stage it
  survived.
- **The union path generates each subject twice and keeps one.** The second
  generation is counted as generated and credited as kept only once — because
  it *is* waste. Crediting both would report a pipeline that discards nothing.
- **Survival is judged against the run's retained objects**, not against any
  intermediate stage. A candidate that survives the judge and then falls to the
  cap was still generated for nothing, and #692 is about generation, not about
  which gate did the discarding.
- **Errored runs are named and excluded, never silently dropped.** A treatment
  that breaks a source looks *better* when its crashed runs stop being counted
  — the gate defect caught twice in the #714/#715 round.
