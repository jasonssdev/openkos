# Curate Command Specification

## Purpose

`openkos curate` is a single interactive session that walks the five kinds
of pending human judgment — identity, structure, metadata, sensitivity,
contradictions — in the one order that is safe (ADR-0005/ADR-0011), gating
each stage's model cost and never letting a decline abort the rest.

## Requirements

### Requirement: Stage Order Is A Product Invariant

`curate` MUST run stages in exactly this order: Preconditions, Identity,
Structure, Metadata, Contradictions. A declined, unavailable, or skipped
stage MUST NOT abort or skip any later stage.

#### Scenario: Full run visits stages in order

- GIVEN a bundle with pending findings in all five categories
- WHEN `openkos curate` runs
- THEN Preconditions, Identity, Structure, Metadata, Contradictions are
  visited in that exact order

#### Scenario: Declining one stage does not abort later stages

- GIVEN the operator declines the Structure stage's cost gate
- WHEN `curate` continues
- THEN Structure records a decline notice and Metadata and Contradictions
  still run

### Requirement: Per-Stage Cost Gate

Each stage descriptor carries a `writes` capability field. Every stage
that would call an LLM MUST print its item count and resulting LLM-call
count before contacting the model, then confirm unless `--auto` is
passed. Without `--auto`, in a non-TTY session, EVERY LLM-costing stage
MUST decline before any model call — no model spend without consent. With
`--auto`, cost gates are auto-accepted; a read-only stage (Contradictions)
MUST then run and report, while a write stage (`writes: true`) MUST
decline its per-item write walk — because per-item write confirmation
cannot happen without a TTY — and MUST print a pointer to the
corresponding standalone verb (e.g. `adjudicate --apply-same
--confirm-count` for Identity).

#### Scenario: Gate states cost before any model call

- GIVEN 6 untyped edge candidates
- WHEN the Structure stage gate prints
- THEN it reads `6 untyped edge(s) -> 6 LLM call(s)` before any model
  call occurs

#### Scenario: --auto accepts every gate

- GIVEN `--auto` is passed
- WHEN `curate` reaches any stage's gate
- THEN the gate is accepted without a prompt

#### Scenario: Non-TTY without --auto declines every LLM-costing stage

- GIVEN stdin is not a TTY and `--auto` is not passed
- WHEN `curate` reaches any LLM-costing stage
- THEN that stage declines before any model call, with no exception for
  read-only stages

#### Scenario: Non-TTY with --auto runs read-only stages, declines writes

- GIVEN stdin is not a TTY and `--auto` is passed
- WHEN `curate` reaches Identity (a write stage) and then Contradictions
  (a read-only stage)
- THEN Identity declines its write walk and prints the pointer to
  `adjudicate --apply-same --confirm-count`, and Contradictions runs and
  reports its findings

### Requirement: Preconditions Stage Halts The Run

`curate` MUST probe `vectors.db` via the existing degrade seam before
Identity. A missing or empty index MUST print the consequence (starved
candidate edges) and a pointer to `openkos reindex`, then MUST exit 0
without running Identity or any later stage.

#### Scenario: Missing vectors.db halts before Identity

- GIVEN no `vectors.db` exists
- WHEN `curate` runs Preconditions
- THEN it prints the starved-candidate-edges consequence and the
  `openkos reindex` pointer, exits 0, and no later stage runs

### Requirement: Identity Stage Reuses Merge Cores

Identity MUST call `find_candidates` then `adjudicate_candidates`, then
apply each accepted pair via `_prepare_one_merge`/`_commit_one_merge`,
auto-committing per merge. N>2 groups MUST NOT be auto-merged; `curate`
MUST print the exact pairwise `openkos merge` commands per group.
Because `find_candidates` now bounds and ranks its output before any
adjudication call (entity-resolution delta: Bounded Candidate-Group
Output Per Call), the number of `CandidateGroup`s Identity's probe
(`_identity_probe`, `cli/curate.py:271-282`) queues, and therefore the
number of adjudication calls `_identity_run` issues, MUST never exceed
`_MAX_CANDIDATE_GROUPS` regardless of corpus size — the SAME sequencer
that already gates Identity's cost line and consent flow (curate-command:
Per-Stage Cost Gate) is unchanged; only the upstream group count it reads
from `probe.llm_calls` is now bounded.

#### Scenario: Accepted pair is committed per-item

- GIVEN one accepted duplicate pair
- WHEN Identity applies it
- THEN `_prepare_one_merge`/`_commit_one_merge` run and the bundle
  auto-commits before the next item

#### Scenario: N>2 group prints pairwise commands, never auto-merges

- GIVEN a candidate group of 3
- WHEN Identity reaches it
- THEN it prints the exact pairwise `openkos merge` commands and performs
  no merge

#### Scenario: Identity's adjudication call count stays capped on a large corpus

- GIVEN a bundle whose Identity queue would otherwise total 150
  `CandidateGroup`s (an uncapped `find_candidates` result)
- WHEN `curate` runs the Identity stage with `--auto`
- THEN the printed cost line's call count and the number of adjudication
  calls actually issued both stay at or below `_MAX_CANDIDATE_GROUPS`

### Requirement: Structure Stage Writes Through The Relate Core

Structure MUST call `suggest_edge_types` with `on_progress`, and MUST
write each accepted suggestion through the extracted `relate` core.
Declined suggestions MUST be skipped without a write.

#### Scenario: Accepted suggestion writes via the extracted core

- GIVEN one accepted edge-type suggestion
- WHEN Structure applies it
- THEN the write occurs through the extracted `relate` core and matches
  standalone `relate`'s output

#### Scenario: Declined suggestion is skipped

- GIVEN one declined suggestion
- WHEN Structure processes it
- THEN no edge is written

### Requirement: Metadata Stage Writes Tiers, Reports Sensitivity

Metadata MUST call `suggest_volatility` with `on_progress` and write
accepted tiers through the extracted `set-volatility` core. Sensitivity
gaps surfaced in the same pass MUST be reported only; Metadata MUST NOT
write sensitivity.

#### Scenario: Accepted tier writes via the extracted core

- GIVEN one accepted volatility tier
- WHEN Metadata applies it
- THEN the write occurs through the extracted `set-volatility` core

#### Scenario: Sensitivity gap is reported, never written

- GIVEN a concept with an unset sensitivity level
- WHEN Metadata reports it
- THEN it prints the gap and names `openkos set-sensitivity`, writing
  nothing

### Requirement: Contradictions Stage Is Report-Only And Last

Contradictions MUST run last, MUST call `find_contradictions` with
`on_progress`, and MUST NOT propose or perform any write.

#### Scenario: Contradictions never writes

- GIVEN pending contradictions exist
- WHEN the Contradictions stage runs
- THEN it prints them and the run ends with no write hint

### Requirement: Resumability By Construction

`curate` MUST NOT persist any queue or checkpoint file. Interrupting
mid-run and re-invoking MUST re-derive every stage's queue from current
bundle state and MUST NOT replay an already-committed decision.

#### Scenario: Interrupted run resumes from bundle state

- GIVEN Identity committed one merge before interruption
- WHEN `curate` is re-invoked
- THEN it re-derives candidates from post-merge state and does not
  reprocess the committed pair

### Requirement: Sensitivity Threading Is Fail-Closed

`--include-confidential` and `--include-deprecated` MUST be forwarded to
every stage's underlying call. Omitting them MUST exclude confidential
and deprecated content by default.

#### Scenario: Confidential content excluded by default

- GIVEN confidential concepts exist and `--include-confidential` is
  omitted
- WHEN any stage runs
- THEN confidential concepts are excluded from that stage's input

### Requirement: Output Discipline And Summary

`curate` MUST honor `NO_COLOR` and non-TTY output using only the existing
`observability` progress helpers, and MUST print a summary line naming
each stage's outcome at the end of the run.

#### Scenario: Piped output stays clean

- GIVEN stdout is piped and `NO_COLOR=1`
- WHEN `curate` runs
- THEN no ANSI color codes or interactive prompts appear in the output

#### Scenario: Summary line names every stage outcome

- GIVEN a completed run
- WHEN `curate` finishes
- THEN one summary line lists each of the five stages with its outcome

### Requirement: Per-Stage Accept-All Is Opt-In And Never Covers Identity

`curate` MUST accept a `--accept STAGES` option taking a comma-separated,
case-insensitive list of stage names whose per-item write prompts are
answered yes without asking. Only stages marked auto-acceptable may be
named; today that is Structure and Metadata.

`--accept identity` MUST be refused with exit 2, and so MUST any name that
is not a stage at all. Both refusals MUST run BEFORE the workspace gate, so
a typo is reported as itself rather than as a missing workspace, and the
refusal MUST name the acceptable stages. Identity is excluded because a
merge absorbs one concept into another and DELETES the absorbed file; no
flag and no config value may apply one unreviewed.

Naming a stage in `--accept` IS per-item write consent for that stage, so
an accepted stage MUST also pass the non-TTY write refusal — `curate --auto
--accept structure` on a pipe writes, matching `suggest-relations --auto`.
Identity MUST remain subject to that refusal on every path.

#### Scenario: An accepted stage applies without prompting

- GIVEN a Structure queue with two valid suggestions
- WHEN `curate --accept structure` runs
- THEN both suggestions are written, no per-item prompt is printed, and the
  summary reports `applied 2, skipped 0`

#### Scenario: Identity cannot be accepted in bulk

- GIVEN any workspace
- WHEN `curate --accept identity` runs
- THEN the exit code is 2, nothing is written, and stderr names the
  acceptable stages

#### Scenario: An unknown stage name is a usage error

- GIVEN any workspace
- WHEN `curate --accept strcture` runs
- THEN the exit code is 2 and stderr names the offending value

### Requirement: Bulk Acceptance Excludes The Least-Specific Relation Type

An accepted Structure stage MUST still route a `related_to` suggestion to
the operator. Every other suggestable type asserts a specific relationship;
`related_to` is the answer the prompt designates as correct when the
documents do not support one, so applying it adds no claim beyond the
untyped link that already existed. It is therefore the cheapest place to
spend a human glance, and at a measured 67% of accepted edges also the
largest.

On a TTY the exempted item falls back to the per-item prompt. On a non-TTY
run there is no channel to ask on, so it MUST be counted as skipped rather
than prompted — reaching the prompt with no terminal would kill the walk
mid-run.

This exemption is scoped to the item, not the stage: the same run still
applies every specific suggestion without asking.

#### Scenario: A specific type applies while `related_to` is prompted

- GIVEN a Structure queue with one specific suggestion and one `related_to`
- WHEN `curate --accept structure` runs on a TTY
- THEN the specific suggestion is written with no prompt
- AND the `related_to` suggestion is prompted per item

#### Scenario: On a pipe the exempted item is skipped, not prompted

- GIVEN the same queue
- WHEN `curate --auto --accept structure` runs with stdout piped
- THEN the specific suggestion is written, the `related_to` suggestion is
  counted as skipped, and no prompt is printed

### Requirement: `review: false` Accepts Only The Non-Destructive Stages

When `--accept` is absent, `review: false` in `openkos.yaml` MUST accept
every auto-acceptable stage — the knob already means "do not confirm before
saving" for the standalone verbs, and `curate` MUST stop ignoring it.

It MUST NOT reach Identity. A value set for the standalone verbs cannot
become retroactive authorization to delete a concept, so every merge still
prompts, and on a non-TTY run Identity still refuses its write walk and
prints the standalone-verb hint.

An explicit `--accept` MUST override `review` and name the exact accepted
set rather than widening it, so an operator running with `review: false`
can still re-review a single stage without editing the config file.

#### Scenario: `review: false` accepts Structure but never Identity

- GIVEN `review: false` and both an Identity and a Structure queue
- WHEN `curate` runs
- THEN Structure applies without per-item prompts
- AND every Identity merge is still prompted individually

#### Scenario: An explicit `--accept` narrows `review: false`

- GIVEN `review: false`, a Structure queue and a Metadata queue
- WHEN `curate --accept structure` runs
- THEN Structure applies silently and Metadata prompts per item

### Requirement: A Failed Writing Stage Discloses What It Already Applied

A writing stage (Identity, Structure, Metadata) that fails part-way through
its batch has, by construction, already committed the accepted writes that
preceded the failure — Identity's merges DELETE the absorbed concept. Its
summary line MUST therefore report the applied and skipped counts, on BOTH
failure shapes: the `failed` outcome returned for a generic error, and the
`unavailable` outcome the sequencer builds when the stage re-raises an
availability failure to short-circuit later stages that would contact the
same model.

Contradictions is exempt: it is report-only and applies nothing, so it has
no destructive work to disclose.

#### Scenario: Generic mid-batch failure discloses write counts

- GIVEN Identity has merged one accepted pair and its next chat fails with a
  generic error
- WHEN `curate` finishes
- THEN Identity's summary line reports both the completed-of-total
  adjudication counts and `applied 1, skipped 0`

#### Scenario: Availability failure still discloses write counts

- GIVEN a writing stage has applied one accepted item and Ollama then
  becomes unavailable mid-batch
- WHEN `curate` finishes
- THEN that stage's summary line carries the availability remediation text
  AND states what it had already applied and skipped before the failure

### Requirement: Each Stage Resolves Its Own Task Model

Every `needs_llm` stage MUST declare which measured task its LLM calls
belong to, and MUST contact the model `models:` names for that task,
falling back to the global `model:` when the workspace names none (#515).
Stage tasks are `adjudication` (Identity), `edge_typing` (Structure),
`volatility_typing` (Metadata), and `contradiction` (Contradictions);
Preconditions makes no LLM calls and declares no task.

Tasks are keyed by TASK, never by stage or verb: Structure and the
standalone `suggest-relations` verb both run `suggest_edge_types`, and a
per-verb key would let the two drift onto different models.

WHEN a stage resolves a model other than the global `model:`, its cost gate
MUST disclose that model before asking for consent — the same item count
means a materially different spend depending on which model runs it. That
disclosure MUST NOT alter the `cost_line` literal itself, so a workspace
that names no per-task model produces byte-identical gate output (see
"Below-Cap Cost-Line Output Is Byte-Identical To Pre-Change Behavior").

WHEN a named model is not installed, ONLY the stage that named it MUST
fail, and its remediation MUST name that model rather than the global
default. Falling back to the global model MUST NOT happen: the operator
would keep writing relation types believing they came from the model they
named.

#### Scenario: A stage runs on its own task model

- GIVEN `openkos.yaml` sets `model: qwen3:8b` and `models.edge_typing:
  gemma2:27b`
- WHEN `curate` reaches the Structure stage
- THEN Structure contacts `gemma2:27b` and every other stage contacts
  `qwen3:8b`

#### Scenario: The cost gate discloses a non-default model

- GIVEN Structure resolves `gemma2:27b` while the global default is
  `qwen3:8b`
- WHEN Structure's cost gate asks for consent
- THEN the printed output names `gemma2:27b` alongside the unchanged
  `"{n} untyped edge(s) -> {n} LLM call(s)"` line

#### Scenario: No per-task model leaves gate output unchanged

- GIVEN a workspace with no `models:` key
- WHEN any stage's cost gate asks for consent
- THEN the printed output is byte-identical to its pre-#515 wording

#### Scenario: A missing task model fails only its own stage

- GIVEN `models.edge_typing` names a model that is not installed
- WHEN `curate` runs
- THEN Structure reports unavailable with an `ollama pull` remediation
  naming THAT model, and Metadata and Contradictions still run

### Requirement: Availability Is Tracked Per Model, Not Per Run

An availability failure (`OllamaUnavailable` or `OllamaModelNotFound`) MUST
skip only the later `needs_llm` stages that resolve the SAME model. A stage
resolving a different model MUST still be attempted (#515).

This replaces the run-scoped skip: one failed connection no longer settles
reachability for models it never contacted. The deliberate cost is that a
genuinely dead server is contacted once per DISTINCT model rather than once
per run; clients MUST be cached by model so stages sharing a tag share one
connection. In a workspace with no `models:` override every stage resolves
the same tag, so the observable behavior is unchanged.

#### Scenario: Failure on one model does not skip a stage on another

- GIVEN Structure resolves `gemma2:27b`, Metadata resolves the global
  default, and Structure fails with an availability error
- WHEN `curate` continues
- THEN Metadata is still attempted

#### Scenario: Failure still skips a later stage on the same model

- GIVEN no `models:` override, so every stage resolves the same tag, and an
  early stage fails with an availability error
- WHEN `curate` continues
- THEN every later `needs_llm` stage is skipped as unavailable

### Requirement: Exit Codes Match Existing Verb Conventions

`curate` MUST exit 0 on a completed or declined run (including a
Preconditions halt), 1 on failure, 2 on usage error, and 3 on a drift
refusal, consistent with other verbs (#319).

#### Scenario: Declined stages still exit zero

- GIVEN every stage is declined
- WHEN `curate` finishes
- THEN it exits 0

#### Scenario: Drift refusal exits three

- GIVEN a write stage detects a drifted target during its write walk
- WHEN `curate` refuses that write
- THEN it exits 3

### Requirement: Extracted Cores Preserve Standalone Behavior

The `relate` and `set-volatility` extraction into pure Phase-A/Phase-B
cores MUST NOT change either verb's observable output; their existing
test suites MUST pass unedited.

#### Scenario: Standalone relate output is unchanged

- GIVEN the same inputs as before extraction
- WHEN `openkos relate` runs standalone
- THEN its output is byte-identical to pre-extraction behavior

### Requirement: Identity Cost Line Discloses Truncation

`_identity_probe` MUST expose the SAME `produced`/`retained` truncation
signal `find_candidates` now makes observable (entity-resolution delta:
Truncation Is Never Silent), through `StageProbe.notice` — the same
channel `_structure_probe` already uses for the Structure stage's
candidate-edge cap (`cli/curate.py:417-431`). WHEN Identity's candidate-
group set is truncated (`produced > retained`), the printed notice MUST
disclose both counts, in a shape consistent with the existing
`"{retained} of {produced} ... shown (cap reached)"` pattern
(`resolution/edge_typing.py:589`) substituting the group noun for the
edge noun used by Structure. WHEN Identity's candidate-group set is NOT
truncated (`produced == retained`), NO truncation notice MUST be printed,
matching Structure's existing no-truncation behavior. The exact notice
wording is confirmed at design time; only this disclose-iff-truncated
contract, and the `{retained} of {produced}` count pair within it, are
required here.

#### Scenario: Cap reached — Identity's notice discloses both counts

- GIVEN a bundle whose Identity candidate-group set is truncated from 80
  produced to 50 retained
- WHEN `curate` runs the Identity stage
- THEN a notice is printed disclosing both the produced count (80) and
  the retained count (50), in the "N of M ... shown (cap reached)" shape

#### Scenario: Cap not reached — no truncation notice

- GIVEN a bundle whose Identity candidate-group set has 12 produced and
  12 retained groups (below the cap)
- WHEN `curate` runs the Identity stage
- THEN no truncation notice is printed for Identity

### Requirement: Below-Cap Cost-Line Output Is Byte-Identical To Pre-Change Behavior

For any bundle whose Identity `CandidateGroup` count does not exceed
`_MAX_CANDIDATE_GROUPS`, EVERY existing pinned literal in
`tests/unit/cli/test_curate.py` that asserts Identity's `cost_line`
output (the `"{n} candidate group(s) -> {n} LLM call(s)"` shape produced
by `cost_line`, `cli/curate.py:188-204`, from `probe.llm_calls`) MUST
remain unchanged: this change MUST NOT alter the cost-line wording,
MUST NOT alter `probe.llm_calls`'s value for a below-cap corpus, and
MUST NOT introduce a truncation notice for a below-cap corpus. Only a
bundle whose candidate-group count exceeds the cap is a test-visible
contract change (a new notice line, and `probe.llm_calls` bounded rather
than equal to the uncapped group count).

#### Scenario: Below-cap Identity cost line is unchanged

- GIVEN a bundle producing 6 candidate groups, below the cap, exactly as
  in the pre-change pinned test fixtures
- WHEN `curate` reaches the Identity stage's cost gate
- THEN the printed cost line reads `"6 candidate group(s) -> 6 LLM
  call(s)"`, byte-identical to its pre-change wording, and no truncation
  notice is printed

#### Scenario: Above-cap Identity cost line reflects the bounded count

- GIVEN a bundle producing 80 candidate groups, exceeding the cap
- WHEN `curate` reaches the Identity stage's cost gate
- THEN the printed cost line's call count is the capped `retained` value
  (50), not the uncapped `produced` value (80), and a truncation notice
  naming both counts is also printed

### Requirement: Slice Boundary

Slice 1 MUST declare all five stages in the runtime `_STAGES` sequence and
MUST implement the stage framework, cost-gate/decline machinery, and
Preconditions + Identity fully; it MUST be independently shippable.
Structure, Metadata, and Contradictions MUST appear in `_STAGES` at
runtime but MUST be skipped without prompting or any model call, and MUST
appear in the end-of-run summary labeled "not yet available in this
version". Slice 2 MUST replace that label with full behavior for
Structure, Metadata, and Contradictions, plus the two core extractions.

#### Scenario: Slice 1 declares five stages, three not-yet-available

- GIVEN only slice 1 is merged
- WHEN `curate` runs
- THEN Preconditions and Identity execute fully, Structure, Metadata, and
  Contradictions are skipped without any prompt or model call, and the
  summary lists all five stages with the latter three marked "not yet
  available in this version"
</content>
