# Archive Report: bounded-edge-seeding

**Issue**: [#378](https://github.com/jasonssdev/openkos/issues/378) (P0) — closed.
**Archived**: 2026-08-05.
**Delivered as**: three pull requests, each squash-merged to `main` after CI.

| PR | Commit on `main` | What it delivered |
|---|---|---|
| [#393](https://github.com/jasonssdev/openkos/pull/393) | `1efe008` | `Source` documents excluded from candidate-edge seeding, on both ends |
| [#394](https://github.com/jasonssdev/openkos/pull/394) | `d5847f6` | Ranking, a per-run ceiling of 50, and `CandidateReport` |
| [#395](https://github.com/jasonssdev/openkos/pull/395) | `da10d54` | The truncation notice, with caller-scoped counts |

## What the issue asked for, and what was true

The issue proposed two corrections. Investigation found the first was already
shipped, and said so in the proposal rather than re-implementing it.

**Correction (a), "top-k per node instead of all pairs" — already implemented.**
`graph/proximity.py` defines `TOP_K = 5`, and `VectorProximitySource.pairs()`
issues one k-NN query per node. Candidate volume was already linear in the node
count, not O(n²); the 74 candidates the issue measured came from the top-k
union, not from an all-pairs walk. The issue's framing of "435 possible pairs →
74 candidates" was incorrect. Only the underlying k-NN compute is O(n²·d), which
#183's design had already registered as negligible below roughly 1500 nodes.

**Correction (b), "exclude `sources/` from seeding" — real, and shipped.**
`_populate_graph_tables` passed its full document walk to the candidate source
with no type filter, so a `Source` could both propose and receive a candidate
edge. It now builds a Source-free seed set and applies it in two places: the
anchor list AND both membership guards. Both are required — `pairs()` queries
the whole vector store and never filters its own hits against the ids it was
given, so narrowing only the anchor list would have left Sources reachable as
receiving endpoints.

**Beyond the issue: a hard per-run ceiling.** The maintainer chose to include
the issue's own deferred "completeness may be the enemy" idea, because the
Source filter alone does not make the system stable: at 300 objects, `TOP_K = 5`
still nominates roughly 1500 candidates and therefore roughly 1500 sequential
LLM calls. Candidates are now ranked by proximity distance and truncated to 50
per projection build.

## What review caught that implementation did not

**The truncation notice disclosed material the caller could not see.**
`resolution/edge_typing.candidate_edges` removes every edge with a blocked
endpoint from the list a command prints, but `CandidateReport`'s counts are
computed in the projection, where no sensitivity filter runs. The notice
therefore printed a pre-cap total that counted confidential-endpoint pairs while
the list below it excluded them.

This finding was initially and wrongly dismissed as a false positive, on the
reasoning that `_load_doc` degrades a confidential document to an empty body
while still counting it. That reasoning was inverted: `_load_doc` runs later, in
`suggest_edge_types`, only on edges that already survived `candidate_edges`'s
filter. It is defense in depth, not the filter. A targeted validator caught the
error, and it is recorded here because the failure mode generalizes — reading
the consumer of a list rather than its producer inverts conclusions about
filtering.

The fix: `CandidateReport` carries its ranked pre-cap `pairs`, and
`candidate_truncation_notice` re-derives both counts through the same
`sensitive_concept_ids` walk, filtering the retained prefix independently of the
dropped tail, because a blocked endpoint can sit inside either.

**The notice was lost on three `curate` paths.** It was echoed only inside
`gate()`, but the sequencer returns before the gate on an unavailable probe, an
empty queue, and a stage skipped because Ollama went down. A run whose
candidates were truncated but whose survivors were all filtered out lands on
exactly the empty-queue branch. The echo moved to `run_curate`, immediately
after the probe returns.

## Spec consolidation

The delta amended `graph-projection`. Consolidated into
`openspec/specs/graph-projection/spec.md`:

- **MODIFIED** `Third Pass — Embedding-Proximity Candidate Edges` — Source
  exclusion on both ends, with passes 1 and 2 and the Concept→Source
  `derived_from` provenance mirror explicitly unaffected.
- **ADDED** `Third Pass — Bounded Candidate Output Per Run` — ranking,
  the ceiling, dedup-before-truncation ordering, and mandatory reporting.
- **ADDED** `Truncation Reporting Is Caller-Scoped` — written during archive,
  not present in the original delta. It records the behaviour that emerged from
  review: the rendered counts are re-derived per caller, the retained prefix is
  filtered independently, and a truncation composed entirely of invisible pairs
  renders nothing.

`candidate-edge-seeding` was deliberately left untouched. Its purity contract —
no workspace-specific configuration — is the reason both requirements live in
`graph-projection` rather than in `graph/proximity.py`, which was never
modified.

## Verification at close

- `uv run pytest` → 3467 passed
- `uv run pytest --cov` → 97.24% against `fail_under = 90`; `sqlite_graph.py`
  and `edge_typing.py` both 100% line and branch
- `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy --strict src tests` → clean, repo-wide
- Review receipts: `review-5210636399abd7c4` (slice 1),
  `review-e9742ea9521fdb49` (slice 2a), `review-c9c57b2f5ff399bc` (slice 2b,
  four lenses, zero blockers)

## Follow-ups, accepted and not fixed

- `candidate_truncation_notice` runs a second sensitivity bundle walk before
  reaching the cheap guard that would have returned early.
- `StageProbe.notice`'s field docstring still attributes the echo to `gate()`.
- `docs/cli.md` and the changelog do not mention that the counts are
  caller-scoped.
- The embedding index carries no sensitivity or lifecycle filtering, so every
  consumer of the proximity source must filter downstream. This is the root
  reason the projection's counts are unfiltered, and it is broader than this
  change.
- The complementary `curate` call-budget issue remains open. This change bounds
  the work *generated*; that one bounds the work *executed* per run.
