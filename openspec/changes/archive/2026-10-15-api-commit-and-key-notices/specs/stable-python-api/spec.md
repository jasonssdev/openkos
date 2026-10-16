# Delta for stable-python-api

## MODIFIED Requirements

### Requirement: Open Decisions Can Be Listed, Previewed And Applied

The API MUST expose operations to list open decisions, to preview one decision,
and to apply one decision, over the same services the `curate` decision
surface uses. Listing and preview MUST write nothing, take no lock, build no
model client and make no model call. Applying MUST act on one named decision per
call, taking the version token a preview returned, and MUST NOT apply any other
item. A decision that is no longer open, a key that no queue row ever carried, or a
decision whose inputs changed since the preview, MUST NOT be applied: the apply and preview operations MUST return the
shipped `Stale` or `NotApplied` outcome as a value (they do not raise it), so a
caller cannot forget to handle it. A key that no queue row ever carried MUST be
reported with the `Stale` reason `unknown_key`, distinct from `row_resolved`,
which means a row carrying the key was settled or retired. A successful apply returns `Applied` and
MUST record the resolution the same way the corresponding human-facing path
does (a row resolves as `applied`) and, as the CLI does, attempt a local git
auto-commit of the paths it wrote; when no commit can be made (not a
repository, identity unset, failure) the apply MUST still succeed with no commit
identity and MUST report the skipped commit through the caller's optional
warning callback. A commit MUST contain only the paths the decision wrote
(a pre-staged unrelated file or an unrelated dirty edit of `openkos.yaml` is
never swept in). `Applied` MUST say, as returned data, whether the commit it
records holds a concept explicitly marked confidential, so a caller can show the
notice the CLI prints; it is false when no commit landed. Applying takes the workspace lock only inside its commit
section, waiting at most the caller's `wait_seconds` (default zero) and raising
the existing workspace-busy error beyond it.

In v1, preview and apply MUST NOT reconcile merged concept bodies with a model
and MUST NOT refresh the derived indexes; `status` reports the indexes as stale
and a later maintenance pass or reindex refreshes them. This is a documented
limitation, not a guarantee of equivalence with `curate`'s merge output.

The decision payload is an unversioned stored mapping and MUST NOT be exposed.
Listing MUST return API-owned values whose kind-specific details (identity,
relation, tier) are typed fields built from the stored payload by reader
functions that live beside the payload's writer; the adjudicator's confidence
MUST NOT be exposed. A payload that cannot be read into its typed detail MUST
yield a listed decision with no detail rather than hiding it. Previews MUST be
API-owned per-action plans holding only identifiers, types and flags.

#### Scenario: Listing shows open decisions

- GIVEN a queue with two open decisions
- WHEN the list operation is called
- THEN both are returned and nothing is written

#### Scenario: Preview does not change anything

- GIVEN an open decision
- WHEN it is previewed
- THEN the bundle and the queue are byte-identical afterwards

#### Scenario: Apply acts on one decision

- GIVEN two open decisions
- WHEN one is applied
- THEN that one is `applied` and the other is still open

#### Scenario: A stale decision is returned as a value

- GIVEN a previewed decision whose member was edited afterwards
- WHEN it is applied with the preview's version
- THEN `Stale` is returned, nothing is raised, and the bundle is unchanged

#### Scenario: A malformed payload is still listed

- GIVEN an open row whose stored payload cannot be read into a typed detail
- WHEN the list operation is called
- THEN the decision is listed with no detail

#### Scenario: A commit that cannot be made does not fail the apply

- GIVEN a workspace that is not a git repository
- WHEN a decision is applied
- THEN the result is `Applied` with no commit identity and the warning callback
  is called once

#### Scenario: Apply does not sweep unrelated changes into the commit

- GIVEN an unrelated staged file in the repository
- WHEN a decision is applied
- THEN the commit contains only the decision's paths and the staged file stays
  staged and uncommitted

#### Scenario: A held lock surfaces as busy

- GIVEN the workspace lock held by another process and `wait_seconds` of zero
- WHEN a decision is applied
- THEN the workspace-busy error is raised and nothing is written

#### Scenario: A key that never existed is distinguishable from a resolved decision

- GIVEN a decision key that no queue row ever carried, and a decision that was
  already applied
- WHEN each is previewed or applied
- THEN the first is `Stale` with reason `unknown_key` and the second is `Stale`
  with reason `row_resolved`, and nothing is written

#### Scenario: A commit holding a confidential concept says so

- GIVEN a decision whose commit holds a concept marked `sensitivity: confidential`
- WHEN the decision is applied
- THEN `Applied` reports a confidential commit, and a commit holding no such
  concept, or no commit at all, reports none
