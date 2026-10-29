# Desktop Settings Specification

## Purpose

The desktop app's settings screen (MVP 6), under `desktop/ui/settings/`: where
a person who never opens a terminal sees and changes where the AI model runs
and which library is open. It reads and changes the model settings through the
desktop client (`desktop-client`) over the local API (`local-http-api`), turns a
cloud AI model on only behind the roadmap's warning, keeps the API key only in
the keychain through the shell (`desktop-shell`), and words everything from
`docs/decision-wording.md` ("Settings"). Its behaviour lives in a view model
and a wording module with no DOM, which are unit-tested; render functions draw
from their state (ADR-0066).

## Requirements

### Requirement: Settings Is Reached From The Foot Of The Sidebar

The desktop app MUST have a Settings view, under `desktop/ui/settings/`, opened
by a quiet "Settings" item at the foot of the sidebar, above the engine's badge,
and held back with the navigation while no library is open. Its behaviour MUST
live in a view model and a wording module with no DOM, drawn by render
functions from their state with the design system's components (ADR-0066,
ADR-0069), and every word MUST come from `docs/decision-wording.md`
("Settings"), with no engine word.

#### Scenario: Settings opens from the sidebar

- GIVEN a running engine
- WHEN the person chooses Settings
- THEN the Settings view shows an "AI model" card and a "Library" card, and the
  Settings item is the current page

### Requirement: The AI Model Is Shown Lightly And Changed Through A Preview

The AI model card MUST say where the AI model runs in one line ("On this Mac."
or "In the cloud, at {host}." from `remote_hosts`), whether a key is saved for a
backend that takes one, and hold the model names behind a disclosure. Change
MUST open a form choosing "On this Mac" or "A cloud AI model"; a cloud model
asks for the address, the AI model, the AI model for search and the key.
Coming back to this Mac MUST use the library's local models in use, or the
engine's packaged ones when it has none. Save MUST preview the change through
`POST /v1/settings/model/preview` and apply the returned version unchanged
through `POST /v1/settings/model/apply`; nothing MUST change before Save.

#### Scenario: Going back to this Mac needs no warning

- GIVEN a cloud AI model in use
- WHEN the person chooses On this Mac and saves
- THEN the change is applied without a warning and the card says "On this Mac."

### Requirement: A Cloud Model Is Turned On Only After The Warning

When the preview's proposed settings name a host not on this machine that the
settings in use do not, the app MUST show, before applying anything, a modal
warning that starts on its safe answer and states who receives the text (the
host), that pages marked Shareable or Private are sent and that pages start as
Private, that pages marked Only on this Mac are never sent, and that the
service may charge. Only its explicit confirmation MUST apply the previewed
version; declining MUST change nothing and keep the form.

#### Scenario: Declining the warning changes nothing

- GIVEN a library on this Mac and a cloud AI model filled in
- WHEN the person saves and then chooses "Keep it on this Mac"
- THEN no apply is sent, no key is kept, and the form is still open

### Requirement: The Key Goes Only To The Keychain And Is Never Shown

The key field MUST be a password field that starts empty on every draw. A
typed key MUST be sent only to the shell's `set_api_key`, after the settings
were applied (setting it restarts the engine), and MUST NOT remain in the view
model once sent. The app MUST show only "Key saved" or "No key", from
`api_key_status`, offer "Remove key" through `clear_api_key`, and word every
refusal from the shell's reason alone.

#### Scenario: A key is kept after the settings

- GIVEN a confirmed cloud AI model with a key typed
- WHEN the change is applied
- THEN the settings are applied first, then the key is sent to the keychain,
  the screen says "Key saved", and the key is in no state the page holds

### Requirement: A Cloud Model Being On Is Shown On Every Screen

While the settings in use are not on this machine, the foot of the sidebar MUST
show a quiet "Cloud AI model on" on every screen, which opens Settings, and it
MUST stay while the engine restarts.

#### Scenario: The line follows the settings

- GIVEN settings whose `is_local` is false
- WHEN any view is shown
- THEN the sidebar's foot shows "Cloud AI model on"

### Requirement: The Library Is Shown And Changed Through The Shell

The Library card MUST show the open library's folder and offer "Open another
library…" and "Create a new library…" through the shell's workspace commands,
with the welcome's refusal wording, and offer neither when the library was
chosen outside the app.

#### Scenario: Another library is opened

- GIVEN a library open in the app
- WHEN the person chooses Open another library… and a folder holding a library
- THEN the shell opens it and the card says "Opening your library…" until the
  engine runs on it

### Requirement: Search Is Rebuilt From Where It Is Behind

The app MUST read `searchIndex` when an engine starts, when Settings or Status
opens and after a settings change, and while `needs_rebuild` holds, a rebuild
runs or one ended this session, it MUST show the rebuild section under the AI
model card in Settings, in Your library's card on Status, and under an Ask
answer whose search may have missed something. The section MUST say why
(a new AI model for search, or changes since search was brought up to date),
what it costs (nothing leaves this Mac, or that the person will see what is
sent to the host first), and offer **Rebuild search**. With a cloud AI model
for search, Rebuild search MUST ask first, with the cloud warning's send rule,
in a dialog that starts on **Not now**, and only then start the job with
`allow_remote`. While it runs the section MUST show its progress in words and
as a bar, with **Stop**; after, how it ended, including the pages withheld,
failed or skipped. A stopped or failed rebuild MUST say search wasn't changed. How a rebuild ended MUST be
closable with **Dismiss**, and MUST show only where a rebuild is started (Settings and Status), never on Ask.

#### Scenario: Rebuilding after a change of the AI model for search

- GIVEN Settings saved a new AI model for search on this Mac
- WHEN the person presses Rebuild search
- THEN the job starts without a dialog, its progress shows, and the section
  ends with "Search is up to date." beside **Dismiss**

#### Scenario: A cloud AI model for search asks first

- GIVEN search sends to `api.example.com`
- WHEN the person presses Rebuild search and then Not now
- THEN no rebuild is started

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
