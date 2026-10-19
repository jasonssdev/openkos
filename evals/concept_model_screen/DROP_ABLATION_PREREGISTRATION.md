# Pre-registration: which deterministic drop costs window lift (#1572)

**Status: REGISTERED 2026-10-19, before any per-rule number was computed.**
This file is merged to `main` before scoring. A change after that point is a
new registration, not an edit to this one.

## Question

The per-stage loss table from the #1557 screen (`RESULTS.md`, "Per-stage
loss") shows the deterministic drops (L7) costing window lift: -0.0088 on
`qwen3:8b`, -0.0111 on `qwen3.6:35b-a3b` and -0.0158 on `gemma4:26b-a4b`
(round 1, n = 1). L7 lumps several rules together. Which rule costs the lift,
and does each rule earn its place?

## No generation

- Only the #1557 records and caches in
  `~/Dev/Projects/openkos-eval-runs/concept-screen/` are read.
- No chat call and no embedding call is made. Embeddings are read from the
  screen's `bge-m3` cache (`cache/embed.sqlite`, digest `790764642607`, the
  digest registered for #1557). The scorer's embedding function raises on a
  cache miss instead of calling a model. A miss stops that measurement, and
  the part is recorded as deferred, not computed some other way.
- Anything downstream of the selector judge is out of scope. Toggling a rule
  changes the judge's input, and the judge's reply to that input was never
  generated. Effects on the judge stage and the final set are therefore
  deferred. The figures here are **stage-local**: L6 and L7, as #1559
  measured them.

## Data

- Pool DA (ES2015a, IS1004b, TS3009c, ES2015d, IS1004d).
- **Primary:** round 1, run 1, all five models. These are the figures the
  issue quotes.
- **Secondary:** runs 1 and 2 for `qwen3:8b` and `qwen3.6:27b`, the only
  models with two runs.
- Each record's `raw` stage (per window) is replayed through production's
  own functions from `openkos.extraction.concept`, with nothing
  reimplemented.

## Rules toggled

Each toggle turns one rule off and keeps every other rule on, in
production's order for the chunked path: strip, then `_dedup_merged`, then
`_drop_wrong_language_titles`, then `_drop_framing_objects`, then
`_drop_source_title_twins`.

| Toggle | Rule turned off | Stage |
|---|---|---|
| `dedup` | `_dedup_merged` (keep-first by type and normalized title) | L6 |
| `language` | `_drop_wrong_language_titles` (wrong-language and recombined) | L7 |
| `framing` | `_drop_framing_objects` | L7 |
| `twins` | `_drop_source_title_twins` | L7 |

**Caps.** The 24-candidate ceiling (L8) and the 20-object backstop (L11)
are not replayed. Their input is downstream of participant capture (L8) or
of the judge (L11). Their stage-local effect is read from the recorded stages
(`pre_ceiling` to `ceiling`, `judge` to `final`) and reported as it is.

**Validity check, before any number is read.** For every record, the
all-rules-on replay must reproduce the recorded `dedup` and `drops` stages
exactly: the same titles, types and windows, in the same order. A record that
fails is excluded and listed. If more than one record fails, the run is
invalid and nothing is concluded.

## Measures

For each model and each toggle:

- **Lift delta**: window lift with the rule off, minus window lift with
  every rule on (the recorded `drops` stage). Window lift is the #1557 primary
  (`window_lifts` and `screen_metrics.lift`, unchanged), averaged over the
  windows that hold gold. A positive delta means the rule costs lift. The 95%
  interval is the paired two-stage bootstrap the screen used (meetings, then
  windows, 10,000 resamples, seed 42).
- **Objects dropped**: objects present with the rule off and absent with all
  rules on, summed over meeting-runs.
- **Windows emptied**: gold windows that have at least one object with the
  rule off and none with every rule on. An empty window scores lift 0, so
  this is a mechanism the metric can reward or punish independently of
  content.

The L7 loss for a model is lift(dedup stage) minus lift(drops stage). It
should match the published table. The same applies to L6.

## Decision rules

1. **Responsible rule.** A rule is named responsible for the L7 loss if its
   toggle restores at least 50% of the L7 loss on at least 2 of the 3 models
   the issue names (`qwen3:8b`, `qwen3.6:35b-a3b`, `gemma4:26b-a4b`). If no
   rule meets that bar, the loss is recorded as **spread thinly**. Rules can
   overlap: an object both rules would drop is restored by neither toggle
   alone. The overlap is reported.
2. **Benefit audit.** Every object a rule drops is classified from its type,
   title and description. This happens before its lift contribution is
   computed. The classes are:
   - **target**: the class the rule is documented to remove. For `framing`,
     an object that names the gathering itself as a container: its
     description is about the meeting as an event, not about a subject the
     meeting discussed. For `twins`, a restatement of the source title. For
     `language`, a wrong-language title. For `dedup`, an exact duplicate.
   - **false positive**: a subject the rule removed only because its title
     contains the trigger. For example, a decision whose title happens to
     contain "kickoff".

   The classified titles are published with the results.
3. **Verdict per rule.**
   - **Sound**: every dropped object is target. A lift cost then comes from
     the metric crediting a container's summary-like description. It is not a
     defect of the rule, and it is recorded as such.
   - **Fix issue**: at least one false positive, and the false positives
     carry a positive share of the rule's lift cost. One follow-up issue is
     filed per rule. It proposes the change and does not make it. No default
     changes here, and a default change would need an ADR.
4. **Secondary (n = 2)** is reported only. It does not change a verdict.

## Disclosed before scoring

While the harness was being read, one record's `dedup` and `drops` title
lists were printed (`qwen3:8b`, ES2015a, run 1). Its `framing`-shaped object
(`Product Development Meeting`, an `Event`) was the one removed. No lift was
computed.
