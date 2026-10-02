---
type: Decision
title: "ADR-0039: The stable Python API ships with a desktop app as its first client, not with interoperability"
description: MVP 5 is narrowed to OKF export, OKF import and sensitivity enforcement at the export boundary; the stable Python API and the third-party extension points leave it. A new MVP 6, the desktop app, builds a local API and the stable Python API together with a real first client; extension points move to the Horizon, built on that API. The shell technology is deferred to its own ADR.
status: Accepted
date: 2026-10-01
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-01T00:00:00Z
sensitivity: public
---

# ADR-0039: The stable Python API ships with a desktop app as its first client, not with interoperability

- **Status:** Accepted
- **Date:** 2026-10-01

## Context

The roadmap placed a stable Python API and extension points for third-party producers and consumers in MVP 5, alongside OKF export and import. Three forces argue against that grouping.

- **Export and import do not need a public API.** Both can be built on the internal `application/` services, as the MCP server was. Nothing in them requires those services to be declared stable.
- **A public API is a different kind of commitment.** A feature can be corrected in the next release; a public API is a compatibility promise with a semver policy and a deprecation path. Freezing it before import has pressured the services, and while OKF itself is v0.2 and pre-1.0, would freeze a shape we have not yet learned.
- **MVP 5 is already full.** Import is cross-bundle entity resolution, an arc of work in its own right.

Separately, the project's stated audience includes non-technical users, and today the main barrier for them is the terminal and the installation (Python, `uv`, a local model runtime, multi-GB models, `openkos.yaml`). "Human curates, engine maintains" currently means `[y/N]` prompts in a terminal. A desktop app is the first consumer of a local API that MCP cannot serve, which is the condition the roadmap set for promoting a local REST API out of the Horizon.

## Decision

- MVP 5 covers OKF export first, OKF import second, and sensitivity enforcement at the export boundary. It no longer carries the stable Python API or the extension points.
- A new MVP 6, The Desktop App, delivers a desktop application (a local web UI wrapped in a native shell, with packaging as a core deliverable), a local API the app talks to, and a stable Python API over the application services, versioned and declared public, with the desktop app as its first client. The API is shaped by a real consumer rather than designed in a vacuum.
- Extension points for third-party producers and consumers move to the Horizon, built on the stable Python API.
- The shell technology (Tauri, Electron, or another) is not decided here. It gets its own ADR when MVP 6 starts; either choice needs the Python engine as a sidecar process.
- Every UI stays a thin adapter over the same local engine; adding it never touches the core.

## Consequences

- MVP 5 is smaller and shippable without a compatibility promise.
- The stable API is designed against a concrete client, which makes its first shape more likely to survive; it also arrives later than third-party authors might want.
- Third-party extension work waits until after MVP 6.
- MVP 6 takes on packaging and model setup, which are new kinds of work for this project (installers, signing, per-OS behavior), and may need to be split once it starts.
- The local API adds a network surface on localhost, so its trust boundary and the sensitivity disclosure gate need their own treatment when it is designed.
- The Horizon items for a graphical knowledge explorer and graph visualization are unchanged.

## Alternatives considered

- **Keep the API in MVP 5.** Rejected: it freezes the services before import has exercised them and couples an irreversible promise to an arc that does not need it.
- **A separate Platform MVP of API and extension points, with no client.** Rejected: an API with no first-party consumer is designed in a vacuum, and nobody would be waiting on it.
- **A GUI before any stable API.** Rejected: the app would either import unstable internals or define an API implicitly; making the API a declared deliverable of the same arc keeps the two honest.
