# Delta for Query Command

## MODIFIED Requirements

### Requirement: LLM And Index Errors Map To Exit 1

WHEN `answer()` raises an `OllamaError`-family exception (for the `ollama`
backend) or the analogous `OpenAICompatibleError`-family exception (for the
`openai-compatible` backend), or `FtsUnavailable`, `query` MUST catch it,
print a message to stderr, and exit 1 with no raw traceback reaching the
user. The stderr message MUST be actionable for each of the three enumerated
causes below and MUST remain generic for all other cases. For the `ollama`
backend, the wording below MUST remain byte-identical to before this change.

- WHEN the raised exception is `OllamaUnavailable` (or, for the
  `openai-compatible` backend, `OpenAICompatibleUnavailable`), the stderr
  message MUST state that the backend is not responding, MUST include the
  endpoint it tried to reach, and MUST additionally point to
  `openkos doctor` to diagnose the environment. For `ollama`, it MUST tell
  the user to start Ollama, referencing the `ollama serve` command. For
  `openai-compatible`, it MUST instead advise the user to verify the
  configured server is running at that endpoint, with no reference to
  `ollama serve` or any Ollama-specific command.
- WHEN the raised exception is `OllamaModelNotFound` (or, for
  `openai-compatible`, `OpenAICompatibleModelNotFound`), the stderr message
  MUST name the configured model that could not be found. For `ollama`, it
  MUST tell the user how to install it, referencing the
  `ollama pull <model>` command with the configured model name. For
  `openai-compatible`, it MUST instead advise the user to make that model
  available on the configured server, with no `ollama pull` reference.
- WHEN the raised exception is `OllamaEmbeddingDimensionMismatch` (or, for
  `openai-compatible`, `OpenAICompatibleEmbeddingDimensionMismatch`), the
  stderr message MUST identify the failure as a PERMANENT dimension
  mismatch caused by the configured embedding model, and MUST name
  restoring the working `embedding_model` value in `openkos.yaml` as the
  remedy. It MUST NOT be worded as transient or self-healing (never "will
  retry next run"), and MUST NOT point the user at `openkos reindex` as the
  remedy — `reindex` fails with this same permanent error until
  `openkos.yaml` is fixed. The reindex hint of the Dense-Unavailable Runs
  Degrade And Hint At Reindex requirement MUST NOT be printed for this
  cause: `answer()` propagates instead of setting `dense_degraded`, so a run
  that hits this error never reaches that hint.
- WHEN the raised exception is any other `OllamaError`/
  `OpenAICompatibleError` or `FtsUnavailable`, `query` MUST print a friendly
  (non-actionable-specific) failure message to stderr — unchanged from prior
  behavior.

For a dimension mismatch on either backend, the exit-1 refusal MUST be
UNCONDITIONAL. `answer()` runs lexical retrieval BEFORE dense retrieval, so
the mismatch surfaces only after FTS retrieval has already succeeded and may
already hold hits that would have grounded a citable answer. `query` MUST
still exit 1 and print nothing on stdout, discarding that already-successful
retrieval work: it MUST NOT print an FTS-only answer, MUST NOT offer any flag
that forces one (no `--fts-only`, no `--allow-degraded`), and MUST NOT make
the refusal conditional on whether FTS found hits. The accepted cost is
denying the user even the answers FTS could have grounded; the ONLY remedy is
restoring the working `embedding_model` in `openkos.yaml`, not a CLI flag.

(Previously: the `OllamaUnavailable` message told the user to run
`ollama serve` with no additional pointer to `openkos doctor`.)
(Previously: `OllamaEmbeddingDimensionMismatch` never reached this ladder —
`answer()` swallowed it into `dense_degraded`, so `query` printed a
successful FTS-only answer at exit 0, plus the misleading
`openkos reindex` hint, and never reported the misconfiguration.)
(Previously: the FTS hits gathered before the mismatch were still fused,
cited, and printed; this requirement deliberately discards them.)
(Previously: only the `ollama` backend existed, so every branch above named
Ollama's own exception classes and wording unconditionally, with no
backend-conditional branch.)

#### Scenario: Ollama backend unreachable

- GIVEN `answer()` raises `OllamaUnavailable` because Ollama is not running
  or not reachable at the configured host
- WHEN `openkos query "<question>"` is run
- THEN stderr states that Ollama is not responding, names the host it tried
  to reach, tells the user to run `ollama serve`, and also names
  `openkos doctor` to diagnose the environment
- AND the process exits 1 with no raw traceback shown

#### Scenario: Configured model not installed

- GIVEN `answer()` raises `OllamaModelNotFound` because the configured model
  has not been pulled
- WHEN `openkos query "<question>"` is run
- THEN stderr names the configured model and tells the user to run
  `ollama pull <model>` with that model's name
- AND the process exits 1 with no raw traceback shown

#### Scenario: Embedding model returns wrong-dimension vectors

- GIVEN `answer()` raises `OllamaEmbeddingDimensionMismatch` because the
  configured `embedding_model` does not emit `EMBED_DIM`-dimensional vectors
- WHEN `openkos query "<question>"` is run
- THEN stderr identifies the failure as a permanent dimension mismatch and
  tells the user to restore the working `embedding_model` value in
  `openkos.yaml`
- AND stderr does NOT suggest running `openkos reindex` and is never worded
  as transient ("will retry next run")
- AND the process exits 1 with no answer on stdout and no raw traceback shown

#### Scenario: Refusal stands even when FTS retrieval already succeeded

- GIVEN a workspace whose FTS index matches the question, so the same run
  would have printed a cited FTS-only answer at exit 0 had the embedding
  model been healthy
- AND the configured `embedding_model` returns a wrong-dimension embedding,
  so `answer()` raises `OllamaEmbeddingDimensionMismatch` AFTER those FTS
  hits were already retrieved
- WHEN `openkos query "<question>"` is run
- THEN the process exits 1 with nothing on stdout — no answer, no citation,
  and no flag exists that would force the degraded FTS-only answer instead
- AND the only remedy is restoring the working `embedding_model` value in
  `openkos.yaml`

#### Scenario: Other Ollama error

- GIVEN `answer()` raises an `OllamaError`-family exception that is neither
  `OllamaUnavailable` nor `OllamaModelNotFound`
- WHEN `openkos query "<question>"` is run
- THEN a friendly failure message is printed to stderr and the process exits
  1, with no raw traceback shown

#### Scenario: FTS index unavailable

- GIVEN `answer()` raises `FtsUnavailable`
- WHEN `openkos query "<question>"` is run
- THEN a friendly failure message is printed to stderr and the process exits
  1

#### Scenario: openai-compatible backend unreachable — no Ollama wording

- GIVEN `cfg.backend == "openai-compatible"` and `answer()` raises
  `OpenAICompatibleUnavailable`
- WHEN `openkos query "<question>"` is run
- THEN stderr states the backend is not responding, names the configured
  endpoint, advises verifying the server is running, and also names
  `openkos doctor`
- AND stderr contains no reference to `ollama serve`
- AND the process exits 1 with no raw traceback shown

#### Scenario: openai-compatible model not found — no ollama pull reference

- GIVEN `cfg.backend == "openai-compatible"` and `answer()` raises
  `OpenAICompatibleModelNotFound`
- WHEN `openkos query "<question>"` is run
- THEN stderr names the configured model and advises making it available on
  the configured server, with no `ollama pull` reference
- AND the process exits 1 with no raw traceback shown

#### Scenario: openai-compatible embedding dimension mismatch is the same permanent remedy

- GIVEN `cfg.backend == "openai-compatible"` and `answer()` raises
  `OpenAICompatibleEmbeddingDimensionMismatch`
- WHEN `openkos query "<question>"` is run
- THEN stderr identifies the failure as a permanent dimension mismatch and
  tells the user to restore the working `embedding_model` value in
  `openkos.yaml`, exactly as for the `ollama` backend
- AND the process exits 1 with nothing on stdout

### Requirement: `--save` Discloses A Possible Duplicate Before Confirming

BEFORE the `--save` confirmation gate, `query` MUST disclose already-filed
insights whose SOURCE QUESTION resembles the question being filed (#762),
one line per candidate, most-similar first.

The lookup MUST run on the question, never on the answer body or the derived
title: both were measured and OVERLAP, with title similarity scoring a
perfect match on a pair of unrelated subjects.

The disclosure MUST be advisory. `query` MUST NOT merge, rename, refuse or
otherwise alter the filing because of it, and an unreachable embedding
backend MUST disclose nothing rather than fail the save.

#### Scenario: A resembling filing is disclosed and the save still writes

- GIVEN an insight already filed from a resembling question
- WHEN `openkos query "<question>" --save --auto` runs
- THEN a possible-duplicate line names that insight and its source question,
  and the new insight is still written

WHEN the lookup could not run — the embedding backend failed, or returned a
malformed batch — `query` MUST say so on stderr rather than rendering the
same silence as a scan that ran and found nothing (#764). Having nothing to
compare against is NOT such a case: that scan ran correctly.

`query` MUST likewise announce on stderr when the pre-synthesis sufficiency
check was requested and could not run, so an answer produced without the
configured guard is distinguishable from one the guard allowed.

#### Scenario: An unavailable lookup is announced and the save still writes

- GIVEN the embedding backend fails during `--save`
- WHEN `openkos query "<question>" --save --auto` runs
- THEN stderr says the question could not be checked against filed insights,
  and the new insight is still written

#### Scenario: No candidate, no line

- GIVEN no filed insight resembles the question
- WHEN `openkos query "<question>" --save --auto` runs
- THEN no possible-duplicate line appears

The lookup MUST compare against EVERY comparable already-filed insight, and
MUST report that it could not run rather than comparing only some of them
(#764). A partial comparison that renders like a complete one is the failure
to avoid, and once the scan promises all of them there is no count left to
disclose a shortfall with.

To make that affordable, `query` MUST cache each filed insight's source-question
embedding and re-embed only questions it has not seen, or whose text changed.
Cached vectors MUST be keyed by embedding model AND embedding backend,
following the same key rule `reindex`'s embedding tag uses (see
`reindex-command`'s "Embedding-Model Tag Gate Forces Full Re-Embed On
Mismatch"): the bare model name for the `ollama` backend, so a cache built
before this change stays valid with no forced re-embed, and a
backend-qualified form for `openai-compatible`, so a backend switch never
reuses a question vector cached under the other backend even when the model
name is identical. Cached vectors MUST be dropped when their insight leaves
the bundle, and MUST be treated as a rebuildable cache: losing the store
costs re-embedding, never correctness.

WHEN the cache is unavailable, `query` MUST report the lookup as one that could
not run. It MUST NOT fall back to embedding every filed question: that is the
cost this design removes, and it would stall the confirmation gate with nothing
on screen explaining the wait.

#### Scenario: Every filed insight is compared, including the oldest

- GIVEN a bundle with more filed insights than any previous bound allowed
- WHEN `openkos query "<question>" --save --auto` runs
- THEN the oldest filing is eligible for disclosure on the same terms as the
  newest, and no line claims a partial comparison

#### Scenario: A warm cache re-embeds nothing

- GIVEN a save that already embedded every filed insight's question
- WHEN a second `openkos query "<question>" --save --auto` runs
- THEN no already-cached question is embedded again

#### Scenario: A backend switch does not reuse a cached question vector

- GIVEN a warm question-vector cache built while `backend: ollama` was
  configured with embedding model `bge-m3`
- WHEN the workspace is reconfigured to `backend: openai-compatible` with the
  same embedding model name and `openkos query "<question>" --save --auto`
  runs
- THEN the cache key for that model changes to a backend-qualified form, no
  cached `ollama`-backend vector is reused, and the affected questions are
  re-embedded under the new backend

#### Scenario: An ollama-backend cache key stays bare, so existing rows remain valid

- GIVEN a question-vector cache written before this change, keyed by bare
  model name only
- WHEN this change is applied and `backend: ollama` stays configured
- THEN the cache key for that model is unchanged (the bare model name), and
  previously cached rows remain valid with no forced re-embed

#### Scenario: No cache is a lookup that could not run

- GIVEN the question-vector cache cannot be opened
- WHEN `openkos query "<question>" --save --auto` runs
- THEN stderr says the question could not be checked, and the new insight is
  still written

WHEN the configured embedding endpoint is NOT verifiably this machine — for
either backend, decided by the shared locality classifier — `--save` MUST
announce, before the send, that already-filed source questions are
transmitted too, naming the ceiling (#764). The standing non-local
embedding-host advisory (`sensitivity-aware-llm`'s "Embedding Is Gated As
Egress" requirement) covers the question just typed; a save additionally
ships other filings' questions, which is a different disclosure rather than
a louder one. A `query` without `--save` MUST NOT print it: no filed
question is sent there.
(Previously: this paragraph and its scenario named `OLLAMA_HOST`
specifically, because no other backend or endpoint source existed; the
condition is now the shared locality classifier's verdict on the effective
embedding endpoint, whichever backend and resolution source produced it.)

#### Scenario: A remote embedding host is told what a save sends

- GIVEN the effective embedding endpoint (resolved per `backend-selection`,
  whether from `OLLAMA_HOST` for `ollama` or the configured `base_url`/
  `embedding_base_url` for `openai-compatible`) names a host that is not
  this machine
- WHEN `openkos query "<question>" --save --auto` runs
- THEN stderr announces that already-filed source questions are sent, with
  the ceiling, and the credentialed host value stays redacted

#### Scenario: A remote openai-compatible embedding endpoint gets the same disclosure

- GIVEN `cfg.backend == "openai-compatible"` and the configured
  `embedding_base_url` names a non-loopback host
- WHEN `openkos query "<question>" --save --auto` runs
- THEN stderr announces that already-filed source questions are sent, with
  the ceiling, identically in shape to the `ollama`/`OLLAMA_HOST` case
