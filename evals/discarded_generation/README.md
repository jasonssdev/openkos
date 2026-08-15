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

## Measured, 2026-08-15 (`qwen3:8b`, 6 runs per fixture)

| fixture | discarded share | recoverable share | killed by a title-only gate |
| --- | --- | --- | --- |
| `es-anchored` | **0.93 ±0.01** | 0.92 ±0.01 | **100%** |
| `es-bare` | **0.79 ±0.05** | 0.78 ±0.05 | **97%** |
| `ami-ts3005a` | **0.53 ±0.08** | 0.52 ±0.08 | **81%** |

**The waste is real, and it is concentrated rather than spread.** On
`es-anchored` the discarded characters are 11 389 ±59 while total generation
swings between 12 183 and 12 375 — the same framing object, generated once per
union pass, with a body of roughly 5 600 characters. #692 frames the defect as
"13 objects written and thrown away"; on these fixtures it is one expensive
object produced twice.

**81–100% of the discarded tail dies in `_drop_framing_objects`**, whose verdict
is `result.type == _TWIN_EXEMPT_TYPE or not
_MEETING_SHAPED_TITLE_RE.search(result.title)` — type and title, never
`description`, never `body`. That is the specific finding that makes #692's
two-phase proposal worth costing: the gate that does almost all of the
discarding could have run before the discarded text existed.

The judge accounts for the rest (0–19%), and it is a different question. It is
a model call over the candidate list, so what it needs in order to choose is
not something this probe can answer.

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
