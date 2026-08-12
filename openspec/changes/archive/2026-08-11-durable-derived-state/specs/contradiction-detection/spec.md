# Delta for Contradiction Detection

Slice 1a.

## ADDED Requirements

### Requirement: Merged-Body Candidate Source Relocates Without Changing Verdict Semantics

`_merged_body_candidates` MUST read merge-ledger entries from each
survivor's `bundle/.state/ledger/` sidecar rather than from the survivor's
own `merged_from` frontmatter. This is a source relocation only: the set
of merged-body candidate pairs generated, the per-entry
`sensitivity.merged_content_blocked` gating applied to each (once per
entry, never once per survivor — see `sensitivity-aware-llm`), and the
resulting `CONTRADICTS`/`CONSISTENT`/`UNCERTAIN` verdicts MUST be
unaffected by the relocation for any fixed bundle state.

#### Scenario: Same bundle state yields the same candidates and verdicts before and after relocation
- GIVEN a bundle whose merge ledger entries are read from
  `bundle/.state/ledger/` instead of survivor frontmatter, but whose
  content is otherwise identical to a pre-relocation bundle
- WHEN `find_contradictions` runs
- THEN it produces the same merged-body candidate pairs, in the same
  order, judged to the same verdicts as it would have against the
  pre-relocation frontmatter-embedded ledger

#### Scenario: A sensitivity-blocked entry stays blocked after relocation
- GIVEN a ledger sidecar entry that `merged_content_blocked` would exclude
  under the per-entry gate
- WHEN `_merged_body_candidates` reads it from `bundle/.state/ledger/`
- THEN it is still excluded from the judged candidate set, identically to
  the pre-relocation behavior
