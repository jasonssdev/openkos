# Tasks: `openkos init` — user-selectable local model

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~260-340 (2 source files + 1 template line + 2 test files + docs) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Land `--model` end-to-end (template, validation, write_config, CLI resolution, docs) as one slice | PR 1 | `uv run pytest tests/unit/test_config.py tests/unit/cli/test_init.py` | `uv run openkos init --model gemma3` and `uv run openkos init` on a TTY in a scratch dir | `git revert` the single commit range; purely additive, no persisted state |

## Phase 1: Validation primitive

- [x] 1.1 RED — in `tests/unit/test_config.py`, add tests for `config.validate_model`: trims and returns `"qwen3:8b"`/`"mistral:7b"` unchanged (colon allowed); raises `ValueError` for `""`, `"  "`, `"a b"`, `'a"b'`, `"a'b"`, `"a#b"`, `"a\nb"`. Confirm it fails (no such function).
- [x] 1.2 GREEN — in `src/openkos/config.py`, add `DEFAULT_MODEL = "qwen3:8b"` and `validate_model(tag: str) -> str` (strip; reject empty/whitespace/quote/`#`/newline). Confirm 1.1 passes.

## Phase 2: Template placeholder + `write_config` substitution

- [x] 2.1 Modify `src/openkos/templates/openkos.yaml.template` line 1: `model: qwen3:8b` → `model: __OPENKOS_MODEL__`, keep trailing spaces + comment verbatim.
- [x] 2.2 RED — in `tests/unit/test_config.py`, redefine `test_write_config_byte_identical` and `test_write_config_ignores_directory_name` expected bytes as the template with `__OPENKOS_MODEL__` replaced by `DEFAULT_MODEL`; add `test_write_config_custom_model` (`model="gemma3"` → file contains `model: gemma3`); add `test_write_config_rejects_invalid_model` (blank/unsafe → `ValueError`, no file written). Confirm failures.
- [x] 2.3 GREEN — in `config.py`, change `write_config(root: Path, model: str = DEFAULT_MODEL) -> None` (line ~144): call `validate_model(model)`, assert `template.count("__OPENKOS_MODEL__") == 1`, `template.replace(...)`, write via `fsio.write_exclusive`. Confirm 2.2 passes and prior byte/CR/newline tests still hold.
- [x] 2.4 REFACTOR — update `write_config`'s docstring (config.py:144-151) to describe the single-token substitution invariant, replacing the "no substitution" claim.

## Phase 3: CLI flag + TTY-prompt resolution

- [x] 3.1 RED — in `tests/unit/cli/test_init.py`, add: flag writes chosen model; flag wins over TTY prompt (no prompt shown); TTY prompt accepts default (`input="\n"`); TTY prompt custom value (`input="mistral\n"`); non-TTY silent default (assert `openkos.yaml` content); blank/unsafe flag input rejected (parametrized `""`, `" "`, `"a b"`, `'a"b'`, `"a'b"`, `"a#b"`) — exit 1, stderr "refusing", `_snapshot` unchanged, no `openkos.yaml`. Confirm all fail (no `--model` option).
- [x] 3.2 GREEN — in `src/openkos/cli/main.py`: add `import sys`; add `_resolve_model(flag: str | None) -> str` (flag → `config.validate_model`; else `sys.stdin.isatty()` → `typer.prompt("Model", default=config.DEFAULT_MODEL)` then validate; else `config.DEFAULT_MODEL`); add `model: str | None = typer.Option(None, "--model")` to `init()`; resolve + catch `ValueError` into the existing refusal message (`"openkos init: refusing to initialize -- {reason}."`, exit 1) before any write; pass resolved model to `config.write_config(root, model=resolved)`. Confirm 3.1 passes, no regression.
- [x] 3.3 REFACTOR — update `init()`'s docstring (main.py:20-45) to document `--model` resolution order and the new invalid-model refusal case.

## Phase 4: Documentation

- [x] 4.1 Update `docs/cli.md:50` — replace the deferred "Model selection during `init` is deferred; see `add-model-selection`" sentence with the actual `--model` flag / TTY-prompt / default (`qwen3:8b`) behavior.

## Phase 5: Verification Gate

- [x] 5.1 `uv run pytest --cov` — full suite green, branch coverage ≥90%.
- [x] 5.2 `uv run ruff check .` and `uv run ruff format --check .` — clean.
- [x] 5.3 `uv run mypy .` — clean (strict mode).
- [x] 5.4 `uv build` + wheel smoke test, matching the repo's existing changes.
