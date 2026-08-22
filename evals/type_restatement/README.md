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
across the bundle. That is a different design, at a different layer, and it
is filed rather than guessed at here.

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
```

`--rescore` re-derives every verdict from `results/delta.json` after
`adjudication.json` is edited, with no re-scan.

[issue]: https://github.com/jasonssdev/openkos/issues/804
