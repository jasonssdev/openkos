# `reconcile_screen` — does a model keep what two notes say when it merges them? (#1563)

**Status: prepared, not run.** No model has been called for this screen. The labels
are one author's until the owner's blind labelling is recorded
([`PREREGISTRATION-DRAFT.md`](PREREGISTRATION-DRAFT.md) lists what is blocked on
that). It measures; it adopts nothing and changes no production code, default,
prompt or task key. Part of #1556; refs #1563, #1557.

```bash
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/reconcile_screen/run_reconcile_screen.py --self-test
OLLAMA_HOST=http://127.0.0.1:1 uv run python evals/reconcile_screen/run_reconcile_screen.py forecast --runs 5
uv run python evals/reconcile_screen/run_reconcile_screen.py blind-sheet --out ~/reconcile-blind-sheet.md
uv run python evals/reconcile_screen/run_reconcile_screen.py adjudicate --sheet ~/reconcile-blind-sheet.filled.md
# after registration only:
uv run python -u evals/reconcile_screen/run_reconcile_screen.py run --model qwen3:8b --runs 5
uv run python evals/reconcile_screen/run_reconcile_screen.py score
```

`--self-test`, `forecast`, `blind-sheet` and `adjudicate` never contact a model.

## Why this task, of the four left in #1563

Revisions, reconcile, decision subject and titles are the four tasks #1563 lists
that have no screen. Edge typing and volatility typing are prepared or done
elsewhere.

| Task | What the model is asked | How often | Who sees the output | Evidence today |
| --- | --- | --- | --- | --- |
| **Reconcile merged body** (`resolution/reconciliation.py`) | Rewrite two overlapping notes as one document that covers every distinct claim once | One call per `merge` whose stacked share is over the threshold (also `curate` merges, `--reconcile` forces it) | **The rewritten body is written into the survivor after the user consented to the merge, not shown first.** The consent plan discloses that a model call happens, not what it will say. Only a length floor (half the longer note), a frontmatter check and a stacked-heading check guard it. `unmerge` can restore, but only if someone notices | None. No harness; `merge` runs it on the global `model:` |
| Decision revisions (`resolution/decision_revision.py`) | Judge whether a later Decision revises an earlier one | One call per candidate pair per `revisions` run | A finding in the review queue; a human accepts or declines it | `evals/decision_revisions/`: 58 labelled pairs, one stored run, qwen3:8b against qwen3:14b |
| Decision subject (`resolution/decision_subject.py`) | Name the subject a Decision is about | **Never in production**: the subject pass was dropped, only the harness calls it | Nobody | Covered through `decision_revisions` |
| Titles (`query_title`) | None. The saved-insight title is derived by a deterministic ladder from the question and answer; no model chooses it | Not a model call | The slug is the Concept ID, but the rule is pure code | `evals/query_title/`, 170 filings |

**Pick: reconcile.** It is the only one whose wrong answer is applied silently to a
canonical document: a dropped fact or an invented figure lands in the bundle with no
review step between the model and the file. It is also measurable without judging
prose, because the failure that matters (a claim lost, repeated or invented) has a
literal signature. Revisions already has a labelled fixture and a human gate; subject
has no caller; titles have no model.

## What production does

`merge` writes the survivor's body, then the absorbed body under
`## Merged content (<id>)` (the **stack**). When the stacked share is high enough it
asks the model for one document instead, after consent. `reconcile_merged_body`
sends `SUBJECT`, `NOTE A` (survivor) and `NOTE B` (absorbed) under a system prompt
that asks for "every distinct claim from both notes exactly once, in one voice",
"keep concrete details", "do not add new claims". It refuses an empty reply, a reply
that starts with frontmatter, one that still carries the stacked heading, and one
under half the longer note; then it pins the leading heading to the survivor's title
and restores any `Related` link the model dropped. Every refusal and every backend
error keeps the stack. So the stack is what the user gets whenever the model call
does not help, and it is the first thing a model must beat.

## The fixture

`fixture.json`: **15 cases, 121 claims.** Each case is two notes about one subject,
quoted **verbatim** from repository files (12 cases from `AGENTS.md`, `docs/vision.md`,
`docs/philosophy.md`, `docs/architecture.md`, `CONTRIBUTING.md` and three ADRs; 3 from
the `good-life-demo` bundle's person, decision, concept and source documents). A note
is 253 to 1,354 characters. `provenance_problems` checks every piece is still in its
source file, and `run` refuses to start if one is not.

Each claim says which note states it, labelled by the first author: **42 only in NOTE A,
46 only in NOTE B, 33 in both.** Two claims are flagged as arguable (`sync-core-07`,
`maria-salazar-03`). A claim is **kept** when its literal anchors survive in the merged
body (casefolded, whitespace collapsed). A claim both notes state must also appear
**exactly once**, because that is what the production prompt asks for; its `once`
literal occurs exactly once in each note. `integrity_problems` checks all of it: every
anchor is in the right note and absent from the other, every `once` literal is unique in
each note, no anchor is in the case title (production pins the title as the heading, which
would hand every model the claim for free), no id repeats. Scoring is per case, the
share of its claims kept, so every case weighs the same.

A call that fails or is refused is scored as the stack, never skipped.

### Fabrication

`novel_tokens` flags a digit-bearing word or a code span in the merged body that neither
note nor the title contains: an invented version, figure, path or identifier. It cannot
see an invented sentence made of ordinary words. It is a veto input, not a quality score.

### Baselines and discrimination

All computed with no model, on the first author's labels, strict:

| Predictor | Claims kept | Mean case score |
| --- | --- | --- |
| `sentence_union` (every NOTE A sentence plus each NOTE B sentence not overlapping a kept one by Jaccard 0.30 on content words) | **93 of 121** | **0.770** |
| `stack` (what production keeps on a refusal) | 88 of 121 | 0.729 |
| `longer_only` | 81 of 121 | 0.672 |
| `absorbed_only` | 79 of 121 | 0.654 |
| `survivor_only` | 75 of 121 | 0.617 |

The union threshold was fixed before any number was computed from it and is not tuned. The
**strongest baseline is `sentence_union`, and it is the reference a model must beat.**

The fixture leaves room to win: the strongest baseline gets **28 of 121 claims** wrong, and
**14 of 15 cases** are merged perfectly by no baseline. By construction no claim is wrong
under *every* baseline (a one-sided claim is kept by the stack, a shared one by a single
note), so that count is 0 and is reported as 0. A baseline that kept every claim and said
nothing twice would score 1.0; none does.

## What this does not measure

- **Anchors are literals.** A model that keeps a claim in other words is scored as having
  lost it. The prompt says "keep concrete details", and the anchors are chosen concrete
  (identifiers, figures, quoted terms), but a paraphrasing model is penalised and a
  literal-copying one is not.
- **Prose quality, voice and coherence are not scored.** A body that lists every anchor in
  a bag passes; the oracle in the self-test does exactly that.
- **Notes are repository documents, not ingest-produced concept bodies.** A real merge
  merges two model-written concept bodies with `## Related` and `# Citations`. The notes
  here are shorter and cleaner than that (up to 1,354 characters).
- **Dedupe is a count of one literal.** A model that mentions a shared claim twice for a
  reason loses it.
- **One author chose the claims.** They cover the facts a reader would check first, not every
  sentence; unclaimed content is not scored.
- **Five seeds** show stability, not power; the fixture limits power (15 cases).
- English only.

## Second labelling

`blind-sheet` writes a sheet for the owner: each case's two notes, then its claims in a
seeded shuffle, one question per claim ("which note states it": `A`, `B` or `AB`). It
withholds the first author's kind and flag, the anchors, every id, every source path and the
fixture order of the claims (the author wrote them grouped, so the order alone would leak the
kind). `--self-test` plants a leak in a claim id, a case id and a source path and checks none
reaches the sheet. The sheet is refused inside the repository.

`adjudicate` compares the filled sheet with the first author's labels and prints Cohen's
kappa and every disagreement; it writes nothing into `fixture.json`. Each disagreement is
resolved by the owner and recorded in the fixture the way the volatility fixture records
it (`adjudication` with `first`, `second`, `resolved`, `reason`, and the first label kept as
`alt_label`). A claim that moves between one-sided and shared **cannot be re-scored** until it
has a `once` literal that occurs exactly once in each note, or is retired with a reason
(`retired`); `integrity_problems` and the sheet both skip retired claims.

## Files

| File | Role |
| --- | --- |
| `fixture.json` | the 15 cases, their claims and the first author's labels |
| `reconcile_core.py` | fixture contract, scoring, baselines, fabrication, summaries, bootstrap, vetoes, forecast |
| `reconcile_blind_adjudication.py` | the blind sheet and `adjudicate` |
| `run_reconcile_screen.py` | `forecast`, `run`, `score`, `blind-sheet`, `adjudicate`, `--self-test` |
| `PREREGISTRATION-DRAFT.md` | the draft pre-registration, blocked on the owner's labelling |
| `tests/unit/test_reconcile_screen_core.py` | the deterministic parts, executed |
