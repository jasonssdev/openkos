# Delta for Query Command

## MODIFIED Requirements

### Requirement: LLM And Index Errors Map To Exit 1

WHEN `answer()` raises an `OllamaError`-family exception or `FtsUnavailable`,
`query` MUST catch it, print a message to stderr, and exit 1 with no raw
traceback reaching the user. The stderr message MUST be actionable for the
two most common first-run causes and MUST remain generic for all other
cases:

- WHEN the raised exception is `OllamaUnavailable`, the stderr message MUST
  state that Ollama is not responding, MUST include the Ollama host it tried
  to reach, MUST tell the user to start Ollama, referencing the
  `ollama serve` command, and MUST additionally point to `openkos doctor`
  to diagnose the environment.
- WHEN the raised exception is `OllamaModelNotFound`, the stderr message MUST
  name the configured model that could not be found, and MUST tell the user
  how to install it, referencing the `ollama pull <model>` command with the
  configured model name.
- WHEN the raised exception is any other `OllamaError` or `FtsUnavailable`,
  `query` MUST print a friendly (non-actionable-specific) failure message to
  stderr — unchanged from prior behavior.

(Previously: the `OllamaUnavailable` message told the user to run
`ollama serve` with no additional pointer to `openkos doctor`.)

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
