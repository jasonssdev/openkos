# Delta for Ingestion

## MODIFIED Requirements

### Requirement: Derived Object Provenance and Sensitivity Inheritance

A successfully validated derived object MUST record `provenance`
referencing its originating Source concept, and MUST inherit the built
Source concept's own resolved `sensitivity` value at creation time — read
from the Source object actually staged in this run, not from
`cfg.default_sensitivity` or any other shared configuration constant. This
inheritance MUST hold even when the Source's resolved `sensitivity` differs
from the configured default (e.g. because of prior propagation or an
explicit override), proving the value is read, not assumed. WHEN the
derived object's OKF type has a configured per-type sensitivity offset
(`type-sensitivity-defaults`), the inherited Source value is a floor, not
the final value: the born `sensitivity` is
`combine_sensitivity(stamp_sensitivity, raise_by(cfg.default_sensitivity,
offset))`, so a type-defaulted object may be born strictly above the
Source's own resolved value, never below it. The `ingest` run summary MUST
carry the born-above-floor advisory (`type-sensitivity-defaults`) whenever
this raise applies to one or more staged derived objects.
(Previously: inheritance was described as unconditional equality to the
Source's resolved `sensitivity` value, with no type-dependent raise above
it.)

#### Scenario: Provenance and sensitivity inherited from the Source's own value

- GIVEN a source ingested with a configured `sensitivity` value and
  successful extraction, and no per-type sensitivity offset configured for
  the derived object's type
- WHEN `openkos ingest <path>` completes
- THEN the derived object's frontmatter `provenance` includes a reference
  to the Source concept and its `sensitivity` equals the Source's own
  `sensitivity`

#### Scenario: A type-defaulted derived object is born above the Source's value

- GIVEN a source ingested and resolved at `public`, and a per-type
  sensitivity offset configured for the derived object's OKF type (e.g.
  `Person`) that raises the workspace floor to `private`
- WHEN `openkos ingest <path>` completes
- THEN that derived object's `sensitivity` is `private`, strictly above the
  Source's own resolved `public` value, and the run summary carries the
  born-above-floor advisory naming it
