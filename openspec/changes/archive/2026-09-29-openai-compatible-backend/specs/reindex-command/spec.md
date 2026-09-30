# Delta for Reindex Command

## MODIFIED Requirements

### Requirement: Embedding-Model Tag Gate Forces Full Re-Embed On Mismatch

At the start of the vector reindex pass, `reindex()` MUST read the stored
`embedding_model` tag from `vectors.db`'s `meta` table and compare it against
the explicit `model_tag` param passed in for this run. For the `ollama`
backend this comparison and the stored tag's bytes are UNCHANGED from before
this change. For the `openai-compatible` backend, the tag additionally
identifies the backend kind, so switching `backend` while keeping the same
model name is itself a mismatch. A stored tag with no backend-kind qualifier
MUST be read as backend `ollama` for this comparison, so an existing
Ollama-only store sees NO forced re-embed purely from upgrading to a version
of `openkos` that understands backend-qualified tags. If the stored tag is
absent OR differs from `model_tag` under this comparison, the vector-store
pass for this run MUST behave as if `force=True` (bypass the content_hash
cache gate; every discovered, readable doc is queued for re-embedding via
the existing `upsert_many` DELETE+INSERT path — no vec0 DROP), and after the
embed pass completes, the new `model_tag` MUST be persisted as the stored
tag ONLY WHEN this run's `skipped` count is `0` AND its `embed_failed` count
is `0`. WHEN one or more queued docs were left un-(re)embedded this run —
via a permanent `skipped` (unreadable/parse/decode) OR a transient
`embed_failed` (embed EOF exhausted retries; see the Per-Doc Embed Failure
Is Isolated, Not Fatal requirement) — the tag MUST NOT be persisted, so the
NEXT run with the same `model_tag` still sees the mismatch and re-forces the
full re-embed, giving the previously-unhealed doc(s) another chance; this
repeats until one run finally reaches `skipped == 0 AND embed_failed == 0`,
at which point the tag is persisted and the store is no longer transiently
mixed-model. This gate is independent of the `--force` CLI flag (either can
trigger the same force-mode behavior) and MUST NOT affect the
`_reindex_fts`/graph pass, which stays gated solely by the bundle-manifest
hash. Switching servers within one backend kind while keeping the same model
name (e.g. moving `bge-m3` from LM Studio to vLLM) is NOT detected by this
gate — see the `openai-compatible-client`/`backend-selection` risk
documentation; `reindex --force` is the remedy for that case.
(Previously: the comparison was model-name-only, with no backend-kind
component, because only one backend existed.)

#### Scenario: Model mismatch forces full re-embed regardless of content_hash

- GIVEN a `vectors.db` with a stored tag `'model-a'` and every doc's
  content_hash already matching `vector_meta`
- WHEN `reindex()` runs with `model_tag='model-b'`
- THEN every discovered doc is re-embedded and upserted, and the stored tag
  becomes `'model-b'`

#### Scenario: Absent tag (pre-slice vectors.db) forces one re-embed then self-heals

- GIVEN a `vectors.db` created before this change, with no `meta` table row
  for `embedding_model`
- WHEN `reindex()` runs once with `model_tag='model-a'`
- THEN every discovered doc is re-embedded this run, the stored tag becomes
  `'model-a'`, and the NEXT `reindex()` run with the same `model_tag` is
  purely incremental (content_hash gate governs normally)

#### Scenario: Matching tag leaves the content_hash gate unchanged

- GIVEN a stored tag equal to the current `model_tag`
- WHEN `reindex()` runs
- THEN cache-hit/changed/new classification for each doc follows the
  existing content_hash comparison exactly as before this change

#### Scenario: Model-tag mismatch does not trigger an FTS/graph rebuild

- GIVEN a stored tag that differs from `model_tag`, and a bundle whose
  documents are otherwise unchanged
- WHEN `reindex()` runs
- THEN the FTS and graph derived indexes are NOT rebuilt by this gate (only
  the bundle-manifest hash, unaffected by the model tag, governs their
  rebuild)

#### Scenario: Any left-behind doc during a model-change run withholds the tag and self-heals

- GIVEN a model-change run where one doc is left un-embedded this run —
  either a permanent `skipped` (unreadable/parse/decode) or a transient
  `embed_failed` (embed EOF exhausted retries) — and the rest succeed
- WHEN `reindex()` completes this run
- THEN the stored tag remains the OLD (or absent) value, NOT `model_tag`,
  and the NEXT `reindex()` call with the same `model_tag` re-forces a full
  re-embed of every doc (`model_changed` stays `True`) until a run finally
  reaches `skipped == 0 AND embed_failed == 0`, at which point the tag is
  persisted

#### Scenario: Partial embed failure during a model change leaves a transient mixed-model store

- GIVEN a model-change run where some docs succeed on the new model and one
  transiently fails (`embed_failed`) and keeps its old-model vector
- WHEN that run completes
- THEN the store transiently contains both new-model (survivor) and
  old-model (failed) vectors simultaneously, `query` retrieval is
  unaffected (dense search does not depend on the stored tag), and the
  mixed state is surfaced to the user via the actionable re-run notice
  rather than left silent

#### Scenario: An existing Ollama-only store forces no re-embed on upgrade

- GIVEN a `vectors.db` whose stored `embedding_model` tag is the legacy,
  backend-unqualified `bge-m3#chunk-v1`, written before this change
- WHEN `reindex()` runs after upgrading to a version of `openkos` that
  understands backend-qualified tags, with `backend: ollama` and the same
  `model_tag`
- THEN the stored tag is read as backend `ollama`, the comparison finds no
  mismatch, and no forced re-embed occurs

#### Scenario: Switching backend with the same model name forces a re-embed

- GIVEN a stored tag identifying backend `ollama` with model `bge-m3`, and a
  workspace now configured with `backend: openai-compatible` and
  `embedding_model: bge-m3`
- WHEN `reindex()` runs
- THEN the backend-kind mismatch forces a full re-embed, exactly as a
  model-name mismatch would

### Requirement: `reindex()` Accepts An Explicit Model Tag Parameter

`state.reindex.reindex()` MUST accept an explicit string parameter — the
current run's EFFECTIVE embedding-model tag — used solely to compare against
and update the stored `embedding_model` tag. For the `ollama` backend this
value MUST remain exactly `cfg.embedding_model`, byte-identical to before
this change. For the `openai-compatible` backend this value MUST
additionally identify the backend kind, in a form that cannot be confused
with a bare Ollama-style model tag (which may itself contain `:`). A stored
tag read back with no backend-kind qualifier MUST be treated as backend
`ollama` (see the Embedding-Model Tag Gate requirement above). The
`Embedder` Protocol MUST NOT gain a model-identity accessor — the tag flows
only through this explicit param, never through the embed-only seam.
(Previously: this parameter was described as "the current
`cfg.embedding_model` value" unconditionally, since only the `ollama`
backend existed and the tag never needed to carry a backend identity.)

#### Scenario: CLI wires the configured model into reindex for the ollama backend

- GIVEN `cfg.backend == "ollama"` and `cfg.embedding_model` resolved from
  `openkos.yaml`
- WHEN `openkos reindex` invokes the orchestrator
- THEN the exact `cfg.embedding_model` string, with no backend qualifier, is
  passed as the model-tag param, and the `Embedder` Protocol's method
  surface is unchanged

#### Scenario: CLI wires a backend-qualified tag for the openai-compatible backend

- GIVEN `cfg.backend == "openai-compatible"` and `cfg.embedding_model`
  resolved from `openkos.yaml`
- WHEN `openkos reindex` invokes the orchestrator
- THEN the model-tag param passed identifies both the backend kind and the
  model, in a form that cannot collide with a bare Ollama-style tag

### Requirement: Reindex Discloses The Real Re-Embed Trigger, Not A False Model-Change Claim

WHEN `reindex` forces a full re-embed via the Embedding-Model Tag Gate, its
CLI summary MUST compare the stored effective tag and the current effective
tag and print exactly one of four true statements, checked in this order:
(1) if no previous tag was stored,
`no embedding-model tag stored (fresh or dropped store)`; (2) if a previous
tag was stored and its backend-kind part differs from the current backend
(treating a legacy, backend-unqualified stored tag as `ollama` for this
comparison),
`embedding backend changed (<old-backend> -> <new-backend>)`; (3) if the
backend parts are equal and the model parts differ,
`embedding model changed (<old-model> -> <new-model>)`; (4) if the backend
and model parts are equal and only the composition part differs,
`embed text composition changed (<old-comp> -> <new-comp>); your embedding
model is unchanged (<model>)`. It MUST NEVER compare the stored effective
tag against the bare configured model name alone — that comparison falsely
reports a model change when only the embed-text composition (e.g. chunking)
changed, or falsely reports a model change when only the backend kind
changed with the model name held constant. A legacy tag with no
backend-kind qualifier MUST NOT, by itself, cause statement (2) to print
when the currently configured backend is `ollama` — upgrading to a version
that emits backend-qualified tags MUST NOT be reported as a backend change.
Every branch MUST also report `embed_calls` over `embedded` documents.
(Previously: this requirement enumerated three statements — model changed,
composition changed, no tag stored — with no backend-changed statement,
because only one backend existed.)

#### Scenario: A composition-only bump reports composition, not model, change

- GIVEN a stored tag `bge-m3#compose-v1` and a current tag `bge-m3#chunk-v1`,
  both backend `ollama`
- WHEN `reindex` forces the re-embed and prints its summary
- THEN it prints the composition-changed wording naming
  `compose-v1 -> chunk-v1` and states the embedding model `bge-m3` is
  unchanged, never "embedding model changed"

#### Scenario: A genuine model bump still reports a model change

- GIVEN a stored tag `bge-m3#chunk-v1` and a current tag
  `qwen3-embedding:0.6b#chunk-v1`, both backend `ollama`
- WHEN `reindex` forces the re-embed and prints its summary
- THEN it prints the model-changed wording naming
  `bge-m3 -> qwen3-embedding:0.6b`

#### Scenario: A fresh or dropped store reports the absent-tag wording

- GIVEN no previous tag is stored
- WHEN `reindex` forces the re-embed and prints its summary
- THEN it prints the fresh-or-dropped-store wording, naming neither a model,
  a backend, nor a composition change

#### Scenario: Every disclosure branch reports the chunk-multiplied call count

- GIVEN any of the four branches above
- WHEN `reindex` completes
- THEN the summary reports `embed_calls`, which for a chunked run exceeds
  the count of `embedded` documents whenever any document produced more
  than one chunk

#### Scenario: A genuine backend change reports a backend change, not a model change

- GIVEN a stored tag identifying backend `ollama` with model `bge-m3`, and a
  current run configured with backend `openai-compatible` and the same
  model name `bge-m3`
- WHEN `reindex` forces the re-embed and prints its summary
- THEN it prints the backend-changed wording naming
  `ollama -> openai-compatible`, never "embedding model changed"

#### Scenario: A legacy tag upgraded to backend-qualified tags is never reported as a backend change

- GIVEN a stored tag with no backend-kind qualifier (written before this
  change), and a current run configured with `backend: ollama` and the same
  model name
- WHEN `reindex` runs after the upgrade
- THEN no forced re-embed occurs (per the Embedding-Model Tag Gate
  requirement) and, in the counterfactual case where some other change also
  forces a re-embed, the summary never attributes it to a backend change

(`purge`'s own pre-emptive quoting of this wording is `privacy-purge`'s
requirement, not this one — see the `privacy-purge` delta's MODIFIED
"Deferred-Reembed Warning On Success" for that scenario, which asserts on
`purge`'s output, not `reindex`'s.)

#### Scenario: query command behavior is unchanged

- GIVEN this change is applied
- WHEN the existing `query` command runs
- THEN its observable behavior is identical to before this change
