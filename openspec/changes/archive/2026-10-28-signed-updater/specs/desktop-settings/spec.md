# Desktop Settings Specification (delta)

## ADDED Requirements

### Requirement: Settings Offers The App's Updates And Installs Only On Confirmation

Once the shell has said whether the app checks for updates, Settings MUST show
an "Updates" card, also while no engine runs, in the words of
`docs/decision-wording.md` ("Updates"): that the app asks the project's release
page on GitHub, that nothing from the library is sent and nothing is installed
until the person says so, whether checking is on, a way to turn it off or on,
"Check now", and what the last check found. The app MUST check once at launch,
with no engine needed. A version on offer MUST be named with its notes on the
card and as a quiet entry at the foot of the sidebar that opens Settings.
"Install and restart…" MUST only open a confirmation that starts on "Not now";
only its confirm MUST call `install_update`, with the version shown. A refusal
MUST be worded from the shell's reason alone (`install_failed`,
`update_changed`, `no_update`, `choice_unkept`); an offer the shell no longer
holds MUST be dropped. A failed check MUST be said only on the card.

#### Scenario: A newer version is offered and nothing is downloaded

- GIVEN the shell's check found 0.7.0
- WHEN the app starts
- THEN the sidebar shows "Update available: 0.7.0", Settings names it and its
  notes, and `install_update` is not called

#### Scenario: Not now installs nothing

- GIVEN the install confirmation is open
- WHEN the person chooses Not now
- THEN `install_update` is not called

#### Scenario: Turning checking off forgets the offer

- GIVEN a version on offer
- WHEN the person chooses Stop checking
- THEN `set_update_check(false)` is called and nothing is offered any more
