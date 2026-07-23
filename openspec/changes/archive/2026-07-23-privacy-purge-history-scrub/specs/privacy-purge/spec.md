# Delta for privacy-purge

## ADDED Requirements

### Requirement: Whole-History Content-Scrub Of index.md And log.md

After a successful rewrite, `purge` MUST content-scrub `bundle/index.md` and
`bundle/log.md` across ALL git history (every past commit's blob of exactly
these two files, and no other path) by removing, as FULL LINE removals, each
purge-set member's catalog bullet, log entries, and any `forget` tombstone
referencing it. Matching MUST use markdown link-identity (the same
`_link_identity` used elsewhere), never a bare id-substring match. A line
whose link-identity does NOT match a purge-set member — including a
surviving sibling concept's catalog bullet or an unrelated log entry that
merely mentions the purged id in prose — MUST be left byte-identical in every
commit. The scrub MUST run in the SAME single `git-filter-repo` pass as the
whole-file expunge (no second rewrite). Content outside `index.md`/`log.md`
(e.g. a surviving concept's bundle body) MUST NOT be scrubbed even if it
contains the purged id or title.

#### Scenario: Purged concept is gone from index.md and log.md history
- GIVEN a successful purge of concept `<id>` with title `<title>`
- WHEN every commit's `bundle/index.md` and `bundle/log.md` blobs are
  inspected after the rewrite
- THEN neither `<id>` nor `<title>` appears in any commit's blob of either
  file

#### Scenario: Surviving sibling and prose mention round-trip unchanged
- GIVEN a purge-set member's catalog bullet exists alongside a surviving
  sibling concept's catalog bullet in `index.md`, and a `log.md` entry that
  mentions the purge-set member's id only in prose (not as its own link)
- WHEN the history scrub runs
- THEN the sibling's catalog bullet and the prose-mention log entry are
  byte-identical, in every historical commit, to their pre-purge content

#### Scenario: Scrub is scoped to index.md and log.md only
- GIVEN a surviving concept's bundle body contains the purged id or title in
  its own text
- WHEN the history scrub runs
- THEN that bundle body's content is unchanged in every commit; only
  `bundle/index.md` and `bundle/log.md` are rewritten

### Requirement: Live log.md Tombstone Cleanup

After a successful rewrite, `purge` MUST remove any LIVE `bundle/log.md`
`forget` tombstone entry referencing a purge-set member, via a new
`remove_log_entry` function mirroring `remove_index_entry`'s live-index
cleanup, matched by the same link-identity rule.

#### Scenario: Prior forget tombstone removed from live log.md
- GIVEN a concept was previously `forget`-ed (leaving a tombstone in the
  live `log.md`) and is now purged
- WHEN the purge completes
- THEN the live `log.md` no longer contains a tombstone entry for that
  concept's id

## MODIFIED Requirements

### Requirement: Live Index Cleanup After Successful Purge

After a successful rewrite, `purge` MUST remove the LIVE `index.md` catalog
bullet for every purge-set member (reusing `forget`'s own
`remove_index_entry`/write path), so the live catalog never keeps a bullet
pointing at a concept absent from every commit. `purge` MUST NOT print any
warning stating that purged content remains in `index.md`/`log.md` history,
because the whole-history content-scrub requirement (above) removes it: after
a successful purge, the purged id/title MUST NOT appear anywhere in
`index.md` or `log.md`, in any commit, live or historical.

(Previously: "Mandatory Residual-Leak Warning" — required printing a warning
that the purged id/title/tombstone remained in index.md/log.md history and
that purge did not provide complete right-to-be-forgotten. Slice 2 removes
this warning and its condition entirely.)

#### Scenario: Live index bullet is removed
- GIVEN a successful purge of any scope
- WHEN the command completes
- THEN `index.md` no longer contains a catalog bullet for any purge-set
  member

#### Scenario: No residual warning is printed
- GIVEN a successful purge of any scope
- WHEN the command completes
- THEN stdout does NOT contain any warning stating that purged content
  remains in `index.md`/`log.md` history, because no such residual exists
