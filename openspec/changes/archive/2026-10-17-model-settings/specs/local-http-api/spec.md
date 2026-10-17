# Delta for local-http-api

## ADDED Requirements

### Requirement: The Model Settings Are Served Under `/v1/settings/model`

The server MUST add these operations to the ones it serves under `/v1`, each
calling the matching `openkos.api` function with the launch's workspace:

| Method and path | `openkos.api` operation |
|---|---|
| `GET /v1/settings/model` | `model_settings` |
| `POST /v1/settings/model/preview` | `preview_model_settings` |
| `POST /v1/settings/model/apply` | `apply_model_settings` |

The preview body MUST be a `ModelSettingsChange` with unknown fields rejected.
The apply body MUST carry the preview's `version` unchanged and an optional
`wait_seconds`, capped as for every write. `ModelSettingsRefused` MUST answer
422 with the reason as its code (`invalid_base_url`,
`invalid_embedding_base_url`, `invalid_model`, `invalid_embedding_model`,
`base_url_required`), except `unchanged`, which MUST answer 409
`settings_unchanged`; `ModelSettingsStale` MUST answer 409 `stale`. No body,
response or error message MUST carry an API key.

#### Scenario: The settings read writes nothing

- GIVEN an initialised workspace and a valid token
- WHEN `GET /v1/settings/model` is requested
- THEN the response is 200 with the fields of `ModelSettings` and the workspace
  is unchanged

#### Scenario: A preview and its version apply the change

- GIVEN a preview answered by `POST /v1/settings/model/preview`
- WHEN its `version` is sent unchanged to `POST /v1/settings/model/apply`
- THEN the response is 200 with the changed settings and `openkos.yaml` holds
  them

#### Scenario: An invalid URL is a typed refusal

- GIVEN a preview body whose `base_url` is not an http(s) URL
- WHEN it is sent
- THEN the response is 422 with code `invalid_base_url`
