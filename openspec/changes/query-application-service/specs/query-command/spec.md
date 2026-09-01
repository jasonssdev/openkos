# Delta for Query Command

## Purpose Update (for archive-time merge into the main spec's `## Purpose`)

Replace the current Purpose paragraph in `openspec/specs/query-command/spec.md`
with:

The `openkos query "<question>"` Typer command is the CLI entry point for
the MVP-1 query chain: it gates the workspace, reads the configuration and
builds the LLM/embedder seams, then delegates to the query application
service, which opens the indexes with degrade handling, calls the
`retrieval.answer()` library seam, and computes the `--save` filing plan.
`query` itself owns only argument parsing, workspace and client setup,
interactive confirmation, exit-code mapping, and rendering the answer plus
citations as plain text to stdout.

(Previously: this paragraph described the command itself as calling
`answer()` and computing the filing plan — that composition now lives
behind the query application service.)

## Notes

No `## Requirements` entries in the main spec change. This is a pure
composition refactor: `require_workspace`'s refusal wording, exit codes,
rendering shape, the `--save` filing rules, and every other behavior
specified in `openspec/specs/query-command/spec.md` are unchanged by moving
their composition behind the service. The existing 161
`test_query.py`/`test_query_save.py` CLI tests remain the behavior
contract, and `query-application-service`'s "The Extraction Preserves
Observable CLI Behavior" requirement is the checkable guarantee tying the
two specs together.

`openspec/specs/query-answer/spec.md` (the `retrieval.answer()` library
seam) is unaffected by this change: its signature, degrade behavior, and
every requirement stay exactly as currently specified. No delta is written
for it.
