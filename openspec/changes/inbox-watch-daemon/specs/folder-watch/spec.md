# Folder Watch Specification (delta)

## ADDED Requirements

### Requirement: The Daemon Stops When Its Supervisor's Pipe Closes

`openkos daemon` MUST accept a hidden `--stop-on-stdin-eof` option, not shown in
`--help`, that reads its standard input on a background thread, discards it, and
treats end of input as a stop request: the daemon finishes the work in progress
and exits 0, as on SIGTERM. Without the option standard input MUST NOT be read.

#### Scenario: End of input stops the daemon

- GIVEN `openkos daemon --stop-on-stdin-eof` idling between passes
- WHEN its standard input reaches end of file
- THEN it finishes and exits 0

#### Scenario: An open pipe keeps it running

- GIVEN the same daemon with its standard input open and silent
- WHEN time passes
- THEN it keeps running
