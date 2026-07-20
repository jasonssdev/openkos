# Design: Entity-Resolution Adjudication (Slice 2)

## Technical Approach

A read-only LLM precision layer over slice-1's `find_candidates`. New config-free leaf
`resolution/adjudication.py` consumes `list[CandidateGroup]`, loads each member's
title + full body read-only, prompts an injected `LLMBackend` once per group ("are these
the same real-world entity?"), fail-closed-parses one JSON verdict object, and returns
`list[AdjudicatedCandidate]` (every group kept). A new read-only `adjudicate` CLI verb
wires config → `OllamaClient` → library, mirroring `query`. Nothing is written. Mirrors
`extraction/concept.py` (leaf, 2-message prompt, validate-returns-None, `OllamaError`
propagates) and `retrieval/answer.py:_assemble_context` (guarded per-member re-read).

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Call shape | Per-group single `chat()` (Approach A) | Batched array reply | Simple one-object parse; a failing group degrades only itself; matches `extract_concept`'s one-call-per-unit. |
| Content depth | Title + full body | Title-only / truncated | Real signal beyond string similarity is the whole point; 120s Ollama timeout absorbs cost; truncation is a follow-up. |
| Verdict model | 3 verdicts SAME/DIFFERENT/UNCERTAIN, keep all | Binary; drop DIFFERENT | UNCERTAIN is honest degrade; keeping DIFFERENT lets a human audit LLM-vs-mechanical disagreement. Library never filters. |
| Malformed reply | → UNCERTAIN, confidence 0.0, rationale notes failure | Drop the group | No-silent-drop: every input group appears in output. |
| CLI surface | New `adjudicate` verb | `--adjudicate` flag on `duplicates` | Keeps `duplicates` provably network-free; isolates the degrade story; matches slice-1's own verb precedent. |
| Signature style | Keyword-only `llm`/`bundle_dir` | Positional | Matches `extract_concept(..., *, source_title, llm)`. (Minor deviation from the launch note's positional sketch — flagged.) |

## Data Flow

    find_candidates(bundle_dir) ──→ list[CandidateGroup]
             │
             ▼  per group
    _load_members(bundle_dir, group) ─ okf.load_frontmatter ─ skip unreadable
             │  title + body per readable member
             ▼
    _build_messages ──→ llm.chat ──→ reply ──→ _parse_verdict (fail-closed)
             │                                        │ malformed → UNCERTAIN/0.0
             ▼                                        ▼
    AdjudicatedCandidate(candidate, verdict, confidence, rationale)
             │
             ▼  (OllamaError propagates OUT, uncaught)
    CLI adjudicate ── renders all; --same-only hides non-SAME in DISPLAY only

## Interfaces / Contracts

```python
class Verdict(Enum):
    SAME = "same"; DIFFERENT = "different"; UNCERTAIN = "uncertain"

@dataclass(frozen=True)
class AdjudicatedCandidate:
    candidate: CandidateGroup
    verdict: Verdict
    confidence: float   # clamped [0.0, 1.0]
    rationale: str

def adjudicate_candidates(
    candidates: list[CandidateGroup], *, bundle_dir: Path, llm: LLMBackend,
) -> list[AdjudicatedCandidate]: ...
```

Reply contract: `{"verdict": "same"|"different"|"uncertain", "confidence": <0..1>, "rationale": "..."}`.
Deterministic mapping (mirrors `concept._extract_json_items`/`_validate`, never raises):
verdict lowercased → enum, unknown/missing → `UNCERTAIN`; `confidence` coerced to float
and clamped [0,1], non-numeric → 0.0; `rationale` stringified (blank allowed).
Any parse/validate failure for a group → `UNCERTAIN`, confidence 0.0, rationale noting
the malformed reply. `OllamaError`-family from `llm.chat` is **not** caught here.

Member loading (mirrors `answer.py:80-102`): `read_text` guarded `except (OSError,
UnicodeDecodeError): continue`; `okf.load_frontmatter` guarded `except Exception: continue`;
`title = str(metadata.get("title") or "") or concept_id`. A group whose members are all
unreadable still yields one `AdjudicatedCandidate` (`UNCERTAIN`, confidence `0.0`, rationale
`"no readable member content"`) WITHOUT calling `llm.chat` for that group — a documented
exception to the one-call-per-group rule (Approach A still holds for every group with at
least one readable member).

Prompt (mirrors `concept._build_messages`): stable system rubric — "Decide if the listed
same-type objects are the SAME real-world entity. Return ONLY JSON {verdict, confidence,
rationale}. When unsure, use uncertain." — plus a user turn listing OKF type, tier, and
each member's `[concept_id — title]\n body`.

## File Changes

| File | Action | Description |
|---|---|---|
| `src/openkos/resolution/adjudication.py` | Create | `Verdict`, `AdjudicatedCandidate`, `adjudicate_candidates`, private prompt/parse/member-load helpers |
| `src/openkos/cli/main.py` | Modify | New `adjudicate` verb: `require_workspace` → `read_config` → `OllamaClient(model=cfg.model)` → `find_candidates` → `adjudicate_candidates`; render grouped verdict+confidence+rationale; `--same-only` display filter; 3-tier `OllamaError` catch (Unavailable→ModelNotFound→generic), exit 1, zero writes |
| `tests/unit/resolution/test_adjudication.py` | Create | `_FakeLLM` (queued replies), verdict/parse/clamp, malformed→UNCERTAIN, unreadable-member skip, OllamaError propagation |
| `tests/unit/cli/` (adjudicate) | Create | verb wiring, `--same-only` display, read-only, degrade-on-no-model (fake raises `OllamaUnavailable`) |
| `tests/unit/resolution/test_layering.py` | Modify | Add positive assertion: `resolution` MAY import `openkos.llm` (siblings), still forbid `bundle`/`state`/`graph` |

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit (lib) | verdict mapping, confidence clamp, malformed→UNCERTAIN/0.0, keep-all, unreadable skip, `OllamaError` propagates | `_FakeLLM` with per-call queued replies; tmp bundle files; fake raising `OllamaUnavailable` |
| Unit (CLI) | wiring, grouped render, `--same-only` display-only, read-only (no writes), 3-tier degrade → stderr + exit 1 | Typer `CliRunner`, tmp workspace, monkeypatched `OllamaClient` |
| Unit (layering) | `resolution`↔`llm` allowed; canonical never imports `resolution` | Extend AST guard |

`_FakeLLM`: per-group calls need distinct replies, so extend the fixed-reply fake to a
reply queue (records `.calls`). Real `OllamaClient` path stays thin (covered by `ollama.py` tests).

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or
process-integration boundary. Read-only; `OllamaClient` HTTP is the existing seam.

## Migration / Rollout

No migration. Additive and read-only — rollback deletes the module, verb, and tests;
nothing persisted.

## ADR Gate

Does NOT fire (`openspec/config.yaml` rules.design ADR gate). It decides patterns/interfaces
(condition 1 true) but they are cheaply reversible — additive, ephemeral output, no new deps,
no persisted type (condition 2 false). Both must hold; no ADR created.

## Open Questions

- [ ] Prompt rubric reliability on small local models — closed vocabulary + fail-closed
  validation mitigate; validate against real Ollama in verify.
- [ ] Latency of N per-group calls on large LOW lists — batching deferred (proposal non-goal).
