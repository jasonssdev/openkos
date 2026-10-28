# Proposal: query-aware-excerpts

## Intent

Refs #1647 (umbrella #1652). When a retrieved document exceeds its share of the
prompt window, `prompt_budget.bounded_text` keeps evenly spaced 800-char windows
regardless of the question, so the only passage that answers it can be elided.
This change adds an opt-in mode that keeps the windows most similar to the
question instead, at no extra model cost.

Success: with `query_aware_excerpts: true`, the window holding the question's
terms survives where even coverage would drop it; with the key absent or false,
every prompt is byte-identical to today.

## Scope

### In Scope

1. A boolean `openkos.yaml` key `query_aware_excerpts`, default `false`
   (`config.DEFAULT_QUERY_AWARE_EXCERPTS`), validated like every other flag and
   documented in the packaged template.
2. `answer(..., query_aware_excerpts=False)`; `application.query.run_query`
   passes `cfg.query_aware_excerpts`, so CLI, MCP and API share it.
3. `bounded_text(..., query=None)`: when a query is given, windows are ranked by
   Okapi BM25 over the document's own windows (model-free, deterministic, no
   embedding call), chosen greedily under the unchanged budget accounting,
   rejoined in document order with the existing marker/adjacency rule.

### Out of Scope

- Making it the default: that needs the pre-registered measurement on
  `evals/retrieval_gold` (#1646 delivered-evidence metric).
- A dense (embedding) variant, head/tail pinning, or any change to
  `fair_shares`, the budget, or the marker.

## Approach

Greedy by `(-score, window index)`; a window that no longer fits is skipped.
Zero-score windows fill leftover budget in document order. A query with no
usable term (after lowercasing and dropping single characters), or one matching
no window, falls back to even coverage; a fitting text is returned unchanged.

## Rollback

Leave the key false (the default), or revert: the parameter defaults off at
every layer, so nothing else depends on it.

## Risks

The choice is unmeasured, hence opt-in. BM25 constants are the textbook values,
untuned.
