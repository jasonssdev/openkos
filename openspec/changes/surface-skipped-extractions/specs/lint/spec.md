# Delta for lint

## ADDED Requirements

### Requirement: Unextracted-Source Scan

`openkos lint` MUST flag any Source document whose frontmatter
`extraction_status` equals `failed` as an `unextracted` finding
(`LintFinding.kind`, joining `stale`/`orphan`/`dangling`). The other three
`extraction_status` values (`no-extractable-text`, `blocked-by-sensitivity`,
`no-concepts-found`) MUST NEVER produce this finding —
`blocked-by-sensitivity` in particular is a deliberate policy outcome, not
debt, and MUST NOT be reported as something to retry. The finding's detail
MUST name the literal retry command built from that Source's own `resource`
frontmatter value (`openkos ingest <resource>`), falling back to a generic
re-ingest hint only when `resource` is missing or empty. This scan MUST
reuse `LintDoc`'s existing single-pass `collect_docs` walk — no new bundle
walk — and MUST NOT change `lint`'s exit code: `lint`'s Non-Gating Exit
Contract already covers all existing kinds and MUST cover this one too.

#### Scenario: failed Source produces an unextracted finding

- GIVEN a Source document with `extraction_status: failed`
- WHEN `openkos lint` runs
- THEN it reports an `unextracted` finding for that Source

#### Scenario: blocked-by-sensitivity produces no finding

- GIVEN a Source document with `extraction_status: blocked-by-sensitivity`
- WHEN `openkos lint` runs
- THEN no `unextracted` finding is reported for that Source, and it appears
  in no retry prompt

#### Scenario: Detail names the exact retry command

- GIVEN a Source with `extraction_status: failed` and `resource: raw/foo.md`
- WHEN `openkos lint` runs
- THEN the finding's detail text names the command
  `openkos ingest raw/foo.md` verbatim

#### Scenario: lint exits 0 with unextracted findings present

- GIVEN a bundle containing at least one `unextracted` finding
- WHEN `openkos lint` runs
- THEN it reports the finding(s) and still exits 0
