# Delta for Type Sensitivity Defaults

## MODIFIED Requirements

### Requirement: Both `build_concept` Birth Seams Consult The Type Default

Every call site that builds a new OKF concept via `okf.build_concept` MUST
consult the effective per-type sensitivity offset mapping and apply the
floor-relative raise formula, using the base sensitivity appropriate to that
seam. This applies to BOTH the ingest extraction path (base = the Source's
resolved `stamp_sensitivity`) and the `query --save` filed-answer path (base
= the existing cited-concept high-water-mark). The two seams MUST produce
identical birth-sensitivity output for the same `(base_sensitivity, type,
cfg.default_sensitivity, cfg's per-type mapping)` inputs, since both route
through the same shared formula.

`openkos import` is a THIRD birth seam, even though it adopts a foreign
document rather than calling `okf.build_concept`. For each adopted concept,
the base sensitivity is the import's folded label (the high-water mark of
`cfg.default_sensitivity`, the `--sensitivity` value when given, and the
folded foreign label, per `okf-import`), and the same shared formula MUST be
applied to it with the adopted concept's OKF `type`. The result MUST be
identical to what the other two seams produce for the same inputs. The
import-written anchor Source is not a concept born through this seam and
MUST NOT be type-defaulted (see "Sources Are Never Type-Defaulted").
(Previously: only the ingest and `query --save` seams consulted the type
default; adoption by import was not a seam.)

#### Scenario: Ingest applies the Person default

- GIVEN a workspace with `default_sensitivity: public` and a
  configured `{"Person": 1}` mapping
- WHEN `ingest` extracts and stages a `Person` concept from a Source
  resolved at `public`
- THEN the staged `Person` concept's `sensitivity` is `private`

#### Scenario: `query --save --type Person` applies the same Person default

- GIVEN a workspace with `default_sensitivity: public` and a configured
  `{"Person": 1}` mapping, and a `query --save --type Person` invocation
  whose cited-concept high-water-mark is `public`
- WHEN the filed answer is saved
- THEN the saved `Person` concept's `sensitivity` is `private`, matching
  what the ingest seam would produce for the same inputs

#### Scenario: Import applies the Person default

- GIVEN a workspace with `default_sensitivity: public` and a configured
  `{"Person": 1}` mapping, and a foreign `Person` document with no label
- WHEN it is adopted by `openkos import`
- THEN the adopted `Person` concept's `sensitivity` is `private`, matching
  what the ingest seam would produce for the same inputs

#### Scenario: An imported Person keeps a higher foreign label

- GIVEN the same configuration and a foreign `Person` labelled
  `confidential`
- WHEN it is adopted
- THEN its `sensitivity` is `confidential`, not lowered to `private`

#### Scenario: Import with an empty mapping applies no offset

- GIVEN a workspace whose `type_sensitivity_defaults` is `{}` and a foreign
  unlabelled `Person`
- WHEN it is adopted
- THEN its `sensitivity` equals `default_sensitivity`, with no per-type
  raise
