# Desktop Client Specification (delta)

## ADDED Requirements

### Requirement: The Client Wraps The Shell's Update Commands

`desktop/client/update.ts` MUST wrap `update_settings`, `set_update_check`,
`check_for_update` and `install_update` over the shell's `invoke`, MUST type a
check as `off`, `up_to_date`, `available` (with `version`, `current_version`
and `notes`) or `failed`, and MUST throw an `UpdateError` carrying the shell's
reason (`choice_unkept`, `no_update`, `update_changed`, `install_failed`), or
`unknown` for any other rejection or an answer in a shape the shell never sends.
A test MUST hold the wrapped command names, the check's states and the reasons
equal to the shell's source.

#### Scenario: An unknown answer is refused

- GIVEN a shell that answers `check_for_update` with `{"state": "soon"}`
- WHEN `check()` is called
- THEN an `UpdateError` with reason `unknown` is thrown
