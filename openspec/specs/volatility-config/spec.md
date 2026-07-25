# Volatility Config Specification

## Purpose

`volatility-config` is the write layer that lets a human act on a
`suggest-volatility` recommendation: the CLI verb `set-volatility` sets one
entry in `type_tiers:` inside `openkos.yaml` via comment-safe text surgery
(no YAML round-trip), so existing comments and formatting survive. It is the
only writer of `type_tiers:`; the read semantics of `type_tiers:` itself stay
owned by `concept-volatility`.

## Requirements

### Requirement: `set-volatility` Command Shape

The system MUST provide a CLI verb `openkos set-volatility <ConceptType>
<tier>` that, on success, sets `type_tiers[<ConceptType>] = <tier>` in
`openkos.yaml`.

#### Scenario: Successful set updates the config value

- GIVEN a workspace with `openkos.yaml`
- WHEN `set-volatility Person volatile` runs and the confirm gate is accepted
- THEN `type_tiers: {Person: volatile}` is present in `openkos.yaml` after
  the run

### Requirement: Strict Tier Validation

`<tier>` MUST be exactly one of `static`, `slow`, `volatile`. Any other value
MUST fail with a clear stderr message and non-zero exit, and MUST NOT write
to `openkos.yaml`.

#### Scenario: Invalid tier value is rejected

- GIVEN `set-volatility Person bogus` where `bogus` is not a valid tier
- WHEN the command runs
- THEN stderr states the value is invalid, the exit code is non-zero, and
  `openkos.yaml` is unchanged

### Requirement: Strict ConceptType Validation

`<ConceptType>` MUST exact-match, case-sensitive, one of the 10 PascalCase
`REGISTRY` type names (including `Source`). Any other value MUST fail with a
clear stderr message listing the valid type names, plus non-zero exit, and
MUST NOT write to `openkos.yaml`.

#### Scenario: Unknown ConceptType is rejected

- GIVEN `set-volatility Widget slow` where `Widget` is not a `REGISTRY` type
- WHEN the command runs
- THEN stderr lists the valid `REGISTRY` type names, the exit code is
  non-zero, and `openkos.yaml` is unchanged

### Requirement: Comment-Safe `type_tiers:` Editing

Editing `type_tiers:` MUST preserve all other lines, comments, and
formatting in `openkos.yaml` byte-for-byte, whether the target entry already
exists, is new under an existing block, or the block itself is absent or
fully commented out (the shipped template state).

#### Scenario: Updating an existing entry preserves surrounding comments

- GIVEN `openkos.yaml` has `type_tiers: {Person: slow}` with comments
  elsewhere in the file
- WHEN `set-volatility Person volatile` runs and is confirmed
- THEN `Person` now maps to `volatile` and every other line, including all
  comments, is byte-identical to before

#### Scenario: Adding a new type under an existing block

- GIVEN `openkos.yaml` has `type_tiers: {Person: slow}` and no `Procedure`
  entry
- WHEN `set-volatility Procedure volatile` runs and is confirmed
- THEN a `Procedure: volatile` entry is added under `type_tiers:` and no
  other content or comments are disturbed

#### Scenario: Block absent or fully commented is created fresh

- GIVEN `openkos.yaml` has no `type_tiers:` key, or it is entirely commented
  out (shipped template state)
- WHEN `set-volatility Person volatile` runs and is confirmed
- THEN a proper `type_tiers:` block containing the one entry is added, and
  the rest of the file is unchanged

### Requirement: Fail-Closed On Unparseable Config Shape

WHEN the existing `type_tiers:` shape cannot be confidently edited by text
surgery — including inline flow-mapping (e.g. `type_tiers: {Person:
volatile}` written as flow style the editor does not recognize), malformed
or duplicate blocks, or tab-indented content — the verb MUST refuse: clear
stderr error, non-zero exit, and `openkos.yaml` left byte-identical to
before the run.

#### Scenario: Inline flow-mapping shape is refused

- GIVEN an unrecognized inline flow-mapping form of `type_tiers:`
- WHEN `set-volatility` runs
- THEN stderr reports the edit was refused, the exit code is non-zero, and
  `openkos.yaml` is byte-identical to before

#### Scenario: Malformed or duplicate block is refused

- GIVEN `openkos.yaml` has a duplicated or structurally malformed
  `type_tiers:` block
- WHEN `set-volatility` runs
- THEN stderr reports the edit was refused, the exit code is non-zero, and
  `openkos.yaml` is byte-identical to before

#### Scenario: Tab-indented content is refused

- GIVEN `openkos.yaml` has tab-indented lines near `type_tiers:`
- WHEN `set-volatility` runs
- THEN stderr reports the edit was refused, the exit code is non-zero, and
  `openkos.yaml` is byte-identical to before

### Requirement: Preview And Confirm Gate

Before writing, the verb MUST print a preview line `<ConceptType>:
<current-or-default-tier> -> <new-tier>`, then apply the same confirm gate
used by other mutating verbs (`--auto` / non-interactive precedence).
Declining MUST result in no write.

#### Scenario: Confirming the preview writes the change

- GIVEN a valid `set-volatility` invocation and confirm input `y`
- WHEN the command runs
- THEN the preview line is printed before the prompt, and `openkos.yaml` is
  written after confirmation

#### Scenario: Declining the preview performs no write

- GIVEN a valid `set-volatility` invocation and confirm input `n` (or
  equivalent decline)
- WHEN the command runs
- THEN no write occurs and `openkos.yaml` is unchanged

### Requirement: Idempotent No-Op

WHEN `<ConceptType>` already has `<tier>` as its effective `type_tiers`
value, the verb MUST print a no-op message, perform no write, create no
commit, and exit 0.

#### Scenario: Re-setting the same tier is a no-op

- GIVEN `type_tiers: {Person: volatile}` already set
- WHEN `set-volatility Person volatile` runs
- THEN a no-op message is printed, `openkos.yaml` is unchanged, no commit is
  created, and the exit code is 0

### Requirement: Auto-Commit On Successful Write

A successful write MUST auto-commit `openkos.yaml`, consistent with the
mutating-verb convention used elsewhere in the CLI.

#### Scenario: Successful write creates a commit

- GIVEN a valid `set-volatility` invocation confirmed with `y`
- WHEN the write completes
- THEN a new commit exists covering the `openkos.yaml` change
