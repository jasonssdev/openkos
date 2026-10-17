# Archive Report: model-settings

**Archived**: 2026-10-17 (the second and last slice of the change)
**Archive folder**: `openspec/changes/archive/2026-10-17-model-settings/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1514, closed by the second slice. Follow-ups: #1517 (mark a page Only on this Mac in one action), #1518 (rebuild search from the app).

## Final State At Close

| Slice | PR | Content |
|-------|----|---------|
| 1 engine and API | #1520 (squash `52b30c4a`) | `config.parse_config`, `validate_base_url`, `set_model_settings`, `model_settings_lines`; `application/model_settings.py`; `autocommit.plan_model_settings_edit`; `api._settings` and twelve public names; three routes; three client methods |
| 2 screen, wording, archive | the PR that closes #1514 | `desktop/ui/settings/`, sidebar entry and cloud line in `main.ts`, the password field type, `docs/decision-wording.md` "Settings", this archive |

- **Shipped surface** (additive, `API_VERSION` 1): `model_settings`, `preview_model_settings`, `apply_model_settings`, `ModelBackend`, `ModelSettings`, `ModelSettingsChange`, `ModelSettingsPreview`, `ModelSettingsVersion`, `ModelSettingsChanged`, `ModelSettingsRefused`, `ModelSettingsStale`, `SettingChange`. HTTP: `GET /v1/settings/model`, `POST /v1/settings/model/{preview,apply}`; codes `invalid_base_url`, `invalid_embedding_base_url`, `invalid_model`, `invalid_embedding_model`, `base_url_required` (422), `settings_unchanged` (409).
- **ADRs**: none created (design: every choice is reversible within the additive rule).

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| stable-python-api | Updated | 3 ADDED appended: The Model Settings Are A Model-Free Read; The Model Settings Are Changed Against A Previewed Version; Model Settings Extend v1 Additively. |
| local-http-api | Updated | 1 ADDED appended: The Model Settings Are Served Under `/v1/settings/model`; its three rows also added to the route table of "The Operations Are Served Under `/v1`". |
| desktop-client | Updated | 1 ADDED appended: The Client Reads And Changes The Model Settings. |
| desktop-settings | Created | New domain with a Purpose and the 6 requirements of the delta. |

Every `### Requirement:` heading in the four living specs is unique (checked by script).

## Requirement-vs-Code Check

Each requirement was read against the shipped code, not the task list: the read builds no client (`forbid_model_clients` in the API and route tests); the change touches only the five lines and reads back through `parse_config`; the warning is shown before any apply and starts on Cancel; the key is sent after the apply and is absent from the view model's state afterwards; the sidebar line follows `is_local`.

## Not Done

- Marking a page Only on this Mac from the app: no API exists (#1517). The roadmap's "one action away" is therefore not met yet.
- Rebuilding search after the AI model for search changes: reported by the preview and the screen, not performed (#1518).
- The screen was verified in jsdom only; it was not exercised in the running Tauri app, and the macOS keychain prompt was not exercised.
