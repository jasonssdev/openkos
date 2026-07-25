# Design: set-volatility write verb (#140)

## Technical Approach

Ship `openkos set-volatility <ConceptType> <tier>` as a `relate`-shaped two-phase write verb whose file edit delegates to a new PURE, comment-safe text-surgery core `config.set_type_tier(yaml_text, concept_type, tier) -> str`. The core never touches disk (unit-testable, reusable by a future `suggest-volatility --apply`); the CLI owns read + preview + confirm + `fsio.write_atomic` + `_autocommit`. No PyYAML round-trip, no new dependency.

## Architecture Decisions

### Decision: Pure text→text core, ValueError on any un-editable shape
**Choice**: `config.set_type_tier(yaml_text: str, concept_type: str, tier: str) -> str`; raises `ValueError`; returns new text (byte-identical if already at target). Placed in `config.py` (config-file concern); adds `from openkos.model import types`.
**Alternatives**: (a) path-in/write-in-place — rejected, couples I/O + surgery, harder to test and reuse; (b) PyYAML `safe_load`→`dump` — rejected, destroys every comment (#128 footgun); (c) `ruamel.yaml` — rejected, new dependency + reflow risk for one key.
**Rationale**: mirrors `write_config`'s constrained `str.replace` philosophy; `ValueError` matches the existing `except (OSError, ValueError)` convention in `read_config`/`relate`.

### Decision: Vocabulary validated against full 10-entry REGISTRY
**Choice**: `concept_type ∈ {ot.name for ot in types.REGISTRY}` (incl `Source`), `tier ∈ types.VOLATILITY_TIERS`. Validated in CLI (clean listing message) AND in core (defense-in-depth for `--apply` reuse).
**Rationale**: `suggest-volatility` can emit a `Source` suggestion; `CLASSIFIABLE_TYPES` (9) would wrongly reject it. Write-time is the ONLY gate (`read_config` is raw passthrough, config.py:345-351).

### Decision: Idempotence detected in CLI via parsed map, not the core
**Choice**: no-op ONLY when `cfg.type_tiers.get(concept_type) == tier` (an explicit file entry already equals target) → message + exit 0, no core call, no write, no commit.
**Rationale**: writing an explicit override equal to the registry DEFAULT is still a real file change, so idempotence keys off the parsed map, not effective tier. Avoids an empty autocommit.

## Text-Surgery Algorithm (load-bearing)

Operate on `yaml_text.splitlines(keepends=True)`. A block header is a column-0 line matching `^type_tiers:\s*(#.*)?$` (leading `#` never matches → the commented template stays case c). Block body = following lines more-indented than col 0, plus interleaved blank/comment lines; ends at the next col-0 non-blank/non-comment key. Entry regex: `^(?P<indent>\s+)(?P<key>Name):(?P<sep>\s*)(?P<val>\S+)(?P<rest>\s*(#.*)?)$`.

- **(a) block present, entry for Type exists** → rewrite ONLY that line's `val`, preserving `indent`/`sep`/`rest` (trailing comment kept). Everything else byte-identical.
- **(b) block present, no Type entry** → insert `{indent}{Type}: {tier}\n` after the last real entry, using the block's canonical indent. **Empty block** (header only) → insert with fixed 2-space indent (template convention) right after the header.
- **(c) no uncommented header** (default case; template ships it commented) → append `type_tiers:\n  {Type}: {tier}\n` at EOF (after ensuring one trailing newline). EOF append is deterministic; uncommenting the template block or inserting after a named key is fragile.

**FAIL CLOSED (raise `ValueError`, no edit):**
| Shape | Detection |
|-------|-----------|
| Inline flow mapping `type_tiers: {…}` | header trailing content starts with `{` |
| Multiple `type_tiers:` keys | header regex matches >1 |
| Non-mapping scalar (`type_tiers: foo` / `[…]` / `null`) | header trailing non-comment scalar |
| Tab-indented block | any entry `indent` contains `\t` |
| Inconsistent entry indent | later entry indent ≠ first entry indent |
| Duplicate Type entry in block | Type matches >1 entry |

## Data Flow

    CLI set-volatility ─validate vocab─→ read_config (idempotence map)
        │ read openkos.yaml text                     │ no-op → exit 0
        ▼                                            ▼
    config.set_type_tier(text,...) ──raise ValueError──→ stderr + exit 1
        │ new text
        ▼ preview (Type: old -> new) ─→ confirm gate (--auto/cfg.review/TTY)
        ▼ fsio.write_atomic(config_path) ─→ _autocommit(root,["openkos.yaml"],msg)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/config.py` | Modify | Add pure `set_type_tier` core + `from openkos.model import types` |
| `src/openkos/cli/main.py` | Modify | New `set-volatility` command (mirror relate 2166-2354); hint line 4379 |
| `tests/unit/test_config.py` | Modify | Core: cases a/b/b-empty/c, idempotent, 6 fail-closed (text→raises) |
| `tests/unit/cli/test_set_volatility.py` | Create | CLI: mirror test_relate.py (CliRunner, tmp workspace, TTY sim) |

## Interfaces / Contracts

```python
def set_type_tier(yaml_text: str, concept_type: str, tier: str) -> str:
    """Return openkos.yaml text with type_tiers[concept_type]=tier, comments
    preserved. Raises ValueError on invalid vocab or any un-editable shape."""
```

CLI commit message (convention-corrected): `openkos: set-volatility <ConceptType> -> <tier>`.
Hint (main.py:4379): `Next: openkos set-volatility <ConceptType> <tier>`.

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit (core) | 3 edit cases + empty block + idempotent identity + 6 fail-closed | before/after text fixtures asserting exact bytes + comment preservation; each fail-closed asserts `ValueError` and no partial output |
| Unit (CLI) | valid write, invalid type/tier exit 1 no-write, unparseable config exit 1 no-write, idempotent exit 0 no-write/no-commit, non-TTY refusal, autocommit landed | `CliRunner`, `_init_workspace` via `init`, `_simulate_tty` monkeypatch, assert `openkos.yaml` bytes + `git log` |

Strict TDD: every case above is a RED test first.

## Threat Matrix

| Boundary | Applicable | Safe/failure behavior + RED test |
|----------|-----------|----------------------------------|
| Config file mutation (text surgery) | Yes | Any un-editable shape fails closed (raise, workspace untouched); atomic write; before/after fixtures |
| VCS automation (`_autocommit`) | Yes (reused unchanged) | Scoped `git add -- openkos.yaml`; non-fatal WARNING on failure (main.py:156-193); no change to primitive |
| Shell/subprocess/routing/exec-classification | N/A | No new subprocess or routing; git only via existing `_autocommit` |

## Migration / Rollout

No migration. Additive command + core + one hint line; autocommit is a standard revertible commit.

## Open Questions

- [ ] Commit-message prefix: proposal locked `set-volatility: <Type> -> <tier>`; design uses `openkos: set-volatility <Type> -> <tier>` to match every other verb (relate main.py:2353). Confirm during apply.
