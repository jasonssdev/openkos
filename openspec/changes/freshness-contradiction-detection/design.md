# Design: Contradiction Detection (S3 of freshness-lint-v1)

## Technical Approach

Mirror `edge_typing.suggest_relations` one layer over. A new read-only `contradictions`
CLI verb clones `adjudicate`'s wiring; a new config-free engine leaf
`src/openkos/resolution/contradiction.py` owns the `openkos.graph` read and issues one
`llm.chat` per candidate pair. Candidate signal = GRAPH TYPED-EDGE PAIRS only
(`relation_type is not None`), deduped to unordered pairs. Zero writes, advisory only.
Part of the freshness-lint-v1 arc (S1/S2 mechanical staleness; S3 adds conflict advisory).
No new ADR (proposal gate fails both limbs: read-only, zero persistence).

## Architecture Decisions

| Decision | Choice | Rejected alternative | Rationale |
|----------|--------|----------------------|-----------|
| Candidate signal | Typed-edge pairs only | O(n·k) embedding neighbors; seeded `contracts` relation | Few, already-related, high-signal pairs; precision-first; no schema change |
| Dedup key | `frozenset({source_id, target_id})` | `(source, target)` tuple | Symmetric/duplicate rows judged once (A→B and B→A collapse) |
| Pair ordering | Sorted by `tuple(sorted(pair))` | `store.edges()` order | Deterministic stable prefix under cap; reproducible reports |
| Threshold/cap home | Module constants | Config knobs | Tuning values, not schema — mirrors S2 `N_SAMPLE_CONCEPTS`/`M_TRUNCATE_CHARS` |
| Parse machinery | Module-local clone of `adjudication` fail-closed helpers | Cross-import `_`-prefixed symbols | Matches S2b design D4 (leaf owns its copies) |
| CLI graph access | Verb imports only `contradiction` leaf | CLI imports `openkos.graph` | "No CLI Surface" boundary (D2/D6), like `suggest-relations` |

### Pinned constants (in `contradiction.py`)

- `CONFIDENCE_DISPLAY_THRESHOLD = 0.7` — default report shows only CONTRADICTS with
  `confidence >= 0.7`. Justification: precision is make-or-break; 0.7 is the conventional
  high-confidence cut and matches the ≥0.7 autonomous-confidence bar used elsewhere in the
  workspace. `--all` bypasses it.
- `MAX_PAIRS = 200` — hard cap on judged pairs. Justification: typed-edge pairs are already
  sparse (related concepts, not O(n²)); at ~2-5s/`llm.chat` locally, 200 bounds a run to a
  tolerable ~10-15 min while exceeding realistic typed-edge counts for most bundles. When
  candidates exceed the cap, the deterministic prefix is judged and the report states
  `N of M pairs shown (cap reached)` — never silent truncation.

## Data Flow

    contradictions verb ─→ require_workspace ─→ read_config ─→ OllamaClient
         │                                                          │
         └─→ find_contradictions(bundle_dir, llm) ────────────────┘
                    │  build_graph → typed edges → frozenset dedup
                    │  → sorted pairs → [:MAX_PAIRS] → _load_doc x2 → llm.chat/pair
                    ▼
             list[ContradictionVerdict]  ─→ grouped render (3-tier OllamaError handler)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/resolution/contradiction.py` | Create | Config-free engine leaf: graph read, dedup/cap, per-pair judge, fail-closed parse |
| `src/openkos/cli/main.py` | Modify | New `contradictions` verb cloning `adjudicate`; `--all` display flag |
| `openspec/specs/contradiction-detection/spec.md` | Create | Capability spec (written by sdd-spec) |

## Interfaces / Contracts

```python
class Verdict(Enum):          # module-local, mirrors adjudication.Verdict
    CONTRADICTS = "contradicts"
    CONSISTENT = "consistent"
    UNCERTAIN = "uncertain"

@dataclass(frozen=True)
class ContradictionVerdict:
    pair_ids: tuple[str, str]            # sorted (a, b)
    verdict: Verdict
    confidence: float                    # _coerce_confidence clamp [0,1]
    rationale: str
    conflicting_claims: tuple[str, ...]  # cited claims; empty => coerced UNCERTAIN

def find_contradictions(bundle_dir: Path, *, llm: LLMBackend) -> list[ContradictionVerdict]: ...
```

Parsing reuses `adjudication`'s machinery verbatim (module-local copies): 3-step
`_extract_json_object`, case-insensitive verdict map (unknown → UNCERTAIN, confidence kept),
`_coerce_confidence`. Expected reply shape:
`{"verdict","confidence","rationale","conflicting_claims"}`. **Precision rule**: a
CONTRADICTS with missing/empty `conflicting_claims` is coerced to UNCERTAIN. Per-pair parse
failures degrade that pair only; `OllamaError`-family propagates unswallowed.

**LLM prompt contract**: system rubric + user turn carrying both concept `[id — title]`
headers, both full bodies, and the `relation_type` linking them. Instruct: assert
CONTRADICTS ONLY when specific conflicting claims can be cited; otherwise CONSISTENT or
UNCERTAIN. Return JSON only, no prose/markdown/fences.

## Report Format

Mirror `adjudicate`'s grouped render. Per contradiction:
`[CONTRADICTS] {A} <-> {B} (confidence: 0.NN)`, then cited `conflicting_claims`, then
rationale. Default hides CONSISTENT/UNCERTAIN and sub-threshold CONTRADICTS; `--all` shows
every verdict grouped. Empty graph / zero typed-edge pairs → `No candidate pairs found.`,
exit 0, no `llm.chat`. Cap reached → append `N of M pairs shown (cap reached)`.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit (leaf) | Dedup collapses A↔B; sorted-prefix under cap; CONTRADICTS w/o claims → UNCERTAIN; unknown verdict → UNCERTAIN; confidence clamp; per-pair degrade; `OllamaError` propagates; empty graph → `[]` no llm call | Fake `LLMBackend`, temp bundle |
| Unit (CLI) | 3-tier handler order; `--all` vs default filter; threshold cut; cap line; empty-graph message; zero writes | Typer runner, fake Ollama |
| Integration | Verb over a fixture bundle with a real typed-edge pair | Stubbed backend, assert no file mutation |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. Read-only verb over local files + injected LLM backend.

## Migration / Rollout

No migration required. Fully reversible: git-revert the two source files + spec. Zero writes,
no persisted schema.

## Open Questions

None.
