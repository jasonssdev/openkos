# Delta for Query Command

## ADDED Requirements

### Requirement: FTS/Graph-Unavailable Runs Degrade And Hint At Reindex

WHEN the persisted FTS or graph derived index is absent or its on-disk store
is unopenable/corrupt (the same condition `answer()` degrades on), `query`
MUST still complete using whichever retrieval lists remain available, exit
`0`, and print an additional stderr hint telling the user to run
`openkos reindex`. STDOUT MUST remain unaffected — answer text and citations
only, computed from whatever lists were available. `query` MUST NOT recompute
or compare the current bundle's manifest hash to reach this decision — per
design D2, staleness detection is reindex's exclusive job; a properly-
reindexed handle is always treated as fresh at query time. This mirrors the
existing dense-unavailable hint.

#### Scenario: Never-reindexed workspace hints at reindex for FTS/graph too

- GIVEN a workspace that has never run `reindex` (no persisted FTS or graph
  index exists)
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0, stdout renders whatever answer the remaining
  retrieval lists support, and stderr includes a hint to run
  `openkos reindex`

#### Scenario: Corrupt or unopenable FTS/graph index degrades with the same hint

- GIVEN a persisted FTS or graph index whose on-disk store cannot be opened
  (e.g. a corrupt file), and no query-time manifest comparison is performed
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0 on the remaining available lists, and stderr
  includes the reindex hint

### Requirement: Docstring No Longer Claims No Persisted State

The `query` command's docstring (`cli/main.py:2226`) MUST no longer state
that graph/FTS retrieval carries "no persisted state, no CLI-level graph
command"; it MUST describe graph and FTS retrieval as reading persisted,
`reindex`-written on-disk indexes.

#### Scenario: Docstring reflects the persisted-index contract

- GIVEN `cli/main.py`'s `query` command docstring
- WHEN a reader reviews it after this change
- THEN it states that graph and FTS retrieval read persisted on-disk indexes
  maintained by `reindex`, and no longer claims no persisted state exists

## MODIFIED Requirements

### Requirement: Happy-Path Answer Rendering

Given a workspace whose bundle answers the question, `query` MUST read the
configured model via `read_config(root).model`, build an `OllamaClient` for
chat, build an `Embedder` (`OllamaClient(cfg.embedding_model)`), open the
vector store via `open_vector_store(layout.vectors_db_path)`, and open the
persisted FTS and graph derived indexes read-only under `.openkos/`, call
`retrieval.answer(question, bundle_dir=layout.bundle_dir, llm=client,
embedder=embedder, vector_store=vector_store, fts_index=fts_index,
graph_index=graph_index, limit=n)`, and render to stdout the answer text
followed by each citation as `concept_id` and `title`. The process MUST
exit 0.
(Previously: only the chat `OllamaClient`, `Embedder`, and vector store were
built and injected; `query` relied on `answer()` to build its own FTS index
and graph internally rather than opening and injecting persisted handles.)

#### Scenario: Matching answer with citations

- GIVEN a workspace whose bundle contains concepts matching the question
- WHEN `openkos query "<question>"` is run
- THEN `query` builds and injects the `Embedder`, vector store, and the
  read-only FTS and graph derived-index handles, stdout contains the
  returned answer text followed by one line per citation showing that
  citation's `concept_id` and `title`, and the process exits 0
