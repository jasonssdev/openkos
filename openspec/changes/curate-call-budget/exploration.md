# Exploration: curate-call-budget

**Issue**: #382 — `curate` has no call budget or batching; it can leave the
machine unusable.
**Status**: complete.
**Phase**: `sdd-explore`. No production code was written; no behavior changed.
**Artifact store**: hybrid — mirrored to Engram topic `sdd/curate-call-budget/explore`.

## Why this change is being made now

This is not polish. It is the last hard blocker to *executing* the validation
gate in **#379 (P0)**, the gate before opening MVP 3. Criterion 3 of #379 reads:

> **Bounded cost:** `curate` completes without exhausting machine memory, and
> the number of LLM calls grows sub-quadratically as the corpus doubles.

The sub-quadratic clause is an explicit acceptance criterion, so the exploration
had to answer not only "where does a `--limit` flag go" but "what makes the
15-source corpus run completable and its cost measurable".

## Current state

`curate` (`src/openkos/cli/curate.py`) runs five stages through `_STAGES`
(`curate.py:813`): Preconditions, Identity, Structure, Metadata, Contradictions.
Each LLM-costing stage prints a pinned cost line and gates on `--auto` / TTY
confirmation (`curate.py:188-232`).

| Stage | Calls | Cap today | Growth vs. corpus |
|---|---|---|---|
| Identity | 1 per `CandidateGroup` from `find_candidates` (`curate.py:274-282`) | **none** | **quadratic** |
| Structure | 1 per candidate edge (`curate.py:433-438`) | `_MAX_CANDIDATE_EDGES = 50` (`graph/sqlite_graph.py:241`) | bounded |
| Metadata | 1 per distinct concept type (`curate.py:583-614`) | bounded by vocabulary size | ~O(1) |
| Contradictions | 1 per typed-edge candidate pair (`curate.py:743-753`) | `_MAX_PAIRS = 200` (`resolution/contradiction.py:71`) | bounded |

## Principal finding — #382 is partially superseded, not closed

`resolution/candidates.py::find_candidates` runs an uncapped pairwise
`near_match_score` over `combinations(keyed, 2)` per type partition. Its own
docstring names the cost:

> WHAT THIS SAVES: the pairwise LOW pass. `find_candidates` runs
> `near_match_score` over `combinations(keyed, 2)` for every type — an
> O(n^2) cost in concepts-per-type

(`candidates.py:307-309`). The module even cites this issue by number:

> Every group costs one adjudication call (#382), so double-reporting one pair
> would buy nothing and charge twice.

(`candidates.py:260-262`).

An exhaustive grep for `_MAX` / `limit` across `resolution/candidates.py` and
`resolution/adjudication.py` returns **zero hits**. The Identity stage is the
one quadratic path, and it is the one stage no prior fix touched.

### What #378 already solved

Commits `1efe008`, `d5847f6`, `da10d54` (bounded edge seeding) capped **only**
the Structure stage: `_MAX_CANDIDATE_EDGES`, a `CandidateReport`, and
never-silent truncation notices. #382 was filed before that landed, so part of
its premise is stale — but three of the four LLM-costing stages, **including the
quadratic one**, remain uncapped. #382 is partially open, not superseded.

### What #404 contributes, and what it does not

`_MAX_OBJECTS_PER_SOURCE = 6` (`extraction/concept.py:369`) is structurally a
different thing: it bounds the objects returned by one already-made call — one
call per source regardless of corpus size — rather than bounding a call count.
Its `produced` / `retained` / discarded-titles reporting shape is reusable here;
its mechanism is not.

### The house pattern a new cap should mirror

`_MAX_CANDIDATE_EDGES` and `_MAX_PAIRS` share one idiom: a module-level
`Final[int]`, applied to a **ranked pre-cap set before any LLM call**, reported
through `produced > retained` and rendered as "N of M shown (cap reached)" —
never silent. A budget for Identity should extend this idiom rather than invent
a second one.

## Configuration and observability surface

- No config surface exists for a curate budget. `config.Config` carries
  `chat_timeout` (`config.py:516`, made configurable in `541ffa5`) and no cap
  knobs.
- No `--limit` flag exists on `curate` (`cli/main.py:10006-10025`).
- **Latency is not observable anywhere.** An exhaustive grep for timing
  instrumentation across `src/` returns zero hits. The issue's "74 calls means
  roughly four minutes" ask therefore requires **new instrumentation**, not a
  read of an existing signal. A hardcoded constant would be a fabricated number.

## Resumption semantics

- Identity, Structure, and Metadata write incrementally (per-item commit).
- Structure issues all its LLM calls in **one synchronous batch before any
  accept or write** (`graph/edge_typing.py:429-453`). A budget must therefore
  truncate the *input queue*; it cannot interrupt an in-flight batch. Granularity
  is stage-level, not item-level.
- **Accepted** items shrink the pool naturally on the next run: merged concepts
  vanish, typed edges are excluded. **Declined or skipped** items are never
  durable — they resurface and are charged again next run.
- Contradictions is report-only with zero persisted state (`curate.py:860`), so
  no resumption model applies to it beyond its fixed 200-pair cap.

Consequence: ranked truncation already yields free incremental resumption for
three of the four stages. An explicit persisted checkpoint would guarantee
forward progress but would introduce an idiom with no precedent in this codebase.

## Affected areas

- `src/openkos/cli/curate.py` — sequencer and stage probes
- `src/openkos/resolution/candidates.py` — the uncapped quadratic source
- `src/openkos/resolution/adjudication.py` — consumes candidates 1:1 into calls
- `src/openkos/resolution/contradiction.py`, `src/openkos/graph/sqlite_graph.py` — reference cap patterns
- `src/openkos/extraction/concept.py` — reference reporting shape only
- `src/openkos/cli/observability.py` — would host any new timing hook
- `src/openkos/config.py`, `src/openkos/cli/main.py:10006-10124` — new budget knob and flag
- `tests/unit/cli/test_curate.py` (1892 lines, pinned cost-line literals),
  `tests/unit/resolution/test_candidates.py`

## Forks the proposal must resolve

1. **Budget unit.** Call-count `--limit` (matches the house pattern, needs no
   new instrumentation) vs. wall-clock budget (answers the issue's "how long"
   ask, but needs latency tracking that does not exist and sits awkwardly against
   Structure's batch-call shape). Middle ground: an enforced per-stage call cap
   plus an informational mean-latency estimate.
2. **Truncate vs. resume.** Ranked truncation (matches the existing idiom
   exactly, and already gives free incremental resumption for three of four
   stages) vs. an explicit persisted checkpoint (guarantees progress, invents a
   new idiom).

## Recommendation carried into the proposal

Extend the existing ranked-cap idiom to Identity's `find_candidates` first: it is
the smallest, most house-consistent fix, and the only one that actually satisfies
#379's sub-quadratic criterion. Treat the wall-clock time estimate as a separable
slice, because it requires instrumentation that does not exist yet.

## Risks

- #382 must not be marked already-fixed on the strength of #378; the quadratic
  stage is untouched.
- A call-count cap alone does not satisfy the issue's time-estimate ask.
- Structure's call-then-write batch limits interruption granularity to
  stage-level.
- Contradictions has no persisted state, so no resumption model applies to it.
- `tests/unit/cli/test_curate.py` pins cost-line output literals; any change to
  those lines is a test-visible contract change.
