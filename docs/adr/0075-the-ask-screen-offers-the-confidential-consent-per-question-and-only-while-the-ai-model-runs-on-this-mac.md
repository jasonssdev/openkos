---
type: Decision
title: "ADR-0075: The Ask screen offers the confidential consent per question, and only while the AI model runs on this Mac"
description: A withheld answer carries a Show this answer button that asks the same question again with include_confidential, after reading the model settings and finding the AI model local; there is no standing setting, and with a cloud model the consent is never offered.
status: Proposed
date: 2026-10-17
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-17T00:00:00Z
sensitivity: public
---

# ADR-0075: The Ask screen offers the confidential consent per question, and only while the AI model runs on this Mac

- **Status:** Proposed
- **Date:** 2026-10-17

## Context

[ADR-0068](0068-asking-through-the-api-applies-the-query-disclosure-rule-with-one-consent.md) made `include_confidential` one consent with two effects: pages marked Only on this Mac may be named in the answer, and they may reach a model that is not on this Mac. It left the app to offer that consent and to show where the model runs beside it. The Ask screen never sent it, so an answer that drew on such a page was withheld, with the line "This can't be changed in the app yet" (#1507). Candidates were a library setting, a per-question choice, or both.

Without the consent, a cloud model never sees these pages: they are left out of retrieval and the answer carries a count. An answer is withheld only when the prompt held one, which happens only through the local exemption, so a withheld answer always came from a model on this Mac. The settings route already reports `is_local`.

## Decision

- The consent is offered per question, on the withheld answer: **Show this answer**. It asks the same question again with `include_confidential`, in place of the withheld answer.
- Before it sends anything the screen reads the model settings. If the AI model is not on this Mac, or the settings cannot be read, it sends nothing and says "Your AI model isn't on this Mac, so these pages stay private."
- There is no standing setting and no way to give the consent while a cloud model is on. Pages marked Only on this Mac that a cloud model left out stay left out, with their count. A person who wants them in an answer switches to a model on this Mac first.
- No API change: the ask request already carries `include_confidential`, and the settings already carry `is_local`.

## Consequences

**What becomes easier:**

- The consent is given for one answer, where the person is looking at why it was withheld, and it cannot outlive that answer or a change of model.
- The principle that Only on this Mac never leaves the device holds in the app by construction: the one path that sends the consent first checks the model is local.

**What becomes harder, and what we accept:**

- A person who always wants these answers asks twice. A standing setting would save that click but would stay on after a switch to a cloud model, which is the case the consent must never reach.
- The check reads the settings just before the ask, not atomically with it. The settings change only through the app, which does not run an ask and a settings change together in practice; the engine's own resolver remains the boundary for a hand-edited file.

## Alternatives considered

- **A library setting.** A standing consent persists across a switch to a cloud model, so every later answer would send these pages away. It needs its own guard and a place in `openkos.yaml` for a decision made once per answer.
- **Both.** The setting's risk with the per-question convenience; more to explain on a screen the person finds crowded.
- **Offer it with a cloud model, with a warning.** Sends pages marked Only on this Mac to a provider, against the principle that they never leave the device.
