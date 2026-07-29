# Delta for ingestion

## ADDED Requirements

### Requirement: Extraction Status Frontmatter Key on Zero-Derived-Object Degrade

WHEN a single `ingest` run writes zero derived objects, the system MUST write
an `extraction_status` frontmatter key on the Source concept, chosen from the
closed vocabulary below, keyed on WHY extraction produced nothing, never on
which specific gate condition fired today. WHEN at least one derived object
is written, `extraction_status` MUST be ABSENT — no `ok`/`none` sentinel.
Readers MUST ignore any value outside this vocabulary without raising.

| Value | Path | Debt |
|---|---|---|
| `no-extractable-text` | empty/undecodable raw content | No |
| `blocked-by-sensitivity` | confidential floor blocks the LLM send | No — deliberate policy, MUST NEVER be reported as retryable |
| `failed` | LLM backend raised an error | Yes — the only retryable value |
| `no-concepts-found` | successful call returned zero candidates | No |

The value MUST be stamped onto the freshly built Source content produced by
`okf.build_source_concept` each run, never merged onto on-disk frontmatter —
a merge would make a stale marker sticky. The system MUST NOT write the raw
exception text (or any other free-text detail) into this or any other
frontmatter field; the full message remains stderr-only, transient, and
local.

This key is independent of, and MUST NOT interact with, the `sensitivity`
resolution shipped for re-ingest (`okf.combine_sensitivity`): those rules
read and combine an on-disk value, while `extraction_status` is never read
from disk — it is recomputed from scratch every run.

#### Scenario: no-extractable-text is written

- GIVEN a source whose content is empty or fails to decode
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_status` is `no-extractable-text`

#### Scenario: blocked-by-sensitivity is written

- GIVEN a workspace `default_sensitivity` floor that blocks the LLM send and
  no `--include-confidential`
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_status` is `blocked-by-sensitivity`

#### Scenario: failed is written

- GIVEN a fake LLM backend whose `chat` call raises `OllamaError`
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_status` is `failed`

#### Scenario: no-concepts-found is written

- GIVEN a fake LLM backend that returns successfully with zero valid
  candidates
- WHEN `openkos ingest <path>` completes
- THEN the Source's `extraction_status` is `no-concepts-found`

#### Scenario: Successful extraction writes no key at all

- GIVEN extraction yields at least one derived object
- WHEN the Source concept's frontmatter is inspected
- THEN it contains no `extraction_status` key

#### Scenario: A previously failed Source self-clears on later success

- GIVEN a Source whose frontmatter currently has `extraction_status: failed`
- WHEN `openkos ingest raw/<name>` is re-run against the same Source and
  extraction now succeeds with at least one derived object
- THEN the rewritten Source's frontmatter has NO `extraction_status` key

#### Scenario: Unrecognized value is ignored without raising

- GIVEN a Source's on-disk `extraction_status` value is outside the closed
  vocabulary (e.g. a value from a future or reverted version)
- WHEN any reader of this field runs
- THEN it ignores the value and does not raise

#### Scenario: Sensitivity resolution is unaffected

- GIVEN a re-ingest that resolves `sensitivity` per `okf.combine_sensitivity`
  (on-disk value combined with `cfg.default_sensitivity`)
- WHEN the same run also stamps `extraction_status`
- THEN `extraction_status` is computed fresh from this run's outcome only,
  never read from or merged with the on-disk frontmatter, unlike
  `sensitivity`
