# Proposal: undo-sensitivity-change

Closes #1706 (the undo half of #1695). When a label change raises derived pages
with the page the person changed, the app says "To change those pages back,
change each one on its own." and offers no undo. The person is left with a chore
and no way back.

Success: where the engine can put the whole change back, the app offers **Undo
this change**, which puts the page and the pages raised with it back in one
commit, each derived page at the level its remaining sources require. It is
refused, with an honest message, when any of those pages changed since. It never
lowers a page below what a page it cites requires, and nothing confidential
leaves the device.

## Scope

### In Scope

1. Recording, additively, what a change raised: the `log.md` line a sensitivity
   change writes ends with ` Also raised: [id](/id.md) (was 'level'), ...` when
   it raised pages. A change that raised nothing writes the line it always did.
2. `application/sensitivity_undo_service.py`: preview and apply of an undo, in
   the same two phases as the change (digest of every byte the plan rests on,
   one lock, plan again, one commit).
3. `openkos.api.preview_undo_sensitivity` / `apply_undo_sensitivity` and the
   types `SensitivityUndoPreview`, `SensitivityUndoVersion`, `RestoredPage`,
   `SensitivityUndone`, `SensitivityNotUndoable`; additive, `API_VERSION`
   unchanged.
4. `POST /v1/pages/sensitivity/undo/preview` and `.../undo/apply` on the local
   API, regenerated client types.
5. The desktop: **Undo this change** in the label confirmation where the engine
   offers it; the old sentence stays where it does not.

### Out of Scope

- Undoing a change from before the record existed, or one that raised nothing:
  the raised set cannot be recovered, so none is offered. A change that raised
  nothing already has the single-page Undo.
- Undoing anything but the newest change to a page.
- A new store: the record is the `log.md` line.

## Approach

**Where the raised set comes from.** Today nothing records it: the log line names
only the page, and the commit message only the page and the level. The commit
does hold the paths, but its sha is not kept anywhere the app can find. So the
log line gains the raised pages with the level each had before. `log.md` already
holds sensitivity transitions, is already swept by `forget`, and never leaves the
device in an export, so it adds no new store to the privacy sweep.

**Reading it back.** The newest `log.md` entry about the page must be a change
with an `Also raised` clause, strictly parsed; anything else (older line, no
raise, an undo, a hand-edited line) is `no_record`.

**The high-water mark.** The pages are first set to their recorded levels in
memory, then the cited high-water rule (`resolve_cited_high_water_raises`, the
one `sweep` uses) is run over the result: a derived page ends at the highest of
its recorded level and what every page it cites requires, so a second source
that came to cite it since keeps it where it is, and so does a page that cites a
page that stays. A page with a citation that does not resolve is not lowered. A
page is never raised above where it stands: the undo only puts back.

**Refusal.** The page must still read the level the change set, and each raised
page the level the change left it (`combine(was, level)`); otherwise
`changed_since`.

**Order.** The page is written first, then the restored pages, then the log, so a
failure part-way leaves the bundle over-classified, never under.

No ADR: nothing here is hard to reverse; the record is one appended clause and the
surface is additive.
