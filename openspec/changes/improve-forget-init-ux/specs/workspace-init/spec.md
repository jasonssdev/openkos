# Delta for Workspace Init

## MODIFIED Requirements

### Requirement: Workspace Creation

The system MUST create `raw/`, `bundle/index.md`, `bundle/log.md`,
`openkos.yaml`, and `AGENTS.md` in the current directory and exit 0, when no
refusal condition (see Refusal Idempotency) applies. It MUST accept no
positional argument and no flags, operating on the current directory only.
On success, it MUST write a confirmation message to stdout that names what
was created, and MUST additionally write a next-step hint to stdout naming
`openkos ingest <path>` as the next command to run. This hint MUST be
printed unconditionally on every successful run — `init` has no TTY/quiet
gating.
(Previously: on success, `init` wrote only the confirmation message naming
what was created, with no hint about the next command.)

#### Scenario: Fresh empty directory

- GIVEN an empty current directory
- WHEN `openkos init` runs
- THEN all five artifacts exist and the process exits 0

#### Scenario: Success message names what was created

- GIVEN an empty current directory
- WHEN `openkos init` runs successfully
- THEN stdout contains a message naming the created workspace/bundle
  artifacts
- AND the process exits 0

#### Scenario: Success output includes the next-step hint

- GIVEN an empty current directory
- WHEN `openkos init` runs successfully
- THEN stdout also contains a next-step hint that names `openkos ingest
  <path>` as the next command to run
- AND this hint is printed regardless of whether stdin is a TTY
</content>
