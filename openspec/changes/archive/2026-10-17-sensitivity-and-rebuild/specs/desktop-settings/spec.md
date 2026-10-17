# Delta for desktop-settings

## ADDED Requirements

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
failed or skipped. A stopped or failed rebuild MUST say search wasn't changed.

#### Scenario: Rebuilding after a change of the AI model for search

- GIVEN Settings saved a new AI model for search on this Mac
- WHEN the person presses Rebuild search
- THEN the job starts without a dialog, its progress shows, and the section
  ends with "Search is up to date."

#### Scenario: A cloud AI model for search asks first

- GIVEN search sends to `api.example.com`
- WHEN the person presses Rebuild search and then Not now
- THEN no rebuild is started
