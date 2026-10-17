# Tasks: Who Can See A Page, And Rebuilding Search, From The App

## 1. Sensitivity (PR 1)

- [x] 1.1 `application/sensitivity_service.py`: preview, apply, the shared log line and commit message; the CLI uses the shared helpers.
- [x] 1.2 `api/_sensitivity.py` and the public names; regenerate `api_surface.json`.
- [x] 1.3 Routes `POST /v1/pages/sensitivity/{preview,apply}`, request models and error rows; regenerate `openapi.json`.
- [x] 1.4 `desktop/client`: `previewSensitivity`, `applySensitivity`, the two codes; regenerate `schema.d.ts`.

## 2. Search (PR 2)

- [x] 2.1 `state.vectorstore.read_stored_model_tag`, `ReindexPorts.commit_section`.
- [x] 2.2 `application/search_rebuild.py`: the read and the rebuild with consent, stop and progress.
- [x] 2.3 `api/_search.py` and the public names; regenerate `api_surface.json`.
- [x] 2.4 The rebuild registry and routes; regenerate `openapi.json`.
- [x] 2.5 `desktop/client`: `searchIndex`, `startSearchRebuild`, `searchRebuild`, `stopSearchRebuild`; regenerate `schema.d.ts`.

## 3. Screens (PR 3)

- [ ] 3.1 Wording in `docs/decision-wording.md`.
- [ ] 3.2 The page panel and the label choice, with Undo; Ask citations and To review page cards.
- [ ] 3.3 The rebuild component in Settings, Ask and Status.

## 4. Close (PR 3)

- [ ] 4.1 Archive: merge the deltas, move the change, write the archive report.
