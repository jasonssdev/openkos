# The desktop design system

How the desktop app looks and why. [`docs/brand.md`](../docs/brand.md) owns the identity (palette, isotype, wordmark); this file owns how the app's screens use it. The tokens are in [`ui/tokens.css`](ui/tokens.css), the component styles in [`ui/system.css`](ui/system.css), the components in [`ui/system/`](ui/system/). The screens stay plain DOM over a tested view model ([ADR-0066](../docs/adr/0066-the-desktop-screens-are-plain-dom-over-a-tested-view-model-with-no-ui-framework.md)).

## See it

The gallery shows every component in every state, and the app's real To review, Status and Ask screens (`ui/review/render.ts`, `ui/status.ts`, `ui/ask/`) on made-up data:

```sh
pnpm --dir desktop install
pnpm --dir desktop dev          # then open http://localhost:5173/gallery.html
```

It needs no engine and no Rust; any browser works. It is not an input of `pnpm build`, so the app never ships it. Dark follows the system appearance; switch the OS to light (or emulate `prefers-color-scheme` in the browser's dev tools) to see light.

## Principles

- **Calm.** Whitespace and type carry the hierarchy, not boxes. Separate with 1 px hairlines and a one-step change of surface; a shadow only on what floats (a dialog).
- **Light: few things at once, the rest on request.** A view shows what the person came for and holds the rest back. Prefer whitespace to a border, one card to two, and a disclosure ("Show details", "Show the folder") to a fact that is rarely needed; a badge or a callout appears only when it says something the text beside it does not. When a view feels busy, remove before restyling. (From the maintainer's review of the gallery: "professional, but still a bit overloaded".)
- **One primary per view, for an action.** Everything else is secondary or quiet. When the user *answers a question* ("Are these the same?"), every answer has the same weight: the app does not nudge a judgment it is not entitled to make.
- **Color never stands alone.** Every tone is a symbol and a word as well as a color.
- **Keyboard first.** Every control is a native button or input, or a documented ARIA pattern; focus is always visible and returns where it was.
- **System fonts only.** The app is offline; nothing is downloaded.

## Type

System sans for UI (`-apple-system`, SF Pro on macOS), system monospace for the wordmark, paths, ids and model names. Two weights only, as `docs/brand.md` says: regular 400 and medium 500. Hierarchy comes from size and color, not bold.

| Step | Size / line height | Use |
| --- | --- | --- |
| `xs` | 12 / 16 | badges, counts |
| `sm` | 13 / 20 | meta lines, hints, small buttons |
| `md` | 14 / 22 | body, controls (the default) |
| `lg` | 16 / 24 | card and dialog titles, the wordmark |
| `xl` | 20 / 28 | page titles |
| `2xl` | 24 / 32 | the question being asked |

Prose stops at `--measure` (68ch).

## Space, density, radii

A 4 px grid: `--space-1` (4) to `--space-16` (64). Controls are 36 px (`md`) or 28 px (`sm`) tall. The sidebar is 15rem; a list pane 22rem. Radii are small: 4 (skeleton lines), 6 (controls, rows), 12 (cards, callouts), 16 (dialogs); pills are fully round.

## Color roles

Dark is the default. Derived values are mixes of two brand colors, stated beside each token in `tokens.css`.

| Role | Dark | Light |
| --- | --- | --- |
| `surface-0` canvas | Ink `#14172b` | Paper `#ecedf5` |
| `surface-1` sidebar, cards | `#1a1d31` (Ink + 3% Paper) | `#f7f8fb` (Paper + 60% white) |
| `surface-2` hover, selected, inset | `#232639` (Ink + 7% Paper) | `#e3e4ed` (Paper + 4% Ink) |
| `surface-3` dialogs | `#2e3143` (Ink + 12% Paper) | `#ffffff` |
| `text-primary` | Paper | Ink |
| `text-secondary` | `#b6b8c2` (Paper 75%) | `#3f4253` (Ink 80%) |
| `text-tertiary` | `#9a9ca8` (Paper 62%) | `#555768` (Ink 70%) |
| `border-subtle` hairlines | `#303345` (Paper 13%) | `#d6d8e1` (Ink 10%) |
| `border-strong` control edges | `#6a6d7c` (Paper 40%) | `#808290` (Ink 50%) |
| `accent` fill | Indigo `#6366e8` | Indigo |
| `accent-hover` | `#5b5ed5` (Indigo + 10% Ink) | `#575acc` (Indigo + 15% Ink) |
| `accent-text` links, focus | `#8c8eec` (Indigo + 30% Paper) | `#4f52b9` (Indigo + 25% Ink) |
| `on-accent` | `#ffffff` | `#ffffff` |

Tones (`neutral`, `info`, `warning`, `success`, `error`) each have a foreground and a background tinted 12% over `surface-1`. In dark the foregrounds are the brand's state colors (Indigo via `accent-text`, Amber, `#57ab5a`, `#f47067`); in light they are darkened toward Ink so they stay readable on Paper.

Symbols: neutral `•`, info `i`, warning `!`, success `✓`, error `✕`.

**Contrast.** [`ui/tokens.test.ts`](ui/tokens.test.ts) measures every pair below, and more, in both modes; it fails when a changed token drops one under AA (4.5:1 for text, 3:1 for focus rings and control edges).

| Pair | Dark | Light |
| --- | --- | --- |
| `text-primary` on `surface-0` / `surface-3` | 15.16 / 11.00 | 15.16 / 17.69 |
| `text-secondary` on `surface-0` / `surface-3` | 8.95 / 6.49 | 8.51 / 9.93 |
| `text-tertiary` on `surface-0` / `surface-3` | 6.48 / 4.70 | 6.10 / 7.12 |
| `accent-text` on `surface-0` / `surface-2` | 6.06 / 5.11 | 5.56 / 5.12 |
| `on-accent` on `accent` / `accent-hover` | 4.57 / 5.24 | 4.57 / 5.62 |
| tone foreground on its background (info, warning, success, error, neutral) | 5.00, 6.07, 4.89, 4.94, 7.55 | 5.25, 5.30, 4.95, 5.02, 7.84 |
| `tone-error-fg` on `surface-2` (destructive button) | 5.23 | 4.71 |
| `focus-ring` on `surface-3` (its weakest surface) | 4.40 | 6.48 |
| `border-strong` on `surface-1` | 3.24 | 3.59 |

Brand Indigo itself is 3.87:1 on Ink, so it is used as a fill (with white text) and never as text on dark; `accent-text` is the readable tint.

## Borders, elevation, focus, motion

- Hairlines (`--hairline`, 1 px, `border-subtle`) separate regions (the sidebar, a list pane); `border-strong` marks the edge of a control that needs one (inputs, secondary buttons). Cards, empty states and choices have no border.
- Elevation is a surface step: a card is `surface-1` on the canvas. `--shadow-overlay` is for dialogs only; `--shadow-raised` is barely there.
- Focus: a 2 px `focus-ring` outline, 2 px out, on every control; headings that take focus programmatically show it only for keyboard focus.
- Motion: 120 ms color and background transitions, the spinner, and the skeleton's pulse. Nothing moves position. With reduced motion, transitions are instant, the pulse stops and the spinner slows.

## States

- **Loading.** First load shows a skeleton of what is coming (`skeleton`); a known wait shows a spinner with what is running ("Checking 3 pages…").
- **Progress.** A download whose size is known shows a progress bar (`progressBar`, a native `progress`) with the numbers beside it in words; the bar alone never carries them.
- **Busy.** The control that started the work keeps focus, shows a spinner and its running label ("Combining…"), and ignores further clicks (`aria-busy`, `aria-disabled`); controls that would conflict are disabled.
- **Empty.** Says what is empty, why, and the one thing to do (`emptyState`).
- **Error.** An error callout in the flow, saying what did and did not happen, with a retry.
- **Offline.** The engine is unreachable: a warning callout saying the files are safe, a way to start it, and the sidebar's engine badge in the error tone.

## The app

`ui/index.html` loads `tokens.css` and `system.css`, and `ui/main.ts` draws the app shell into it: the sidebar holds the lockup, the navigation (**Ask**; **To review**, with a count of what its list shows, or what the status counted before the list is read; **Status**) and the engine's badge at its foot. The main region shows one view at a time. Every screen is drawn from the system; there are no other styles.

- **Status** (`ui/status.ts`): a page header; an Engine card with the engine's state in words and any detail behind "Show details"; a Your library card with the counts first, the folder behind "Show the folder", a skeleton while it waits for the engine and an error callout when the status cannot be read, "Check setup" while the AI models are not ready, and, while search is behind, the rebuild section (`ui/search/`): why, **Rebuild search**, then its progress with Stop and how it ended. Settings shows the same section under its cards.
- **Welcome** (`ui/setup/`) replaces everything when no library is chosen yet, with the navigation held back: a page header asking for a folder, one primary ("Create a new library") and one secondary ("Open an existing library"), and a refusal as an error callout with its report behind "Show details".
- **Setup** (`ui/setup/`) opens on its own, once per session, when the library's AI models are not ready, and from Status's "Check setup" while they are not: one card for the step in hand (install Ollama, then download the models, each model a row with its own download, progress and stop), an info callout when the memory is below the floor, and "You're ready" at the end. It is not in the navigation.
- **Ask** (`ui/ask/`) is on the system: a page header; the question field with one primary **Ask**, unavailable with a reason while the engine starts; then the shown question as a card. While the answer is on its way the card holds a spinner with the step it is on; then the answer as text, the pages it came from in the card's footer ("From:", each with a quiet **Keep only on this Mac**), and at most a few quiet notes under it (a search that may miss recent changes, pages left out). An outcome with no answer names itself in the card's meta line and says why in one sentence; a failure is an error callout, with **Try again** only when asking again can help. Earlier questions of the session wait behind "Earlier questions", in memory only. The screen never asks the engine to name pages marked Only on this Mac: an answer that drew on one stays private and is counted, never named (ADR-0068's default). Where the person may agree otherwise is an open decision (#1507). Its few layout rules are in `ui/ask/ask.css`.
- **To review** (`ui/review/`) is a list and a detail (`splitView`). The list pane holds the title, **Check all** when something waits for a check, one row per decision (what it is about, the question as its meta, a Changed badge only when it changed), and one row for the pages you combined. The detail shows one thing at a time: the open decision (the question, its pages compared side by side, each card ending with who can see it as a `segmented` choice from `ui/label/`, the answers, each with what it does), the check, a split, or the combined pages; with nothing open, an empty state says what to do. An identity asks "Are these the same?" first, with **Same thing** and **Different things** side by side at one weight; which pages, or what it is, is asked only after Same thing, in the first question's place, with Back. The model's reason waits behind "Why the AI model thinks so". Two pages that disagree show what doesn't match under the cards, each claim quoted; a newer decision shows each page's quote, in place of its excerpt, and its date. A decision the app cannot act on shows its plain sentence and "This can't be settled in the app yet."; its answers appear once the wording gives them, with no other change to the screen.

## Components

All in [`ui/system/`](ui/system/), each a function returning an element: `appShell`, `splitView`, `navItem` (with count), `lockup`/`isotype`, `pageHeader`, `listRow`/`uiList`, `uiCard`, `uiButton` (primary, secondary, quiet, destructive; `sm`/`md`; disabled; loading), `badge`, `callout`, `openDialog`/`dialogPanel` (modal, labelled, Escape, focus kept and returned, the rest inert), `segmented` (a radio group with arrow keys), `answerList` (the answers to a question, each with what it does and, when unavailable, why), `choiceList` (native checkboxes or radios in a labelled group), `compare`, `emptyState`, `spinner`, `skeleton`, `progressBar`, `textField`, `facts`, `disclosure` (a native `details`/`summary` for secondary content, closed unless asked; `ui-pre` sets a detail block inside one). A `navItem` can be listed but not offered (`disabled` with a `hint`): it stays focusable with `aria-disabled`, and choosing it does nothing. `el` and `redraw` (`ui/system/dom.ts`) are the two primitives under them: an element whose children are text, and a redraw that keeps focus on the same control. Every class is prefixed `ui-`; the layout classes a screen composes (`ui-pane`, `ui-detail`, `ui-question`, `ui-lead`, `ui-stack`, `ui-line`) are in `system.css` too.

Component behavior is tested in jsdom (a dev dependency; `// @vitest-environment jsdom` per file), so the view models keep running in plain Node. Components may be built ahead of the screens that use them and reviewed in the gallery first ([ADR-0069](../docs/adr/0069-the-desktop-components-are-a-design-system-built-ahead-of-screens-and-tested-in-jsdom.md), extending ADR-0066).
