# Delta for desktop-client

## ADDED Requirements

### Requirement: The Client Exposes Setup Readiness And The Model Pull

The client MUST offer `setupReadiness()` (`GET /v1/setup/readiness`),
`startModelPull(body)` (`POST /v1/setup/pulls`), `modelPull(pullId)`
(`GET /v1/setup/pulls/{pull_id}`) and `stopModelPull(pullId)`
(`POST /v1/setup/pulls/{pull_id}/stop`), typed from the generated schema, and
its error codes MUST include `pull_active`, `model_not_configured` and
`pull_not_supported`.

#### Scenario: A pull is started and polled

- GIVEN a ready engine
- WHEN `startModelPull({model})` and then `modelPull(id)` are called
- THEN each request carries the token and the typed job is returned

#### Scenario: An active pull is a typed refusal

- GIVEN the engine answers 409 `pull_active`
- WHEN `startModelPull` is called
- THEN the client raises its typed error with code `pull_active`
