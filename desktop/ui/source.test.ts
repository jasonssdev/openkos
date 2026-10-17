// Guards on the page's source that no unit test of behaviour can see (ADR-0066):
// every value is written as text, and the styles use only the design tokens.
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const UI = new URL(".", import.meta.url).pathname;

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path);
    return entry.name.endsWith(".ts") && !entry.name.endsWith(".test.ts") ? [path] : [];
  });
}

describe("the page's source", () => {
  it.each(sources(UI).map((path) => [path.slice(UI.length), path]))("%s never writes markup or inline style", (_name, path) => {
    const text = readFileSync(path, "utf8");
    for (const sink of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", ".style."]) {
      expect(text, sink).not.toContain(sink);
    }
  });

  it.each(["system.css", "gallery.css"])("%s styles with the tokens only", (sheet) => {
    const css = readFileSync(join(UI, sheet), "utf8");
    const tokens = readFileSync(join(UI, "tokens.css"), "utf8");
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(css).not.toMatch(/\b(?:rgb|rgba|hsl|hsla|oklch|color-mix)\(/i);
    const defined = new Set(Array.from(tokens.matchAll(/(--[\w-]+)\s*:/g), (m) => m[1]));
    const used = Array.from(css.matchAll(/var\((--[\w-]+)\)/g), (m) => m[1]);
    expect(used.length).toBeGreaterThan(0);
    for (const name of used) expect(defined, name).toContain(name);
  });
});
