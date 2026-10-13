# Delta for ingestion

## MODIFIED Requirements

### Requirement: Source Event Date Frontmatter Key

`ingest` MUST support an optional Source frontmatter key, `event_date`,
holding a strict `YYYY-MM-DD` calendar date, or a naive local date-time
`YYYY-MM-DDTHH:MM` or `YYYY-MM-DDTHH:MM:SS`, naming when the recorded event
happened — distinct from `generated.at` (or a legacy `timestamp` on a
pre-v0.2 or unmigrated document), which continues to record when the
Source's content was last generated, unconditionally set at ingest time.
`event_date` is an OKF §4.1 extension key: `ingest` MUST emit it on the
generated Source concept ONLY when a value is available (from the
`--event-date` flag, file-name inference, or carry-forward on re-ingest); a
Source ingested with no such evidence MUST omit the key entirely, producing
the same document as if `event_date` were not a supported key.
`event_date` MUST NEVER be defaulted to ingest time or any other derived
value — "no evidence" and "unknown" MUST be represented by the key's
absence, never by a stand-in value. WHEN `ingest` writes the key, it MUST
emit a quoted ISO-8601 string (`event_date: '2026-07-14'` or
`event_date: '2026-07-14T10:00'`), never a bare, unquoted date scalar. A
date-time MUST be written as `YYYY-MM-DDTHH:MM`, with `:SS` appended only
when the seconds are non-zero, so one instant has exactly one text. A
date-only value MUST be written exactly as before. A date-time carries no
offset: it is the local wall-clock time of the event, and an offset, `Z`,
fractional seconds, or a space separator MUST NOT be accepted.

#### Scenario: No flag and no dated file name omits the key

- GIVEN an initialized workspace and a source file whose name carries no
  `YYYY-MM-DD` token, ingested with no `--event-date` flag
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter contains no `event_date`
  key, and the document is otherwise identical to what `ingest` writes when
  `event_date` is unsupported

#### Scenario: event_date is never the ingest timestamp

- GIVEN a source ingested with no `--event-date` flag and no dated file
  name
- WHEN the generated Source concept's frontmatter is inspected
- THEN its `generated.at` reflects the ingest time, and no
  `event_date` key exists carrying that same or any other derived value

#### Scenario: Derived objects never carry an event date

- GIVEN a source ingested with a resolved `event_date`, whose extraction
  yields one or more derived objects
- WHEN the derived objects' frontmatter is inspected
- THEN none of them carry an `event_date` key; the value exists only on
  the Source concept, reachable from a derived object through its
  `provenance`

#### Scenario: A date-time is written in its canonical form

- GIVEN an initialized workspace and a plain file at `<path>`
- WHEN `openkos ingest <path> --event-date 2026-07-14T10:00:00` completes
- THEN the generated Source concept's frontmatter carries
  `event_date: '2026-07-14T10:00'`

#### Scenario: Non-zero seconds are kept

- GIVEN an initialized workspace and a plain file at `<path>`
- WHEN `openkos ingest <path> --event-date 2026-07-14T10:00:30` completes
- THEN the generated Source concept's frontmatter carries
  `event_date: '2026-07-14T10:00:30'`

### Requirement: Explicit `--event-date` Ingest Flag

`openkos ingest <path>` MUST accept an optional `--event-date` flag whose
value is `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM` or `YYYY-MM-DDTHH:MM:SS`. The
value MUST be validated as a strict ISO-8601 calendar date or naive local
date-time (a real calendar date, and a time of day with no offset). An
offset or `Z`, fractional seconds, a space separator, and week or ordinal
dates MUST be refused. WHEN the value fails that
validation, `ingest` MUST refuse with exit code `2`, a clear error on
stderr naming the accepted shapes, and MUST perform no write of any kind — no raw copy, no concept
document, no catalog or log change — before this validation runs.

#### Scenario: A valid flag value is accepted

- GIVEN an initialized workspace and a plain file at `<path>`
- WHEN `openkos ingest <path> --event-date 2026-07-14` completes
- THEN the generated Source concept's frontmatter carries
  `event_date: '2026-07-14'`

#### Scenario: An invalid calendar date is refused before any write

- GIVEN an initialized workspace and a plain file at `<path>`
- WHEN `openkos ingest <path> --event-date 2026-13-01` runs
- THEN it exits with code `2`, writes a clear error to stderr, and no raw
  copy, concept document, or catalog/log change is made

#### Scenario: A malformed value is refused before any write

- GIVEN an initialized workspace and a plain file at `<path>`
- WHEN `openkos ingest <path> --event-date not-a-date` runs
- THEN it exits with code `2` and writes nothing

#### Scenario: A naive date-time is accepted

- GIVEN an initialized workspace and a plain file at `<path>`
- WHEN `openkos ingest <path> --event-date 2026-07-14T10:00` completes
- THEN the generated Source concept's frontmatter carries
  `event_date: '2026-07-14T10:00'`

#### Scenario: An offset is refused before any write

- GIVEN an initialized workspace and a plain file at `<path>`
- WHEN `openkos ingest <path> --event-date 2026-07-14T10:00Z` runs, or with
  `+02:00`, a fractional second, or a space instead of `T`
- THEN it exits with code `2`, the error names the accepted shapes, and
  nothing is written

### Requirement: Re-Ingest Event-Date Carry-Forward

On a re-ingest of a Source that already exists, `ingest` MUST resolve
`event_date` in this precedence: the `--event-date` flag, when given and
valid; otherwise the value already stored on the existing Source's
frontmatter; otherwise a valid `date:` key lifted from the source's
incoming frontmatter; otherwise file-name inference; otherwise unset. An
incoming `created:` key, or any other frontmatter date-like key, MUST NOT
be consulted for this precedence. A re-ingest with no flag MUST NEVER
clobber a stored `event_date` with a different or absent value, and MUST
NEVER invent one from ingest time. WHEN the on-disk stored value was
written as an unquoted YAML date scalar and is read back as a native date
or naive date-time object rather than a string, `ingest` MUST recognize it as the same
stored value the precedence chain compares against, exactly as if it had
been read back as a string.

WHEN an explicit `--event-date` flag differs from the currently stored
value, `ingest` MUST overwrite the stored value with the flag's value.

WHEN the stored `event_date` value cannot be read as a valid calendar date
or naive local date-time — for example a hand-edited value, a date-time with
an offset or fractional seconds, or any other malformed value — `ingest` MUST treat it as absent for the
precedence chain above: it MUST NEVER be carried forward as-is. `ingest`
MUST print exactly one warning to stderr naming the malformed value and
the Source it was found in, and MUST NOT fail the run.

#### Scenario: Re-ingest with no flag keeps the stored value

- GIVEN a Source previously ingested with `event_date: 2026-07-14`
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` remains `2026-07-14`

#### Scenario: A differing flag overwrites the stored value

- GIVEN a Source previously ingested with `event_date: 2026-07-14`
- WHEN `openkos ingest <path> --event-date 2026-08-01` re-ingests the same
  source
- THEN the regenerated Source's `event_date` is `2026-08-01`

#### Scenario: A stored value wins over a frontmatter date

- GIVEN a Source previously ingested with `event_date: 2026-07-14`, and a
  re-ingest whose incoming frontmatter now carries `date: 2026-09-01`
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` remains `2026-07-14`,
  unaffected by the frontmatter `date:` value

#### Scenario: A frontmatter date fills a Source with no stored value, ahead of the file name

- GIVEN a Source with no stored `event_date`, whose raw file name carries
  the dated token `2026-01-01`, and whose incoming frontmatter now carries
  `date: 2026-07-14`
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` is `2026-07-14`, not the file
  name's `2026-01-01`

#### Scenario: A stored date read back as a YAML date object is still recognized

- GIVEN a Source whose on-disk `event_date: 2026-07-14` was parsed by
  PyYAML into a native `date` object rather than a string
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` remains `2026-07-14`, carried
  forward correctly regardless of the on-disk value's parsed type

#### Scenario: A legacy Source with no stored value falls back to the file name

- GIVEN a legacy Source ingested without frontmatter recording, carrying
  no `event_date`, whose raw file name carries a single valid dated token
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` is inferred from the file
  name

#### Scenario: A legacy Source with no stored value and no dated file name stays unset

- GIVEN a legacy Source ingested without frontmatter recording, carrying
  no `event_date`, whose raw file name carries no dated token
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's frontmatter carries no `event_date` key; it
  is never defaulted to the re-ingest's timestamp

#### Scenario: A malformed stored value is not carried forward and warns

- GIVEN a Source whose on-disk `event_date` value is malformed — for
  example a date-time with an offset, or a string that is not a real
  calendar date
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag and no dated file name
- THEN the malformed value is not carried forward, exactly one warning is
  printed to stderr naming the malformed value and the Source, and the run
  completes without failing

#### Scenario: A malformed stored value is filled by the flag or the file name

- GIVEN a Source whose on-disk `event_date` value is malformed
- WHEN `openkos ingest <path>` re-ingests the same source with a valid
  `--event-date` flag, or with no flag but a dated file name
- THEN the malformed value is treated as absent, the resolved value (the
  flag's or the inferred one) fills the gap, and the same stderr warning is
  printed

#### Scenario: A stored date-time is carried forward

- GIVEN a Source previously ingested with `event_date: '2026-07-14T10:00'`
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` remains `2026-07-14T10:00`

#### Scenario: Restating the same instant at another precision is not a change

- GIVEN a Source previously ingested with `event_date: '2026-07-14T10:00'`
- WHEN `openkos ingest <path> --event-date 2026-07-14T10:00:00` re-ingests it
- THEN the resolved value is unchanged and the Source is not rewritten for
  it
