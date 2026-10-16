# Delta for judging-application-service

## MODIFIED Requirements

### Requirement: A Quote Carries Its Notices As Data

A quote MUST carry as returned data: a truncation notice when the candidate set was
capped (a count for identity, rendered `text` for `relation_type`), a quarantine
notice for `relation_type` when edges were quarantined (identity has no quarantine), a model notice naming
the model and backend the run would use, an insecure-key notice when an `openai-compatible` API key would be sent over plain
HTTP to a non-local host (the advisory the CLI prints before it builds a client,
naming the host and never the key), and an unattended notice when the
unattended engine has recent job activity recorded in the workspace's job records,
meaning a job in progress or one that started within a recent window (the window is
a design detail). The job records MUST be read without being created, so a workspace
that never ran the engine has no records and gets no notice. The unattended and insecure-key notices
MUST be informational only: it MUST NOT block, change the price or alter the token.
No notice MUST be printed.

#### Scenario: Notices are returned, not printed

- GIVEN a candidate set that was capped
- WHEN a client requests a quote
- THEN the quote carries the truncation notice as data and nothing is written to a
  stream

#### Scenario: The unattended notice appears only when it applies

- GIVEN one workspace whose job records show recent unattended activity and one
  with no job records
- WHEN a client requests a quote for each
- THEN only the first quote carries the unattended notice, both quotes have the
  same price for the same work, and no job record is created for the second

#### Scenario: The insecure-key notice is returned before consent

- GIVEN an `openai-compatible` backend at a non-local `http://` host and an API key
  in the environment
- WHEN a client requests a quote
- THEN the quote carries the insecure-key notice naming the host, a quote for an
  `https://` host or with no key carries none, and the token is unchanged
