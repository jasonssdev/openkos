# Delta for desktop-client

## ADDED Requirements

### Requirement: The Client Quotes And Runs Adding Files

The client MUST offer `quoteIngest(body)` (`POST /v1/ingest/quote`),
`quoteRefusedFile(decisionKey, body)` (`POST /v1/decisions/{key}/ingest-quote`,
the key path-encoded),
`startIngest(body)` (`POST /v1/ingest/runs`), `ingestRun(runId)`
(`GET /v1/ingest/runs/{run_id}`, the id path-encoded) and `stopIngest(runId)`
(`POST /v1/ingest/runs/{run_id}/stop`), typed from the generated schema, each
raising the client's typed error for a non-2xx response. Its error codes MUST
include `ingest_active` and `ingest_refused`. The client MUST NOT poll on its
own, and MUST send the quote's token back unchanged.

#### Scenario: A quote posts the chosen paths

- GIVEN a client over a fake fetch
- WHEN `quoteIngest({ paths: ["/a/notes.md"] })` is called
- THEN one `POST /v1/ingest/quote` is sent with that JSON body

#### Scenario: An ingest run is polled by id

- GIVEN a client over a fake fetch
- WHEN `ingestRun("r1")` is called
- THEN one `GET /v1/ingest/runs/r1` is sent

#### Scenario: An active ingest is a typed error

- GIVEN the engine answers 409 `ingest_active`
- WHEN `startIngest` is called
- THEN it rejects with the typed error carrying code `ingest_active` and
  `retryable` true
