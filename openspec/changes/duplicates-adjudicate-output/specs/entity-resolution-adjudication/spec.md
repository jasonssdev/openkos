# Delta for Entity-Resolution Adjudication

## ADDED Requirements

### Requirement: Leading Verdict Tally Line Over Full Results

When `openkos adjudicate` has one or more results, the FIRST line of stdout
MUST be `adjudicated N: x SAME, y DIFFERENT`, where `N`, `x`, and `y` are
counted over the FULL `results` set returned by `adjudicate_candidates`,
independent of the `--same-only` display filter. A `, z UNCERTAIN` segment
MUST be appended ONLY when `z > 0`; it MUST be omitted entirely when
`z == 0`.

#### Scenario: Mixed SAME/DIFFERENT, no UNCERTAIN

- GIVEN adjudication results with 2 SAME and 1 DIFFERENT verdicts and zero
  UNCERTAIN
- WHEN `adjudicate` runs
- THEN the first stdout line is `adjudicated 3: 2 SAME, 1 DIFFERENT`

#### Scenario: Mixed results with UNCERTAIN present

- GIVEN adjudication results with 2 SAME, 1 DIFFERENT, and 1 UNCERTAIN
- WHEN `adjudicate` runs
- THEN the first stdout line is
  `adjudicated 4: 2 SAME, 1 DIFFERENT, 1 UNCERTAIN`

#### Scenario: Zero UNCERTAIN omits the segment

- GIVEN adjudication results containing zero UNCERTAIN verdicts
- WHEN `adjudicate` runs
- THEN the tally line contains no `UNCERTAIN` segment

#### Scenario: All-SAME results

- GIVEN adjudication results where every verdict is SAME
- WHEN `adjudicate` runs
- THEN the first stdout line is `adjudicated N: N SAME, 0 DIFFERENT` for the
  matching count `N`

#### Scenario: All-DIFFERENT results

- GIVEN adjudication results where every verdict is DIFFERENT
- WHEN `adjudicate` runs
- THEN the first stdout line is `adjudicated N: 0 SAME, N DIFFERENT` for the
  matching count `N`

#### Scenario: `--same-only` filters display, not the tally count

- GIVEN adjudication results with a mix of SAME, DIFFERENT, and UNCERTAIN
- WHEN `adjudicate --same-only` runs
- THEN the tally line still reports counts over the full results set, while
  only SAME verdicts appear in the printed detail below it

### Requirement: One-Time Verdict-Column Legend Line

When at least one result is printed, `adjudicate` MUST print exactly one
legend line explaining the per-group verdict/confidence/rationale columns,
placed after the tally and BEFORE the results loop. The legend MUST NOT
repeat per group.

#### Scenario: Legend appears once regardless of result count

- GIVEN four adjudication results
- WHEN `adjudicate` runs
- THEN the legend line appears exactly once, before the first result's
  detail lines

### Requirement: Trailing Next-Action Hint

When at least one result is printed, the LAST line of `adjudicate` stdout
MUST be `Next: openkos merge <survivor> <absorbed>`.

#### Scenario: Hint is the final line

- GIVEN at least one adjudication result is displayed
- WHEN `adjudicate` runs
- THEN the last stdout line is `Next: openkos merge <survivor> <absorbed>`

### Requirement: Empty And Same-Only-Empty States Stay Single-Line

WHEN there are no candidate groups to adjudicate, OR `--same-only` filters
the display down to zero SAME verdicts, stdout MUST contain ONLY the
existing sole message for that path — no tally, no legend, and no `Next:`
hint. The full-`results` tally still exists conceptually but MUST NOT be
printed on these paths.

#### Scenario: No candidates to adjudicate

- GIVEN a bundle with no candidate groups
- WHEN `adjudicate` runs
- THEN stdout is exactly the existing "no candidates" message with no
  additional lines

#### Scenario: `--same-only` filters every result out

- GIVEN adjudication results containing zero SAME verdicts
- WHEN `adjudicate --same-only` runs
- THEN stdout is exactly the existing
  `"No SAME-verdict candidates to display (--same-only)."` message with no
  additional lines

### Requirement: Reusable Verdict-Tally Formatting Helper

The system MUST provide a pure formatting helper, sibling to
`_format_type_tally`, that renders the verdict tally line
(SAME/DIFFERENT/UNCERTAIN counts, UNCERTAIN segment omitted when zero) from
the per-verdict counts, reusing `_plural`. Given all-zero counts it MUST
return `""`. Its argument shape is an implementation detail; only the
returned string and the empty-on-zero contract are observable.

#### Scenario: Zero counts yield empty string

- GIVEN the helper is called with all-zero verdict counts
- WHEN it runs
- THEN it returns `""`

#### Scenario: Populated counts yield the tally line

- GIVEN the helper is called with counts for SAME, DIFFERENT, and UNCERTAIN
- WHEN it runs
- THEN it returns the `adjudicated N: x SAME, y DIFFERENT, z UNCERTAIN` line
  matching those counts, omitting the UNCERTAIN segment when its count is 0

### Requirement: Existing Detail Lines Stay Byte-Identical

All per-result detail lines (verdict, confidence, rationale) emitted by
`adjudicate` before this change MUST remain byte-identical after adding the
tally, legend, and hint lines.

#### Scenario: Pre-existing substring assertions still pass

- GIVEN any pre-existing CliRunner test asserting a per-result detail
  substring on `adjudicate` output
- WHEN `adjudicate` runs after this change
- THEN that substring is still present, unchanged
