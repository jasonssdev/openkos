# Proposal: add-progress-and-queue

Refs #1696, #1697.

## Intent

A single file takes minutes to add and the Add files screen showed `0 of 1` for
all of it, and left no way to hand over more files meanwhile.

## Scope

1. Progress inside a file: the extraction's existing phase hook (`on_progress`,
   `PhaseHook`) is read by the ingest run, which reports `parts_done` of
   `parts_total` on `IngestProgress` and on the local API's ingest job. The
   screen shows a bar, "n of m parts read", the time on this file, and an honest
   "this can take several minutes per file" line.
2. Queue: the engine allows one active ingest, so the queue is client-side. Files
   put in during a run are quoted and agreed to as usual, wait as one-file runs
   built from the agreed quote's own token entries, and start in order. Waiting
   files can be removed; Stop leaves them.

## Deltas

- `desktop-add-files`: two ADDED requirements.
- `local-http-api`: MODIFIED ingest job requirement (two fields) and one scenario.
- `stable-python-api`: MODIFIED `run_ingest` requirement (`progress` counts).
