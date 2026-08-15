# `participant_language` — does the participant pass translate the source? (#713)

`#713` measured the participant-capture pass returning **English descriptions
and English bodies** from a 100% Spanish meeting transcript, on 3 of 3 runs —
and the bodies were *translations of the source's own turns*, not summaries in
the wrong language.

```bash
uv run python -u evals/participant_language/run_participant_language_probe.py --self-test
uv run python -u evals/participant_language/run_participant_language_probe.py --runs 3
uv run python -u evals/participant_language/run_participant_language_probe.py --arm both --runs 3
uv run python -u evals/participant_language/run_participant_language_probe.py --rescore results/<file>.jsonl
```

**Use `-u`.** Piping through `tee` makes Python buffer and a long run looks hung.

## Why a separate harness from `evals/language_leak`

That one answers a different question and must keep answering it: it scores
**titles**, on the **chunked** path, because that is where #563 measured the
leak and where the shipped gate `_drop_wrong_language_titles` runs.

#713 is `description` and `body`, from a 5.4 KB source that never chunks,
produced by a call that gate never sees. Mixing the two would make neither set
of numbers comparable with its own history.

The **classifiers are imported** from it rather than rewritten, so the repo
keeps one definition of "what language is this string": `classify_title`
(generic marker voting, despite the name) and `quoted_verbatim` (the #618 class
split).

## Why a leaked body is worse than a leaked title

A leaked title is a wrong permanent slug — already serious, already measured. A
leaked **body** is the stored *content* of the object being something the
source never said, in a language the user did not write. It is what `query`
cites back, and for a `Person` object it is personal data restated by a model
rather than quoted.

## What counts as harmful

`en` **and** not verbatim in the source. The class split is #618's, applied to
prose instead of titles:

| class | harmful? | why |
| --- | --- | --- |
| `en`, no verbatim support | **yes** | a translated rendering of the source |
| `en`, verbatim in the prose | no | the source's own words — a quoted name or phrase |
| `mixed` | no | Spanish prose quoting an English technical term, which is correct |
| `neutral` | no | unlabelled; reported in its own column, never folded into either side |

Counting `mixed` and unstripped parentheticals as harmful is exactly what
overstated the first #563 analysis by roughly 2x.

The `title` field is deliberately **not** scored here — it belongs to
`evals/language_leak` and to the shipped gate.

## Result, and what shipped

`qwen3:8b`, 3 runs per arm, two 100% Spanish transcripts, reproduced on two
independent sweeps.

| fixture | arm | fields | harmful | rate |
| --- | --- | --- | --- | --- |
| `es-anchored` | baseline (no anchor) | 24 | 18 | **0.75** |
| `es-bare` | baseline (no anchor) | 18 | 0 | 0.00 |
| `es-anchored` | anchored (shipped) | 30 | 0 | **0.00** |
| `es-bare` | anchored (shipped) | 18 | 0 | 0.00 |

Cause: `_build_participant_capture_messages` was the **only** extraction call in
the pipeline that omitted `_LANGUAGE_ANCHOR` — *'Write every "title",
"description" and "body" in the same language as the SOURCE TEXT below.'* Its
docstring justified the omission on the grounds that "the source text itself
still carries the source's language". That assumption is false here, and #522
had already measured the same shape from the other side: removing the only
source-language text from a user turn produced English output in 28 of 30 runs.

The anchor shipped. Candidates went **up** (5 per run against 4 on the fixture
that leaked) and latency did not increase.

The `es-bare` contrast #713 flagged is real and reproduced: the same pipeline,
model and run leaves that fixture in Spanish either way. It is the shorter,
role-free transcript, so the arms differ only where the model had role prose to
paraphrase.

## The arms are built by ABLATION

Because the anchor now ships, `anchored` is the shipped builder untouched and
`baseline` strips the anchor back out. Adding it to a builder that already
carries it would send the instruction **twice** and compare duplication against
itself while the table still said anchor-against-no-anchor.

`_assert_shipped_carries_the_anchor` fails loudly if the shipped builder stops
sending it — otherwise the ablation is a silent no-op, both arms are one prompt,
and the probe reports "the anchor does nothing" for an anchor that was never
there to remove. The same inversion `evals/stage_attrition` needed once #715's
clause shipped.

## The self-test

Guards three silent-success failures:

1. a scorer that counted every non-Spanish string harmful would report a large
   leak made mostly of quoted proper names, and the table would look like strong
   evidence — so a verbatim English quotation must score clean;
2. a scorer that missed translations would report zero;
3. an arm that failed to install would measure one prompt twice.

It also asserts the ablation actually shortened the user turn, and that the
title and source text survive it.
