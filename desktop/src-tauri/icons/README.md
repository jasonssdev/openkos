# App icons

`icon.svg` (the brand isotype on an Ink rounded square, geometry from `docs/brand.md`) is the source of every raster file here. `icon-template.svg` is the single-color (white, transparent) variant for menu-bar and small uses.

Regenerate (no project dependency; macOS, Tauri CLI on PATH):

```sh
uv run --no-project --with cairosvg python -c \
  "import cairosvg; cairosvg.svg2png(url='icon.svg', write_to='/tmp/icon-1024.png', output_width=1024, output_height=1024)"
cd .. && cargo tauri icon /tmp/icon-1024.png -o icons
rm -rf icons/android icons/ios icons/Square*.png icons/StoreLogo.png icons/64x64.png
```

The last line drops the mobile and Windows Store sizes `cargo tauri icon` also emits; the app ships for macOS only.
