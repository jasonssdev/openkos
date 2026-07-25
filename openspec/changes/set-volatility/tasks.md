# Tasks: set-volatility Write Verb (#140)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~500-600 (production ~120-150: core `set_type_tier` + CLI command + hint line; tests ~380-450: many fail-closed shape fixtures + CLI TTY/confirm/idempotence matrix) |
| 800-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | auto-forecast (AUTOMATIC) |
| Chain strategy | pending |

Decision needed before apply: No

The pure-core algorithm (3 edit cases + 6 fail-closed shapes) and the CLI verb (mirroring
`relate`) are additive and self-contained; no existing production code path is rewritten.
Test volume (many small before/after text fixtures) dominates the diff but each fixture is
a few lines, keeping the total comfortably under the 800-line budget. Re-forecast during
`sdd-apply` only if the fail-closed fixture count grows materially beyond the six shapes
enumerated in the design.

### Suggested Work Units (informational — single PR, not a chain)

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Pure core `config.set_type_tier` (3 edit cases + 6 fail-closed shapes) | PR 1 (only) | `uv run pytest tests/unit/test_config.py -k set_type_tier` | pure text-in/text-out, no I/O | `set_type_tier` additions in `config.py` revertable independently |
| 2 | CLI `set-volatility` command (validation, preview, confirm gate, write, autocommit, idempotence) | PR 1 (only) | `uv run pytest tests/unit/cli/test_set_volatility.py` | `CliRunner`, tmp workspace, TTY simulation (mirrors `test_relate.py`) | New command block in `main.py` revertable independently |
| 3 | `suggest-volatility` hint line update | PR 1 (only) | `uv run pytest tests/unit/cli/test_suggest_volatility.py -k hint` | `CliRunner` | Single line change, trivially revertable |

## Phase 1: Pure Core — `config.set_type_tier` (Comment-Safe Text Surgery)

Requirement: volatility-config / "Comment-Safe `type_tiers:` Editing";
"Fail-Closed On Unparseable Config Shape"

- [x] 1.1 RED: `tests/unit/test_config.py` — case (a) block present with a `Person`
      entry: `set_type_tier(text, "Person", "volatile")` returns text where only that
      line's value changed, indent/trailing-comment/everything else byte-identical.
- [x] 1.2 RED: `tests/unit/test_config.py` — case (b) block present, no `Procedure`
      entry: inserts `{indent}Procedure: volatile\n` after the last real entry using
      the block's canonical indent; case (b) empty block (header only, no entries):
      inserts with fixed 2-space indent directly after the header.
- [x] 1.3 RED: `tests/unit/test_config.py` — case (c) no uncommented `type_tiers:`
      header (absent key, or fully commented shipped-template state): appends
      `type_tiers:\n  Person: volatile\n` at EOF after ensuring exactly one trailing
      newline; rest of file untouched.
- [x] 1.4 RED: `tests/unit/test_config.py` — idempotent identity: entry already equals
      target tier → returned text is byte-identical to input (defense-in-depth; CLI
      still short-circuits before calling the core, see 2.6).
- [x] 1.5 RED: `tests/unit/test_config.py` — 6 fail-closed shapes, each asserting
      `pytest.raises(ValueError)` and (where a fixture path is used) the source text
      untouched:
      (i) inline flow-mapping `type_tiers: {…}`;
      (ii) multiple `type_tiers:` header keys;
      (iii) non-mapping scalar value (`type_tiers: foo` / `[…]` / `null`);
      (iv) tab-indented block;
      (v) inconsistent entry indent (later entry indent differs from first);
      (vi) duplicate `Type` entry within the same block.
- [x] 1.6 GREEN: implement `set_type_tier(yaml_text: str, concept_type: str, tier:
      str) -> str` in `src/openkos/config.py` per the design's text-surgery algorithm
      (header regex `^type_tiers:\s*(#.*)?$`, entry regex, cases a/b/b-empty/c, the 6
      fail-closed detections); add `from openkos.model import types` and validate
      `concept_type`/`tier` vocabulary in the core too (defense-in-depth). Confirm
      1.1-1.5 all GREEN.

## Phase 2: CLI Verb — `set-volatility`

Requirement: volatility-config / "`set-volatility` Command Shape"; "Strict Tier
Validation"; "Strict ConceptType Validation"; "Preview And Confirm Gate";
"Idempotent No-Op"; "Auto-Commit On Successful Write"

- [x] 2.1 RED: `tests/unit/cli/test_set_volatility.py` — invalid tier (e.g. `bogus`):
      stderr states the value is invalid, non-zero exit, `openkos.yaml` unchanged, no
      commit created.
- [x] 2.2 RED: `tests/unit/cli/test_set_volatility.py` — invalid `ConceptType` (e.g.
      `Widget`): stderr lists the valid `REGISTRY` type names, non-zero exit,
      `openkos.yaml` unchanged, no commit created.
- [x] 2.3 GREEN: implement `set-volatility` command skeleton in
      `src/openkos/cli/main.py` (mirror `relate`, main.py:2166-2354): argument
      parsing + vocabulary validation (tier ∈ `VOLATILITY_TIERS`; ConceptType ∈
      `{ot.name for ot in types.REGISTRY}`, exact-match case-sensitive) before any
      read/write. Confirm 2.1-2.2 GREEN.
- [x] 2.4 RED: `tests/unit/cli/test_set_volatility.py` — unparseable existing config
      shape (one of the fail-closed fixtures read through `read_config`'s raw text):
      `set_type_tier` raises `ValueError` → CLI catches it, stderr reports refusal,
      non-zero exit, `openkos.yaml` byte-identical to before, no commit.
- [x] 2.5 GREEN: wire the read-text → `config.set_type_tier` → catch-`ValueError`
      path into the command (stderr message + exit 1 on raise, no partial state).
      Confirm 2.4 GREEN.
- [x] 2.6 RED: `tests/unit/cli/test_set_volatility.py` — idempotence: `Person` already
      mapped to `volatile` in the parsed `type_tiers` map → no-op message printed,
      exit 0, no read of `set_type_tier` write path, no file write, no commit.
      Explicit override equal to the REGISTRY default (not present in the parsed
      map) is NOT idempotent and proceeds as a real write (separate assertion).
- [x] 2.7 GREEN: add the idempotence short-circuit before the preview/confirm/write
      path, keyed off `cfg.type_tiers.get(concept_type) == tier`. Confirm 2.6 GREEN.
- [x] 2.8 RED: `tests/unit/cli/test_set_volatility.py` — preview line format
      `<ConceptType>: <old-or-default> -> <new>` printed before the confirm prompt on
      a valid, non-idempotent invocation.
- [x] 2.9 RED: `tests/unit/cli/test_set_volatility.py` — confirm-gate matrix (mirror
      `test_relate.py` TTY simulation): `--auto` skips prompt and writes directly;
      non-TTY + `cfg.review` → refusal, exit 1, no write; interactive decline (`n`)
      → no write, no commit; interactive accept (`y`) → write proceeds.
- [x] 2.10 GREEN: implement preview print + confirm gate
      (`if not auto and cfg.review: typer.confirm(abort=True)` else non-TTY refusal
      exit 1) exactly mirroring `relate`'s gate. Confirm 2.8-2.9 GREEN.
- [x] 2.11 RED: `tests/unit/cli/test_set_volatility.py` — successful confirmed write:
      `openkos.yaml` bytes match the expected post-edit text (reusing Phase 1 core
      behavior end-to-end); a new commit exists with message
      `openkos: set-volatility <Type> -> <tier>` covering `openkos.yaml`.
- [x] 2.12 GREEN: wire `fsio.write_atomic(config_path, new_text)` +
      `_autocommit(root, ["openkos.yaml"], f"openkos: set-volatility {concept_type}
      -> {tier}")` after a successful confirm. Confirm 2.11 GREEN.

## Phase 3: `suggest-volatility` Hint Update

Requirement: volatility-suggestion / "Workspace-Gated, Read-Only Per-Type
Suggestion" (MODIFIED)

- [x] 3.1 RED: `tests/unit/cli/test_suggest_volatility.py` — trailing report hint
      reads `Next: openkos set-volatility <ConceptType> <tier>` (no longer the
      hand-edit `type_tiers:` instruction).
- [x] 3.2 GREEN: update the hint line at `main.py:4379`. Confirm 3.1 GREEN; confirm
      no other `suggest-volatility` assertion (zero-writes, per-type tier/rationale
      output) regresses.

## Phase 4: Non-Regression + Quality Gate

- [x] 4.1 Run `uv run pytest` — full suite green, including unaffected `config`,
      `relate`, and `suggest-volatility` tests unchanged in behavior.
- [x] 4.2 Run `uv run ruff check . && uv run ruff format --check . && uv run mypy .`
      — quality gate green.
