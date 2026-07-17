# Delta for Workspace Init

## MODIFIED Requirements

### Requirement: Refusal Idempotency

The system MUST evaluate all refusal conditions in a pre-flight check
before writing any file. It MUST exit 1 and write nothing if any of the
following applies: `openkos.yaml` already exists; `AGENTS.md` already
exists; `raw/` exists and is non-empty; `bundle/` exists and is non-empty;
`raw` or `bundle` exists and is not a directory; or `raw` or `bundle`
exists as a pre-existing symlink (to a directory, to a file, or to a
nonexistent target). It MUST NEVER overwrite an existing file, follow a
symlinked workspace path when creating or writing, or write outside the
workspace root. It MUST leave no partial artifacts behind on refusal —
writing MUST NOT begin until every condition has been checked and none
apply. On refusal, it MUST write to stderr a message identifying which
condition triggered the refusal; when the trigger is a non-empty
`bundle/`, that message MUST additionally identify the leftover as a
likely remnant of an interrupted (crashed or killed) prior `init` and
point to manual remediation, instead of the bare "already exists and is
not empty".
(Previously: refusal conditions did not include a symlinked `raw`/`bundle`
target, and the non-empty-`bundle/` message carried no cause or
remediation hint.)

#### Scenario: Existing openkos.yaml

- GIVEN a directory containing `openkos.yaml`
- WHEN init runs
- THEN it exits 1, no file is created or modified, and stderr identifies
  the existing `openkos.yaml` as the cause

#### Scenario: Existing AGENTS.md

- GIVEN a directory containing `AGENTS.md` and no `openkos.yaml`
- WHEN init runs
- THEN it exits 1, `AGENTS.md` is unchanged, no other file is created, and
  stderr identifies the existing `AGENTS.md` as the cause

#### Scenario: Non-empty raw/ or bundle/

- GIVEN no `openkos.yaml` but a non-empty `raw/` or `bundle/`
- WHEN init runs
- THEN it exits 1, writes nothing, and stderr identifies the non-empty
  directory as the cause

#### Scenario: raw or bundle exists as a non-directory

- GIVEN no `openkos.yaml`, and either `raw` or `bundle` exists in the
  current directory as a regular file, not a directory
- WHEN init runs
- THEN it exits 1 in pre-flight, writes nothing, raises no uncaught
  exception, and stderr identifies that path as not a directory

#### Scenario: Second run on an initialized workspace

- GIVEN a directory already initialized by init
- WHEN init runs again
- THEN it exits 1 and none of the five artifacts are overwritten or
  truncated

#### Scenario: No partial output kept on refusal

- GIVEN any refusal condition, detected in pre-flight before any write
- WHEN init exits 1
- THEN none of the five artifacts exist unless they pre-existed, and any
  pre-existing one is unchanged

#### Scenario: Symlinked raw or bundle target is refused

- GIVEN no `openkos.yaml`, and `raw` or `bundle` in the current directory
  is a pre-existing symlink, whether it targets a directory, a file, or a
  nonexistent path
- WHEN init runs
- THEN it exits 1 in pre-flight, writes nothing anywhere (including
  through the symlink or at its target), never follows the symlink, and
  stderr identifies that path as a symlink

#### Scenario: Stray bundle/ retry names the likely crashed-init cause

- GIVEN a prior `init` run left a non-empty `bundle/` behind (for example
  after a mid-write crash) and no `openkos.yaml` exists
- WHEN init runs again
- THEN it exits 1, writes nothing, and stderr's message identifies the
  leftover `bundle/` as a likely remnant of an interrupted init and points
  to remediation — not the bare "already exists and is not empty"

### Requirement: OKF Conformance

Init's output MUST satisfy OKF §9 conformance for a fresh bundle. Rules 1
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
(Previously: any read or parse failure, including I/O failures such as a
permission error or invalid encoding, was reported indistinguishably as a
conformance violation, "no parseable frontmatter".)

#### Scenario: Mechanical check reports no violations on a fresh bundle

- GIVEN a successful init
- WHEN the OKF conformance check (rules 1 and 2) runs against `bundle/`
- THEN it reports no violations, because `bundle/` contains only the two
  reserved files and no non-reserved `.md` file exists to check

#### Scenario: Rule 3 holds by construction, not by mechanical check

- GIVEN a successful init
- WHEN `bundle/index.md` and `bundle/log.md` are inspected against the
  shapes required by Bundle Index Shape and Bundle Log Shape
- THEN both satisfy OKF §9 rule 3 by construction
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

## Notes

Findings #2 (snapshot helper hardening), #5 (shared exclusive-create
helper), #6 (named refusal-condition type), and #7 (docstring correction)
are covered by this change but introduce no new or changed observable
behavior; they carry no requirement or scenario here.
