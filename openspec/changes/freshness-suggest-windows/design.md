# Design: freshness-suggest-windows (S2) — `suggest-volatility`

## Technical Approach

S2 adds a read-only LLM advisory verb `suggest-volatility` that proposes a
volatility TIER per concept TYPE, plus a hand-edited `type_tiers:` config layer
that injects a new override step into `lint.window_for_doc`'s precedence ladder.
It mirrors `suggest-relations` (`cli/main.py:2074`) and `edge_typing.py` limb for
limb: a config-free engine leaf with an injected `LLMBackend`, fail-closed
per-item degrade, unswallowed `OllamaError`. Absent `type_tiers` reproduces
byte-identical S1 lint behavior. Extends ADR-0007 (no new ADR).

> **ADR note**: S2 extends the ADR-0007 resolution model with a `type_tiers`
> override source in the same precedence ladder. No new ADR; ADR-0007 unedited.

## Architecture Decisions

| Decision | Choice | Alternative rejected | Rationale |
|---|---|---|---|
| Verb name | `suggest-volatility` | `suggest-windows` | Output is a TIER, not a duration; mirrors `suggest-<object>` naming |
| Engine home | new `resolution/volatility_typing.py` | extend `edge_typing.py` | Config-free leaf per type-seam; different input unit (TYPE not edge) |
| Config threading | new field on `VolatilityWindows` | new `window_for_doc` param | `VolatilityWindows` already flows resolve→check→window_for_doc; centralizes degrade in `resolve_windows` |
| Apply path | hand-edit `type_tiers:` only | auto-write accepted tier | No safe partial-YAML writer exists; keeps verb zero-write |
| LLM input | deterministic sampled bodies | all bodies / random | Bounds prompt for local models; reproducible advisory input |

## Deterministic Sampling Rule (the propose-flagged detail)

Per concept TYPE, the LLM sees: type name, current `default_tier`, then the
bodies of the **first N=5 concepts of that type ordered by sorted `identity`**,
each body **truncated to M=1000 chars**. Types are iterated in sorted-name
order; one `llm.chat` per type. Justification: N=5 gives enough signal to judge
a type's churn without flooding a local model's context; M=1000 bounds each
body to a token-cheap excerpt; sorting by identity makes the INPUT selection
reproducible regardless of filesystem walk order. The LLM OUTPUT need not be
deterministic — only the input selection is pinned.

## Data Flow

    suggest-volatility (main.py)
      require_workspace → read_config → OllamaClient(model=cfg.model)
        └─→ volatility_typing.suggest_volatility(bundle_dir, llm)
              collect_docs → group by type → sample(N,M) → llm.chat/type
              └─→ [TierSuggestion,...]  (OllamaError propagates)
      3-tier OllamaError handler → stdout report → "Next: edit type_tiers"

    lint path (unchanged trigger): read_config → resolve_windows(cfg)
      builds VolatilityWindows(+type_tiers) → window_for_doc uses new step

## File Changes

| File | Action | Description |
|---|---|---|
| `resolution/volatility_typing.py` | Create | Config-free engine leaf; `TierSuggestion`; `suggest_volatility` |
| `cli/main.py` | Modify | New `suggest-volatility` verb cloning `suggest_relations_cmd` wiring |
| `config.py` | Modify | `Config.type_tiers: dict[str,str]` passthrough, absent-default `{}` |
| `lint.py` | Modify | `VolatilityWindows.type_tiers` field; `resolve_windows` populate; `window_for_doc` step |
| `templates/openkos.yaml.template` | Modify | Commented `type_tiers:` block |

## Interfaces / Contracts

```python
# resolution/volatility_typing.py
@dataclass(frozen=True)
class TierSuggestion:
    type_name: str            # concept type present in the bundle
    current_default: str      # TYPE_TO_DEFAULT_VOLATILITY[type_name]
    suggested_tier: str | None  # in VOLATILITY_TIERS, or None on fail-closed degrade
    rationale: str            # never blank on the degrade path

def suggest_volatility(bundle_dir: Path, *, llm: LLMBackend) -> list[TierSuggestion]:
    ...  # one TierSuggestion per distinct type, sorted-name order; OllamaError unswallowed
```

```python
# config.py read_config — mirror volatility_windows passthrough (config.py:379,396)
type_tiers = raw.get("type_tiers")
# ... Config(..., type_tiers=type_tiers if type_tiers is not None else {})
```

```python
# lint.window_for_doc — BEFORE
tier = doc.volatility.strip()
if tier not in types.VOLATILITY_TIERS:
    tier = types.TYPE_TO_DEFAULT_VOLATILITY.get(doc.type, "")
# ... static→None / slow / volatile / fallback

# lint.window_for_doc — AFTER (type_tiers step inserted between 1 and old-2)
tier = doc.volatility.strip()                          # 1 per-concept
if tier not in types.VOLATILITY_TIERS:
    tier = windows.type_tiers.get(doc.type, "")        # 2 type_tiers override
if tier not in types.VOLATILITY_TIERS:                 # invalid/unknown → fall through
    tier = types.TYPE_TO_DEFAULT_VOLATILITY.get(doc.type, "")  # 3 registry default
# ... static→None / slow / volatile / else windows.fallback (4 global)
```

**Final precedence**: per-concept `volatility` → `type_tiers[type]` (if a valid
tier) → registry `default_tier` → global fallback. Every degrade
(non-mapping config, unknown type, invalid tier value) is ignore-and-fall-through
via the two `not in VOLATILITY_TIERS` guards — never raises. `resolve_windows`
sets `type_tiers = cfg.type_tiers if isinstance(cfg.type_tiers, dict) else {}`
(mirrors the `volatility_windows` non-mapping guard at `lint.py:212-213`); absent
→ `{}` → byte-identical S1.

## Report Format (stdout)

```
openkos suggest-volatility: workspace at {root}

[slow] Person
  rationale: {text}
[?] Project
  note: no valid tier suggested

Next: edit type_tiers in openkos.yaml
```

Header line, blank, one block per type (`[{tier}] {TypeName}` + `  rationale:`,
or `[?]` + `  note: no valid tier suggested` on degrade), closing hint. Empty
bundle / no types → `No concept types found.` after the header.

## Testing Strategy

| Layer | What to Test | Approach |
|---|---|---|
| Unit (leaf) | one suggestion per type, sorted order; fail-closed parse → `None`; sampling N=5/M=1000 deterministic; `OllamaError` propagates | fake `LLMBackend`, temp bundle |
| Unit (config) | `type_tiers` passthrough; absent → `{}`; null → `{}` | crafted `openkos.yaml` |
| Unit (lint) | new precedence step; per-concept still wins; unknown type / invalid tier / non-mapping fall through; absent → S1 identity | `window_for_doc` / `resolve_windows` table tests |
| Unit (CLI) | 3-tier OllamaError ordering, exit 1, zero writes; report shape incl. `[?]` | Typer runner, mock leaf |

## Threat Matrix

N/A — no NEW routing, shell, subprocess, or process-integration boundary. The
verb reuses the existing `OllamaClient` wiring unchanged, performs zero writes,
and adds no executable-file classification or VCS/PR automation.

## Migration / Rollout

No migration. Verb is additive; `type_tiers:` is additive, read-only,
absent-default `{}`. Rollback = revert the four source files + template block;
`window_for_doc` collapses to exact S1 precedence.

## Open Questions

- None blocking.
