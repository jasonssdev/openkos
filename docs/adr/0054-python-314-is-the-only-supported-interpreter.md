---
type: Decision
title: "ADR-0054: Python 3.14 is the only supported interpreter"
description: The Python floor rises from 3.12 to 3.14 and 3.14 becomes the only tested interpreter, so development, CI, release and the frozen desktop app all run one interpreter; users on 3.12 or 3.13 stay on 0.5.5.
status: Proposed
date: 2026-10-14
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-10-14T00:00:00Z
sensitivity: public
---

# ADR-0054: Python 3.14 is the only supported interpreter

- **Status:** Proposed
- **Date:** 2026-10-14

## Context

OpenKOS declared `requires-python = ">=3.12"`, ran its tests on 3.12, 3.13 and 3.14, pinned the quality, build and release jobs to 3.12, pinned MyPy to `python_version = "3.12"`, and developed locally on 3.13 through `.python-version`. Five interpreters were in play and none of them was the one the next arc ships.

MVP 6 (the desktop app) freezes exactly one interpreter into the application. A packaging spike on 2026-10-14 re-validated 3.14: on 3.14.6, PyInstaller 6.22.3 froze the engine, `sqlite_vec` loaded in the frozen build, `run_query` answered, and startup was 0.078 s median.

The matrix also costs real time. CI duration is dominated by the full suite under coverage, run once per interpreter, and 3.12 and 3.13 add wall-clock time without testing anything the desktop app will run. Interpreter drift has shipped bugs before: `Path.exists()` re-raises `EACCES` on 3.12 and 3.13 but suppresses it on 3.14, which forced a `stat()` workaround.

The maintainer wants a single interpreter.

## Decision

Python 3.14 is the only supported interpreter. `requires-python` is `>=3.14`, the only trove classifier is 3.14, MyPy analyses for 3.14, `.python-version` is 3.14, every CI and release job uses 3.14, and the test matrix has one entry. A test pins that these sources agree, so a future drift goes red.

Ruff infers its target from `requires-python`, so the `UP` rules now modernise for 3.14. Dead compatibility branches for older interpreters are removed.

## Consequences

**What becomes easier:**

- One interpreter in development, CI, release and the frozen desktop app: a bug reproduces everywhere or nowhere.
- CI runs the coverage suite once instead of three times.
- Language and standard-library features of 3.14 are usable without a fallback.

**What becomes harder, and what we accept:**

- **A user-visible break.** `pip install openkos` on Python 3.12 or 3.13 resolves to 0.5.5, the last release that supports them, and does not receive later versions. They must move to Python 3.14 to upgrade.
- Contributors need Python 3.14, which `uv` installs on request.
- Nothing is tested on 3.12 or 3.13 from here on, so a regression there would not be caught, and none is promised against.

## Alternatives considered

- **Keep the three-version matrix.** This preserves the widest reach, but it keeps the slow CI, the interpreter-specific workarounds, and a tested set that differs from the interpreter the desktop app ships. Rejected.
- **Measure coverage on one version only, and run the rest of the matrix without it.** This recovers most of the CI time but keeps three supported interpreters, the user's objection, and the drift between them. Rejected.
- **A 3.13 floor.** This drops only the oldest interpreter, but the spike validated 3.14 and not 3.13, and it would still leave two interpreters to reason about. Rejected.
