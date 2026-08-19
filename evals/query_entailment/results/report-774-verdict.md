# #774 verdict — the attribution fallback IS the fabrication signal

`qwen3:8b` + `bge-m3`, 2026-08-19. Constructed-corpus runs are stored beside
this file; the field runs are gitignored (`field-*`) because they quote a
private bundle verbatim — only their aggregates are reported here.

## What was measured

Three candidate mechanisms for the missing third guard (the answer↔context
entailment row #774 names), scored cost-first:

| mechanism | fabricated caught | grounded false-flagged | cost |
| --- | --- | --- | --- |
| `unsupported` judge (evidence-first) | 3 of 6 | 8 of 9 | 1 chat call, runaway risk |
| `binary` judge (control) | 5 of 6 (1 judge runaway) | 6 of 9 | 1 chat call |
| lexical coverage | separates per-bundle only | overlaps across corpora | free |
| **attribution == `absent`** | **30 of 30** | **1 of 45** | **free, already computed** |

## The three findings, in the order they were forced

**1. A clean corpus cannot expose the defect.** Two constructed fabrication
corpora (crisp definitions, then thin hedged meeting-note fragments — see
`fabrication_corpus.py`) produced 30 of 30 compliant restatements: exposure
zero, UNFALSIFIABLE. The fabrication reproduced only in field mode, against
the real bundle that produced the issue (35 thin extracted objects from
garbled ASR transcripts): 30 of 30 fabricated answers across two definitional
questions at n=15, including the issue's own specimen behaviors — an invented
treatise, and a "this term is not recognized" meta-answer that still shipped
five citations.

**2. Both chat-judge formulations are dead on the cost side.** The
evidence-first formulation that rescued the sufficiency check in #760 did NOT
rescue entailment: it flagged 24 of 27 grounded constructed-corpus answers
and 8 of 9 grounded field answers, with the quoted "unsupported" sentence
verified in every flag and 3-of-3 per-question consistency — systematic
over-strictness (paraphrase and summary read as unsupported), not sampling
noise. The judge also ran away thinking to the generation cap exactly on a
fabricated answer. An arm that buys the fabrications by flagging grounded
answers has reproduced the refusal defect #753's distance floor was rejected
for.

**3. The model's own attribution compliance separates the classes.** When
qwen3:8b fabricates, it also omits the `USED:` attribution line — the same
act of ignoring the system prompt produces both behaviors. Field, n=15:
30 of 30 fabricated answers were `absent` (all citing the full retrieval
set); 44 of 45 grounded answers were `reported` (the one `absent` grounded
answer is the measured false-positive rate: 2.2%, and its cost under the
shipped #777 notice is one stderr line). Constructed corpora: 63 of 63
grounded or compliant answers `reported`. Lexical coverage corroborates
per-bundle (field: fabricated ≤ 0.69 vs grounded ≥ 0.79) but overlaps across
corpora (constructed grounded went down to 0.48), so it cannot be the
primary signal.

## What ships

The `--save` gate (#774): filing an `absent`/`unparsed` answer's citations
as permanent provenance asks its own stronger question on a TTY and refuses
off a TTY even under `--auto`, with `--allow-unattributed` as the explicit
opt-in for the deliberate pre-#753 fallback population (backends that never
emit the line). No chat-judge guard ships; the read-path already discloses
the fallback via #777's notice.

## Honest limits

- The fabrication sample is one real bundle, two questions, one model. The
  signal's mechanism (prompt-compliance collapse) is plausible across
  models, but only qwen3:8b was measured.
- `reported` is trusted as compliant: an answer that fabricates AND emits a
  plausible `USED:` line would pass both guards. Not observed in 75 field
  rows, but not impossible.
- Field runs are unpublishable by design; reproducing them needs a real
  degraded bundle and `--workspace` mode.
