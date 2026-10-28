#!/usr/bin/env bash
# Build the macOS app with the frozen engine inside (ADR-0056).
#
#   1. build the web view's page into desktop/dist (Vite, from the pnpm lockfile;
#      ADR-0065), the shell's frontendDist;
#   2. freeze the engine (build-engine.sh);
#   3. bundle the shell with `cargo tauri build`, the freeze as the resource
#      Contents/Resources/engine (tauri.bundle.conf.json);
#   4. deep ad-hoc re-sign the whole .app and verify the signature;
#   5. check the interpreter of the engine INSIDE the .app;
#   6. smoke-check the engine inside the .app;
#   7. build the .dmg from that verified .app;
#   8. with --release, make the update archive from that same verified .app,
#      unpack it and verify the copy, then sign it with the updater key
#      (ADR-0079, ADR-0080).
#
# Any failure stops the build before the .dmg is written. Needs macOS on Apple
# Silicon, uv, Node and pnpm (desktop/package.json pins both), the Rust
# toolchain and the Tauri CLI (`cargo install tauri-cli --version "^2"
# --locked`). Outputs, all in ignored directories:
#   desktop/dist/
#   desktop/src-tauri/target/release/bundle/macos/OpenKOS.app
#   build/desktop/OpenKOS.dmg
#   build/desktop/OpenKOS.app.tar.gz and .sig   (--release only)
#
# --release VERSION builds the app as that version (semver, no leading `v`), so
# the updater compares it with the feed's, and needs the updater's private key
# and its password in TAURI_SIGNING_PRIVATE_KEY and
# TAURI_SIGNING_PRIVATE_KEY_PASSWORD. Both are checked before anything is built.
#
# Usage: desktop/scripts/build-app.sh [--release VERSION]
set -euo pipefail

usage="usage: desktop/scripts/build-app.sh [--release VERSION]"
release=""
if [[ $# -gt 0 ]]; then
  if [[ "$1" != "--release" || $# -ne 2 ]]; then
    echo "${usage}" >&2
    exit 2
  fi
  release="$2"
  if [[ ! "${release}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "build-app.sh: release version '${release}' is not semver (no leading v)" >&2
    exit 1
  fi
  if [[ -z "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
    echo "build-app.sh: a release needs the updater key in TAURI_SIGNING_PRIVATE_KEY" >&2
    exit 1
  fi
  if [[ -z "${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}" ]]; then
    echo "build-app.sh: a release needs the key's password in TAURI_SIGNING_PRIVATE_KEY_PASSWORD" >&2
    exit 1
  fi
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
web_dir="${repo_root}/desktop"
shell_dir="${repo_root}/desktop/src-tauri"
app="${shell_dir}/target/release/bundle/macos/OpenKOS.app"
bundled_engine="${app}/Contents/Resources/engine"
out_dir="${repo_root}/build/desktop"
dmg="${out_dir}/OpenKOS.dmg"

echo "==> building the page"
rm -rf "${web_dir}/dist"
(cd "${web_dir}" && pnpm install --frozen-lockfile && pnpm build)
if [[ ! -f "${web_dir}/dist/index.html" ]]; then
  echo "build-app.sh: the page did not build: desktop/dist/index.html is missing" >&2
  exit 1
fi

"${repo_root}/desktop/scripts/build-engine.sh"

echo "==> bundling the app"
rm -rf "${app}"
version_config=()
if [[ -n "${release}" ]]; then
  # The app's own version is what the updater compares with the feed's.
  version_config=(--config "{\"version\":\"${release}\"}")
fi
(cd "${shell_dir}" && cargo tauri build --ci --bundles app --config tauri.bundle.conf.json ${version_config[@]+"${version_config[@]}"})

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
unpacked=""
trap 'rm -rf "${staging}" ${unpacked:+"${unpacked}"}' EXIT
# `ditto` copies the bundle with its signature, extended attributes and modes.
ditto "${app}" "${staging}/OpenKOS.app"
ln -s /Applications "${staging}/Applications"
mkdir -p "${out_dir}"
hdiutil create -volname OpenKOS -srcfolder "${staging}" -format UDZO -ov "${dmg}"

if [[ -n "${release}" ]]; then
  archive="${out_dir}/OpenKOS.app.tar.gz"
  echo "==> making the update archive from the verified app"
  rm -f "${archive}" "${archive}.sig"
  # COPYFILE_DISABLE: no AppleDouble `._` files, which the updater would unpack
  # as ordinary files into the sealed bundle (ADR-0055). The app has no script
  # wrapper whose signature lives in an extended attribute, so nothing signed
  # is lost; the verification below is what proves it.
  COPYFILE_DISABLE=1 tar -czf "${archive}" -C "$(dirname "${app}")" OpenKOS.app
  # The archive must hold the app that was checked: unpack it and verify.
  unpacked="$(mktemp -d)"
  tar -xzf "${archive}" -C "${unpacked}"
  codesign --verify --deep --strict --verbose=2 "${unpacked}/OpenKOS.app"
  echo "==> signing the update archive"
  # Reads the key and its password from the environment; writes ${archive}.sig.
  cargo tauri signer sign --app-version "${release}" "${archive}"
  if [[ ! -s "${archive}.sig" ]]; then
    echo "build-app.sh: the update archive was not signed: ${archive}.sig is missing" >&2
    exit 1
  fi
  echo "==> update archive: ${archive} ($(du -sh "${archive}" | cut -f1))"
fi

echo "==> app: ${app} ($(du -sh "${app}" | cut -f1))"
echo "==> dmg: ${dmg} ($(du -sh "${dmg}" | cut -f1))"
