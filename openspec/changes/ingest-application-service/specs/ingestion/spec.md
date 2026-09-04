# Delta for Ingestion

## Purpose Update (for archive-time merge into the main spec's `## Purpose`)

Replace the current Purpose paragraph in `openspec/specs/ingestion/spec.md`
with:

`openkos ingest <path>` is the CLI entry point for ingesting a raw source:
it gates the workspace, reads the configuration, builds the LLM client,
and performs every snapshot read, then delegates to the ingest
application service, which stages a bounded list of derived objects —
zero up to a post-judge backstop cap of 12, each classified across the
9-type derived-object vocabulary (`Concept`, `Entity`, `Place`, `Event`,
`Procedure`, `Decision`, `Project`, `Person`, `Organization`) — alongside
the generated Source concept. `ingest` itself owns argument parsing,
workspace and client setup, the confirmation gate, rendering the
extraction notices and derived-object preview, catalog (`index.md`) and
log (`log.md`) writes via the shared write helpers, and degrading to
Source-only behavior with zero crashes on any LLM failure.

(Previously: this paragraph described `ingest` itself as attempting
extraction and staging derived objects — that composition now lives
behind the ingest application service.)

## Notes

No `## Requirements` entries in the main spec change. This is a
composition refactor: workspace gating, exit codes, the confirmation and
preview shape, catalog/log behavior, title derivation, type
classification, staging/dedup rules, and every other behavior specified
in `openspec/specs/ingestion/spec.md` are unchanged by moving their
composition behind the service. The existing 307
`tests/unit/cli/test_ingest.py` tests remain the behavior contract, and
`ingest-application-service`'s "The Extraction Preserves Observable CLI
Behavior" requirement is the checkable guarantee tying the two specs
together.

`openspec/specs/extraction-union-judge/spec.md` and any other spec
governing `extraction/concept.py` itself are unaffected: that leaf was
already pure and CLI-free before this change, and its signature and
behavior are unchanged. No delta is written for it.
