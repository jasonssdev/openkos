# Design: settle-other-kinds

## Context

`curate_decisions.list_open_decisions` lists six kinds; three carry actions.
`contradiction` and `revision` rows resolve in the CLI through
`contradictions --decline` (`contradictions_service.apply_contradiction_decision`)
and `reconcile` (`reconcile_service.reconcile_pair`), neither of which calls a
model. `watch_refusal` resolves through `ingest`, which does. The public surface
is classified: a widened non-`Literal` union is breaking, so `Decision.detail` and
`DecisionPreview.plan` cannot take new members within v1 (the group-decision
change hit the same wall and reused `MergePlan`).

## Decisions

### D1. Findings preview through `preview_finding`; apply is shared

A reconcile or a decline is not a merge, a relation, a tier or a keep-distinct,
so no existing plan type can carry it honestly. `preview_finding` returns
`FindingPreview` with its own union (`ReconcilePlan | DeclinePlan`) and the same
`DecisionVersion`, which `apply_decision` already takes. Each preview refuses
the other's actions with `NotApplied("wrong_preview")`, so a client that calls
the wrong one learns which to call instead of a misleading `not_offered`. Over
HTTP the route is `POST /v1/decisions/{key}/preview-finding`, a sibling of the
decision preview, so the generated client's `DecisionPreview` union, which the
review screen switches over exhaustively, does not change under it. Recorded in
ADR-0072.

### D2. Directions are relative to `targets`, as `relate_reversed` is

`supersede` makes `targets[0]` the holder; `supersede_reversed` makes
`targets[1]` the holder; `revise` likewise. The plan names `holder_id` and
`target_id` explicitly, so a client words the preview from the plan, not from the
action name. Contradiction targets are the verdict's pair (sidecar owner first),
revision targets the sorted pair.

### D3. The action set follows the CLI's walks

- A typed-edge contradiction gets the `--from-findings` symmetric reconcile, the
  two-id `--winner` in both directions, and `--decline`. `--revision` is left out:
  a refinement does not settle an incompatibility, and the brief would have to
  explain a fourth option for no case the walk offers.
- A merged-body contradiction names a page that was absorbed: `reconcile`
  resolves only the typed-edge row, and the walk excludes merged-content
  findings, so only decline is offered, with reconcile withheld
  (`merged_content`). Splitting the page again is already in the API.
- A revision gets exactly what the walk offers: with a date order, the one write
  `relation_for` names, held by the later Decision; without, the four
  choices of the combined prompt. Direction comes from the dates
  (`pair_direction`), never from the model (ADR-0025). `reconcile` is added as
  the one "no" a revision has: it keeps both current and removes the pair from
  later revision passes (`RESOLUTION_RELATION_TYPES`). There is no revision
  decline in the CLI, so none is invented.
- A watch refusal gets no action (#1496).

### D4. Freshness uses each finding's own reader rule

Contradiction rows use the lenient rule of `findings._is_stale` (a ref that is
not a readable concept counts as unchanged), which is what
`reconcile --from-findings` filters by, plus the pair's presence. Revision rows
use the strict rule of `revisions.is_fresh` (recomputed digests equal the
recorded ones exactly), over the row's recorded inputs. A stale finding is listed
`fresh=False` and its preview is `Stale("row_inputs_changed")`, as for every kind.

### D5. The reconcile transaction is split, not copied

`reconcile_service` gains `prepare_pair` (Phase A, public, returns the
`PreparedPair`) and `commit_pair(root, prepared, *, ports, guard=None)` (the
section: an optional guard, the drift check, the writes, the queue resolution,
the commit). `reconcile_pair` becomes prepare, confirm, commit, byte-for-byte the
same writes and the same refusals. The conflict gate raises `ReconcileConflict`, a
`Refused` subclass, so the CLI renders it as before and the decision surface maps
it to `NotApplied("already_reconciled")`. The guard is how the token path
re-checks the row inside the section, as every other `commit_*_decision` does.

### D6. The version binds the direction

The content digest covers the two pages' bytes and a `plan` line
(`relation_type`, holder, target). The action is not part of the token, so
without the plan line a supersede version would apply as `supersede_reversed`
over the same bytes. The planned output bytes are not hashed: they hold today's
date in the reconciliation note, and a preview before midnight would go stale
after it. A decline's digest covers the sorted pair, the merged absorbed id and
the pages' bytes.

### D7. The revision row resolves with the reconcile

`queue_resolution.resolve_reconciled` resolves the revision row over the pair too
(`as_proposed` when the edge and holder match the row's verdict and dates). Left
alone, an applied revision would stay listed until the next revisions pass, and
its preview would reconcile again as an idempotent no-op. The CLI's `reconcile`
gains the same resolution; the registry entry is unchanged.

## Sequence: apply a finding

```
client            api.apply_decision     curate_decisions        finding_decisions          reconcile_service
  | version,action  |                      |                         |                          |
  |---------------->| apply_decision ----->| row, refusal, finding? -|                          |
  |                 |                      |------ commit_reconcile ->| prepare_pair (no lock) -->|
  |                 |                      |                         | version == expected?      |
  |                 |                      |                         |  no -> Stale(content)     |
  |                 |                      |                         | commit_pair(guard) ------>| section: guard (row open,
  |                 |                      |                         |                          |  fresh), drift, write,
  |                 |                      |                         |                          |  resolve rows, commit
  |<---- Applied ---|<---------------------|<------------------------|<-------------------------|
```

## ADR gate

D1 decides an interface (a second preview function and route inside v1) and is
hard to reverse once a client ships against it: ADR-0072. D2-D7 follow existing
patterns and are recorded here.
