# Delta for Entity Resolution Adjudication

## MODIFIED Requirements

### Requirement: Degrade-On-No-Model Mirrors `query`'s 3-Tier Catch

For the `ollama` backend, the `adjudicate` verb MUST catch
`OllamaUnavailable`, then `OllamaModelNotFound`, then generic `OllamaError`
(in that subclass order), report a clear actionable message, and write
nothing, mirroring `query`'s degrade contract. WHEN the caught exception is
`OllamaUnavailable`, the message MUST additionally point to `openkos doctor`
to diagnose the environment, mirroring `query`'s `OllamaUnavailable`
wording; the `OllamaModelNotFound` and generic `OllamaError` messages are
unchanged. This wording MUST remain byte-identical to before this change.

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
(Previously: the `OllamaUnavailable` message told the user to run
`ollama serve` with no additional pointer to `openkos doctor`.)
(Previously: only the `ollama` backend existed, so this requirement named
Ollama's exception classes and wording unconditionally, with no
backend-conditional branch.)

#### Scenario: Ollama unreachable also points to doctor

- GIVEN `adjudicate_candidates` raises `OllamaUnavailable`
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
