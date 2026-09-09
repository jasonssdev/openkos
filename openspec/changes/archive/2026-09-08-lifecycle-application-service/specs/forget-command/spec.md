# Delta for Forget Command

## Purpose Update (for archive-time merge into the main spec's `## Purpose`)

Replace the current Purpose paragraph in `openspec/specs/forget-command/spec.md`
with:

`openkos forget <concept-id>` is the missing removal counterpart to
`ingest`: it deletes a concept file and removes that concept's reference
from `index.md`, across any section. `forget` itself owns argument parsing,
workspace and configuration setup, the confirmation gate (TTY detection and
the non-TTY refusal), rendering the preview and refusal messages, and the
catalog (`index.md`) write via the shared write helpers; the Phase A
(validate + preview) / confirm-gate / Phase B (write) composition itself is
delegated to the lifecycle application service, mirroring `ingest`'s shape.

(Previously: this paragraph described `forget` as performing the Phase A /
confirm-gate / Phase B composition itself — that composition now lives
behind the lifecycle application service.)

## Notes

No `## Requirements` entries in the main spec change. This is a composition
refactor: scope selection, provenance descendant resolution, path safety,
surviving-reference detection and refusal, preview and confirm shape, and
catalog write behavior — every behavior specified in
`openspec/specs/forget-command/spec.md` — are unchanged by moving their
composition behind the service. The existing `tests/unit/cli/test_forget.py`
suite (89 tests) remains the behavior contract, and
`lifecycle-application-service`'s "The Extraction Preserves Observable CLI
Behavior" requirement is the checkable guarantee tying the two specs
together. `forget`'s surviving-references refusal gate stays a hard refusal,
never representable as a confirmation, per that spec's own requirement.
