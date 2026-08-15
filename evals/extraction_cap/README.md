# `extraction_cap` — the cap-and-decay harness for #404

Scores real extraction runs over [`examples/extraction-corpus/`](../../examples/extraction-corpus/)
against its hand-written ground truth. Manual tool, not pytest, not shipped.

```bash
uv run python -u evals/extraction_cap/run_cap_eval.py --self-test  # no model needed
uv run python -u evals/extraction_cap/run_cap_eval.py              # baseline arm
uv run python -u evals/extraction_cap/run_cap_eval.py --baseline --temperature 0.1
```

**Use `-u`.** Piping a sweep through `tee` or a log file makes Python
block-buffer stdout, so nothing appears until the run ends. A sweep takes tens
of minutes and a silent one is indistinguishable from a hung one — which is
exactly the wrong thing to have to guess about, given how often this material
makes the model fail to terminate.

## Sizing a run

**`--runs 5` is not enough to conclude anything.** Two identical 5-run sweeps of
`large-03-skills-vs-tools` at baseline gave mean produced 6.80 vs 8.20, recall
0.77 vs 0.63, and `cap_cost` 1.00 vs 0.40 — with one run emitting 20 objects
where the other sweep's maximum was 9. The run-to-run variance on this material
is the same size as the effects being measured. Use 15+ per cell before
arguing about a cap value.

**Timeouts dominate wall-clock, not extraction.** Every successful run observed
so far finished in under 60 s; the failures are non-terminating generations
that burn the full timeout (#405). At `--timeout 600` a single bad cell can add
an hour. `--timeout 180` has never truncated an observed success and cuts that
waste to a third — just read the resulting rate as "did not finish in 180 s".

Arms compose: `--baseline` ADDS the model-default arm to any `--temperature`
arms rather than being replaced by them, so the two columns of #404's own
tables land in one report. Omitting both still gives baseline alone.

## The two numbers it exists to produce

**`cap_cost`** — genuine subjects the model DID produce and
`_MAX_OBJECTS_PER_SOURCE` then threw away, named rather than counted. This is
the cap-too-low half of #404. It is deliberately measured against what the
model produced, not against the full ground truth: a subject never proposed was
missed by extraction, a different defect with a different fix, and blaming the
cap for it would recommend raising a ceiling that was never the constraint.

**The position curve** — at each reply position `k`, how often that slot held a
genuine subject, a known facet, or something unjudged. `extract_concept`'s
docstring claims "the model front-loads genuine subjects and degrades into
facets of the last one afterwards, so reply order correlates with quality", and
that "any future ranking has to be measured AGAINST this prefix rather than
assumed better than it". That claim is currently asserted. This curve is the
evidence for or against it, and the baseline any ranking proposal must beat.

## Matching is exact-only, on purpose

A produced title counts as a subject only on an exact normalized comparison
(strip, casefold, collapse whitespace, drop wrapping backticks/quotes) or on a
**human-curated alias** written into the ground-truth file. There is no fuzzy
matching, no token overlap, no embedding similarity anywhere in the harness.

The reason is concrete. `Skill Creation Process` is a *facet* of the subject
`Skill Creator`; every similarity metric scores that pair high. A fuzzy matcher
would credit the facet as a subject, and the harness would then report the
defect as smaller than it is. A scorer that can be generous in the direction of
the hypothesis measures nothing. The self-test pins that exact pair.

The cost is real and is paid on purpose: a genuine rephrasing (`PowerPoint
Skill` for the subject `PowerPoint Presentation Skill`) scores as a **miss**
until a human says otherwise. The bias runs AGAINST the hypothesis, which is
the safe direction for a measurement.

## Five verdicts, counted apart

There are four distinct ways for a produced title to not be a subject, and
folding any two together lets one failure inflate another's number:

| mark | verdict | what it means |
| --- | --- | --- |
| `S` | subject | a genuine subject, by title or curated alias |
| `F` | facet | decay — a subject shredded into attributes. The argument AGAINST raising the cap |
| `D` | near-duplicate | a second object re-naming a subject already emitted. Burns a cap slot without adding knowledge |
| `X` | out of scope | something the document mentions but is not about |
| `?` | unjudged | nobody has ruled on it yet |

`D` and `X` matter because both would otherwise land in `F` and read as decay.
A run that spends two cap slots naming one subject twice is not decaying, and
a fix aimed at decay would not help it.

## The adjudication queue is the workflow

Every title matching no list is printed at the end of the report with its
frequency. Work it by hand, editing the ground truth:

| what it is | where it goes |
| --- | --- |
| a rephrasing of a listed subject | `## Aliases` — `Canonical Title \| the rephrasing` |
| a second name for a subject the same run already emitted | `## Near-duplicates` — `Canonical Subject \| the duplicate` |
| a facet of some subject | `## Facets, not subjects` |
| mentioned but not what the doc is about | `## Out of scope` |
| a subject nobody listed | `## Genuinely distinct subjects`, and bump `**Count:**` |

Alias against near-duplicate is the call that matters most: an alias is free,
a near-duplicate costs a cap slot. Filing the second as the first hides a real
defect.

Scores sharpen each round, and the judgment stays human. Ground truth "cannot
be model-generated" (corpus README) — that applies to the matcher too.

## Rescoring: an adjudication must change exactly one thing

Every sweep saves its RAW observations to `results/runs-<stamp>-<model>.json`,
always, never behind a flag. After editing a ground truth:

```bash
uv run python evals/extraction_cap/run_cap_eval.py \
  --rescore evals/extraction_cap/results/runs-20260805T233000Z-qwen3-8b.json
```

Zero model calls. The verdicts are recomputed from the ground truth on disk
right now — saved runs deliberately do NOT carry verdicts, which would let a
stale judgment travel with the data and outvote the file a human just edited.

This is not only a speed trick. Re-running the model to see the effect of a
human judgment moves two variables at once, and sampling variance on this
material is large enough to swamp the adjudication. Replaying the same bytes
is the only way to attribute a change to the judgment.

## Ground-truth sections this harness added

`## Aliases` and `## Out of scope` are absent from `corpus.py`'s stub because
they are only ever written from a real run's queue. `## Near-duplicates` was
already in the stub as prose; it now takes the same pipe form so it can be
scored instead of only read.

```markdown
## Aliases

- PowerPoint Presentation Skill | PowerPoint Skill

## Near-duplicates

- Pre-built Skills | Document Skills

## Out of scope

- MinerU
```

The parser rejects an alias or a near-duplicate naming a subject that does not
exist, a near-duplicate line missing its pipe, and any file whose
`**Count: N**` disagrees with its bullet count — a half-edited ground truth
would otherwise skew every recall figure by a silent constant.

### `## Path invariant` (#726)

A fixture whose numbers only mean something on ONE extraction path declares
it, and the sweep refuses to run when the source no longer takes that path:

```markdown
## Path invariant

- chunked
```

Optional, and exactly one bullet — `chunked` or `whole-document`. Before the
first call, `preflight_path_invariants` asks
`concept._chunk_threshold_for` which path each selected fixture actually takes
under each arm's title, and aborts with exit 2 naming the fixture, both paths,
the size and the threshold. It never restates a constant: the boundary
branches on shape since #714 (12 000 for a meeting-shaped source, 18 000
otherwise), so a guard carrying its own copy would drift from the thing it
watches.

`medium-10-reunion-plataforma` is why this exists. It sits **718 characters**
above `_MEETING_CHUNK_THRESHOLD` so it takes the chunked path where #699's
fragmentation lives, and until #726 the only thing protecting that was a
paragraph in its ground truth asking the reader to re-check. Drop it below the
line and every number still renders, under the same fixture name, describing a
different pipeline.

Note the unit: **characters**, not bytes. Production compares
`len(source_text)`, and that fixture is 12 718 characters against 12 948
bytes.

## Two things it refuses to do

**No corpus-wide average.** `small-04-pre-build-skills` is the same lesson as
`large-03-skills-vs-tools`, in Spanish at ~45% of the length; both ground
truths forbid averaging them. Rather than special-case the pair, the harness
never aggregates across fixtures at all — three documents of different sizes
and languages were never a population to take a mean over.

**No silent blame for the twin rule.** A ground-truth subject whose title
normalizes equal to `derive_source_title(source)` is flagged in its fixture's
section, because `_drop_source_title_twins` deletes such an object whenever
another survives. `medium-08-sdk-skills` documents that collision on purpose
and demands the check; the harness performs it automatically so a miss there is
never recorded as an extraction failure.

Since #413 the flag excludes `Procedure` subjects: the rule exempts that type
outright, exactly because a tutorial's primary object is the one its title
names. Excusing a miss on a `Procedure` would now hide a real extraction
failure behind a rule that cannot fire on it.

## Lever arms (`--lever`, #699)

Window-size levers, each measured as its own row against the same baseline in
the same sweep:

```bash
uv run python -u evals/extraction_cap/run_cap_eval.py \
  --fixture medium-10-reunion-plataforma --runs 8 --union-judge on \
  --lever chunk:6000 --lever chunk:8000
```

**Levers are never crossed with each other** — #699 asks for them measured
separately, a crossed cell answers a third question ("do they compose?") while
making neither of the first two readable, and on a fixture this slow it doubles
the sweep for an answer nobody asked for.

A second lever, `carry-titles`, was measured here on 2026-08-15 and
**rejected**: it amplified the fragmentation it was built to remove, from 0.12
to 0.62 redundant subject emissions per run. Its axis was removed with the
production mechanism it drove; both are preserved on the annotated tag
`experiment/699-carry-titles`. See [`report-699-levers.md`](report-699-levers.md).

The untreated row is always present and always first. A lever's number means
nothing without the baseline it is read against, and the run-to-run variance
on this material is large enough that yesterday's baseline from another sweep
is not a safe comparison.

**The arm has to bite, and that is checked.** `_chunk_lines` used to take
`target: int = _CHUNK_TARGET` — a signature default, bound once when the
function is defined — so setting the module constant chunked at 4 KB while the
report said 8 KB. That is the inert-arm defect a reviewer caught in the #714
probe, and it is the worst kind: it emits a full set of plausible numbers for a
treatment that never ran, and nothing in the output looks wrong. Production now
reads the constant at call time (pinned by
`test_chunk_target_is_read_at_call_time_not_bound_at_definition`), and this
harness's self-test fails if either lever goes inert or leaks past a run.

**Adjudicate before comparing arms.** A treatment that coins new titles leaves
them `UNJUDGED`, and unjudged positions are excluded from the precision
denominator — so an unworked queue can flatter exactly the arm that changed the
most. Work the queue, then `--rescore` the saved runs; that replays the same
bytes against the edited ground truth with zero model calls, which is the only
way to attribute a movement to the judgment rather than to resampling.

## Sampling arms

`OllamaClient` exposes no temperature knob, and this harness does not add one —
a measurement tool must not widen the production surface to make itself easier
to write. `--temperature` injects `options.temperature` through the client's
already-public `urlopen` seam, rewriting `/api/chat` payloads only. Zero
production bytes touched. Omitting the flag gives the `baseline` arm, matching
the column name in #404's own tables so a new run is comparable to those
numbers rather than to a fresh baseline.

## Reproducibility

`examples/extraction-corpus/sources/` is git-ignored (third-party material this
project cannot relicense), so a fresh clone parses the ground truth but skips
every fixture with a "source not present" note instead of crashing. Others
reproduce the METHOD, not the exact numbers. Same trade the corpus makes, for
the same reason.

## `measure_acronym_fabrication.py` — a ground-truth-free fabrication probe

`python evals/extraction_cap/measure_acronym_fabrication.py` answers issue
#423's fabrication half from data already on disk: it scans every
`runs-*.json` under `evals/` and makes **zero model calls**.

It needs no ground truth, which is the point. The test is
**self-contradiction**: an acronym the extractor expands parenthetically
should expand the same way every time, so two distinct expansions of one
acronym within a fixture prove at least one emission is fabricated. That
keeps it immune to the trap the adjudication queue creates elsewhere in this
harness, where a fixture's numbers are under-reported until a human has
worked its queue — a comparison across fixtures at different adjudication
depths is invalid, but a comparison of a fixture against *itself* is not.

Measured on the stored runs (2026-08-09), the result is one-sided:

| fixture | language | emissions | distinct expansions |
| --- | --- | --- | --- |
| `large-03-skills-vs-tools` | EN | 40 | 1, always correct |
| `medium-08-sdk-skills` | EN | 52 | 1, always correct |
| `medium-09-sdk-skills-notes` | EN | 10 | 1, always correct |
| `small-04-pre-build-skills` | **ES** | 11 | **5, none correct** |

102 English emissions with zero fabrications against 11 Spanish emissions
with zero correct ones. The correct expansion never appears in Spanish at
all. This is why #423 is treated as language-specific rather than as a
general hallucination bug: the English base rate is not low, it is zero.

## `measure_title_containment.py` — scoring a twin-rule widening before writing it (#722)

Zero model calls. Replays the stored `results/runs-*.json` plus the transcript
probes that share `evals/participant_anchor`'s fixtures, and asks what a
CONTAINMENT twin rule would delete that production keeps today.

Written to answer #722 (a title FRAGMENT escapes `_drop_source_title_twins`,
which tests normalized equality) on the #613 / #622 / #630 / #699 precedent:
a deterministic treatment is scored against stored data first, and one that
fails ships as a measurement.

```bash
uv run python evals/extraction_cap/measure_title_containment.py --floor 3
```

The answer was **REJECTED** — 110 adjudicated ground-truth subjects deleted
against 1 true positive, because a descriptive source title contains its
subjects by construction. See `report-722-containment.md`.

Two properties worth reusing in any successor:

- It models the WHOLE rule at the RUN level — the `Procedure` exemption and
  both floors — not just the string comparison. Scoring the comparison alone
  counts deletions the shipped rule never performs, which is the reading error
  `extraction_collapse`'s `title_twin_runs` made before it consulted the type.
- Every verdict prints its own denominator. The meeting-shaped narrowing scores
  zero false positives, and that zero is worthless: it was offered 217 objects
  across both pools and matched one. `eligible` is printed so that reads as
  UNFALSIFIABLE rather than as a cleared bar.
