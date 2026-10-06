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
--confirm-count` for Identity). The one exception is Identity's automatic
pass under `--auto-merge`, which is not a per-item write walk and which
MUST run on a non-TTY once the gate is accepted by `--auto`; the pass's
spend is part of the Identity gate's disclosed call count, and the per-item
remainder still declines with the pointer.

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

#### Scenario: Non-TTY with --auto and --auto-merge runs only the automatic pass

- GIVEN stdin is not a TTY, `--auto` and `--auto-merge` are passed, and
  Identity has an eligible in-class group and an out-of-class group
- WHEN `curate` reaches Identity
- THEN the automatic pass runs and merges the eligible group, the per-item
  write walk is declined with the pointer, and the Identity cost line's call
  count covered the pass's judgments

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
When `--auto-merge` is passed and the run is eligible, Identity MUST run the
automatic pass (`identity-auto-merge`) before the per-item walk, through the
same merge cores with no reconcile, and MUST NOT offer a group again that
the pass merged. The pass's merges are committed once per run
(`workspace-autocommit`) rather than per merge; every other merge in Identity
keeps its per-merge commit.
Because `find_candidates` bounds and ranks its output before any
adjudication call (`entity-resolution`: Bounded Candidate-Group
Output Per Call), the number of `CandidateGroup`s Identity's probe
(`_identity_probe` in `cli/curate.py`) queues, and therefore the
number of adjudication calls `_identity_run` issues, MUST never exceed
`_MAX_CANDIDATE_GROUPS` regardless of corpus size — the SAME sequencer
that already gates Identity's cost line and consent flow (curate-command:
Per-Stage Cost Gate) is unchanged; only the upstream group count it reads
from `probe.llm_calls` is bounded. A group beyond that cap is not seen by the
automatic pass in that run, and the existing cap disclosure still applies.

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

#### Scenario: The automatic pass precedes the per-item walk

- GIVEN `--auto-merge`, an eligible run, one in-class group the pass merges
  and one out-of-class group
- WHEN Identity runs on a TTY
- THEN the in-class group is merged first, without a prompt, and the
  out-of-class group is then prompted individually

#### Scenario: A group the pass declines is still prompted

- GIVEN `--auto-merge`, an in-class group judged `same` at confidence 0.80
- WHEN Identity runs on a TTY
- THEN the pass does not merge it and the normal per-item prompt offers it

#### Scenario: A group beyond the cap is not seen by the pass

- GIVEN more candidate groups than `_MAX_CANDIDATE_GROUPS`, with an
  eligible in-class group beyond the cap
- WHEN `curate --auto --auto-merge` runs
- THEN that group is not merged in this run and the cap disclosure is printed

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
`on_progress`, and MUST NOT propose or perform any write to the knowledge
bundle. Persisting the verdicts the stage already computed to the
pending-work store (`.openkos/`) is NOT such a write: it records the
stage's own completed output, proposes nothing to the operator, changes no
file under `bundle/`, and requires no prompt.

#### Scenario: Contradictions never writes to the bundle

- GIVEN pending contradictions exist
- WHEN the Contradictions stage runs
- THEN it prints them, persists each verdict to the pending-work store, and
  the run ends with no write to `bundle/` and no write hint

#### Scenario: Persisting a finding is not a bundle write

- GIVEN the Contradictions stage produces one `CONTRADICTS` verdict
- WHEN the stage records that verdict under `.openkos/`
- THEN no file under `bundle/` changes and no `[y/N]` prompt is shown

### Requirement: Resumability By Construction

`curate` MUST NOT persist any queue or checkpoint file. Interrupting
mid-run and re-invoking MUST re-derive every stage's queue from current
bundle state and MUST NOT replay an already-committed decision. Persisting
contradiction findings and operator decisions is NOT persisting a queue or
checkpoint: each stage's candidate queue is still re-derived from current
bundle state on every run, and no run-scoped progress marker is written —
the pending-work store records completed output and operator judgment,
never "where the last run left off".

#### Scenario: Interrupted run resumes from bundle state

- GIVEN Identity committed one merge before interruption
- WHEN `curate` is re-invoked
- THEN it re-derives candidates from post-merge state and does not
  reprocess the committed pair

#### Scenario: A persisted finding is not a resume checkpoint

- GIVEN a prior run persisted one contradiction finding
- WHEN `curate` is re-invoked
- THEN Contradictions re-derives its candidate pairs from current bundle
  state, independent of whether a finding for a pair already exists

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
flag and no config value may apply one unreviewed, with one exception: the
per-run `--auto-merge` flag, which applies only the measured structural class
under `identity-auto-merge` and leaves every other Identity group to its
per-item prompt. `--auto-merge` is not an `--accept` stage name and does not
widen `--accept`.

Naming a stage in `--accept` IS per-item write consent for that stage, so
an accepted stage MUST also pass the non-TTY write refusal — `curate --auto
--accept structure` on a pipe writes, matching `suggest-relations --auto`.
Identity MUST remain subject to that refusal on every path, apart from the
automatic pass under `--auto-merge`.

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

#### Scenario: `--auto-merge` does not make Identity acceptable in bulk

- GIVEN an in-class group the pass merges and an out-of-class `same` group
- WHEN `curate --auto-merge` runs on a TTY
- THEN only the in-class group is merged without a prompt and the other is
  prompted individually

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

### Requirement: `review: false` Accepts Only The Non-Destructive Stages

When `--accept` is absent, `review: false` in `openkos.yaml` MUST accept
every auto-acceptable stage — the knob already means "do not confirm before
saving" for the standalone verbs, and `curate` MUST stop ignoring it.

It MUST NOT run Structure: `review: false` is consent to save without asking,
not a request for the stage, so Structure still needs `--structure` or
`--accept structure`.

It MUST NOT reach Identity. A value set for the standalone verbs cannot
become retroactive authorization to delete a concept, so every merge still
prompts, and on a non-TTY run Identity still refuses its write walk and
prints the standalone-verb hint.

An explicit `--accept` MUST override `review` and name the exact accepted
set rather than widening it, so an operator running with `review: false`
can still re-review a single stage without editing the config file.

#### Scenario: `review: false` accepts Structure but never Identity

- GIVEN `review: false` and both an Identity and a Structure queue
- WHEN `curate --structure` runs
- THEN Structure applies without per-item prompts
- AND every Identity merge is still prompted individually

#### Scenario: An explicit `--accept` narrows `review: false`

- GIVEN `review: false`, a Structure queue and a Metadata queue
- WHEN `curate --accept structure` runs
- THEN Structure applies silently and Metadata prompts per item

#### Scenario: `review: false` alone does not run Structure

- GIVEN `review: false` and a Structure queue
- WHEN `curate` runs with no `--structure` and no `--accept structure`
- THEN Structure is not probed or run and its summary line says it was not
  reviewed
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
belong to, and MUST contact the model resolved for that task by this
precedence: an explicit `models:` entry, then the packaged per-task default
(`DEFAULT_TASK_MODELS`, applied on the `ollama` backend only), then the global
`model:`. The packaged defaults are `gemma4:26b-a4b` for `adjudication`
(Identity) and `contradiction` (Contradictions) and nothing for the other
tasks (ADR-0047). An explicit
YAML null in `models:` MUST decline a packaged default and resolve to the
global `model:` — the operator's stated choice always wins over a shipped
one, and a packaged default that costs a large download MUST have an
opt-out that does not require restating the global tag. The packaged
defaults MUST NOT apply when `cfg.backend` is `openai-compatible`: a model
tag is meaningful only to its own provider, so those stages resolve the
global `model:` unless `models:` names them.
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

WHEN a named model is not installed (for the `ollama` backend) or not
available on the configured server (for the `openai-compatible` backend),
ONLY the stage that named it MUST fail, and its remediation MUST name that
model rather than the global default, worded per the configured backend —
an `ollama pull` command for `ollama`, or advice to make the model available
on the configured server for `openai-compatible`, with no `ollama pull`
reference in that case. Falling back to the global model MUST NOT happen:
the operator would keep writing relation types believing they came from the
model they named.

#### Scenario: A stage runs on its own task model

- GIVEN `openkos.yaml` sets `model: qwen3:8b` and `models.edge_typing:
  gemma2:27b`
- WHEN `curate` reaches the Structure stage
- THEN Structure contacts `gemma2:27b` and every other stage contacts
  `qwen3:8b`

#### Scenario: The packaged judge default applies without any config

- GIVEN `cfg.backend == "ollama"` and `openkos.yaml` has no `models:` key
- WHEN `curate` reaches the Identity and Contradictions stages
- THEN both contact `gemma4:26b-a4b` and every other stage contacts the
  global `model:`

#### Scenario: The packaged judge default does not apply on openai-compatible

- GIVEN `cfg.backend == "openai-compatible"` and `openkos.yaml` has no
  `models:` key
- WHEN `curate` reaches the Identity and Contradictions stages
- THEN both contact the global `model:`

#### Scenario: An explicit null declines the packaged default

- GIVEN `openkos.yaml` sets `models.contradiction: null`
- WHEN `curate` reaches the Contradictions stage
- THEN Contradictions contacts the global `model:`

#### Scenario: The cost gate discloses a non-default model

- GIVEN Structure resolves `gemma2:27b` while the global default is
  `qwen3:8b`
- WHEN Structure's cost gate asks for consent
- THEN the printed output names `gemma2:27b` alongside the unchanged
  `"{n} untyped edge(s) -> {n} LLM call(s)"` line

#### Scenario: No per-task model leaves gate output unchanged

- GIVEN a stage that resolves the global `model:` (a stage with no packaged
  default and no `models:` entry, or any stage on the `openai-compatible`
  backend with no `models:` entry)
- WHEN that stage's cost gate asks for consent
- THEN the printed output is byte-identical to the wording without per-task models

#### Scenario: A missing packaged judge fails only its own stage

- GIVEN `cfg.backend == "ollama"`, no `models:` key, and `gemma4:26b-a4b` is
  not installed
- WHEN `curate` runs
- THEN Identity and Contradictions each report unavailable with
  `ollama pull gemma4:26b-a4b`, never a fallback to the global `model:`, and
  Structure and Metadata still run

#### Scenario: A missing task model fails only its own stage

- GIVEN `cfg.backend == "ollama"` and `models.edge_typing` names a model
  that is not installed
- WHEN `curate` runs
- THEN Structure reports unavailable with an `ollama pull` remediation
  naming THAT model, and Metadata and Contradictions still run

#### Scenario: A missing task model on the openai-compatible backend fails only its own stage

- GIVEN `cfg.backend == "openai-compatible"` and `models.edge_typing` names
  a model that is not available on the configured server
- WHEN `curate` runs
- THEN Structure reports unavailable with a remediation naming THAT model
  and advising the operator to make it available on the configured server,
  with no `ollama pull` reference, and Metadata and Contradictions still run
### Requirement: `rationale_language` Pins The Language Of Suggested Rationales

The system MUST accept an optional `rationale_language` key in
`openkos.yaml`: a free-form language name that pins the language in which
the Metadata stage (`suggest-volatility`) and the Structure stage
(`suggest-relations`) write their per-item rationales. The key MUST be
absent-default `None`, and an explicit YAML null MUST behave as absent.
WHEN it is set, the system MUST append one language sentence to the SYSTEM
half of both rationale prompts, for the stages and for the standalone
`suggest-relations` and `suggest-volatility` verbs alike, so one workspace
never prints rationales in two languages depending on the verb. WHEN it is
unset, the assembled prompts MUST be byte-identical to the prompts assembled
without the key, and the rationale language is inherited per item from the
documents involved.

A present value MUST be a string that, after stripping surrounding
whitespace, is non-blank, single-line (no `\n` or `\r`), at most 40
characters, and free of the sentence-ending marks `.`, `!` and `?`; the
stripped value is what is used. The system MUST NOT validate the value
against a vocabulary of languages. A value that fails any of these checks,
including a non-string such as the YAML word `no`, which parses as a
boolean, MUST be refused when the config is read, with an error naming
`rationale_language` and the offending value, before any model call.

#### Scenario: A pinned language reaches both rationale prompts

- GIVEN `rationale_language: Spanish`
- WHEN the Structure and Metadata stages assemble their prompts
- THEN each SYSTEM prompt ends with the sentence `Write the "rationale" in
  Spanish.`, and nothing else in the prompt differs from the unpinned prompt

#### Scenario: An unset key sends the unpinned prompt

- GIVEN `openkos.yaml` has no `rationale_language` key
- WHEN either rationale prompt is assembled
- THEN the SYSTEM prompt is exactly the unpinned system text

#### Scenario: A sentence typed into the field is refused

- GIVEN `rationale_language: Write everything in Spanish.`
- WHEN the config is read
- THEN the read fails with an error naming `rationale_language` and the
  value, and no model is contacted

#### Scenario: A boolean-shaped value is refused

- GIVEN `rationale_language: no`
- WHEN the config is read
- THEN the read fails with an error naming `rationale_language`, rather than
  pinning a language called `False`

### Requirement: Availability Is Tracked Per Model, Not Per Run

An availability failure — `BackendUnavailable` or `BackendModelNotFound`,
raised by either backend — MUST skip only the later `needs_llm` stages that
resolve the SAME model. A stage resolving a different model MUST still be
attempted.

There is no run-scoped skip: one failed connection does not settle
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
refusal, consistent with other verbs.

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

- GIVEN any valid `relate` inputs
- WHEN `openkos relate` runs standalone
- THEN its output is unaffected by the helper `curate` shares with it

### Requirement: Identity Cost Line Discloses Truncation

`_identity_probe` MUST expose the SAME `produced`/`retained` truncation
signal `find_candidates` makes observable (`entity-resolution`:
Truncation Is Never Silent), through `StageProbe.notice` — the same
channel `_structure_probe` already uses for the Structure stage's
candidate-edge cap (`_structure_probe` in `cli/curate.py`). WHEN Identity's candidate-
group set is truncated (`produced > retained`), the printed notice MUST
disclose both counts, in a shape consistent with the existing
`"{retained} of {produced} ... shown (cap reached)"` pattern
(`candidate_truncation_notice` in `resolution/edge_typing.py`) substituting the group noun for the
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
by `cost_line` in `cli/curate.py`, from `probe.llm_calls`) MUST
remain unchanged: the candidate-group cap MUST NOT alter the cost-line
wording,
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

### Requirement: All Five Stages Run

`curate` MUST declare all five stages — Preconditions, Identity, Structure,
Metadata, Contradictions — in its runtime stage sequence, and each stage the
run asks for MUST compute its own queue when the loop reaches it. Structure is
the one stage a run does not ask for by default (see "The Structure Stage Is
Reviewed On Request"). A stage MUST NOT be skipped as unimplemented; an empty
queue or a declined cost gate is reported as that stage's outcome. The end-of-run summary MUST list an outcome for all five
stages.

#### Scenario: Every stage executes

- GIVEN a bundle with pending findings in every category
- WHEN `curate --structure` runs to completion
- THEN Preconditions, Identity, Structure, Metadata, and Contradictions each
  probe their own queue and execute, and the summary lists all five stages
  with a real outcome for each

### Requirement: Curate Reads And Resolves The Pending Queue

WHEN the pending-work queue exists, each `curate` stage MUST take the open
rows of its kind as its candidates before recomputing, MUST enqueue any
proposal it computes that has no open row, and MUST resolve each row it
presents: `applied` (as proposed or modified) when the item is written,
`declined` when the operator declines it through a surface that records the
decline. A row presented and neither written nor declined MUST return to
`pending`. The Contradictions stage stays report-only: it enqueues and
never applies.

#### Scenario: An accepted relation resolves its row

- GIVEN an open relation-type row
- WHEN the operator accepts it in the Structure stage
- THEN the relation is written and the row is `applied` as proposed
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
meaning), and `a` MUST NOT be offered on the Identity per-item prompt. Identity's
accept-recommended is a separate pre-pass question over the recommended set
only, not an accept-remaining answer. Before applying items without asking,
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
### Requirement: `--auto-merge` Applies The Measured Identity Class Without A Prompt

`curate` MUST accept a boolean `--auto-merge` flag, off by default and
meaningful only for the run it is passed to. When set and the run is
eligible (`identity-auto-merge`: Run Eligibility Is Checked Once And Every
Failure Is Reported), Identity MUST run an automatic pass before its
per-item walk that merges the groups meeting `identity-auto-merge`:
Per-Group Eligibility, without a per-item prompt. Every group the pass does
not merge MUST continue through the normal Identity flow.

`--auto-merge` MUST NOT imply `--auto`. Unattended use is `curate --auto
--auto-merge`. With `--auto-merge` alone the Identity cost gate MUST behave
as it does without the flag: it prompts on a TTY and, on a non-TTY, declines
before any model call, so the automatic pass makes no judgment and no merge.
The automatic pass MUST run only after the Identity cost gate has been
accepted.

`--auto-merge` together with `--reconcile` MUST be refused with exit 2,
naming both flags, before any workspace gate or read, with no write and no
model call.

On a non-TTY, `--auto-merge` MUST exempt only the automatic pass from the
Identity non-TTY write refusal. The interactive remainder MUST still be
declined on a non-TTY exactly as today, with the standalone-verb pointer, and
MUST NOT reach a prompt on a pipe.

#### Scenario: Unattended run merges an eligible in-class pair

- GIVEN stdin is not a TTY, an eligible run, and one in-class `same` group at
  confidence 0.95
- WHEN `curate --auto --auto-merge` runs
- THEN the pair is merged, the disclosure block names it with
  `openkos unmerge <survivor>`, and no prompt is printed

#### Scenario: `--auto-merge` without `--auto` still hits the cost gate on a TTY

- GIVEN a TTY and an in-class `same` group
- WHEN `curate --auto-merge` runs
- THEN the Identity cost line is printed and a confirmation is asked before
  any model call

#### Scenario: `--auto-merge` without `--auto` on a non-TTY spends nothing

- GIVEN stdin is not a TTY and an in-class `same` group
- WHEN `curate --auto-merge` runs without `--auto`
- THEN Identity declines before any model call, no group is merged, and no
  prompt is printed

#### Scenario: `--auto-merge` with `--reconcile` is refused

- GIVEN any workspace
- WHEN `curate --auto-merge --reconcile` runs
- THEN the exit code is 2, stderr names both flags, and nothing is read,
  written or sent to a model

#### Scenario: The non-TTY remainder is still declined

- GIVEN stdin is not a TTY, one eligible in-class group and one out-of-class
  `same` group
- WHEN `curate --auto --auto-merge` runs
- THEN the in-class group is merged, the out-of-class group is neither
  judged nor merged, and the stage notice counts it among the candidate
  groups left for review and points to running `openkos curate` on a terminal

#### Scenario: The flag does not carry over

- GIVEN a run that used `--auto-merge`
- WHEN `curate` runs again without the flag
- THEN no group is merged without a per-item answer

#### Scenario: `--accept identity` stays refused alongside the flag

- GIVEN any workspace
- WHEN `curate --auto-merge --accept identity` runs
- THEN the exit code is 2 and nothing is written

### Requirement: Identity Offers Accept-Recommended For In-Class Groups

When the run is eligible and, after the automatic pass (if any), at least one
remaining Identity group belongs to the recommended set
(`identity-auto-merge`: The Recommended Set For Accept-Recommended), Identity
MUST, before its per-item walk, offer one accept-recommended question on a
TTY. The offer MUST list every group of the set with its survivor, its
absorbed id and the undo command `openkos unmerge <survivor>`. If the
operator accepts, each listed group MUST be applied as an ordinary per-item
merge with its own commit (#800) and its own `log.md` bullet. If the operator
declines, every group MUST proceed to the per-item walk unchanged.

Accept-recommended MUST be a separate pre-pass question, not an answer on the
per-item prompt. It MUST NOT be offered when the set is empty, when the run
is ineligible, or on a non-TTY. Groups outside the set MUST keep their
per-item prompt whatever the operator answers. It MUST NOT be added to
`adjudicate --apply` or `adjudicate --apply-same`.

The offer MUST NOT depend on `--auto-merge`, but its set only holds verdicts
judged in this run, so without the flag a verdict served from the store or a
queue row is not offered and the cost line is unchanged. When the flag was not
passed, run eligibility MUST be established only after judging and only when
at least one fresh in-class `same` verdict exists; an ineligible run then
offers nothing and prints no ineligibility line, because no automatic pass was
requested.

Each listed group MUST be prepared immediately before its own write, because
every earlier merge changes what the next one reads. A bulk answer MUST NOT
cover a group whose stacked body crosses the stacked-body guardrail when it is
prepared (the rule `adjudicate --apply-same` applies): such a group MUST be
left unmerged, MUST be named on stderr as keeping its per-item prompt, and
MUST then be prompted individually in the per-item walk.

#### Scenario: The offer lists survivor, absorbed and undo per item

- GIVEN a TTY and two in-class groups judged `same`, at confidences 0.95 and
  0.60
- WHEN Identity reaches the accept-recommended offer
- THEN both groups are listed, each with survivor, absorbed and `openkos
  unmerge <survivor>`

#### Scenario: Accepting applies per-merge commits

- GIVEN the previous scenario and the operator accepts
- WHEN Identity applies the set
- THEN two merges are applied with two separate commits

#### Scenario: Declining falls through to per-item prompts

- GIVEN the same offer and the operator declines
- WHEN Identity continues
- THEN both groups are prompted individually and none is merged without `y`

#### Scenario: An out-of-set group keeps its prompt after accepting

- GIVEN an offer for one in-class group and a separate out-of-class `same`
  group, and the operator accepts
- WHEN Identity continues
- THEN the in-class group is merged and the out-of-class group is prompted
  individually

#### Scenario: No offer for an empty set

- GIVEN no remaining group is in the recommended set
- WHEN Identity runs
- THEN no accept-recommended question is printed

#### Scenario: No offer on a non-TTY

- GIVEN stdin is not a TTY and a group in the recommended set
- WHEN `curate --auto` runs without `--auto-merge`
- THEN no accept-recommended question is printed and no group is merged

#### Scenario: A guardrail-crossing group is listed but keeps its prompt

- GIVEN an offer for two in-class groups, one of which merges to a stacked
  body crossing the guardrail, and the operator accepts
- WHEN Identity applies the set
- THEN the other group is merged with its own commit
- AND the crossing group is not merged, is named on stderr as keeping its
  per-item prompt, and is prompted individually in the walk

#### Scenario: A served verdict is not offered without `--auto-merge`

- GIVEN a TTY, an in-class group whose `same` verdict is served from the
  store, and a second in-class group judged `same` in this run
- WHEN `curate --auto` runs without `--auto-merge`
- THEN only the group judged in this run is offered
- AND the cost line prices exactly the groups it priced before this change

#### Scenario: An ineligible run offers nothing and is silent without the flag

- GIVEN a TTY, a fresh in-class `same` group, and a run whose model digest
  differs from the measured digest
- WHEN `curate --auto` runs without `--auto-merge`
- THEN no accept-recommended question is printed
- AND no ineligibility line is printed
- AND the group is prompted individually in the walk

#### Scenario: Groups already merged by the pass are not offered again

- GIVEN `--auto-merge` merged one in-class group in this run
- WHEN Identity reaches the accept-recommended offer
- THEN the merged group is not listed

### Requirement: The Structure Stage Is Reviewed On Request

`curate` MUST NOT probe, compute or present the Structure stage unless the run
asks for it with `--structure` or names it in `--accept` (`--accept structure`
implies `--structure`). `review: false` and `--accept metadata` MUST NOT ask
for it. A run that does not ask for it MUST make no model call for Structure,
build no graph or candidate-edge queue for it, and print no cost line or
per-item prompt for it. A run that asks for it MUST behave exactly as the stage
did before it became opt-in.

The summary line for a stage the run did not present MUST say that it was not
reviewed this run and MUST name the command that reviews it. For Structure it
MUST state how many relation suggestions wait (the open `relation_type` rows
of the pending-work queue, excluding supersessions, which are decided with
`openkos relate <newer> supersedes <older>`) and name
`openkos curate --structure`; when none wait it MUST say so and say the same
command computes and reviews them. The count MUST be read from the pending-work
queue without a model call.

The suggestions MUST remain computed and kept as `relation_type` pending rows by
the surfaces that already do so (the unattended maintenance pass, `suggest-relations`,
and `curate --structure`); this requirement changes only whether `curate`
presents them.

#### Scenario: A plain curate does not compute or present Structure

- GIVEN a bundle with candidate untyped edges and a counting model backend
- WHEN `curate` runs with no Structure flag
- THEN no per-item relation prompt and no Structure cost line appear
- AND the suggester, the candidate-edge walk and the backend's `chat` are
  never called

#### Scenario: The summary says how many suggestions wait

- GIVEN two open relation suggestions and one open supersession in the queue
- WHEN `curate` runs with no Structure flag
- THEN the summary has the line `Structure: not reviewed this run -- 2 relation
  suggestion(s) waiting; review them with `openkos curate --structure`.`

#### Scenario: Nothing waiting still names the opt-in

- GIVEN no open relation suggestion
- WHEN `curate` runs with no Structure flag
- THEN the Structure summary line says no relation suggestions are waiting and
  that `openkos curate --structure` computes and reviews them

#### Scenario: `--structure` presents the stage exactly as before

- GIVEN two candidate untyped edges
- WHEN `curate --structure` runs
- THEN the cost line `2 untyped edge(s) -> 2 LLM call(s)` and the per-item
  relation prompts appear and accepted suggestions are written through the
  relate core

#### Scenario: `--accept structure` implies the opt-in

- GIVEN two candidate untyped edges
- WHEN `curate --accept structure` runs
- THEN Structure runs and applies its symmetric suggestions without a per-item
  prompt

