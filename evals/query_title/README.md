# `query_title` — subject-named or question-named? (#696)

Issue #696: `query --save` still titles filed insights with the question, so
the slug — the permanent OKF Concept ID — is an interrogative sentence
(`por-qué-es-importante-la-trazabilidad-en-un-sistema-de-conocimiento`).

```bash
uv run python -u evals/query_title/run_query_title_probe.py --runs 6
uv run python -u evals/query_title/run_query_title_probe.py --rescore
uv run python -u evals/query_title/run_query_title_probe.py --self-test
```

Requires a local Ollama serving `bge-m3` and the chat model. Generation is
paid once and stored; the ladder is a pure function of `(question, answer)`,
so every arm re-derives offline from `results/runs-*.json`.

## The issue's diagnosis was wrong

#696 says "both answers opened with a perfectly usable declarative sentence,
so the fallback never engaged", and proposes deriving the title from the
subject in the ordinary case rather than only as a fallback.

Running the shipped ladder against its own two evidence questions shows
**both** rungs refusing:

| rung | why it refused |
| --- | --- |
| `_declarative_answer_title` | a real Spanish opening measures 158 chars against `_DECLARATIVE_TITLE_MAX_CHARS = 90` |
| `_question_subject` | neither `¿por qué es importante X?` nor `¿qué relación hay entre X e Y?` is one of the eleven definitional scaffolds #646 narrowed itself to |

So the proposed reorder promotes a rung that returns `None`, and changes
nothing for the issue's own examples. The binding constraint is the ceiling
against long Spanish openings — which `_question_subject`'s own docstring
already named in prose, before the issue was filed.

## What it measured (qwen3:8b, 14 runs/probe, 170 filings, 2026-08-17)

| arm | titled by question | residuals resolved | converged | FP exposure | FPs | regressions | verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `baseline` | 91 of 170 | 0 | 8 of 16 | 27 | 0 | 0 | NO EFFECT |
| `clause` | 16 of 170 | 61 | 8 of 16 | 27 | 0 | 0 | **SHIPPED** |
| `scaffold` | 35 of 170 | 56 | 8 of 16 | 27 | 0 | 0 | measured, NOT adopted |
| `clause+scaffold` | 13 of 170 | 64 | 8 of 16 | 27 | 0 | 0 | measured, NOT adopted |

**54% of filings were named after the question.** `clause` takes that to
9% with zero regressions on the filings the shipped ladder already got
right, and zero adjudicated false positives against 27 exposed open
questions.

## Convergence is NOT fixed, by any arm

Every arm scores exactly the baseline's `8 of 16`. #696 states its harm as
duplicate detection — two phrasings of one question filing as unrelated
objects — and **nothing here fixes that**. What ships fixes the narrower
complaint the issue opens with: the Concept ID stops being an interrogative
sentence.

The diverging family is instructive:

| arm | `¿por qué son importantes las fuentes inmutables?` | `¿por qué importan las fuentes inmutables?` |
| --- | --- | --- |
| `baseline` | `por-qué-son-importantes-las-fuentes-inmutables` | `por-qué-importan-las-fuentes-inmutables` |
| `scaffold` | `fuentes-inmutables` | `por-qué-importan-las-fuentes-inmutables` |
| `clause` | `las-fuentes-inmutables-son-importantes` | `las-fuentes-inmutables-importan` |

## #757 asks for the `scaffold` arm, and the same numbers refuse it

[#757](https://github.com/jasonssdev/openkos/issues/757) reopens the request
one day later: *"Applying the subject-derived title in the ordinary case —
rather than only as a fallback when the first clause is unusable — is the
change."* That is this harness's `scaffold` arm, re-scored here at zero cost
over the stored population.

**Three of the issue's claims do not survive contact with the code or the
measurement.**

1. **Its description of the current ladder is wrong.** The shipped order is
   `_declarative_answer_title` → `_question_subject` → `_clause_answer_title`
   → the question verbatim. The subject rung is already ABOVE the clause
   rung, not below it — and that ordering was MEASURED, not assumed: with the
   clause rung on top, `¿qué es la trazabilidad?` over a long opening cuts to
   `La trazabilidad`, article and all, where the subject rung gives the
   cleaner `Trazabilidad`.

2. **"Keep the original question as a field" already ships.** `query --save`
   assigns the question as the filing's default `description`
   (`resolved_description = question if description is None else description`),
   pinned by `test_stage_filed_answer_title_description_default_to_question`.
   Nothing is lost today.

3. **The widening it asks for measures WORSE than what ships.** Alone,
   `scaffold` leaves 35 of 170 filings titled by the question against the
   shipped `clause` arm's 16. Stacked on top of it, `clause+scaffold` reaches
   13 — three filings out of 170, bought with a prefix list somebody has to
   keep extending, whose blind spot is measured rather than hypothesized
   (`por qué importan ` is one word from its `por qué son importantes `
   entry and returns `None`).

**And #757's own second cost is unfixed by the thing it asks for.** The issue
argues the slug is the identity, so two phrasings of one question file as
unrelated objects that duplicate detection cannot group. Convergence is
**8 of 16 for every arm, including `scaffold`** — identical to the baseline.
The remedy does not touch the harm.

What remains real is the residual 9%: filings still named after the question
under the shipped ladder. Any future attempt on them should be measured
against this population before it is written, and should be aimed at
convergence, which nothing here has moved.

## Why `scaffold` was not adopted

Its blind spot is **measured, not hypothesized**. `por qué importan ` is one
word away from its `por qué son importantes ` entry, and it returns `None` —
the filing drops to the question verbatim. That is the enumerate-the-forms
tail showing up inside the very measurement meant to justify the
enumeration.

Adding all four prefixes on top of `clause` buys **3 filings out of 170**
(16 → 13) and permanently adds a list somebody has to keep extending. Not
worth it. `clause` reads the ANSWER, so it covers question shapes nobody
enumerated.

## Two corrections this harness made to itself

Both are recorded because each one had already produced a number I was ready
to act on.

1. **n=1 reversed at n=6.** With one run, `clause` and `scaffold` each
   resolved 4-of-4 residuals and I concluded scaffold was redundant. At 66
   filings it was 22 vs 24 — complementary, not redundant. At 144 it flipped
   again (51 vs 48) once a paraphrase scaffold cannot see entered the
   corpus. Consistent with `five-run-arm-swings-wider-than-the-effect`.

2. **The first convergence metric measured the wrong thing, twice.** It
   first grouped `¿qué es X?` with `¿por qué importa X?` as one subject and
   scored every arm 0-of-12 — those ask different things and SHOULD file
   separately. Then, after regrouping into true paraphrases, `--rescore`
   accumulated two generations whose `run` indices collided, merging
   families whose members never coexisted. Families are now keyed by
   generation as well as run; single-member families are excluded, since a
   family with one member can neither converge nor diverge.

## What the review round changed

Four lenses ran over this change; all four findings were WARNING-level and
all four were acted on rather than filed onward.

- **Resilience** — `generate()` had no checkpointing around ~78 sequential
  paid generations, so a failure at the last probe discarded every one
  before it. It now skips-and-records a failed probe and checkpoints to the
  final results file after each completed run, so an abort leaves a legible
  partial population instead of nothing. `failures` rides in the same file
  precisely so a partial cannot read as complete.
- **Reliability** — the cut-point tie-break (`min` over a comma and up to
  seventeen connectors) had no test where two boundaries compete. Two now
  exist, both mutation-verified against `min` → `max`.
- **Readability, production** — the `found > 0` filter is a correct spelling
  of "found" only because every connector carries a leading space. The
  invariant is now stated where the connectors are defined and pinned by
  `test_clause_connectors_are_space_delimited`.
- **Readability, harness** — `widened_question_subject` had copied
  `_question_subject`'s body. It now calls the production function with the
  prefix tuple swapped in a `try/finally`, so the `scaffold` arm cannot
  drift from the behaviour it claims to measure.

Risk found nothing.

A second round over the corrected state found two more, severity falling:

- **Resilience** — the checkpoint I had just added wrote with a plain
  `write_text`, so a process killed mid-write leaves truncated JSON where a
  whole file used to be all-or-nothing; and `_stored_runs` reads EVERY
  `runs-*.json`, so one bad checkpoint would break every future `--rescore`.
  **A defect the previous fix introduced.** Now writes through the
  production `fsio.write_atomic`, and the read side raises loudly naming the
  file rather than skipping it — a silently dropped run file shrinks the
  population, which is how a zero-exposure result starts reading as safety.
- **Reliability** — the clause rung was spliced directly above the terminal
  `or question`, and no test drove a filing through all three refusals to
  that fallback. One now does, mutation-verified.

Readability and risk found nothing in the second round.

Rounds three, four and five kept going, severity never once exceeding
WARNING. The two worth naming:

- **The cut index was measured against the wrong string.** Two lenses found
  it independently, which is the signal worth trusting. `_clause_answer_title`
  took the comma index from `candidate` but connector indices from
  `candidate.lower()` — and `str.lower()` is NOT length-preserving (`"İ"`
  becomes two codepoints), so each such character drifts the cut one
  position right and several cut inside the preceding word. Now a single
  case-insensitive regex searches `candidate` itself; as a bonus,
  leftmost-alternation replaces the per-connector `min` scan.
- **A defect introduced by fixing another defect, twice over.** The
  checkpoint added in round one wrote non-atomically (round two), then wrote
  the REQUESTED run count instead of the completed one (round four), which
  would have made an aborted partial file claim to be whole. Both fixed;
  the second is the same silent-truncation shape one layer up.

Round five's two findings were both in the harness — production came back
clean from every lens — and were the FTS handle reopened per run and never
closed, and `_load_adjudication` swallowing a corrupt file into an empty
label map. That last one was an inconsistency this work created: losing a
`bad` label turns a REJECTED arm into a merely-unadjudicated one, so it now
fails loud on corruption while still treating an ABSENT file as the normal
"nothing adjudicated yet" state.

Rounds six and seven found no production defect at all beyond **two comments
of mine that lied about the code**: the connector docstring still described
the `str.find` filter the regex had replaced, and a comment claimed both
SQLite handles had been hoisted out of the run loop when only the FTS one
had. A comment describing deleted code is worse than no comment, so both
were corrected — the second by making the claim true rather than by
softening it. The remaining findings were harness input-validation gaps
(`_stored_runs` raising a bare `KeyError` on a foreign run file,
`_load_adjudication` accepting a non-object `labels`) and two `datetime.now()`
calls that gave a report and its own evidence file different timestamps.

Across seven rounds: **22 findings, zero BLOCKER, zero CRITICAL.** Risk
returned clean every round but the first two. The rounds stopped when the
remaining findings were entirely about this harness rather than the product,
which is the point of diminishing returns, not a clean bill of health.

## The scorer can come out against the hypothesis

`--self-test` scores one synthetic population three ways and fails unless
the scorer REJECTS an adjudicated-bad invented title, and unless a
population with no open question scores `UNFALSIFIABLE` rather than safe.
Mutation-verified: disabling the unadjudicated queue turns it red.

`adjudication.json` carries the labels and records its one contested call —
`Decidimos que el ledger de merges vive fuera del frontmatter` — as
contested, with the argument on both sides.
