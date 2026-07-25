# Exploration: set-volatility (#140)

Add a write verb `set-volatility <ConceptType> <tier>` that safely edits
`type_tiers:` in `openkos.yaml`, completing the advisor→action pattern for
`suggest-volatility`. Scope: the core write verb ONLY; `suggest-volatility
--apply` deferred (a shared core makes it cheap later).

## Current State
- `Config.type_tiers: dict[str, str]` (`src/openkos/config.py:345-409`) is raw
  passthrough — NO read-time validation. Write-time validation in the new verb is
  the ONLY gate before `lint.window_for_doc` consumes it.
- Valid tiers: `types.VOLATILITY_TIERS = {"static","slow","volatile"}`
  (`model/types.py:77-80`).
- Valid ConceptType: the full 10-entry `types.REGISTRY` (`model/types.py:36-47`),
  NOT the 9-entry `CLASSIFIABLE_TYPES` (which excludes `Source`).
  `TYPE_TO_DEFAULT_VOLATILITY` covers all 10 incl. `Source`, and
  `suggest_volatility()` (`resolution/volatility_typing.py:161-165`) groups by any
  frontmatter `doc.type`, so a `Source` suggestion is legitimate and must settable.

## CRITICAL: comment-safe edit
- `openkos.yaml.template` (`src/openkos/templates/openkos.yaml.template:1-13`) ships
  `type_tiers:` COMMENTED OUT, every line hand-commented.
- `read_config` uses plain PyYAML (`config.py:16,376`) — does NOT preserve
  comments/order on round-trip dump. No `ruamel.yaml` in the repo.
- `write_config` (`config.py:295-320`) only exclusive-creates a fresh template with
  one placeholder substitution — there is NO safe edit-in-place mechanism today.
- A naive `safe_load`→mutate→`dump` would destroy every comment — the exact footgun
  #140/#128 warn against.

## Recommended approach
Line/regex TEXT SURGERY on the raw `openkos.yaml` text (comment-safe, no new
dependency): locate/insert the `type_tiers:` block and the target type's line,
write via `fsio.write_atomic` (`fsio.py:32-62`). Extract into a callable core
`config.set_type_tier(...)` from day one (same reason `merge_core` was extracted
before `adjudicate --apply`), so a future `suggest-volatility --apply` reuses it.
MUST fail closed (refuse to write, never corrupt) on any `openkos.yaml` shape it
can't confidently parse — including the type_tiers-block-absent/commented case
(add the block) vs present (update/insert the type's line).

## Write-verb pattern to mirror
`relate` (`main.py:2166-2354`): Phase A validate/build-in-memory → preview →
confirm gate (`--auto`/`cfg.review`/TTY, `2324-2333`) → Phase B `write_atomic` +
`_autocommit` (`156-`). `set-volatility` mirrors this.

## Also update
`suggest-volatility` (`main.py:4267-4379`) — change the `Next: edit type_tiers in
openkos.yaml` hint (line 4379) to `Next: openkos set-volatility <ConceptType> <tier>`.

## Affected areas
- `src/openkos/config.py` — new comment-safe `set_type_tier(...)` core.
- `src/openkos/cli/main.py` — new `set-volatility` command + suggest-volatility hint.
- `tests/unit/test_config.py`, `tests/unit/cli/test_relate.py` — test conventions
  (CliRunner + tmp_path + TTY simulation).

## Risks
- Text surgery must fail closed on unexpected `openkos.yaml` shapes.
- Validate ConceptType against full 10-entry `REGISTRY`, not `CLASSIFIABLE_TYPES`.
- No downstream re-validation of `type_tiers` — write-time validation is the sole gate.
- Handle both states: type_tiers block absent/commented (add) vs present (update).

## Deferred (future slice)
`suggest-volatility --apply` interactive walk (clone `_run_adjudicate_apply` shape,
calling the shared `set_type_tier` core).
