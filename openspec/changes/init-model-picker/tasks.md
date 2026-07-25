# Tasks: Interactive Model Picker for `openkos init` (Slice B, closes #128)

## Review Workload Forecast

Split delivery: two sequential PRs, sequential-to-main (B-i merges before B-ii branches).

### Sub-slice B-i — list_models widening + doctor adaptation

| Field | Value |
|-------|-------|
| Estimated changed lines | ~215 (prod ~55: ollama.py ~45, main.py ~10; tests ~160: test_ollama.py ~80, test_doctor.py ~30, plus init preflight-related asserts ~50) |
| 400-line budget risk | Low |
| Chained PRs recommended | Yes (part of 2-PR chain) |
| Suggested split | PR 1 of 2 |
| Delivery strategy | SPLIT (user chose) |
| Chain strategy | sequential-to-main |

### Sub-slice B-ii — picker

| Field | Value |
|-------|-------|
| Estimated changed lines | ~205 (prod ~55: main.py `_pick_chat_model` + `_resolve_model` TTY branch; tests ~150: test_init.py) |
| 400-line budget risk | Low |
| Chained PRs recommended | Yes (part of 2-PR chain) |
| Suggested split | PR 2 of 2, depends on B-i merged |
| Delivery strategy | SPLIT (user chose) |
| Chain strategy | sequential-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: sequential-to-main
400-line budget risk: Low
```

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| B-i | Widen `list_models()` return type, add `InstalledModel`/`is_embedding_model`, adapt doctor + init preflight to new shape | PR 1 | `uv run pytest tests/unit/llm/test_ollama.py tests/unit/cli/test_doctor.py` | `openkos doctor` against local Ollama or stub | revert PR 1 branch; `list_models()` reverts to `list[str]`, no persisted-data impact |
| B-ii | Add `_pick_chat_model()` picker in `_resolve_model` TTY branch | PR 2 (base: main, post B-i merge) | `uv run pytest tests/unit/cli/test_init.py` | `openkos init` on TTY with Ollama running | revert PR 2 branch only; B-i behavior unaffected |

---

## Sub-slice B-i: list_models widening + doctor adaptation (PR 1 — merges to main first)

### Phase 1: Foundation — `src/openkos/llm/ollama.py`

- [x] 1.1 RED: add tests in `tests/unit/llm/test_ollama.py` — `InstalledModel(tag, family)` frozen dataclass exists; `is_embedding_model()` returns True for family "bert"/"nomic-bert" (case-insensitive), False for unknown/None family.
- [x] 1.2 GREEN: implement `@dataclass(frozen=True, slots=True) class InstalledModel: tag: str; family: str | None` and `_EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert"})` + `is_embedding_model(model) -> bool` in `ollama.py`.
- [x] 1.3 RED: extend `test_ollama.py` — `list_models()` returns `list[InstalledModel]`; entry with `details.family` parsed; entry missing `details`/`family` still returned with `family=None`; `model`-or-`name` tag fallback preserved; convert existing `== list[str]` assertions to compare against `InstalledModel` instances.
- [x] 1.4 GREEN: widen `list_models()` to parse `entry.get("details", {}).get("family")` guarded, return `list[InstalledModel]`; keep malformed-skip and `OllamaError`/`OllamaUnavailable` mapping unchanged.

### Phase 2: Call-site adaptation (behavior-preserving)

- [x] 2.1 RED: extend `tests/unit/cli/test_doctor.py` — update fake stub to return `InstalledModel`; assert checks 3/4/5 pass/fail outcomes identical to pre-change behavior.
- [x] 2.2 GREEN: in `src/openkos/cli/main.py` doctor checks (~5557-5636), keep `model_tag_matches(tag, list[str])` signature; build `installed_tags = [m.tag for m in installed]` once after check 3, pass to checks 4/5.
- [x] 2.3 RED: add/extend init test covering post-write preflight (main.py ~333-352) with `InstalledModel` shape.
- [x] 2.4 GREEN: update preflight call site to `model_tag_matches(resolved_model, [m.tag for m in probe.list_models()])`.

### Phase 3: Quality Gate (B-i)

- [x] 3.1 Run `uv run pytest` — full suite green.
- [x] 3.2 Run `uv run ruff check . && uv run ruff format --check .`.
- [x] 3.3 Run `uv run mypy .`.

---

## Sub-slice B-ii: picker (PR 2 — branches off main AFTER B-i merges)

### Phase 4: Picker implementation — `src/openkos/cli/main.py`

- [ ] 4.1 RED: extend `tests/unit/cli/test_init.py` — `_fake_ollama_client` returns `InstalledModel` list; test numbered list rendered with chat models, `DEFAULT_MODEL` marked "(recommended)".
- [ ] 4.2 GREEN: implement `_pick_chat_model() -> str` — probe Ollama, filter via `is_embedding_model`, ensure `DEFAULT_MODEL` present, print numbered list.
- [ ] 4.3 RED: test Enter (empty input) selects `DEFAULT_MODEL`; test numeric choice maps to corresponding tag.
- [ ] 4.4 GREEN: implement bounded `typer.prompt` reprompt loop mapping empty→recommended, in-range digit→tag.
- [ ] 4.5 RED: test invalid input reprompts until valid; result still passes through `validate_model`.
- [ ] 4.6 GREEN: wire invalid-input reprompt + `validate_model` call on chosen tag.
- [ ] 4.7 RED: test `--model` flag bypasses picker entirely (no probe call).
- [ ] 4.8 GREEN: confirm `_resolve_model` short-circuits before `_pick_chat_model` when `--model` given.
- [ ] 4.9 RED: test non-TTY path silently returns `DEFAULT_MODEL`, no picker invoked.
- [ ] 4.10 GREEN: confirm non-TTY branch bypasses `_pick_chat_model`.
- [ ] 4.11 RED: test Ollama unreachable → falls back to existing typed-prompt/default flow, no hard-fail, workspace still created, exit 0.
- [ ] 4.12 RED: test zero chat models after embedding filter → same fallback behavior.
- [ ] 4.13 GREEN: wrap probe + filter in broad `except Exception`; on exception or zero candidates, fall back to `typer.prompt("Model", default=DEFAULT_MODEL)`.
- [ ] 4.14 RED: test embedding model (e.g. `bge-m3`, family "bert") absent from numbered list while chat model (e.g. `qwen3:8b`) present.
- [ ] 4.15 GREEN: confirm embedding filter applied before rendering list (should already pass from 4.13; add if gap found).

### Phase 5: Quality Gate (B-ii)

- [ ] 5.1 Run `uv run pytest` — full suite green.
- [ ] 5.2 Run `uv run ruff check . && uv run ruff format --check .`.
- [ ] 5.3 Run `uv run mypy .`.

## Notes

- B-ii depends on `InstalledModel`/`is_embedding_model` from B-i being merged to main; do not branch B-ii until B-i lands.
- Threat matrix: N/A per design (no new routing/shell/subprocess/VCS/executable-classification); safe-degradation behavior (4.11/4.12) captured as RED tests per spec's CRITICAL scenario.
