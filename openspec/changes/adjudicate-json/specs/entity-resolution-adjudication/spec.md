# Delta for Entity-Resolution Adjudication

## ADDED Requirements

### Requirement: Machine-Readable `--json` Output Mode

`openkos adjudicate` MUST accept a `--json` flag. When set, on the SUCCESS
path, stdout MUST be a single valid JSON array, one object per entry in the
full `results` set (independent of `--same-only`), with EXACTLY these fields
per object: `member_ids` (list of strings, already sorted), `okf_type`
(string), `tier` (`"HIGH"` or `"LOW"`), `verdict` (`"SAME"`, `"DIFFERENT"`, or
`"UNCERTAIN"`), `rationale` (string). The object MUST NOT contain a
`confidence` field or any survivor/absorbed field.

#### Scenario: Exact field set, no confidence

- GIVEN adjudication results with mixed verdicts
- WHEN `adjudicate --json` runs
- THEN each parsed JSON object has exactly the keys `member_ids`, `okf_type`,
  `tier`, `verdict`, `rationale`
- AND no object contains a `confidence` key

#### Scenario: Example mixed-verdict payload

- GIVEN two candidate groups, one SAME and one DIFFERENT
- WHEN `adjudicate --json` runs
- THEN stdout parses to:
  ```json
  [
    {
      "member_ids": ["concept-a", "concept-b"],
      "okf_type": "person",
      "tier": "HIGH",
      "verdict": "SAME",
      "rationale": "Same individual; identical canonical name and role."
    },
    {
      "member_ids": ["concept-c", "concept-d"],
      "okf_type": "org",
      "tier": "LOW",
      "verdict": "DIFFERENT",
      "rationale": "Distinct organizations despite similar names."
    }
  ]
  ```

### Requirement: `--json` Fully Suppresses Human Output

When `--json` is passed, stdout MUST contain ONLY the JSON payload — no
tally line, legend line, per-group detail lines, or `Next:` hint. The entire
stdout content MUST parse cleanly via `json.loads`.

#### Scenario: No human-output substrings under `--json`

- GIVEN adjudication results with at least one verdict
- WHEN `adjudicate --json` runs
- THEN stdout contains none of: `"adjudicated "`, the legend line, or
  `"Next: openkos merge"`
- AND `json.loads(stdout)` succeeds

### Requirement: `--same-only` Composes With `--json`

`adjudicate --json` MUST include every result by default, regardless of
verdict. `adjudicate --json --same-only` MUST filter the emitted array to
objects where `verdict == "SAME"` only.

#### Scenario: `--json` alone includes all verdicts

- GIVEN results with SAME, DIFFERENT, and UNCERTAIN verdicts
- WHEN `adjudicate --json` runs
- THEN the parsed array contains one object per result, all verdicts present

#### Scenario: `--json --same-only` filters to SAME

- GIVEN results with SAME, DIFFERENT, and UNCERTAIN verdicts
- WHEN `adjudicate --json --same-only` runs
- THEN the parsed array contains only objects with `"verdict": "SAME"`

### Requirement: Empty State Emits Valid Empty Array Under `--json`

WHEN there are no candidate groups, OR `--same-only` filters every result out,
`adjudicate --json` MUST emit stdout that is a valid empty JSON array `[]`,
NOT the plain-text "no candidates" message used in the non-JSON path.

#### Scenario: No candidates, `--json`

- GIVEN a bundle with no candidate groups
- WHEN `adjudicate --json` runs
- THEN `json.loads(stdout) == []`

#### Scenario: `--same-only` filters all results out, `--json`

- GIVEN results containing zero SAME verdicts
- WHEN `adjudicate --json --same-only` runs
- THEN `json.loads(stdout) == []`

### Requirement: Deterministic, Pretty-Printed JSON

The JSON array MUST preserve the order of the `results` set (no re-sorting),
with `member_ids` already sorted, and MUST be pretty-printed with `indent=2`.
Identical input MUST yield byte-identical stdout across runs.

#### Scenario: Stable ordering across runs

- GIVEN the same fixture bundle and model responses
- WHEN `adjudicate --json` runs twice
- THEN both stdout outputs are byte-identical and array order matches
  `results` order

#### Scenario: Output is indented JSON

- GIVEN at least one adjudication result
- WHEN `adjudicate --json` runs
- THEN stdout parses as JSON and spans multiple indented lines (not a single
  compact line)

### Requirement: Error Paths Unaffected By `--json`

The Ollama-unavailable, model-not-found, and generic-error handlers MUST
remain unchanged when `--json` is passed: the error message MUST go to
stderr, the process MUST exit with code 1, and stdout MUST NOT contain any
JSON (partial or otherwise).

#### Scenario: Ollama unavailable with `--json`

- GIVEN Ollama is unreachable
- WHEN `adjudicate --json` runs
- THEN stderr contains the existing unavailability message
- AND the exit code is 1
- AND stdout does not parse as JSON and contains no partial payload

### Requirement: Non-JSON Output Stays Byte-Identical

Without `--json`, `adjudicate` output (tally, legend, per-group detail,
`Next:` hint, and empty-state messages) MUST remain byte-identical to its
behavior before this change.

#### Scenario: Human output unchanged when `--json` is absent

- GIVEN any pre-existing CliRunner assertion on `adjudicate` stdout without
  `--json`
- WHEN `adjudicate` runs after this change
- THEN that assertion still passes unchanged
