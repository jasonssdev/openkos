# Proposal: concept-edge-seeding (issue #183)

> Artifact store `hybrid`. Mirrors Engram `sdd/concept-edge-seeding/proposal`.
> Upstream: `explore.md` in this folder (Engram `sdd/concept-edge-seeding/explore`, obs #1969).
> Design decisions settled by the maintainer are recorded in Engram obs #1971 and are
> CONSTRAINTS here, not options.

## Intent

`ingest` produces edges, but every one of them is `derived_from` Concept→Source, so both
downstream consumers discard them and the knowledge graph is structurally inert.

Verified evidence:
- `graph/sqlite_graph.py:242-332` is the only edge producer. A body link whose target is in the
  doc's `provenance:` list is synthesized as `derived_from` (311-317); otherwise `NULL`.
- `model/okf.py:140-206` (`build_concept`) writes exactly one body link — a `## Related`
  backlink to each `provenance` entry — so the untyped branch never fires at ingest time.
- `resolution/edge_typing.py:116-138` narrows candidates to `relation_type is None` → empty set
  → `"No untyped relations found."` (`cli/main.py:4805`).
- `resolution/contradiction.py:187-191` excludes `derived_from` → `"No candidate pairs found."`
  (`cli/main.py:5037-5038`).

Success: after `ingest`, concept-to-concept candidate edges exist without human hand-linking,
and every empty result explains *why* it is empty.

## Scope

### In Scope

**Slice 0 — legibility (independent, ships first, <150 lines).** `suggest-relations`
(`cli/main.py:4804-4806`), `contradictions` (`cli/main.py:5037-5038`) and `status`
(`cli/main.py:4228-4306`, next to the existing `vectors.db` needs-attention line at 4297-4300)
must distinguish "the graph has no concept-to-concept edges yet" from "candidates existed but
none matched". Needs at most one small read-only graph-size helper. No dependency on Slice 1.

**Slice 1 — candidate-edge sourcing (300-500 lines).**
1. A third pass in `graph/sqlite_graph.py::_populate_graph_tables`, run on every `build_graph()`,
   that k-NN queries `state/vectorstore.py` per concept and emits candidate edges above a
   distance cutoff. Projection-ephemeral, dedup key and insertion-order determinism mirroring the
   existing `edge_pairs` / `typed_edges` sets (305-330).
2. A new deterministic, config-free candidate-scoring module shaped like
   `resolution/candidates.py` + `resolution/similarity.py` (ephemeral dataclasses, may carry a
   distance score; no persisted-schema change).
3. `ingest` wiring so candidates appear in the SAME run.

### Out of Scope (non-goals)

- **Option A (LLM-emitted links at extraction time).** Higher blast radius, non-deterministic,
  and requires injecting the full concept catalog into the classification prompt — a scaling risk
  Option B does not have. Its own future change if wanted.
- **Durable persistence.** Candidate edges are NEVER written to `relations:` frontmatter.
  Therefore zero changes to `model/okf.py` (`Relation` 479-496, `encode_relation`/
  `decode_relation`, `merge_relations` 599-812) and zero changes to `bundle/relations.py`;
  merge/unmerge reversibility is untouched.
- **Reconciling `AGENTS.md:42` (Pydantic/instructor).** The gap is real and still live
  (`pyproject.toml:66` has `pydantic` dev-only; `instructor` absent), but this change needs no
  new LLM call at all. Not this change's job.
- **Option C (same-source co-occurrence)** and threshold auto-tuning. Deferred.
- **Fixing issue #187** (ingest's Ollama-down degradation) beyond not regressing it.

## Hard requirement: ingest must degrade, never fail

Decisions 2 + 3 create a real tension. A `build_graph()`-time k-NN pass can only see concepts
that already have an embedding in `vectors.db`, and embeddings come from
`state/reindex.py::reindex()` via an injected `Embedder` — a live Ollama dependency. `ingest`
today builds only a chat client (`cli/main.py:1424`), not an embedder, and already degrades
gracefully when Ollama is unreachable: it keeps the Source and reports
`"concept extraction skipped"` (`cli/main.py:1087-1093`).

Therefore:
1. An unreachable or failing embedder MUST NOT fail the `ingest` write. Same shape as the
   existing degrade path: keep the write, report to stderr.
2. The missing-embedding state MUST NOT be silently indistinguishable from "no candidates
   found". Slice 0's vocabulary must cover a third state: *candidates not computable yet
   (embeddings missing)*.
3. `build_graph()` with an absent or empty `vectors.db` MUST remain a successful, non-fatal read.

## Approach

Option B (embedding proximity) — see `explore.md`'s option table. Zero new LLM calls: `reindex`
already embeds every concept doc (`state/vectorstore.py`, `EMBED_DIM=1024`); the k-NN query is
plain SQL over sqlite-vec.

**Open for design — candidate row typing.** Two shapes:

| Shape | Cost | Tradeoff |
|---|---|---|
| `relation_type = NULL` (**recommended**) | Zero changes to `edge_typing.py` and `contradiction.py` — `_candidate_edges` picks them up as-is | Candidate edges become indistinguishable from genuine hand-written untyped body links; `status`/`suggest-relations` cannot label their provenance |
| New synthesized type, mirroring `derived_from` (311-317) | Self-describing; `status` can report them separately | Requires an exclusion in `contradiction.py:187-191` (one-line, same shape as today's `derived_from` exclusion) AND a change in `edge_typing.py:116-138` so they still reach `suggest-relations` |

Recommendation: start `NULL`-typed for the smallest blast radius. `sdd-design` settles it, along
with the distance cutoff and top-K (precedent: `resolution/similarity.py`'s
`SIMILARITY_THRESHOLD = 0.75`).

## Capabilities

### New Capabilities
- `candidate-edge-seeding`: deterministic embedding-proximity sourcing of projection-ephemeral
  concept-to-concept candidate edges, and its ingest-time trigger with graceful degradation.

### Modified Capabilities
- `graph-projection`: adds a THIRD edge-extraction pass alongside the two documented at
  `openspec/specs/graph-projection/spec.md` ("Edges Extracted From Bundle-Relative Markdown
  Links", "Edge `relation_type` Populated From Frontmatter…"). The read-only derived-cache
  requirement must still hold.
- `status`: reports concept-to-concept edge counts and the "embeddings missing" state.
- `llm-edge-production`: `suggest-relations` empty-result messaging distinguishes empty graph
  from no candidates.
- `contradiction-detection`: same messaging distinction for `contradictions`.
- `ingestion`: `ingest` triggers candidate-edge computation and degrades without failing the
  write.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/graph/sqlite_graph.py:242-332` | Modified | Third k-NN pass in `_populate_graph_tables` |
| new module (`graph/` or `resolution/`) | New | Deterministic candidate scoring, shaped like `resolution/candidates.py` |
| `src/openkos/cli/main.py:4228-4306, 4804-4806, 5037-5038` | Modified | Slice 0 messaging + `status` line |
| `src/openkos/cli/main.py` ingest path (~1424, 1082-1101) | Modified | Embedder wiring + degrade branch |
| `src/openkos/state/vectorstore.py` | Read-only consumer | k-NN `query` reused as-is |
| `model/okf.py`, `bundle/relations.py`, `extraction/*` | UNTOUCHED | Guaranteed by the ephemeral-persistence decision |
| `resolution/edge_typing.py`, `resolution/contradiction.py` | Untouched if `NULL`-typed | Otherwise one exclusion each |

## Delivery: chained PRs (`auto-chain`)

Two child PRs; Slice 0 first because it is independent and de-risks review of Slice 1.

```
main
 └── PR#1  slice-0 legibility        (<150 lines)   📍 first
      └── PR#2  slice-1 seeding      (300-500 lines)
```

PR#2 targets PR#1's branch (Feature Branch Chain); retarget/rebase if PR#1's diff leaks into
PR#2. Review-workload forecast:

- `Decision needed before apply: No` (strategy cached as `auto-chain`)
- `Chained PRs recommended: Yes`
- `400-line budget risk: Medium` — total 450-650 lines, but each slice lands under the 400-line
  per-PR guard; Slice 1 is the one to watch and may split again if tests push it over.

## Acceptance Criteria

**Slice 0**
- [ ] `suggest-relations` on a bundle with zero concept-to-concept edges emits a DIFFERENT
      message than on a bundle with edges that are all already typed.
- [ ] `contradictions` makes the same distinction.
- [ ] `status` reports concept-to-concept edge count (and typed count), plus the
      embeddings-missing state.
- [ ] New CLI tests assert the distinct messages; verify the existing `tests/unit/cli/` harness
      shape before writing rather than assuming.

**Slice 1**
- [ ] RED-first integration-style test proving issue #183's symptom: ingest N sources →
      candidate edges appear → `suggest-relations` types them → `contradictions` finds pairs.
      No such end-to-end test exists today; strict TDD requires it written failing first.
- [ ] `build_graph()` third pass is deterministic (stable edge order, stable dedup) and does not
      alter existing pass-1/pass-2 output — existing `tests/unit/graph/test_sqlite_graph.py` and
      `tests/unit/resolution/test_edge_typing.py` pass unchanged.
- [ ] `ingest` with an unreachable embedder still writes the Source and concepts, exits 0, and
      reports the degrade on stderr.
- [ ] `build_graph()` with absent/empty `vectors.db` succeeds and yields zero candidates.
- [ ] `uv run pytest` green; branch coverage stays at/above `fail_under = 90`.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| k-NN on every `build_graph()` slows graph reads | Med | Bounded top-K; measure on the demo bundle; escalate to design if cost is real (no cache gate is allowed by decision 2) |
| Embedding proximity is recall-oriented → false-positive pairs | High | Acceptable: `suggest-relations`/`relate` stay human-gated. Threshold chosen deliberately in design, documented like `SIMILARITY_THRESHOLD` |
| `ingest` gains an Ollama embedder dependency | Med | Hard requirement above: fail-open, never fail the write; distinct message for the missing-embedding state |
| `NULL`-typed candidates are indistinguishable from real untyped body links | Med | Design decides typing shape; Slice 0's `status` line surfaces counts either way |
| Slice 1 exceeds 400 lines with tests | Med | Split the new scoring module from the `sqlite_graph.py` pass into two PRs if the forecast holds |

## Rollback Plan

Both slices are additive and independently revertable. Slice 1's edges are projection-ephemeral:
reverting the third pass makes them vanish on the next `build_graph()` with no migration, no
frontmatter cleanup, and no stale `graph.db` state that outlives the code. Slice 0 is
message-selection logic only. `git revert` per PR is sufficient.

## Dependencies

- `vectors.db` populated by `state/reindex.py::reindex()` (already shipped).
- Ollama reachable for embeddings; explicitly optional at ingest time per the degradation
  requirement.

## Proposal question round

Execution mode is `auto` and the maintainer already settled the blocking product decisions
(obs #1971), so no interactive round was run. Residual product questions handed to `sdd-design`,
not assumed here: (a) the distance cutoff / top-K that keeps candidates useful without flooding
`suggest-relations`; (b) whether candidate rows are `NULL`-typed or a new synthesized type;
(c) whether the human-facing candidate should surface its similarity score.
