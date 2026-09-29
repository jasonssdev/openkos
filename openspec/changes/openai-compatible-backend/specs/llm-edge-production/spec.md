# Delta for LLM Edge Production

## MODIFIED Requirements

### Requirement: Ollama Unavailability Points To `doctor`

WHEN the suggestion verb's underlying `suggest_relations` call raises
`OllamaUnavailable` (for the `ollama` backend) or `OpenAICompatibleUnavailable`
(for the `openai-compatible` backend), the CLI MUST catch it before the
generic `OllamaError`/`OpenAICompatibleError` handler, print to stderr a
message that states the backend is not responding, and additionally points
to `openkos doctor` to diagnose the environment, then exit 1 with zero
writes to any bundle file. For `ollama`, the message MUST tell the user to
start it with `ollama serve`, byte-identical to before this change. For
`openai-compatible`, the message MUST instead advise verifying the
configured server is running at its endpoint, with no `ollama serve`
reference. The `OllamaModelNotFound`/`OpenAICompatibleModelNotFound` and
generic `OllamaError`/`OpenAICompatibleError` branches, and their ordering
relative to the unavailable exception, MUST remain unchanged.
(Previously: only the `ollama` backend existed, so this requirement named
`OllamaUnavailable` and `ollama serve` unconditionally, with no
backend-conditional branch.)

#### Scenario: Ollama unreachable points to doctor

- GIVEN `suggest_relations` raises `OllamaUnavailable`
- WHEN the suggestion verb runs
- THEN stderr tells the user to run `ollama serve` and also names
  `openkos doctor` to diagnose the environment
- AND the process exits 1 with zero writes to any bundle file

#### Scenario: Model-not-found and generic errors unchanged

- GIVEN `suggest_relations` raises `OllamaModelNotFound` or a generic
  `OllamaError`
- WHEN the suggestion verb runs
- THEN the existing pull-remedy or generic failure message is printed
  unchanged, with no `doctor` pointer added
- AND the process exits 1

#### Scenario: openai-compatible backend unreachable points to doctor, no Ollama wording

- GIVEN `cfg.backend == "openai-compatible"` and `suggest_relations` raises
  `OpenAICompatibleUnavailable`
- WHEN the suggestion verb runs
- THEN stderr advises verifying the configured server is running at its
  endpoint, names `openkos doctor`, and contains no `ollama serve` reference
- AND the process exits 1 with zero writes to any bundle file
