# Tasks: `improve-forget-init-ux` — idempotent re-ingest + init next-step hint

## Review Workload Forecast

| Field | Value |
|---|---|
| Estimated changed lines | ~205 (`cli/main.py` ~65; `test_ingest.py` ~120; `test_init.py` ~10; `docs/cli.md` ~12) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR — one cohesive byte-aware-refusal change across `main.py`/tests/docs, all additive/local (D1-D6) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|---|---|
| 1 | Byte-aware `ingest` refusal/regenerate branch (D1-D5) + `init` next-step hint (D6) + tests + docs | PR 1 (single PR, under budget) | `uv run pytest tests/unit/cli/test_ingest.py tests/unit/cli/test_init.py` | `CliRunner` end-to-end `init`→`ingest`→`forget`→`ingest` round-trip in a tmp workspace — no live Ollama, no external process | `git revert`; additive branch replacing one refusal block, no schema/state/dependency change; `forget` untouched |

## Phase 1: RED — ingest idempotency tests (`tests/unit/cli/test_ingest.py`)
- [x] 1.1 Replace `test_collision_refuses_in_phase_a`'s `"raw"` case with `test_differing_source_reingest_refuses` — differing bytes under an existing `raw/<name>` → exit 1, stderr distinguishes "differs from the existing 'raw/<name>' copy" from the identical case, raw bytes unchanged (Scenario: Differing re-ingest still refused).
- [x] 1.2 Replace the `"concept"` case with standalone `test_raw_absent_concept_present_refuses_inconsistent_workspace` (D5) — remove `raw/<name>` but keep the concept, re-ingest → exit 1, "inconsistent" workspace message, nothing written (Scenario: Raw absent but concept present).
- [x] 1.3 Add `test_reingest_after_forget_regenerates_concept` — `init` → `ingest --auto` → `forget --auto` → `ingest --auto` (same file) → exit 0, concept restored, `raw/<name>` bytes byte-identical to the pre-forget snapshot, exactly ONE `sources/<slug>.md` bullet in `index.md`, a new `**Re-ingest**` log entry (Scenario: byte-identical re-ingest, post-forget sub-case).
- [x] 1.4 Add `test_reingest_without_forget_regenerates_without_duplicate_index_entry` (D3) — `init` → `ingest --auto` → `ingest --auto` (same file, no forget) → exit 0, `index.md` contains exactly ONE occurrence of `sources/<slug>.md` (dedup proof), raw bytes unchanged, concept content regenerated.
- [x] 1.5 Add `test_reingest_preview_shows_regenerate_not_new_raw` — TTY confirm run on an identical re-ingest → stdout shows `~ raw/<name>` (existing copy reused) and no `+ raw/<name>` line.

## Phase 2: GREEN — byte-aware `ingest` branch (`src/openkos/cli/main.py`)
- [x] 2.1 Replace the blanket `raw_dest.exists() or concept_path.exists()` refusal (245-256) with the D1/D4/D5 branch from design: full-byte compare before any write; differing → refuse; identical → `regenerate = True`; raw absent + concept present → D5 refuse.
- [x] 2.2 Thread `regenerate` through the build block (301-308): when `True`, `index_text, _ = bundle_index.remove_index_entry(index_text, f"sources/{slug}")` before `insert_source_entry` (D3 dedup), and the log line becomes `**Re-ingest**: Regenerated ...` instead of `**Ingest**: Imported ...`.
- [x] 2.3 Branch the preview block (315-319): regenerate path prints `~ raw/<name>` / `~ bundle/sources/<slug>.md` / `~ index.md` / `~ log.md` instead of the fresh `+ raw` / `+ concept` set.
- [x] 2.4 Branch Phase B writes (332-337): regenerate path skips `fsio.copy_exclusive` and writes the concept via non-exclusive `fsio.write_atomic` (D2) instead of `write_exclusive`.

## Phase 3: RED — init next-step hint test (`tests/unit/cli/test_init.py`)
- [x] 3.1 Extend `test_fresh_empty_directory` (or add a dedicated test) — assert `result.stdout` contains a next-step hint naming `openkos ingest` (Scenario: Success output includes the next-step hint).

## Phase 4: GREEN — init next-step hint (`src/openkos/cli/main.py`)
- [x] 4.1 Add one unconditional `typer.echo("Next: run \`openkos ingest <path>\` to import your first source.")` after the success summary (after line 128), per D6.

## Phase 5: Docs (`docs/cli.md`)
- [x] 5.1 Update the `ingest` section: replace the "already exists is refused" line with the byte-identical-regenerates-cleanly behavior + the differing-source-still-refused behavior.
- [x] 5.2 Update the `init` section: mention the unconditional next-step hint pointing at `openkos ingest`.

## Phase 6: Verification Gate
- [x] 6.1 `uv run pytest --cov` — full suite green, ≥90% branch coverage on changed lines.
- [x] 6.2 `uv run ruff check .` && `uv run ruff format --check .` — clean.
- [x] 6.3 `uv run mypy .` — clean (strict).
