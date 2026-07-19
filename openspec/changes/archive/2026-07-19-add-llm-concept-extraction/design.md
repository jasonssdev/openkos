# Design: LLM Concept/Entity Extraction (MVP-2 vertical slice)

## Technical Approach
Add one fallible, network-touching step inside `ingest()`'s existing pure Phase A, after `okf.build_source_concept` (main.py:316) and before the preview/confirm gate. A new config-free leaf `extraction/concept.py` (mirrors `retrieval/answer.py`) takes source text + an injected `LLMBackend`, prompts for a single JSON object, parses fail-closed, and returns a validated `ExtractionResult | None`. `main.py` owns slug/path/degrade messaging (LLM client construction like `query`, main.py:806). On success it builds a derived doc via a new validated `okf.build_concept`, extends the SAME index/log diff (one confirm gate, one preview), and adds one create-only Phase B write. Any `OllamaError`-family failure or `None` degrades to today's Source-only behavior, exit 0. Additive: the null-compiler path is untouched and is the degrade target.

## Architecture Decisions
| Decision | Choice | Alternatives rejected | Rationale |
|---|---|---|---|
| Module home | New leaf pkg `extraction/concept.py`, config-free, injected LLM | Put in `retrieval/`; inline in main.py | Extraction is a compile step, not retrieval; leaf keeps `llm/` config-free and unit-testable with a fake (screaming architecture) |
| Degrade seam | `extract_concept()` returns `ExtractionResult\|None`; lets `OllamaError` PROPAGATE to main.py | Catch/swallow in extraction | Mirrors `answer()`→`query` split (answer.py:151, main.py:809-829); keeps degrade UX in the CLI layer |
| JSON parsing | 3-step: `json.loads` raw → strip ``` fences → regex first `{...}` block; any failure → None | Trust raw text; Ollama JSON-mode API | No JSON precedent exists; local models wrap JSON in prose/fences; JSON-mode not wired in `OllamaClient` |
| Vocabulary | Closed set `{Concept, Entity}` enforced by validation, not prompt | Concept-only; all 11 types | Exercises real classification + prefer-specific-over-Entity (KOM:176) with near-zero ontology risk |
| Builder validation | New `okf.build_concept` WITH a validation gate (raises `ValueError`) | Reuse `build_source_concept` (skips validation) | Source fields are engine-derived/trusted (okf.py:55-58); LLM fields are untrusted and need their own gate |
| Idempotency/collision | Create-only: if target derived path exists, SKIP it (never overwrite) | `write_atomic` overwrite; suffix slug | Preserves user edits (living-doc, KOM:286); one rule covers re-ingest + slug collision |
| Catalog | Generalize inserter to `insert_index_entry(*, section, link_dir, ...)`; canonical-order-aware placement | Add separate `insert_concept_entry` | Sources stays last (existing test green); Concepts/Entities insert at correct rank |

## Data Flow
    raw text ─▶ extract_concept(text, llm) ─▶ ExtractionResult|None
                     │ (OllamaError propagates)      │
                     ▼                               ▼ (None → stderr note, Source only)
      main.py: _slugify title ─▶ okf.build_concept ─▶ extend index/log diff
                     │ (path exists → skip)          │
                     ▼                               ▼
              ONE preview + confirm gate ─▶ Phase B create-only writes (content, catalog last)

## File Changes
| File | Action | Description |
|---|---|---|
| `src/openkos/extraction/__init__.py` | Create | New leaf package |
| `src/openkos/extraction/concept.py` | Create | Prompt, JSON parse, validation, `ExtractionResult`, `extract_concept` |
| `src/openkos/model/okf.py` | Modify | Add `build_concept` (validated) parallel to `build_source_concept` |
| `src/openkos/bundle/index.py` | Modify | Generalize inserter with `section`/`link_dir`; canonical order `[Concepts, Decisions, Entities, People, Sources]`; `insert_source_entry` becomes thin wrapper |
| `src/openkos/cli/main.py` | Modify | Extraction call in Phase A; slug/path; degrade notes; extend diff; one Phase B write |
| `tests/unit/extraction/test_concept.py` | Create | Fake-LLM parse/validation matrix |
| `tests/unit/cli/test_ingest.py` | Modify | End-to-end scenarios (below) |
| `tests/unit/model/test_okf.py`, `.../bundle/test_index.py` | Modify | `build_concept` + generalized inserter |

## Interfaces / Contracts
```python
# extraction/concept.py  (config-free leaf; OllamaError propagates)
@dataclass(frozen=True)
class ExtractionResult:
    type: str; title: str; description: str; body: str  # type in {"Concept","Entity"}

def extract_concept(source_text: str, *, source_title: str,
                    llm: LLMBackend) -> ExtractionResult | None: ...
# None ⇔ extract:false OR any fail-closed validation failure.
```
Prompt (2-message `chat`): SYSTEM carries the closed `{Concept, Entity}` set, the three-test heuristic (distinct structure/relationships/recurrence, KOM:169), prefer-specific-over-Entity (KOM:176), and "return ONLY one JSON object, no prose/markdown". USER carries the raw source text. Schema requested: `{"extract": bool, "type": "Concept"|"Entity", "title": str, "description": str, "body": str}` (type/title/description/body meaningful only when extract true).
Validation (fail-closed → None): valid JSON object; `extract is True`; `type in {"Concept","Entity"}`; `title`/`description` non-empty after strip; `body` string (blank → builder falls back to description); `_slugify(title)` non-empty.
`okf.build_concept(*, type, title, description, body, provenance, sensitivity, timestamp)` frontmatter: type, title, description, tags=[], timestamp, status=active, version=1, freshness=snapshot, sensitivity (inherited from Source), provenance=[f"sources/{source_slug}"]; raises `ValueError` if type not in set or title/description blank. Body: `# {title}\n\n{description}\n\n{body}\n\n## Related\n\n- [{source_title}](/sources/{source_slug}.md) — source this was extracted from\n`.
Path: Concept→`bundle/concepts/<slug>.md`, Entity→`bundle/entities/<slug>.md`; slug via existing `_slugify(title)`. `write_exclusive` (create-only); pre-check existence in Phase A and drop from plan if present.
Security: derived title/description/slug pass through the existing `index._reject_newline` guard (index.py:41-51), so untrusted model output cannot forge `#`/`##` sections; create-only writes prevent overwrite.
Degrade wording — None: stderr `openkos ingest: no concept extracted from this source; keeping the Source only.`; OllamaError: stderr `openkos ingest: concept extraction skipped -- {exc}; keeping the Source only.` Both exit 0.

## Testing Strategy (TDD, `uv run pytest`)
| Layer | What | Approach |
|---|---|---|
| Unit (extraction) | valid Concept; valid Entity; fenced JSON; JSON-in-prose; malformed JSON→None; invalid type→None; extract:false→None; empty title→None; body-fallback | Fake `LLMBackend` (copy `_FakeLLM`, test_answer.py:41-50) returning canned strings |
| Unit (okf) | `build_concept` frontmatter/provenance/sensitivity/body; ValueError on bad type/blank title | Direct call |
| Unit (index) | Concepts/Entities section created at correct canonical rank; Sources round-trip unchanged | String assertions |
| Integration (ingest) | (1) valid → Source+derived both written, index 2 bullets, log mentions both, provenance points at Source; (2) invalid type → Source-only; (3) malformed JSON → Source-only; (4) extract:false → Source-only, neutral note; (5) fake raising OllamaUnavailable → Source-only + stderr warning, exit 0; (6) --auto → extraction runs, both written; (7) sensitivity=confidential inherited; (8) re-ingest with existing derived file → left untouched | Fake LLM injected; assert files/exit/stderr |
| Guard | `extraction/` and `llm/` import no `openkos.config` | import-graph assertion |

## Threat Matrix
N/A — no routing, shell, subprocess, VCS/PR automation, or executable-file classification. The one new risk surface is UNTRUSTED LLM output written to files; mitigated by fail-closed validation, the existing `_reject_newline` newline-injection guard, and create-only writes (no overwrite).

## Migration / Rollout
No migration. Additive slice; revert = remove extraction call, `build_concept`, and the `section` param. New `bundle/concepts/` & `bundle/entities/` dirs are created on demand (`mkdir(parents=True, exist_ok=True)`), like `sources/`.

## Decisions made (proposal left open)
- Entity gets its own `# Entities` catalog section + `bundle/entities/` dir (parallel to Concepts), added to canonical order.
- `build_concept` accepts an LLM `body` field (schema extended per orchestrator brief); blank body falls back to description.
- Provenance recorded as `sources/<slug>` (concept-id form, matches `remove_index_entry`/`Citation`), pointing at the Source concept not the raw file (KOM:230).
- Degrade distinguishes two stderr notes (neutral "no concept" vs "skipped -- reason").

## Open Questions
- [ ] None blocking. Non-blocking: whether to add an `extraction` on/off config key later (deferred; v1 always attempts, always fail-closed).
