# Delta for Query Command

## Purpose Update (for archive-time merge into the main spec's `## Purpose`)

Replace the current Purpose paragraph in `openspec/specs/query-command/spec.md`
with:

The `openkos query "<question>"` Typer command is the CLI entry point for
the MVP-1 query chain: it is a thin adapter over the query application
service, which performs workspace gating, builds the LLM/embedder seams,
calls the `retrieval.answer()` library seam, and computes the `--save`
filing plan. `query` itself owns only argument parsing, interactive
confirmation, exit-code mapping, and rendering the answer plus citations as
plain text to stdout.

(Previously: this paragraph described the command itself as gating the
workspace, building the client, calling `answer()`, and rendering — that
composition now lives behind the query application service.)

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
