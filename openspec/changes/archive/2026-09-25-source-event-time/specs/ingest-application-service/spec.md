# Delta for Ingest Application Service

## ADDED Requirements

### Requirement: compose_source_document Accepts An Optional Event Date

`compose_source_document` MUST accept an optional `event_date` parameter
and forward it to `okf.build_source_concept` unchanged. It MUST NOT
resolve the value itself (no flag parsing, no file-name inference, no
stored-value read-back) — the caller supplies the value the `ingestion`
capability's precedence rules already resolved. WHEN `event_date` is
`None`, the generated Source concept's frontmatter MUST omit the
`event_date` key, and the rest of the generated document MUST be
byte-identical to `compose_source_document`'s output before this
parameter existed.

#### Scenario: A None event_date produces a byte-identical Source

- GIVEN a call to `compose_source_document` with `event_date=None` and
  inputs identical to a call made before this parameter existed
- WHEN the two calls' output is compared
- THEN the generated Source concept document is byte-identical, and
  neither carries an `event_date` key

#### Scenario: A given event_date reaches the generated document

- GIVEN a call to `compose_source_document` with
  `event_date=date(2026, 7, 14)`
- WHEN the Source concept is composed
- THEN its frontmatter carries `event_date: '2026-07-14'`

#### Scenario: The service performs no resolution of its own

- GIVEN the ingest application service module
- WHEN its handling of the `event_date` parameter is inspected
- THEN it contains no flag parsing, no file-name inference, and no read of
  any on-disk stored value — it only forwards the value it received

### Requirement: compose_catalog_update Preserves event_date On Marker-Only Rebuilds

`compose_catalog_update` MUST also preserve a Source's `event_date` on a
conditional rebuild triggered solely to record a new `extraction_status` or
`extraction_notice` marker. A marker-only rebuild MUST NOT drop an
`event_date` the Source already carries.

#### Scenario: A marker-only catalog rebuild keeps the resolved event_date

- GIVEN a Source whose resolved `event_date` is `2026-07-14`, and a catalog
  update that rebuilds the Source concept solely to record a new
  `extraction_status` or `extraction_notice`
- WHEN `compose_catalog_update` performs that rebuild
- THEN the rebuilt Source concept's frontmatter still carries
  `event_date: '2026-07-14'`
