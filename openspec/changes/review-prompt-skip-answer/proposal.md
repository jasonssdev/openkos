# Proposal: Review Prompts Get A Skip Answer; Only `d` Records A Ruling (#1264)

## Intent

The per-item review prompts offered `y` and `N`. On the Identity prompts
(`adjudicate --apply`, `curate`) the `N` default silently recorded a permanent
keep-distinct ruling with its own commit (#797); nothing in the prompt said
so, and there was no way to say "not now". A true duplicate declined by
mistake had to be undone with `duplicates --reopen` and a manual `merge`.
Faced with 69 per-item questions, a reviewer also had no way to accept a
stage's remainder at the moment the queue was in front of them.

## Scope

### In Scope

- Identity prompt (`adjudicate --apply`, `curate` Identity):
  `Merge <absorbed> into <survivor>? [y]es / [s]kip / [d]istinct (d records a
  permanent keep-distinct ruling)`. `s`, `n` and Enter skip (write nothing,
  the pair stays pending and is offered again next run); only `d` records the
  ruling.
- Structure, Metadata and `suggest-relations --apply` per-item prompts:
  `[y/N/s]`; `s` skips, `n` declines, neither persists anything.
- `a` (accept remaining) on the Structure and Metadata prompts for
  bulk-acceptable items; asymmetric relation types still ask per item.
- The end-of-run summary distinguishes skipped (`left pending`) from declined
  (`d`).
- Living-spec deltas and `docs/cli.md`.

### Out of Scope

- Any Identity accept-recommended path or structural-signal heuristic: it
  needs the pre-registered measurement ADR-0034 requires (part 3 of #1264,
  tracked in the evals lane). `a` is deliberately not offered on Identity.
- Remembering a skip across runs (no snooze state); skip is "not now".
- The reconcile and supersession consent prompts, the whole-plan
  `typer.confirm` gates, and `merge`'s own confirmation.
- `application/lifecycle.py`: the shared `merge_walk_confirmation` factory is
  unchanged; the CLI layer renders the new suffix.

## Decisions

| # | Decision | Evidence |
|---|---|---|
| 1 | Only an explicit `d` persists a ruling; `n` is a synonym of skip | #1264: the destructive-permanent answer must be a deliberate key, not the default |
| 2 | Skip is not remembered | The pending-queue spec already returns a presented-but-unresolved row to `pending`; no new state is needed |
| 3 | `a` only on non-destructive stages, bulk-acceptable items | Mirrors `--accept` (Identity excluded, #702/ADR-0034) and #624 (asymmetric types always ask) |

## Capabilities

### Modified Capabilities

- `entity-resolution-adjudication` -- MODIFIED "Survivor/Absorbed Preview And
  Prompt", "Prompt Response Semantics", "End-Of-Run Summary With Breakdown".
- `curate-command` -- MODIFIED "Structure Stage Writes Through The Relate
  Core", "Bulk Acceptance Excludes Asymmetric Relation Types"; ADDED "Identity
  Prompt Separates Skip From A Keep-Distinct Ruling", "Structure, Metadata And
  suggest-relations Prompts Offer Skip", "Accept-Remaining Answer On
  Non-Destructive Stages".

## Approach

A shared tri-state answer helper in `cli/curate.py` replaces the boolean
`_confirm` at the Identity, Structure and Metadata call sites; the Identity
prompt is the shared factory's question with the new suffix. The
`suggest-relations` observer port returns a tri-state. `a` extends the run's
accepted-stage set mid-walk.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/curate.py` | Modified | Tri-state helpers, Identity/Structure/Metadata walks |
| `src/openkos/cli/main.py` | Modified | `adjudicate --apply` walk and summary, `suggest-relations` observer |
| `src/openkos/application/suggest_relations_service.py` | Modified | Observer port return type, skipped vs declined |
| `tests/unit/...` | Modified | Prompt, summary and persistence tests |
| `docs/cli.md` | Modified | Prompt answers |

## Rollback Plan

Revert the commits; no data migration. Rulings already recorded stay valid.

## Success Criteria

- [ ] `n`, `s` and Enter on the Identity prompt write nothing and record no
      ruling; `d` records it.
- [ ] The prompt states what `d` records.
- [ ] Structure/Metadata/`suggest-relations` accept `s`; Structure/Metadata
      accept `a`; Identity does not.
- [ ] All gates green.
- [ ] Part 3 of #1264 stays open (PR uses `Refs #1264`).
