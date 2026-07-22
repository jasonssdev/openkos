# Design: Freshness Lint v1 — Slice 1 (Volatility Taxonomy + Volatility-Aware Windows)

## Technical Approach

Make lint's stale-stamp window depend on knowledge volatility instead of one global `7d`. Three additive layers on the existing read path, preserving lint's read-only / never-fail / deterministic (injected clock) contract: (1) a per-type default tier on the `ObjectType` registry; (2) a new absent-by-default `volatility:` frontmatter override carried on `LintDoc`; (3) a pure, never-raising resolver in `lint.py` mapping `doc → tier → window`. The `freshness: "snapshot"` skip is unchanged and orthogonal. No LLM, no writes.

## Architecture Decisions

| Decision | Choice | Alternatives rejected | Rationale |
|---|---|---|---|
| Window values + config shape | Per-tier `volatility_windows:` map; legacy `freshness_window` retained as ultimate fallback | Direct per-type numeric windows (no named tiers, ADR-rejected); keep single global | Named tiers are the stable interface; 3 knobs beat 10; legacy key stays valid |
| Where volatility lives | `default_volatility` attr on registry + absent-by-default `volatility:` frontmatter override; **ingest unchanged** | Emit `volatility` from `build_concept` | Absent-by-default keeps ingest output byte-stable; derived concepts already carry `freshness:snapshot` (skipped anyway) |
| Resolution home | `VolatilityWindows` value resolved once from config; per-doc resolution inside `check_stale_stamps` | `Callable` window per doc; resolve in CLI | Keeps injected/deterministic shape; pure value is trivially table-testable |

## Per-Tier Windows (concrete)

| Tier | Default types | Window | Config key |
|---|---|---|---|
| `static` | Place, Event, Decision, Source | none — never flagged | — |
| `slow` | Concept, Entity, Person, Organization | `90d` | `volatility_windows.slow` |
| `volatile` | Procedure, Project | `7d` | `volatility_windows.volatile` |
| fallback | unknown/absent type | `freshness_window` (`7d`) | `freshness_window` (legacy) |

```yaml
# openkos.yaml (openkos.yaml.template)
freshness_window: 7d          # legacy global; ultimate fallback (unchanged)
volatility_windows:
  slow: 90d
  volatile: 7d                # static has no window (never flagged)
```

Code default: `config.DEFAULT_VOLATILITY_WINDOWS = {"slow": "90d", "volatile": "7d"}`. **Rationale**: `volatile` stays `7d` → current behavior preserved for fast-moving types; `slow` `90d` → quarterly review for stable-but-evolving concepts; `static` never nags on fixed historical facts / archived sources.

## Data-Model Change (before / after)

`LintDoc`: **before** `path, identity, rel_dir, body, freshness` → **after** adds `type: str`, `volatility: str` (both `""` when absent), parsed in `collect_docs` via `metadata.get("type"/"volatility", "")`.

`resolve_window(raw) -> (timedelta, notice)` (global) is kept unchanged. **Add**:

```python
@dataclass(frozen=True)
class VolatilityWindows:
    slow: timedelta
    volatile: timedelta
    fallback: timedelta                     # global freshness_window

def resolve_windows(cfg) -> tuple[VolatilityWindows, list[str]]: ...   # never raises
def window_for_doc(doc, windows) -> timedelta | None: ...             # None = static/never
```

`check_stale_stamps(docs, *, today, window: timedelta)` → `window` becomes `windows: VolatilityWindows`; it resolves per doc and skips when `window_for_doc` returns `None`. `today` still injected.

`config.Config` gains raw passthrough `volatility_windows: dict` (default `{}`, `is not None` fallback like `freshness_window`); grammar parsing stays in `lint`.

## Registry Change

Add `default_volatility: str` to the frozen `ObjectType`; populate all 10 entries. Add `VOLATILITY_TIERS: frozenset = {"static","slow","volatile"}` and derived `TYPE_TO_DEFAULT_VOLATILITY = {ot.name: ot.default_volatility for ot in REGISTRY}`. `types.py` stays a zero-dependency leaf (strings only; window values live in `config.py`).

## Resolution Algorithm (never-fail — load-bearing)

Tier precedence per doc: (1) `doc.volatility.strip()` ∈ `VOLATILITY_TIERS` → that tier; (2) else `TYPE_TO_DEFAULT_VOLATILITY.get(doc.type)` → that tier; (3) else → fallback. Tier→window: `static`→`None` (skip); `slow`→`windows.slow`; `volatile`→`windows.volatile`; fallback→`windows.fallback`.

| Degrade input | Behavior (never raises) |
|---|---|
| absent `volatility` | per-type default |
| unknown `volatility` value | per-type default |
| unknown / absent `type` | global fallback window |
| malformed tier window in config | that tier → `DEFAULT_FRESHNESS_WINDOW` + notice (reuses `resolve_window`) |
| `volatility_windows` not a map / null | treated empty → all tier defaults |
| `freshness: "snapshot"` | skipped before resolution (unchanged) |

All notices flow into `LintReport.notices`, matching the existing pattern.

## Data Flow

    read_config ─→ resolve_windows ─→ (VolatilityWindows, notices)
    collect_docs ─→ [LintDoc(type, volatility, …)]
    today(injected) + windows ─→ check_stale_stamps ─→ window_for_doc(per doc)
                                     └─ static → skip · else age > window → finding

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/model/types.py` | Modify | `default_volatility` on `ObjectType`; `VOLATILITY_TIERS`, `TYPE_TO_DEFAULT_VOLATILITY` |
| `src/openkos/config.py` | Modify | `DEFAULT_VOLATILITY_WINDOWS`; read `volatility_windows` into `Config` |
| `src/openkos/lint.py` | Modify | `LintDoc.type/volatility`; `VolatilityWindows`, `resolve_windows`, `window_for_doc`; `check_stale_stamps` signature |
| `src/openkos/cli/main.py` | Modify | `resolve_windows(cfg)`, pass `windows`, surface notices |
| `src/openkos/templates/openkos.yaml.template` | Modify | Add `volatility_windows:` block |
| `openspec/specs/lint/spec.md` | Modify | Revise "lint never reads it" Non-Goal |
| `docs/adr/0007-volatility-taxonomy.md` | Create | ADR |
| `docs/adr/README.md` | Modify | Index rows (0006, 0007) |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | registry defaults; `resolve_windows` degrade; `window_for_doc` precedence + degrade | table tests, injected values |
| Unit | `check_stale_stamps` per-doc: static never flagged, slow vs volatile boundary, snapshot skip | fixed `today`/windows |
| Unit | config parse of `volatility_windows` incl. malformed / null | `read_config` cases |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary.

## Migration / Rollout

No data migration. Purely additive read path: existing concepts without `volatility` → per-type default → global window. Rollback: revert PR; any `volatility:` frontmatter becomes inert and lint falls back to the single global window.

## Open Questions

None blocking.
