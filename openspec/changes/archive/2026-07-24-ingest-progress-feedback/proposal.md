# Proposal: Ingest Progress Feedback (spinner + per-type tally)

## Intent

`openkos ingest` runs one blocking `extract_concept` LLM call (`main.py:493-501` → `concept.py:255`) that sits silent for ~20s with no feedback, so users cannot tell whether the command hung. The final summary (`main.py:918-925`) also reports only a flat count with no breakdown of what was produced. This MVP-3 UX-polish slice adds two small, additive, non-breaking signals: a live activity indicator during the LLM wait, and a compact per-type tally after import.

## Scope

### In Scope
- Spinner (`rich.status.Status`, activity indicator — NOT a progress bar) on STDERR, wrapping only the `extract_concept` call site (`main.py:493-501`); `Console(stderr=True)` built per-call inside `_stage_derived_objects`.
- New reusable helper `_format_type_tally(counts: dict[str, int]) -> str`, colocated near `_plural` (`main.py:348-351`).
- One new tally line after the existing summary (`main.py:925`), built from `derived_plans` grouped by `.doc_type`, emitted only when at least one object was produced.

### Out of Scope
- **#133 typed counts in `status`** — ALREADY SHIPPED; do not touch `status`, `_bundle_content_lines`, or `BundleSurvey.by_type`.
- Progress bars, ETA, or multi-phase progress.
- Any `pyproject.toml` / lockfile change (rich is already transitive via `typer==0.27.0`).

## Capabilities

### New Capabilities
None (additive UX to existing `ingest`; no new spec-level capability).

### Modified Capabilities
None — no existing exit code, stdout line, or behavior changes. Spinner is stderr-only; tally is a strictly additive stdout line.

## Approach

- **Spinner**: context-manager `rich.status.Status` around `main.py:493-501` so it always clears on both success and `OllamaError`. Console pinned to stderr so it never pollutes the structured stdout the tests assert on; built per-call (not module-level) to bind the current `CliRunner`-swapped stream. rich no-ops on non-TTY.
- **Tally**: `_format_type_tally` renders `extracted {N} objects — {count} {Type}, ...` (em dash, comma-separated). "objects" pluralized via `_plural`; type names printed verbatim as canonical `CLASSIFIABLE_TYPES` strings; types ordered by canonical `REGISTRY` order (not insertion/alphabetical) for run-stable output. Input is a plain `dict[str, int]` so #133 could reuse it later without coupling to ingest internals.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/cli/main.py` `_stage_derived_objects` | Modified | Spinner wrap at `493-501` |
| `src/openkos/cli/main.py` `ingest` | Modified | Tally line after `925` |
| `src/openkos/cli/main.py` (near `348-351`) | New | `_format_type_tally` helper |
| `tests/unit/cli/test_ingest.py` | Modified | Spinner + tally tests |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Spinner control chars leak into stdout | Med | `Console(stderr=True)`; assert clean stdout in non-TTY CliRunner test |
| Module-level Console freezes wrong stream under CliRunner | Med | Construct per-call inside function |
| Nondeterministic tally ordering | Med | Canonical `REGISTRY` order, tested zero/one/mixed |
| Spinner not cleared on `OllamaError` | Low | Context-manager `__exit__` clears on both paths |

## Rollback Plan

Single small PR (~well under 800-line budget). Revert the commit; both features are additive with no migration or state change.

## Dependencies

None new — rich available transitively via typer.

## Success Criteria

- [ ] Spinner shows on TTY during LLM call, silent/clean on non-TTY, clears on success and `OllamaError`.
- [ ] Tally line matches `extracted N objects — c Type, ...` in canonical order; absent when zero objects.
- [ ] All existing stdout-substring ingest tests still pass; exit codes unchanged.
