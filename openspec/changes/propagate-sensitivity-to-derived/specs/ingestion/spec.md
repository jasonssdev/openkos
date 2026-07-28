# Delta for ingestion

## MODIFIED Requirements

### Requirement: Derived Object Provenance and Sensitivity Inheritance

A successfully validated derived object MUST record `provenance`
referencing its originating Source concept, and MUST inherit the built
Source concept's own resolved `sensitivity` value at creation time — read
from the Source object actually staged in this run, not from
`cfg.default_sensitivity` or any other shared configuration constant. This
inheritance MUST hold even when the Source's resolved `sensitivity` differs
from the configured default (e.g. because of prior propagation or an
explicit override), proving the value is read, not assumed.
(Previously: stated verbatim inheritance without requiring the value to be
read from the Source's own resolved field, which a shared constant applied
to both concepts independently could also satisfy.)

#### Scenario: Provenance and sensitivity inherited from the Source's own value

- GIVEN a source ingested with a configured `sensitivity` value and
  successful extraction
- WHEN `openkos ingest <path>` completes
- THEN the derived object's frontmatter `provenance` includes a reference
  to the Source concept and its `sensitivity` equals the Source's own
  `sensitivity`

#### Scenario: Inheritance tracks the Source's resolved value, not the config default

- GIVEN a source ingested where the built Source concept's resolved
  `sensitivity` differs from `cfg.default_sensitivity` (e.g. a non-default
  value was resolved for this run)
- WHEN `openkos ingest <path>` completes
- THEN every derived object's `sensitivity` equals the built Source's own
  resolved value, and would differ from the derived object's value if
  `cfg.default_sensitivity` had been used instead
