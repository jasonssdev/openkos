# Design: undo-findings

Inputs: `proposal.md`, issue #1497, ADR-0067, ADR-0072. Decision record:
ADR-0074.

## Current state

- **A decline** (`contradictions --decline`, and `apply_decision` with
  `decline`) is one `DecisionRecord` with state `declined` in the decisions
  sidecar of the sorted pair's first id
  (`contradictions_service.apply_contradiction_decision`), keyed by
  `decision_key_for(pair, merged_absorbed_id)`. `contradictions --reopen`
  rewrites the same record with state `open`; `--declined` lists the declined
  records. A decline resolves the queue row; a reopen resolves nothing.
- **A reconcile** (`reconcile`, and `apply_decision` with `reconcile`,
  `supersede…` or `revise…`) is `reconcile_service.prepare_pair` /
  `commit_pair`. It writes, in one commit of both pages and `log.md`:
  - the resolution edge: `reconciled_with` on both pages, or one `supersedes`
    or `revises` edge on the holder;
  - a `## Reconciliation` note appended to each page that has none for its
    counterpart: heading, hidden anchor
    `<!-- okos:reconcile target=<counterpart> role=<role> -->`, one sentence;
  - for a `supersedes` edge it added, the counterpart's deprecated-status
    export (`status: deprecated`, `status_derived_from: supersedes`), unless a
    human-authored status blocks it;
  - a `**Reconcile**` line in `log.md`.
  A pair carries at most one resolution: a different request is refused
  (`ReconcileConflict`), and disagreeing edges, which only a hand edit makes,
  classify as `mixed`. Nothing reverses a reconcile; the refusal says "edit the
  concepts manually or revert with git".
- **`unrelate`** removes one `(source, type, target)` edge and, for
  `supersedes`, withdraws the target's export when no other page still
  supersedes it after the removal (`lifecycle.prepare_unrelate`, a walk over the
  bundle). It does not touch body notes. ADR-0067 exposes it as
  `preview_unrelate` / `apply_unrelate`.

## Decisions

### D1. Two more kinds in ADR-0067's shape, keyed by what the choice wrote

Each kind is a list, a preview that returns a version with a content digest,
and an apply that takes the workspace lock once, re-plans from current bytes,
returns `UndoStale` on a moved digest and otherwise writes and commits only its
own paths. No model, no derived-index refresh, no queue row resolved or
proposed (ADR-0067 D7).

- **Reopen a decline** is keyed by the sorted pair and `merged_absorbed_id`,
  the decline record's identity; the typed-edge and the merged-body finding over
  one pair are two different things to undo.
- **Undo a reconcile** is keyed by the sorted pair alone. The resolution is
  read from the pages, not named by the caller: a pair holds at most one, so the
  pair names it, and a caller cannot ask to undo a resolution the pages do not
  hold.

### D2. A reconcile's inverse removes what reconcile wrote (ADR-0074)

`reconcile` has no CLI inverse, so the undo is derived from its write:

1. **The edges.** Every edge of the pair's resolution type between the two
   pages is removed, through `lifecycle.prepare_unrelate` per edge, so the
   relations list is edited exactly as `unrelate` edits it (the key dropped when
   it empties). The two pages are resolved by `reconcile_service.resolve_pair`,
   as `reconcile` resolves them (two distinct existing files).
2. **The out-of-date mark.** For `supersedes`, `prepare_unrelate`'s own
   withdrawal decides it: withdrawn when nothing else supersedes the page after
   the removal and the projection allows it; a human-authored status stays.
3. **The notes.** On each page, the `## Reconciliation` block anchored on the
   counterpart is removed when it has the shape `reconcile` writes: the heading
   line, the anchor line, one sentence line, with the blank line before it. Any
   role is accepted, as the anchor gate in `reconcile` accepts any role. A note
   whose anchor is present in another shape (edited by hand) is left as it is,
   and that page changes only by its edge. The removal is
   `reconcile_service.remove_reconciliation_note`, the inverse beside the
   builder.
4. **The log.** One `**Unreconcile**` line, as `unrelate` adds its line.
5. **The commit.** One commit of the pages whose bytes change and `log.md`:
   `openkos: unreconcile <first> <-> <second>`.

`reconcile` writes a `relations` key on both pages, even on the page that gets
no edge, so an emptied list is dropped on a page the undo rewrites, as
`unrelate` drops it. The withdrawn mark is `unrelate`'s projection, which writes
`status: stable`, the value the engine's own pages carry. So for pages in the
engine's form (a reconcile, then its undo, nothing else in between) both pages
return to their bytes before the reconcile, and a persisted contradiction
verdict over them is fresh again and served without a model call on the next
check. A hand-written page comes back with the same frontmatter values and
body, re-serialised, and gains `status: stable` after a supersede.

Rejected: offering `unrelate` for a supersede. It leaves both notes, and a
symmetric reconcile has two edges, so the app would need two unrelates and
still leave "Reconciled with" notes behind. Rejected: storing what the
reconcile wrote and replaying it backwards. The pages already say it; a second
record is a second truth to keep in sync, and the CLI's reconciles have none.

### D3. Refusals

`NotUndoable` gains `not_declined` (no declined record for exactly this pair
and discriminator, an invalid id included) and
`not_reconciled` (the pair holds no reconcile resolution, holds disagreeing
ones, or a page no longer exists, or an id is invalid). Both are 409 over HTTP,
as `not_kept_distinct` and `not_related` are. A disagreeing (`mixed`) pair is
not listed and not undone: only a hand edit makes it, and the undo would have to
pick which resolution to remove.

### D4. What each version digests

- **Reopen a decline:** the sorted pair, the discriminator (`-` for none) and
  the bytes of the sidecar holding the record.
- **Undo a reconcile:** each page's Concept ID and bytes, and whether the
  out-of-date mark is withdrawn. The resolution is read from those bytes, so it
  needs no line of its own. The withdrawal line is needed because both pages
  are always digested, so a bystander page that starts or stops superseding the
  target would otherwise not move the digest; under the lock the re-plan sees it
  and the digests differ.

`log.md` is not digested, as for unrelate.

### D5. The lists

- `declined_contradictions` walks the decisions sidecars (the `--declined`
  walk, without its findings join, so the read opens no database) and returns
  `(first_id, second_id, merged_absorbed_id, decided_at)`, newest first, then
  by pair and discriminator.
- `reconciled_pairs` walks every concept page's `relations:` for the three
  resolution types, classifies each pair with the reconcile classifier, and
  returns `(first_id, second_id, relation_type, holder_id, target_id)` for every
  pair whose pages both exist and whose state is one resolution, ordered by
  pair. The pages carry no timestamp a list could order by; the note's date is
  prose.

Both take no lock, build no client and write nothing.

### D6. The local HTTP API

| Route | Operation | Body |
| --- | --- | --- |
| `GET /v1/declined-contradictions` | `declined_contradictions` | none, `{contradictions: [...]}` back |
| `POST /v1/contradictions/reopen/preview` | `preview_reopen_contradiction` | `{first_id, second_id, merged_absorbed_id}` |
| `POST /v1/contradictions/reopen/apply` | `apply_reopen_contradiction` | `{version, wait_seconds}` |
| `GET /v1/reconciled` | `reconciled_pairs` | none, `{pairs: [...]}` back |
| `POST /v1/unreconcile/preview` | `preview_unreconcile` | `{first_id, second_id}` |
| `POST /v1/unreconcile/apply` | `apply_unreconcile` | `{version, wait_seconds}` |

New rows: `not_declined` and `not_reconciled` (409). The apply bodies are
`_EchoRequest`s. The routes are added as a new requirement in the
`local-http-api` spec, not by modifying the operations table, so concurrent
changes to that table do not conflict at archive.

## Sequence: undo a reconcile

```
client -> POST /v1/unreconcile/preview {first, second}       (no lock, no write)
engine -> classify pair; plan edges (prepare_unrelate), mark, notes; digest
client -> POST /v1/unreconcile/apply {version}
engine -> lock; re-plan; digest equal? write pages + log; commit; unlock
          else UndoStale / NotUndoable, nothing written
```

## Testing

Strict TDD. Application tests over pages written by `reconcile_service` itself
(each mode, both directions, a note edited by hand, a bystander that still
supersedes, a human status, stale and refused paths, byte restoration), and over
records written by `apply_contradiction_decision`. Facade tests reverse choices
applied through `preview_finding` / `apply_decision` in a git-backed workspace:
one commit, the right paths, no lock for previews and lists, no model client.
Route, error-table and OpenAPI snapshot tests; the surface classifier; vitest for
the client methods and `ERROR_CODES`. Mutation checks on each guard line.

## ADR gate

D2 decides an interface and a write the CLI does not have (the inverse of
`reconcile`, including removing notes), and is hard to reverse once a client
ships against it: ADR-0074. D1 and D3-D6 follow ADR-0067.
