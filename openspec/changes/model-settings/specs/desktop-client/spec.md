# Delta for desktop-client

## ADDED Requirements

### Requirement: The Client Reads And Changes The Model Settings

The client MUST offer `modelSettings()` (`GET /v1/settings/model`),
`previewModelSettings(body)` (`POST /v1/settings/model/preview`) and
`applyModelSettings(body)` (`POST /v1/settings/model/apply`), typed from the
generated schema.

#### Scenario: A preview is applied with its version

- GIVEN a ready engine
- WHEN `previewModelSettings(change)` and then `applyModelSettings({version})`
  are called
- THEN each request carries the token and the typed answer is returned
