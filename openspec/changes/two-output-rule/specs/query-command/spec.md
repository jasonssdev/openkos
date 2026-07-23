# Delta for Query Command

## Non-Goals Update

The current spec's Non-Goals line excludes "automated re-filing of an answer
back as a concept." This change REVERSES that exclusion, gated behind the new
opt-in `--save` flag: re-filing is now in scope when `--save` is passed, and
still explicitly out of scope when it is not. At archive time, replace
"automated re-filing of an answer back as a concept" in the main spec's
Non-Goals line with: "automated re-filing without `--save`; LLM-generated
titles; mandatory `--title`."

## ADDED Requirements

### Requirement: Read-Only Purity Without `--save`

`query` MUST default `--save` to OFF. WHEN `--save` is not passed, `query`
MUST behave byte-identically to its pre-existing read-only path: no bundle
file is created, no index or log entry is written, and no confirmation
prompt is shown.

#### Scenario: Query without `--save` is unchanged

- GIVEN a workspace and a question with a matching answer
- WHEN `openkos query "<question>"` is run without `--save`
- THEN stdout/stderr output is identical to the pre-`--save` behavior
- AND no new file, index entry, or log entry is created

### Requirement: `--save` Files The Cited Answer As A New Concept

WHEN `--save` is passed and `answer()` returns a matched result, `query`
MUST, after rendering the answer, build a new concept via the ingest
builder with: body = the rendered answer text; title = the question, or
`--title` when given; description = the question, or `--description` when
given; type = `"Concept"`, or `--type` when given; provenance = the cited
concepts' ids (`result.citations`).

#### Scenario: Default filing uses the question as title/description

- GIVEN `openkos query "<question>" --save` is run and the answer matches
- WHEN the concept is built
- THEN body is the rendered answer, title and description are the
  question, type is `"Concept"`, and provenance lists the cited concept ids

#### Scenario: `--title`, `--description`, `--type` override defaults

- GIVEN `openkos query "<question>" --save --title "T" --type "Procedure"`
- WHEN the concept is built
- THEN title is `"T"` and type is `"Procedure"`, overriding the question
  and `"Concept"` defaults

### Requirement: Sensitivity Is The High-Water-Mark Of Cited Concepts

WHEN filing via `--save`, `query` MUST re-read each cited concept's
frontmatter and set the filed concept's sensitivity to the high-water-mark
(`okf.combine_sensitivity`) across them, seeded at `cfg.default_sensitivity`.
An unreadable OR unparseable cited concept MUST fold the running floor to
`confidential` -- the most-restrictive level, NOT be skipped -- fail-closed,
consistent with the project's pervasive "cannot verify sensitivity ->
confidential" stance (`okf._rank`, `sensitivity.blocks_llm_send`). WHEN
`--include-confidential` caused a confidential cited concept to be used, the
filed concept's sensitivity MUST be confidential. WHEN there are zero
citations, `query` MUST REFUSE to file, exit non-zero, and leave the bundle
unchanged -- `build_concept` requires non-empty provenance, and a sourceless
"derived" concept is not a real derived node.

#### Scenario: Confidential citation propagates confidentiality

- GIVEN `--include-confidential` is set and one cited concept is
  confidential
- WHEN `openkos query "<question>" --save` files the answer
- THEN the filed concept's sensitivity is confidential

#### Scenario: Unreadable or unparseable citation folds to confidential

- GIVEN a cited concept's file is missing, or its frontmatter is
  unparseable, at save time
- WHEN `openkos query "<question>" --save` files the answer
- THEN the filed concept's sensitivity is confidential, not the seeded
  default

#### Scenario: Zero citations refuse to file

- GIVEN zero readable citations
- WHEN `openkos query "<question>" --save` is run
- THEN `query` refuses, exits non-zero, and the bundle is unchanged

### Requirement: Preview, Confirm, And Non-TTY Gate For `--save`

`--save` MUST reuse ingest's stage → preview → confirm → write pipeline:
`query` MUST show a preview of the additions (new bundle file, `index.md`,
`log.md`) before writing. WHEN running on a TTY with review enabled, `query`
MUST prompt for confirmation unless `--auto` is passed. WHEN running
non-interactively (no TTY) with review enabled and `--auto` is absent,
`query` MUST refuse to write and exit non-zero, leaving the bundle
unchanged.

#### Scenario: TTY confirms before writing

- GIVEN an interactive TTY and review enabled
- WHEN `openkos query "<question>" --save` is run without `--auto`
- THEN a preview of the new file, index, and log changes is shown and the
  write proceeds only after confirmation

#### Scenario: `--auto` or `review: false` bypasses the prompt

- GIVEN `--auto` is passed, or config sets `review: false`
- WHEN `openkos query "<question>" --save` is run
- THEN the preview is shown and the write proceeds without prompting

#### Scenario: Non-TTY without `--auto` refuses to write

- GIVEN no TTY is attached, review is enabled, and `--auto` is absent
- WHEN `openkos query "<question>" --save` is run
- THEN `query` refuses to write, exits non-zero, and the bundle is
  unchanged

### Requirement: Filed Concept Is Not Auto-Reindexed

`--save` MUST NOT trigger `reindex`. After a successful write, `query` MUST
print a distinct "filed answer" log entry and a stdout/stderr hint telling
the user to run `openkos reindex` so the new concept becomes retrievable.

#### Scenario: Successful filing hints at reindex

- GIVEN `openkos query "<question>" --save"` files a concept successfully
- WHEN the write completes
- THEN a "filed answer" log entry is recorded and output hints at running
  `openkos reindex`
