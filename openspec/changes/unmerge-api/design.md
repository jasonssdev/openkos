# Design: Expose Unmerge Through `openkos.api` And The Local API

Inputs: `proposal.md`, issue #1432, ADR-0053, ADR-0060, ADR-0061, ADR-0063.
Decision record: ADR-0064.

## Current state

- `application/unmerge_service.py` has two entry points, `unmerge_concept` (one
  step, LIFO tail only) and `unwind_merges` (`--to`: tail-first down to and
  including a named entry). Both gate the workspace, canonicalise the ids, refuse
  a torn ledger, then run `_unmerge_step` per entry: Phase A
  (`lifecycle.prepare_unmerge`, pure), the confirmation, the drift guard and
  Phase B inside `ports.commit_section`, then the auto-commit. A failing step of
  a chain raises `UnwindStopped` with the step's own refusal as `cause`.
- `prepare_unmerge` raises `ValueError` when the survivor's bytes differ from the
  tail entry's `survivor_after_sha256` unless `discard_survivor_edits`, and then
  reports `survivor_edits_discarded`.
- `openkos.api` may import only the standard library, `openkos.api.*`,
  `openkos.errors`, `openkos.config`, `openkos.lock`, `openkos.application.*` and
  `openkos.llm.base` (plus the wiring module's extras). Ledger and OKF reads must
  therefore live in `application/`.

## Decisions

### D1. One operation for a step and a chain

`preview_unmerge(survivor_id, absorbed_id)` always plans the unwind sequence down
to and including `absorbed_id`. When it is the newest merge the sequence has one
step, which is exactly the classic two-argument unmerge (the CLI already
documents `--to <tail>` as identical). A group undo names the group's first
absorbed member (the group is merged in ascending order, so that member is the
deepest of the group's entries). The wording brief asks for this shape: "the later
combines on this page are split first: {list}. Continue?". A separate
single-step function would only add a refusal the preview already prevents.

Rejected: a `to` flag mirroring the CLI. The CLI needs two forms because it has
one prompt; the API has a preview, which lists every step before consent.

### D2. The version is a digest over what the plan reads

`UnmergeVersion(survivor_id, absorbed_id, content_digest, discard_survivor_edits)`.
The digest is sha-256 over sorted lines:

- `target\t<survivor>\t<absorbed>` and `discard\t<true|false>`;
- `ledger\t<sha256 of the survivor's committed ledger sidecar>` (it holds every
  entry being reversed);
- per step: `step\t<n>\t<absorbed>\t<merged_at>`, `absent\t<absorbed>.md\t<-|sha>`
  (the recreated file must be free), and `file\t<rel>\t<sha|->` for every file the
  entry rewrites (links, relations, provenance);
- for the first step, the drift baselines of Phase A: the survivor's bytes,
  `index.md`/`log.md` when the entry restores snapshots (a delta entry is
  re-composed, as merge's catalog is, so it is not guarded), and the read
  dependencies.

The time the preview ran is not part of it, so the same bundle always yields the
same digest. A flipped `discard_survivor_edits` changes a line, so an echoed
version with the flag altered is stale.

### D3. Apply checks under one lock, then runs the CLI's loop

`apply_unmerge` validates `wait_seconds`, opens one locked commit section, and
inside it: re-previews from current bytes (a refusal is returned as is), compares
the digest (`UnmergeStale("content_changed")` on a mismatch), then calls
`unwind_merges` with `UnmergePolicy(auto=True, discard_survivor_edits=...)` and
an unlocked inner section. `auto=True` is correct here because the version is the
consent: the person previewed this exact plan. The check and every write share
the lock, as a group decision does (ADR-0063), so no other writer interleaves.
Each step keeps its own auto-commit, so each split is a commit of its own.

### D4. Typed refusals where the cause is decidable, `Refused` otherwise

Before planning, the preview checks, in order: the survivor's file exists
(`survivor_missing`, with `find_absorber`'s answer as `absorbed_into`), the
ledger records `absorbed_id` (`not_merged`). It then prepares the first step with
`discard_survivor_edits=True` and reads `survivor_edits_discarded`: when set and
the caller did not opt in, the result is `survivor_edited`. No error text is
parsed. Only the first step is decided at preview: the survivor a later step
sees is what the earlier step restores, including any deprecated-status export
folded into it over the post-unmerge state, so predicting it from the ledger alone
could refuse a chain the run would complete. A later step's survivor check runs in
that step's own Phase A at apply, as on the CLI, and a failure there is
`UnmergeInterrupted` (D5). Everything else Phase A refuses (an invalid id, a torn ledger, a file already at
the absorbed path, a drifted rewrite file, a redacted entry) stays the service's
`Refused`, whose message the local API passes through as `write_refused`.

### D5. A chain that stops part way

`UnwindStopped` at step 1 means nothing was written: its `cause` (`Refused` or
`DriftDetected`) is re-raised. At step N > 1 the API raises
`UnmergeInterrupted(WriteFailed)` with `restored_ids`, `commit_shas` and
`failed_absorbed_id`; `WriteFailed.operation` gains `"unmerge"`. The local API
maps it to 500 `unmerge_interrupted`, not retryable, before `write_failed`.

### D6. History is a ledger read in the application layer

`unmerge_service.merge_history(root, concept_id)` reads the committed sidecar
(`bundle_ledger.read_entries`) and returns, newest first, the absorbed id, the
`merged_at` stamp and the title from the recorded absorbed snapshot's
frontmatter (`None` when it cannot be read, including a redacted snapshot). An id
that is not a valid Concept ID, or names no ledger, lists nothing: a read never
refuses. The title is stored content, which the owner-local surface may return
(ADR-0060 statement 2).

### D7. Public types live in `api/_unmerge.py`

`MergeRecord`, `UnmergeStep`, `UnmergeVersion`, `UnmergePreview`,
`NotUnmergeable`, `UnmergeStale`, `Unmerged` are frozen dataclasses defined in the
facade (as `MergePlan` is), built from the service's internal results.
`UnmergeInterrupted` lives in `application/write_gate.py` beside
`MergeGroupInterrupted` and is re-exported. Commit wiring reuses
`_wiring.CommitNotes` and `skipped_warning`, so `committed_confidential` and the
"not committed" warning behave as for a decision.

### D8. HTTP shape

- `GET /v1/merges?concept_id=<id>` returns `{"merges": [MergeRecord...]}`.
- `POST /v1/unmerge/preview` with `{survivor_id, absorbed_id,
  discard_survivor_edits?}` returns the `UnmergePreview`.
- `POST /v1/unmerge/apply` with `{version, wait_seconds?}` returns
  `{"unmerged": Unmerged, "warnings": [...]}`.

Concept IDs contain `/`, so they travel in a query string or a body, never a path
segment. Rows: `survivor_missing`, `not_merged`, `survivor_edited` are 409 and not
retryable; `UnmergeStale` is 409 `stale`; `UnmergeInterrupted` is 500
`unmerge_interrupted`. The apply body echoes the version with the same
`_EchoRequest` pattern, so `UnmergeVersion` stays one OpenAPI component.

## Testing

Strict TDD per slice. The API tests build real merges through the decision
surface (a pairwise `merge` and a `merge_group`) in a git-backed workspace and
undo them through the public facade only, asserting byte parity of the bundle and
one commit per step. Mutation checks target each new guard line (the digest
comparison, the survivor-edit decision, the interrupted mapping, each error row).
