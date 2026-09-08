# Delta for Entity-Resolution Adjudication

## Purpose Update (for archive-time merge into the main spec's `## Purpose`)

Replace the current Purpose paragraph in
`openspec/specs/entity-resolution-adjudication/spec.md` with:

`resolution/adjudication.py` is a read-only, config-free precision layer over
slice 1's `find_candidates` output: it prompts an injected `LLMBackend` to
adjudicate each `CandidateGroup` — using member title + full body — into a
`SAME` / `DIFFERENT` / `UNCERTAIN` verdict with confidence and rationale,
surfaced through the read-only `adjudicate` CLI verb; `adjudicate_candidates`
itself never merges, writes, or decides. The same `adjudicate` verb's
`--apply` and `--apply-same` modes act on those verdicts: each owns argument
parsing, workspace setup, the confirmation gate — including
`--apply-same`'s typed-count challenge-response — and rendering the preview
and summary; the Phase A (prepare) / confirm-gate / Phase B (write)
composition for both modes, including driving `merge_core` per accepted
pair, is delegated to the lifecycle application service.

(Previously: this paragraph described only the read-only
`adjudicate_candidates` core and did not mention `--apply`/`--apply-same`,
whose composition now lives behind the lifecycle application service.)

## Notes

No `## Requirements` entries in the main spec change. This is a composition
refactor: eligibility filtering, ordering, warning/consent disclosure,
per-item and typed-count confirmation semantics, re-verification before each
merge, fail-fast on `merge_core` failure, and every other behavior specified
in `openspec/specs/entity-resolution-adjudication/spec.md` are unchanged by
moving `--apply`/`--apply-same`'s composition behind the service. The
existing `tests/unit/cli/test_adjudicate.py` suite (154 tests) remains the
behavior contract; the 4 `monkeypatch.setattr("openkos.cli.main.…")` sites
that target relocated names (`prepare_merge`, `merge_core`) are repointed at
the relocated module, not behaviorally changed.
`lifecycle-application-service`'s "ConfirmationRequest Is A Tagged Union Of
Boolean And Typed-Count Variants" and "The Extraction Preserves Observable
CLI Behavior" requirements are the checkable guarantees tying the two specs
together.
