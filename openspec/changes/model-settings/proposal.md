# Proposal: Settings — The AI Model And The Library

Tracking issue: #1514. Refs #1477. Governing decisions: ADR-0031 (the
`openai-compatible` backend), ADR-0060 (the versioned Python API), ADR-0061 (the
local API adapter), ADR-0065 (the generated TypeScript client), ADR-0066 (plain
DOM screens over tested view models), ADR-0068 (asking applies the query
disclosure rule), ADR-0069 (the design system), ADR-0070 (the keychain key),
ADR-0071 (the shell remembers the chosen workspace). No new decision record: the
choices below are reversible within the additive rule and are recorded in
`design.md`. Paths are under `src/openkos` unless they start with `tests/`,
`docs/`, `desktop/` or `openspec/`.

## Intent

MVP 6 promises that "a cloud model is optional, off by default, and never
offered during setup. It is turned on in settings, behind a plain-language
warning that states what leaves the machine, what never does, who receives it
and that the provider may charge" (roadmap). ADR-0070 gave the shell a place to
keep the key, but nothing in the app turns a cloud model on: `openkos.api`
cannot read the model setup without probing the runtime, and cannot change it.
Switching backends today means editing `openkos.yaml` by hand. The app also has
no place to see which library is open, or to open or create another, once
setup is done.

## Scope

### In scope

- **Read.** `model_settings(workspace)`: the configured backend, `base_url`,
  `embedding_base_url`, chat and embedding model, the redacted hosts that are
  not on this machine, and whether the backend takes an API key. Model-free:
  no client, no network, no write, no lock.
- **Change.** `preview_model_settings(workspace, change)` and
  `apply_model_settings(workspace, version, ...)`: a two-phase rewrite of only
  the `backend`, `base_url`, `embedding_base_url`, `model` and
  `embedding_model` lines of `openkos.yaml`, validated by the existing config
  validators and by reading the result back through the same parser
  `read_config` uses. The preview lists what changes and whether search must be
  rebuilt (the embedding identity changes). The apply takes the lock once,
  re-plans, writes, and commits only that change.
- **Local API and client.** `GET /v1/settings/model`,
  `POST /v1/settings/model/preview`, `POST /v1/settings/model/apply`; the
  TypeScript client regenerated.
- **Screen.** `desktop/ui/settings/`: "AI model" (on this computer by default;
  a cloud model only after the roadmap's warning and an explicit
  confirmation; the key through the shell's keychain commands, shown only as
  saved or not) and "Library" (the open folder; open another; create a new
  one). A quiet sidebar entry, and a calm sidebar indicator while a cloud model
  is on.
- **Wording.** A "Settings" section in `docs/decision-wording.md`.

### Out of scope

- Marking a page confidential from the app. No API sets a page's sensitivity
  yet; it is tracked as its own issue, and the settings screen says where
  sensitivity is set today rather than offering a control that does not exist.
- Per-task model overrides (`models:`), sampling, timeouts and every other key:
  the change never touches them.
- Testing a connection to the cloud endpoint from the settings screen. Setup
  readiness already probes the configured runtime.
- Rebuilding search after an embedding change: the preview says it is needed;
  the existing maintenance path does it.

## Principles

- **Local-first.** The default stays local. Nothing is sent anywhere by the read
  or the change; the cloud model is opt-in behind a warning.
- **The key never touches the workspace.** The change refuses to write any key
  (the parser already refuses `api_key` and friends); the key goes through the
  shell (ADR-0070).
- **Human curates.** The change is previewed, then applied against a version.

## Rollback

Every addition is additive within `API_VERSION` 1 and can be reverted by
reverting its commits. A workspace whose settings were changed keeps a git
commit per change, so the previous `openkos.yaml` is one `git revert` away.
