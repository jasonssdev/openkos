# Exploration: ingest-progress-feedback (#136)

Spinner during ingest extraction + per-type tally in the `openkos ingest` summary.
Scope: #136 ONLY (do not touch #133), but the tally is a reusable helper.

## Current State
- `ingest` command: `src/openkos/cli/main.py:582-931`. Final summary line at
  `main.py:918-925` — insertion point for the new tally line, just before
  `_autocommit` at `main.py:927`.
- Blocking LLM call confirmed at `src/openkos/extraction/concept.py:255`, inside
  `extract_concept()` (`concept.py:240-262`), called from `_stage_derived_objects`
  at `main.py:494` inside `try/except OllamaError` (`main.py:493-501`).
- `extract_concept()` returns `list[ExtractionResult]`; `ExtractionResult.type` is
  a plain `str` from the closed 9-value vocabulary
  `openkos.model.types.CLASSIFIABLE_TYPES`. Each becomes a `_DerivedPlan.doc_type`
  (`main.py:382-399`, `main.py:567`). `ingest()`'s `derived_plans` list
  (`main.py:803`) is the ready-made source for the tally, grouped by `.doc_type`.
- Tightest spinner-wrap candidate is `main.py:493-501` (the actual call + its
  `except OllamaError`), NOT the whole `_stage_derived_objects` invocation — the
  sensitivity/blank-content fast paths above it (`main.py:478-491`) return
  instantly and shouldn't show a spinner.
- `rich` is NOT a direct dependency in `pyproject.toml` but IS genuinely importable
  today as a locked transitive dependency of `typer==0.27.0` (`uv.lock:710-716`,
  `uv.lock:829-837`). No existing `rich` import anywhere — this would be the first.
- #133 (typed counts in `status`) is ALREADY implemented: `okf.BundleSurvey.by_type:
  dict[str,int]` (`okf.py:882-887,908-949`) rendered by `_bundle_content_lines`
  (`main.py:3306-3327`) as a multi-line table, commented `(#133)`. Different shape
  than #136's compact single line; the new helper should accept a `dict[str,int]`
  so it stays reusable without touching `_bundle_content_lines`.
- Helper-placement convention: `main.py` already hosts small reused private string
  helpers (`_plural`, `main.py:348-351`; `_bundle_content_lines`,
  `main.py:3306-3327`). `cli/observability.py` is narrowly scoped — not a fit.
- Tests: `tests/unit/cli/test_ingest.py` uses `typer.testing.CliRunner`, asserts on
  `result.stdout` substrings, has a `_simulate_tty(monkeypatch)` fixture
  (`test_ingest.py:32-38`) and a mocked `OllamaClient` (`test_ingest.py:60-83`).
  Non-TTY is the default; TTY is opt-in via that fixture.

## Affected Areas
- `src/openkos/cli/main.py` — `_stage_derived_objects` (spinner), `ingest()` (tally
  line), new `_format_type_tally` helper.
- `src/openkos/extraction/concept.py` — reference only, no change.
- `pyproject.toml` — likely no change (rich resolvable transitively); explicit
  direct pin is a design-phase call.
- `tests/unit/cli/test_ingest.py` — new tests: tally content (zero/one/mixed
  types), non-TTY spinner silence, TTY spinner presence, spinner clears on
  `OllamaError`.

## Recommendation
`rich.status.Status` as a context manager at `main.py:493-501`, targeting stderr,
`Console` built per-call (binds current stream under `CliRunner`). Pair with a new
`_format_type_tally(counts: dict[str, int]) -> str` colocated near `_plural`, fed by
`derived_plans` grouped by `.doc_type`, printed after `main.py:925` only when
`derived_plans` is non-empty.

## Risks
- Non-TTY/piped stdout must be explicitly tested (no stray control chars, exit 0).
- `Console` must be constructed per-call, not at module import, or it binds the
  wrong stream under `CliRunner`.
- Spinner must target stderr, not stdout, to avoid corrupting the plain-text lines
  the suite already asserts against.
- Tally string format (singular/plural, ordering — likely canonical registry order
  for determinism) needs an explicit tested contract.
- Scope: `_bundle_content_lines`/`BundleSurvey.by_type` (#133, shipped) untouched.

## Open Design Decision
Exact tally string format and type ordering. Issue specifies
`extracted 3 objects — 2 Concept, 1 Procedure`; recommend canonical registry order
for determinism.
