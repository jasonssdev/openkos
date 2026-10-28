# Desktop Packaging Specification (delta)

## ADDED Requirements

### Requirement: A Release Build Signs The Update Archive Made From The Verified App

`desktop/scripts/build-app.sh --release VERSION` MUST refuse, before building
anything, a version that is not semver without a leading `v`, and a missing
`TAURI_SIGNING_PRIVATE_KEY` or `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. It MUST
build the app as `VERSION`, and after the deep ad-hoc re-sign, the verification
and the engine smoke check MUST make `build/desktop/OpenKOS.app.tar.gz` from
that same `.app` with `COPYFILE_DISABLE=1`, unpack it and verify the copy with
`codesign --verify --deep --strict`, then sign it with
`cargo tauri signer sign --app-version VERSION` (ADR-0080). `createUpdaterArtifacts`
MUST stay off. CI's macOS bundle job MUST run this path on every pull request
with a key generated for the run, never the project's.

The `desktop-release` job MUST run in the `release` environment, read the two
signing secrets only there, fail before building when either is empty or
`tauri.conf.json` still carries the placeholder public key, never print a
secret, and upload the archive, its `.sig` and a `latest.json` written by
`desktop/scripts/update_feed.py` (the version, the notes, the time and the
`darwin-aarch64` url and signature) with the `.dmg`. A tag with a suffix MUST
be released as a prerelease, which `releases/latest` never offers.

#### Scenario: A release without the key builds nothing

- GIVEN `TAURI_SIGNING_PRIVATE_KEY` is unset
- WHEN `build-app.sh --release 0.7.0` runs
- THEN it exits 1 naming the variable, before the first build step

#### Scenario: The archive holds the app that was checked

- GIVEN a release build
- WHEN the archive is made
- THEN it is unpacked and its copy of the app passes `codesign --verify --deep
  --strict` before it is signed
