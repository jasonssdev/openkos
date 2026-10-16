#!/usr/bin/env bash
# Freeze the engine with PyInstaller, one-directory (ADR-0056).
#
# Syncs a dedicated build environment from uv.lock (the `[api]` extra and the
# `freeze` dependency group, `openkos` installed non-editable) on a uv-managed
# CPython 3.14, runs desktop/engine/openkos-engine.spec, and verifies the
# interpreter the freeze carries. Output: build/freeze/dist/openkos-engine/.
#
# Usage: desktop/scripts/build-engine.sh
#        desktop/scripts/build-engine.sh --check-interpreter <frozen-engine-dir>
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="${repo_root}/build/freeze"
engine_dir="${build_dir}/dist/openkos-engine"
expected_lib="libpython3.14.dylib"

# The freeze must carry exactly one interpreter library, the 3.14 one, and no
# framework build: the packaging spike once shipped a 3.12 interpreter inside a
# "3.14" app without any error.
check_interpreter() {
  local dir="$1"
  local internal="${dir}/_internal"
  if [[ ! -d "${internal}" ]]; then
    echo "interpreter check: ${internal} is not a directory" >&2
    return 1
  fi
  local found=()
  local lib
  while IFS= read -r lib; do
    found+=("$(basename "${lib}")")
  done < <(find "${internal}" -maxdepth 1 -name 'libpython*.dylib')
  if [[ "${#found[@]}" -ne 1 || "${found[0]}" != "${expected_lib}" ]]; then
    echo "interpreter check: expected exactly ${expected_lib} in ${internal}," \
      "found: ${found[*]:-none}" >&2
    return 1
  fi
  if [[ -n "$(find "${dir}" -name 'Python.framework' -print -quit)" ]]; then
    echo "interpreter check: ${dir} carries a Python.framework;" \
      "expected the uv-managed ${expected_lib} only" >&2
    return 1
  fi
  echo "interpreter check: ${dir} carries ${expected_lib}"
}

if [[ "${1:-}" == "--check-interpreter" ]]; then
  check_interpreter "${2:?usage: build-engine.sh --check-interpreter <dir>}"
  exit
fi

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "build-engine.sh: the freeze targets macOS on Apple Silicon only" >&2
  exit 1
fi

# A virtual environment activated in the caller's shell would otherwise make uv
# warn and could mislead PyInstaller about which interpreter it freezes.
unset VIRTUAL_ENV

echo "==> syncing the build environment from uv.lock"
UV_PROJECT_ENVIRONMENT="${build_dir}/venv" uv sync \
  --project "${repo_root}" \
  --locked --no-dev --extra api --group freeze --no-editable \
  --managed-python --python 3.14

echo "==> freezing the engine"
"${build_dir}/venv/bin/pyinstaller" \
  --noconfirm --clean \
  --distpath "${build_dir}/dist" \
  --workpath "${build_dir}/work" \
  "${repo_root}/desktop/engine/openkos-engine.spec"

check_interpreter "${engine_dir}"
echo "==> frozen engine: ${engine_dir}/openkos-engine ($(du -sh "${engine_dir}" | cut -f1))"
