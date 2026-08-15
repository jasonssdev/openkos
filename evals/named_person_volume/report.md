# `named_person_volume` report — #712 slice 1

**Every number below is re-derivable from `results/*.jsonl` with
`--rescore`.** Model `qwen3:8b`, 3 runs per (fixture, arm), 2 fixtures ×
2 arms = 12 runs attempted, 10 completed without error.

```
uv run python -u evals/named_person_volume/run.py --rescore
```

## VERDICT: REJECT

Two of the four REJECT-rule conditions (design D2) fire independently:

1. **Run latency >= 1.5x baseline** — treatment averaged **104.7s**
   against a baseline of **54.6s** (1.92x, threshold 82.0s).
2. **Merely-named person count did not increase over baseline** —
   treatment **0** <= baseline **0**, after hand adjudication
   (`adjudication.json`).

Subject recall (condition 1 of D2) did **not** independently reject: it
was **0.00 on both arms on both fixtures**, so the treatment did not drop
it *below* baseline — baseline was already at the floor. See "Subject
recall is zero everywhere" below; this is a real, orthogonal finding, not
a treatment-caused regression.

Fabrication (condition 4) did not fire: every proposed name on the
name-bearing fixture (`es-bare`: `Ana`, `Bruno`, `Carla`) is a literal
source name.

**Per design D2: rejection ships nothing prompt-level. The D2 rewrite
stays in `run.py` as `_TREATMENT_CAPTURE_SYSTEM_PROMPT`, a reproducible
monkeypatch, exactly like #613/#622/#630/#706's own rejected treatments.**

## Per-combination metrics (A/B/C/D)

| fixture | arm | ok/err | avg participants (A) | merely-named (B) | subject recall (C) | avg latency (D) | avg produced/retained (D) | judge statuses (D) |
|---|---|---|---|---|---|---|---|---|
| es-bare | baseline | 3/0 | 3.00 | 0 | 0.00 | 52.0s | 3.0/3.0 | ok, ok, ok |
| es-bare | treatment | 3/0 | 3.00 | 0 | 0.00 | 49.9s | 3.0/3.0 | ok, ok, ok |
| ami-ts3005a | baseline | 3/0 | 2.67 | 0 | 0.00 | 57.3s | 2.7/2.7 | ok, ok, skipped |
| ami-ts3005a | treatment | 1/2 | 1.00 | 0 | 0.00 | 268.9s | 1.0/1.0 | error, error, ok |

Overall (both fixtures pooled, ok runs only): baseline latency 54.6s,
treatment latency 104.7s. Overall merely-named: baseline 0, treatment 0.

## Capacity number

```
p_max (treatment) = 3
_PARTICIPANT_BACKSTOP = max(8, ceil(1.5 * 3)) = 8
```

`p_max` is the largest distinct participant count in any single treatment
run, across both fixtures (`es-bare` runs held 3; the one successful
`ami-ts3005a` treatment run held 1). **Derived, not chosen** (design D1):
the eval never picked 8 in advance, it fell out of `max(8, ceil(1.5*3))` —
the floor, not the multiplier, is what actually binds here, because no
measured run came close to it.

## Reject condition 2 in detail: the `ami-ts3005a` reliability cost

Two of three treatment runs on the real AMI transcript failed outright
with `OllamaGenerationCapped` at the shipped 8192-token ceiling — the
SAME ceiling `openkos.yaml.template` ships, so this is not a probe-only
artifact; a real `ingest` over this document under the treatment prompt
would fail identically. Only one treatment run on this fixture completed,
at 268.9s (baseline's slowest completed run was 146.9s). The reported
overall treatment latency (104.7s) is a MEAN OVER SUCCESSFUL RUNS ONLY and
therefore **understates** the treatment's true cost: two of three
AMI/treatment attempts produced nothing at all within the shipped ceiling,
which the latency number cannot represent, only `err=2` next to it.

The baseline arm did not experience a single failure on either fixture.
This asymmetry is real, measured, and reproducible from the stored JSONL
— it is not an artifact of run-to-run model variance alone, since it is
one-directional (favoring baseline) across all three treatment/AMI
attempts.

## Reject condition 3 in detail: adjudication

`adjudication.json` labels every recorded `Person` candidate title
`has-role`, based on the description/body text actually returned:

- `es-bare` (both arms, 5 of 6 total title occurrences): every
  description names a concrete meeting action — "initiated the
  discussion", "reviewed the records", "identified the cause" — an
  anchor by even the SHIPPED baseline prompt's own definition ("spoke in
  this meeting" / "attended" already qualify).
- `ami-ts3005a` (both arms, every run that produced a candidate):
  descriptions state the source's own stated role verbatim (`Project
  Manager`, `User Interface Designer`, `Industrial Designer`, `Marketing
  Expert`).

Net: in THIS sweep, the treatment did not change how much role
information the model volunteered — both arms already state a role or
action for every candidate they retain, on both fixtures. The removed
anchor demand bought no additional merely-named capture here, which is
exactly what condition 3 measures and exactly why it rejects.

**One qualitative exception, reported but not counted**: `es-bare`
treatment run 3 is the sole run in the entire sweep where all three
candidates collapsed to a generic `"Participante en la reunión"`
description — no action, no role, attendance only. This is genuinely
borderline "merely named," but the title-keyed adjudication scheme
(shared with `evals/participant_anchor`, run index deliberately excluded
from the key — the same project convention that lets a title's
description reword run to run without going stale) cannot express "5 of 6
occurrences state a role, 1 does not" under one label. It is labeled
`has-role` (the dominant pattern) and reported here instead of silently
folded into metric B's count. Read as a wording-instability signal on the
treatment prompt, not as measured benefit.

## Subject recall is zero everywhere — a pre-existing finding, not a treatment regression

Metric C (subject recall) is 0.00 on **every** one of the four
combinations, baseline included. Inspecting the raw retained objects
(`results/*.jsonl`) shows why: in every successful run on both fixtures,
`retained` consists ENTIRELY of `Person` candidates — the hand-placed
`es-bare` Decision (context-window fix) and Concept (latency regression)
subjects, and the ground-truth `ami-ts3005a` Event (kick-off meeting)
subject, were never once present in a retained set, under either the
baseline or the treatment capture prompt.

This means the union+judge pipeline, as CURRENTLY SHIPPED (baseline arm,
unmodified production behavior), already retains zero genuine subjects on
both of this eval's fixtures whenever `Person` candidates are also in
play — the judge is choosing participants over subjects wholesale on
these two sources, independent of anything #712 touches. The REJECT
rule's condition 1 ("recall drops below baseline") cannot detect this,
because it can only fire on a RELATIVE drop and baseline is already at
the floor. It does not change the REJECT verdict above (conditions 2 and
3 already reject on their own), but it is a real, orthogonal, and
concerning finding that any slice 2+ author or a future eval should
investigate independently of this change.

## Fixtures

- `es-bare`: constructed Spanish meeting, `Ana`/`Bruno`/`Carla` named and
  (by construction) never anchored beyond meeting-conduct actions;
  name-bearing.
- `ami-ts3005a`: real AMI corpus meeting (`evals/decision_extraction/
  sources/TS3005a.transcript.txt`), single-letter speaker labels, every
  personal name elided by the corpus itself; NOT name-bearing (rule 4
  never applies to it, by construction).

## Task 0.1 — readers of `ExtractionReport.produced`/`.retained`/`.discarded_titles`

Enumerated read-only (grep `src/`, `cli/main.py`, `evals/`), for slice 3's
D3 consequence 1. Genuine `ExtractionReport` readers, all production:

- `src/openkos/cli/main.py` — `_judge_failure_notice` (near line 2909,
  reads `.retained`)
- `src/openkos/cli/main.py` — `_extraction_cap_notice` (near lines
  3087-3095, reads `.produced`, `.retained`, `.discarded_titles`)

Eval/script readers (non-production; slice 3's narrowing to subject-lane
counts changes what these historically measured):

- `evals/extraction_collapse/run_collapse_probe.py`
- `evals/extraction_cap/run_cap_eval.py`
- `evals/decision_extraction/scripts/run_type_coverage.py`
- `evals/participant_anchor/run_participant_anchor_probe.py` (design D3's
  own named consequence-2 site; the `RunRecord.schema` marker is planned
  there in slice 3)
- `evals/named_person_volume/run.py` (this eval) — also stores
  `outcome.report.produced`/`.retained` into its own `RunRecord`. Slice 3
  should decide whether this eval's ALREADY-STORED runs (this report) need
  a retroactive schema marker, or whether this report is closed/historical
  by the time slice 3 lands and is read as `schema: 1` by convention.

**Not readers of `ExtractionReport`** (same field NAMES, different
classes — checked and excluded):

- `src/openkos/cli/curate.py` / `src/openkos/resolution/candidates.py` —
  `find_candidates_report`'s own `.produced`/`.retained` (candidate-group
  resolution report)
- `src/openkos/resolution/edge_typing.py` / `src/openkos/cli/main.py:11915`
  — pass-3 edge-typing's own `.produced`/`.retained`
- `evals/model_spike/run_spike.py`, `evals/model_spike/run_title_ab.py` —
  a local harness field (`outcome.produced`, a list of type/title tuples),
  unrelated to `ExtractionReport`
