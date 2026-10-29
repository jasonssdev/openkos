# Stable Python API Specification (delta)

## ADDED Requirements

### Requirement: A Whole Sensitivity Change Is Undone Against A Previewed Version

`openkos.api` MUST export `preview_undo_sensitivity(workspace, concept_id)` and
`apply_undo_sensitivity(workspace, version, *, wait_seconds=0.0,
on_warning=None)`, taking back the newest sensitivity change to a page together
with the derived pages raised with it, in one commit.

What the change raised MUST be read back from the `log.md` line it wrote. An
invalid Concept ID or a page that does not exist MUST return
`SensitivityNotUndoable("not_found")`; a newest entry that is not a change that
recorded the pages it raised (an older change, a change that raised nothing, an
undo, an edited line) MUST return `SensitivityNotUndoable("no_record")`; a page
or a raised page no longer at the level the change left it MUST return
`SensitivityNotUndoable("changed_since")`.

The preview MUST return a `SensitivityUndoPreview` with `concept_id`, `title`,
`now`, `restores`, `pages` (`RestoredPage`: `concept_id`, `title`, `now`,
`after`) and a `SensitivityUndoVersion` (`concept_id`, `content_digest`). Each
raised page's `after` MUST be the highest of the level it had before the change
and the level every page it cites requires, MUST NOT be above its `now`, and
MUST equal `now` when a citation does not resolve. The digest MUST cover
`log.md` and every page of the bundle. The preview MUST NOT take the lock, build
a client or write.

The apply MUST take the lock once, plan again, and return `SensitivityStale` or
`SensitivityNotUndoable` without writing. Otherwise it MUST write the page, then
the pages whose `after` differs from `now`, then one `log.md` line, commit only
those paths, and return `SensitivityUndone` (`concept_id`, `level`, `restored`,
`kept`, `commit_sha`, `committed_confidential`). It MUST NOT build a model client
and MUST NOT refresh a derived store.

#### Scenario: The page and the pages raised with it go back in one commit

- GIVEN a Source raised to `confidential`, with two pages raised with it
- WHEN the undo is previewed and the returned version applied
- THEN the Source and both pages are at their previous levels in one commit

#### Scenario: A page another source requires is not lowered

- GIVEN that change, and afterwards a raised page came to cite a second,
  confidential source
- WHEN the undo is applied
- THEN that page stays `confidential`, it is listed in `kept`, and the pages that
  cite it stay with it

#### Scenario: A change with no record is not offered

- GIVEN a change whose log line carries no `Also raised` clause
- WHEN the undo is previewed
- THEN it returns `SensitivityNotUndoable("no_record")` and nothing is written

#### Scenario: A page changed since is refused

- GIVEN a raised page edited to another level after the change
- WHEN the undo is previewed
- THEN it returns `SensitivityNotUndoable("changed_since")` and nothing is written

### Requirement: Undoing A Sensitivity Change Extends v1 Additively

The names `preview_undo_sensitivity`, `apply_undo_sensitivity`,
`SensitivityUndoPreview`, `SensitivityUndoVersion`, `RestoredPage`,
`SensitivityUndone` and `SensitivityNotUndoable` MUST be added within
`API_VERSION` 1, with the surface snapshot regenerated and classified additive.

#### Scenario: The snapshot classifies the change as additive

- WHEN the surface snapshot is regenerated
- THEN every change it prints is additive and `API_VERSION` is 1
