# Delta for Entity Resolution Adjudication

## MODIFIED Requirements

### Requirement: Persisted Verdicts Are Served Before Re-Judging

`openkos adjudicate` MUST persist every freshly judged verdict to the
`adjudications` tables of `.openkos/findings.db`
(`state.adjudications`, the findings store's second tenant, so `purge`'s
wholesale deletion and `forget`'s sweep cover it with no new privacy
surface), alongside one content-hash digest per member computed at
persist time. A later run MUST serve a candidate group from the store,
with NO model call for it, exactly when: the latest persisted row for the
group's member set matches the run's EFFECTIVE confidential inclusion
(`--include-confidential` OR the verified local-backend exemption -- the
same disjunction `sensitivity.should_block` applies, so the partition
runs only after the exemption is resolved; a verdict computed over a
different member subset must never serve), was computed under the
CURRENT judgment rubric (the row's stored `rubric_digest`
equals `resolution.adjudication.rubric_digest()`, a fingerprint over the
adjudication system prompt plus the deterministic post-parse withdrawal
rule's defining data, so the prompt and the rule cannot drift apart; a
row with no stored digest, written before the column existed, is never
servable, because a verdict from an unknown rubric is not a verdict this
build would produce), carries a digest row for
EVERY current group member and no others, every stored digest equals the
member's CURRENT content hash, and the stored verdict is in the
vocabulary. The `rubric_digest` column MUST be added to a store a
build without the column created by a real migration at the one place the tables
are created, and the read path MUST tolerate the pre-migration shape
(reporting those rows' rubric as unknown) rather than degrading the
whole store to a failed read.
Everything else re-judges, conservatively -- including a
present-but-corrupt store, which degrades to one stderr advisory and a
full fresh judge, and a persist failure, which costs one advisory, never
the run. A result any of whose members has no current digest MUST NOT be
persisted (a row whose staleness can never be checked would serve
forever -- this also keeps the no-readable-member UNCERTAIN
short-circuit out of the store).

The one exception to serving is `curate`'s automatic Identity pass
(`identity-auto-merge`): it MUST judge its in-class groups fresh in the run
with the measured model and MUST NOT act on a served verdict, because a
persisted row records neither the model that produced it nor its digest. The
exception changes only what the pass acts on; every other caller of the store
serves as above, and the store's schema is unchanged.

The run MUST report the split on stderr
(`N of M candidate group(s) served from persisted adjudications; K judged
fresh.`), mirroring `contradictions`' line, and a `--fresh` flag MUST
bypass the serve and re-persist, mirroring `contradictions --fresh`.
The rubric-driven re-spend MUST be announced, not silent: when at least one group re-judges only because its row
predates the current rubric (mismatched or absent digest), one stderr
line names the rubric as the cause -- a rubric change re-judging every
cached group would otherwise surface as a sudden `0 of N served` with no
stated reason. The line MUST be absent on the healthy path and on
ordinary member drift.
Served and fresh verdicts MUST render identically through every output
mode, in candidate order — and that identity is what obliges a served
verdict to pass through the SAME reply-only judgment a fresh one does
(see Requirement: A `SAME` Verdict Whose Rationale Argues For Two
Occurrences Is Withdrawn). A row written before that withdrawal existed
still carries the verdict its own rationale argues against; re-deciding it
on read costs no model call, because the rule is pure and reads only the
rationale the row already stores. Writes stay confined to derived state under
`.openkos/` -- the bundle remains untouched on a read-only run.
(Previously: every caller served a matching persisted verdict with no model
call; there was no caller exempt from serving.)

#### Scenario: A repeat run on an unchanged bundle costs zero model calls

- GIVEN a bundle adjudicated once, unchanged since
- WHEN `openkos adjudicate` runs again
- THEN every group is served from the store, the model receives zero
  groups, and the split line reports `N of N ... 0 judged fresh`

#### Scenario: Member drift re-judges

- GIVEN a persisted verdict and a member edited since
- WHEN `openkos adjudicate` runs
- THEN that group is judged fresh and the new verdict re-persisted

#### Scenario: An effective-inclusion mismatch never serves

- GIVEN a verdict persisted from a run whose EFFECTIVE inclusion was
  exclusive (no flag, local exemption disabled)
- WHEN `openkos adjudicate --include-confidential` runs on the unchanged
  bundle
- THEN the group is judged fresh

#### Scenario: The store stays bounded by the live group set

- GIVEN a group re-judged (drift or `--fresh`)
- WHEN the fresh verdict is persisted
- THEN the group's superseded rows are replaced, not accumulated

#### Scenario: A rubric change re-judges and names the reason

- GIVEN a persisted verdict whose stored `rubric_digest` differs from the
  current build's
- WHEN `openkos adjudicate` runs on the unchanged bundle
- THEN the group is judged fresh, AND stderr carries one line naming the
  judgment rubric as the cause of the re-spend

#### Scenario: A pre-rubric row never serves

- GIVEN a persisted verdict row carrying no `rubric_digest` (written
  before the column existed)
- WHEN `openkos adjudicate` runs on the unchanged bundle
- THEN the group is judged fresh under the same announced reason

#### Scenario: The rubric line is absent when nothing was rubric-stale

- GIVEN a fully served repeat run, or a re-judge caused only by member
  drift
- WHEN `openkos adjudicate` runs
- THEN stderr carries no judgment-rubric line

#### Scenario: The automatic pass ignores a servable verdict

- GIVEN an in-class group with a persisted verdict that `adjudicate` would
  serve with no model call, and an eligible `curate --auto-merge` run
- WHEN the automatic pass decides on that group
- THEN the group is judged by the model in this run and the persisted
  verdict is not the basis for the merge decision

#### Scenario: `adjudicate` still serves after the exception

- GIVEN the same persisted verdict
- WHEN plain `openkos adjudicate` runs on the unchanged bundle
- THEN the group is served from the store with no model call
