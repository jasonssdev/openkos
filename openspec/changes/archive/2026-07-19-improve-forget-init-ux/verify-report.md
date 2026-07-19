# Verify Report: `improve-forget-init-ux`

**Change**: idempotent re-ingest + init next-step hint
**Mode**: Full artifact set (proposal/specs/design/tasks) — Strict TDD active
**Verdict**: PASS WITH WARNINGS

## Task Completeness

16/16 tasks marked `[x]` in `openspec/changes/improve-forget-init-ux/tasks.md`. `git status --porcelain` confirms modifications to exactly the files tasks/apply-progress claim: `docs/cli.md`, `src/openkos/cli/main.py`, `tests/unit/cli/test_ingest.py`, `tests/unit/cli/test_init.py`. No unchecked tasks — full verification proceeds.

## Test / Build Evidence

| Command | Exit | Result |
|---|---|---|
| `uv run pytest --cov=openkos --cov-report=term-missing` | 0 | 438 passed, TOTAL coverage 98.90% (>=90% required); `cli/main.py` 99% (2 pre-existing uncovered lines: 429, 536->538, unrelated to this change, in `_resolve_concept_path`/`forget`) |
| `uv run ruff check .` | 0 | All checks passed |
| `uv run ruff format --check .` | 0 | 45 files already formatted |
| `uv run mypy .` | 0 | Success, no issues in 45 source files (strict) |

## Spec Compliance Matrix

### Capability: `ingestion` (1 requirement, 7 scenarios)

| # | Scenario | Test | Result |
|---|---|---|---|
| 1 | Successful ingest embeds verbatim text | `test_successful_ingest_of_valid_path` | PASS |
| 2 | Path does not exist | `test_path_does_not_exist` | PASS |
| 3 | Raw absent but concept present is refused as an inconsistent workspace (D5) | `test_raw_absent_concept_present_refuses_inconsistent_workspace` | PASS — exit 1, "inconsistent" in stderr, full-tree `_snapshot` before==after (nothing written) |
| 4 | Byte-identical re-ingest regenerates the concept and catalog (D1-D3) | `test_reingest_after_forget_regenerates_concept` (post-forget sub-case), `test_reingest_without_forget_regenerates_without_duplicate_index_entry` (no-forget/D3 dedup sub-case), `test_reingest_preview_shows_regenerate_not_new_raw` (preview wording sub-case) | PASS — all 3 sub-clauses covered |
| 5 | Differing re-ingest under the same name is still refused (D4) | `test_differing_source_reingest_refuses` | PASS — exit 1, "differs"/"immutable" in stderr, full-tree snapshot before==after |
| 6 | Undecodable source falls back without crashing | `test_undecodable_source_degrades_without_crashing` | PASS (unchanged) |
| 7 | Empty source renders a distinct body | `test_empty_source_renders_distinct_body` | PASS (unchanged) |

**Requirements: 1/1. Scenarios: 7/7.**

### Capability: `workspace-init` (1 requirement, 3 scenarios)

| # | Scenario | Test | Result |
|---|---|---|---|
| 1 | Fresh empty directory | `test_fresh_empty_directory` | PASS (unchanged) |
| 2 | Success message names what was created | `test_fresh_empty_directory` | PASS (unchanged) |
| 3 | Success output includes the next-step hint (D6) | `test_fresh_empty_directory` (extended assertion `"openkos ingest" in result.stdout`) | PASS, but only exercised in the DEFAULT (non-TTY) `CliRunner.invoke` path — see WARNING below |

**Requirements: 1/1. Scenarios: 3/3.**

**Total: Requirements 2/2. Scenarios 10/10, all covered by passing runtime tests.**

## CORE Guarantee: raw bytes never written/deleted outside the fresh path

Verified directly in source and tests:

- `src/openkos/cli/main.py:255-277` — the byte-aware branch: `raw_dest.exists()` differing -> refuse before any write (line 257, comparison happens via `read_bytes()`, no write call reached); identical -> `regenerate = True`, no write in Phase A either.
- `src/openkos/cli/main.py:378-385` — Phase B: `if regenerate: fsio.write_atomic(concept_path, ...)` (raw copy step `fsio.copy_exclusive` is skipped entirely) `else: fsio.copy_exclusive(src, raw_dest); fsio.write_exclusive(concept_path, ...)`. `copy_exclusive`/raw write is reached ONLY on the fresh (non-regenerate) path.
- `tests/unit/cli/test_ingest.py::test_differing_source_reingest_refuses` asserts `_snapshot(tmp_path) == before` post-refusal (`_snapshot` reads every file's raw bytes under the workspace root via `rglob`), proving zero bytes changed anywhere, including `raw/`.
- `tests/unit/cli/test_ingest.py::test_reingest_after_forget_regenerates_concept` asserts `(tmp_path / "raw" / "notes.txt").read_bytes() == raw_snapshot` after the post-forget re-ingest round-trip (`init` -> `ingest --auto` -> `forget --auto` -> `ingest --auto`) — direct byte-identity proof across the regenerate path.
- `tests/unit/cli/test_ingest.py::test_reingest_without_forget_regenerates_without_duplicate_index_entry` asserts the same raw-byte-unchanged invariant on the no-forget regenerate path.

**Confirmed: raw/ bytes are never written or deleted in the regenerate or differing-refuse paths. Only the fresh (non-regenerate) path calls `copy_exclusive`. Both the post-forget round-trip test and the differing-refuse test assert raw-byte-unchanged.**

## D3 Dedup Guarantee

`src/openkos/cli/main.py:322-328` — on `regenerate`, `bundle_index.remove_index_entry(index_text, f"sources/{slug}")` runs before `insert_source_entry`, so a no-forget re-ingest (bullet already present) is deduped and a post-forget re-ingest (bullet absent, `remove_index_entry` is a no-op per design D3 rationale) still ends with exactly one entry.

`test_reingest_without_forget_regenerates_without_duplicate_index_entry` asserts `index_text.count("sources/notes.md") == 1` after two `ingest --auto` runs on the identical file with no `forget` in between — **directly counts index entries and proves exactly ONE**, confirming D3.

## D5 Odd State

`src/openkos/cli/main.py:268-276` — `elif concept_path.exists()` (reached only when `raw_dest` does NOT exist) refuses with `typer.Exit(code=1)` before any write; caught by the outer `except (OSError, ValueError)` only for genuine I/O errors, not for this explicit refusal path (it's not an exception, it's a direct `typer.Exit`). `test_raw_absent_concept_present_refuses_inconsistent_workspace` confirms exit 1, "inconsistent" message, and full-tree snapshot unchanged — **refuses cleanly, no crash**.

## Init Next-Step Hint

`src/openkos/cli/main.py` (per apply-progress, after line 128) adds one unconditional `typer.echo("Next: run \`openkos ingest <path>\` to import your first source.")`. `test_fresh_empty_directory` asserts `"openkos ingest" in result.stdout`. **Confirmed: init prints the next-step hint.**

## Design Coherence (D1-D6)

| Decision | Code location | Match |
|---|---|---|
| D1 discriminant (raw exists + byte compare, not concept exists) | main.py:256-277 | Matches |
| D2 non-exclusive `write_atomic` for regenerate concept write | main.py:382 | Matches |
| D3 remove-then-insert index dedup | main.py:326-328 | Matches |
| D4 full-byte inline compare, no new fsio primitive | main.py:257 (`src.read_bytes() != raw_dest.read_bytes()`) | Matches |
| D5 odd-state refusal | main.py:268-276 | Matches (message wording deviation noted below) |
| D6 unconditional init hint, no TTY/quiet gating | main.py (after success summary) | Matches |

**Documented deviation (non-blocking)**: D5's refusal message uses "the workspace is inconsistent" instead of design.md's literal draft "the workspace is in an inconsistent state" — the substring "state" is dropped because `tests/unit/state/test_fts.py::test_ingest_and_forget_do_not_reference_state_fts` asserts that literal substring never appears in `ingest`'s source (a dormant-dependency guard unrelated to this change). Both the spec's "inconsistent" wording requirement and the D5 scenario's exit-1 + inconsistency-identifying-message requirement remain satisfied. This is a WARNING (design deviation that does not break the spec), not a CRITICAL.

## Issues

### CRITICAL
None.

### WARNING
1. **Init hint TTY coverage gap** (carried over from review lineage `review-e8956f1fb92ffef0`, accepted non-blocking follow-up): `test_fresh_empty_directory` asserts the next-step hint only via the default (non-TTY) `CliRunner.invoke(app, ["init"])` path. No test simulates a TTY stdin (`_simulate_tty`, already used elsewhere in `test_init.py` for other scenarios) and then re-asserts the hint. The spec explicitly requires the hint "regardless of whether stdin is a TTY," and the implementation is a single unconditional `typer.echo` with no TTY branching at all — so behaviorally there is no gap, only a test-coverage gap for one of the two stdin states. Accepted as a non-blocking follow-up per prior review; does not block archive.
2. **D5 message wording deviation** from design.md's literal draft (see Design Coherence table above) — functionally equivalent, does not break the spec's "inconsistent" requirement.

### SUGGESTION
1. Consider adding a TTY-simulated variant of the init-hint assertion in a follow-up to close the WARNING above and give the "no TTY/quiet gating" claim symmetric test coverage.

## Verdict

**PASS WITH WARNINGS** — 2/2 requirements and 10/10 scenarios covered by passing runtime tests; full gate (pytest/ruff/ruff-format/mypy) green; CORE raw-byte-immutability guarantee, D3 dedup, and D5 odd-state handling all independently confirmed via direct test assertions (byte snapshots, index-entry counts). No CRITICAL findings. Two WARNINGs are both pre-accepted/non-blocking (TTY coverage gap, cosmetic message wording deviation) and do not require rework before archive.

## Next Recommended

`sdd-archive` (after merge).
