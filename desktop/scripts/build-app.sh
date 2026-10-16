#!/usr/bin/env bash
# Build the macOS app with the frozen engine inside (ADR-0056).
#
#   1. freeze the engine (build-engine.sh);
#   2. bundle the shell with `cargo tauri build`, the freeze as the resource
#      Contents/Resources/engine (tauri.bundle.conf.json);
#   3. deep ad-hoc re-sign the whole .app and verify the signature;
#   4. check the interpreter of the engine INSIDE the .app;
#   5. smoke-check the engine inside the .app;
#   6. build the .dmg from that verified .app.
#
# Any failure stops the build before the .dmg is written. Needs macOS on Apple
# Silicon, uv, the Rust toolchain and the Tauri CLI (`cargo install tauri-cli
# --version "^2" --locked`). Outputs, all in ignored directories:
#   desktop/src-tauri/target/release/bundle/macos/OpenKOS.app
#   build/desktop/OpenKOS.dmg
#
# Usage: desktop/scripts/build-app.sh
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
shell_dir="${repo_root}/desktop/src-tauri"
app="${shell_dir}/target/release/bundle/macos/OpenKOS.app"
bundled_engine="${app}/Contents/Resources/engine"
out_dir="${repo_root}/build/desktop"
dmg="${out_dir}/OpenKOS.dmg"

"${repo_root}/desktop/scripts/build-engine.sh"

echo "==> bundling the app"
rm -rf "${app}"
(cd "${shell_dir}" && cargo tauri build --ci --bundles app --config tauri.bundle.conf.json)

if [[ -e "${app}/Contents/MacOS/engine" ]]; then
  echo "build-app.sh: the engine landed in Contents/MacOS; it must be a resource" >&2
  exit 1
fi

echo "==> deep ad-hoc re-signing ${app}"
codesign --force --deep --sign - "${app}"
codesign --verify --deep --strict --verbose=2 "${app}"

"${repo_root}/desktop/scripts/build-engine.sh" --check-interpreter "${bundled_engine}"

echo "==> smoke-checking the engine inside the app"
"${repo_root}/build/freeze/venv/bin/python" "${repo_root}/desktop/scripts/smoke_engine.py" \
  "${bundled_engine}/openkos-engine"

# Running the engine must not have written into the sealed bundle.
codesign --verify --deep --strict "${app}"

echo "==> building the dmg"
staging="$(mktemp -d)"
trap 'rm -rf "${staging}"' EXIT
# `ditto` copies the bundle with its signature, extended attributes and modes.
ditto "${app}" "${staging}/OpenKOS.app"
ln -s /Applications "${staging}/Applications"
mkdir -p "${out_dir}"
hdiutil create -volname OpenKOS -srcfolder "${staging}" -format UDZO -ov "${dmg}"

echo "==> app: ${app} ($(du -sh "${app}" | cut -f1))"
echo "==> dmg: ${dmg} ($(du -sh "${dmg}" | cut -f1))"
