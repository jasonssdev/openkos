# Desktop Shell Specification (delta)

## ADDED Requirements

### Requirement: The Shell Checks For The App's Updates Only While The Person Allows It

The shell MUST check for a newer build of the app only through the Tauri updater
plugin (ADR-0079), from Rust; no capability MUST give the web view the plugin's
own commands, and the web view's content security policy MUST NOT name the
feed's hosts (ADR-0081). The feed MUST be exactly
`https://github.com/jasonssdev/openkos/releases/latest/download/latest.json`,
over https, with no `dangerous*` option, and `requireSignedVersion` MUST be on.
The public key MUST be committed in `tauri.conf.json`.

Whether the app checks MUST be the person's choice, kept in the shell's own
configuration directory (`updates.json`), never in a workspace: on when the
file is missing, off when it cannot be read or parsed, and written atomically.
`check_for_update` MUST make no request while the choice is off and answer
`off`; otherwise it MUST answer `up_to_date`, `available` (the version, the
running version and the release notes) or `failed`, a failure carrying no
detail. `update_settings` MUST answer the choice and `set_update_check` MUST
keep it, refusing with `choice_unkept` when it cannot be kept; turning it off
MUST forget an update on offer.

#### Scenario: Off asks nothing

- GIVEN the person turned update checks off
- WHEN the page invokes `check_for_update`
- THEN the shell answers `off` and no request is made

#### Scenario: A malformed choice is off

- GIVEN an `updates.json` that is not valid JSON
- WHEN the shell reads the choice
- THEN checking is off

#### Scenario: A failed check tells the page nothing more

- GIVEN the feed cannot be reached
- WHEN the page invokes `check_for_update`
- THEN the answer is `{"state": "failed"}` with no other field

### Requirement: The Shell Installs Only The Update The Person Confirmed

The shell MUST download and install an update only from `install_update`,
called with the version the page was shown, and only when that version is the
one the last check found and still holds; otherwise it MUST refuse with
`update_changed` (another version is held, which stays held) or `no_update`
(none is held). The updater MUST verify the archive against the committed
public key before it replaces the app; a failure to download, verify or install
MUST refuse with `install_failed`. After a successful install the shell MUST
stop the watch's daemon and the engine, then restart the app. Nothing MUST
download or install an update without that command.

#### Scenario: Only the version shown is installed

- GIVEN a check found 0.7.0
- WHEN `install_update` is called with 0.8.0
- THEN it is refused with `update_changed` and 0.7.0 is still held
