# Exploration: let `init` choose the embedding model, with an explicit re-embed warning

Issue: [#189](https://github.com/jasonssdev/openkos/issues/189) (P2, MVP 1 — The Compiler)

Required prior reading: `docs/adr/0006-default-embedding-model.md`.

## Current State

### `init`'s model-resolution path (`src/openkos/cli/main.py`)

- `init()` (line 324) is Phase A / Phase B (D1): Phase A does a pure read
  (`config.refusal_reason`) plus `_resolve_model(model)` (line 373); Phase B
  writes `raw/`, the bundle, `AGENTS.md`, then `openkos.yaml` last.
- `_resolve_model(flag)` (line 151): precedence `--model` flag > TTY picker
  (`_pick_chat_model`) > `config.DEFAULT_MODEL`, always validated through
  `config.validate_model`.
- `_pick_chat_model()` (line 182): probes Ollama in Phase A strictly before any
  workspace write, wrapped in a broad `except Exception` (Graceful Degradation)
  so an unreachable server falls back to a typed prompt. It filters candidates
  with `not is_embedding_model(m) and _is_selectable_model_tag(m.tag)` (line
  208) — it already **excludes** embedding models from the chat list and never
  offers one.
- `config.write_config(root, model=resolved_model)` (`config.py:310`) is the
  only workspace-write call in `init` that takes a model parameter. There is no
  `embedding_model=` parameter in `write_config` or in `init`. `embedding_model`
  is never written by `init` — confirmed by `Config`'s own docstring
  (`config.py:344-347`): "`embedding_model` is default-only ... not part of
  `openkos.yaml.template`, but a user may hand-add the key."
- `config.validate_model` (`config.py:64`) is the chat-model validator
  (allowlist regex, YAML-reserved-word rejection, leading/trailing `:`/`-`
  rejection). There is **no** `validate_embedding_model`.
- `DEFAULT_EMBEDDING_MODEL = "bge-m3"` (`config.py:24`), consumed only by
  `read_config`'s `is not None` fallback (`config.py:428-430`) when
  `embedding_model` is absent from `openkos.yaml`.

### Full consumer call chain for `embedding_model`

| Verb | Site | Use |
|---|---|---|
| `ingest` | `main.py` ~1632-1636 | `_embed_after_ingest(layout, OllamaClient(model=cfg.embedding_model), model_tag=cfg.embedding_model)` |
| `query` / `adjudicate` | `main.py` ~5758-5759 | `embedder = OllamaClient(model=cfg.embedding_model)` |
| `reindex` | `main.py` ~6008-6023 | `embedder = OllamaClient(model=cfg.embedding_model)`; `state.reindex.reindex(..., model_tag=cfg.embedding_model)` |
| `doctor` | `main.py` ~6303-6391 | non-critical check "Embedding model '{tag}' installed" via `model_tag_matches` |

## The re-embed gate — named and verified

The **model-tag gate** (MVP-2 follow-up #5) lives in `src/openkos/state/reindex.py`
(`reindex()`, line 137) and `src/openkos/state/vectorstore.py`
(`EMBEDDING_MODEL_KEY`, `read_model_tag`/`write_model_tag`, lines 85, 208-219,
512-525).

1. `model_changed = model_tag is not None and stored_model_tag != model_tag`
   (`reindex.py:218`) compares the stored tag against `cfg.embedding_model`.
2. If changed, every concept is forced through embedding this run (no vec0
   `DROP`; the existing `upsert_many` DELETE-then-INSERT), independent of
   `--force`.
3. Per-doc embed loop (`reindex.py` ~259-278): `OllamaUnavailable` /
   `OllamaModelNotFound` are fatal and re-raise immediately (checked first,
   since both subclass the generic `OllamaError`). The generic `OllamaError` is
   caught, increments `embed_failed`, and the loop **continues**.
4. The new tag is persisted (`db.write_model_tag`) only when
   `model_changed and skipped == 0 and embed_failed == 0` (`reindex.py:305`) —
   otherwise the same forced re-embed repeats on every subsequent `reindex`.
5. The user-facing summary (`main.py:6108-6121`) distinguishes complete
   ("re-embedded all vectors — embedding model changed") from incomplete
   ("... INCOMPLETE: N doc(s) could not be re-embedded, will retry next run").

### Load-bearing finding: a permanent failure is reported as transient

A wrong-dimension model does **not** crash `reindex` outright:

- `_validate_embedding_row` (`llm/ollama.py:344`) raises `ValueError` when
  `len(row) != EMBED_DIM`.
- `embed()` (`llm/ollama.py:268`) catches that in
  `except (json.JSONDecodeError, KeyError, TypeError, ValueError)` and rewraps
  it as the **generic** `OllamaError` — not `OllamaUnavailable` /
  `OllamaModelNotFound`.
- `reindex.py:273` therefore treats a **permanent** dimension mismatch exactly
  like a **transient** embed hiccup: its own comment reads "Generic transient
  failure ... isolate THIS doc only and keep processing the rest".

Result: `embed_failed` increments for every document, the run ends with "will
retry next run", and retrying never helps because the dimension is fixed. This
is a real, pre-existing latent bug, and it is the exact mechanism by which a
wrong-dimension pick would surface today.

## The dimension problem

No source in this repo, and nothing in Ollama's `/api/tags`, reports a model's
output dimension ahead of an actual `/api/embed` call. `InstalledModel` carries
only `tag` and `family` (`llm/ollama.py:59-80`) — `family` classifies "is this
an embedding model", not "what dimension".

So "let `init` choose the embedding model" is only safe for 1024-dim models, and
the code cannot tell which those are up front.

### Options (non-exhaustive, no recommendation made here)

1. **Vetted allowlist of known-1024-dim models.** Zero runtime cost,
   deterministic. Cost: static and prone to staleness; mirrors the
   `_EMBEDDING_TAG_MARKER` heuristic's maintenance burden (which already needed
   a patch round in issue #188); blocks unlisted-but-valid local models.
2. **Probe embed during `init`** (send one short text, measure row length).
   Authoritative — it exercises the real `_validate_embedding_row` path. Cost:
   requires the model already pulled and Ollama reachable; materially heavier
   than `_pick_chat_model`'s cheap `list_models()` GET; collides with `init`'s
   "Phase A probe strictly before any workspace write" discipline and needs its
   own graceful-degradation design (does a failed probe silently allow the pick,
   defeating the safety goal, or fall back to the vetted default?).
3. **Surface as information plus a config key, not a picker.** Print the
   resolved embedding model and ADR-0006's warning without offering a selectable
   list; changing it stays a manual `openkos.yaml` edit. Cheapest, zero new
   failure surface — but borderline against the issue's explicit rejection of
   "leaving it non-interactive".
4. **Validate late but loudly, at first embed** — i.e. fix the
   transient/permanent misclassification above so the message is honest.
   Complementary to 1-3, not a substitute: on its own it offers no `init`-time
   choice.

These are not mutually exclusive. A plausible shape combines 1 or 3 for the
`init` UX with 4 as a correctness fix regardless.

## Test surface

- `tests/unit/cli/test_init.py` is the sole file covering `init`'s model
  resolution.
- `_fake_ollama_client(installed=...)` fakes only `list_models()`; it accepts a
  bare `str` (chat shorthand) or a full `InstalledModel(tag=..., family=...)`.
- `test_picker_lists_chat_models_excludes_embedding` and
  `test_picker_zero_chat_models_falls_back_to_typed_prompt` already exercise
  `is_embedding_model` exclusion using `InstalledModel(tag="bge-m3",
  family="bert")` — the closest existing analogue for an embedding picker's
  tests.
- `test_model_flag_*` covers `--model` / `validate_model` end-to-end through the
  CLI.
- No existing test exercises `embedding_model` selection, a
  `validate_embedding_model` (which does not exist), or a probe-embed path —
  this is genuinely greenfield.
- Coverage for `model_reembedded` / `embed_failed` in the reindex state tests was
  not read in this pass — flagged for `sdd-spec`.

## Non-goals

- No second dropdown symmetrical with the chat picker.
- No `EMBED_DIM` or vec0 schema change.
- No network pull during `init` (surfaced only as a cost of option 2, not
  proposed).

## Risks

1. The transient/permanent `OllamaError` misclassification in `reindex.py`
   becomes far more likely to trigger once users can pick non-default models.
   `sdd-propose` must scope it explicitly in or out, or the issue's "explicit
   re-embed warning" promise rings hollow on the first real mismatch.
2. Allowlist maintenance risk parallels the `_EMBEDDING_TAG_MARKER` precedent.
3. A probe embed changes `init`'s Phase A latency and failure profile and needs
   an explicit degradation design.
4. `config.write_config` has no `embedding_model=` parameter and the packaged
   template has only one placeholder (`__OPENKOS_MODEL__`), so writing a chosen
   embedding model needs a template change or a distinct write path.

## Ready for proposal

Yes. `sdd-propose` must pick between options 1/3 (or a combination) for the
`init` UX, and decide whether the transient/permanent `OllamaError` fix is
in-scope or a named follow-up.
