# Delta for Curate Command

## ADDED Requirements

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

## MODIFIED Requirements

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
