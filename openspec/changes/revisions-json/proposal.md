# Proposal: revisions-json

Refs #1739.

## Intent

`openkos revisions` has no machine-readable output. An external consumer that
needs the detected decision relations must read `.openkos/findings.db`, an
internal schema that can change without notice, while `query --json` (#1345)
and `adjudicate --json` already give stable contracts. This adds `--json` to
`revisions` as a versioned machine contract.

Success: `openkos revisions --json` prints exactly one JSON object on stdout
listing every verdict the human report would list, with both Decision ids, the
verdict, the relation it would record, the direction source, the confidence and
the evidence; nothing else changes.

## Scope

### In Scope

1. `--json` on `revisions`; one object, `schema_version` `1`.
2. A pure builder in `application/revisions_report.py` over the same
   `RevisionPlan` / `RevisionOutcome` the human report renders.
3. Valid JSON for every non-refusal status (no Decisions, vectors absent,
   model mismatch, declined), and for a partial batch.
4. `docs/cli.md` and the CHANGELOG.

### Out of Scope

- Applying revisions at ingest or unattended (measured, not adopted, #1350).
- Any change to detection, judging, persistence, ordering or the human text.
- An `applied` field: a pair already joined by `supersedes`,
  `reconciled_with` or `revises` is never a candidate, so it never reaches the
  list, and the field would be constant `false`.

## Approach

The builder reuses the report's `_FindingView` normalization, display filter
and grouping order, so the JSON array and the human report cannot disagree
about which findings exist or in what order. `--all` governs the array exactly
as it governs the human report (the `adjudicate --same-only` precedent); the
`counts` object describes the run. Refusals keep exit `1` with an empty stdout.
The confirmation prompt, which Typer writes to stdout, is sent to stderr under
`--json`.

## Risks

- A consumer could read `relation` as "applied". The spec states it is the
  relation `reconcile --from-findings` would record, and that revisions only
  suggests.
