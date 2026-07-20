# Delta for Entity-Resolution Adjudication

## MODIFIED Requirements

### Requirement: Degrade-On-No-Model Mirrors `query`'s 3-Tier Catch

The `adjudicate` verb MUST catch `OllamaUnavailable`, then
`OllamaModelNotFound`, then generic `OllamaError` (in that subclass order),
report a clear actionable message, and write nothing, mirroring `query`'s
degrade contract. WHEN the caught exception is `OllamaUnavailable`, the
message MUST additionally point to `openkos doctor` to diagnose the
environment, mirroring `query`'s `OllamaUnavailable` wording; the
`OllamaModelNotFound` and generic `OllamaError` messages are unchanged.
(Previously: the `OllamaUnavailable` message told the user to run
`ollama serve` with no additional pointer to `openkos doctor`.)

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
