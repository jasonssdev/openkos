# Delta for Query Command

## ADDED Requirements

### Requirement: Revision History Citations Are Marked

`query` MUST render a citation whose `history` field is `"superseded"` with
a `[superseded]` marker and one whose `history` field is `"refined"` with a
`[refined]` marker, following the same trailing-marker convention as the
existing `[confidential]` and `[synthesis]` markers. An ordinary hit
citation (`history` is `None`) MUST carry neither marker on its account.

#### Scenario: A superseded history citation renders its marker

- GIVEN an answer citing a history block whose `Citation.history` is
  `"superseded"`
- WHEN `openkos query "<question>"` renders its citations
- THEN that citation's line ends with `[superseded]`

#### Scenario: A refined history citation renders its marker

- GIVEN an answer citing a history block whose `Citation.history` is
  `"refined"`
- WHEN `openkos query "<question>"` renders its citations
- THEN that citation's line ends with `[refined]`

#### Scenario: An ordinary citation carries neither marker

- GIVEN an answer citing only ordinary hits, none of which are revision
  history
- WHEN `openkos query "<question>"` renders its citations
- THEN no citation line carries `[superseded]` or `[refined]`

### Requirement: Revision History Truncation Notice

WHEN `result.history_truncated_titles` is non-empty, `query` MUST print one
stderr notice, after the existing omitted-context notice, naming the count
and titles of the retrieved successors whose own history was truncated:
`openkos query: the revision history of N document(s) (T1, T2) goes back
further than the earlier versions shown; the answer did not see the rest.`
WHEN `result.history_truncated_titles` is empty — including every run with
`revision_history` disabled — `query` MUST NOT print this notice.

#### Scenario: A truncated successor's title triggers the stderr notice

- GIVEN `answer(...)` returns with `history_truncated_titles` containing one
  title
- WHEN `openkos query "<question>"` runs
- THEN stderr carries the notice naming that title, printed after the
  existing omitted-context notice

#### Scenario: Multiple truncated successors are named together

- GIVEN `history_truncated_titles` contains two titles
- WHEN `openkos query "<question>"` runs
- THEN the stderr notice names both titles and reports a count of 2

#### Scenario: No truncation prints no notice

- GIVEN `history_truncated_titles` is `[]` — including every run where
  `revision_history` is disabled
- WHEN `openkos query "<question>"` runs
- THEN no revision-history truncation notice is printed to stderr

### Requirement: `revision_history` Config Key Is Threaded By `run_query`

`config.py` MUST accept an optional top-level `revision_history` key,
validated as bool-only — rejecting a non-bool value the way other boolean
config keys are rejected — and defaulting to `False` when absent. The
packaged `templates/openkos.yaml.template` MUST include a comment
documenting the key as opt-in, unmeasured, and off by default, and MUST NOT
recommend enabling it.

`run_query` (`application/query.py`) MUST read `cfg.revision_history` and
pass it to `answer(..., revision_history=cfg.revision_history)`.

`read_config` MUST NOT reject an unrecognized top-level key. This applies to
a leftover `revision_history` key found in `openkos.yaml` after this feature
is reverted from the codebase — `read_config` reads declared keys by name
and never rejects a key it does not look for, so a stale line requires no
user action.

#### Scenario: Key absent defaults to off

- GIVEN `openkos.yaml` has no `revision_history` line
- WHEN `read_config` loads it
- THEN `cfg.revision_history` is `False`

#### Scenario: Key present and true is threaded to answer()

- GIVEN `openkos.yaml` sets `revision_history: true`
- WHEN `openkos query "<question>"` runs
- THEN `run_query` calls `answer(..., revision_history=True)`

#### Scenario: A non-bool value is rejected

- GIVEN `openkos.yaml` sets `revision_history` to a non-bool value (e.g. a
  string or number)
- WHEN `read_config` loads it
- THEN configuration validation rejects it, the same way another
  bool-only key is rejected

#### Scenario: The template documents the key as unmeasured and off by default

- GIVEN `templates/openkos.yaml.template`
- WHEN its `revision_history` comment is read
- THEN it states the key is opt-in and unmeasured, and does not recommend
  enabling it

#### Scenario: A leftover key from a reverted feature is silently ignored

- GIVEN `openkos.yaml` still contains a `revision_history: true` line after
  the feature that reads it has been reverted, so `Config` no longer
  declares that field
- WHEN `read_config` loads the file
- THEN it succeeds without error — `read_config` never rejects an unknown
  top-level key, so the leftover line requires no user action

## MODIFIED Requirements

### Requirement: `--save` Files The Cited Answer As An Insight

WHEN `--save` is passed and `answer()` returns a matched result, `query`
MUST, after rendering the answer, build a new document via the ingest
builder with: body = the rendered answer text; title = the first rung of the
TITLE LADDER that resolves -- the answer's first sentence as a DECLARATIVE
title, else a definitional question's own SUBJECT (issue #646), else that
first sentence's opening CLAUSE when it was refused for LENGTH alone (issue
#696), else the question verbatim -- or `--title` when given; description =
the question, or `--description` when given; type
= `"Insight"` (the filed-synthesis type, issue #570), or `--type` when
given (any buildable type); provenance = the cited concepts' ids
(`result.citations`).

The type distinction is truth-decay (issue #570): an extracted `Concept`
depends on an immutable `Source`; a filed synthesis depends on the MUTABLE
bundle, so every ingest, merge, or correction can invalidate it. `Insight`
therefore defaults to the `volatile` tier, is never emitted by the LLM
classifier (`BUILDER_ONLY_TYPES`), files under `bundle/insights/`, and its
slug -- the permanent Concept ID -- is declarative rather than an
interrogative sentence.

WHEN a filed citation's `history` field is not `None`, the `provenance:`
list MUST still be that citation's `concept_id` exactly like any other
cited concept — the `provenance:` shape (a flat id list) is UNCHANGED.
`model/okf.py`'s `build_concept` MUST accept an optional per-reference
`related_notes` mapping, defaulting to `None` so every existing call site
stays byte-identical, so that a history citation's `## Related` bullet
reads `— earlier version (superseded) cited as history for this answer`
for a `"superseded"` history citation, or `— earlier version (refined)
cited as history for this answer` for a `"refined"` one. An ordinary
(non-history) cited concept's `## Related` bullet MUST be unaffected —
unchanged in shape from current behavior.
(Previously: no history citations existed, so `provenance:` and every
`## Related` bullet were built from ordinary cited concepts only, and
`build_concept` accepted no `related_notes` parameter.)

#### Scenario: Default filing is a declaratively-titled Insight

- GIVEN `openkos query "<question>" --save` is run and the answer matches
  with a usable first sentence
- WHEN the document is built
- THEN body is the rendered answer, the title is the answer's first
  sentence, the description is the question, the type is `"Insight"` under
  `bundle/insights/`, and provenance lists the cited concept ids

#### Scenario: A definitional question titles the filing by its subject

- GIVEN the answer's first sentence is unusable AND the question is a
  recognized definitional scaffold (`¿qué es el Model Context Protocol?`)
- WHEN the document is built
- THEN the title is the question's subject (`Model Context Protocol`),
  never the clause rung below it

#### Scenario: An over-long first sentence titles the filing by its clause

- GIVEN the answer's first sentence is refused for LENGTH ALONE and the
  question is not a recognized definitional scaffold (`¿por qué es
  importante la trazabilidad en un sistema de conocimiento?`)
- WHEN the document is built
- THEN the title is that sentence cut at its first clause boundary, so the
  permanent Concept ID is declarative rather than interrogative

#### Scenario: An unusable first sentence falls back to the question title

- GIVEN the answer's first sentence is shorter than the declarative
  minimum, or itself a question, or is over-long with no clause boundary to
  cut at, AND the question names no recognizable subject
- WHEN the document is built
- THEN the title falls back to the question (the pre-#570 default)

#### Scenario: `--title`, `--description`, `--type` override defaults

- GIVEN `openkos query "<question>" --save --title "T" --type "Procedure"`
- WHEN the document is built
- THEN title is `"T"` and type is `"Procedure"`, overriding the derived
  title and `"Insight"` defaults

#### Scenario: A superseded history citation is filed with its mark

- GIVEN an answer citing a history block whose `Citation.history` is
  `"superseded"`
- WHEN `openkos query "<question>" --save` files the answer
- THEN `provenance:` includes that predecessor's `concept_id` exactly as
  any other cited concept, and the filed concept's `## Related` section
  carries a bullet for it marked `(superseded)`

#### Scenario: A refined history citation is filed with its mark

- GIVEN an answer citing a history block whose `Citation.history` is
  `"refined"`
- WHEN `openkos query "<question>" --save` files the answer
- THEN the filed concept's `## Related` section carries a bullet for that
  predecessor marked `(refined)`

#### Scenario: An ordinary cited concept's Related bullet is unaffected

- GIVEN an answer citing only ordinary (non-history) concepts
- WHEN `openkos query "<question>" --save` files the answer
- THEN the filed concept's provenance and `## Related` section are
  byte-identical in shape to the pre-history-feature behavior

#### Scenario: build_concept without related_notes stays byte-identical

- GIVEN an existing call site of `model/okf.py`'s `build_concept` that does
  not pass `related_notes`
- WHEN that call site runs unchanged
- THEN its output is byte-identical to its pre-history-feature output
