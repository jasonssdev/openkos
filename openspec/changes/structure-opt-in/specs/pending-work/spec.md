# Delta for Pending Work

## MODIFIED Requirements

### Requirement: `openkos pending` Lists The Queue Read-Only

`openkos pending` MUST list open rows grouped by kind, each with its target
ids and the command that resolves it, and MUST NOT render a row's payload
(proposal text can carry a person's words); it applies no confidentiality
gate, because it runs in the owner's own terminal and shows only ids,
kinds and commands. The list is followed by the most recent
unattended job outcomes that need attention (`budget_exhausted`,
`timed_out`, `commit_failed`, `failed`). `--all` MUST also list `applied`,
`declined`, and `stale` rows. It MUST take no lock, make no model call, and
write nothing. When the queue is absent it MUST say so and point at
`openkos daemon --once`, never report the base as having nothing pending.
Each row MUST name a subject and a resolving command that can actually
resolve THAT row: a volatility row (about a concept type, so it has no
target) names its type; a watch refusal's command names the refused file, not
a placeholder; an identity row names the merge walk when its group was judged
the same, and otherwise the judgment walk (`adjudicate --apply`, whose prompt
answers are `y` merge, `s` skip and `d` keep-distinct; for a group of more than
two members, which that walk does not merge, it says the walk prints the
pairwise `merge` commands) with the `duplicates --keep-distinct` ruling over
its members as an alternative on its own line, since `duplicates` alone only
lists groups. The only payload fields read for
this are a type name, an inbox path and an adjudication verdict -- names and
a verdict, never proposal prose.

When relation suggestions wait (open `relation_type` rows other than
supersessions), `openkos pending` MUST say so on the line under its heading,
with the count and `openkos curate --structure`, the command that reviews
them, because `curate` does not present its Structure stage by default. A
relation row's resolving command MUST be that command, except a supersession's,
which is `openkos relate <newer> supersedes <older>`. The only extra payload
field read for this is a relation row's suggested type, to tell a supersession
from a suggestion.

The identity and relation-type advisors keep at most a fixed number of
candidates per run, and the queue records no truncation. A kind whose open
rows reach that cap MUST be listed with a note that the cap is the per-run
candidate cap, so more may exist, naming the verb that reports the total
(`duplicates`, `suggest-relations`); a kind under the cap carries no note.

#### Scenario: An absent queue is not an empty queue

- GIVEN a workspace with no queue table
- WHEN `openkos pending` runs
- THEN it says the queue has not been computed, names how to compute it,
  and does not say nothing is pending

#### Scenario: Waiting relation suggestions are counted and the command named

- GIVEN one open relation suggestion and one open supersession
- WHEN `openkos pending` runs
- THEN the line under the heading reads `1 relation suggestion(s) waiting --
  review them with `openkos curate --structure`.`
- AND the suggestion's resolving command is `openkos curate --structure`
