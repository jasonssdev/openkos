# Delta for Query Command

## MODIFIED Requirements

### Requirement: Happy-Path Answer Rendering

Given a workspace whose bundle answers the question, `query` MUST read the
configured model via `read_config(root).model`, build an `OllamaClient` for
chat, build an `Embedder` (`OllamaClient(cfg.embedding_model)`) and open the
vector store via `open_vector_store(layout.vectors_db_path)`, call
`retrieval.answer(question, bundle_dir=layout.bundle_dir, llm=client,
embedder=embedder, vector_store=vector_store, limit=n)`, and render to
stdout the answer text followed by each citation as `concept_id` and
`title`. The process MUST exit 0.
(Previously: only the chat `OllamaClient` was built; no dense seams were
constructed or injected.)

#### Scenario: Matching answer with citations

- GIVEN a workspace whose bundle contains concepts matching the question
- WHEN `openkos query "<question>"` is run
- THEN `query` builds and injects both the `Embedder` and the vector store,
  stdout contains the returned answer text followed by one line per
  citation showing that citation's `concept_id` and `title`, and the
  process exits 0

### Requirement: Stderr Retrieval Summary On Every Run

`query` MUST print a one-line retrieval summary to stderr on every
completed run (successful answer or no-match), stating `fts_hit_count`,
`dense_hit_count`, `fused_count`, whether the LLM was invoked, and
`cited_count`. STDOUT MUST carry only the answer text and (when present)
the `Citations:` block — unchanged in shape from current behavior.
(Previously: the stderr line reported `fts_hit_count` only, with no dense
or fused counts.)

#### Scenario: Successful answer keeps stdout pipe-clean

- GIVEN a workspace whose bundle answers the question
- WHEN `openkos query "<question>"` is run
- THEN stdout (captured via `capsys`/`capfd`) contains exactly the answer
  text plus the `Citations:` block, with no summary text mixed in
- AND stderr (captured separately) contains one line reporting
  `fts_hit_count`, `dense_hit_count`, `fused_count`, LLM-invoked status,
  and `cited_count`

#### Scenario: No-match run still emits a stderr summary

- GIVEN `answer()` returns a no-match `AnswerResult`
- WHEN `openkos query "<question>"` is run
- THEN stderr reports the extended retrieval summary for that run
  (including zero counts where applicable) and the process exits `0`

## ADDED Requirements

### Requirement: Dense-Unavailable Runs Degrade And Hint At Reindex

WHEN dense retrieval degrades (absent/empty `vectors.db`, `VecUnavailable`,
or a read-path `sqlite3.Error`), `query` MUST still complete on the
FTS-only fused result, exit `0`, and print an additional stderr hint
telling the user to run `openkos reindex` to enable semantic retrieval.
STDOUT MUST remain unaffected — answer text and citations only, computed
from FTS-only fusion.

#### Scenario: Cold store (never reindexed) hints at reindex

- GIVEN `vectors.db` does not exist under the current workspace
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0, stdout renders the FTS-only answer and
  citations unaffected, and stderr includes a hint to run
  `openkos reindex`

#### Scenario: Locked or corrupt vectors.db degrades with the same hint

- GIVEN `vector_store.query` raises a read-path `sqlite3.Error` or
  `VecUnavailable`
- WHEN `openkos query "<question>"` is run
- THEN the process exits 0 on the FTS-only fused result, and stderr
  includes the same reindex hint
