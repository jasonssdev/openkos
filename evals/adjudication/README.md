# Scoring the identity adjudicator (#796, #869)

**Result: the rubric clause is the fix. The deterministic check adds 0.00 on
top of it, and ships anyway — for a reason that is not this number.**

**#869 addendum (2026-08-25): the asymmetric-members miss is a CLASS, and
both remedies measured against it were refuted — see
[the #869 section](#the-asymmetric-members-class-869) below. Production is
untouched; what holds is the layered defense the issue itself names.**

## Why this harness exists

Two Events sharing one title were judged `same`, and the judge's own
rationale said the event *"appears to be a continuation or follow-up of the
same meeting"* — an argument for `different`, written under a `same`
verdict. A `same` verdict feeds a **destructive merge**, so this class of
error costs a document.

Nothing scored this judge. A prompt fix would have been adopted on
intuition, which this repository has already paid for (see
`evals/edge_typing/README.md`, where a longer prompt lost its own A/B).

## The seven probe classes

The measurement runs **both directions** of the change, because a rule that
makes identical Event titles read as a recurring series buys its precision
somewhere.

| probe | n pairs | expected | what it guards |
| --- | --- | --- | --- |
| `recurrence` | 3 | `different` | the reported class |
| `event-same` | 2 | `same` | one meeting recorded twice — **what the fix must not cost** |
| `asym-recurrence` | 3 | `different` | #869's shape: one detailed member elaborating a sparse member's purpose |
| `asym-same` | 2 | `same` | the sparse-but-genuine duplicate an asymmetry rule must not cost |
| `person-same` | 1 | `same` | identical name IS identity for a Person |
| `alias-same` | 1 | `same` | one entity, two names, general recall |
| `part-whole` | 1 | `different` | the exclusion the shipped prompt already states |

Without `event-same`, a rubric that answered `different` to every Event pair
would score perfectly; without `asym-same`, one that answered `different`
whenever detail is asymmetric would too.

## The 2×2 ablation, 15 runs per arm

Two mechanisms shipped for #796, so each is measured by **taking it away**.
`qwen3:8b`, generation ceiling 8192, context window 12288 — production's own
settings, not the client's opted-out defaults.

**These four arms were measured on the original 8-pair fixture set**, before
#869 added the five asymmetric pairs. Arms are never comparable across
fixture sets, so the table stands as recorded and the #869 arms below were
re-measured whole rather than read off it.

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

## The asymmetric-members class (#869)

A wild pair (0.2.9, real corpus) was judged `same` with a rationale
asserting overlap the members do not carry: one body dated and attended,
the other bare, action items disjoint — but the detailed body ELABORATES
the sparse body's stated purpose, and shared purpose read as shared
substance. The rubric's own asymmetric branch ("their subject matter must
substantively overlap") was satisfied by ASSERTION. The question #869
posed: class or sample?

**Class.** Five pairs joined the fixture set (`asym-recurrence` ×3,
`asym-same` ×2 — the guard), and the baseline, 15 runs
(`runs-baseline-20260825T015816Z`):

| arm | asym-recurrence | wild-shape pair | asym-same | every other class |
| --- | --- | --- | --- | --- |
| production (baseline) | **0.67** | **0.13** | 0.97 | 1.00 |
| treatment: checkable-fact sentence | 0.67 | 0.07 | 0.93 | 1.00 |

The miss concentrates exactly where thematic elaboration is strongest: the
pair mirroring the wild shape (`grupo-calidad-datos`) is judged `same` 13
of 15 runs, **stably, at 0.95 confidence** — the same
confidence-carries-nothing finding as every other arm in this file. The
class the plain `recurrence` probe measures stays at 1.00; its one
asymmetric pair keeps both bodies short and topically disjoint, which is
why the 2×2 table above could read 1.00 while the wild pair failed.

**Remedy 1 — rubric sentence, REJECTED.** Replacing "their subject matter
must substantively overlap" with a checkable requirement (an identical
concrete fact stated by BOTH bodies, elaboration named as the SERIES
showing through — `adjudication_prompts._ASYMMETRY_SENTENCE_TREATMENT`,
`--arm treatment`) moved the class **0.67 → 0.67** and cost one `asym-same`
flip (0.97 → 0.93). The model asserts the overlap either way; a wording it
can satisfy by assertion is not a constraint. Fifth prompt treatment this
repo has measured and rejected.

**Remedy 2 — withdrawal-marker extension, REJECTED.** Some wrong rationales
concede the point in #796's own signature ("which is a specific instance of
the recurring event", under `same`). Rescoring every stored arm offline
(free, #807's verbatim rationales): an indefinite-article
instance-of-recurring pattern withdraws **3 of 82** wrong `same` verdicts,
0 of 417 correct ones. The phrasing is a lottery — the same reason the
check-only arm was rejected as a fix above — and a marker edit re-digests
the rubric (#838), re-spending every stored workspace's verdicts for a 0.04
recovery.

**What holds** is what #869 itself reports holding: the verdict is
advisory, the #776 cross-source note flags the pair at review time, and
`duplicates --keep-distinct` (#797) records the human ruling every surface
honors. The class is real, measured, and priced; neither remedy clears the
bar this repo ships prompt changes at.

## What this does NOT establish

- **The labels are constructed, not adjudicated.** Each pair is written to be
  unambiguous under the rubric the prompt states, so accuracy reads as
  rubric-consistency, not as agreement with a human on a real bundle.
- **`recurrence` and `asym-recurrence` have three items each.** Their
  aggregates move in steps of ⅓; read the per-pair modal verdicts, which
  each report prints.
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
uv run python -u evals/adjudication/run_adjudication_eval.py --arm treatment --runs 15
uv run python -u evals/adjudication/run_adjudication_eval.py --arm baseline --ablate-clause --runs 15
uv run python -u evals/adjudication/run_adjudication_eval.py --arm baseline --ablate-clause --ablate-withdrawal --runs 15
```

`baseline` runs production untouched; each `--ablate-*` flag removes one
shipped mechanism, which is the only way to measure a mechanism that has
already shipped. `treatment` swaps in #869's REJECTED candidate sentence
(kept so its stored arm stays reproducible). `--self-test` needs no model
and no network: it proves the fixture bundle produces exactly one candidate
group per labelled pair, and that neither the ablation nor the treatment is
silently identical to production.

[i838]: https://github.com/jasonssdev/openkos/issues/838
[c558]: https://github.com/jasonssdev/openkos/issues/558
