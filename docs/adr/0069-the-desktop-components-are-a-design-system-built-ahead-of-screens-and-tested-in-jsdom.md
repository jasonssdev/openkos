---
type: Decision
title: "ADR-0069: The desktop components are a design system built ahead of the screens, and tested in jsdom"
description: The desktop app's components live in one design system under desktop/ui/system/, built and reviewed in a gallery before the screens that use them, with their DOM behavior tested in jsdom as a development dependency; this extends ADR-0066, whose view models and plain-DOM render functions stand.
status: Proposed
date: 2026-10-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# ADR-0069: The desktop components are a design system built ahead of the screens, and tested in jsdom

- **Status:** Proposed
- **Date:** 2026-10-16
- **Extends:** [ADR-0066](0066-the-desktop-screens-are-plain-dom-over-a-tested-view-model-with-no-ui-framework.md)

## Context

[ADR-0066](0066-the-desktop-screens-are-plain-dom-over-a-tested-view-model-with-no-ui-framework.md) made the desktop screens plain DOM over a view model tested in Node, and made two narrower calls for that stage: components are extracted "when a screen needs one, not ahead of it", and a DOM test environment (jsdom or happy-dom) was rejected because the markup held no decision.

On the first hands-on run the maintainer judged the app's look unacceptable: brand colors without hierarchy, layout or considered components ([#1477](https://github.com/jasonssdev/openkos/issues/1477)). The decision was to build a real design system before more screens are built. That changes both forces ADR-0066 weighed:

- **Components are now designed, not extracted.** An app shell, list rows, cards, four button variants, callouts, a dialog, a segmented choice and the loading, empty, error and offline states are drawn together, so they read as one system, and reviewed in a gallery on made-up data before a screen adopts them. Extracting them one screen at a time is what produced the prototype look.
- **The components now hold behavior.** A dialog keeps focus inside, returns it on close and makes the rest inert; a segmented choice is a radio group driven by arrow keys; a busy button ignores clicks and says what runs; an unavailable nav item stays focusable and inert. That is behavior a person relies on, and it is not in any view model, so "the markup holds no decision" no longer holds for this layer.

## Decision

- **One design system, under `desktop/ui/system/`.** Its components are plain functions returning elements, styled only by `ui-`-prefixed classes in `desktop/ui/system.css` over the tokens of `tokens.css`, and described in `desktop/DESIGN.md`. They may be built ahead of the screens that use them; the gallery (`desktop/ui/gallery.html`, a development page that is not an input of the build) is where they are reviewed first.
- **Their DOM behavior is tested in jsdom.** jsdom is a development dependency; a test file opts in with `// @vitest-environment jsdom`, so the view models and the client keep running in plain Node. Render functions of a screen may be tested the same way where they carry focus or disclosure behavior.
- **Everything else in ADR-0066 stands.** View models with no DOM hold every transition and every word; render functions write text only and decide nothing; no UI framework and no runtime dependency enters the page.
- **Screens move onto the system one at a time.** A screen not yet moved keeps its older styles under a `ui-legacy` scope, where the system's resets do not reach it, and the older classes go when nothing uses them.

## Consequences

- Easier: a screen is assembled from components that were already reviewed together, so the app reads as one product and a restyle is a change to one stylesheet.
- Easier: focus handling, keyboard patterns and busy states are tested where they live, not only through the participant session.
- Harder: jsdom is a second dependency tree to pin and update, though only in development; the page's supply chain is unchanged.
- Harder: a component built ahead of a screen can turn out wrong for it. The gallery and the first screen to adopt it are the check, and a component no screen adopts within its arc should be removed rather than kept.
- jsdom is not a browser: layout, contrast and visual rendering are still not checked in CI (contrast is measured from the tokens by `tokens.test.ts`).

## Alternatives considered

- **Keep extracting components per screen (ADR-0066 as written).** Rejected: it produced the look the maintainer rejected, and it gives no place to review the system as a whole.
- **happy-dom instead of jsdom.** Another DOM implementation for tests. Not chosen: jsdom is already pinned and the component suite runs on it; nothing measured here favors a switch, and the choice is cheap to revisit because tests opt in per file.
- **A component library or a UI framework.** Rejected for the reasons in ADR-0066: the page's first runtime dependency and release cadence, for behavior a few tested functions carry.
- **Browser tests (Playwright) for the components.** Closer to the real web view. Rejected for this stage: a browser download in CI for a handful of components whose behavior jsdom exercises; it stays the option if a defect shows jsdom diverging from WebKit.
