# Delta for curate-decisions

## MODIFIED Requirements

### Requirement: Apply Is A Two-Phase Preview And Commit Against A Version Token

Applying a write action MUST be two phases. The preview phase MUST return,
without writing, what the action would do, plus a version token for the item.
The commit phase MUST take the token the caller holds and MUST re-validate it before any write: the content half against a re-preview from current bytes before the commit section opens (a mismatch is the typed Stale result `content_changed`), and the row half re-read inside the commit section, where the workspace lock is held (`row_resolved`, `row_superseded`, `row_inputs_changed`). A change that lands between the two is raised as drift, not returned as Stale. When a reconciliation is planned, a row-level Stale can be returned after the reconciliation port has run.
The token MUST be derived from the item's payload digest plus its input
digests; for a volatility item, which carries no input digests, it MUST cover the proposed tier and the bytes of `openkos.yaml`, which determine the currently effective tier, so any configuration change makes it Stale; inside the commit section the row's recorded tier is also compared with the effective one. A caller MUST be able to treat
the token as opaque. No lock MUST be held between the two phases.

If the token no longer matches, the commit MUST return the typed Stale result
and MUST write nothing. A caller that holds no state other than the token and
the `decision_key` MUST be able to complete a commit: every other input the
re-preview needs MUST come from the open row's payload or from a default policy
the caller MAY override, never from state the caller must remember. A token
whose payload digest is absent, because no queue row backs the item, MUST be
rejected as Stale on this path.

A decision key that no queue row of any status ever carried MUST be reported as
the typed Stale result `unknown_key`, in both the preview and the commit phase;
`row_resolved` is reserved for a key whose row was settled or retired. When the
queue exists but cannot be read, the key MUST NOT be reported as never having
existed.

#### Scenario: A current token commits

- GIVEN a preview of an identity merge and no change since
- WHEN the client commits with the previewed token
- THEN the merge is written and the row is resolved

#### Scenario: A superseded token returns Stale and writes nothing

- GIVEN a preview of an identity merge, and then a member concept edited
- WHEN the client commits with the previewed token
- THEN the typed Stale result is returned, no concept or queue row changes,
  and no commit is made

#### Scenario: A volatility item goes stale when the effective tier changes

- GIVEN a preview of a tier change and then the effective tier changed
  elsewhere
- WHEN the client commits with the previewed token
- THEN the typed Stale result is returned and `openkos.yaml` is unchanged

#### Scenario: Preview writes nothing

- GIVEN any write action
- WHEN the client previews it
- THEN no bundle file, sidecar, queue row or configuration changes

#### Scenario: A key that never existed is Stale as unknown

- GIVEN a decision key that no queue row ever carried
- WHEN the client previews or commits it
- THEN the typed Stale result `unknown_key` is returned and nothing is written
