# Delta for Ingestion

## ADDED Requirements

### Requirement: Source Event Date Frontmatter Key

`ingest` MUST support an optional Source frontmatter key, `event_date`,
holding a strict `YYYY-MM-DD` calendar date naming when the recorded event
happened — distinct from `timestamp`, which continues to record ingest time
unconditionally. `event_date` is an OKF §4.1 extension key: `ingest` MUST
emit it on the generated Source concept ONLY when a value is available
(from the `--event-date` flag, file-name inference, or carry-forward on
re-ingest); a Source ingested with no such evidence MUST omit the key
entirely, producing output byte-identical to `ingest`'s behavior before
this key existed. `event_date` MUST NEVER be defaulted to ingest time or
any other derived value — "no evidence" and "unknown" MUST be represented
by the key's absence, never by a stand-in value. WHEN `ingest` writes the
key, it MUST emit a quoted ISO-8601 date string (`event_date:
'2026-07-14'`), never a bare, unquoted date scalar.

#### Scenario: No flag and no dated file name omits the key

- GIVEN an initialized workspace and a source file whose name carries no
  `YYYY-MM-DD` token, ingested with no `--event-date` flag
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter contains no `event_date`
  key, and the document is otherwise byte-identical to `ingest`'s output
  before this key existed

#### Scenario: event_date is never the ingest timestamp

- GIVEN a source ingested with no `--event-date` flag and no dated file
  name
- WHEN the generated Source concept's frontmatter is inspected
- THEN its `timestamp` reflects the ingest time as before, and no
  `event_date` key exists carrying that same or any other derived value

#### Scenario: Derived objects never carry an event date

- GIVEN a source ingested with a resolved `event_date`, whose extraction
  yields one or more derived objects
- WHEN the derived objects' frontmatter is inspected
- THEN none of them carry an `event_date` key; the value exists only on
  the Source concept, reachable from a derived object through its
  `provenance`

### Requirement: Explicit `--event-date` Ingest Flag

`openkos ingest <path>` MUST accept an optional `--event-date YYYY-MM-DD`
flag. The value MUST be validated as a strict ISO-8601 calendar date (a
real calendar date in `YYYY-MM-DD` form). WHEN the value fails that
validation, `ingest` MUST refuse with exit code `2`, a clear error on
stderr, and MUST perform no write of any kind — no raw copy, no concept
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

### Requirement: `--event-date` Is Refused For Directory Or Glob Input

`--event-date` MUST be honored only when `<src>` is a plain file. WHEN
`--event-date` is combined with a directory or a glob pattern as `<src>` —
regardless of how many files that directory or glob actually matches,
including exactly one — `ingest` MUST refuse with exit code `2`, a clear
error on stderr, and MUST perform no write of any kind for any file the
batch would otherwise have touched.

#### Scenario: A directory input with --event-date is refused

- GIVEN an initialized workspace and a directory containing one or more
  ingestable files
- WHEN `openkos ingest <dir> --event-date 2026-07-14` runs
- THEN it exits with code `2`, writes a clear error to stderr, and no file
  is copied, written, or catalogued

#### Scenario: A glob input with --event-date is refused, even matching one file

- GIVEN an initialized workspace and a glob pattern that currently matches
  exactly one file
- WHEN `openkos ingest <glob> --event-date 2026-07-14` runs
- THEN it is refused exactly as a directory input is — the refusal is
  decided by the input's shape, not by how many files it happens to match

### Requirement: File-Name Event-Date Inference

WHEN no `--event-date` flag is given, `ingest` MUST attempt to infer
`event_date` from the source file's name using a strict, fail-closed
parser. The parser MUST yield a date ONLY WHEN the file name contains
EXACTLY ONE distinct valid `YYYY-MM-DD` token — four digits, a hyphen, two
digits, a hyphen, two digits, forming a real calendar date — bounded on
both sides by a non-digit character (or the start/end of the name). Any
other case MUST yield no date: a token whose digits do not form a real
calendar date; a token immediately preceded or followed by another digit
(digit-adjacency), so the token is not cleanly delimited; or more than one
distinct valid token found in the same name. A name containing the SAME
valid token more than once MUST still count as exactly one distinct date
and MUST yield it.

#### Scenario: A single dated token yields that date

- GIVEN a source file named `call-with-maria-2026-07-14.txt`
- WHEN `openkos ingest <path>` runs with no `--event-date` flag
- THEN the generated Source concept's `event_date` is `2026-07-14`

#### Scenario: A non-ISO-ordered date token yields no date

- GIVEN a source file named `notes-03-04-2026.txt`
- WHEN `openkos ingest <path>` runs with no `--event-date` flag
- THEN no `event_date` key is written; `03-04-2026` does not match the
  `YYYY-MM-DD` token shape

#### Scenario: An invalid calendar date in the file name yields no date

- GIVEN a source file named `report-2026-13-01.txt`
- WHEN `openkos ingest <path>` runs with no `--event-date` flag
- THEN no `event_date` key is written; `2026-13-01` matches the token
  shape but is not a real calendar date

#### Scenario: A leading digit-adjacent token yields no date

- GIVEN a source file named `12026-07-14.txt`
- WHEN `openkos ingest <path>` runs with no `--event-date` flag
- THEN no `event_date` key is written; the substring `2026-07-14` is
  immediately preceded by the digit `1`, so it is not a cleanly bounded
  token

#### Scenario: A trailing digit-adjacent token yields no date

- GIVEN a source file named `2026-07-149-notes.txt`
- WHEN `openkos ingest <path>` runs with no `--event-date` flag
- THEN no `event_date` key is written; the substring `2026-07-14` is
  immediately followed by the digit `9`, so it is not a cleanly bounded
  token

#### Scenario: Two distinct dated tokens yield no date

- GIVEN a source file named `trip-2026-07-14-and-2026-08-01.txt`
- WHEN `openkos ingest <path>` runs with no `--event-date` flag
- THEN no `event_date` key is written; the name is ambiguous between two
  distinct valid dates

#### Scenario: The same dated token repeated yields that one date

- GIVEN a source file named `call-2026-07-14-copy-2026-07-14.txt`
- WHEN `openkos ingest <path>` runs with no `--event-date` flag
- THEN the generated Source concept's `event_date` is `2026-07-14`; the
  repeated token is one distinct date, not an ambiguity

### Requirement: Event-Date Precedence On First Ingest

On a source with no prior Source concept, `ingest` MUST resolve
`event_date` in this precedence: the `--event-date` flag, when given and
valid; otherwise the file-name inference above; otherwise unset (the key
is omitted).

#### Scenario: The flag wins over a dated file name

- GIVEN a source file named `call-2026-07-14.txt`, ingested with
  `--event-date 2026-08-01`
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `event_date` is `2026-08-01`, not
  `2026-07-14`

#### Scenario: The file name is used when no flag is given

- GIVEN a source file named `call-2026-07-14.txt`, ingested with no
  `--event-date` flag
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's `event_date` is `2026-07-14`

#### Scenario: Neither source leaves the key unset

- GIVEN a source file with no dated name, ingested with no `--event-date`
  flag
- WHEN `openkos ingest <path>` completes
- THEN the generated Source concept's frontmatter carries no `event_date`
  key

### Requirement: Re-Ingest Event-Date Carry-Forward

On a re-ingest of a Source that already exists, `ingest` MUST resolve
`event_date` in this precedence: the `--event-date` flag, when given and
valid; otherwise the value already stored on the existing Source's
frontmatter; otherwise file-name inference; otherwise unset. A re-ingest
with no flag MUST NEVER clobber a stored `event_date` with a different or
absent value, and MUST NEVER invent one from ingest time. WHEN the on-disk
stored value was written as an unquoted YAML date scalar and is read back
as a native date object rather than a string, `ingest` MUST recognize it
as the same stored value the precedence chain compares against, exactly as
if it had been read back as a string.

WHEN an explicit `--event-date` flag differs from the currently stored
value, `ingest` MUST overwrite the stored value with the flag's value.

WHEN the stored `event_date` value cannot be read as a valid calendar date
— for example a hand-edited value, a `datetime` instead of a `date`, or any
other malformed value — `ingest` MUST treat it as absent for the
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

#### Scenario: A stored date read back as a YAML date object is still recognized

- GIVEN a Source whose on-disk `event_date: 2026-07-14` was parsed by
  PyYAML into a native `date` object rather than a string
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` remains `2026-07-14`, carried
  forward correctly regardless of the on-disk value's parsed type

#### Scenario: A legacy Source with no stored value falls back to the file name

- GIVEN a Source previously ingested before this feature existed, carrying
  no `event_date`, whose raw file name carries a single valid dated token
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's `event_date` is inferred from the file
  name

#### Scenario: A legacy Source with no stored value and no dated file name stays unset

- GIVEN a Source previously ingested before this feature existed, carrying
  no `event_date`, whose raw file name carries no dated token
- WHEN `openkos ingest <path>` re-ingests the same source with no
  `--event-date` flag
- THEN the regenerated Source's frontmatter carries no `event_date` key; it
  is never defaulted to the re-ingest's timestamp

#### Scenario: A malformed stored value is not carried forward and warns

- GIVEN a Source whose on-disk `event_date` value is malformed — for
  example a `datetime` value, or a string that is not a real calendar date
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

### Requirement: Converged Re-Ingest Date-Only Rewrite

WHEN a re-ingest's Source is otherwise unchanged, already extracted, and
carries an `origin_key` — the case that ordinarily converges and writes
nothing — BUT the resolved `event_date` differs from the currently stored
value, `ingest` MUST rewrite the Source concept only. It MUST NOT invoke
extraction (no LLM call is made), MUST NOT create, modify, or remove any
derived object, and MUST carry forward the Source's existing extraction
markers (`extraction_status` and `extraction_notice`) unchanged. This
rewrite MUST go through the same preview, confirm gate, drift guard, and
autocommit as any other regenerate. WHEN the resolved `event_date` equals
the currently stored value, the run MUST continue to converge and write
nothing, exactly as `ingest` behaved before this feature existed.

#### Scenario: A differing flag on a converged Source rewrites it with no extraction

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and is stored with `event_date: '2026-07-10'`
- WHEN `openkos ingest <path> --event-date 2026-07-14` re-ingests it
- THEN the Source concept is rewritten with `event_date: '2026-07-14'`, no
  LLM call is made, no derived object is created, modified, or removed, and
  the Source's existing extraction markers are carried forward unchanged

#### Scenario: A pre-feature Source with a dated file name gains the date on plain re-ingest

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, carries no stored `event_date`, and whose raw file name
  carries a single valid dated token
- WHEN `openkos ingest <path>` re-ingests it with no `--event-date` flag
- THEN the Source concept is rewritten with the inferred `event_date`, no
  LLM call is made, and no derived object changes

#### Scenario: A resolved date equal to the stored value writes nothing

- GIVEN a Source that is unchanged, already extracted, carries an
  `origin_key`, and is stored with `event_date: '2026-07-14'`
- WHEN `openkos ingest <path> --event-date 2026-07-14` re-ingests it (or a
  plain re-ingest resolves the same stored value)
- THEN convergence is preserved: nothing is written, exactly as before this
  feature existed

#### Scenario: The date-only rewrite is idempotent

- GIVEN a converged Source that was just rewritten by a date-only rewrite
- WHEN the same `openkos ingest <path> --event-date <same value>` command
  runs again
- THEN the second run resolves the value as `kept`, `changed` is false, and
  convergence writes nothing

### Requirement: Event-Date Origin Disclosure Line

WHEN a re-ingest or fresh ingest records an `event_date` on the generated
Source concept, `ingest` MUST print exactly one line reporting the
recorded value and its origin — `flag`, `file name`, or `kept` (the value
was already stored and carried forward unchanged). WHEN an explicit
`--event-date` flag overwrites a differing stored value on re-ingest, that
line MUST name both the old and the new value. WHEN no `event_date` is
recorded at all, `ingest` MUST print no such line.

#### Scenario: The line names the flag origin

- GIVEN a fresh ingest with `--event-date 2026-07-14`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming `2026-07-14` and `flag` as its origin

#### Scenario: The line names the file-name origin

- GIVEN a fresh ingest with no flag and a source file named
  `call-2026-07-14.txt`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming `2026-07-14` and `file name` as its
  origin

#### Scenario: The line names a carried-forward value as kept

- GIVEN a re-ingest with no flag, whose stored `event_date` is
  `2026-07-14`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming `2026-07-14` and `kept` as its origin

#### Scenario: An overwrite names both the old and new value

- GIVEN a re-ingest whose stored `event_date` is `2026-07-14`, run with
  `--event-date 2026-08-01`
- WHEN `openkos ingest <path>` completes
- THEN one line is printed naming the change from `2026-07-14` to
  `2026-08-01`

#### Scenario: No line is printed when no event date is recorded

- GIVEN an ingest with no flag and no dated file name, of a source with no
  stored `event_date`
- WHEN `openkos ingest <path>` completes
- THEN no line reporting an `event_date` is printed
