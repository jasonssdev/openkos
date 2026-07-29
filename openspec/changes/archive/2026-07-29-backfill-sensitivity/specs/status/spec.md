# Delta for status

## ADDED Requirements

### Requirement: Needs-Attention Surfaces Below-Source Sensitivity And Uncovered Multi-Source Descendants

`openkos status` MUST fold `lint`'s `below-source-sensitivity` and
`multi-source-uncovered` findings into its "needs attention" section, as two
distinct entries, reusing the same in-memory `docs` list from the
`collect_docs()` call it already makes — it MUST NOT perform a second
`collect_docs()` call or any new bundle walk. Each surfaced
`below-source-sensitivity` entry MUST name the descendant, its Source, and
both sensitivity levels; each surfaced `multi-source-uncovered` entry MUST
name the descendant and every cited concept id, and MUST be labeled as not
covered by `backfill-sensitivity`. Findings MUST be informational: their
presence MUST NOT cause a non-zero exit.

#### Scenario: A below-Source descendant is surfaced under needs attention

- GIVEN a bundle containing a provenance descendant below its single citing
  Source's `sensitivity`
- WHEN `openkos status` runs
- THEN it lists that descendant under "needs attention", naming the
  descendant, the Source, and both levels, and still exits 0

#### Scenario: An uncovered multi-source descendant is surfaced distinctly

- GIVEN a bundle containing a provenance descendant that is a member of no
  single Source's closure, which the `sensitivity-backfill` sweep therefore
  cannot raise
- WHEN `openkos status` runs
- THEN it lists that descendant under "needs attention" as not covered by
  `backfill-sensitivity`, distinct from any `below-source-sensitivity`
  entry, and still exits 0

#### Scenario: A clean bundle adds no new needs-attention entries

- GIVEN a bundle where every descendant already meets or exceeds every
  citing Source's `sensitivity`
- WHEN `openkos status` runs
- THEN no `below-source-sensitivity` or `multi-source-uncovered` entry
  appears under "needs attention"

#### Scenario: No new bundle walk is introduced

- GIVEN `status` already calls `lint_check.collect_docs()` once
- WHEN the below-Source and multi-source-uncovered checks also run
- THEN they reuse that same in-memory `docs` list and `status` performs no
  more bundle walks than before this change
