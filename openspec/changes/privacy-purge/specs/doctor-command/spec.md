# Delta for Doctor Command

## ADDED Requirements

### Requirement: git-filter-repo Availability Check

`doctor` MUST report whether `git` is resolvable on PATH and whether
`git-filter-repo` is installed and invocable, reusing the same
accumulate-never-raise `CheckResult` pattern as the other checks and printing
exactly one `[PASS]`/`[FAIL]` line for this check. This check MUST be
informational (its failure alone MUST NOT affect the exit code) and MUST run
independently of workspace state and Ollama reachability, since `purge` needs
this signal even outside an initialized workspace.

#### Scenario: Both available passes
- GIVEN `git` is on PATH and `git-filter-repo` is installed
- WHEN `openkos doctor` runs
- THEN the git-filter-repo check prints `[PASS]`

#### Scenario: git-filter-repo missing shows an install remediation
- GIVEN `git` is on PATH but `git-filter-repo` is not installed
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by an indented fix line naming
  how to install `git-filter-repo`, and the process still exits 0 if every
  critical check otherwise passes

#### Scenario: git itself missing shows an install remediation
- GIVEN no `git` binary is resolvable on PATH
- WHEN `openkos doctor` runs
- THEN the check prints `[FAIL]` followed by an indented fix line naming
  how to install `git`

#### Scenario: Check runs pre-init and independently of Ollama
- GIVEN no initialized workspace and Ollama unreachable
- WHEN `openkos doctor` runs
- THEN the git-filter-repo check still runs and reports its own
  `[PASS]`/`[FAIL]` result, unaffected by workspace or Ollama state
