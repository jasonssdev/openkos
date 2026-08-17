# Function words must not decide a containment (#755)

Deterministic, stdlib-only, no model and no GPU. One pair scan over every
title stored in `evals/**/results/*.json`, comparing the pre-#755 near-match
rule against production's.

```
uv run python -u evals/duplicate_function_words/run_function_word_probe.py --self-test
uv run python -u evals/duplicate_function_words/run_function_word_probe.py
uv run python -u evals/duplicate_function_words/run_function_word_probe.py --rescore
```

## The adjudication rule

`adjudication.json` is hand-written, and the rule applied to every entry is
stated here so a later reader can disagree with it rather than guess at it:

- **`duplicate`** — a person working the LOW-confidence review queue would
  consider merging the two. Same subject, or one title contained in the
  other. LOW tier is a read-only queue that never auto-merges, so the bar is
  "worth showing", not "certainly identical".
- **`distinct`** — the titles name unrelated subjects. Surfacing the pair is
  noise.
- **`cross-type`** — production would never compare them, because
  `candidates.py` compares same-type documents only. The harvested corpus
  carries titles WITHOUT their types, so this scan is a strict superset of
  the comparisons production makes. Over-reporting is the safe direction for
  a false-positive bar, but charging the change for a pair that cannot occur
  would reject a fix on an artifact of the harness.

One entry is `cross-type`: a meeting Event against the Concept it reviews.
One is `distinct`. The remaining 36 are `duplicate`.

## Result, 2026-08-17

549 distinct stored titles, 150,426 pairs, 38 distinct comparisons whose
verdict changed.

| metric | count |
| --- | --- |
| newly matched | 37 |
| newly lost | 1 |
| exposed (adjudicated) | 37 |
| recovered duplicates | 36 |
| **false positives** | **0** |
| false positives REMOVED | 1 |
| regressions | 0 |
| cross-type (excluded) | 1 |
| unadjudicated | 0 |

**Verdict: SHIPPABLE at this bar.**

## Two things the issue got wrong, and the measurement found

**The reported pair is not blocked by `del`.** #755's mechanism section says
an unmatched three-letter function word makes containment return `None`. On
its own evidence pair that is false: both token sets hold four members, the
tie makes the LEFT set the required one, and the token that actually blocks
is `decisiones` — a genuine content word absent from the right-hand title.

Excluding `del` still fixes the pair, but by a different mechanism: it makes
the right set SMALLER, so containment is asked in the direction that can
succeed. **Size picks the direction**, which is why function words have to be
excluded from the count and not merely from the comparison.

**Function words also manufacture matches.** The issue frames them purely as
blockers. The scan found the opposite direction too, and it is the one that
produced a live false positive: `SequenceMatcher(None, "model", "del")` is
exactly `0.750`, the threshold. A `del` sitting in the larger set was a
lexical target that `model` matched, pairing `Integración del knowledge
source project` with `Knowledge Object Model`. Removing function words from
scoring — not just from the required set — is what removes it.

## What this harness cannot tell you

The corpus is titles extraction actually produced, but it is the titles this
repository's own eval runs happen to have stored: heavily Spanish/English
mixed, project-domain, and shaped by the fixtures those runs used. A bundle
of a different character could expose pairs this population does not contain.

It also cannot see types, hence the `cross-type` ruling above.
