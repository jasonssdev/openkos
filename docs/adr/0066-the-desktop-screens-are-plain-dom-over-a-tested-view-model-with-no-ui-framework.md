---
type: Decision
title: "ADR-0066: The desktop screens are plain DOM over a tested view model, with no UI framework"
description: The desktop app's screens are written in TypeScript against the DOM directly, split into a framework-free view model that holds every state transition and every word on screen, and thin render functions built from a few extracted components on the design tokens; no UI framework or runtime dependency enters the page.
status: Proposed
date: 2026-10-16
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-16T00:00:00Z
sensitivity: public
---

# ADR-0066: The desktop screens are plain DOM over a tested view model, with no UI framework

- **Status:** Proposed
- **Date:** 2026-10-16

## Context

The review screen is the first real screen of the desktop app ([roadmap](../roadmap.md#mvp-6--the-desktop-app)): a person who has never used a terminal settles the pending-decision queue on it. [ADR-0065](0065-the-desktop-web-view-is-typescript-built-with-vite-over-a-client-generated-from-the-openapi-snapshot.md) made the web view a TypeScript package built with Vite over a typed client with no runtime dependency, and left open how screens are written. The status page that proved it is a few dozen lines of `textContent` writes.

The review screen is larger: a list, one decision at a time with its evidence, previews and applies with echoed versions, a check (judging) with consent, progress and stop, splits from the merge history, and a message for every refusal the [wording brief](../decision-wording.md) lists. The forces:

- **The hard part is the state, not the markup.** Which question to ask, which options exist, what happens on a stale version, a busy library or an interrupted group: that is where a defect strands the person, and it is what must be tested. The markup is a handful of cards, buttons and notices.
- **Every word is fixed by the brief.** The copy must be testable as data, apart from any rendering.
- **The tests run in Node.** The client's vitest suite has no DOM environment, and adding one (jsdom, happy-dom) is a second dependency tree to pin for markup that is not where the risk is.
- **The page ships what the bundler emits.** ADR-0065 keeps every npm package a development dependency; a framework would be the first runtime dependency in the page and a release cadence the app must follow.
- **No full design system.** The user's direction is to extract components as this screen needs them, on the tokens of `desktop/ui/tokens.css`.

## Decision

We write the desktop screens against the DOM directly, in two layers, with no UI framework.

- **A view model with no DOM.** One module per screen holds the screen's state and its transitions as plain TypeScript over a narrow interface of the client (the methods it calls), and one holds the wording: pure functions from the API's data to the sentences, options and messages of the brief. Both are unit-tested in Node with a mocked client; they are where the behavior lives.
- **Thin render functions.** The screen is redrawn from the state on every change by functions that build elements with `document.createElement`, write every value with `textContent` (never markup), and wire events back to the view model's transitions. They hold no decision of their own.
- **Components are extracted functions, not a library.** A component (a button with a role, a notice with its symbol, a decision card, a page's evidence) is a function returning an element, styled only with the custom properties of `tokens.css`. Components are added when a screen needs one, not ahead of it.
- **No runtime dependency.** The page still ships only what Vite emits from our own sources.

## Consequences

- Easier: every transition and every word on the screen is tested without a browser or a DOM library; the vitest suite stays in Node.
- Easier: nothing outside the repository decides how the page renders, upgrades or breaks; the page's supply chain is unchanged.
- Easier: the render layer is replaceable. A later screen, or a later framework, can render the same view model.
- Harder: redrawing a section from state is ours to keep efficient and to keep focus stable; render functions must restore the focused control after a redraw, which a framework would do for us.
- Harder: the render functions are not unit-tested; their correctness rests on staying thin, on typechecking and on the participant session of the [MVP 6 preregistration](../../evals/desktop_app/PREREGISTRATION.md). Visual rendering is not checked in CI.
- Contributors write components as plain functions; a pattern that grows past a screen's needs is a sign to revisit this ADR, not to grow a framework by hand.

## Alternatives considered

- **A UI framework (React, Preact, Svelte, Solid, Lit).** Declarative rendering and focus handling come for free. Rejected for now: it is the page's first runtime dependency and release cadence, it moves the screen's logic into components that need a DOM environment to test, and the screen's risk is in its state and copy, which a framework does not test. A later ADR may adopt one over the same view model if the app grows past what thin render functions can carry.
- **Web components with custom elements.** No dependency, and encapsulated styling. Rejected: the shadow DOM separates the components from the tokens' custom properties only to have them passed back in, and custom elements add lifecycle code without testing the state any better.
- **A DOM test environment (jsdom or happy-dom) and tests of the render functions.** Rejected for this stage: a second dependency tree for markup that holds no decision; the view-model tests cover the behavior, and the exit-criteria session covers what a person sees.
- **Templates written as HTML strings.** Short to write. Rejected: every value would have to be escaped by hand, and one missed escape renders a page title as markup; `textContent` makes that impossible by construction.
