# Proposal: let `init` choose the embedding model, with an explicit re-embed warning

Issue [#189](https://github.com/jasonssdev/openkos/issues/189) (P2, MVP 1). Required prior reading: `docs/adr/0006-default-embedding-model.md`.

## Intent

`init` picks the chat model interactively but silently hardcodes `bge-m3` for embeddings; `embedding_model` is never written to `openkos.yaml`. Users discover the key only by reading source. Worse, a wrong-dimension model fails **permanently** while `reindex` reports it as transient ("will retry next run") forever — so today's silence is the only thing hiding a latent lie.

## Decision 1 — vetted 1024-dim allowlist picker, not a runtime probe

| Option | Verdict |
|---|---|
| Vetted 1024-dim allowlist picker | **Chosen.** An allowlist *is* ADR-0006's reliability filter made executable: only already-vetted candidates are eligible, quality ranks second. Zero runtime cost, deterministic, testable. |
| Probe embed during `init` | Rejected. Loads a ~1.2 GB model in Phase A; degradation is a trap (allow-on-failure defeats safety, fallback-on-failure defeats choice). |
| Info + config key only | Rejected as the whole answer (also rejected by #189), but kept as the escape hatch. |
| Late-but-loud validation | Adopted as a **complement** — see Decision 2. |

Shape: `--embedding-model` flag > TTY picker over installed ∩ allowlist > `DEFAULT_EMBEDDING_MODEL`, mirroring `_resolve_model`/`_pick_chat_model` (same graceful degradation, same non-TTY silence). The flag is the expert escape hatch: validated for YAML safety, warned but not blocked when off-allowlist. Staleness is bounded because the allowlist gates *the picker*, never the config key.

The warning is about **future** cost, not present cost: a fresh workspace has nothing to re-embed. Wording must say the choice is sticky and changing it later forces a full corpus re-embed via the model-tag gate.

## Decision 2 — the `reindex` misclassification is IN scope, narrowly

Pre-existing and in another subsystem, but this change makes it *reachable*: shipping a picker without it means #189's "explicit warning" is contradicted by the first real mismatch. Bounded fix: a dedicated permanent error raised by `embed()` on dimension mismatch, treated as **fatal** in the per-doc loop alongside `OllamaUnavailable`/`OllamaModelNotFound`. No broader retry or taxonomy rework.

## Scope

### In Scope

- `--embedding-model` flag, `validate_embedding_model`, vetted 1024-dim allowlist constant.
- Embedding picker in Phase A; `write_config(embedding_model=…)` + new template placeholder.
- Sticky re-embed warning on every successful `init`.
- Dimension mismatch → permanent, fatal, honestly-worded failure.

### Out of Scope (non-goals)

- Any `EMBED_DIM` / vec0 schema change; any dimension other than 1024.
- Runtime dimension probing or `ollama pull` during `init`.
- Retry/backoff redesign or wider `OllamaError` taxonomy rework.
- `doctor` gaining an allowlist check.
- Migrating existing workspaces (the model-tag gate already self-heals).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `workspace-init`: `model:` is no longer "the single user-selectable field"; embedding resolution, picker, allowlist degradation, and the sticky-re-embed warning are added.
- `reindex-command`: dimension mismatch is fatal, not a `embed_failed` transient; the "will retry next run" notice must not cover it.
- `llm-client`: new permanent embedding-dimension error distinct from generic `OllamaError`.

## Approach

1. Add the allowlist + `validate_embedding_model` in `config.py`; add the `embedding_model=` write path and template placeholder.
2. Add `_pick_embedding_model` / `_resolve_embedding_model` mirroring the chat pair, reusing the existing `is_embedding_model` family classification and the same broad-`except` degradation.
3. Raise a dedicated dimension-mismatch error in `llm/ollama.py`; catch it as fatal in `state/reindex.py`'s per-doc loop.
4. Print the sticky warning unconditionally after Phase B, next to the existing preflight warning.

## Affected Areas

| Area | Impact | Description |
|---|---|---|
| `src/openkos/cli/main.py` | Modified | `init` flag, resolver, picker, warning |
| `src/openkos/config.py` | Modified | allowlist, validator, `write_config(embedding_model=)` |
| `src/openkos/templates/openkos.yaml.template` | Modified | new placeholder |
| `src/openkos/llm/ollama.py` | Modified | permanent dimension-mismatch error |
| `src/openkos/state/reindex.py` | Modified | treat that error as fatal |
| `tests/unit/cli/test_init.py`, reindex + ollama unit tests | Modified | greenfield coverage |

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Allowlist goes stale (the `_EMBEDDING_TAG_MARKER` precedent, #188) | Med | Allowlist gates only the picker; flag and manual YAML edit stay open |
| Template placeholder change breaks byte-identity assertions | Med | `workspace-init` delta spec updates the byte-identity requirement explicitly |
| Fatal reclassification aborts runs that previously limped on | Low | Only the dimension case; it never succeeded anyway |
| Users on an off-allowlist non-1024 model now fail loudly at `reindex` | Low | That is the intended honesty gain; message names the fix |

## ADR gate

**Likely required — flag for `sdd-design`, do not write here.** This decides a *pattern* (curated allowlist over runtime probing as the enforcement of ADR-0006's reliability-first filter) and is *hard-to-reverse* (removing shipped choices is a UX regression; `embedding_model` becomes part of every new workspace's written contract). `sdd-design` must apply `rules.design` and decide.

## Rollback Plan

Revert is clean and independent per layer:

1. Revert the CLI/config/template commit — new workspaces fall back to today's default-only behavior. Workspaces already written keep an explicit `embedding_model:` key, which `read_config` already honors, so **no workspace breaks**.
2. Revert the `llm`/`reindex` commit independently — restores the (buggy but non-fatal) transient classification.
3. No data migration, no vec0 change, no stored-tag change; the model-tag gate is untouched either way.

## Dependencies

- ADR-0006 (accepted) supplies the reliability-prior-filter rule the allowlist encodes.
- `is_embedding_model` family classification (llm-client) already ships.

## Success Criteria

- [ ] A TTY `init` with ≥1 installed allowlisted embedding model offers a numbered picker with `bge-m3` marked recommended.
- [ ] `--embedding-model` overrides with no picker; non-TTY resolves silently to the default.
- [ ] Ollama unreachable or zero allowlisted candidates degrades to the default, exit 0, no crash.
- [ ] The resolved value is written to `openkos.yaml`, every other field byte-identical to the template.
- [ ] Every successful `init` prints the sticky re-embed warning.
- [ ] A dimension-mismatched model makes `reindex` fail fatally with a permanent-failure message; no "will retry next run".
