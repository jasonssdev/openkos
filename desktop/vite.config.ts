// Builds the web view from ui/ into dist/, the shell's frontendDist
// (src-tauri/tauri.conf.json), and runs the tests of the client and the page.
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const here = (path: string) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  root: here("./ui"),
  build: {
    outDir: here("./dist"),
    emptyOutDir: true,
    // The web view is a current WebKit; nothing older loads this page.
    target: "safari16",
  },
  test: {
    root: here("."),
    include: ["client/**/*.test.ts", "ui/**/*.test.ts"],
  },
});
