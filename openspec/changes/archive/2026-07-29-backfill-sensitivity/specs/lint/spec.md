# Delta for lint

## ADDED Requirements

### Requirement: Below-Source Sensitivity Scan

`openkos lint` MUST flag any provenance descendant for which
`okf.combine_sensitivity(descendant_sensitivity, source_sensitivity)`
differs from the descendant's current value — the same test the sweep uses
to stage a write, so a missing, blank, or unrecognized `sensitivity` is
ranked fail-closed (ADR-0003) and is flagged — as a
`below-source-sensitivity` finding (`LintFinding.kind`, joining
`stale`/`orphan`/`dangling`/`unextracted`). The scan MUST reuse the SAME
closure algorithm and rank comparator the `sensitivity-backfill` verb uses
(`bundle.provenance.provenance_closure` plus `okf.combine_sensitivity`) and
MUST reuse `LintDoc`'s existing single-pass `collect_docs` walk; it MUST NOT
introduce a new bundle walk and MUST NOT render write-ready file content.
The finding's detail MUST name the descendant's current level, its Source's
level, and the Source's id. This scan MUST NOT change `lint`'s exit code:
the Non-Gating Exit Contract already covers all existing kinds and MUST
cover this one too.

#### Scenario: A descendant below its single Source is flagged

- GIVEN a Source with `sensitivity: confidential` and a provenance
  descendant citing only that Source with `sensitivity: public`
- WHEN `openkos lint` runs
- THEN it reports a `below-source-sensitivity` finding naming the
  descendant, its current level, the Source's level, and the Source's id

#### Scenario: A descendant at or above its Source's level produces no finding

- GIVEN a Source and a provenance descendant already at or above the
  Source's `sensitivity`
- WHEN `openkos lint` runs
- THEN no `below-source-sensitivity` finding is reported for that descendant

#### Scenario: A clean bundle reports no below-source findings

- GIVEN a bundle where every descendant's `sensitivity` already meets or
  exceeds its citing Source's level
- WHEN `openkos lint` runs
- THEN it reports zero `below-source-sensitivity` findings and still exits 0

#### Scenario: below-source-sensitivity findings do not change the exit contract

- GIVEN a bundle containing one or more `below-source-sensitivity` findings
- WHEN `openkos lint` runs
- THEN it reports the findings and still exits 0, and no bundle file is
  created, modified, or deleted

#### Scenario: A missing or dirty sensitivity under a Source is flagged fail-closed

- GIVEN a provenance descendant whose `sensitivity` is missing, blank, or
  not a recognized level, citing a single Source with `sensitivity: public`
- WHEN `openkos lint` runs
- THEN `okf.combine_sensitivity` ranks the dirty value fail-closed (ADR-0003)
  and a `below-source-sensitivity` finding is reported for that descendant

### Requirement: Multi-Source Uncovered-Descendant Scan

`openkos lint` MUST flag, as a distinct finding kind
(`multi-source-uncovered`), any doc with a non-empty `provenance` whose
cited ids all resolve to bundle concepts, which is a member of no
single-Source closure, and whose `sensitivity` sits strictly below the
high-water-mark of its cited concepts' levels. This category MUST be
reported separately from `below-source-sensitivity` findings — it names
descendants the `sensitivity-backfill` sweep cannot and will not raise, per
that verb's per-Source scan scope — and its detail MUST name the
descendant, its current level, and every cited concept id with that
concept's level, and MUST mark the finding as not covered by
`backfill-sensitivity`. A doc whose `provenance` cites two or more concepts
that all fall inside a single Source's closure MUST be reported as
`below-source-sensitivity`, not as `multi-source-uncovered`.

#### Scenario: A multi-source descendant below one of its Sources is flagged distinctly

- GIVEN a derived concept citing two Sources, neither of which lies inside
  the other's provenance closure, one at `sensitivity: public` and one at
  `sensitivity: confidential`, with the descendant itself at
  `sensitivity: public`
- WHEN `openkos lint` runs
- THEN it reports a `multi-source-uncovered` finding naming the descendant
  and both cited concept ids, distinct from any `below-source-sensitivity`
  finding

#### Scenario: A multi-source descendant already at the highest cited level produces no finding

- GIVEN a derived concept citing two Sources whose `sensitivity` is
  `public` and `private`, with the descendant already at `private`
- WHEN `openkos lint` runs
- THEN no `multi-source-uncovered` finding is reported for that descendant

#### Scenario: A descendant citing one Source plus a foreign derived concept is flagged as uncovered

- GIVEN a derived concept whose `provenance` cites one Source directly and
  a second concept that is itself derived from a different Source, with the
  descendant's `sensitivity` below the high-water-mark of both cited levels
- WHEN `openkos lint` runs
- THEN it reports a `multi-source-uncovered` finding naming the descendant,
  its current level, and both cited concept ids with their levels

#### Scenario: A descendant citing two concepts inside the same Source's closure is reported as below-source, not uncovered

- GIVEN a derived concept whose `provenance` cites two concepts that are
  both members of the same Source's closure, with the descendant's
  `sensitivity` below that Source's level
- WHEN `openkos lint` runs
- THEN it reports a `below-source-sensitivity` finding for that descendant,
  and no `multi-source-uncovered` finding is reported for it
