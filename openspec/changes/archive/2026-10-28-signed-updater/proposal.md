# Proposal: signed-updater

## Intent

Refs #1624. ADR-0079 (Accepted): the desktop app updates itself through the
Tauri updater, with a feed on GitHub releases, an in-app prompt that never
installs silently, a check the person can turn off, the public key committed
and the private key and its password only in a protected `release`
environment that the `desktop-release` job runs in.

Success: an installed app learns of a newer release when it opens, unless the
person turned that off; it names the version and its notes; nothing is
downloaded until the person confirms; the archive it installs is the re-signed,
verified app the release built, signed with the project's key for that version.

## Scope

### In Scope

1. Shell: `tauri-plugin-updater` pinned to an exact version and used only from
   Rust; the remembered choice to check; the commands `update_settings`,
   `set_update_check`, `check_for_update` and `install_update`.
2. Page: a client for those commands, an Updates card in Settings and a quiet
   sidebar entry while a version is on offer; a check at launch.
3. Build and release: `build-app.sh --release VERSION`, `update_feed.py`, the
   `release` environment, the loud failure without the key, the uploads, the
   prerelease flag, and the same path on every pull request with a throwaway key.
4. Docs: the install page (the check, the off switch, the owner's key steps),
   the wording brief, the roadmap, the architecture, and ADR-0080 for what the
   follow-on settled.

### Out of Scope

- Generating the project's real key and creating the `release` environment:
  the owner's action, filed separately.
- An end-to-end update between two published releases: needs the real key.
- Windows and Linux archives.

## Approach

The page never talks to the plugin: as with the dialog and the notification
(ADR-0071, ADR-0078), the shell owns the plugin and exposes four commands, so no
capability is granted and the CSP stays loopback-only. The shell holds the
update it found and installs only the version the page sends back after the
person's confirmation.

## Interpretations of the ADR

- **`createUpdaterArtifacts`.** ADR-0079 turns it on but requires the archive
  to be made from the re-signed, checked app; the bundler archives before the
  re-sign. ADR-0080 settles the order: the build script makes and signs it.
- **The CSP.** ADR-0079 says the CSP must name the connection. The CSP governs
  the web view, and the request is the shell's, so it stays unchanged (ADR-0080).
- **The off switch.** "The check can be turned off" is a choice about the app,
  not a library, so it lives in the shell's configuration directory, next to
  the remembered workspace, and is changed from Settings > Updates.
