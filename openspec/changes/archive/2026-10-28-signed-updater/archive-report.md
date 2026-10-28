# Archive Report: signed-updater

**Archived**: 2026-10-28
**Archive folder**: `openspec/changes/archive/2026-10-28-signed-updater/`
**Store mode**: filesystem merge; written in place in the archive.
**Tracking issue**: #1624.

## Final State At Close

- The app checks the latest GitHub release's `latest.json` when it opens,
  unless the person turned that off in Settings > Updates; the choice is
  `updates.json` in the shell's configuration directory.
- A version on offer is named with its notes in Settings and at the foot of the
  sidebar; "Install and restart…" opens a confirmation that starts on "Not
  now", and the shell installs only the version shown, after the updater
  verifies its signature and that it was signed for that version.
- The release job runs in the `release` environment, refuses to start without
  the signing secrets or with the placeholder public key, and uploads the
  archive, its signature and the feed. The archive is made by `build-app.sh`
  from the re-signed, verified app (ADR-0081).
- The public key in `tauri.conf.json` is a marked placeholder until the owner
  generates the real pair.

## Specs Synced

| Domain | Action | Details |
|--------|--------|---------|
| desktop-shell | Updated | 2 ADDED requirements appended: the check only while allowed; the install only of the confirmed version. |
| desktop-settings | Updated | 1 ADDED requirement appended: the Updates card and the confirmation. |
| desktop-client | Updated | 1 ADDED requirement appended: the update command wrappers. |
| desktop-packaging | Updated | 1 ADDED requirement appended: the release build signs the archive made from the verified app. |

### Heading Name-Match

Every `### Requirement:` and `#### Scenario:` heading of each delta appears
exactly once in its living spec after the merge, and none was present before.
No MODIFIED, REMOVED or RENAMED headings.

## Requirement-vs-Code Check

The requirements were written against the shipped code, not the task list. The
CSP and `createUpdaterArtifacts` statements follow ADR-0081, not ADR-0079's
wording, and say so.

## Left Open

- No update has been installed end to end: that needs the real key and a
  published release.
- Whether macOS asks for Gatekeeper approval again after the updater replaces
  an ad-hoc signed app is not measured.
