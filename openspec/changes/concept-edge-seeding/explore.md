# Exploration: concept-edge-seeding (issue #183)

> Artifact store: `hybrid`. Mirrors Engram observation `sdd/concept-edge-seeding/explore`
> (obs #1969). The exploring agent had no write tool; this file was written by the
> orchestrator from that observation, after independently re-verifying the three
> load-bearing code claims at their cited lines.

## Note on retrieval

The exploring session's Engram toolset exposed only `mem_save` (no `mem_search` /
`mem_get_observation`), so the prior exploration `sdd/llm-edge-production/explore`
(obs #1270) and `sdd-init/openkos` could not be retrieved by the agent. All claims
below were re-verified directly against current `main` instead of trusted from that
prior artifact. The orchestrator separately read obs #1270 and confirms its open
question "candidate-edge SOURCING is unspecified" is exactly what this exploration
resolves; its proposed suggestion verb has since shipped as `suggest-relations`.

## Current State (verified against `main`, file:line evidence)

**Extraction has no relation/link field** — CONFIRMED.
`src/openkos/extraction/concept.py:170-182` — `ExtractionResult` is exactly
`{type, title, description, body}`. The system prompt (`concept.py:29-153`) never asks
the model to name a related concept. `model/okf.py::build_concept` (140-206) only emits
a `## Related` section that backlinks each `provenance` entry (the Source it came from),
never concept-to-concept. Nothing in `extraction/` or `model/okf.py` writes body links
between two derived concepts.

**Untyped edges exist only from a bundle-relative body link, sometimes reclassified as
`derived_from`** — CONFIRMED, with a nuance the issue did not mention.
`src/openkos/graph/sqlite_graph.py:242-332` (`_populate_graph_tables`) is the sole edge
producer. Pass 1 (`_LINK_RE`, line 95) scans doc bodies for `[text](/….md)` links; a
matched edge is inserted as `relation_type = "derived_from"` IFF the target id is a member
of the source doc's `provenance:` list (lines 311-317, feature #135 "provenance-mirror
synthesis"), else `relation_type = NULL` (genuinely untyped). Pass 2 reads `relations:`
frontmatter (`okf.decode_relations`) into typed edges (lines 319-330).

Consequence: **every edge ingest currently produces is `derived_from`**, because
`build_concept`'s only body link (`## Related`) always points at a `provenance` entry.
Post-ingest, `graph.db` / `build_graph` DOES contain edges — but every one is typed
`derived_from`, pointing Concept→Source. There are zero Concept↔Concept edges, typed or
untyped, until a human runs `relate`.

**`find_contradictions` excludes `derived_from` from candidate pairs** — CONFIRMED.
`src/openkos/resolution/contradiction.py:187-191` (`_candidate_pairs`) filters to
`edge.relation_type is not None and edge.relation_type != "derived_from"`. Combined with
the above, this is why `contradictions` reports `"No candidate pairs found."`
(`cli/main.py:5037-5038`) even on a bundle with edges — the only edges that exist (Source
`derived_from` links) are excluded by design (#135: "a derivation/provenance link is never
a contradiction candidate").

**`suggest-relations` only types edges that already exist** — CONFIRMED.
`src/openkos/resolution/edge_typing.py::_candidate_edges` (116-138) narrows to untyped
rows (`relation_type is None`) whose pair is not already typed elsewhere. Since ingest
produces zero untyped Concept↔Concept edges (all Concept→Source edges are synthesized
`derived_from`, not `NULL`), `candidate_edges` is empty until a human hand-writes a
`[[link]]` / markdown link between two concepts, or runs `relate` directly. CLI message:
`"No untyped relations found."` (`cli/main.py:4805`).

**The circularity is real and slightly worse than the issue states.** It is not merely
"no candidate edges exist" — ingest actively produces provenance edges that are then
filtered OUT of both `suggest-relations`'s candidate set (untyped-only) and
`contradictions`'s candidate set (non-`derived_from`-only). The graph layer is populated
but structurally inert for both consumers.

## Adjacent, reusable infrastructure discovered

1. **The vector store already exists and is populated at `reindex` time**, independent of
   this issue: `src/openkos/state/vectorstore.py` — a full sqlite-vec `VectorStoreDB` with
   `upsert_many` / `query` (k-NN, ascending distance), already wired through
   `state/reindex.py::reindex()`, which embeds every concept doc's raw text via an injected
   `Embedder` (`llm/base.py:33-42`, `EMBED_DIM=1024`) and stores it incrementally
   (content-hash gated). `status` already reports `vectors.db` presence
   (`cli/main.py:4297-4300`). An embedding-proximity candidate-edge source therefore needs
   **zero new LLM calls** for the embeddings themselves — only the k-NN query is new work,
   and it is a cheap SQL query, not a model call.

2. **A deterministic, config-free candidate-generation module already exists for a
   structurally identical problem** (merge dedup, not edges):
   `src/openkos/resolution/candidates.py` (`find_candidates`) plus
   `resolution/similarity.py` (`near_match_score`, difflib-based). This is the
   entity-resolution precedent: whole-bundle walk, partition by type, deterministic tiered
   scoring (HIGH exact-key / LOW near-match), ephemeral dataclass output, consumed only by a
   later human/LLM step. It is the architectural template a candidate-edge module should
   mirror — bundle walk → deterministic candidate pairs → hand off to existing
   `suggest_edge_types`.

3. **`AGENTS.md:42`'s LLM-call prescription is still stale — CONFIRMED, still live.**
   `AGENTS.md:42` says LLM calls should "use Pydantic-validated structured output (e.g.
   `instructor`) with retry." But `pyproject.toml:66` has `pydantic` as a **dev-only**
   dependency, and `instructor` is not a dependency at all. Every existing LLM consumer
   (`extraction/concept.py`, `resolution/edge_typing.py`, `resolution/contradiction.py`,
   `resolution/adjudication.py`) hand-rolls JSON parsing via
   `llm/parsing.py::extract_json_object` / `extract_json_items`, fail-closed, no retry, no
   Pydantic schema. This gap predates this issue and should NOT be silently "fixed" here —
   that would be an unrequested architectural pivot for the whole codebase driven by one
   issue's edge case. Flagged as an open decision, not an assumption.

4. **Edge identity semantics.** `graph/base.py::Edge` (20-36) has no `id` field;
   `SqliteGraphStore.edges()` sorts by `(source_id, target_id, relation_type)`. Any
   candidate-edge producer must not collide with this. `merge_relations` /
   `build_merged_document` (`model/okf.py:599-812`) only rewire TYPED `relations:`
   frontmatter entries (`(target, type)` identity per `Relation`, `model/okf.py:479-496`);
   untyped body-link edges and any new "candidate" edge kind are NOT part of the
   merge/unmerge reversibility ledger today. A candidate-edge kind that is NOT written to
   `relations:` frontmatter (i.e. stays projection-only, like today's untyped body-link
   pass) does not touch merge/unmerge reversibility. One that IS written to frontmatter
   WOULD need merge/unmerge handling — **this is the single biggest blast-radius fork in the
   whole design space.**

## Candidate-Sourcing Options

| Option | LLM call volume | Determinism | `Relation` codec interaction | Schema/frontmatter change |
|---|---|---|---|---|
| **A. LLM-emitted links at extraction time** (issue direction 1: extend `ExtractionResult` with a `links`/`relations` field) | +0 extra calls (folds into the existing 1 call/source in `extract_concept`), but the model must know OTHER existing concept ids/titles — needs the full concept catalog injected into the prompt, which grows with bundle size (context-window risk at scale) | Non-deterministic (LLM choice); needs fail-closed per-link validation like `_validate` | New: extraction would emit `(target, type?)` pairs; `build_concept` would resolve target strings to real concept ids (fuzzy match against catalog) — a NEW validation surface, not `okf.Relation`'s existing codec | Cheapest schema-wise IF `build_concept` emits a literal markdown link the existing `_LINK_RE` pass already understands; otherwise a new frontmatter key |
| **B. Post-ingest embedding proximity** (issue direction 2, using the vector store): k-NN the new concept's embedding against `vectors.db`, emit candidate untyped edges to the top-K nearest concepts above a distance threshold | **Zero new LLM calls** — reuses embeddings `reindex` already computes; the k-NN query is pure SQL | Fully deterministic given a fixed embedding model + threshold | None — candidate edges are synthesized the same way `_LINK_RE`'s untyped pass already is | Zero IF kept projection-only (a new pass inside `sqlite_graph.py`); a schema change only if written durably |
| **C. Co-occurrence within a source** (direction 2 variant: 2+ concepts extracted from the SAME source share provenance) | Zero new LLM calls — pure structural inference from `_stage_derived_objects`'s known batch | Fully deterministic | None if projection-only; could piggyback on the same provenance-mirror mechanism | Zero if synthesized read-time; cheap win, but only wires SAME-BATCH siblings, not cross-source — narrower coverage than B |
| **D. Existing untyped body links** (rejected by the issue) | N/A | N/A | N/A | Already implemented; does not scale past hand-curated bundles — the issue's own reasoning holds, no new evidence contradicts it |
| **E. Whole-bundle pairwise** (not named by the issue; named here to reject it) | O(n²) LLM calls if judged directly — prohibitive even at ~30 objects (435 pairs) | N/A | N/A | Rejected on cost alone; viable only as a last-resort fallback bounded by the same `_MAX_PAIRS`-style cap `contradiction.py` / `edge_typing.py` already use |

**Assessment.** **Option B (embedding proximity) is the strongest single candidate-edge
source** — free (no new LLM calls, reuses `reindex`'s embeddings), deterministic, and
requiring no `Relation` codec or frontmatter change if implemented as a THIRD pass inside
`sqlite_graph.py`'s projection build (mirroring the two-pass docstring at
`sqlite_graph.py:10-47`). It would produce `NULL`-typed rows (or a new synthesized type,
mirroring the `derived_from` synthesis precedent at lines 311-317) that `_candidate_edges`
already picks up via its pair-level untyped filter — **zero changes needed to
`suggest_relations` / `_candidate_edges` at all.**

Option C (co-occurrence) is a strictly narrower, essentially-free complement that could
ship alongside B or be dropped. Option A is the highest blast-radius and lowest-determinism
option — it changes `ExtractionResult`'s schema, requires injecting the full concept
catalog into the classification prompt (a scaling risk B/C do not have), and needs a new
fuzzy title→id resolution step with its own failure modes. It should not be the first slice.

## Blast Radius Map

- **`model/okf.py`** (`Relation`, `encode_relation` / `decode_relation`, `merge_relations`):
  UNTOUCHED by B/C if candidate edges stay projection-only. Option A would require either
  reusing the existing untyped-link body-write path (no codec change) or a new frontmatter
  field (codec change plus merge/unmerge implications — `merge_relations`'s self-loop/dedupe
  logic at `okf.py:599-664` is scoped to `relations:` list entries; a new key would not
  automatically get that handling).
- **`bundle/relations.py`** (rewrite/reverse for merge): UNTOUCHED by B/C — projection-only
  edges are recomputed on every `build_graph` call and never persisted, the same reason
  today's `derived_from` provenance-mirror synthesis does not touch it.
- **`graph/sqlite_graph.py`**: DIRECTLY MODIFIED for B/C — a third edge-extraction pass in
  `_populate_graph_tables` (242-332), following the two-pass pattern documented in the module
  docstring (10-47). Needs its own dedup key and insertion-order determinism guarantee,
  mirroring the existing `edge_pairs` / `typed_edges` set pattern (lines 305-330).
- **`resolution/*`**: `edge_typing.py` / `contradiction.py` need ZERO changes for B/C if the
  new pass emits `relation_type = NULL`. If a NEW synthesized type is chosen instead,
  `contradiction.py::_candidate_pairs` needs an explicit exclusion added (lines 187-191) the
  same way `derived_from` is excluded today — a same-shaped one-line change, low risk.
- **`extraction/*`**: UNTOUCHED for B/C. DIRECTLY MODIFIED (schema + prompt + validation) for A.
- **CLI verbs**: `suggest-relations` / `contradictions` / `status` need the legibility fix
  regardless of sourcing option. `reindex` (or a new dedicated pass triggered from `ingest`)
  is the natural home for B's k-NN computation, since it already holds the fresh embedding
  right after `upsert`.
- **New module needed for B**: a `graph`- or `resolution`-layer function shaped like
  `resolution/candidates.py` (deterministic, config-free, ephemeral dataclasses) that queries
  `vectors.db` for each concept's nearest neighbors and emits candidate edges above a distance
  threshold. This is new code with its own threshold-tuning and test surface.

## The Legibility Fix (empty graph vs. no candidates)

All three decision points are already isolated one-liners:

- **`suggest-relations`**: `cli/main.py:4804-4806` —
  `if total == 0: typer.echo("No untyped relations found."); return`. To distinguish "graph
  has zero edges at all" from "graph has edges but none untyped/unclaimed," the CLI needs one
  more cheap signal (a `graph_is_empty(bundle_dir)` helper, or extending `candidate_edges`'s
  return to include graph size). `candidate_edges` already opens `build_graph` internally
  (`edge_typing.py:307`), so this is small and additive.
- **`contradictions`**: `cli/main.py:5037-5038`, backed by `find_contradictions`'s
  `(verdicts, total_pair_count)` return (`contradiction.py:381-468`). It already distinguishes
  cap-reached from nothing-to-judge, but not "zero typed edges anywhere" from "typed edges
  exist but all are excluded." The same helper applies.
- **`status`**: `cli/main.py:4228-4306` already reports `vectors_db_path.exists()` as a
  needs-attention line (4297-4300) — the natural place to also report
  "N concept-to-concept edges (M typed)" or "No concept relationships yet." Same shape as the
  existing dangling-target check (4293-4296), so low risk to extend.

**This fix is cleanly separable into its own small slice.** It touches only `cli/main.py`
message-selection logic plus at most one small read-only helper, has NO dependency on which
sourcing option ships, and could land FIRST as a low-risk, fast PR.

## Slicing

- **Slice 0 (small, independent, ship first)**: legibility fix — `status` /
  `suggest-relations` / `contradictions` distinguish "empty graph" from "no candidates."
  Estimated <150 lines including tests.
- **Slice 1 (the core fix)**: Option B as a new third pass in `graph/sqlite_graph.py`, plus a
  new threshold-tuned candidate-scoring module (shaped like `resolution/candidates.py`), plus
  wiring so `reindex` (or `ingest`) triggers it once embeddings exist. Estimated 300-500 lines
  including tests, following the existing `test_sqlite_graph.py` / `test_candidates.py`
  patterns.
- **Slice 2 (optional, separable)**: Option C (same-source co-occurrence) — cheap and small;
  fold into Slice 1 or drop as future scope. Estimated <150 lines.
- **Option A is NOT recommended for this change at all** — different risk profile, different
  blast radius, and duplicates what B already solves for less cost. If wanted, it should be
  its own future change.

This fits as ONE change with two chained PR slices (Slice 0, then Slice 1), both comfortably
under the 800-line change budget and each individually under or near the 400-line single-PR
guard.

## Open Decisions For The Human (before `sdd-propose`)

1. **Candidate-edge persistence model.** Should embedding-proximity candidate edges be purely
   projection-ephemeral (recomputed on every `build_graph` call, like today's `derived_from`
   synthesis — zero frontmatter change, zero merge/unmerge impact), or materialized as a real
   body link / frontmatter entry (durable, but reopens the `Relation` / merge blast radius)?
   *Recommendation: projection-ephemeral, matching the existing `_LINK_RE` + `derived_from`
   precedent.*
2. **Where the k-NN candidate computation lives and when it runs.** A `sqlite_graph.py` third
   pass invoked on every `build_graph()` / `suggest-relations` call (always fresh, but adds
   vector-store I/O to every graph read), or a `reindex`-time precomputation cached alongside
   `graph.db` (faster reads, but another cache-invalidation gate to design, mirroring
   `state/reindex.py`'s manifest-hash gates)? Affects performance and the interaction with
   `write_graph_store`'s on-disk persistence contract (`sqlite_graph.py:358-410`).
3. **Distance/similarity threshold and top-K.** What cutoff produces useful candidates without
   flooding `suggest-relations` with noise (mirrors `resolution/similarity.py`'s
   `SIMILARITY_THRESHOLD = 0.75` precedent — needs its own empirically chosen, documented
   constant).
4. **`AGENTS.md:42` vs. reality (Pydantic/instructor)** — CONFIRMED STILL LIVE. Ignore the
   prescription and hand-roll JSON parsing like every other consumer, or reconcile `AGENTS.md`
   with reality? *Recommendation: hand-roll for THIS change — Option B needs no new LLM call
   at all, so the decision may not even be triggered by Slice 1.*
5. **Does `Relation` / edge gain provenance or confidence fields?** `Relation`
   (`okf.py:479-496`) has none; `EdgeSuggestion` (ephemeral, `edge_typing.py:85-99`) carries a
   `rationale` but no numeric confidence. Surfacing a distance/similarity score to a human
   reviewer would be a new ephemeral field on the candidate dataclass — it does NOT require
   touching the persisted `Relation` schema, given decision 1.
6. **Should `ingest` itself trigger candidate-edge computation**, or is this exclusively a
   `suggest-relations` / `reindex`-time concern? Determines whether a fresh `ingest`
   immediately shows candidate edges in the SAME run, or requires a follow-up invocation — a
   UX question the issue's reproduction steps suggest matters to the reporter.

## Testing Surface

Test command: `uv run pytest` (coverage via `uv run pytest --cov`, `fail_under = 90`, branch
coverage on). Strict TDD is active.

- `tests/unit/graph/test_sqlite_graph.py` — exercises the two-pass extraction; a THIRD pass
  needs new tests here, following its fixture/fake patterns (likely a fake `VectorStore` /
  `Embedder`, mirroring `tests/unit/state/test_vectorstore.py`).
- `tests/unit/resolution/test_edge_typing.py` — covers `_candidate_edges` / `candidate_edges` /
  `suggest_edge_types`. If B emits projection-ephemeral untyped rows, this module needs no
  changes, but its "already-typed pair excluded" tests must be re-run for regressions.
- `tests/unit/resolution/test_contradiction.py` — covers the `derived_from` exclusion; a NEW
  synthesized type would need a new exclusion test mirroring it.
- `tests/unit/resolution/test_candidates.py` + `test_similarity.py` — the closest template for
  a new embedding-proximity-scoring suite.
- **No existing test exercises the FULL ingest → suggest-relations → contradictions chain
  end-to-end** with a real multi-object bundle. A new integration-style test demonstrating
  "ingest N sources → candidate edges appear → suggest-relations types them → contradictions
  finds pairs" would directly validate the issue's reported symptom and should be part of
  Slice 1's acceptance criteria. Strict TDD means this test (or a scoped-down unit version)
  is written FIRST, red, before any `sqlite_graph.py` change.
- Slice 0 needs new CLI-level tests asserting the two DISTINCT messages; verify the exact
  existing harness in `tests/unit/cli/` before writing rather than assuming.

## Recommendation

Ship as ONE change (`concept-edge-seeding`) with two chained PR slices: Slice 0 (legibility
fix, first, low risk) then Slice 1 (embedding-proximity candidate-edge sourcing as a
projection-ephemeral third pass in `graph/sqlite_graph.py`, feeding the EXISTING
`suggest-relations` / `_candidate_edges` machinery unchanged). Do not pursue LLM-emitted
extraction-time links (Option A) in this change.

## Risks

- Six open decisions above must be answered before `sdd-propose`, especially #1 (persistence
  model) and #2 (where the k-NN pass lives) — these materially change blast radius and could
  turn a 2-slice change into a bigger one.
- Embedding proximity is a recall-oriented heuristic, like `resolution/similarity.py`'s own
  documented tradeoff; it will produce false-positive candidate pairs. Acceptable because
  `suggest-relations` / `relate` remain human-gated, but the threshold (#3) needs deliberate
  tuning, not a rushed constant.
- The exploring session could not retrieve obs #1270 or `sdd-init/openkos` via Engram search;
  all "verify against main" claims were independently re-derived from source. The orchestrator
  has since read #1270 and found no contradiction.

## Ready for Proposal

Yes, contingent on the human resolving the six open decisions — `sdd-propose` should present
these as explicit choices rather than assume answers.
