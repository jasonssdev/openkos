# Design: hybrid-retrieval (Slice 1) — Embedder seam + Ollama embeddings

## Technical Approach

Add a hermetic embedding seam behind the existing `llm/` leaf, cloning the
proven `chat()` plumbing. Four additive edits, no refactor, no retrieval
change: an `Embedder` Protocol beside `LLMBackend` (`llm/base.py:21`); an
additive `OllamaClient.embed()` (`llm/ollama.py`); an `embedding_model` config
field; a non-fatal `doctor` check. The leaf stays config-free — the CLI/config
layer resolves the tag and constructs a distinct `OllamaClient(model=embedding_model)`.

## Architecture Decisions

### Decision: Embedder as a structural Protocol
**Choice**: `class Embedder(Protocol)` with `embed(texts: Sequence[str]) -> list[list[float]]`, plus a `EMBED_DIM = 1024` contract constant in `base.py`.
**Alternatives considered**: ABC with inheritance; dimension baked only into `ollama.py`.
**Rationale**: Mirror `LLMBackend` exactly (structural, no explicit inheritance) so a fake satisfies it duck-typed, matching the fake-`LLMBackend` injection in `retrieval/answer.py:130`. Defining `EMBED_DIM` once at the seam keeps the 1024 contract in one place; `ollama.py` imports it.

### Decision: `embed()` on `OllamaClient`, reusing `chat()` plumbing
**Choice**: POST `{"model": self._model, "input": list(texts)}` to `{host}/api/embed`; reuse the identical connect / read / parse try-except ladder, `_map_http_error`, and `_unavailable`. Short-circuit `if not texts: return []` (no HTTP). Parse defensively: prefer `embeddings` (list of rows), fall back to singular `embedding` (wrap as one row); validate every row is length-`EMBED_DIM` and numeric, else raise `OllamaError`.
**Alternatives considered**: New `OllamaEmbedder` class; new HTTP dependency (httpx); trusting response shape.
**Rationale**: Proposal locks `embed()` onto `OllamaClient`. Reusing the transport ladder inherits the same typed-error vocabulary (`OllamaError`/`OllamaUnavailable`/`OllamaModelNotFound`) and the trusted-host S310 discipline (`ollama.py:39`), with zero new deps. Defensive parse absorbs the `/api/embed` vs legacy `/api/embeddings` shape variance (Med risk).

### Decision: `embedding_model` is default-only in `config.py` (no template line)
**Choice**: Add `DEFAULT_EMBEDDING_MODEL = "qwen3-embedding:0.6b"` + an `embedding_model: str` field on `Config`, resolved in `read_config` via the existing `is not None` fallback (`config.py:309-325`). Do NOT touch `openkos.yaml.template`.
**Alternatives considered**: Static template line + fallback (symmetry with the other four fields); a second `__OPENKOS_*__` placeholder.
**Rationale**: Scope. The proposal (id 1346) locks the modified capabilities to `llm-client` and `doctor-command` only; a template line touches a THIRD capability, `workspace-init`, outside the locked slice. Default-only keeps Slice 1 to the two agreed capabilities and matches the spec's decision #1 (id 1348). Users can still override by adding the key by hand; the `is not None` fallback covers every workspace that omits it. Template discovery is deferred to a later slice.

### Decision: doctor check is informational (non-fatal)
**Choice**: New check "Embedding model '<tag>' installed", `critical=False`, `[SKIP]` when Ollama unreachable (D6 no-double-report). Reuses the already-fetched `installed` list and `model_tag_matches`; tag from `cfg.embedding_model` or `DEFAULT_EMBEDDING_MODEL` outside a workspace. Renumber bundle-readable to the last check.
**Alternatives considered**: `critical=True` (like the chat model); touch `init` preflight too.
**Rationale**: No retrieval path consumes embeddings in Slice 1, so a failure must not flip doctor's exit code. Promote to critical in a later slice once retrieval depends on it. Init preflight stays untouched — locked scope names only doctor.

## Data Flow

    CLI/config ── embedding_model tag ──▶ OllamaClient(model=tag)
                                              │ embed(texts)
                                              ▼
                              POST /api/embed {model,input}
                                              │
                          parse embeddings ──▶ validate 1024-float rows
                                              │ else OllamaError
                                              ▼
                                    list[list[float]]  (len == len(texts))

Tests inject a fake `Embedder` (deterministic vectors) or a fake `urlopen`,
never a live server.

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/openkos/llm/base.py` | Modify | Add `Embedder` Protocol + `EMBED_DIM = 1024`. |
| `src/openkos/llm/ollama.py` | Modify | Add `embed()`; import `EMBED_DIM`; defensive parse + dim validation. |
| `src/openkos/config.py` | Modify | Add `DEFAULT_EMBEDDING_MODEL`, `Config.embedding_model`, `read_config` fallback (default-only; template untouched). |
| `src/openkos/cli/main.py` | Modify | Add non-fatal embedding-model doctor check; renumber bundle check. |
| `tests/unit/llm/`, `tests/unit/config/`, `tests/unit/cli/` | New/Modify | Fake `Embedder`, `embed()`, config, doctor tests. |
| `docs/cli.md`, `docs/roadmap.md` | Modify | Document field + doctor line. |

## Interfaces / Contracts

```python
# llm/base.py
EMBED_DIM = 1024

class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...  # pragma: no cover -- Protocol stub, never executed
```

`embed([])` returns `[]`. Every returned row has `len == EMBED_DIM`; malformed,
wrong-dim, unreachable, or HTTP-error responses raise the matching typed error.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | Fake satisfies `Embedder` structurally | Tiny class with `embed()`; assert usable where `Embedder` expected. |
| Unit | `embed()` happy + branches | Reuse `_fake_urlopen`/`_raising_urlopen`/`_fake_urlopen_returning`: `embeddings` shape, singular `embedding` fallback, wrong-dim→`OllamaError`, non-JSON→`OllamaError`, empty-input no-HTTP short-circuit, `URLError`/`TimeoutError`/read-phase→`OllamaUnavailable`, 500→`OllamaError`, 404→`OllamaModelNotFound`. |
| Unit | Config resolution | `read_config` returns tag from YAML; falls back to `DEFAULT_EMBEDDING_MODEL` when absent/null. |
| Unit | Doctor check | Monkeypatch `OllamaClient.list_models` for pass/fail/skip; assert non-fatal exit code. |
| Unit | Leaf discipline | Existing AST no-config-import test stays green. |

Branch coverage ≥90% is met by exercising the malformed-response and
daemon-unreachable branches above.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, or executable-file
classification. The one new network call targets the same trusted, env/config
host as `chat()`, reusing `_normalize_host`'s S310 discipline; nothing derives
the URL from document content.

## Migration / Rollout

No migration. Fully additive and reversible (rollback = remove the four edits
and their tests). No persisted state, no dependency change, no retrieval touch.

## Open Questions

- None. Config open question resolved: default-only (`DEFAULT_EMBEDDING_MODEL` + `read_config` fallback, no template line), matching spec decision #1 and the two-capability scope lock.
