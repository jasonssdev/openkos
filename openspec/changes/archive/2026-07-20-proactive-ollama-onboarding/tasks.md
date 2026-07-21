# Tasks: Proactive Ollama Onboarding (MVP-2 Onboarding UX)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~180-250 total (prod ~35-45, tests ~140-200, docs ~5-10) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | single PR |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

Rationale: four additive, localized edits confined to `src/openkos/cli/main.py` (no new modules, no schema/interface change, `llm/` untouched). Design's own rollout section concurs: "Under 400-line budget — single PR." Base branch `main` @ f478253.

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | All four edits (D1-D4) + full test matrix | PR 1 (single PR) | `uv run pytest tests/unit/cli/test_init.py tests/unit/cli/test_doctor.py tests/unit/cli/test_query.py tests/unit/cli/test_adjudicate.py tests/unit/cli/test_suggest_relations.py` | `uv run openkos init` in a scratch dir with Ollama stopped/started, then `uv run openkos doctor` | Revert the single commit/PR; each edit is independently removable text (no cross-file coupling) |

## Phase 1: Foundation — Shared Preflight Timeout (D1)

- [x] 1.1 GREEN: `src/openkos/cli/main.py` — add `_PREFLIGHT_TIMEOUT = 5.0` module constant right after `app = typer.Typer()` (~:40), carrying the existing "fast interactive diagnostic" comment from ~:2349-2350.
- [x] 1.2 GREEN: `src/openkos/cli/main.py` — replace the bare `timeout=5.0` at ~:2351 (doctor's `OllamaClient`) with `_PREFLIGHT_TIMEOUT`.

## Phase 2: Init Non-Fatal Preflight (D2)

- [x] 2.1 RED: `tests/unit/cli/test_init.py` — reachable + model present -> `openkos init` prints no Ollama warning, exit 0 (spec: workspace-init "both good -> no warning, exit 0").
- [x] 2.2 RED: `tests/unit/cli/test_init.py` — Ollama unreachable (`OllamaUnavailable` from probe) -> single warning naming `openkos doctor`, exit 0 (spec: "unreachable -> warn+exit0").
- [x] 2.3 RED: `tests/unit/cli/test_init.py` — reachable but `model_tag_matches` False (model missing) -> warning naming `openkos doctor`, exit 0 (spec: "model-missing -> warn+exit0").
- [x] 2.4 RED: `tests/unit/cli/test_init.py` — probe raises an unexpected `Exception` (not an `OllamaError` subclass) -> still non-fatal, warning, exit 0, no traceback leaks to stdout/stderr (spec: "probe-errors -> still non-fatal").
- [x] 2.5 RED: `tests/unit/cli/test_init.py` — assert the workspace files (`raw/`, `bundle/index.md`, `bundle/log.md`, `AGENTS.md`, `openkos.yaml`) exist identically across all four preflight outcomes (2.1-2.4) — pure-file-writer guarantee unchanged.
- [x] 2.6 RED: `tests/unit/cli/test_init.py` — assert `ollama.pull`/`ollama.serve`/any server-spawn call is never invoked by the preflight probe.
- [x] 2.7 GREEN: `src/openkos/cli/main.py` — after Phase B success (after ~:141), add the non-fatal probe: build `OllamaClient(model=resolved_model, timeout=_PREFLIGHT_TIMEOUT)`, call `list_models()` + `model_tag_matches()`, `except Exception` (not `BaseException`) sets `ready = False`; if not ready, `typer.echo(..., err=True)` naming `openkos doctor`; no `raise typer.Exit`, no pull/serve.

## Phase 3: Doctor Binary-Aware Remediation (D3)

- [x] 3.1 RED: `tests/unit/cli/test_doctor.py` — `shutil.which("ollama")` returns `None` + unreachable -> remediation is "no `ollama` binary found on PATH -- install from https://ollama.com" (covers both remedies per design), NEVER the string "not installed" (spec: "no-binary -> install msg, no over-claim").
- [x] 3.2 RED: `tests/unit/cli/test_doctor.py` — `shutil.which("ollama")` returns a path + unreachable -> remediation stays exactly `"ollama serve"` (spec: "binary-found-refused -> ollama serve").
- [x] 3.3 RED: `tests/unit/cli/test_doctor.py` — regression: reachable-pass, missing-model, outside-workspace checks unaffected byte-for-byte (spec: "missing-model (unchanged)", "outside-workspace (unchanged)").
- [x] 3.4 GREEN: `src/openkos/cli/main.py` — `import shutil` at top (used ONLY in this branch); in the `OllamaUnavailable` branch (~:2363-2372) gate `remediation` on `shutil.which("ollama") is None`; `detail=str(exc)` and the generic `OllamaError` branch (~:2373-2376) untouched.

## Phase 4: Verb Doctor Pointer (D4)

- [x] 4.1 RED: `tests/unit/cli/test_query.py` — `OllamaUnavailable` message ends with `" Or run \`openkos doctor\` to diagnose the environment."`, exit 1 preserved.
- [x] 4.2 RED: `tests/unit/cli/test_adjudicate.py` — same appended-pointer assertion for `adjudicate`, exit 1 preserved, zero writes.
- [x] 4.3 RED: `tests/unit/cli/test_suggest_relations.py` — same appended-pointer assertion for `suggest-relations`, exit 1 preserved, zero writes.
- [x] 4.4 RED: across all three test files — regression assertions that `OllamaModelNotFound`/generic `OllamaError` messages are byte-unchanged and specific-before-general ordering is preserved (spec: "ModelNotFound/OllamaError branches unchanged").
- [x] 4.5 GREEN: `src/openkos/cli/main.py` — append `" Or run \`openkos doctor\` to diagnose the environment."` to the `OllamaUnavailable` remediation strings at ~:1965-1971 (adjudicate), ~:2072-2078 (suggest-relations), ~:2202-2208 (query); touch ONLY these three `except OllamaUnavailable` blocks.

## Phase 5: Regression Sweep + Docs

- [x] 5.1 VERIFY: run `openkos.llm` config-free AST guard test unmodified, stays green (remediation text stays in `cli/`).
- [x] 5.2 VERIFY: `ingest` command/tests untouched — no diff in its degrade path or exit code.
- [x] 5.3 DOCS: `docs/cli.md` — document the init post-success preflight note and doctor's binary-aware remediation.
- [x] 5.4 VERIFY: `uv run pytest tests/unit/cli/test_init.py tests/unit/cli/test_doctor.py tests/unit/cli/test_query.py tests/unit/cli/test_adjudicate.py tests/unit/cli/test_suggest_relations.py` green; `uv run ruff check . && uv run ruff format --check .` and `uv run mypy --strict` clean on touched files.
- [x] 5.5 VERIFY: `uv run pytest --cov` full suite green, >= 90% branch coverage on touched code; confirm zero diff in `resolution/`, `graph/`, `model/`, `llm/` outside the reused `OllamaClient`/`list_models`/`model_tag_matches` call sites.
