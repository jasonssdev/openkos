# Delta for Stable Python API

## ADDED Requirements

### Requirement: A Page Is Readable As A Curated Field Set

`openkos.api` MUST export `read_page(workspace, concept_id)`, returning a
`Page` or `None`. A `Page` MUST carry exactly `concept_id` (the canonical
Concept ID), `title` (the frontmatter title with whitespace collapsed),
`okf_type` (the frontmatter `type` when it is a string, else `None`),
`excerpt`, `sensitivity` and `sources`, and no other stored field: it is a
curated field set, never a frontmatter passthrough.

- `excerpt` MUST be the opening of the body as plain text: a leading level-one
  heading equal to the title is dropped; a markdown link keeps its label and
  loses its target; citation markers such as `[1]`, emphasis marks and heading
  marks are dropped; runs of whitespace become one space; and text longer than
  280 characters is cut at the last word boundary before that length and ends
  in `…`.
- `sensitivity` MUST be one of `public`, `private`, `confidential`, normalised
  with the engine's fail-closed rank: a missing or blank value is `private` and
  an unrecognised one is `confidential`.
- `sources` MUST be a tuple of `PageSource(source_id, file_name, added_at)`,
  one per provenance entry, in provenance order, whose document is a readable
  `Source` with a `raw/` resource: `file_name` is that resource's base name and
  `added_at` the Source's `generated.at` as recorded, or `None` when absent.
  Entries that are not such a Source are omitted.

The read MUST return `None` for an id that is not a valid Concept ID, a page
that does not exist, and a page whose file cannot be read or whose frontmatter
cannot be parsed. It MUST NOT take the workspace lock, build a model client or
write anything, and it applies no disclosure gate (the surface is owner-local).

#### Scenario: A page is read with its sources

- GIVEN a Person page whose provenance names one Source of `raw/notes.md`,
  added at `2026-10-12T09:00:00Z`
- WHEN `read_page` is called for it
- THEN it returns its title, `okf_type` `Person`, its plain opening and one
  source with `file_name` `notes.md` and that `added_at`
- AND the workspace is unchanged

#### Scenario: The excerpt is plain and bounded

- GIVEN a page whose body opens with its title as a heading, a link
  `[Stoicism](/concepts/stoicism.md)`, a citation marker `[1]` and 400
  characters of text
- WHEN `read_page` is called
- THEN the excerpt starts after the heading, contains `Stoicism` but not the
  link target or the marker, is at most 281 characters and ends in `…`

#### Scenario: An unrecognised sensitivity fails closed

- GIVEN a page whose `sensitivity` is `secret`, and one with none
- WHEN `read_page` is called for each
- THEN the first reads `confidential` and the second `private`

#### Scenario: An invalid, missing or unreadable page is None

- GIVEN an id with a `..` segment, an id with no file, and a page whose
  frontmatter does not parse
- WHEN `read_page` is called for each
- THEN each returns `None` and nothing is written

### Requirement: The Page Read Extends v1 Additively

The page read MUST be added within `API_VERSION` 1, and every change to the
public surface MUST be classified additive by the surface classifier, so the
golden snapshot is regenerated and `API_VERSION` is not raised: new names
(`Page`, `PageSource`, `read_page`). No existing union, alias or signature
changes.

#### Scenario: The page-read surface diff is additive

- GIVEN the snapshot recorded at the merge base with `origin/main`
- WHEN the snapshot is regenerated after the change
- THEN every difference is classified additive and `API_VERSION` is still 1
