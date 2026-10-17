# Tasks: Settings — The AI Model And The Library

## 1. Configuration (PR 1)

- [x] 1.1 `config.parse_config(root, text)`: the body of `read_config` without the file read; `read_config` calls it.
- [x] 1.2 `config.validate_base_url` public over `_validate_base_url`.
- [x] 1.3 `config.set_model_settings(text, ...)` and `config.model_settings_lines(text)`: text surgery on the five lines, fail-closed on un-editable shapes.

## 2. Service and API (PR 1)

- [x] 2.1 `application/model_settings.py`: read, preview, apply with the digest, the lock and the one-change commit.
- [x] 2.2 `vcs/autocommit.plan_transformed_config_edit` and the API's settings ports in `_wiring`.
- [x] 2.3 `api/_settings.py` and the public names; regenerate `api_surface.json`.

## 3. Local API and client (PR 1)

- [x] 3.1 Routes, request models and error rows; regenerate `openapi.json`.
- [x] 3.2 `desktop/client`: `modelSettings`, `previewModelSettings`, `applyModelSettings`; regenerate `schema.d.ts`.

## 4. Screen (PR 2)

- [x] 4.1 `desktop/ui/settings/wording.ts` and the "Settings" section of `docs/decision-wording.md`.
- [x] 4.2 `desktop/ui/settings/model.ts`: the view model over injected ports.
- [x] 4.3 `desktop/ui/settings/render.ts`: the screen on the design system.
- [x] 4.4 Wiring in `desktop/ui/main.ts`: the sidebar item and the cloud indicator.

## 5. Close (PR 2)

- [x] 5.1 Issues for setting a page's sensitivity from the app (#1517) and rebuilding search from it (#1518).
- [ ] 5.2 Archive: merge the deltas, move the change, write the archive report.
