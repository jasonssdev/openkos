# Delta for Query Command

## MODIFIED Requirements

### Requirement: Sensitivity Is The High-Water-Mark Of Cited Concepts

WHEN filing via `--save`, `query` MUST re-read each cited concept's
frontmatter and set the filed concept's sensitivity to the high-water-mark
(`okf.combine_sensitivity`) across them, seeded at `cfg.default_sensitivity`.
An unreadable OR unparseable cited concept MUST fold the running floor to
`confidential` -- the most-restrictive level, NOT be skipped -- fail-closed,
consistent with the project's pervasive "cannot verify sensitivity ->
confidential" stance (`okf._rank`, `sensitivity.blocks_llm_send`). WHEN
`--include-confidential` caused a confidential cited concept to be used, the
filed concept's sensitivity MUST be confidential. WHEN there are zero
citations, `query` MUST REFUSE to file, exit non-zero, and leave the bundle
unchanged -- `build_concept` requires non-empty provenance, and a sourceless
"derived" concept is not a real derived node. WHEN the filed concept's OKF
`--type` has a configured per-type sensitivity offset
(`type-sensitivity-defaults`), the cited-concept high-water-mark computed
above is a floor, not the final value: the filed sensitivity is
`combine_sensitivity(cited_high_water_mark, raise_by(cfg.default_sensitivity,
offset))`, so a type-defaulted filed answer may be saved strictly above the
cited high-water-mark, never below it. The success message MUST carry the
born-above-floor advisory (`type-sensitivity-defaults`) whenever this raise
applies.
(Previously: the filed concept's sensitivity was described as exactly the
cited-concept high-water-mark, with no type-dependent raise above it.)

#### Scenario: Confidential citation propagates confidentiality

- GIVEN `--include-confidential` is set and one cited concept is
  confidential
- WHEN `openkos query "<question>" --save` files the answer
- THEN the filed concept's sensitivity is confidential

#### Scenario: A type-defaulted filed answer is saved above the cited high-water-mark

- GIVEN `openkos query "<question>" --save --type Person` where every cited
  concept's high-water-mark resolves to `public`, and a per-type sensitivity
  offset configured for `Person` that raises the workspace floor to
  `private`
- WHEN the answer is filed
- THEN the saved concept's `sensitivity` is `private`, strictly above the
  cited high-water-mark, and the success message carries the
  born-above-floor advisory naming it
