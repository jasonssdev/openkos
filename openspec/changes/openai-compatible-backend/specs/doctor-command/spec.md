# Delta for Doctor Command

## ADDED Requirements

### Requirement: Doctor Shows The Effective Endpoint And Its Resolution Source

`doctor` MUST print, alongside its backend-reachable check, the effective
endpoint it is checking against and which resolution source produced it
(explicit constructor argument, `OLLAMA_HOST`, the configured `base_url`, or
the packaged default), per the `backend-selection` endpoint-resolution
precedence. This line is informational and MUST NOT affect the exit code.
WHEN the resolution source is the packaged default, `doctor` MUST NOT print
this endpoint-and-source line at all: the reachable check's detail MUST
remain byte-identical to its pre-existing default-path wording (e.g.
`12 models`), so a user who never sets `base_url` or `OLLAMA_HOST` sees no
change in `doctor`'s output from this change.

#### Scenario: The effective endpoint and its source are shown

- GIVEN a workspace with `base_url: http://127.0.0.1:8000` configured and no
  `OLLAMA_HOST` set
- WHEN `openkos doctor` runs
- THEN it prints the effective endpoint `http://127.0.0.1:8000` and states
  that it came from the configured `base_url`

#### Scenario: OLLAMA_HOST is named as the source when it wins precedence

- GIVEN `OLLAMA_HOST` is set in the environment alongside a configured
  `base_url`
- WHEN `openkos doctor` runs
- THEN it prints the endpoint resolved from `OLLAMA_HOST` and names
  `OLLAMA_HOST`, not `base_url`, as its source

#### Scenario: The packaged default prints no endpoint-and-source line

- GIVEN a workspace with no `base_url` configured, no `OLLAMA_HOST` set, and
  `backend: ollama` resolving to the packaged default endpoint
- WHEN `openkos doctor` runs
- THEN the reachable check's detail is byte-identical to its pre-existing
  default-path wording, and no endpoint-and-source line is printed

### Requirement: The API Key Is Never Printed By Doctor

WHEN `OPENKOS_OPENAI_API_KEY` is set, `doctor` MUST NOT print its value on
any output stream, under any check outcome, including a failing
backend-reachable check whose remediation names the endpoint. `doctor` MUST
NOT gate any check's pass/fail outcome on the key's presence or absence.

#### Scenario: A configured key never appears in doctor's output

- GIVEN `OPENKOS_OPENAI_API_KEY` is set to a value
- WHEN `openkos doctor` runs, including when the backend-reachable check
  fails
- THEN that value appears nowhere in `doctor`'s stdout or stderr

#### Scenario: An absent key does not affect any check's outcome

- GIVEN `OPENKOS_OPENAI_API_KEY` is unset and the configured
  `openai-compatible` server requires no key
- WHEN `openkos doctor` runs
- THEN no check fails, is skipped, or is reported not-run because of the
  key's absence

## MODIFIED Requirements

### Requirement: Failed Checks Print Actionable Remediation

Each `[FAIL]` line MUST be immediately followed by an indented
`-> <fix command>` line naming the user's own next command: for the
`ollama` backend, an unreachable server points to starting it or installing
it, depending on whether the `ollama` binary is resolvable on the current
process's PATH, and a missing model points to pulling that model tag; for
the `openai-compatible` backend, an unreachable server points to verifying
that the configured server is running at its configured endpoint (no
`ollama serve`/`shutil.which("ollama")` signal applies, since there is no
single universal binary or start command across llama.cpp, LM Studio, vLLM,
and LocalAI), and a missing model lists the model ids the configured server's own
`/v1/models` response reports and advises setting `model:` to one of them
(no `ollama pull` reference, since it does not apply), with a note that
llama.cpp's `llama-server` reports its loaded GGUF file's path (or the value
passed to `--alias`, when set) rather than an arbitrary model name in
`/v1/models` — so on that server a "model not found" critical MAY be a false
alarm caused by naming convention rather than a genuinely missing model, and
the remediation MUST say so rather than asserting the model is absent. An
uninitialized workspace points to initializing it, for either backend.
`doctor` MUST NOT run these commands itself.

For the `ollama` backend's unreachable case specifically, the system MUST
use `shutil.which("ollama")` as a non-authoritative signal to select the
remediation wording: WHEN `shutil.which("ollama")` returns `None`, the
remediation MUST state that no `ollama` binary was found on PATH and point
to https://ollama.com for installation, and MUST NOT claim "Ollama is not
installed"; WHEN a binary is found but the endpoint still refuses the
connection, the remediation MUST point to `ollama serve`, unchanged from
prior behavior; WHEN the signal cannot be read confidently, the remediation
MUST cover both remedies rather than asserting either state as certain. For
the `openai-compatible` backend, `doctor` MUST NOT probe for or reference
`ollama`, `shutil.which("ollama")`, `ollama serve`, or `ollama pull` in any
remediation line: none of that wording applies to a non-Ollama server.
(Previously: any `OllamaUnavailable` failure produced the same generic
`ollama serve` remediation regardless of whether the binary was present on
PATH, and no backend other than `ollama` existed, so no branch existed for
`openai-compatible`.)

#### Scenario: Binary found, endpoint refuses — start-server remediation

- GIVEN `shutil.which("ollama")` resolves to a path, but the endpoint
  refuses the connection
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check prints `[FAIL]` followed by a
  `-> ollama serve` remediation line

#### Scenario: No binary on PATH — install remediation, no over-claim

- GIVEN `shutil.which("ollama")` returns `None`
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check prints `[FAIL]` followed by a remediation
  line stating no `ollama` binary was found on PATH and pointing to
  https://ollama.com
- AND the remediation text never states "Ollama is not installed"

#### Scenario: Uncertain signal covers both remedies

- GIVEN the `shutil.which("ollama")` signal cannot be read confidently
- WHEN `openkos doctor` runs
- THEN the Ollama-reachable check's remediation covers both installing and
  starting Ollama, rather than asserting either state as certain

#### Scenario: Missing model shows a pull remediation naming the tag

- GIVEN Ollama is reachable but the configured model tag is not installed
- WHEN `openkos doctor` runs
- THEN the model-installed check prints `[FAIL]` followed by an indented
  fix line naming a pull command for that exact configured tag

#### Scenario: Outside a workspace shows an init remediation

- GIVEN the current directory is not an initialized workspace
- WHEN `openkos doctor` runs
- THEN the workspace-initialized check prints `[FAIL]` followed by an
  indented fix line naming the init command

#### Scenario: openai-compatible server unreachable — endpoint-check remediation, no Ollama wording

- GIVEN `cfg.backend == "openai-compatible"` and the configured endpoint
  refuses the connection
- WHEN `openkos doctor` runs
- THEN the backend-reachable check prints `[FAIL]` followed by a remediation
  line naming the configured endpoint and advising the user to verify that
  server is running
- AND the remediation contains no reference to `ollama`, `ollama serve`, or
  `shutil.which("ollama")`

#### Scenario: openai-compatible model missing — no ollama pull reference

- GIVEN `cfg.backend == "openai-compatible"`, the configured server is
  reachable, but the configured model is not available on it
- WHEN `openkos doctor` runs
- THEN the model-installed check prints `[FAIL]` followed by a remediation
  line naming the configured model and advising the user to make it
  available on that server
- AND the remediation contains no `ollama pull` reference

#### Scenario: openai-compatible model missing — lists the server's reported ids

- GIVEN `cfg.backend == "openai-compatible"`, the configured server is
  reachable, and its `/v1/models` response lists ids `a`, `b`, `c`, none of
  which match the configured model
- WHEN `openkos doctor` runs
- THEN the model-installed check's remediation lists `a`, `b`, `c` and
  advises setting `model:` to one of them

#### Scenario: llama.cpp's GGUF-path listing is called out as a possible false alarm

- GIVEN `cfg.backend == "openai-compatible"` and the configured server is a
  llama.cpp `llama-server` instance whose `/v1/models` response lists a GGUF
  file path (or the value passed to `--alias`) instead of the configured
  model name
- WHEN `openkos doctor` runs and the model-installed check fails
- THEN the remediation mentions that `llama-server` reports the GGUF path
  (or `--alias`) rather than an arbitrary model name, so the mismatch MAY
  not mean the model is genuinely missing
