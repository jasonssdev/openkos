# Decision Revision Detection Specification (delta)

## ADDED Requirements

### Requirement: `revisions --json` Emits One Machine-Readable Object

`openkos revisions` MUST accept `--json`. With it, stdout MUST carry exactly
one JSON object (schema version `1`) and nothing else; every diagnostic, the
experimental notice, the cost gate and the confirmation prompt MUST stay on
stderr. A refusal (outside a workspace, an unreadable workspace, no `--auto`
without a TTY) and a backend failure before any verdict MUST keep exit `1`
with an empty stdout. Without `--json`, stdout, stderr and exit codes MUST be
byte-identical to a build without the flag. The flag MUST NOT change
candidate selection, judging, persistence or ordering.

The object's fields, in order:

- `schema_version` (`1`), `openkos_version`.
- `status`: `completed`, `no_decisions`, `vectors_absent`, `model_mismatch` or
  `declined`.
- `partial`: `true` exactly when a mid-run backend failure stopped judging
  (the run then also exits `1`, with the failure on stderr); otherwise `false`.
- `counts`: `{candidate_pairs, served, judged, malformed,
  without_embedding, changed_since_reindex, excluded_unreadable_relations}`,
  or `null` unless `status` is `completed`.
- `revisions`: an array, `[]` for every status but `completed` and when there
  is nothing to list. One entry per finding the human report lists, in the
  order it lists them (`--all` widens the array as it widens the report), each:
  - `decisions`: the pair as two objects in sorted id order, each
    `{id, event_date, date_state, quote}`. `event_date` is an ISO string or
    `null`; `date_state` is the resolver's state; `quote` is the verbatim
    quote from that Decision, or `null` when none was verified.
  - `verdict`: `reverses`, `refines`, `reaffirms` or `unrelated`.
  - `relation`: `supersedes` for a directed `reverses`, `revises` for a
    directed `refines`, otherwise `null`. It is the relation
    `reconcile --from-findings` would offer, never one `revisions` wrote.
  - `untyped_change`: `true` for a `reverses`/`refines` whose direction is
    unknown.
  - `direction`: `{source, earlier, later, reason}`. `source` is
    `event_dates` when the Sources' event dates order the pair and `none`
    otherwise; `earlier` and `later` are Decision ids, or `null` when `source`
    is `none`; `reason` is `dated` or the reason the direction is unknown.
  - `confidence`: the judge's number; `rationale`; `served` (read from
    persisted findings, no model call) and `malformed` (never persisted).
  - `actionable`: `true` when `reconcile --from-findings` would offer it.

Sensitivity MUST follow the human report: the array is built from the same
plan and outcome, so a confidential Decision appears (by id, quote and
rationale) only when `--include-confidential` or the local exemption admitted
it into the run.

#### Scenario: A directed reversal is listed with its relation

- GIVEN two Decisions whose Sources carry different event dates and a judged
  `reverses` verdict
- WHEN `openkos revisions --auto --json` runs
- THEN stdout is one JSON object whose single entry has `verdict` `reverses`,
  `relation` `supersedes` and `direction.source` `event_dates` with the later
  Decision as `later`

#### Scenario: Nothing to list is still valid JSON

- GIVEN a workspace with no Decision
- WHEN `openkos revisions --json` runs
- THEN stdout is one object with `status` `no_decisions`, `counts` `null` and
  `revisions` `[]`, and the exit code is `0`

#### Scenario: Confidential Decisions are gated as in the report

- GIVEN a confidential Decision and a private one that would pair
- WHEN `openkos revisions --auto --json` runs without `--include-confidential`
- THEN neither id nor any text of the confidential Decision is on stdout
