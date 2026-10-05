# Delta for Job Runtime

The digest requirement itself ships in the in-flight `unattended-digest` change
and is not yet in the living spec, so this delta ADDS a requirement beside it
rather than modifying one.

## ADDED Requirements

### Requirement: The Digest States How Many Relation Suggestions Wait

A daemon pass that prints the what-changed digest MUST end it with one more
stdout line when relation suggestions wait in the pending-work queue (open
`relation_type` rows other than supersessions): `openkos daemon: N relation
suggestion(s) waiting -- review them with `openkos curate --structure`.` It MUST
print no such line when none wait, and a pass that prints no digest prints no
such line either. The count is read from the queue without a model call and
without a lock.

#### Scenario: The digest names the waiting suggestions

- GIVEN two open relation suggestions and one open supersession, and a pass that
  made one automatic commit
- WHEN the pass ends
- THEN the digest is followed by `openkos daemon: 2 relation suggestion(s)
  waiting -- review them with `openkos curate --structure`.`

#### Scenario: Nothing waits, nothing is said

- GIVEN no open relation suggestion and a pass that made one automatic commit
- WHEN the pass ends
- THEN the digest carries no relation-suggestion line
