---
type: Reference
title: OpenKOS Brand
description: The OpenKOS visual identity — isotype, wordmark, color palette, typography, and usage rules.
tags:
  - openkos
  - brand
  - design-system
  - reference
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-15T02:00:00Z
sensitivity: public
---

# Brand

The identity reflects what OpenKOS is: **durable, local-first, calm, transparent, and developer-grade** — a tool you trust with a decade of your thinking, not AI hype. **Dark is the default mode.**

## Isotype

The mark is two linked nodes: a **Knowledge Object connected to another**. It is the knowledge model reduced to its irreducible essence, which is why it survives as a silhouette at any size and in a single color.

- The larger **indigo** node is the knowledge core.
- The smaller **amber** node is what connects to it (amber is also the "freshness / new" color across the product).
- The **link** is the relationship — the heart of the graph.

It is deliberately just two shapes and a link, so it reads at 16 px, in a macOS menu bar, in a favicon, and in a terminal. Reference SVG (48×48):

```svg
<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
  <line x1="18" y1="30" x2="32" y2="18" stroke="#6366E8" stroke-width="6" stroke-linecap="round"/>
  <rect x="7" y="19" width="22" height="22" rx="6" fill="#6366E8"/>
  <rect x="25.5" y="11.5" width="13" height="13" rx="4" fill="#E0A32E"/>
</svg>
```

For **monochrome** contexts (menu-bar template icons, print, sizes ≤ 24 px), render all three shapes in a single color (white on dark, ink on light). Keep clear space around the mark equal to the height of the small node.

## Wordmark

The logo is `openkos` — **monospace, lowercase, one color.** Monospace evokes the terminal, code, and the plain-text markdown that is the product's substrate; lowercase matches the command you actually type.

- **Primary (workhorse):** `openkos` in one color — paper on dark, ink on light. Use this everywhere by default; it is the only version that works in monochrome.
- **Hero variant:** the same wordmark with `kos` in **indigo**, tying the logotype to the isotype. Use only where there is color and space (website, README header) — never as the monochrome workhorse.

The serif and the camelCase wordmark are **not** used as the logo (serif clashes with the geometric isotype; camelCase is reserved for prose).

## Lockup

The primary horizontal lockup is the **isotype + `openkos`** monospace wordmark, one color. Keep the isotype's height roughly equal to the wordmark's cap height, with clear space between them equal to the small node's width.

## Naming

One coherent system, no contradictions:

- **Logo:** `openkos` (lowercase, monospace).
- **Command:** `openkos`.
- **Name in prose:** OpenKOS (Open Knowledge Orchestration System).

## Color palette

Five brand colors. Dark-first: `Ink` is the default surface.

| Color | Hex | Role |
| --- | --- | --- |
| Ink | `#14172B` | Primary dark surface (default background) |
| Paper | `#ECEDF5` | Light surface; text and marks on dark |
| Indigo | `#6366E8` | Brand accent — knowledge; links, primary actions, the core node |
| Amber | `#E0A32E` | Freshness / "new"; the satellite node; warnings |
| Slate | `#565A78` | Secondary text, borders, muted UI |

**Functional (state) colors**, used only to encode meaning in the CLI and UI — not part of the brand palette: **indigo** for info/citations, **amber** for warnings and freshness, **green** (`#57AB5A`) for success/added, **red** (`#F47067`) for errors. Color always pairs with a symbol or label so meaning survives in monochrome (see the CLI reference).

## Typography

- **Monospace** — the logo, the CLI, code, file paths, and terminal output. It carries the plain-text, local-first character of the project.
- **Sans-serif** — UI and documentation prose.
- Two weights only (regular 400, medium 500). Sentence case everywhere except the product name (OpenKOS) and the lowercase logo.

## Usage

**Do**
- Use the two-node isotype and the monospace lowercase wordmark.
- Default to the one-color logo; reserve the indigo-accent variant for color-rich hero contexts.
- Collapse the mark to a single color for menu bars, favicons ≤ 24 px, and print.
- Keep the defined clear space.

**Don't**
- Re-add rings, orbits, or strata to the mark (earlier drafts had them; they dissolve at small sizes).
- Recolor the mark arbitrarily, stretch, or rotate it.
- Use the serif or camelCase wordmark as the logo.
- Put the two-color wordmark in a monochrome context.
