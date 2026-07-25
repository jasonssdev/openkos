# Proposal: Interactive Model Picker for `openkos init`

> Slice B of issue #128 (final slice — CLOSES #128). Builds on shipped Slice A
> (`config-model-hardening`: `validate_model` reserved-word rejection,
> `read_config` type-check, doctor never crashes on non-str model).

## Intent

`openkos init` asks for the chat model via a free-text `typer.prompt` that
accepts anything typed, offers no discovery of installed models, and can steer
users onto embedding models (e.g. `bge-m3`) that cannot chat. Replace it with a
numbered picker over installed chat models, so onboarding is guided and correct.
Doing this robustly requires the client to expose per-model family, which also
unlocks capability-aware doctor checks later.

## Scope

### In Scope
- **Widen `OllamaClient.list_models()`** (ollama.py:236-276) to return per-model
  detail preserving at least tag + `details.family` from `/api/tags`, instead of
  `list[str]`. Keep D2 field variance (`model` or `name`). Return type decided in
  design.
- **Adapt doctor call sites** (checks 3/4/5, main.py:5557-5634) to the new shape —
  behavior-preserving (still plain tag matching via `model_tag_matches`).
- **Interactive picker in `openkos init`** — replace `_resolve_model`'s free-text
  prompt (main.py:103-121) with a numbered list over installed CHAT models
  (family-filtered; embedding models excluded). Mark `DEFAULT_MODEL` (qwen3:8b) as
  recommended; Enter picks it. Persist as today.

### Out of Scope
- Config-layer hardening (shipped in Slice A).
- Making doctor capability-aware (family enables it; not done here).
- Changing embedding-model resolution or `--embedding-model` flow.
- Pulling/installing models from the picker.

## Capabilities

### New Capabilities
- None

### Modified Capabilities
- `llm-client`: `list_models()` returns per-model detail (tag + family), not `list[str]`.
- `workspace-init`: interactive numbered chat-model picker replaces free-text prompt; degrade + non-interactive rules preserved.
- `doctor-command`: checks 3/4/5 consume the new `list_models()` shape (behavior unchanged).

## Approach

Family from `/api/tags` (`bert` → embedding, `qwen`/`llama` → chat) filters
candidates. Picker UX sketch (issue #128):

```
Select a model:
  1) qwen3:8b   (recommended)
  2) llama3.1:8b
  3) mistral:7b
Model [1]:
```

Precedence unchanged: `--model <tag>` wins outright (no picker); non-TTY silently
takes the default. The picker probes Ollama in Phase A (before any write) — if the
server is unreachable OR reports zero chat models, fall back to the typed prompt /
packaged default. NEVER hard-fail; workspace is still created.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/openkos/llm/ollama.py` | Modified | `list_models()` widened |
| `src/openkos/cli/main.py` | Modified | `_resolve_model`/`init` picker; doctor checks 3/4/5 |
| `tests/unit/llm/test_ollama.py` | Modified | new return shape (`urlopen`/`_tags_body` layer) |
| `tests/unit/cli/test_init.py` | Modified | picker + degrade tests (reuse `_fake_ollama_client`) |
| `tests/unit/cli/test_doctor.py` | Modified | adapted-shape assertions |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Contract change ripples to doctor + ollama tests | High | Behavior-preserving doctor adaptation; update both test layers |
| Phase-A probe timing differs from post-write preflight — must degrade before prompt | Med | Broad-`except` fallback to typed prompt/default; hard non-fatal requirement |
| Family field absent/variant in some `/api/tags` responses | Med | Treat missing family as non-embedding (include); never exclude on ambiguity |
| Combined diff may approach/exceed 800-line budget | Med | Flagged for sdd-tasks forecast (possible B-i / B-ii split) |

> **Sizing flag for sdd-tasks**: widening + doctor adaptation + picker + tests may
> approach/exceed the 800-line budget and could split into **B-i** (list_models
> widening + doctor adaptation) and **B-ii** (picker). Do NOT decide here — surface
> in the Review Workload Forecast.

## Rollback Plan

Revert the change branch. `list_models()` returns to `list[str]`; doctor and
`_resolve_model` restore prior behavior. No persisted data or config format
changes, so no migration to undo.

## Dependencies

- Slice A (`config-model-hardening`) merged on main — satisfied.

## Success Criteria

- [ ] `list_models()` exposes per-model tag + family; both call layers updated.
- [ ] Doctor checks 3/4/5 behavior unchanged on the new shape.
- [ ] `init` on a TTY shows a numbered chat-model list; embedding models excluded; Enter picks qwen3:8b.
- [ ] `--model` and non-TTY paths unchanged.
- [ ] Unreachable Ollama or zero chat models degrades to typed prompt/default; workspace still created; exit 0.
- [ ] Issue #128 closed.
