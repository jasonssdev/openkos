# Delta for Doctor Command

## ADDED Requirements

### Requirement: Doctor Behavior Unchanged By list_models() Contract Widening

Doctor's chat-model-installed and embedding-model-installed checks MUST
continue to report the exact same pass/fail outcomes as before
`list_models()`'s return contract widened to include per-model family.
Outcomes MUST depend solely on tag-normalized matching (`model_tag_matches`)
against the returned entries' tags, unaffected by the added family field —
this is a no-behavior-change requirement guarding the refactor.

#### Scenario: Configured model present in installed tags still passes

- GIVEN Ollama reports installed models via the widened `list_models()`
  shape, and the configured chat model tag matches one of them (exact or
  `:latest`-normalized)
- WHEN `openkos doctor` runs
- THEN the chat model-installed check prints `[PASS]`, identical to its
  outcome before the contract change

#### Scenario: Configured model absent still fails with pull remediation

- GIVEN Ollama reports installed models via the widened `list_models()`
  shape, and no entry matches the configured chat model tag
- WHEN `openkos doctor` runs
- THEN the chat model-installed check prints `[FAIL]` with a pull
  remediation naming the configured tag, identical to its outcome before
  the contract change

#### Scenario: Embedding-model check outcome also unchanged

- GIVEN Ollama reports installed models via the widened `list_models()`
  shape, and the configured `embedding_model` tag matches an installed
  entry
- WHEN `openkos doctor` runs
- THEN the embedding-model-installed check prints `[PASS]`, identical to
  its outcome before the contract change
