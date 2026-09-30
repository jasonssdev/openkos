# Delta for Entity Resolution Adjudication

> **Conditional delta.** Built ONLY if the pre-registered decision rule in
> `auto-merge-safe-class/design.md` passes. On FAIL this file is deleted
> from the change before archive.

## ADDED Requirements

### Requirement: Persisted Verdicts Record The Adjudicating Model

Every freshly judged verdict persisted to the `adjudications` table MUST
record the name of the model that produced it in a nullable `model` column.
A store created before this column existed MUST be migrated in place the
way `rubric_digest` was added (#838): existing rows read the column as
`NULL`, and a read-only path over an unmigrated store MUST NOT raise.

The recorded model MUST NOT change which verdicts are served (the serving
rule is unchanged); it is read only by consumers that must know which model
a verdict came from. A `NULL` model MUST be treated by such consumers as
"unknown model", never as a match for any model.

#### Scenario: A fresh verdict records its model

- GIVEN adjudication runs with task model `qwen3:8b`
- WHEN a group is judged and persisted
- THEN its row's `model` is `qwen3:8b`

#### Scenario: An old store reads as unknown model

- GIVEN a `findings.db` created before the `model` column existed
- WHEN verdicts are read from it
- THEN every row's model reads as `NULL`, no error is raised, and serving
  behaves exactly as before
