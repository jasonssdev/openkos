// The contrast the design system promises (desktop/DESIGN.md), measured on the
// tokens themselves in both modes, so a changed color cannot quietly fail AA.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const CSS = readFileSync(new URL("./tokens.css", import.meta.url), "utf8");

function block(source: string): string {
  const start = source.indexOf("{") + 1;
  let depth = 1;
  for (let i = start; i < source.length; i++) {
    if (source[i] === "{") depth++;
    if (source[i] === "}" && --depth === 0) return source.slice(start, i);
  }
  throw new Error("unclosed block");
}

function declarations(body: string): Map<string, string> {
  return new Map(Array.from(body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g), (m) => [m[1] as string, (m[2] as string).trim()]));
}

const dark = declarations(block(CSS.slice(CSS.indexOf(":root"))));
const lightOnly = CSS.slice(CSS.indexOf("prefers-color-scheme: light"));
const light = new Map([...dark, ...declarations(block(lightOnly.slice(lightOnly.indexOf(":root"))))]);

function hex(tokens: Map<string, string>, name: string): string {
  const value = tokens.get(name);
  if (value === undefined) throw new Error(`undefined token ${name}`);
  const ref = /^var\((--[\w-]+)\)$/.exec(value);
  return ref ? hex(tokens, ref[1] as string) : value;
}

function luminance(color: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const v = Number.parseInt(color.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const SURFACES = ["--surface-0", "--surface-1", "--surface-2", "--surface-3"];
const TONES = ["neutral", "info", "warning", "success", "error"];

// [foreground, background, minimum]: 4.5 for text, 3 for focus rings and control edges.
const PAIRS: [string, string, number][] = [
  ...["--text-primary", "--text-secondary", "--text-tertiary"].flatMap((fg) => SURFACES.map((bg) => [fg, bg, 4.5] as [string, string, number])),
  ...["--surface-0", "--surface-1", "--surface-2"].map((bg) => ["--accent-text", bg, 4.5] as [string, string, number]),
  ["--on-accent", "--accent", 4.5],
  ["--on-accent", "--accent-hover", 4.5],
  ...TONES.flatMap((tone) =>
    [`--tone-${tone}-bg`, "--surface-0", "--surface-1"].map((bg) => [`--tone-${tone}-fg`, bg, 4.5] as [string, string, number]),
  ),
  ["--tone-error-fg", "--surface-2", 4.5],
  ...SURFACES.map((bg) => ["--focus-ring", bg, 3] as [string, string, number]),
  ...["--surface-0", "--surface-1"].map((bg) => ["--border-strong", bg, 3] as [string, string, number]),
];

describe.each([
  ["dark", dark],
  ["light", light],
])("the %s tokens", (_mode, tokens) => {
  it.each(PAIRS)("%s on %s reaches %d:1", (fg, bg, minimum) => {
    expect(contrast(hex(tokens, fg), hex(tokens, bg))).toBeGreaterThanOrEqual(minimum);
  });
});
