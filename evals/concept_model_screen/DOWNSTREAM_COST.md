# Downstream cost of the selector judge's cut (#1579)

Status: descriptive measurement. It registers nothing, decides nothing and
changes no code in `src/` or any default. Reproduce with
`uv run python evals/concept_model_screen/downstream_cost.py measure`; the
harness has a model-free `--self-test`. No chat or embedding call is made.

## Question

Steps 1 to 3 of #1579 found no measured benefit of the cut. The open question
in `NEXT_STEP.md` is its downstream cost: the judge keeps 214 objects (`self`)
or 209 (`fixed`) over 25 sources, against 329 without it. Does the extra third
add work in the stages after extraction?

## Method

For each of the 25 primary #1571 records (5 extractors x 5 meetings, seed 0;
the same records and arm logic as `cut_profile.py`) the harness builds two
object sets: **with judge (k)**, the objects the judge kept, and **without
(N)**, the ceiling objects under the positional backstop of 20, which is what
the production path hands on when no judge ran. Each set is written as a
temporary OKF bundle outside the repository (`okf.build_concept`) and the
production candidate generators run over it:

| Stage | Production function | Model-free? | Cost it prices |
|---|---|---|---|
| Identity candidates | `resolution.candidates.find_candidates_report` | yes | one adjudication call and one queue row per retained group (cap 50) |
| Edge typing | `resolution.edge_typing.candidate_edges` | partly | one call per untyped edge; proximity edges need embeddings |
| Contradictions | `resolution.contradiction.plan_candidates` | yes | one call per typed-edge pair or merged body (cap 200) |
| Reindex | `state.reindex.reindex`, with a counting stand-in embedder | yes | one embedding call per chunk |
| Revision candidates | `resolution.decision_revision.plan_revision_candidates` | no | the cosine threshold gates every pair |

Two groupings are reported. **Per source** is the sum over 25 one-source
bundles. **Per extractor workspace** puts all five meetings of one extractor
in one bundle (5 workspaces), which is what lets a pairwise stage see
cross-source pairs. Objects with the same type and slug attach to one
document, as at ingest, so the workspace figure is a few objects lower.

## Result

Arm `self` (the extractor judges); arm `fixed` is in the command output.

| Stage | Grouping | with judge (k) | without (N) | k/N |
|---|---|---|---|---|
| Objects | per source | 214 | 329 | 0.650 |
| Objects | per workspace | 205 | 322 | 0.637 |
| Reindex embedding calls | per source | 214 | 329 | 0.650 |
| Reindex embedding calls | per workspace | 205 | 322 | 0.637 |
| Identity groups (= LLM calls = queue rows) | per source | 4 | 12 | 0.333 |
| Identity groups (= LLM calls = queue rows) | per workspace | 7 | 26 | 0.269 |
| Decisions | both | 72 | 123 | 0.585 |
| Revision pair pool (upper bound) | per source | 0 | 0 | - |
| Revision pair pool (upper bound) | per workspace | 481 | 1308 | 0.368 |
| Edge typing candidates | both | 0 | 0 | - |
| Contradiction candidates | both | 0 | 0 | - |

Arm `fixed`: objects 209 vs 329 (0.635); identity groups 2 of 12 per source
and 3 of 26 per workspace; Decisions 88 of 123; revision pool 692 of 1308
(0.529) per workspace.

No identity group was an exact-title match in either set. Every group is a
near-match (LOW tier).

## Reading

- **Linear stages: objects and reindex embedding calls.** They fall with the
  object count, k/N about 0.64. Removing the cut adds 115 objects and 115
  embedding calls over 25 sources (`self`), 120 for `fixed`. Here each
  description fits one chunk, so calls equal objects; real bodies chunk more,
  so the absolute calls are a floor and the ratio is the usable part.
- **Pairwise stages: identity groups and the revision pool.** They fall faster
  than the object count. Identity groups go 4 of 12 per source and 7 of 26 per
  workspace; the revision pool goes 481 of 1308, and its ratio tracks the
  square of the Decision ratio (0.585 squared is 0.34; 0.715 squared is 0.51).
  Pooling five meetings into one bundle roughly doubles the groups (12 to 26)
  and creates a revision pool that does not exist per source, because the
  eligibility rule excludes pairs from the same source. The super-linear
  growth is in the bundle size, so it keeps growing with every meeting added.
- **Absolute identity load is small.** Removing the cut adds 8 groups over 25
  sources (`self`), or 19 per workspace, each one adjudication call and one
  review row. The cap of 50 never binds.
- **Zero by construction: edge typing and contradictions.** A freshly
  extracted bundle has only provenance links, which are typed `derived_from`
  and excluded. These stages scale with relations added after extraction, not
  with the extraction set, so this measurement says nothing about them.
- **Implied cost of removing the cut (over these 25 sources):** 115 to 120
  more objects and embedding calls; 8 to 10 more identity adjudication calls
  and review rows per source set (19 to 23 per workspace set); and, in a
  multi-meeting workspace, a revision pair pool 1.9 to 2.7 times larger,
  which the similarity threshold would then thin by an unmeasured amount.

## What this licenses, and what it cannot show

It licenses a number for a future pre-registration of the cut's downstream
cost: the per-stage ratios above are the exposure the extra third creates,
and the identity stage is the one with a direct, measured review-row cost.

It cannot show:

- **Revision candidates and proximity edges.** Both are gated by embeddings and
  are not measured. The revision figure is a similarity-blind upper bound, not
  a candidate count; the review rows and calls it implies are unknown.
- **Body-keyed behaviour.** The records store type, title, description and
  `body_chars`, not body text or links. Title-keyed generators see real
  titles; anything that reads bodies sees only the description.
- **Whether the extra objects are worth reviewing.** The count of review rows
  is not their value; steps 1 to 3 found the cut objects neither less
  supported nor redundant.
- **Adopted-corpus scale.** Five meetings per workspace is small; pairwise
  stages are quadratic, so the gap widens with more sources.
- **LLM spend per call.** Only call counts are priced, not their latency or
  token cost.
