# Proposal: query-aware-excerpts-default-on

Refs #1647, #1652. Governing decision: ADR-0082 (Proposed).

## Intent

Make `query_aware_excerpts` default on. The pre-registered comparison
(`evals/retrieval_gold/RESULTS-1647.md`, verdict SUCCESS) raised delivered gold
evidence from 13 to 25 of 54 at k=5 and from 6 to 18 at k=8.

## Scope

1. `config.DEFAULT_QUERY_AWARE_EXCERPTS` becomes `True`; the packaged template and
   `examples/good-life-demo/openkos.yaml` show the opt-out line.
2. `answer()` keeps its `False` default: library and eval callers opt in.
3. Delta: MODIFIED requirement "Context Excerpts May Be Chosen By Similarity To The Question".
