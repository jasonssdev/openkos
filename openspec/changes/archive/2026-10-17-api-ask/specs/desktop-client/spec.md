# Delta for desktop-client

## ADDED Requirements

### Requirement: The Client Starts And Polls An Ask

The client MUST offer `ask(body)`, which posts the body to `/v1/ask` and
resolves to the ask job, and `askJob(askId)`, which gets `/v1/ask/{ask_id}`
with the id path-encoded and resolves to the job. Both MUST be typed from the
generated schema and MUST raise the client's typed error for a non-2xx
response, as every other method does. The client MUST NOT poll on its own.

#### Scenario: Ask posts the question

- GIVEN a client over a fake fetch
- WHEN `ask({ question: "q" })` is called
- THEN one `POST /v1/ask` is sent with the JSON body `{"question": "q"}`

#### Scenario: A job is polled by id

- GIVEN a client over a fake fetch
- WHEN `askJob("a1")` is called
- THEN one `GET /v1/ask/a1` is sent

#### Scenario: An active ask is a typed error

- GIVEN the engine answers 409 `ask_active`
- WHEN `ask` is called
- THEN it rejects with the typed error carrying code `ask_active` and
  `retryable` true
