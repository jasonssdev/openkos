# Delta for desktop-review

## MODIFIED Requirements

### Requirement: A Choice Is Previewed, Then Applied Against Its Version

Choosing an option other than Decide later or Check these MUST first preview
it (`POST /v1/decisions/{key}/preview`) and show what it will do from the plan
(for a combine: the name the page keeps and the number of other pages that
will point to it, and the "different files" line when the plan reports one),
then apply it only when the person confirms, echoing the preview's version
unchanged. After apply the app MUST show the brief's confirmation in the past
tense, MUST add "The combined page is marked Only on this Mac." when the
result reports `committed_confidential`, and MUST reload To review. The
confirmation of a combine, a keep separate, a connection and a how-quickly
setting MUST offer Undo; no other confirmation offers one.
(Previously: only a combine's confirmation offered Undo.)

#### Scenario: A combine is applied against the previewed version

- GIVEN a previewed combine whose version is V
- WHEN the person confirms
- THEN `POST /v1/decisions/{key}/apply` is sent with version V unchanged
- AND the confirmation reads "Combined into '{title}'." with Undo

#### Scenario: A keep separate is confirmed with Undo

- GIVEN a previewed keep separate
- WHEN the person confirms and the apply succeeds
- THEN the confirmation reads "Kept separate." with Undo

### Requirement: A Combine Is Undone With Split Again

The app MUST list, under "Pages you combined", every page `GET /v1/merged`
returns, newest combine first, each with one "Combined from '{title}' on
{date}" line per merge record, newest first, each with Split again; the list
MUST be read whenever To review is loaded, so it survives a restart of the app.
A combine's Undo is Split again on its oldest record. Split again MUST preview
(`POST /v1/unmerge/preview`) and, when the plan has more than one step, say "To
split this, the later combines on this page are split first: {list}. Continue?"
before applying with the version unchanged. The app MUST NOT send
`discard_survivor_edits`: a `survivor_edited` refusal MUST show "This page was
changed after it was combined, so it can't be split here." with OK only. An
interrupted split MUST say "Only some of these were split. Each page that was
split is back as it was." and read the combined pages again.
(Previously: only the pages combined while the app had been open were listed,
under "Combined just now".)

#### Scenario: A later combine is split first

- GIVEN a page that absorbed b and then c, and Split again on b
- WHEN the preview returns two steps
- THEN the app lists c as split first and applies only after Continue

#### Scenario: An edited page is refused without an offer

- GIVEN a split whose preview answers `survivor_edited`
- WHEN the answer arrives
- THEN the refusal sentence is shown with OK and no control to split anyway

#### Scenario: A combine made before the app opened can be split

- GIVEN `GET /v1/merged` returns a page combined in an earlier session
- WHEN To review is loaded
- THEN that page is listed under "Pages you combined" with Split again on each
  of its lines

## ADDED Requirements

### Requirement: Keep Separate, A Connection And A Setting Are Undone From Their Confirmation

Undo on the confirmation of a keep separate MUST reopen exactly the members the
plan kept separate (`POST /v1/reopen/preview`, then `/apply`), then refresh the
decision queue (`POST /v1/queue/refresh`, a failure ignored) and reload To
review. Undo on a connection MUST remove exactly the edge the plan wrote
(`POST /v1/unrelate/preview`, then `/apply`). Undo on a how-quickly setting MUST
restore the plan's `configured_tier`, removing the entry when it is `null`
(`POST /v1/tiers/preview`, then `/apply`). Each undo previews and applies in one
step, echoing the version unchanged, retries a busy apply for up to five
seconds, and on success replaces the confirmation with the brief's undone line
and removes Undo. A stale or refused undo MUST be worded from the brief and MUST
NOT be applied again blindly.

#### Scenario: A keep separate is undone

- GIVEN a pair just kept separate
- WHEN the person chooses Undo
- THEN the reopen is previewed and applied for that pair, the queue is
  refreshed, and the confirmation reads "Undone. We'll ask you about these
  again." with no Undo

#### Scenario: A setting with no earlier entry is restored by removal

- GIVEN a set-tier decision whose plan reported `configured_tier` `null`
- WHEN the person confirms it and then chooses Undo
- THEN `POST /v1/tiers/preview` is sent with `tier` `null`

#### Scenario: A connection already removed is refused in plain words

- GIVEN a connection removed elsewhere after it was confirmed
- WHEN the person chooses Undo and the preview answers `not_related`
- THEN the app says "These pages aren't connected any more." and offers OK
