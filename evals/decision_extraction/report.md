# Type coverage over AMI `TS3005a` / `TS3005b` — 2026-08-06

`qwen3:8b`, 3 runs per source, real `extraction.concept.extract_concept` seam.
Reproduce with the two commands in [README.md](README.md).

Filed as [#454](https://github.com/jasonssdev/openkos/issues/454).

> **This supersedes a first run whose transcripts were silently truncated.**
> The segment→word range resolver indexed only lexical elements, so any range
> whose endpoint landed on a `<disfmarker>` or `<vocalsound>` was dropped —
> about a fifth of every transcript. `TS3005a` went from 229 to 288 turns and
> `TS3005b` from 524 to 694 once fixed. The numbers below are the complete
> inputs; the earlier ones described a corpus that was never measured on
> purpose.

## Result

| Source | Size | Objects per run | Types emitted (3 runs) |
|---|---:|---|---|
| `TS3005a.summary` | 1.1 KB | 4, 4, 4 | `Concept` 3 · `Decision` 3 · `Event` 3 · `Project` 3 |
| `TS3005a.transcript` | 16.4 KB | **1, 1, 5** | `Concept` 2 · `Decision` 1 · `Event` 3 · `Project` 1 |
| `TS3005b.summary` | 2.5 KB | 1, 1, 1 | `Project` 3 |
| `TS3005b.transcript` | 40.8 KB | 1, 1, 1 | `Event` 3 |

**The cap is never involved.** `_MAX_OBJECTS_PER_SOURCE` is 6 and every run
reports `n proposed, n retained`. Nothing was truncated away.

## Two findings, and the second is the sharper one

**1. `TS3005b` collapses to one object in both variants.** Its 40.8 KB
transcript yields a single `Event` in all three runs, and its 2.5 KB summary a
single `Project`. Neither is a cap effect.

**2. `TS3005a.transcript` is unstable: 1, 1, 5.** Same 16.4 KB input, same
model, same prompt — one run produced five objects reaching `Decision` and
`Project`, two produced one. That variance is not noise around a mean; it is
the difference between a usable bundle and a useless one, from identical
inputs.

The first finding alone would suggest a size ceiling. The second says the
pipeline can do better on the same input and often does not, which is a
different problem with a different fix.

## What works

`Decision` is reachable, from the transcript as well as the summary. Reading
this report as "the classifier cannot leave `Concept`" would overstate it.

## Absences the corpus does not explain

| Type | Annotated mentions | Objects emitted |
|---|---:|---:|
| `Person` | 17 (`TS3005a`), 3 (`TS3005b`) | **0** |
| `Organization` | 4 (`TS3005a`), 1 (`TS3005b`) | **0** |
| `Place` | 3 (`TS3005a`) | **0** |

Zero across four sources and twelve runs, against annotations written by AMI's
team before this project existed. The "corpus contained none" explanation is
ruled out by evidence that cannot have been tuned toward this result.

Open question that could narrow this: whether `Person`/`Organization`/`Place`
are meant to be emitted for a *participant in* a meeting at all, or only for a
subject the document is *about*. If the latter, these absences may be correct
behaviour and only the collapse and the variance are defects.

## The size hypothesis, weakened

Size still correlates loosely — the 40.8 KB source never exceeds one object —
but `TS3005a.transcript` at 16.4 KB produced five in one run and one in two
others, so size cannot be the whole mechanism. #379's corpus also contains
13–17 KB documents that produced 5–10 objects each.

A single `Event` for a 40.8 KB meeting suggests the model is *summarising* the
source rather than *enumerating* its subjects — a prompt-level failure rather
than a cap or parsing one. Untested.

## What this says about #379

That gate measured multiplicity and closed it: 8 sources → 13–19 derived
objects, median 3.5 per source. The measurement was correct for what it
sampled — one kind of corpus, edited technical prose about software — and the
conclusion was then carried as if it were about extraction in general.

On meeting material the same pipeline reaches 1:1 routinely and unpredictably.
"Multiplicity holds" needed a second corpus shape before it was a claim about
the extractor rather than about that corpus.

## 2026-08-07 — union+judge gate rerun (change `union-judge-extraction`, #456)

Post-#455 baseline vs the union+judge pipeline (`--union-judge`, 3 runs,
fresh Ollama):

| Source | baseline (blind cap) | union+judge | reading |
|---|---|---|---|
| `TS3005a.summary` | 4, 4, 1 | 4, 4, 6 | no regression |
| `TS3005a.transcript` | 1, 1, 1 | **1, 1, 4** | first run set ever past 1 object; reached `Concept`, `Procedure`, `Project` beside `Event` |
| `TS3005b.summary` | 1, 1, 1 | 1, 1, 2 | first `Decision` from this summary |
| `TS3005b.transcript` | 6, 6, 6 of 10 | **5, 1, 7** | type coverage held (`Decision` 4, `Event` 6, `Project` 2, `Procedure` 1); see caveat |

**Defect found and fixed by this gate.** The first union rerun returned
`0 kept of 0 proposed` with `judge_status="ok"` on `TS3005a.transcript`: both
runs collapsed to the umbrella Event `AMI meeting TS3005a`, the judge rejected
it, and the pipeline had no floor. Fixed before delivery: an empty admitted
set with a non-empty union now degrades to the backstop-truncated union
(`judge_status="empty"`, distinct stderr notice) — extraction can no longer
return zero objects from a substantive source by judge decision alone.

**Caveat that is now #457.** The `5, 1, 7` spread on `TS3005b.transcript`
includes a run where the judge kept 1 of ~10 merged candidates from 40.8 KB —
the old collapse silhouette, produced by selection rather than generation.
Whether those rejections were right (facets) or wrong (subjects) is
UNDECIDABLE without AMI subject-level ground truth; the blind cap's `6, 6, 6`
was never truth either (#454 showed it discarding genuine `Decision` titles).
Adjudicate first, tune the judge after — never the reverse.

The `Person`/`Organization`/`Place` absences persist unchanged under the
union path (still zero against 17/4/3 annotated mentions) — the open
participant-extraction question above stands, and #457's adjudication pass is
positioned to settle it.

## 2026-08-13 — participant coverage baseline (change `first-class-participants`, #668)

First `--participants` run of the extended probe (qwen3:8b, 3 runs per
source, PR1's judge re-admission live in the measured tree):

| Meeting | Source | Person emitted | Organization emitted | Judge-selected | Re-admitted | Anchor-less discards |
|---|---|---:|---:|---:|---:|---:|
| TS3005a | summary | 0 | 0 | 0 | 0 | 0 |
| TS3005a | transcript | 0 | 0 | 0 | 0 | 0 |
| TS3005b | summary | 0 | 0 | 0 | 0 | 0 |
| TS3005b | transcript | 0 | 0 | 0 | 0 | 0 |

Affordance floors unchanged: TS3005a has 17 Person / 4 Organization
annotated mentions, TS3005b has 3 / 1. All four Person/Organization
absences remain UNEXPLAINED; precision side is trivially clean (nothing
emitted, nothing unexplained, zero stub flooding).

**Reading.** Generation is confirmed as the dominant defect, exactly as the
exploration's 0-of-12 finding predicted: with re-admission live, zero raw
`Person`/`Organization` candidates were proposed, so the re-admission and
anchor gates never fired (`re-admitted 0`, `anchor-less discards 0` — the
machinery is in place and measurably inert). This satisfies the
`participant-coverage-probe` spec's phase-2 trigger — zero generation on
2 meetings across 3 runs — and is the recorded justification for opening
the phase-2 scoped capture pass (design D6). Phase-1a alone moves nothing
on this corpus, as predicted.

## 2026-08-14 — capture-pass effect measurement (change `first-class-participants`, #668, PR3)

The post-capture-pass `--participants` re-run (qwen3:8b, 3 runs × 4
sources) reproduced the baseline exactly: zero Person/Organization
everywhere, zero anchor-less discards. **That re-run is a NULL EXPERIMENT,
not a verdict on the pass**: the harness passes `path.stem`
(`TS3005a.transcript`) as `source_title`, which never matches
`_MEETING_SHAPED_TITLE_RE`, so neither PR1's re-admission nor PR3's
capture pass ever fired on this corpus. The mechanism was measurably inert
by gating, not by failure.

An isolated gate-fired probe (same transcript text, truthful title
"AMI project meeting TS3005a (transcript)", 2 runs) measured the mechanism
itself:

| Run | capture_runs | Captured | Anchor-less discards | In final set |
|---|---:|---|---:|---|
| 1 | 1 | Person A, B, C, D | 0 | all 4 |
| 2 | 1 | Person A, B, C, D | 0 | all 4 |

The pass emits exactly the meeting's four speakers (AMI anonymizes
participants to letters), each carrying a role anchor, deterministically
across runs, with zero stub flooding. Recall against the 4-speaker floor is
4/4 once the gate fires; the single-letter titles are the corpus's own
anonymization, not a naming defect.

**Reading.** PR3's mechanism is validated in isolation; the epic's
remaining gap is DETECTION: a real meeting transcript whose title is a
code (`TS3005a.transcript`, an export named `2026-08-13-recording.txt`)
is invisible to the title-only gate that D3 chose — the exact target of
the owner's transcript-scope contract. Content-shape detection
(speaker-turn density — D3's named alternative) is the follow-up, filed
separately with both measurements as evidence. Per the probe spec's gate
semantics, phase-2 work shipped on the recorded zero-generation baseline;
this section records its measured effect honestly rather than
overclaiming corpus recall the gating cannot yet deliver.
