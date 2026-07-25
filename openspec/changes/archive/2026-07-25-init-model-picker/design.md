# Design: Interactive Model Picker for `openkos init`

Slice B of #128 (final slice, CLOSES #128). Implements USER-LOCKED Approach A: widen
`list_models()` to preserve `details.family`, filter picker candidates by family.

## Technical Approach

Change `OllamaClient.list_models()` from `list[str]` to `list[InstalledModel]` carrying
`tag + family`. A single `is_embedding_model()` helper classifies each entry. The `init`
Phase-A model resolution gains a numbered picker over family-filtered chat candidates,
probing Ollama BEFORE any write with a broad-except fallback to the existing typed prompt.
Doctor checks 3/4/5 adapt to the new shape behavior-preserving by extracting `.tag`.

## Architecture Decisions

### Decision 1 (ADR-worthy): widened return type

**Choice**: `list_models() -> list[InstalledModel]` where `InstalledModel` is a
`@dataclass(frozen=True, slots=True)` with `tag: str` and `family: str | None`.
**Alternatives**: `list[dict]` (rejected: no mypy-strict guarantees, stringly-typed keys);
`NamedTuple` (viable, but dataclass reads clearer and extends cleanly for future capability
fields, matching the proposal's "unlocks capability-aware doctor later" goal).
**Rationale**: repo is mypy-strict; a named frozen type gives attribute access (`.tag`,
`.family`), immutability, and a stable extension point. Parsing keeps the D2 field variance
`entry.get("model") or entry.get("name")` for `tag`; family is read as
`entry.get("details", {}).get("family")` guarded so a missing `details`/`family` yields
`family=None`. Malformed-entry skipping and the existing `OllamaError` wrapping are unchanged.

**ADR note**: this is a breaking change to a public client method, but confined to two
in-repo call sites (init preflight, doctor) with no external consumers. This design's
Architecture Decisions section serves as the ADR record; no standalone ADR file is required.

### Decision 2: embedding-family classification

**Choice**: module-level `_EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert"})` plus a
single testable helper `is_embedding_model(model: InstalledModel) -> bool` returning
`model.family is not None and model.family.lower() in _EMBEDDING_FAMILIES`.
**Rationale**: bge-m3/mxbai-embed/all-minilm report family `"bert"`; nomic-embed-text reports
`"nomic-bert"`. Unknown or missing family (`None`) is treated as NON-embedding (included) —
never exclude on ambiguity (proposal risk). Case-insensitive. Lives in `ollama.py` beside the
type so both the picker and future doctor reuse it.

### Decision 3: picker rendering + input

**Choice**: keep `_resolve_model(flag)` as the precedence gate; the TTY branch delegates to a
new `_pick_chat_model() -> str`. Precedence preserved: `--model` wins outright (no probe, no
picker); non-TTY returns `DEFAULT_MODEL` silently. `_pick_chat_model` probes Ollama, filters
via `is_embedding_model`, ensures `DEFAULT_MODEL` is present (prepend if absent) and marks it
`(recommended)`, prints a numbered list, and reads a choice with `typer.prompt("Model",
default=<recommended-number>)` in a bounded reprompt loop: empty/Enter → recommended tag;
in-range digit → that tag; invalid → stderr error + reprompt. The chosen tag still passes
through `config.validate_model`.
**Fallback**: the probe (`OllamaClient(...).list_models()`) is wrapped in `except Exception`;
unreachable Ollama OR zero chat candidates → fall back to the existing
`typer.prompt("Model", default=DEFAULT_MODEL)`. NEVER hard-fails; mirrors the post-write
preflight tolerance (main.py:341-345). Runs in Phase A, before any write.

### Decision 4: doctor adaptation (minimal, behavior-preserving)

**Choice**: leave `model_tag_matches(configured, installed: list[str])` unchanged. After check 3
build `installed_tags = [m.tag for m in installed]` once; checks 4 and 5 call
`model_tag_matches(model, installed_tags)`. Check 3's `detail=f"{len(installed)} models"` is
unchanged. Init preflight line 343 becomes `model_tag_matches(resolved_model, [m.tag for m in
probe.list_models()])`. Confines the contract change to `.tag` extraction; identical pass/fail.

## Data Flow

    init (Phase A, TTY)                    doctor
      │                                      │
      _pick_chat_model()                     list_models() ─→ [InstalledModel]
      │  probe.list_models() ─→ [InstalledModel]                │
      │  filter is_embedding_model()                            [m.tag ...]
      │  numbered prompt ─→ tag                                 model_tag_matches
      └─ validate_model ─→ write config                        └─ CheckResult

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/llm/ollama.py` | Modify | Add `InstalledModel`, `_EMBEDDING_FAMILIES`, `is_embedding_model`; widen `list_models` to parse `details.family` |
| `src/openkos/cli/main.py` | Modify | `_pick_chat_model` + `_resolve_model` TTY branch; doctor 3/4/5 `.tag` extraction; preflight line 343 |
| `tests/unit/llm/test_ollama.py` | Modify | Update 3 existing assertions; add family-parse + `is_embedding_model` tests |
| `tests/unit/cli/test_init.py` | Modify | Extend `_fake_ollama_client` to `InstalledModel`; add picker tests |
| `tests/unit/cli/test_doctor.py` | Modify | Update fake stub to `InstalledModel`; behavior-preserving assertions |

## Interfaces / Contracts

```python
@dataclass(frozen=True, slots=True)
class InstalledModel:
    tag: str
    family: str | None

def is_embedding_model(model: InstalledModel) -> bool: ...
def list_models(self) -> list[InstalledModel]: ...  # was list[str]
```

## Testing Strategy (strict TDD — RED first)

| Layer | RED tests |
|-------|-----------|
| ollama unit | family parsed into `InstalledModel`; missing `details` → `family=None`; missing `family` key → `None`; `name`-field fallback keeps family; malformed skipped; update 3 existing list-of-tags assertions to `InstalledModel` |
| ollama unit | `is_embedding_model`: `bert`→True, `nomic-bert`→True, `qwen`→False, `None`→False, case-insensitive |
| init CLI | numbered list rendered; Enter picks `DEFAULT_MODEL`; numeric pick maps to tag; invalid→reprompt→valid; `--model` bypass (no probe); non-TTY default (no picker); unreachable→typed-prompt fallback; zero-chat-models (only embeddings)→fallback; embedding excluded from list |
| doctor CLI | checks 4/5 pass/fail identical with widened shape; check 3 detail string unchanged |

## Threat Matrix

N/A — no new routing, shell command, subprocess spawn, VCS/PR automation, or executable-file
classification. The picker reuses the existing Ollama HTTP probe (an existing integration
point). Its safe-behavior requirement (probe failure/zero-candidates degrades to typed prompt,
never hard-fails, exit 0, workspace still created) is captured as RED tests above.

## Migration / Rollout

No migration. No persisted-data or config-format change. Rollback = revert branch,
`list_models` returns to `list[str]`.

## Sizing & Delivery — SPLIT recommended

Per-file changed-line estimate: ollama.py ~45; main.py doctor+preflight ~10; main.py picker
~55; test_ollama.py ~80; test_init.py ~150; test_doctor.py ~30. Total ~370 lines — under 800,
at/above the 400 review budget.

**Recommendation: SPLIT into two stacked PRs** (matches repo Slice 2b-i/2b-ii convention):
- **B-i**: `InstalledModel` + `is_embedding_model` + widened `list_models` + doctor/preflight
  `.tag` adaptation + test_ollama.py + test_doctor.py (~215 lines). Self-contained, behavior-
  preserving, independently green. Must merge first.
- **B-ii**: picker (`_pick_chat_model`/`_resolve_model`) + test_init.py (~205 lines). Depends on
  B-i's `InstalledModel`/`is_embedding_model`.

Each slice stays at/under the 400-line budget with a clean start/finish, autonomous scope, and
independent verification. A single PR would sit near the budget ceiling and mix a contract
change with UX — the split keeps review load and rollback surface bounded.

## Open Questions

None blocking. Approach A, split delivery, and dataclass return type are decided.
