# Delta for curate-decisions

## MODIFIED Requirements

### Requirement: Behavior Of The Command Is Unchanged

Routing the `curate` walks through the surface MUST NOT change any stdout,
stderr, exit code, stream ordering, on-disk format, queue schema or consent
rule of `curate`. Characterization goldens recorded before the surface exists
MUST pass unchanged after each slice that moves a walk onto it. A behavior change
specified for the command itself is not a routing move: the Identity group walk
(`curate-command`: "Identity Walks A Group Through The Group Decision") replaces
the skip lines for groups whose row offers merge-group.
(Previously: no exception was stated, because no walk had gained behavior.)

#### Scenario: Goldens pass unchanged

- GIVEN characterization goldens recorded on the tree before any move
- WHEN each slice that rewires a walk is complete
- THEN stdout, stderr and exit codes match the goldens byte for byte in every
  recorded scenario
