# Delta for Workspace Init

## MODIFIED Requirements

### Requirement: Bundle Index Shape

`bundle/index.md` MUST carry frontmatter whose parsed form has exactly one
key, `okf_version`, with parsed value equal to the string `0.2`, and an
empty body. The requirement is on the parsed value, not on the byte
sequence — either single- or double-quoted YAML scalars satisfy it.
(Previously: the parsed value was `0.1`; OKF v0.2 adoption bumps the
bundle-root declared version.)

#### Scenario: Exact parsed frontmatter, empty body

- GIVEN a successful init
- WHEN `bundle/index.md` is parsed
- THEN the parsed frontmatter equals exactly `{okf_version: "0.2"}` as data
  (quote style on disk is not asserted) and the body is empty

### Requirement: OKF Conformance

Init's output MUST satisfy OKF §11 conformance for a fresh bundle. Rules 1
(frontmatter present) and 2 (non-empty `type`) MUST pass vacuously, because
a fresh bundle contains zero non-reserved `.md` files for the mechanical
conformance check to inspect. Rule 3 (reserved-file structure) MUST hold by
construction, through the `index.md` and `log.md` shapes required by the
Bundle Index Shape and Bundle Log Shape requirements above. This slice MUST
NOT claim a mechanical check of rule 3 — that check is deferred to `lint`.
When the mechanical conformance check encounters a file it cannot read or
decode (for example a permission error or invalid encoding), it MUST
report that failure distinctly as an I/O/read error and MUST NOT report it
as a conformance violation.
(Previously: cited OKF §9 conformance; OKF v0.2 renumbers the conformance
section to §11 (v0.2 §13.1); the underlying rules and their pass/fail
behavior are unchanged.)

#### Scenario: Mechanical check reports no violations on a fresh bundle

- GIVEN a successful init
- WHEN the OKF conformance check (rules 1 and 2) runs against `bundle/`
- THEN it reports no violations, because `bundle/` contains only the two
  reserved files and no non-reserved `.md` file exists to check

#### Scenario: Rule 3 holds by construction, not by mechanical check

- GIVEN a successful init
- WHEN `bundle/index.md` and `bundle/log.md` are inspected against the
  shapes required by Bundle Index Shape and Bundle Log Shape
- THEN both satisfy OKF §11 rule 3 by construction
- AND no mechanical rule-3 check is performed by this slice; that check is
  deferred to `lint`

#### Scenario: Unreadable file is reported as an I/O error, not a conformance violation

- GIVEN a non-reserved `.md` file under `bundle/` that exists but cannot
  be read as text — for example permission denied, or content that cannot
  be decoded with the expected encoding
- WHEN the OKF conformance check runs against `bundle/`
- THEN the failure is reported as an I/O/read error distinct from a
  conformance violation, and is not phrased as "no parseable frontmatter"
  or any other conformance-violation wording
