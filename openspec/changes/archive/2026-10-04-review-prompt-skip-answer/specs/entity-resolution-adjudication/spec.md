# Delta for Entity Resolution Adjudication

## MODIFIED Requirements

### Requirement: Survivor/Absorbed Preview And Prompt

For each eligible group whose two members are a base/`-N` family (the same
directory, one id being the other plus `-` and decimal digits -- the
ingest-time collision suffix), the survivor MUST be the un-suffixed id
whatever the bodies weigh, and the preview MUST state
`survivor: <id> (canonical id (base of a -N family))`: the Concept ID is
the entity's identity under OKF, so a disambiguator must not become
permanent. The absorbed body is not lost -- the merge stacks and
reconciles both. For every other group, the survivor MUST be the member
with the RICHER BODY (longer stripped body text), falling back to `member_ids[0]`
(ascending id) only on an exact tie (string order alone made a
bilingual pleonasm the permanent Concept ID purely because `f` sorts
before `o`; the richer-body rule mirrors the extraction union's own
twin-drop precedent). An unreadable member measures below every readable
one and never survives on this rule. The criterion that DECIDED MUST be
stated in the preview (`survivor: <id> (richer body)` or `survivor: <id>
(id order -- equal body length)`) -- an arbitrary-looking choice with no
stated criterion is the defect, not the determinism. Before prompting, a
preview of what `prepare_merge` would fuse (survivor, absorbed, rewrites,
removed) MUST be printed. The prompt text MUST be exactly
`Merge <absorbed> into <survivor>? [y]es / [s]kip / [d]istinct (d records a
permanent keep-distinct ruling)` -- the same prompt `curate`'s Identity stage
asks. The prompt MUST say what `d` records, because `d` is the only answer
that persists anything.

The same ordering rule and criterion disclosure apply to EVERY walk that
drives `_prepare_one_merge`: `--apply`, `--apply-same`, and `curate`'s
Identity stage. Each walk MUST compute the ordering ONCE, display it, and
PIN that exact `(survivor, absorbed)` pair through the prepare-and-write
that follows -- `--apply-same`'s Pass 2 in particular MUST apply the
direction Pass 1 previewed and the typed count consented to, never a live
recomputation: an earlier merge in the same batch can enrich a shared
member enough to flip a recomputed ordering, and the operator would then
get a direction they never saw. A structural consequence, deliberate: with the smaller
body always absorbed into the larger, a 2-member batch merge cannot
reach the 80% stacked-share domination guardrail -- the hazard
that guardrail refuses is prevented by construction (the guardrail
itself remains as defense in depth).

#### Scenario: Preview precedes the exact prompt text

- GIVEN an eligible SAME 2-member group
- WHEN `adjudicate --apply` runs
- THEN a `prepare_merge` preview is printed before the prompt
- AND the prompt line is exactly `Merge <absorbed> into <survivor>? [y]es /
  [s]kip / [d]istinct (d records a permanent keep-distinct ruling)`
  with `<survivor>` = the richer-body member and `<absorbed>` the other

#### Scenario: The richer body survives regardless of id order

- GIVEN a SAME 2-member group whose alphabetically-later member carries
  the longer body
- WHEN any apply walk previews it
- THEN that member is the survivor and the preview states `richer body`

#### Scenario: A base/-N family keeps the canonical id
- GIVEN a SAME 2-member group `people/ana` and `people/ana-2`, where
  `people/ana-2` has the longer body
- WHEN any apply walk previews it
- THEN `people/ana` is the survivor and the preview states the
  canonical-id criterion

#### Scenario: A tie keeps ascending-id order and says so

- GIVEN a SAME 2-member group whose members' stripped bodies have equal
  length
- WHEN any apply walk previews it
- THEN `member_ids[0]` is the survivor and the preview states the id-order
  tiebreak

### Requirement: Prompt Response Semantics

The prompt MUST be validated by the same helper `curate`'s Identity stage
uses, with three answers, matched case- and whitespace-insensitively:

- `y`/`yes` MUST apply the merge.
- `s`/`skip`, `n`/`no` and empty input (the documented default, `Enter =
  skip`) MUST skip the pair: nothing is written and no keep-distinct ruling
  is recorded, so the pair stays pending and is offered again on the next
  run. `n` is accepted as a synonym of `skip` and MUST NOT record a ruling.
- `d`/`distinct` MUST record the permanent keep-distinct ruling for the
  group (its own commit, `openkos: keep distinct <ids>`) and continue to the
  next group. It is the only answer that persists a ruling.

Any OTHER answer MUST be re-asked with a one-line notice naming the accepted
tokens, never silently counted as a skip or a ruling.

#### Scenario: `y` applies the merge

- GIVEN an eligible group and CliRunner `input="y\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is applied

#### Scenario: empty input skips and records nothing

- GIVEN an eligible group and CliRunner `input="\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied, the run continues, and no keep-distinct
  ruling is recorded

#### Scenario: `n` is a synonym of skip, not a ruling

- GIVEN an eligible group and CliRunner `input="n\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied, no keep-distinct ruling or commit is
  written, and the group is offered again by the next `adjudicate --apply`

#### Scenario: `s` skips

- GIVEN an eligible group and CliRunner `input="s\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied and no keep-distinct ruling is recorded

#### Scenario: `d` records the keep-distinct ruling

- GIVEN an eligible group and CliRunner `input="d\n"`
- WHEN `adjudicate --apply` runs
- THEN the merge is NOT applied, the keep-distinct ruling is recorded with
  its own commit, and the next run no longer offers the group

#### Scenario: an unrecognized answer is re-asked, not counted as a skip

- GIVEN an eligible group and CliRunner `input="maybe\ny\n"`
- WHEN `adjudicate --apply` runs
- THEN a notice naming the accepted tokens is printed
- AND the merge is applied by the subsequent `y`

### Requirement: End-Of-Run Summary With Breakdown

At the end of the run, `adjudicate --apply` MUST print a summary line
`applied X, skipped Y` where `Y` breaks down into N>2 skips, already-merged
skips, cross-type skips, operator skips (`s`, `n`, empty -- reported as
`left pending: N` only when N is non-zero) and declined (`d`) prompts.
After the summary line, each merge answered `d` MUST be named on its own
`  declined: <absorbed> -> <survivor>` line — two-space indented, exactly as
the implementation emits it (mirroring `curate`'s decline listing) —
and no such line may appear for a merge that was applied.

#### Scenario: Summary reflects applied and skipped counts

- GIVEN a run with one applied merge, one N>2 skip, and one `d` answer
- WHEN `adjudicate --apply` completes
- THEN stdout shows `applied 1, skipped 2` with the breakdown of skip reasons

#### Scenario: Declined merges are named after the summary

- GIVEN a run with one applied merge and one merge answered `d`
- WHEN `adjudicate --apply` completes
- THEN the declined pair is named `declined: <absorbed> -> <survivor>` after
  the summary line
- AND the applied pair is not listed as declined

#### Scenario: Operator skips are counted but never named as declined

- GIVEN a run with one merge answered `s`
- WHEN `adjudicate --apply` completes
- THEN the summary counts it as skipped and reports `left pending: 1`
- AND no `declined:` line names the pair
