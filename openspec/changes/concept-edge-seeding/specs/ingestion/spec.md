# Delta for Ingestion

## ADDED Requirements

### Requirement: Ingest Triggers Candidate-Edge Computation With Graceful Embedder Degradation

`ingest` MUST trigger candidate-edge computation (the graph-projection
third pass) in the SAME run, so a fresh `ingest` shows candidate edges
without a follow-up invocation. Doing so requires `ingest` to hold an
Embedder dependency it does not have today (it currently builds only a
chat client). An unreachable or failing embedder MUST NOT fail the
`ingest` write: `ingest` MUST keep the Source (and any extracted
derived objects), emit an explanatory note to stderr distinguishing
this degrade from the existing concept-extraction-skipped degrade, and
exit 0 — the same non-fatal shape as today's Ollama-unreachable
extraction degrade. Candidate-edge computation MUST NOT block or delay
the Source/derived-object write path on success or failure.

#### Scenario: Successful ingest surfaces candidate edges in the same run

- GIVEN an initialized workspace, a reachable embedder, and a source
  whose content is close in embedding space to an existing concept
- WHEN `openkos ingest <path>` completes
- THEN candidate edges involving the newly ingested concept(s) are
  visible via the graph projection without any further command

#### Scenario: Unreachable embedder degrades without failing the write

- GIVEN an initialized workspace and an embedder that is unreachable or
  raises an error
- WHEN `openkos ingest <path>` runs
- THEN the Source concept (and any successfully extracted derived
  objects) is still written, a note distinguishing the embedder
  degrade from the concept-extraction degrade appears on stderr, and
  the command exits 0

#### Scenario: Missing or empty vector store does not fail ingest

- GIVEN an initialized workspace whose `vectors.db` is absent or empty
  at the time of ingest
- WHEN `openkos ingest <path>` runs
- THEN the ingest write completes normally and exits 0, with zero
  candidate edges produced for this run
