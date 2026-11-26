# Backend Selection Specification (delta)

## ADDED Requirements

### Requirement: Reasoning Effort Is An Opt-In Setting For The openai-compatible Backend

`openkos.yaml` MAY set `reasoning_effort` to a non-empty string of lowercase
ASCII letters. Absent or null it MUST default to unset. Any other value (a
non-string, an empty string, capitals, a space or a digit) MUST be refused when
the configuration is read, with a message naming `reasoning_effort`. OpenKOS
MUST NOT validate the word against a fixed set. WHEN `backend` is `ollama`
(explicitly or by default), a set `reasoning_effort` MUST be refused when the
configuration is read, with a message saying the key requires
`backend: openai-compatible`. `openkos doctor` MUST show the configured value
for an `openai-compatible` workspace and MUST show nothing for the key when it
is unset.

#### Scenario: Unset changes nothing

- GIVEN an `openkos.yaml` without `reasoning_effort`, under either backend
- WHEN the configuration is read
- THEN `Config.reasoning_effort` is unset and no behaviour differs

#### Scenario: A lowercase word is accepted

- GIVEN `backend: openai-compatible` and `reasoning_effort: low`
- WHEN the configuration is read
- THEN `Config.reasoning_effort` equals `"low"`

#### Scenario: An invalid value is refused

- GIVEN `reasoning_effort: Low`, `reasoning_effort: 1` or `reasoning_effort: ""`
- WHEN the configuration is read
- THEN reading fails naming `reasoning_effort`

#### Scenario: The ollama backend refuses it

- GIVEN `reasoning_effort: low` and no `backend`, or `backend: ollama`
- WHEN the configuration is read
- THEN reading fails saying the key requires `backend: openai-compatible`

#### Scenario: Doctor shows the value

- GIVEN `backend: openai-compatible` and `reasoning_effort: low`
- WHEN `openkos doctor` runs
- THEN the backend reachability line includes `reasoning effort: low`
