# A title that restates its own type (#804)

**Result: REFUTED.** 18 duplicates recovered, **11 false positives**, over 29
newly matched pairs with zero left unadjudicated. The bar is one.

## The question

[#804][issue] reports two documents describing one project under two names --
`Project Helios` (typed `Project`) and `Helios Data Platform` (typed
`Concept`) -- that no candidate tier can group.

Widening the per-type block is one half, and it ships. It is **not enough on
its own**, which is what sent this probe looking for a second half: even once
the two are compared, the pair fails LOW, because containment needs an
equivalent for `project` and the larger title has none.

| token pair | `SequenceMatcher.ratio()` |
| --- | --- |
| `project` / `helios` | 0.154 |
| `project` / `data` | 0.182 |
| `project` / `platform` | 0.267 |

All three sit far under the 0.75 threshold. So the question this probe asks
is whether `project` in `Project Helios` is a *name* or the document
restating the type it is already filed under -- and if the latter, whether it
can be excused from finding an equivalent so the pair meets at `helios`.

## What was measured

558 distinct stored titles, 155,372 pairs, deterministic and stdlib-only --
no model, no GPU. The rule is applied only where production's
`near_match_score` already declined, so it is additive by construction and
every delta pair is newly matched.

Three tightenings were needed before the rule was worth adjudicating at all,
and each is measured:

| form of the rule | newly matched |
| --- | --- |
| excuse any OKF type word | 136 |
| ...and require an EXACT match for what remains | 121, **with regressions** |
| ...applied only where the baseline declined, and only for an APPOSED type word | **29** |

The middle row is the interesting failure: requiring exactness for the
remainder while also re-deciding pairs the baseline had already matched
*lost* pairs -- `Decision on Bilingual Documentation` ‖ `Decisión sobre
Documentación Bilingüe` among them, because `documentation` and
`documentacion` are not exactly equal. A rule that can subtract is not a
recall fix, so the final form only ever adds.

The apposition guard is what took 136 down to 29. `Project Helios` and
`Onboarding Procedure` name a thing and file it; `Decision on Bilingual
Documentation` and `Decisión sobre la fuente canónica` are *about* something,
and the something is not the document. The separator is the function word
between them.

## The result

| metric | count |
| --- | --- |
| newly matched | 29 |
| exposed (adjudicated) | 29 |
| recovered duplicates | 18 |
| **false positives** | **11** |
| unadjudicated | 0 |

The false positives are one recognisable class, not a scatter: **an occurrent
about a thing matched to the thing.**

- `Meeting Discussion on Remote Control Design` ‖ `Remote Control Design Project`
- `Knowledge Recovery Project` ‖ `Reunión del equipo de knowledge recovery system`
- `Decisión sobre el re-ranking del retrieval` ‖ `Re-ranking Procedure`
- `Remote Control Design Project` ‖ `Remote Control Design Specifications`

The apposition guard cannot reach these, because the type word genuinely *is*
an apposition on the side that carries it: `Remote Control Design Project` is
a bare `Project` suffix. Excusing it leaves `remote control design`, which
every document about that design contains.

## Why no further tightening was tried

Because the surviving token is what decides, and the two cases are
structurally identical:

```
Project Helios      -> excuse 'project'   -> requires {helios}   -> matches 'Helios Data Platform'
Re-ranking Procedure -> excuse 'procedure' -> requires {ranking}  -> matches 'Retrieval Re-ranking Project'
```

`helios` is a rare proper noun and `ranking` is a common one, but nothing in
a **pairwise lexical** comparison can tell them apart -- which is the same
wall `near_match_score`'s own docstring describes for `cats` ⊂ `carts and
currency` versus `stoicism` ⊂ `stoic philosophy`. Separating them needs a
signal this function does not have: how often the surviving token occurs
across the bundle. That was filed as [#837][issue-837] and measured below.

## The filed follow-up, measured: corpus frequency does not separate either (#837)

`--df-floor` scores #837's proposed signal -- allow the excusal only when
what survives it is *distinctive in the bundle* -- over the same 29
adjudicated pairs, with document frequency counted across the corpus's 527
distinct normalized keys through the same token pipeline the rule scores
with. Deterministic, stdlib-only, and re-derivable from the committed delta
and rulings. The full table is `results/df_floor_report.md`.

**Result: REFUTED, structurally rather than by threshold.** Five
required-token sets carry BOTH rulings:

| required set | duplicates | distinct |
| --- | --- | --- |
| `{ranking}` | 3 | 2 |
| `{onboarding}` | 2 | 1 |
| `{ranking, retrieval}` | 1 | 1 |
| `{knowledge, recovery}` | 3 | 4 |
| `{remote, control, design}` | 3 | 3 |

Every statistic a floor could consult -- max, min, sum, any weighting -- is
a function of the required tokens alone, so within each of these groups the
duplicate and the distinct produce the **identical** number, at every
threshold. The sweep half agrees: the largest zero-false-positive floor
keeps **0 of 18** adjudicated duplicates on maxDF and **2 of 18** on minDF
(the two `button less remote` pairs, whose rarest token has DF 4).

Two facts the filing conjectured and the count corrects:

- **`ranking` is not common here: DF 6 of 527.** The filing's rare/common
  axis (`helios` in two titles, `ranking` in dozens) does not exist in the
  measured corpus.
- **The motivating pair cannot even be scored by the floor it motivated.**
  Neither `Project Helios` nor `Helios Data Platform` is a stored title
  (`helios` has DF 0), so the pair is not in the delta at all -- a floor
  tuned to admit it would be tuned against no observation.

What actually separates the identical-requirement cases is visible in the
table's larger titles: `reunión del equipo de`, `migración ... al nuevo
formato`, `meeting discussion on`, `user preferences for`, `decisión
sobre` -- what the pair is *about*, not how rare its shared tokens are.
That is the same occurrent-versus-thing distinction named above, it is
semantic, and `near_match_score`'s contract already defers precision at
this tier to LLM adjudication over the review queue, which is where these
pairs land today.

Contested-ruling check, run before scoring: `knowledge recovery project` ‖
`knowledge recovery system` is ruled a duplicate on a reading this README
already flags as reading-dependent. Flipping it does not rescue the signal
-- the `{knowledge, recovery}` group still carries both rulings through its
other members.

## What this does NOT establish

- **The types are synthetic.** The corpus stores titles without their OKF
  types, so each title is assigned the type word it contains -- the
  assignment that excuses the most. A real bundle excuses a subset, so 29 is
  an upper bound on the recall and 11 an upper bound on the harm. Four of the
  eleven are named above precisely because they survive any plausible real
  typing: `Remote Control Design Project` really is a `Project`.
- **Adjudication reads titles, not bodies.** The corpus is harvested title
  text with no documents behind it. `Knowledge Recovery Project` ‖
  `Knowledge Recovery System` is ruled a duplicate on the reading that a
  project and its system are one entity recorded twice; a bundle where they
  are deliberately separate objects would rule the other way. The four
  occurrent-versus-thing false positives above do not depend on any such
  reading.
- **The cross-type block widening is not measured here.** It ships on a
  different argument: it only widens *who is compared*, never how, and every
  widened pair still has to pass the same unchanged ACRONYM/LOW scoring.

## Usage

```bash
uv run python -u evals/type_restatement/run_type_restatement_probe.py --self-test
uv run python -u evals/type_restatement/run_type_restatement_probe.py
uv run python -u evals/type_restatement/run_type_restatement_probe.py --rescore
uv run python -u evals/type_restatement/run_type_restatement_probe.py --df-floor
```

`--rescore` re-derives every verdict from `results/delta.json` after
`adjudication.json` is edited, with no re-scan. `--df-floor` scores the
corpus-frequency floor (#837) over the same stored delta and rulings.

[issue]: https://github.com/jasonssdev/openkos/issues/804
[issue-837]: https://github.com/jasonssdev/openkos/issues/837
