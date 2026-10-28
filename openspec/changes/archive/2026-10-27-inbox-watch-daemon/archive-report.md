# Archive Report: inbox-watch-daemon

**Archived**: 2026-10-27
**Archive folder**: `openspec/changes/archive/2026-10-27-inbox-watch-daemon/`
**Store mode**: filesystem merge and `git mv`.
**Tracking issue**: #1626. Engine and shell slices merged in #1655.

## Final State At Close

- The inbox watch can be turned on from Settings: the shell supervises
  `openkos daemon --stop-on-stdin-eof` for the current library only while the
  person turned the watch on, the folder is written through the settings API's
  preview and apply, and the card names what the watch does and the daemon's
  other jobs (ADR-0078, Three and Four).
- A stopped watch is visible without looking for it (ADR-0078, Five): on, off,
  paused on the AI model's limit, or stopped, on the Settings card and in the
  sidebar, plus a macOS notification for the last two.
- **"Stopped on the budget" has no exit code.** The daemon idles on a spent
  budget and records `budget_exhausted` in `jobs.db`, so a small read-only
  engine read was added: `application/unattended_activity.py`,
  `openkos.api.unattended_activity` and `GET /v1/unattended`. It is true only
  when the newest watch or maintenance job ended `budget_exhausted`.
- **Notification, and why no capability.** `tauri-plugin-notification` is pinned
  to `=2.6.0` and used only from Rust with fixed text; the budget notice is
  requested by the page through `notify_watch_budget`, which takes no text. No
  capability gives the web view a notification permission.
- ADR-0078 was Accepted before the work and is unchanged.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| desktop-shell | Updated | 2 ADDED requirements appended: supervision of the daemon only while the person turned it on; the notification with fixed text. |
| folder-watch | Updated | 1 ADDED requirement appended: the daemon stops when its supervisor's pipe closes. |
| local-http-api | Updated | 2 ADDED requirements appended: `/v1/settings/inbox` and `/v1/unattended`. |
| desktop-settings | Updated | 2 ADDED requirements appended: the folder is watched after a preview; the status is kept on every screen. |
| desktop-client | Updated | 1 ADDED requirement appended: the watch folder and shell watch wrappers. |
| stable-python-api | Updated | 2 ADDED requirements appended: the inbox settings facade and `unattended_activity`. |

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta appears
exactly once in its living spec after the merge, and none was present before.
No MODIFIED, REMOVED or RENAMED headings.

## Requirement-vs-Code Check

The requirements describe what shipped. Two were corrected while writing the
deltas against the code: "stopped by the person" is shown when a folder is saved
and not watched, or the person stopped it in this session (the shell keeps no
"turned off" flag, so a never-used watch and a stopped one cannot otherwise be
told apart); and Stop watching removes the folder (ADR-0078, Two), so the saved
folder is not kept to restart from.

## Left Open

- The stable-python-api and desktop-client deltas for the settings routes of
  tasks 2 and 4 were not written with those slices; they were added here against
  the shipped code.
- A real notification on a signed macOS build was not exercised by any test:
  the unit tests cover the fixed text and the callback seam, not the operating
  system's delivery.
