"""The frozen engine's entry point: the `openkos` command line, under its own name.

PyInstaller freezes this script (`openkos-engine.spec`); the executable it builds
accepts exactly what the `openkos` console script accepts, so the desktop shell
runs it as `openkos-engine serve --workspace <dir>`.
"""

from openkos.cli.main import app

if __name__ == "__main__":
    app(prog_name="openkos")
