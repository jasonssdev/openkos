# Proposal: size-context-window-at-setup

Refs #1703 (first slice only; the issue stays open).

## Intent

One `context_window` for every machine truncates long transcripts on large
machines and cannot be raised safely on small ones. Size it once, at setup,
from the machine's memory.

## Scope

1. `config.sized_context_window` is a pure function of total memory
   (`< 32 GiB` or unknown: 12288; `>= 32 GiB`: 16384; `>= 64 GiB`: 24576),
   floored at `minimum_context_window` and capped by a known model limit.
2. `init` reads memory through `application.setup.physical_memory_bytes` (the
   probe the setup screen already uses) and writes the result with
   `write_config`, through a third template placeholder.
3. Latency cost stated next to the setting in `docs/cli.md` and the template.
4. Delta: MODIFIED requirement "Static openkos.yaml Template".

## Out of scope

`DEFAULT_CONTEXT_WINDOW` and absent-key resolution for existing workspaces are
unchanged; nothing is computed at run time. Demoting whole Source bodies when
derived pages exist changes retrieval output and needs a pre-registered
measurement.
