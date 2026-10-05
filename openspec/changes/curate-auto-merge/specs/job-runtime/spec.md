# Delta for Job Runtime

## ADDED Requirements

### Requirement: The Daemon And Maintenance Pass Never Auto-Merge

The daemon, every job it runs, and the maintenance pass MUST NEVER merge a
Concept, whatever flags, configuration or queue contents are present. In
particular, `curate --auto-merge` (`identity-auto-merge`) MUST NOT be reachable
from the daemon or the maintenance pass: the runner MUST NOT pass an
auto-merge input to any core, and an identity proposal MUST remain a pending
work row until a person acts on it.

#### Scenario: A daemon maintenance cycle over an in-class pair merges nothing

- GIVEN a workspace with an in-class base/`-N` pair that the measured model
  would judge `same` at high confidence
- WHEN a daemon maintenance cycle completes
- THEN both files are unchanged, no merge commit exists, and the queue holds
  an identity row for the pair

#### Scenario: The runner passes no auto-merge input

- GIVEN the runner's calls into the application services
- WHEN a maintenance job runs
- THEN no call carries an auto-merge input
