# Delta for stable-python-api

## ADDED Requirements

### Requirement: Contradictions And Revisions Are Previewed As Findings And Applied As Decisions

`Decision` MUST gain the field `finding`, defaulting to `None`, holding the typed
detail of a finding row: `ContradictionDetail` (`first_id`, `second_id`,
`merged_absorbed_id`, `rationale`, `conflicting_claims`) for a contradiction,
`RevisionDetail` (`first_id`, `second_id`, `change` `"reverses"` or `"refines"`,
`later_id`, `None` when the dates do not order the pair, `rationale`,
`first_quote`, `second_quote`, `first_date`, `second_date`) for a revision, and
`RefusedFileDetail` (`file_name`, the path relative to the inbox, and `reason`
`"changed_after_import"` or `"exceeds_pass_budget"`) for a refused file. `first_id`
and `second_id` are the decision's first two targets. The model's confidence MUST
NOT be exposed. A payload that cannot be read into its detail MUST leave
`finding` `None`, and the decision MUST still be listed.

`openkos.api` MUST export `preview_finding(workspace, decision_key, action)`,
returning a `FindingPreview` (`decision_key`, `action`, `plan`, `version`), or
the `NotApplied` and `Stale` values `preview_decision` returns for the same
cases. Its `plan` MUST be a `ReconcilePlan` (`first_id`, `second_id`,
`relation_type` `"reconciled_with"`, `"supersedes"` or `"revises"`, `holder_id`
and `target_id`, both `None` for `reconciled_with`, `deprecates_id`, the page a
`supersedes` lists as deprecated, `pages_changed`, and `kind` `"reconcile"`) for
reconcile, supersede, supersede reversed, revise and revise reversed, or a
`DeclinePlan` (`first_id`, `second_id`, `merged_absorbed_id`, `kind`
`"decline"`) for decline. Its `version` MUST be a `DecisionVersion`, and
`apply_decision` MUST apply it with the same contract as any decision: the lock
only inside the commit section, a commit of only the paths written, `Applied`,
`NotApplied` or `Stale` as values, and the `WriteRefused` family raised for a
check that fails after the preview.

`preview_decision` given a finding action, and `preview_finding` given any other
action the decision offers, MUST return `NotApplied` with the reason
`wrong_preview` and write nothing. The preview MUST take no lock, build no
client, make no model call and write nothing.

#### Scenario: A contradiction lists its detail and its actions

- GIVEN an open typed-edge contradiction over A and B
- WHEN `list_decisions` is called
- THEN the decision's `finding` is a `ContradictionDetail` naming A, B and the
  rationale, its `detail` is `None`, and its actions include decline and
  reconcile

#### Scenario: A finding is previewed and then applied as a decision

- GIVEN an open typed-edge contradiction over A and B
- WHEN `preview_finding` is called with reconcile and `apply_decision` with the
  returned version and reconcile
- THEN the preview's plan is a `ReconcilePlan` with `relation_type`
  `"reconciled_with"`, the result is `Applied` with a commit holding A, B and
  `log.md`, and the row is `applied`

#### Scenario: The decision preview points at the finding preview

- GIVEN an open contradiction
- WHEN `preview_decision` is called with decline
- THEN `NotApplied` with the reason `wrong_preview` is returned and nothing is
  written

#### Scenario: A refused file says why

- GIVEN an open watch_refusal row refused because its name was taken by
  different bytes
- WHEN `list_decisions` is called
- THEN its `finding` is a `RefusedFileDetail` with the inbox file name and the
  reason `"changed_after_import"`, and it carries no action

### Requirement: Findings Extend v1 Additively

The finding surface MUST be additive within `API_VERSION` 1 under the
classifier: new names (`preview_finding`, `FindingPreview`, `ReconcilePlan`,
`DeclinePlan`, `ContradictionDetail`, `RevisionDetail`, `RefusedFileDetail`),
new `Action` members (`decline`, `reconcile`, `supersede`,
`supersede_reversed`, `revise`, `revise_reversed`), a new defaulted field on
`Decision`, and widened `Literal`s (`NotApplied.reason` gains
`already_reconciled` and `wrong_preview`, `WithheldAction.reason` gains
`merged_content`). The `DecisionPreview.plan` union, the `Decision.detail` union
and `ApplyOutcome` MUST stay unchanged, and `API_VERSION` MUST stay 1.

#### Scenario: The decision unions are unchanged

- GIVEN the public surface after this change
- WHEN `DecisionPreview.plan`, `Decision.detail` and `ApplyOutcome` are read
- THEN each holds exactly the members it held before, and `API_VERSION` is 1
