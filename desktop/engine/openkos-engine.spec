# PyInstaller build recipe for the frozen engine (ADR-0056).
#
# One-directory, never one-file: one-file unpacks to a temporary directory on
# every start, outside the signed app bundle. Built by
# `desktop/scripts/build-engine.sh`, which runs it from a dedicated environment
# synced from `uv.lock` (the `[api]` extra and the `freeze` dependency group),
# on a uv-managed CPython 3.14.
#
# `collect_all` brings each package's data files, native libraries and every
# submodule: `sqlite_vec` ships its loadable extension as package data, and
# `openkos` imports its local API lazily (inside `openkos serve`) and reads its
# templates as package data, neither of which import analysis can see.

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
for package in ("openkos", "sqlite_vec"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    ["openkos_engine.py"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # Build tooling that reaches the environment through PyInstaller itself;
    # the engine never imports it.
    excludes=["setuptools", "pkg_resources"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="openkos-engine",
    console=True,
    # The app bundle is re-signed as a whole after it is built (ADR-0056).
    codesign_identity=None,
    upx=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="openkos-engine",
    upx=False,
)
