# Delta for Doctor Command

## MODIFIED Requirements

### Requirement: Failed Checks Print Actionable Remediation

Each `[FAIL]` line MUST be immediately followed by an indented
`-> <fix command>` line naming the user's own next command: Ollama
unreachable points to starting the server or installing it, depending on
whether the `ollama` binary is resolvable on the current process's PATH; a
missing model points to pulling that model tag; an uninitialized workspace
points to initializing it. `doctor` MUST NOT run these commands itself.

For the Ollama-unreachable case specifically, the system MUST use
`shutil.which("ollama")` as a non-authoritative signal to select the
remediation wording: WHEN `shutil.which("ollama")` returns `None`, the
remediation MUST state that no `ollama` binary was found on PATH and point
to https://ollama.com for installation, and MUST NOT claim "Ollama is not
installed"; WHEN a binary is found but the endpoint still refuses the
connection, the remediation MUST point to `ollama serve`, unchanged from
prior behavior; WHEN the signal cannot be read confidently, the remediation
MUST cover both remedies rather than asserting either state as certain.
(Previously: any `OllamaUnavailable` failure produced the same generic
`ollama serve` remediation regardless of whether the binary was present on
PATH.)

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
