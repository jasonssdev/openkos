# Delta for Doctor Command

## ADDED Requirements

### Requirement: Workspace Vector Index Presence Check

`doctor` MUST report whether the WORKSPACE `.openkos/vectors.db`
(`layout.vectors_db_path`) exists on disk, as a check distinct from the
existing Vector-Extension-Loadable Check (which probes a throwaway
`:memory:` connection and says nothing about the workspace's own index
file). This check MUST be informational (its failure alone MUST NOT affect
the exit code), MUST run only when a workspace is initialized (skipped
outside a workspace, mirroring the config-valid/bundle-readable checks),
and a `[FAIL]` line MUST be followed by an indented fix line naming
`openkos reindex`.

#### Scenario: Present workspace vectors.db passes

- GIVEN an initialized workspace whose `.openkos/vectors.db` file exists
- WHEN `openkos doctor` runs
- THEN the workspace-vectors check prints `[PASS]`

#### Scenario: Absent workspace vectors.db fails with a reindex remediation

- GIVEN an initialized workspace whose `.openkos/vectors.db` file is
  absent (e.g. after `openkos purge`)
- WHEN `openkos doctor` runs
- THEN the workspace-vectors check prints `[FAIL]` followed by an indented
  fix line naming `openkos reindex`, and the process still exits 0 if
  every critical check otherwise passes

#### Scenario: Check is skipped outside a workspace

- GIVEN no initialized workspace
- WHEN `openkos doctor` runs
- THEN the workspace-vectors check prints `[SKIP]` (not applicable), and
  does not affect the exit code
