# Proposal: bound what candidate-edge seeding generates

**Issue**: [#378](https://github.com/jasonssdev/openkos/issues/378) (P0).
**Baseline**: `main` @ `87b3aba`. **Mode**: hybrid.
**Upstream**: Engram `sdd/bounded-edge-seeding/explore` (obs #2381).

## Intent

A complete run over 15 sources / 30 objects nominated **74 untyped candidate edges**, which became
74 sequential `suggest_edge_types` calls plus 73 sequential `find_contradictions` calls. `curate`
took **17m19s**, Ollama's prompt cache hit its 8 GiB ceiling, and the machine was unusable until
Ollama was killed. This is a stability failure, not a slow path.

Two things generate that volume, and neither is the one the issue names.

### Correction: the issue's premise is half wrong

**"Switch from all-pairs to top-k per node" is already shipped** (#183, archived as
`2026-07-27-concept-edge-seeding`). Verified on `main`:

| Evidence | Location |
|---|---|
| `TOP_K: Final[int] = 5` — hard per-node cap | `graph/proximity.py:67` |
| ONE k-NN query per node, `neighbors(concept_id, TOP_K + 1)`; at most `TOP_K` hits kept above the `MAX_NEIGHBOR_DISTANCE` floor; canonical `(min, max)` dedup keeping the smaller distance; stable sort | `graph/proximity.py:115-152` |

Candidate **volume** is therefore already linear (union-of-k, bounded by `k·n`), never O(n²). The
issue's "435 possible pairs → 74 candidates" framing is factually wrong: those 74 came out of the
top-k union, not an all-pairs walk. Only the underlying k-NN **compute** is O(n²·d), which #183's
design pre-registered as negligible below ~1500 nodes. Nothing here re-implements top-k.

### Correction: the `status` label is already accurate

The briefing for this proposal held that `status`'s `"{total} concept-to-concept edge(s)"` line has
no endpoint-type filter. **Refuted.** `graph/summary.py:39-43` (`_is_concept_edge`) requires BOTH
endpoints to sit under `CLASSIFIABLE_LINK_DIRS`, and `sources/` is the one registry `link_dir`
excluded from that tuple. `status` (`cli/main.py:7205-7211`) consumes exactly that filtered count.

This actually *explains* the issue's "74 applied, only 25 concept-to-concept" evidence instead of
being a bug: `resolution/edge_typing.py:122-144` (`_candidate_edges`) has **no** endpoint-type
filter and fed all 74 to the LLM, while `status` reported the 25 that survive `_is_concept_edge`.
Two different numbers from two correctly-behaving functions over the same rows. **Decision: the
`status` label is out of scope** — no text change is warranted, and after slice 1 the two counts
converge by construction.

## What is actually wrong

| # | Defect | Evidence |
|---|---|---|
| 1 | `Source` documents both propose and receive candidate edges | `graph/sqlite_graph.py:404` calls `candidates.pairs(sorted(node_ids))` over the full `okf._iter_docs` node set with no type filter |
| 2 | There is no ceiling on candidates generated per run | Nothing between `pairs()` and `_INSERT_EDGE_SQL` bounds the count; at 300 objects `TOP_K=5` still nominates ~1500 candidates → ~1500 sequential LLM calls |

Defect 1 alone shrinks the reported failure (~74 → ~25, and the triple-reported MCP date
contradiction collapses to one). Defect 2 is what makes the system *stable* rather than merely
smaller: linear growth is still unbounded growth.

## Scope

### In scope

1. **Exclude `Source` documents from the seeding node set.** Filter in `_populate_graph_tables`
   before `candidates.pairs(...)`, using the `metadatas` list the same walk already collects
   (`sqlite_graph.py:324,347`). Direct precedent: `resolution/candidates.py:98` (`_iter_eligible`)
   already does `okf_type == "Source"` exclusion for entity resolution. This keeps
   `graph/proximity.py` type-agnostic, preserving its "MUST take no workspace-specific
   configuration and MUST be pure" contract (`specs/candidate-edge-seeding/spec.md` R1) — the
   module only ever sees ids and embeddings through the narrow `NeighborQuery` Protocol, and its
   `_FakeNeighborQuery` test isolation never sees frontmatter. **Adopted as recommended.**
2. **A hard per-run cap on candidate edges.** Deterministic ranking + truncation:
   - **Ranking key**: `ProximityPair.distance` ascending (closest first). This is the "future
     ranking" `proximity.py:99-101` explicitly reserves the field for — pass 3 does not read it today.
   - **Tie-break**: `(source_id, target_id)` lexicographic, matching `pairs()`'s existing
     `sorted(best)` determinism guarantee.
   - **Placement**: AFTER the Source filter and AFTER the dedup against passes 1 and 2. Precedent
     is explicit: `resolution/contradiction.py`'s post-review HIGH correction moved deprecation
     filtering *before* the `_MAX_PAIRS` slice because filtering after a cap lets discarded rows
     consume cap slots and starve eligible ones. The same trap applies here, so the dedup set must
     retain `distance` rather than collapse to bare `(source, target)` tuples.
   - **Default**: `50`, as a private `Final` module constant. Derivation: it is comfortably above
     the post-filter ~25 of the reported bundle (so today's demo bundle is unaffected and no fixture
     churn is introduced), it bounds the worst case to ~50 typing + ~50 contradiction calls ≈ 5-8
     minutes at 3-5s/call versus the observed 17m19s, and it sits below
     `contradiction._MAX_PAIRS = 200` so seeding bounds what is *generated* while that cap remains
     the downstream backstop on what is *executed*. Design confirms the number.
   - **Truncation is never silent.** `contradiction.py:71-78` is the house precedent: it returns
     `(verdicts, total_pair_count)` and the CLI reports "N of M pairs shown (cap reached)".
     Recommended mechanism here: emit a note through `_populate_graph_tables`'s existing `skipped`
     return channel, which is already the "what I did not include" surface. Design settles the
     exact wording and which verb renders it.
3. **Spec deltas** on `graph-projection` (see Capabilities).
4. **RED-first tests** per strict TDD, extending `tests/unit/graph/test_sqlite_graph.py`'s existing
   `_StubCandidateSource` pattern.

### Out of scope (non-goals)

- **Changing `TOP_K` or its union-of-k semantics.** Mutual-k was explicitly rejected. Recalibrating
  `TOP_K`/`CANDIDATE_SIMILARITY_THRESHOLD` requires a fixture anchor per `proximity.py`'s own
  calibration note; it is not this change.
- **The `curate` call budget** ([#382](https://github.com/jasonssdev/openkos/issues/382), P2). That
  bounds work *executed* per run; this bounds work *generated*. Complementary, separate.
- **The `status` edge-count label** — already correct (see above).
- **`state/reindex.py`**: Source docs keep their embeddings. They are used by dense retrieval
  (`retrieval-fusion`); excluding them from the seeding node set is sufficient and cheaper than
  gating `reindex`.
- **`extraction/`**, any persisted-schema change, any new runtime dependency, any CLI flag.

## Capabilities

### New capabilities

- None.

### Modified capabilities

- `graph-projection`: "Third Pass — Embedding-Proximity Candidate Edges" gains (a) a
  Source-exclusion requirement on the node set handed to `pairs()`, and (b) a bounded-output
  requirement — deterministic distance ranking, post-filter/post-dedup truncation at a fixed
  ceiling, and mandatory reporting when truncation occurs.
- `candidate-edge-seeding`: **unchanged**. Its purity contract is the reason both requirements live
  in `graph-projection` rather than in `graph/proximity.py`.

## Configurability

No CLI flag. `TOP_K`, `CANDIDATE_SIMILARITY_THRESHOLD` and `_MAX_PAIRS` are all private `Final`
module constants under a deliberate no-configuration contract, and no CLI-flag precedent exists in
this area. The public surface that genuinely changes is **output text only**: `curate`'s Structure
gate line ("N untyped edge(s) → N LLM call(s)", N shrinks), `suggest-relations`/`contradictions`
candidate counts, `status`'s number (not its label), plus the new truncation notice. No argument,
flag, or persisted-schema change.

## Delivery: chained PRs

| Slice | Content | Estimate |
|---|---|---|
| 1 | Source exclusion in `_populate_graph_tables` + tests + `graph-projection` delta | ~150-180 lines |
| 2 | Ranking + cap + truncation reporting + tests + `graph-projection` delta | ~200-280 lines |

Slice 1 first: it is the smaller, mechanical fix, it alone resolves the reported symptom, and it
shrinks the input slice 2's cap operates on. PR#2 targets PR#1's branch (Feature Branch Chain).

- `Decision needed before apply: No`
- `Chained PRs recommended: Yes`
- `400-line budget risk: Low` per slice (~430-460 total, under the 800-line session budget)

## Affected areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/graph/sqlite_graph.py:325-347, 386-411` | Modified | Source filter on the node set; ranking + cap + truncation note in pass 3 |
| `src/openkos/graph/proximity.py` | Unchanged | Purity contract preserved; `distance` already exposed |
| `src/openkos/graph/summary.py`, `cli/main.py:7205-7211` | Unchanged | Already filters endpoints correctly |
| `src/openkos/resolution/edge_typing.py`, `contradiction.py` | Unchanged | They treat a proximity row like any untyped link; no Source-touching row will exist to filter |
| `openspec/specs/graph-projection/spec.md` | Modified (delta) | Both new requirements |
| `tests/unit/graph/test_sqlite_graph.py` | Modified | `_StubCandidateSource` extended with a Source fixture and an over-cap fixture |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| The cap silently hides genuinely useful candidates | Med | Truncation is reported, never silent (`contradiction.py` precedent); dropped pairs are the *farthest*, i.e. weakest, by construction; they reappear on the next run once closer pairs are typed and deduped out |
| Capping before dedup starves eligible pairs | Med | Explicitly ruled out: cap runs after the Source filter and after both dedup passes, per `contradiction.py`'s post-review HIGH correction |
| `50` is the wrong default | Med | Chosen to be a no-op on today's bundle, so it can be lowered later on evidence without a behavioral regression to unwind; design confirms |
| Source exclusion removes a Concept↔Source relation a user wanted | Low | Provenance links are already produced as typed `derived_from` rows by pass 1 and are unaffected; proximity was nominating *untyped* Source pairs no verb was designed to consume |
| Non-determinism creeps in via the ranking | Low | Distance + `(source_id, target_id)` tie-break; existing determinism assertions in `tests/unit/graph/test_proximity.py` and pass-3 tests must pass unedited |

## Rollback plan

Both slices are one-file reverts in `graph/sqlite_graph.py`. Candidate edges are
projection-ephemeral — recomputed on every `build_graph()`, never written to `relations:`
frontmatter — so reverting restores the previous edge set on the next build with no migration, no
frontmatter cleanup, and no stale `graph.db` state outliving the code. `git revert` per PR suffices.

## Dependencies

- No new runtime dependencies.
- Shipped: `concept-edge-seeding` (#183), `graph-projection-reuse` (#195/#196).
- Unblocks [#379](https://github.com/jasonssdev/openkos/issues/379)'s criterion 3;
  [#377](https://github.com/jasonssdev/openkos/issues/377) (multi-object extraction, now shipped)
  makes this more urgent, since more objects per source means more nodes feeding seeding.

## Success criteria

- [ ] Zero candidate edges touch a `sources/` (`type: Source`) document; the ids passed to
      `.pairs()` contain no Source id.
- [ ] The reported bundle's candidate count drops from ~74 to ~25, and the MCP date contradiction
      is reported once rather than three times.
- [ ] No run produces more than the cap in candidate edges, at any bundle size.
- [ ] When the cap truncates, the user is told; when it does not, no notice appears.
- [ ] Candidate output is byte-identical across two builds of the same bundle.
- [ ] `status`'s count and `suggest-relations`'s candidate count agree for bundles whose only
      untyped rows come from pass 3.
- [ ] Existing pass-1/pass-2 tests and `test_proximity.py` pass **unedited**.
- [ ] Quality gate green: `uv run pytest --cov` (branch coverage ≥ 90), ruff check + format, mypy strict.

## Proposal question round

Execution mode is `auto`; the maintainer already fixed the scope, so no interactive round was run.
Assumptions open to correction:

1. **The cap is the load-bearing half.** The Source filter alone leaves growth linear-but-unbounded;
   only an absolute ceiling makes `curate` cost predictable at any bundle size.
2. **`50` is a deliberate no-op today.** It is set above current volume so shipping it changes no
   observed output, and can be tightened later on measurement rather than guess.
3. **Dropped candidates are not lost work.** The farthest pairs are the weakest, and they resurface
   on later runs as closer pairs get typed and deduped out of the candidate set.
4. **The truncation notice rides the existing `skipped` channel** rather than a new return type.
   If design finds that channel semantically wrong (it currently means "doc I could not read"), the
   fallback is widening pass 3's return, which is a larger but still local change.
5. **`status`'s label is left alone** because it is already correct. If the maintainer still wants
   the wording revisited, that is a separate, purely cosmetic change.
