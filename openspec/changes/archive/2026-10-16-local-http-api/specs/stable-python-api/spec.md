# Delta for Stable Python API

## MODIFIED Requirements

### Requirement: An Import Guard Bars Interactive And Disclosing Layers From The API

A test MUST inspect the imports of every module under `openkos.api` against an
allowlist and MUST fail, naming the module and the import, on any import
outside it. Every module under `openkos.api` MAY import only the standard
library, `openkos.api.*`, `openkos.errors`, `openkos.config`, `openkos.lock`,
`openkos.application.*` and `openkos.llm.base`; the API's backend and commit
wiring module additionally MAY import the concrete backend client modules and
the print-free git commit core. In particular the test MUST fail, by name, if
any module imports `openkos.cli`, `openkos.mcp` (including `openkos.mcp.gate`),
`typer`, `rich`, `asyncio`, `openkos.state` or `sqlite3`. A second check MUST
fail if any public signature or public dataclass field annotation mentions
`sqlite3`, a type defined under `openkos.state`, or any `openkos` type that
`openkos.api` does not export. A third check MUST fail if any module under
`src/openkos/` outside `openkos.api` and outside `openkos.localapi` imports
`openkos.api`; `openkos.localapi`, the local HTTP adapter, is the one package
permitted to import it, and it MUST import no other `openkos` module (see the
`local-http-api` capability). The existing layering tests MUST continue to bar
`openkos.mcp`, and every top-level package other than `application`, `cli` and
`api` (so including `openkos.localapi`), from importing the owner-local
`application/` services, and MUST NOT be weakened for any importer.
(Previously: the third check admitted no importer of `openkos.api` outside the
package; it now admits `openkos.localapi` as the sole importer.)

#### Scenario: An API module importing the CLI is rejected

- GIVEN a module under `openkos.api` imports `openkos.cli`
- WHEN the import guard runs
- THEN it fails and names the module and the import

#### Scenario: An API module importing the disclosure gate is rejected

- GIVEN a module under `openkos.api` imports `openkos.mcp.gate`
- WHEN the import guard runs
- THEN it fails

#### Scenario: A leaked internal type is rejected

- GIVEN a public function whose return annotation is a `state.*` type
- WHEN the signature-type check runs
- THEN it fails and names the function

#### Scenario: An import outside the allowlist is rejected

- GIVEN a module under `openkos.api` imports a module not on the allowlist
- WHEN the import guard runs
- THEN it fails and names the module and the import

#### Scenario: Nothing outside the API and its local adapter imports it

- GIVEN a module outside `openkos.api` and outside `openkos.localapi` imports
  `openkos.api`
- WHEN the guard runs
- THEN it fails and names the module

#### Scenario: The local HTTP adapter may import the API

- GIVEN a module under `openkos.localapi` imports `openkos.api`
- WHEN the guard runs
- THEN it passes

#### Scenario: The local HTTP adapter cannot reach owner-local services

- GIVEN a module under `openkos.localapi` imports an owner-local
  `openkos.application` service
- WHEN the existing layering test runs
- THEN it fails

#### Scenario: The MCP adapter still cannot reach owner-local services

- GIVEN a module under `openkos.mcp` imports an owner-local `application/`
  service
- WHEN the existing layering test runs
- THEN it fails
