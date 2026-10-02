# Delta for Curate Command

## MODIFIED Requirements

### Requirement: Structure Stage Writes Through The Relate Core

Structure MUST call `suggest_edge_types` with `on_progress`, and MUST
write each accepted suggestion through the extracted `relate` core.
Declined and skipped suggestions MUST be skipped without a write.

#### Scenario: Accepted suggestion writes via the extracted core

- GIVEN one accepted edge-type suggestion
- WHEN Structure applies it
- THEN the write occurs through the extracted `relate` core and matches
  standalone `relate`'s output

#### Scenario: Declined suggestion is skipped

- GIVEN one declined suggestion
- WHEN Structure processes it
- THEN no edge is written

#### Scenario: A skipped suggestion is not listed as declined

- GIVEN one suggestion answered `s`
- WHEN Structure processes it
- THEN no edge is written and the suggestion is counted as skipped, but no
  `declined:` line names it

### Requirement: Bulk Acceptance Excludes Asymmetric Relation Types

An accepted Structure stage MUST apply every suggestion whose type is not
asymmetric without asking, including `related_to`. `related_to` is the
answer the prompt designates as correct when the documents do not support a
specific relationship, so applying it adds no claim beyond the untyped link
that already existed; prompting for it saves the operator nothing and
erodes the attention the asymmetric prompts need.

An accepted Structure stage MUST still route every asymmetric type
(`caused_by`, `depends_on`, `member_of`, `part_of`, `produced_by`) to the
operator, because the suggested direction is unverified. On a TTY that
prompt MUST accept, besides `y`, `n` and `s` (skip), an answer `a` that accepts the
item and every remaining item of the SAME asymmetric type for the run, and
an answer `r` that applies the item with source and target swapped. `a`
MUST NOT apply any other asymmetric type, and the flag by itself MUST NOT
apply any asymmetric type. An unrecognized answer asks again.

On a non-TTY run there is no channel to ask on, so an asymmetric item MUST
be counted as skipped rather than prompted — reaching the prompt with no
terminal would kill the walk mid-run.

Each applied item keeps its own commit and log entry.

#### Scenario: `related_to` applies without a prompt

- GIVEN a Structure queue with one specific suggestion and one `related_to`
- WHEN `curate --accept structure` runs on a TTY
- THEN both suggestions are written with no per-item prompt

#### Scenario: Accept-the-rest is scoped to one asymmetric type

- GIVEN two `part_of` suggestions and one `depends_on` suggestion
- WHEN `curate --accept structure` runs on a TTY and the first `part_of`
  prompt is answered `a`
- THEN both `part_of` suggestions are written, and the `depends_on`
  suggestion is still prompted

#### Scenario: The reversed direction is an explicit answer

- GIVEN an asymmetric suggestion `a -> b [produced_by]`
- WHEN the operator answers `r`
- THEN the relation is written from `b` to `a`

#### Scenario: On a pipe asymmetric items are skipped, not prompted

- GIVEN a queue with one specific suggestion and one asymmetric suggestion
- WHEN `curate --auto --accept structure` runs with stdout piped
- THEN the specific suggestion is written, the asymmetric suggestion is
  counted as skipped, and no prompt is printed

## ADDED Requirements

### Requirement: Identity Prompt Separates Skip From A Keep-Distinct Ruling

The Identity stage's per-item merge prompt MUST be exactly the prompt
`adjudicate --apply` asks (`entity-resolution-adjudication`: Survivor/Absorbed
Preview And Prompt) and MUST accept the same answers (`entity-resolution-
adjudication`: Prompt Response Semantics): `y`/`yes` applies the merge;
`s`/`skip`, `n`/`no` and empty input skip it, writing nothing and leaving the
pair pending so the next run offers it again; `d`/`distinct` records the
permanent keep-distinct ruling. Only `d` MUST persist a ruling, and the
summary's `declined:` listing MUST name only pairs answered `d`. A skipped
pair MUST NOT resolve its pending-queue row.

#### Scenario: `n` skips without a ruling

- GIVEN one Identity pair and `input="n\n"`
- WHEN `curate` runs the Identity stage
- THEN no merge and no keep-distinct ruling is written, the pair is counted
  as skipped, no `declined:` line names it, and its pending row stays open

#### Scenario: `d` records the ruling

- GIVEN one Identity pair and `input="d\n"`
- WHEN `curate` runs the Identity stage
- THEN the keep-distinct ruling is recorded, the pending row is `declined`,
  and `declined: <absorbed> -> <survivor>` names the pair

### Requirement: Structure, Metadata And suggest-relations Prompts Offer Skip

The per-item write prompts of `curate`'s Structure and Metadata stages and of
`suggest-relations --apply` MUST be `[y/N/s]`: `y`/`yes` writes; `n`/`no` and
empty input decline; `s`/`skip` skips. Neither declining nor skipping writes
anything or records a ruling, and either leaves the item's pending row open;
they differ only in that a declined item is named in the `declined:` listing
and a skipped one is counted but not named. Any other answer MUST be re-asked
with a one-line notice naming the accepted tokens.

#### Scenario: `s` skips a Metadata tier

- GIVEN one Metadata suggestion and `input="s\n"`
- WHEN Metadata processes it
- THEN nothing is written, the item is counted as skipped, and no
  `declined:` line names it

#### Scenario: `s` skips a `suggest-relations --apply` suggestion

- GIVEN one suggestion and `input="s\n"`
- WHEN `suggest-relations --apply` runs
- THEN no relation is written and no `declined:` line names the suggestion

### Requirement: Accept-Remaining Answer On Non-Destructive Stages

The Structure and Metadata per-item prompts MUST accept `a`/`all`, which
accepts the item and every remaining item of that stage that is acceptable in
bulk, for the rest of the run -- the same set `--accept` applies. Structure's
asymmetric relation types MUST still be asked per item (`a` on a symmetric
prompt never applies one; the asymmetric prompt's own `a` keeps its per-type
meaning), and `a` MUST NOT be offered on the Identity prompt, whose
accept-recommended path is not defined. Before applying items without asking,
the stage MUST print a one-line notice on stderr that it is doing so. `a` is
offered only on the prompt of a bulk-acceptable item.

#### Scenario: `a` accepts the rest of the Metadata stage

- GIVEN three Metadata suggestions and `input="a\n"`
- WHEN Metadata runs
- THEN all three tiers are written and only the first was asked

#### Scenario: `a` on a symmetric relation still asks asymmetric ones

- GIVEN a `related_to` suggestion followed by a `part_of` suggestion
- WHEN the operator answers `a` to the first
- THEN the `related_to` is written and the `part_of` is still prompted

#### Scenario: Identity does not offer `a`

- GIVEN one Identity pair
- WHEN the operator answers `a`
- THEN the answer is unrecognized and the prompt is asked again
