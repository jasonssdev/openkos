# Delta for Ingestion

## ADDED Requirements

### Requirement: Per-Type Derived-Object Tally Summary

After a successful `openkos ingest <path>` run that writes at least one
derived object, the command MUST print one additional summary line to
STDOUT of the form `extracted {N} objects — {count} {Type}[, {count}
{Type}...]`, where `N` is the total count of derived objects written and
"objects" is pluralized via the existing `_plural` helper (`extracted 1
object — ...` for `N == 1`). Only types with `count > 0` MUST appear; each
type MUST be rendered using its canonical `CLASSIFIABLE_TYPES` string, and
types MUST be ordered by canonical type-registry order, NOT insertion order
or alphabetical order, so identical input always renders the same string.
WHEN zero derived objects are written (Source-only degrade), this line MUST
NOT be emitted. This line is strictly additive: it MUST NOT replace, alter,
or reorder any existing stdout line, and MUST NOT change any exit code.

#### Scenario: Zero derived objects — no tally line

- GIVEN `openkos ingest <path>` completes with zero derived objects written
  (Source-only degrade)
- WHEN the command's stdout is inspected
- THEN no tally line matching `extracted ... objects` appears

#### Scenario: Single object, singular wording

- GIVEN `openkos ingest <path>` completes writing exactly one derived
  object of type `Concept`
- WHEN the command's stdout is inspected
- THEN it contains the line `extracted 1 object — 1 Concept`

#### Scenario: Multiple objects, one type

- GIVEN `openkos ingest <path>` completes writing three derived objects, all
  of type `Entity`
- WHEN the command's stdout is inspected
- THEN it contains the line `extracted 3 objects — 3 Entity`

#### Scenario: Multiple objects, mixed types in canonical order

- GIVEN `openkos ingest <path>` completes writing derived objects of types
  `Person`, `Concept`, and `Event` (in that write/reply order), and the
  canonical registry orders these as `Concept`, `Event`, `Person`
- WHEN the command's stdout is inspected
- THEN the tally line lists counts in canonical registry order (`Concept`,
  then `Event`, then `Person`), regardless of write or reply order

### Requirement: Blocking-Extraction Activity Indicator

While the blocking `extract_concept` LLM call runs during `ingest`, the
system MUST display a live, indeterminate activity indicator (spinner) on
STDERR only. The indicator MUST NOT report a percentage, ETA, or any other
determinate progress signal. On a non-TTY stream (e.g. piped or captured
stdout, such as under `CliRunner`), STDOUT MUST remain byte-clean of any
spinner control characters or partial-line artifacts, and the exit code MUST
be unchanged from before this indicator was added. The indicator MUST be
cleared whether `extract_concept` returns successfully OR raises
`OllamaError`, leaving no leftover partial line on either path.

#### Scenario: Spinner is stderr-only and stdout stays clean

- GIVEN `openkos ingest <path>` running with stdout captured/piped
  (non-TTY)
- WHEN the blocking `extract_concept` call runs
- THEN stdout contains no spinner control characters or partial lines, and
  the command's exit code is unchanged from behavior before this indicator

#### Scenario: Spinner clears on extraction success

- GIVEN `extract_concept` returns successfully
- WHEN the call completes
- THEN the activity indicator is cleared with no leftover partial line

#### Scenario: Spinner clears on OllamaError

- GIVEN `extract_concept` raises `OllamaError`
- WHEN the error is raised
- THEN the activity indicator is cleared with no leftover partial line, and
  `ingest` proceeds to its existing Source-only degrade behavior

### Requirement: Reusable Type-Tally Formatting Helper

The system MUST provide a helper `_format_type_tally(counts: dict[str,
int]) -> str` whose contract depends only on its `dict[str, int]` input
(type-name → count), decoupled from any `ingest`-specific internals (e.g.
`derived_plans`), so other commands MAY reuse it. Given a non-empty dict, it
MUST render `extracted {N} objects — {count} {Type}[, {count} {Type}...]`
per the tally requirement above (pluralization, canonical-registry
ordering, only `count > 0` entries). Given an empty dict, it MUST return an
empty string (`""`), signaling "no line to print" to the caller.

#### Scenario: Empty dict yields empty string

- GIVEN `_format_type_tally({})`
- WHEN the helper is called
- THEN it returns `""`

#### Scenario: Single-entry dict yields singular line

- GIVEN `_format_type_tally({"Concept": 1})`
- WHEN the helper is called
- THEN it returns `"extracted 1 object — 1 Concept"`

#### Scenario: Multi-entry dict is ordered by canonical registry, not insertion order

- GIVEN `_format_type_tally({"Person": 2, "Concept": 1})` (insertion order:
  `Person` before `Concept`), where canonical registry order places
  `Concept` before `Person`
- WHEN the helper is called
- THEN the returned string lists `Concept` before `Person`, regardless of
  the dict's insertion order
