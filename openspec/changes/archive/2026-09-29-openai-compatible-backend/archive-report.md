# Archive Report: openai-compatible-backend

**Date archived:** 2026-09-29
**Issue:** #1057
**ADR:** ADR-0031, "OpenAI-compatible backend", Accepted at archive
**Tasks:** all checked; the "open PR" halves were closed at archive against the delivery record in `tasks.md`.

## PRs (stacked to main)

| PR | Scope |
|---|---|
| #1096 | Planning, ADR-0031 (Proposed); backend-neutral helpers moved to `llm/base.py`; `llm` commit scope |
| #1097 | Neutral `Backend*` error bases; every catch site outside the clients migrated; AST guard |
| #1098 | `backend`, `base_url`, `embedding_base_url` config surface; API keys refused in `openkos.yaml` |
| #1099 | `OpenAICompatibleClient` chat core; never follows redirects (urllib forwards `Authorization` on redirect) |
| #1100 | Chat bounds, `embed()` with L2 normalization and retry, `list_models()` |
| #1101 | `OfflineOpenAICompatible` double; network guard over both clients |
| #1102 | Resolver seam: `BackendFactories`, `resolve_endpoint`, key read from `OPENKOS_OPENAI_API_KEY` only |
| #1103 | Embed and diagnostics construction through the resolver; backend-aware embedding tag, 4-branch re-embed disclosure, question-vector cache key |
| #1104 | Backend-aware remediation wording (doctor, CLI, curate, MCP); `insecure_key_warning` |
| #1105 | `openai-compatible` enabled; loopback end-to-end test; docs and AGENTS.md |

## Delta specs merged into `openspec/specs/`

Each existing domain was composed with `gentle-ai sdd-archive-compose` (exit 0), and every requirement heading in each delta was confirmed present in its living spec. The two new domains were copied whole.

| Domain | Requirements before → after |
|---|---|
| backend-selection | new → 9 |
| openai-compatible-client | new → 19 |
| doctor-command | 18 → 20 |
| curate-command | 21 → 21 |
| entity-resolution-adjudication | 48 → 48 |
| llm-client | 18 → 18 |
| llm-edge-production | 7 → 7 |
| mcp | 26 → 26 |
| query-command | 23 → 23 |
| reindex-command | 15 → 15 |
| workspace-init | 29 → 29 |

## Findings during apply

- **Redirects leaked the key.** `urllib.request.HTTPRedirectHandler.redirect_request` copies every non-content header, including `Authorization`, onto the redirected request. The client's default transport refuses all redirects; a two-server loopback test pins it (#1099).
- **Ollama stays byte-identical.** The Ollama embedding tag is unchanged (`bge-m3#chunk-v1`), and a legacy tag parses as `ollama`, so upgrading forces no re-embed.
