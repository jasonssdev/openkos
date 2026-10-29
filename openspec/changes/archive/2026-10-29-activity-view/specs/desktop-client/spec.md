# Desktop Client Specification (delta)

## ADDED Requirements

### Requirement: The Client Reads The Engine's Activity

The client MUST offer `engineActivity()` (`GET /v1/activity`), typed from the
generated schema.

#### Scenario: The method calls its route

- WHEN `engineActivity()` is called
- THEN it sends a GET to `/v1/activity` with the bearer token
