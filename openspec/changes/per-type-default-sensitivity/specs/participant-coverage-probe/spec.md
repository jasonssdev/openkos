# Delta for Participant Coverage Probe

## MODIFIED Requirements

### Requirement: No Per-Type Sensitivity Behavior in Probe Scope

The probe MUST NOT introduce or measure any per-type default-sensitivity
behavior itself: it MUST NOT branch on OKF type when applying or reporting
sensitivity, and MUST NOT consult a per-type sensitivity offset mapping in
its own measurement path. Within the probe's own measurement scope,
sensitivity remains a single, uniformly-applied value, unaffected by object
type. This requirement does NOT claim that sensitivity is a single
workspace-wide value outside the probe: a separately-owned per-type default
mechanism (`type-sensitivity-defaults`) may raise a `Person` or
`Organization` concept's birth sensitivity above the workspace floor
elsewhere in the system; the probe simply does not consult, apply, or
measure that mechanism.
(Previously: this requirement additionally claimed sensitivity remains a
single workspace-level setting unaffected by object type workspace-wide,
which is no longer true once `type-sensitivity-defaults` ships.)

#### Scenario: Probe reports coverage without sensitivity branching

- GIVEN a probe run over sources with the workspace default sensitivity
  applied uniformly within the probe's own extraction/measurement path
- WHEN the probe reports results
- THEN no per-type sensitivity value, override, or branch appears in the
  report or in the extraction path the probe itself measures

#### Scenario: A workspace-wide per-type default does not put the probe out of compliance

- GIVEN a workspace configured with a per-type sensitivity offset mapping
  (e.g. `{"Person": 1}`) that raises `Person` concepts born elsewhere in the
  system above the workspace floor
- WHEN the participant coverage probe runs
- THEN the probe's own measurement path still applies no per-type
  sensitivity branching, and the presence of the per-type default elsewhere
  in the system does not violate this requirement
