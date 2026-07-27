# LLM Edge Production Specification

## Purpose

`llm-edge-production` is slice 2b of the typed-graph work: a read-only CLI
verb that reads existing UNTYPED body-link edges (`relation_type = NULL`)
from the derived graph projection, asks the LLM to suggest a relation
`type` + rationale for each, and instructs the human to confirm the write
via the existing `relate` verb. Zero new write path.

## Non-Goals

This spec does NOT define: a batch-write verb that writes edges directly;
`Relation` provenance/confidence fields; discovery of NEW edges between
unlinked objects; migrating existing LLM consumers to Pydantic/retry; or
any change to the relation-type vocabulary or graph-projection schema —
all deferred.

## Requirements

### Requirement: Read-Only Suggestion Of Relation Types For Untyped Links

The system MUST provide a CLI verb that reads every existing untyped
body-link edge (source, target, `relation_type = NULL`) from the derived
graph projection and, for each, MUST print an LLM-suggested relation
`type` plus a rationale. The verb MUST perform ZERO writes to any bundle
file, index, or log. Every printed suggested type MUST be a value accepted
by the existing `validate_relation_type` check. The candidate set MUST be
restricted to untyped edges only; edges that already carry a `relation_type`
MUST NOT be listed as suggestion candidates. Because graph projection now
synthesizes `relation_type = "derived_from"` for provenance-mirror edges
(edges whose target is a member of the source document's `provenance:`
frontmatter list), those edges carry a `relation_type` and MUST NOT be
listed as candidates, and MUST NOT trigger an LLM call.
(Previously: the candidate set excluded only edges typed via `relations:`
frontmatter; it now also excludes edges typed by provenance-mirror
projection synthesis, with no code path distinction required since both
sources populate the same `relation_type` field read by this requirement.)

#### Scenario: Verb lists every untyped edge with a valid suggestion

- GIVEN a bundle containing three untyped body-link edges
- WHEN the suggestion verb runs
- THEN it prints all three edges, each with a suggested `type` (a member of
  the relation vocabulary accepted by `validate_relation_type`) and a
  rationale

#### Scenario: Verb performs zero writes

- GIVEN a bundle with untyped body-link edges
- WHEN the suggestion verb runs to completion
- THEN no bundle file, `index.md`, or `log.md` is modified on disk

#### Scenario: Already-typed edges are excluded from suggestions

- GIVEN a bundle where one edge already has a `relation_type` set (via prior
  `relate`) and another edge is untyped
- WHEN the suggestion verb runs
- THEN only the untyped edge appears in the output; the already-typed edge
  is not re-suggested

#### Scenario: Bundle with only provenance-mirror edges surfaces zero candidates

- GIVEN a bundle whose only body links are provenance-mirror edges (every
  link target is a member of its source document's `provenance:`
  frontmatter list, now typed `derived_from` by graph projection)
- WHEN the suggestion verb runs
- THEN it prints zero candidate edges, makes zero LLM calls, and reports
  honestly that there is nothing to type

#### Scenario: A genuine untyped concept-to-concept edge is still surfaced

- GIVEN a bundle containing one provenance-mirror edge (now typed
  `derived_from`) and one genuine untyped concept-to-concept edge whose
  target is not a member of its source's `provenance:` list
- WHEN the suggestion verb runs
- THEN only the genuine untyped edge is printed as a candidate with an
  LLM-suggested type and rationale; the provenance-mirror edge is absent

### Requirement: Fail-Closed LLM Parsing

The system MUST parse LLM output fail-closed: a malformed or partial
response for one candidate edge MUST cause that edge's suggestion to be
dropped or flagged as unresolved, and MUST NOT crash or abort the verb for
the remaining candidates. An item whose suggested type fails
`validate_relation_type` MUST be dropped or flagged, never printed as if
it were a valid suggestion, and never written anywhere.

#### Scenario: Malformed LLM output degrades one item, not the run

- GIVEN the LLM returns unparseable output for one of five candidate edges
- WHEN the suggestion verb runs
- THEN the four well-formed suggestions are printed, the malformed one is
  dropped or flagged, and the verb exits without crashing

#### Scenario: Invalid suggested type is not surfaced as valid

- GIVEN the LLM suggests a type not accepted by `validate_relation_type`
- WHEN the suggestion verb runs
- THEN that item is dropped or flagged as invalid, never printed as an
  accepted suggestion, and nothing is written for it

### Requirement: Ollama Unavailability Points To `doctor`

WHEN the suggestion verb's underlying `suggest_relations` call raises
`OllamaUnavailable`, the CLI MUST catch it before the generic `OllamaError`
handler, print to stderr a message that states Ollama is not responding,
tells the user to start it with `ollama serve`, and additionally points to
`openkos doctor` to diagnose the environment, then exit 1 with zero writes
to any bundle file. The `OllamaModelNotFound` and generic `OllamaError`
branches, and their ordering relative to `OllamaUnavailable`, MUST remain
unchanged.

#### Scenario: Ollama unreachable points to doctor

- GIVEN `suggest_relations` raises `OllamaUnavailable`
- WHEN the suggestion verb runs
- THEN stderr tells the user to run `ollama serve` and also names
  `openkos doctor` to diagnose the environment
- AND the process exits 1 with zero writes to any bundle file

#### Scenario: Model-not-found and generic errors unchanged

- GIVEN `suggest_relations` raises `OllamaModelNotFound` or a generic
  `OllamaError`
- WHEN the suggestion verb runs
- THEN the existing pull-remedy or generic failure message is printed
  unchanged, with no `doctor` pointer added
- AND the process exits 1

### Requirement: Layering Invariant

The canonical layer (`model`, `bundle`, `state`) MUST NOT import the
derived `graph` layer. The suggestion verb, as derived/CLI code, MAY read
`graph` to source untyped edges.

#### Scenario: Canonical layer has no graph import

- GIVEN the codebase after this change
- WHEN `model`, `bundle`, and `state` modules are inspected for imports
- THEN none of them import from the `graph` package

### Requirement: Human-In-The-Loop Write Path Unchanged

Writing an accepted suggestion MUST go only through the existing `relate`
verb, unmodified by this change: `relate` MUST retain its fail-closed
source/target validation, containment checks, idempotency, and confirm
gate (Phase A compute-no-write, preview, confirm).

#### Scenario: Human confirms a suggestion via relate

- GIVEN the suggestion verb printed `(source, suggested_type, target,
  rationale)` for one edge
- WHEN the human runs `openkos relate <source> <suggested_type> <target>`
  and confirms
- THEN the relation is written via `relate`'s existing validated,
  confirm-gated path, identical to any other `relate` invocation

### Requirement: Three-State Empty-Result Messaging For `suggest-relations`

WHEN `suggest-relations` finds zero candidate edges, it MUST distinguish
four mutually exclusive states rather than a single generic message: (1)
the graph has no concept-to-concept edges at all — nothing to work with
yet; (2) concept-to-concept edges exist and none of them are
untyped/unclaimed; (2b) untyped concept-to-concept edges DO still exist,
but every one of them was excluded by pair-level or confidentiality
filtering; (3) candidates are not computable yet because embeddings are
missing (`vectors.db` absent or empty). State 3 MUST use a message
distinguishable from state 1: an absent/empty embedding index MUST NOT be
reported as "no edges."

State 2's message MUST NOT be emitted when untyped rows remain in the
graph projection. Reporting "none are untyped" is a factual claim about
the projection, so it MUST be selected from a count that actually measures
untyped rows — not from a raw row total that pair-level and
confidentiality filtering never touched. State 2b MUST report how many
untyped rows exist and MUST state that they were excluded rather than
absent.

#### Scenario: Empty graph reports nothing-to-work-with

- GIVEN a bundle whose graph projection has zero concept-to-concept edges
  and `vectors.db` is present and populated
- WHEN `suggest-relations` runs
- THEN it prints a message stating the graph has no concept-to-concept
  edges yet, distinct from both other states, and exits 0

#### Scenario: Every edge is typed

- GIVEN a bundle whose graph projection has concept-to-concept edges and
  every one of them carries a `relation_type`
- WHEN `suggest-relations` runs
- THEN it prints a message stating no untyped candidates remain, distinct
  from the empty-graph message, and exits 0

#### Scenario: Untyped edges exist but every one was excluded

- GIVEN a bundle whose graph projection still holds at least one untyped
  concept-to-concept row whose pair is already typed by a separate
  `relations:` row (the state `relate` leaves behind, since it never
  removes the original untyped body-link row)
- WHEN `suggest-relations` runs
- THEN it prints a message reporting how many untyped rows exist and
  stating they were excluded as already-typed-elsewhere or confidential,
  and it MUST NOT claim that none are untyped, and exits 0

#### Scenario: Missing embeddings reports not-computable-yet

- GIVEN a bundle whose `vectors.db` is absent or empty
- WHEN `suggest-relations` runs
- THEN it prints a message stating candidates are not computable yet due
  to missing embeddings, distinct from both other messages, and exits 0
