# Design: Undo Every Review Choice From The App, And List Every Combined Page

Inputs: `proposal.md`, issues #1466 and #1467, ADR-0053, ADR-0060, ADR-0061,
ADR-0064, ADR-0066. Decision record: ADR-0067.

## Current state

- **What each choice writes.**
  - *Combine*: the survivor's merge ledger sidecar,
    `bundle/.state/ledger/<id>.ledger.okf`, reversed by `unmerge`.
  - *Keep separate*: one `IdentityDecisionRecord` with `state: declined` over the
    sorted member set, in the decisions sidecar of the first member,
    `bundle/.state/decisions/<id>...` (`duplicates_service.apply_identity_decision`).
    `duplicates --reopen` rewrites the same record with `state: open`; `duplicates
    --kept-distinct` lists every declined record. A group decision records one
    declined set per unmarked member, plus one over all unmarked members
    (`curate_decisions._group_ruling_sets`).
  - *Connect*: one `{target, type}` entry in the source page's `relations:`
    frontmatter, a `log.md` line and, for a `supersedes` edge, the target's
    deprecated-status export. `unrelate` removes it
    (`lifecycle.prepare_unrelate` / `unrelate_core`), withdrawing the export
    when nothing else supersedes the target, and refuses an absent edge.
  - *How quickly*: one `type_tiers:` entry in `openkos.yaml`
    (`config.set_type_tier`, comment-safe text surgery). Nothing removes an
    entry today; `set-volatility` only sets one.
- **The ledgers.** `bundle.ledger.iter_ledgers` walks every committed sidecar. An
  absorbed survivor's own ledger stays on disk after its page is deleted (a
  chained merge), so a ledger does not imply a page.
- **The facade** may import only `openkos.application.*` and the other allowed
  modules; every ledger, sidecar and config read lives in `application/`.

## Decisions

### D1. Combined pages are every existing page with a ledger entry, newest first

`unmerge_service.merged_concepts(root)` walks `iter_ledgers`, derives each Concept
ID from the walked path (the traversal guard `list_kept_distinct` applies), and
calls the existing `merge_history` for it, so the records are exactly the
per-page read's. It keeps a page only when its concept file exists and its
history is non-empty, and orders pages by their newest `merged_at`, newest first,
then by Concept ID. A ledger whose page was itself absorbed is left out: its
merges are undone after the merge that absorbed it, and that page is listed.
A ledger that cannot be read is skipped, as every bundle-wide ledger walk skips
one (`_SIDECAR_SKIP_ERRORS`); `doctor` diagnoses it, and `merge_history` on that
page still refuses.

Rejected: listing every ledger. A page that no longer exists cannot be split
directly (`survivor_missing`), so listing it would offer a choice that always
refuses.

### D2. An undo is keyed by what the choice wrote, not by its decision

The decision's queue row was resolved when it was applied, and the same ruling,
relation or tier can be written by the CLI with no row at all. Each undo is
therefore keyed by the thing itself, as unmerge is (ADR-0064): the member set,
the edge `(source, type, target)`, the type name. No decision key is involved and
the queue is not touched.

### D3. One refusal type and one stale type for the three undos

`NotUndoable(reason)` with `not_kept_distinct` (no declined ruling over exactly
these members), `not_related` (the source page has no such typed relation, or one
of its pages no longer exists), `tier_unchanged` (the type's entry already is
the requested one) and `unknown_type` (not a concept type the engine knows).
`UndoStale` means the bytes changed between preview and apply. Per-kind refusal
types would add six names for four reasons; one type keeps the HTTP table to one
row per reason. Each kind keeps its own version type, because each carries a
different key.

### D4. Re-plan under one lock and compare the digest

Each apply validates its wait, takes the workspace lock once, re-plans from the
bytes on disk, returns `NotUndoable` when the re-plan refuses and `UndoStale`
when its digest differs from the version's, then writes and commits only the
paths it wrote. The plan and the write see the same bytes, so no drift guard is
needed. Each undo is one write and one commit (`openkos: reopen identity
a/b`, `openkos: unrelate a -> b (type)`, `openkos: set-volatility T -> tier` or
`openkos: clear-volatility T`).

### D5. What each version digests

- **Reopen:** the sorted member set and the bytes of the decisions sidecar that
  holds the ruling.
- **Unrelate:** the edge, the source page's bytes, the target page's bytes when
  the plan withdraws its deprecated-status export, and whether it does. A
  bystander change that alters whether the withdrawal happens moves the digest;
  any other bystander change does not matter, because the plan is made under the
  lock.
- **Tier change:** the type, the requested tier (`-` for removal) and the bytes
  of `openkos.yaml`.

`log.md` is not digested: the unrelate line is inserted over its current bytes,
as every verb does.

### D6. A tier undo restores the entry, removing it when there was none

`TierPlan.configured_tier` reports the type's `type_tiers` entry before the
change, or `None` when it had none and the engine's default applied. An undo
calls `preview_tier_change(type, configured_tier)`. With `None` the entry is
removed by `config.clear_type_tier`, the same text surgery as `set_type_tier`
(every other line byte-identical, the same un-editable shapes refused); a
`type_tiers:` header left with no entries stays. `vcs.autocommit.plan_config_edit`
takes `tier: str | None` so an uncommitted edit elsewhere in `openkos.yaml` is
handled as for a set (#1330). Writing the old effective tier explicitly was
rejected: it would pin the type and stop it following a later default.

### D7. An undo resolves nothing; a reopen asks the queue again from the app

An undo writes what the CLI verb writes and nothing else: no queue row is
resolved or proposed, no derived index is refreshed, no model is called. After a
reopen the app calls `POST /v1/queue/refresh` (model-free) so the group can be
offered again, then lists the decisions; a refresh that fails is ignored, and
the next refresh offers it.

### D8. The local HTTP API

| Route | Operation | Body |
| --- | --- | --- |
| `GET /v1/merged` | `merged_concepts` | none, `{concepts: [...]}` back |
| `GET /v1/kept-distinct` | `kept_distinct` | none, `{groups: [...]}` back |
| `POST /v1/reopen/preview` | `preview_reopen` | `{member_ids}` |
| `POST /v1/reopen/apply` | `apply_reopen` | `{version, wait_seconds}` |
| `POST /v1/unrelate/preview` | `preview_unrelate` | `{source_id, relation_type, target_id}` |
| `POST /v1/unrelate/apply` | `apply_unrelate` | `{version, wait_seconds}` |
| `POST /v1/tiers/preview` | `preview_tier_change` | `{type_name, tier}` (`tier` one of the three tiers or `null`) |
| `POST /v1/tiers/apply` | `apply_tier_change` | `{version, wait_seconds}` |

New rows: `not_kept_distinct`, `not_related` and `tier_unchanged` (409),
`unknown_type` (422); `UndoStale` is `stale` (409). The apply bodies are
`_EchoRequest`s, so the version is echoed unchanged; a flipped field is `stale`.
The tier is typed by the public alias `VolatilityTier`, so an unknown tier is a
422 `invalid_request` before the operation runs.

### D9. The review screen

- **Combined pages.** `load()` reads `GET /v1/merged` with the decisions and
  builds the combined list from it, reading each survivor's page for its title.
  The section is headed "Pages you combined" and lists every page, not only this
  session's. A failed read leaves the list as it was.
- **Undo.** The `done` step carries what Undo reverses, from the previewed plan:
  a split (as today), a reopen (`KeepDistinctPlan.member_ids`), an unrelate
  (`RelatePlan`'s edge as written) or a tier change (`TierPlan.type_name` and
  `configured_tier`). Undo previews and applies in one click: the person already
  saw the choice, and the undo restores what was there. A busy apply is retried
  for five seconds; a stale or refused undo is worded from the brief and offers
  only reload or OK. After success the confirmation is replaced by the undone
  line and Undo disappears.
- **A group decision's keep-all-separate** reverses the one ruling over all
  members. A combined group's unmarked-member rulings are not undone by Split
  again (out of scope, as in ADR-0064).

## Sequence: an undo

```
person -> model.undo()
model  -> POST /v1/{kind}/preview        (no lock, no write)
engine -> preview + version (digest)
model  -> POST /v1/{kind}/apply {version}  (retry while busy, 5 s)
engine -> lock; re-plan; digest equal? write; commit; unlock
model  -> done: undone line, no Undo; reopen: POST /v1/queue/refresh; GET /v1/decisions
```

## Testing

Strict TDD. Application tests over real git-backed workspaces made through the
decision surface (each undo reverses a real applied decision and the tree returns
to its prior bytes, `log.md` excepted for unrelate), facade tests for model-free,
lock-free previews and byte-unchanged trees, stale and refusal paths, the surface
classifier, route and error-table tests, the OpenAPI snapshot, and vitest for the
model and the wording. Mutation checks on each guard line.
