# Tasks: `improve-ollama-onboarding` — actionable Ollama errors in `query` (Shape A)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~118 (`cli/main.py` ~26; `tests/unit/cli/test_query.py` ~90; `docs/cli.md` ~2) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR — one cohesive except-split across `main.py`/tests/docs, all additive |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Split `query`'s combined except into ordered `OllamaUnavailable` / `OllamaModelNotFound` / `(FtsUnavailable, OllamaError)` handlers + tests + docs | PR 1 (single PR, under budget) | `uv run pytest tests/unit/cli/test_query.py` | `CliRunner` monkeypatching `openkos.cli.main.answer` to raise each exception class — no live Ollama process (per design's existing seam) | `git revert`; additive except-branch split only, no schema/config/state/exit-code change |

## Phase 1: RED — except-ordering and message tests
- [x] 1.1 `tests/unit/cli/test_query.py`: update imports (L17/19) — add `OllamaError, OllamaModelNotFound` alongside existing `OllamaClient, OllamaUnavailable` from `openkos.llm.ollama`.
- [x] 1.2 same file: extend `test_query_ollama_unavailable_maps_to_exit_one` (L177) — assert stderr ALSO contains `ollama serve` in addition to the existing `Ollama not reachable` assertion (Scenario: Ollama backend unreachable).
- [x] 1.3 same file: `test_query_model_not_found_maps_to_exit_one` — monkeypatch `answer` to raise `OllamaModelNotFound(...)`; init workspace with a KNOWN configured model tag (e.g. via `openkos.yaml`/config default); assert stderr contains `is not installed` AND the literal `ollama pull <that exact model tag>`, exit 1, no `Traceback` (Scenario: Configured model not installed).
- [x] 1.4 same file: assert in 1.3 (or a dedicated case) that the ACTUAL configured model name is interpolated into the pull command — not a hardcoded/placeholder tag — so a wrong-model regression fails (design's configured-model-in-pull-message risk).
- [x] 1.5 same file: `test_query_generic_ollama_error_maps_to_exit_one` — monkeypatch `answer` to raise a plain `OllamaError("boom")`; assert stderr has the unchanged generic `failed -- boom.` message with NO `ollama serve`/`ollama pull` text, exit 1 (Scenario: Other Ollama error).
- [x] 1.6 same file: confirm `test_query_fts_unavailable_maps_to_exit_one` (L199) still asserts the generic message unchanged — pin as regression guard (Scenario: FTS index unavailable).
- [x] 1.7 same file: `test_query_specific_ollama_subclasses_do_not_fall_through_to_generic` — for BOTH `OllamaUnavailable` and `OllamaModelNotFound`, assert stderr does NOT contain the bare generic `failed -- {exc}.` shape without remediation text, proving each subclass reaches its OWN handler and not the tuple fallback (direct RED test for D1 handler ordering).
- [x] 1.8 Run `uv run pytest tests/unit/cli/test_query.py` — confirm 1.2–1.7 fail RED (production code still has the single combined handler).

## Phase 2: GREEN — split the except block
- [x] 2.1 `src/openkos/cli/main.py`: update import (L15) to `from openkos.llm.ollama import OllamaClient, OllamaError, OllamaModelNotFound, OllamaUnavailable`.
- [x] 2.2 same file: replace L700-704's single `except (FtsUnavailable, OllamaError)` with the 3 ordered handlers from design's Interfaces snippet — `except OllamaUnavailable` (append `ollama serve` remediation to `{exc}`) → `except OllamaModelNotFound` (author message from `cfg.model`, `ollama pull {cfg.model}`) → `except (FtsUnavailable, OllamaError)` (unchanged generic). All three: `typer.echo(..., err=True)`, `raise typer.Exit(code=1) from exc`.
- [x] 2.3 Run `uv run pytest tests/unit/cli/test_query.py` — confirm all Phase 1 tests GREEN, no regressions in the untouched query tests (L30-173).

## Phase 3: Docs
- [x] 3.1 `docs/cli.md` L84: reword the clause "a failure to reach Ollama … is caught and reported on stderr (exit 1), never a raw traceback" to note that an unreachable Ollama and a not-installed configured model now print actionable guidance (`ollama serve` / `ollama pull <model>`); one-line prose only, no command-table change.

## Phase 4: Verification Gate
- [x] 4.1 `uv run pytest --cov` — full suite green; ≥90% branch coverage on changed lines.
- [x] 4.2 `uv run ruff check .` && `uv run ruff format --check .` — clean.
- [x] 4.3 `uv run mypy .` — clean (strict).
