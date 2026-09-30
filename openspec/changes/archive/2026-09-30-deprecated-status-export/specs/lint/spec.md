# Delta for Lint

## ADDED Requirements

### Requirement: Deprecated-Status Export Drift Scan

`openkos lint` MUST evaluate the deprecated-status export projection
(`deprecated-status-export`) for every concept over the bundle's superseded
set, and MUST report: a `status-export-drift` finding for every concept
whose outcome is EXPORT (a superseded concept whose frontmatter does not
say so), WITHDRAW (an export no edge justifies any more), or DROP-MARKER
(an invalid marker); and a `status-export-blocked` finding for every
concept whose outcome is BLOCKED, naming its own `status` value. A
`status-export-drift` finding's detail MUST name `openkos repair` as the
command that fixes it; a `status-export-blocked` finding's detail MUST say
that the concept is hidden from retrieval regardless and that only a person
editing its `status` changes what OKF consumers see. The scan MUST stay
read-only and its findings non-gating (Non-Gating Exit Contract).

WHEN the edge walk is incomplete (a concept document fails to read or
parse, or carries malformed `relations:`), the scan MUST still report
EXPORT, BLOCKED, and DROP-MARKER findings, and MUST report the WITHDRAW
half as `not-run` with the reason naming an unreadable document, rather
than asserting that an export is stale.

#### Scenario: A superseded concept without an export is reported

- GIVEN `a` supersedes `b`, and `b` carries `status: stable`
- WHEN `openkos lint` runs
- THEN it reports one `status-export-drift` finding for `b` naming
  `openkos repair`, and exits `0`

#### Scenario: A stale export is reported

- GIVEN `b` carries `status: deprecated` and `status_derived_from:
  supersedes`, and no `supersedes` edge targets `b`
- WHEN `openkos lint` runs
- THEN it reports one `status-export-drift` finding for `b`

#### Scenario: A blocked draft is reported, not called drift

- GIVEN `a` supersedes `b`, and `b` carries `status: draft`
- WHEN `openkos lint` runs
- THEN it reports one `status-export-blocked` finding for `b` naming
  `draft`, and no `status-export-drift` finding for `b`

#### Scenario: A consistent bundle reports nothing

- GIVEN every superseded concept carries a valid export and no concept
  carries an export without a superseding edge
- WHEN `openkos lint` runs
- THEN it reports no `status-export-drift` and no `status-export-blocked`
  finding

#### Scenario: An incomplete walk degrades the stale half to not-run

- GIVEN one concept document fails to parse, and `b` carries a valid
  export with no readable edge targeting it
- WHEN `openkos lint` runs
- THEN no `status-export-drift` finding is reported for `b`, and the
  stale-export half of the scan reports `not-run` naming the unreadable
  document
