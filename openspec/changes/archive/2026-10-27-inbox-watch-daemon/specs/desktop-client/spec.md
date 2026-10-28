# Desktop Client Specification (delta)

## ADDED Requirements

### Requirement: The Client Reads And Changes The Watch Folder And Asks The Shell About The Watch

The client MUST offer `inboxSettings()` (`GET /v1/settings/inbox`),
`previewInboxSettings(body)` (`POST /v1/settings/inbox/preview`),
`applyInboxSettings(body)` (`POST /v1/settings/inbox/apply`) and
`unattendedActivity()` (`GET /v1/unattended`), typed from the generated schema.
It MUST also offer `createWatch(invoke)` over the shell's `watch_state`,
`set_watch`, `pick_inbox_folder` and `notify_watch_budget` commands. A shell
answer that is not one of the shell's states MUST become a `WatchError` with
reason `unknown`, and a refusal MUST carry the shell's reason (`no_workspace`,
`watch_choice_unkept`).

#### Scenario: The watch is turned on through the shell

- GIVEN a shell that answers `set_watch`
- WHEN `set(true)` is called
- THEN the command is invoked with `{on: true}` and the new state is returned

#### Scenario: An answer the shell never sends is refused

- GIVEN a shell that answers `watch_state` with an unknown state
- WHEN `state()` is called
- THEN a `WatchError` with reason `unknown` is thrown
