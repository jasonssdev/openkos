# Proposal: Expose Unmerge Through `openkos.api` And The Local API

Tracking issue: #1432 (found while planning #1430). Governing decisions:
ADR-0053 (the two-phase decision surface), ADR-0060 (the versioned Python API),
ADR-0061 (the local API is a FastAPI adapter over the Python API), ADR-0063 (an
identity group is merged pairwise, each step reversible with `unmerge`). New
decision record: ADR-0064 (Proposed, written with this plan). Paths are under
`src/openkos` unless they start with `tests/`, `docs/` or `openspec/`.

## Intent

`docs/decision-wording.md` promises the app user a **Split again** undo for every
combine: "Combined from '{other title}' on {date}" lines on the combined page,
newest first, each with **Split again**, and, when later combines sit on top,
"To split this, the later combines on this page are split first: {list}.
Continue?". The engine does this through `openkos unmerge` (one step) and
`openkos unmerge --to` (a chain), but `openkos.api` and the local HTTP API expose
no unmerge operation and no way to read what was combined into a page. A person
who combined two pages, or a group (#1430), in the app cannot undo it there.
MVP 6's review screen needs the undo before it ships.

## Scope

### In scope

- **A merge-history read,** `merge_history(workspace, concept_id)`: what was
  combined into a concept, newest first, as `MergeRecord`s (survivor, absorbed
  Concept ID, the absorbed document's title as it was recorded, and when). It
  reads the concept's merge ledger only: no lock, no model, no write. It is what
  the "Combined from ... on ..." lines are built from.
- **A two-phase unmerge,** in the decision surface's shape:
  `preview_unmerge(workspace, survivor_id, absorbed_id)` returns an
  `UnmergePreview` whose `steps` list every single-step unmerge in execution
  order (the survivor's newer merges first, down to and including the one that
  absorbed `absorbed_id`) and an `UnmergeVersion`;
  `apply_unmerge(workspace, version)` performs exactly that plan, or returns why
  it cannot. One operation covers both undoing a single step (the named merge is
  the newest) and undoing a whole group or chain (the named merge is buried,
  `unmerge --to`), because the preview lists every step the person consents to.
- **The version binds the plan.** Its content digest covers the ledger entries
  being reversed and the current bytes of every file the steps restore, rewrite
  or recreate; any change between preview and apply is
  `UnmergeStale("content_changed")` and nothing is written.
- **Typed refusals** a client can word: `NotUnmergeable` with reason
  `survivor_missing` (the page was itself combined into another; carries the
  absorber), `not_merged` (nothing of that id is recorded on that page), or
  `survivor_edited` (the page was edited since the combine, so restoring it would
  discard the edit).
- **`discard_survivor_edits`,** an opt-in keyword on the preview, default off,
  carried in the version: the API equivalent of `unmerge --discard-survivor-edits`.
- **Partial failure** of a chain is a raised `UnmergeInterrupted` (a
  `WriteFailed`) carrying the completed steps and their commits, as
  `MergeGroupInterrupted` does for a group.
- **Local HTTP API:** `GET /v1/merges`, `POST /v1/unmerge/preview`,
  `POST /v1/unmerge/apply`, their error rows and the regenerated OpenAPI snapshot.
- **Docs:** `docs/decision-wording.md` drops the "(see Open questions)" gap and
  its open-question line for #1432; the CHANGELOG gains its entry.
- **ADR-0064,** Proposed, flipped to Accepted at archive.

### Out of scope

- **The CLI.** `openkos unmerge` is unchanged byte for byte; no verb changes, so
  `docs/cli.md` does not change.
- **Recording keep-distinct after a split** (`unmerge --keep-distinct`). Whether
  a split from the app also means "these are different" is not in the wording
  brief; the parameter can be added later without breaking v1.
- **Refreshing derived indexes.** No API write refreshes them (stable-python-api
  v1 limitation); `status` reports stale indexes, as after any apply.
- **The queue.** A merged decision's row stays resolved; an unmerge proposes
  nothing and resolves nothing (`queue_resolution` already lists `unmerge_core`
  as a non-resolving writer).

## Approach

`application/unmerge_service.py` gains a pure history read, a pure chain preview
with its content digest, and an apply that re-previews under the workspace lock,
compares the digest, and runs the existing `unwind_merges` loop with its
confirmation satisfied by the version. `openkos.api` maps those to public result
types in a new `_unmerge.py`; `openkos.localapi` adds three routes. Design records
the decisions.

## Affected specs

| Spec | Change |
|---|---|
| `stable-python-api` | ADDED: Merge History Is Readable; Unmerge Is Previewed, Then Applied Against Its Version; Unmerge Extends v1 Additively. |
| `local-http-api` | MODIFIED: The Operations Are Served Under `/v1`, One Per Python API Operation; Outcomes And Errors Map To Status Codes With A Uniform Body. ADDED: The Unmerge Version Travels Unchanged From Preview To Apply. |

## Principles check

- **Human curates, engine maintains.** Nothing is undone that the person did not
  preview: every step of a chain is listed before apply, the version binds that
  exact list, and the API never chooses a target (both ids are explicit, as the
  CLI requires). Discarding a survivor edit is an explicit opt-in, never implied.
- **Reversible and reconstructible.** Each step is the CLI's own single-step
  unmerge, restoring byte parity and committing; the ledger stays the source.
- **Sensitivity.** Owner-local, as every API operation (ADR-0058, ADR-0060):
  `merge_history` returns Concept IDs, a title and a timestamp, never a body.
  `committed_confidential` reports a confidential concept in a commit.
- **Local-first, OKF.** No new dependency, format or file kind.

## Risks

| Risk | Mitigation |
|---|---|
| A chain stops part way. | Completed steps are committed and reported in `UnmergeInterrupted`; the bundle is consistent after every step (the CLI's invariant). Steps whose survivor-edit check is decidable from the ledger are refused at preview instead. |
| The lock is held for the whole chain. | No model call runs in it; each step is the CLI step's reads and writes. The wait to acquire stays bounded (5 s on HTTP). |
| A widened public union breaks v1. | No existing union, alias or signature changes; everything is a new name, a new keyword-free function or a new `Literal` value of an input parameter (`WriteFailed.operation`). The surface classifier proves it. |
| The client flips `discard_survivor_edits` in the echoed version. | The flag is a line of the content digest: a flipped flag is `UnmergeStale`. |

## Rollback plan

Every slice is one squash-merged PR and reverts on its own, newest first. S2 and
S3 add public names; reverting one after merge removes them, which is a breaking
surface change, so the preferred rollback is to stop the app from calling the
operations. Unmerges already made are ordinary unmerges in git history.
