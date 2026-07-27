# Design: let `init` choose the embedding model, with an explicit re-embed warning

Mirror the shipped chat-model resolver for embeddings, gated by a curated 1024-dim allowlist that filters **the picker only**, and make a dimension mismatch a distinct permanent `OllamaError` subclass that `reindex` treats as fatal.

## ADR verdict: NO new ADR

| Gate | Verdict |
|---|---|
| (1) Decides a technology, pattern, interface, or trade-off? | **Yes** — curated allowlist over runtime probing; a new exception type. |
| (2) Hard-to-reverse? | **No.** |
| Both true? | **No → do not create an ADR.** |

Reversibility evidence, not assertion: the allowlist gates the picker but never `read_config`, so reverting it removes a menu, not a capability. Reverting `write_config(embedding_model=)` leaves already-written workspaces with an explicit key `read_config` already honors — no workspace breaks, no migration. The new error is an **additive** `OllamaError` subclass: every existing `except OllamaError` site keeps its current behavior, so removing it re-widens rather than breaks. The genuinely hard-to-reverse decisions here — `EMBED_DIM = 1024` and "reliability is a prior hard filter" — are already owned by **ADR-0006**; this change is its *implementation*, not a new decision. `rules.design`: "when in doubt, do not create one." Next free number remains 0008 for a future change.

## Decisions

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D1 | `EMBEDDING_MODEL_ALLOWLIST: tuple[str, ...]` in `config.py`, next to `DEFAULT_EMBEDDING_MODEL`, default first (= recommended). | `llm/ollama.py` alongside `_EMBEDDING_TAG_MARKER`. | It is ADR-0006 policy data, not transport/classification. `llm/` stays free of config policy. Honesty rule lives in its docstring: an entry is added only after a measured 1024-dim embed; a unit test asserts `DEFAULT_EMBEDDING_MODEL in EMBEDDING_MODEL_ALLOWLIST`. |
| D2 | The embedding picker filters on the allowlist **alone** — `is_embedding_model` is NOT applied. | Filter with `is_embedding_model(m) and allowlisted`. | Load-bearing: `bge-m3` has no `embed` substring, so `_EMBEDDING_TAG_MARKER` never fires and it classifies as embedding **only** via `family == "bert"`. An Ollama entry with no `details.family` would silently drop the recommended default from its own picker. The curated allowlist is stronger evidence than the heuristic; stacking them can only subtract correct candidates. |
| D3 | Candidate matching uses `ollama.model_tag_matches` normalization; the **allowlist** spelling is what gets displayed and written. | Raw string equality on server tags. | `/api/tags` reports `bge-m3:latest`; writing that would make `cfg.embedding_model` differ from `DEFAULT_EMBEDDING_MODEL` and trip the model-tag re-embed gate for a no-op change. |
| D4 | `_pick_embedding_model` runs its **own** `list_models()` probe, structurally cloned from `_pick_chat_model` (same broad `except Exception`, same `_MAX_PICKER_ATTEMPTS`, same non-TTY silence). | Hoist one shared probe into `init` and pass candidates to both pickers. | The probe is a cheap local GET dwarfed by human prompt latency. A shared probe couples the two pickers' degradation (one failure kills both) and rewrites `_pick_chat_model`'s signature plus its existing tests, for no user-visible gain. |
| D5 | `validate_embedding_model` = the existing `validate_model` body extracted into a private `_validate_model_token(tag, field)` helper, reused by both with a field-specific message. | Duplicate the regex/reserved-word logic. | One source of truth for YAML-scalar safety. The validator checks **safety only** — never allowlist membership (D6). |
| D6 | `--embedding-model` accepts an off-allowlist value: validated, **warned** on stderr in Phase A, never blocked. | Reject off-allowlist values. | The escape hatch is the whole reason staleness is bounded. Blocking would make the allowlist a hard gate on the config key, which the proposal explicitly rejects. |
| D7 | New `OllamaEmbeddingDimensionMismatch(OllamaError)`, raised **directly** by `_validate_embedding_row` on the length branch. | Keep `ValueError` and re-classify in `_embed_once`. | The length branch is inside `_embed_once`'s `except (JSONDecodeError, KeyError, TypeError, ValueError)` rewrap. An `OllamaError` subclass is not a `ValueError`, so it escapes that clause unwrapped with zero restructuring. Non-numeric entries stay `ValueError` → generic `OllamaError` (scope discipline). |
| D8 | `embed()`'s retry loop raises it immediately: `except (OllamaModelNotFound, OllamaEmbeddingDimensionMismatch): raise`. | Leave it to fall into `except OllamaError` and retry. | **The proposal does not mention this and it is mandatory.** Without it every mismatched embed burns the full backoff budget (`base * 2**(n-1)` sleeps) before failing — per document — for a condition that cannot heal. Same reasoning that already exempts `OllamaModelNotFound`. |

## `init` Phase A sequence

```
user            init()            _resolve_embedding_model      OllamaClient        disk
 |                |                          |                       |                |
 |-- init ------->|                          |                       |                |
 |                |-- refusal_reason ------------------------------------------------>|  PHASE A
 |                |-- _resolve_model(--model) ---------> (chat picker, probe #1) ----->|  (reads +
 |                |------------------------->|                       |                |   probes
 |                |                          |  flag? -> validate ---+                |   only,
 |                |                          |  TTY?  -> list_models ---------------->|   no write)
 |                |                          |     candidates = installed ∩ allowlist |
 |                |                          |     (except Exception -> [] -> default)|
 |                |                          |     numbered picker, bge-m3 recommended|
 |                |                          |  else -> DEFAULT_EMBEDDING_MODEL       |
 |                |<-- resolved_embedding ---|                       |                |
 |                |                                                                   |
 |                |-- raw/ -> bundle -> AGENTS.md -> openkos.yaml (LAST) ------------>|  PHASE B
 |                |-- git setup (best-effort) -> Ollama preflight -> STICKY WARNING -->|  POST
 |<-- exit 0 -----|                                                                   |
```

Both resolvers sit in `init`'s existing `try: ... except ValueError` block, so an unsafe flag still refuses at exit 1 before any write. `except Exception` around each probe is preserved verbatim: Ollama unreachable → zero candidates → default → exit 0, no crash (question 6).

## File changes

| File | Action | Change |
|---|---|---|
| `src/openkos/config.py` | Modify | `EMBEDDING_MODEL_ALLOWLIST`; `_validate_model_token` + `validate_embedding_model`; `write_config(..., embedding_model=DEFAULT_EMBEDDING_MODEL)` with a second placeholder guard; correct `Config`'s stale docstring (lines 344-347) that claims the key is not in the template. |
| `src/openkos/templates/openkos.yaml.template` | Modify | Add `embedding_model: __OPENKOS_EMBEDDING_MODEL__  # 1024-dim; changing it forces a full re-embed` under `model:`. |
| `src/openkos/cli/main.py` | Modify | `--embedding-model` option; `_resolve_embedding_model` / `_pick_embedding_model`; pass to `write_config`; sticky warning; a `reindex` ladder branch for the new error placed **before** the generic tuple. |
| `src/openkos/llm/ollama.py` | Modify | `OllamaEmbeddingDimensionMismatch`; raise it in `_validate_embedding_row`; exempt it from the `embed()` retry loop. |
| `src/openkos/state/reindex.py` | Modify | Add it to the fatal tuple at line 261. |

## Exception taxonomy and call-site audit (question 5)

```
Exception
└── OllamaError                          generic / transient
    ├── OllamaUnavailable                fatal
    ├── OllamaModelNotFound              fatal
    └── OllamaEmbeddingDimensionMismatch fatal, permanent  ← new
```

| Call site | Action | Ordering note |
|---|---|---|
| `llm/ollama.py` `embed()` retry loop | **Change** — join the immediate-raise clause | Must precede `except OllamaError` (D8). |
| `state/reindex.py:261` | **Change** — join `(OllamaUnavailable, OllamaModelNotFound)` | Must precede `except OllamaError:` at line 272. Getting this backwards is the exact silent-swallow the existing comment warns about. |
| `cli/main.py:6024-6068` (`reindex` ladder) | **Change** — add a dedicated branch after `OllamaModelNotFound` | Must precede `except (VecUnavailable, FtsUnavailable, OllamaError)`. Message names the fix: the model does not emit 1024-dim vectors; restore `embedding_model` in `openkos.yaml` and re-run. Must NOT say "will retry next run". |
| `cli/main.py:1234` `_embed_after_ingest` | **No change** | Broad `except Exception` already reports it non-fatally; `ingest` must not start failing on embeds. |
| `retrieval/answer.py:310` `_vector_hits` | **No change — deliberate deferral** | It would be *consistent* to re-raise here (both existing fatal subclasses are), but that flips `query` from FTS-only degradation to exit 1, which is a `query`-capability change the proposal did not scope and the concurrent spec will not cover. Named follow-up. |
| Chat-only sites (`extraction/`, `resolution/`, `query`/`adjudicate` chat ladders) | **No change** | `embed()` is never on those paths. |

## Testing strategy

| Layer | What | How |
|---|---|---|
| Unit — `config` | Allowlist contains the default; `validate_embedding_model` parity with `validate_model` (blank, reserved word, bad chars, leading/trailing `:`/`-`); `write_config` substitutes both placeholders and raises when either placeholder count ≠ 1; template byte-identity updated for the new line. | pytest, `tmp_path` |
| Unit — `cli/init` | Flag > picker > default; picker lists installed ∩ allowlist with `bge-m3` recommended; **`InstalledModel(tag="bge-m3", family=None)` still appears** (D2 regression guard); `:latest` normalization writes `bge-m3` (D3); off-allowlist flag warns but writes; unreachable Ollama and zero candidates both → default, exit 0; non-TTY silent; sticky warning on every success. | Extend `_fake_ollama_client` in `tests/unit/cli/test_init.py` |
| Unit — `llm/ollama` | Wrong-length row raises `OllamaEmbeddingDimensionMismatch`, not generic `OllamaError`; **zero `sleep` calls** (D8); non-numeric entry still generic. | Injected `_urlopen`/`_sleep` fakes |
| Unit — `state/reindex` | Mismatch propagates out of `reindex`; `embed_failed` stays 0; no `upsert_many`, no `commit`, no `write_model_tag`. | Fake embedder raising the new error |

## Threat matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary. `init`'s git block is untouched.

## Migration / rollout

No migration. Existing workspaces without `embedding_model:` keep `read_config`'s default fallback. New workspaces get an explicit key. The model-tag gate is untouched. Rollback, per layer and independent:

1. Revert CLI/config/template → new workspaces return to default-only; already-written explicit keys still parse. No workspace breaks.
2. Revert `llm`/`reindex` → restores the transient (buggy) classification. Independent of 1 because the new type is purely additive.

## Open questions

- [ ] None blocking. Follow-up: `retrieval/answer.py:310` still degrades silently on a permanent dimension mismatch (see audit table).
