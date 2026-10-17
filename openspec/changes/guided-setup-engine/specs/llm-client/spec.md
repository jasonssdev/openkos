# Delta for llm-client

## ADDED Requirements

### Requirement: Ollama Reports Its Version

`OllamaClient.version()` MUST send `GET {host}/api/version` and return the
`version` string of the reply. A transport failure MUST raise
`OllamaUnavailable` naming only the redacted display host; a reply without a
string `version` MUST raise `OllamaError`.

#### Scenario: The version is read

- GIVEN a server answering `{"version": "0.32.9"}`
- WHEN `version()` is called
- THEN it returns `"0.32.9"`

#### Scenario: A refused connection is unavailable

- GIVEN a host that refuses connections
- WHEN `version()` is called
- THEN `OllamaUnavailable` is raised

### Requirement: Ollama Pulls A Model As A Progress Stream

`OllamaClient.pull(model, *, on_event, should_stop)` MUST send
`POST {host}/api/pull` with `{"model": <model>, "stream": true}`, read the reply
one JSON line at a time, and call `on_event` with a `PullEvent` (`status`,
`digest`, `completed`, `total`, each null when absent) for every line. It MUST
call `should_stop` after each line and, when it returns true, close the reply
and return `False`. It MUST return `True` after a line whose status is
`success`. A line carrying `error` MUST raise `OllamaModelNotFound` when the
text says the model does not exist or was not found, and `OllamaError`
otherwise. A stream that ends without `success` MUST raise `OllamaError`; a
transport failure MUST raise `OllamaUnavailable`. A malformed line MUST raise
`OllamaError`, and unknown fields MUST be ignored. The capability is declared by
the `ModelRuntime` Protocol in `llm/base.py`, which extends `BackendDiagnostics`
with `version` and `pull`.

#### Scenario: A stream is read to success

- GIVEN a reply of three progress lines and `{"status": "success"}`
- WHEN `pull` is called
- THEN `on_event` received four events and the result is `True`

#### Scenario: A stop closes the reply

- GIVEN `should_stop` returns true after the first line
- WHEN `pull` is called
- THEN the reply is closed, no further line is read, and the result is `False`

#### Scenario: An unknown model is not found

- GIVEN a reply line `{"error": "pull model manifest: file does not exist"}`
- WHEN `pull` is called
- THEN `OllamaModelNotFound` is raised
