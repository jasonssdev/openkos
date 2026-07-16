# Examples

## `vector-db-demo`

A small, hand-written OpenKOS bundle used as a **reference** and a **test fixture**. It shows what the MVP 1 `ingest` should produce: two immutable sources in `raw/`, a per-source summary (`sources/`), and Knowledge Objects — concepts, a person, and a decision — carrying provenance, `as of` freshness stamps, sensitivity labels, and typed markdown links, plus `index.md`, `log.md`, `openkos.yaml`, and `AGENTS.md`.

Open it in any markdown editor to see the shape of a bundle, or treat it as the expected output when building the compiler. Note the `sensitivity: confidential` objects (the person and the source summary) — by the high-water-mark rule, anything derived from them inherits that level.
