# Delta for stable-python-api

## ADDED Requirements

### Requirement: The Model Settings Are A Model-Free Read

`openkos.api.model_settings(workspace)` MUST return a `ModelSettings` with the
configured `backend` (`ModelBackend`: `"ollama"` or `"openai-compatible"`),
`base_url` and `embedding_base_url` as configured (`None` when unset), `model`,
`embedding_model`, `remote_hosts` (the redacted display host of each resolved
endpoint, chat then embedding when different, that is not on this machine, as
`classify_backend_host` judges it; `OLLAMA_HOST` counts for `ollama` as the
client would use it), `is_local` (true exactly when `remote_hosts` is empty) and
`takes_api_key` (true for `openai-compatible`). It MUST NOT build a client,
reach the network, take the lock or write, and it MUST NOT carry an API key or a
URL's userinfo. An unreadable `openkos.yaml` MUST raise `WorkspaceUnreadable`.

#### Scenario: A fresh workspace is local

- GIVEN a workspace created by `init` and no `OLLAMA_HOST`
- WHEN `model_settings` is called
- THEN `backend` is `ollama`, `base_url` is `None`, `is_local` is true,
  `remote_hosts` is empty and `takes_api_key` is false, and nothing is written

#### Scenario: A remote endpoint is named by its host only

- GIVEN `backend: openai-compatible` and `base_url: https://api.example.com/v1`
- WHEN `model_settings` is called
- THEN `is_local` is false and `remote_hosts` is `("api.example.com",)`

### Requirement: The Model Settings Are Changed Against A Previewed Version

`openkos.api` MUST export `ModelSettingsChange` (`backend`, `base_url`,
`embedding_base_url`, `model`, `embedding_model`),
`preview_model_settings(workspace, change)` and
`apply_model_settings(workspace, version, *, wait_seconds=0.0,
on_warning=None)`. The change MUST rewrite only the `backend`, `base_url`,
`embedding_base_url`, `model` and `embedding_model` lines of `openkos.yaml`,
leaving every other line byte-identical; a `None` URL removes its line, and an
absent `backend:` line stays absent for `ollama`. Values MUST be trimmed and
checked by the existing config validators, and the rewritten text MUST be read
back by the configuration parser and match the request before anything is
written.

The preview MUST return a `ModelSettingsPreview` with `current` and `proposed`
(`ModelSettings`), `changes` (one `SettingChange` — `field`, `before`, `after`
— per field that differs, in the order backend, base_url, embedding_base_url,
model, embedding_model), `search_rebuild` (true when the embedding identity
changes, so stored vectors no longer match) and a `ModelSettingsVersion` whose
`content_digest` covers the normalized change and the bytes of `openkos.yaml`.
It MUST return `ModelSettingsRefused` with reason `invalid_base_url`,
`invalid_embedding_base_url`, `invalid_model`, `invalid_embedding_model`,
`base_url_required` (`openai-compatible` with no `base_url`) or `unchanged`. It
MUST NOT take the lock, build a client or write. A backend outside
`ModelBackend` MUST raise `ValueError`; a shape of `openkos.yaml` the change
cannot edit MUST raise `WriteFailed`.

The apply MUST take the lock once, plan again, return `ModelSettingsRefused` or
`ModelSettingsStale` (the file or the echoed version changed) without writing,
refuse with `Refused` when `openkos.yaml` carries an uncommitted edit to one of
the five lines, and otherwise write `openkos.yaml`, commit only this change, and
return `ModelSettingsChanged` (`settings`, `commit_sha`).

#### Scenario: Turning on an endpoint rewrites only the model lines

- GIVEN a fresh workspace
- WHEN a change to `openai-compatible` with a `base_url` and two model names is
  previewed and applied
- THEN `openkos.yaml` reads back with those values, every other line is
  byte-identical, and the last commit holds only `openkos.yaml`

#### Scenario: Switching back removes the endpoint

- GIVEN a workspace on `openai-compatible`
- WHEN a change to `ollama` with no URLs is applied
- THEN the `base_url` line is gone and `model_settings` reports `is_local`

#### Scenario: A key-shaped URL is refused

- GIVEN a `base_url` carrying userinfo
- WHEN the change is previewed
- THEN it returns `ModelSettingsRefused("invalid_base_url")` and nothing is
  written

#### Scenario: A configuration changed after the preview is stale

- GIVEN a preview, and `openkos.yaml` edited afterwards
- WHEN the apply is called with the preview's version
- THEN it returns `ModelSettingsStale` and nothing is written

#### Scenario: A new embedding model needs a search rebuild

- GIVEN a change whose `embedding_model` differs from the configured one
- WHEN it is previewed
- THEN `search_rebuild` is true

### Requirement: Model Settings Extend v1 Additively

Every name this change adds (`model_settings`, `preview_model_settings`,
`apply_model_settings`, `ModelBackend`, `ModelSettings`, `ModelSettingsChange`,
`ModelSettingsPreview`, `ModelSettingsVersion`, `ModelSettingsChanged`,
`ModelSettingsRefused`, `ModelSettingsStale`, `SettingChange`) MUST be
classified additive by the surface classifier, and `API_VERSION` MUST stay 1.

#### Scenario: The settings surface diff is additive

- GIVEN the regenerated `api_surface.json`
- WHEN the classifier compares it with the snapshot on `main`
- THEN every change is additive and `API_VERSION` is 1
