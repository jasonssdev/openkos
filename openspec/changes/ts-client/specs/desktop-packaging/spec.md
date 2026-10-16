# Delta for Desktop Packaging

## MODIFIED Requirements

### Requirement: The App Bundle Ships The Freeze Under Resources, Deep Ad-Hoc Signed

The repository MUST provide one command that builds the macOS app bundle with
the frozen engine as the resource `Contents/Resources/engine`, never under
`Contents/MacOS`. Before it bundles, the command MUST install the desktop
package from its committed lockfile without updating it and build the page into
`desktop/dist/`, and MUST fail if the built page has no `index.html`. After the
bundle is built the command MUST deep ad-hoc re-sign the whole `.app` and MUST
fail unless `codesign --verify --deep --strict` passes. It MUST fail unless the
engine inside the `.app` passes the interpreter check and the smoke check. It
MUST build a `.dmg` from the re-signed and verified `.app`. Every output MUST be
written under directories the repository ignores.

(Previously: the command bundled the static page under `desktop/ui/` and built
no page.)

#### Scenario: The app carries a verified frozen engine

- GIVEN a checkout on macOS with the Rust toolchain, the Tauri CLI, Node and pnpm
- WHEN a contributor runs `desktop/scripts/build-app.sh`
- THEN the page is built into `desktop/dist/` before the bundle
- AND `OpenKOS.app/Contents/Resources/engine/openkos-engine` exists
- AND `codesign --verify --deep --strict` passes on the `.app`
- AND the smoke check passes against the engine inside the `.app`
- AND a `.dmg` holding that `.app` is written

#### Scenario: A broken signature fails the build

- GIVEN the re-signed `.app` does not verify
- WHEN the build checks the signature
- THEN the build fails and no `.dmg` is written

#### Scenario: A page that did not build fails the build

- GIVEN the page build leaves no `desktop/dist/index.html`
- WHEN the build checks the page
- THEN the build fails before it bundles
