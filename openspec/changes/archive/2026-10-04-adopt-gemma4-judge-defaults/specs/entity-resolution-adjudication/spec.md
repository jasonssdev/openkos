# Entity Resolution Adjudication Specification (delta)

## MODIFIED Requirements

### Requirement: Degrade-On-No-Model Mirrors `query`'s 3-Tier Catch

For the `ollama` backend, the `adjudicate` verb MUST catch
`BackendUnavailable`, then `BackendModelNotFound`, then generic `BackendError`
(in that subclass order), report a clear actionable message, and write
nothing, mirroring `query`'s degrade contract. WHEN the caught exception is
`BackendUnavailable`, the message MUST additionally point to `openkos doctor`
to diagnose the environment, mirroring `query`'s `BackendUnavailable`
wording; the `BackendModelNotFound` and generic `BackendError` messages keep
their own wording, byte-for-byte, with no `openkos doctor` pointer added.

For the `ollama` backend the `BackendModelNotFound` message, and the partial
batch failure line, MUST name the model the `adjudication` task resolved
(`config.resolve_task_model`), together with its pull command, never the
global `model:` when the two differ; the verb MUST NOT fall back to the
global model.

For the `openai-compatible` backend, `adjudicate` MUST catch the analogous
`OpenAICompatibleUnavailable`, then `OpenAICompatibleModelNotFound`, then
generic `OpenAICompatibleError`, in the same subclass order, following the
same 3-tier degrade contract. WHEN the caught exception is
`OpenAICompatibleUnavailable`, the message MUST name the configured endpoint,
advise verifying the configured server is running, and point to
`openkos doctor`, with no `ollama serve` reference. The
`OpenAICompatibleModelNotFound` message MUST name the configured model and
advise making it available on the configured server, with no `ollama pull`
reference.

#### Scenario: Ollama unreachable also points to doctor

- GIVEN `adjudicate_candidates` raises `BackendUnavailable`
- WHEN `openkos adjudicate` runs
- THEN stderr tells the user to run `ollama serve` and also names
  `openkos doctor` to diagnose the environment
- AND the process exits 1 with zero bundle writes

#### Scenario: No model available degrades cleanly

- GIVEN no local Ollama server or configured model is reachable
- WHEN `adjudicate` runs
- THEN it reports a clear actionable error, performs zero bundle writes,
  and exits without an unhandled traceback

#### Scenario: openai-compatible backend unreachable points to doctor, no Ollama wording

- GIVEN `cfg.backend == "openai-compatible"` and `adjudicate_candidates`
  raises `OpenAICompatibleUnavailable`
- WHEN `openkos adjudicate` runs
- THEN stderr names the configured endpoint, advises verifying the server is
  running, and also names `openkos doctor`, with no `ollama serve` reference
- AND the process exits 1 with zero bundle writes

#### Scenario: openai-compatible model not found — no ollama pull reference

- GIVEN `cfg.backend == "openai-compatible"` and `adjudicate_candidates`
  raises `OpenAICompatibleModelNotFound`
- WHEN `openkos adjudicate` runs
- THEN stderr names the configured model and advises making it available on
  the configured server, with no `ollama pull` reference
- AND the process exits 1 with zero bundle writes

#### Scenario: A missing packaged judge is named by its own tag

- GIVEN `cfg.backend == "ollama"`, no `models:` key, and the model is not
  installed
- WHEN `openkos adjudicate` runs
- THEN stderr names `gemma4:26b-a4b` and `ollama pull gemma4:26b-a4b`, not
  the global `model:`, the process exits 1, and nothing is written
