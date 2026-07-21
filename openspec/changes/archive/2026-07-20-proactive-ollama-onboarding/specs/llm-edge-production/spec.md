# Delta for LLM Edge Production

## ADDED Requirements

### Requirement: Ollama Unavailability Points To `doctor`

WHEN the suggestion verb's underlying `suggest_relations` call raises
`OllamaUnavailable`, the CLI MUST catch it before the generic `OllamaError`
handler, print to stderr a message that states Ollama is not responding,
tells the user to start it with `ollama serve`, and additionally points to
`openkos doctor` to diagnose the environment, then exit 1 with zero writes
to any bundle file. The `OllamaModelNotFound` and generic `OllamaError`
branches, and their ordering relative to `OllamaUnavailable`, MUST remain
unchanged.

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
