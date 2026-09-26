# Exploration: superseded-history-in-query (#1014 piece b)

*Produced by the sdd-explore phase and persisted by the orchestrator. The `synthesis_note` precedent (`retrieval/answer.py:663-669`) and the `Citation.excerpted`/`confidential` fields (`answer.py:336`, `:351`) were spot-checked.*

## Pipeline and attach point
- **`application/query.py::run_query` → `retrieval/answer.py::answer()`:**
  - FTS and dense search;
  - status-aware filtering (`lifecycle.deprecated_concept_ids` + `sensitivity`, via `filter_hits`);
  - RRF fusion into `fused_ids`;
  - `_assemble_context`, a guarded re-read per hit that builds `labels`/`bodies`/`citations`;
  - `_bound_bodies`, one shared budget split with `fair_shares`;
  - `_user_content`, which numbers the blocks;
  - synthesis;
  - `_split_attribution`, which reads `USED:` and filters the citations.
- **Attach point:** inside `_assemble_context`'s loop, right after the successor's own frontmatter is re-read. Its outbound `supersedes` edges are already loaded there, so no bundle-wide walk is needed. Predecessors are appended as extra blocks **before** `_bound_bodies`, so they share the existing budget.
- **Budget interaction:** extra blocks raise the `fair_shares` divisor and shrink every other hit's share. This needs a rule: either a sub-share inside the successor's own share, or the flat pool.

## Chain walk
- The successor holds the outbound edge, so the walk goes S → `decode_relations` → `supersedes` target P → read P → repeat.
- It is cheap: no `okf._iter_docs`, no `build_graph`.
- It needs a depth bound N, a `visited` set (cycle guard; `lifecycle`'s R2 fail-safe covers only the reverse direction), and a rule for multiple predecessors.
- **`revises`:** the predecessor is never hidden, so it may already appear as an ordinary hit. Attaching it again risks a duplicate.

## Event-date labels
- No cheap production helper resolves a concept's event date through its provenance. `provenance_source_ancestors` is whole-bundle, and Phase B's `provenance_source_ancestors_many` is not on main. The eval's `resolve_decision_date` is an eval-only stand-in.
- **Proposed:** a narrow new helper. It reads the chain member's own `provenance:`; for each `sources/` entry it does one guarded read plus `okf.read_event_date`, with at most one further hop. Its cost is bounded by chain length, not bundle size.
- **Layering:** `retrieval/` MUST NOT import `resolution/` (`lifecycle.py:9-10`). The date-state vocabulary needs its own home, or a narrow local type.

## Prompt
- **Recommended:** add no system-prompt text. Use a per-block label suffix instead, mirroring the shipped `Insight` `synthesis_note`: `" (superseded <date>; earlier revision, not current)"`.
- **Gate:** a default-off config key following the `rationale_language` (#812) and `sufficiency_check` threading pattern. When off, there is no walk, no extra read, and the prompt is byte-identical.
- **Measurement:** no `evals/query_*` probe covers supersession narration. `evals/decision_revisions/`'s dated fixture could seed a new probe.

## Citations (options)
- **(A)** Splice the history into the successor's body. This breaks the "a citation names the bytes shown" invariant (#882/#753).
- **(B)** Give the history its own numbered block plus `Citation.superseded: bool = False`. This mirrors `excerpted`/`confidential`, so the model can attribute through `USED:`. It leaves open whether `--save` files it as provenance.
- **(C)** Attach the history but never number it. The model cannot disclose that it used it.

## `--include-deprecated`
The predecessor becomes an ordinary hit, so attaching it again would duplicate it. The options are to suppress the feature under the flag, or to dedupe against `fused_ids`.

## Specs, ADR, size
- **Specs:** `query-answer` (primary), `status-aware-retrieval` (a scenario: an attached predecessor is never a hit), and possibly `query-command`.
- **ADR:** ADR-0026 is likely, because the `Citation` schema and block shape become hard to reverse once `--save` files them.
- **Size and slices:** about 400–500 lines in two slices. Slice 1 is the chain walk, attach, `Citation` field, config and tests. Slice 2 is the CLI/docs surface and a measurement probe.

## Open product decisions
1. Default on or opt-in.
2. Whether history can be cited, and whether `--save` files it as provenance.
3. Whether refinements (`revises`) are narrated too, or reversals only.
4. The chain depth N.
5. Behavior under `--include-deprecated`.

## Risks
- **No cheap event-date helper exists yet.** It is new surface.
- **Budget sharing** needs an allocation rule.
- **Layering** forbids a retrieval → resolution import.
- **Citation and provenance semantics** should be settled before the `Citation` schema ships.

## Decisions (2026-09-26)
- **Owner:**
  - History is cited as its own numbered block, with `Citation.superseded`. `query --save` files it in provenance, marked superseded.
  - Both reversals (`supersedes`) and refinements (`revises`) are narrated, each with its own label. A `revises` predecessor that is already an ordinary hit is not repeated.
- **Orchestrator defaults** (stated to the owner, reversible):
  - an opt-in `openkos.yaml` key, default off, keeping the prompt byte-identical;
  - chain depth 3, with a cycle guard;
  - history suppressed under `--include-deprecated`.
