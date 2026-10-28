# Local HTTP API Specification (delta)

## ADDED Requirements

### Requirement: The Watch Folder Is Served Under `/v1/settings/inbox`

The server MUST add these operations to the ones it serves under `/v1`, each
calling the matching `openkos.api` function with the launch's workspace:

| Method and path | `openkos.api` operation |
|---|---|
| `GET /v1/settings/inbox` | `inbox_settings` |
| `POST /v1/settings/inbox/preview` | `preview_inbox_settings` |
| `POST /v1/settings/inbox/apply` | `apply_inbox_settings` |

The preview body MUST be `{inbox}` with unknown fields rejected and `inbox`
required: a folder, or `null` to stop watching. The apply body MUST carry the
preview's `version` unchanged and an optional `wait_seconds`, capped as for every
write. `InboxSettingsRefused` MUST answer 422 with a code per reason
(`invalid_inbox`, `inbox_inside_workspace`, `inbox_not_a_directory`), except
`unchanged`, which MUST answer 409 `inbox_unchanged`; `InboxSettingsStale` MUST
answer 409 `stale`. Applying MUST write only the `unattended.inbox` line of
`openkos.yaml`, commit only it, leave the person's other uncommitted edits out of
the commit, and refuse (not overwrite) an uncommitted edit to that line. It MUST
NOT start or stop any process.

#### Scenario: A preview names the folder and writes nothing

- GIVEN an initialised workspace and an existing folder outside it
- WHEN `POST /v1/settings/inbox/preview` is sent with that folder
- THEN the response is 200, `proposed.inbox` is the folder's resolved path and
  the workspace is unchanged

#### Scenario: The engine's own trees are refused with their reason

- GIVEN a preview body naming the workspace's `raw/` folder
- WHEN it is sent
- THEN the response is 422 with code `inbox_inside_workspace`

#### Scenario: Turning the watch off removes the key

- GIVEN a workspace that watches a folder
- WHEN a preview with `inbox: null` is applied
- THEN `openkos.yaml` has no `unattended.inbox` and `inbox_settings` reads `null`
