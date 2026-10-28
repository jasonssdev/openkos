# Desktop Settings Specification (delta)

## ADDED Requirements

### Requirement: A Folder Is Watched From Settings After A Preview

Once the library's inbox settings have been read (`GET /v1/settings/inbox`),
Settings MUST show a "Watch a folder" card. Before anything is turned on, the
card MUST say, in the words of `docs/decision-wording.md`, what the watch does:
text files that stay unchanged for a short while are added by themselves using
the AI model within the daily limit set for it, other files are ignored, the
folder is never changed, an edited file becomes a new version, and combining
pages, connections and replacing an older version wait in To review. It MUST
name the daemon's other jobs (trying again to save changes that failed, and the
maintenance pass that uses the AI model within the same limit) and that
watching stops when the app quits or another library is opened.

"Choose a folder…" MUST open the shell's folder window (`pick_inbox_folder`)
and preview the folder through `POST /v1/settings/inbox/preview`; nothing MUST
be written until the person presses "Start watching" on a confirmation that
names the folder as OpenKOS will keep it. Start watching MUST apply the
previewed version unchanged through `POST /v1/settings/inbox/apply` and only
then start the watch through `set_watch`; a folder that is already the saved
one (`inbox_unchanged`) MUST only start the watch. "Stop watching" MUST stop
the watch first and then remove the folder through the same preview and apply.
A refusal MUST be worded from its code alone (`inbox_inside_workspace`,
`inbox_not_a_directory`, `invalid_inbox`, and the model settings' `stale`,
`workspace_busy`, `write_refused`), never from the engine's or the shell's text.
A folder that was saved but whose watch did not start MUST say so.

#### Scenario: A chosen folder is previewed, then saved and started

- GIVEN a library that watches nothing
- WHEN the person chooses a folder and presses Start watching
- THEN the folder is previewed, then applied with the previewed version, then
  `set_watch(true)` is called, in that order

#### Scenario: Cancelling writes nothing

- GIVEN a previewed folder awaiting confirmation
- WHEN the person presses Cancel
- THEN no apply and no `set_watch` call is made

#### Scenario: A folder inside the library is refused in plain words

- GIVEN a folder that is the library's own files
- WHEN it is previewed and the engine answers `inbox_inside_workspace`
- THEN the card says "That folder is part of your library. Choose a folder
  outside it." and nothing is written or started

#### Scenario: Stopping takes the folder out

- GIVEN a library that watches a folder
- WHEN the person presses Stop watching
- THEN `set_watch(false)` is called, then the folder is removed from the
  library's settings, and the card says "OpenKOS stopped watching."

### Requirement: The Watch Status Is Kept On Every Screen

The watch MUST have a persistent status with four states: on; stopped by the
person; stopped on the budget; failed. It MUST be shown on the Settings card
and as a quiet entry at the foot of the sidebar on every screen, which opens
Settings. "Stopped on the budget" MUST be read from the engine's latest
unattended job record (`GET /v1/unattended`, `budget_exhausted`) because the
daemon idles on a spent budget and does not exit; "failed" MUST come from the
shell's state once its restart budget is spent, with the daemon's report behind
a disclosure and a "Start again" action. Nothing MUST be shown before the watch
was ever turned on. The page MUST read the watch again on a timer, so a stop
shows without opening Settings.

#### Scenario: A stop on the limit shows without a visit to Settings

- GIVEN the watch is on and the latest unattended job ended `budget_exhausted`
- WHEN the page next reads the watch
- THEN the sidebar's entry says "Watching: paused"

#### Scenario: A failure offers Start again

- GIVEN the shell reports the watch as failed
- WHEN Settings is shown
- THEN the card says watching stopped because something went wrong, offers
  Start again, and holds the daemon's report behind Show details

#### Scenario: A watch never turned on says nothing

- GIVEN no folder is saved and the person never turned the watch on
- WHEN any view is shown
- THEN no watch status is shown
