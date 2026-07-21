# Delta for Workspace Init

## ADDED Requirements

### Requirement: Non-Fatal Post-Success Ollama Preflight

After a successful `init` (Phase B writes complete, before the process
returns), the system MUST run exactly one non-fatal, bounded-timeout Ollama
preflight probe using the resolved model and the same short timeout `doctor`
uses for its Ollama-reachable check. The probe MUST reuse the existing
config-free `list_models()`/`model_tag_matches()` primitives and MUST NOT
pull a model or start the Ollama server. WHEN the probe finds Ollama
unreachable, OR reachable but the resolved model not installed, OR the probe
itself raises any exception, `init` MUST print exactly ONE warning naming
`openkos doctor` as the next diagnostic step, and MUST still exit 0. WHEN
Ollama is reachable AND the resolved model is installed, no warning MUST be
printed. This requirement MUST NOT alter `init`'s exit code, refusal
behavior, or file-writer guarantee under any preflight outcome.

#### Scenario: Ollama unreachable prints a warning, exit still 0

- GIVEN a successful init and Ollama is not reachable at probe time
- WHEN the post-success preflight runs
- THEN a warning is printed naming `openkos doctor`
- AND `init` exits 0

#### Scenario: Model missing prints a warning, exit still 0

- GIVEN a successful init, Ollama is reachable, but the resolved model is
  not installed
- WHEN the post-success preflight runs
- THEN a warning is printed naming `openkos doctor`
- AND `init` exits 0

#### Scenario: Ollama and model both available — no warning

- GIVEN a successful init, Ollama is reachable, and the resolved model is
  installed
- WHEN the post-success preflight runs
- THEN no warning is printed
- AND `init` exits 0

#### Scenario: Preflight itself errors — still non-fatal

- GIVEN a successful init and the preflight probe raises any exception
  (e.g. an unexpected transport error) rather than a clean unreachable
  result
- WHEN the post-success preflight runs
- THEN the exception is caught broadly, a warning naming `openkos doctor`
  is printed, `init` exits 0, and no traceback reaches the user

#### Scenario: Preflight never pulls a model or starts the server

- GIVEN a successful init with any preflight outcome
- WHEN the post-success preflight runs
- THEN no `ollama pull` or `ollama serve` action is invoked by `openkos`
