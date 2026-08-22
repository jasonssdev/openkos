# Scoring the identity adjudicator (#796)

**Result: the rubric clause is the fix. The deterministic check adds 0.00 on
top of it, and ships anyway — for a reason that is not this number.**

## Why this harness exists

Two Events sharing one title were judged `same`, and the judge's own
rationale said the event *"appears to be a continuation or follow-up of the
same meeting"* — an argument for `different`, written under a `same`
verdict. A `same` verdict feeds a **destructive merge**, so this class of
error costs a document.

Nothing scored this judge. A prompt fix would have been adopted on
intuition, which this repository has already paid for (see
`evals/edge_typing/README.md`, where a longer prompt lost its own A/B).

## The five probe classes

The measurement runs **both directions** of the change, because a rule that
makes identical Event titles read as a recurring series buys its precision
somewhere.

| probe | n pairs | expected | what it guards |
| --- | --- | --- | --- |
| `recurrence` | 3 | `different` | the reported class |
| `event-same` | 2 | `same` | one meeting recorded twice — **what the fix must not cost** |
| `person-same` | 1 | `same` | identical name IS identity for a Person |
| `alias-same` | 1 | `same` | one entity, two names, general recall |
| `part-whole` | 1 | `different` | the exclusion the shipped prompt already states |

Without `event-same`, a rubric that answered `different` to every Event pair
would score perfectly.

## The 2×2 ablation, 15 runs per arm

Two mechanisms shipped for #796, so each is measured by **taking it away**.
`qwen3:8b`, generation ceiling 8192, context window 12288 — production's own
settings, not the client's opted-out defaults.

| rubric clause | self-refutation check | recurrence precision | accuracy | stability | latency |
| --- | --- | --- | --- | --- | --- |
| ✓ | ✓ | **1.00** | 1.00 | 1.00 | 18.9s |
| ✓ | ✗ | 1.00 | 1.00 | 1.00 | 18.7s |
| ✗ | ✓ | 0.51 | 0.82 | 0.93 | 16.1s |
| ✗ | ✗ | **0.33** | 0.75 | 1.00 | 16.4s |

**Every control class is 1.00 in all four arms.** `event-same`,
`person-same`, `alias-same` and `part-whole` do not move at all — neither
mechanism buys its recurrence precision out of anything this harness can
see.

### Read the table honestly

- **The clause carries the whole effect.** 0.33 → 1.00, at stability 1.00,
  on all three `recurrence` pairs.
- **The check on its own recovers a third of the failures**: 0.33 → 0.51,
  and replayed against the pre-fix arm's stored rationales it
  withdraws **9 of 30** wrong `same` verdicts (0.30).
- **Together they equal the clause alone.** The check's marginal
  contribution to the shipped configuration is **0.00**.
- The clause costs **+15% latency**. The check costs none.

### Why the check ships despite adding nothing here

Because it does something the clause structurally cannot: it reaches a
verdict **already persisted**. A stored verdict is served whenever the
member digests still match, whatever rubric produced it ([#838][i838]), so
the clause never reaches a workspace that already ran `adjudicate` — but the
check is pure and reads the stored rationale, so it corrects such a row on
read, with no model call. It recovers 0.30 of them, not all; that is a net,
not a fix, and this file says so rather than letting the shipped 1.00 imply
otherwise.

It also costs nothing measurable to keep: **0 false withdrawals across 120
correct `same` verdicts**, on both the clause and no-clause arms.

### Why the check alone is unstable

Stability drops to 0.93 in the check-only arm, and the reason is visible in
the stored rationales: the phrasing is a lottery. The same pair, same
prompt, different run:

> "...indicating they are **instances of the same event**." — no distinctness
> word, not withdrawn
>
> "...indicating they are the same entity under **different instances**." —
> withdrawn

The check fires when the model happens to write a distinctness word. On
`comite-evaluacion-coordinacion` — the pair built to mirror #796's own — it
fired on **1 of 15** runs, leaving that pair's modal verdict `same`. A mechanism that depends on the wording of the
mistake cannot be the answer to the mistake.

## What this does NOT establish

- **The labels are constructed, not adjudicated.** Each pair is written to be
  unambiguous under the rubric the prompt states, so accuracy reads as
  rubric-consistency, not as agreement with a human on a real bundle.
- **`recurrence` has three items.** The aggregate moves in steps of ⅓; read
  the per-pair modal verdicts, which each report prints.
- **The fixtures are de-identified analogues.** The reported documents are a
  private meeting transcript naming real people. Eval fixtures are
  committed, published, and quoted back inside stored model rationales, so
  `comite-evaluacion-coordinacion` reproduces the *properties* that make the
  class hard — an abbreviated Spanish date, three named attendees on one
  side only, disjoint agendas — with invented names and content. It is not
  the reported document, and a shorter body may be an easier one.
- **One model.** Everything above is `qwen3:8b`.
- **Confidence carries no information.** Every verdict in the no-clause arms,
  right and wrong, was stated at exactly 0.95. A confidence gate over this
  judge would separate nothing — the same finding [#558][c558] made about
  the contradiction judge, on a different judge.

## Usage

```bash
uv run python -u evals/adjudication/run_adjudication_eval.py --self-test
uv run python -u evals/adjudication/run_adjudication_eval.py --arm baseline --runs 15
uv run python -u evals/adjudication/run_adjudication_eval.py --arm baseline --ablate-clause --runs 15
uv run python -u evals/adjudication/run_adjudication_eval.py --arm baseline --ablate-clause --ablate-withdrawal --runs 15
```

`baseline` runs production untouched; each `--ablate-*` flag removes one
shipped mechanism, which is the only way to measure a mechanism that has
already shipped. `--self-test` needs no model and no network: it proves the
fixture bundle produces exactly one candidate group per labelled pair, and
that the ablation is not silently identical to production.

[i838]: https://github.com/jasonssdev/openkos/issues/838
[c558]: https://github.com/jasonssdev/openkos/issues/558
