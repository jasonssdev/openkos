# Operating manual

This is an OpenKOS knowledge bundle. If you are an AI agent working on it, follow these conventions.

## What this is

A directory of markdown concept documents (Open Knowledge Format) compiled from the immutable sources in `raw/`. Each file is one Knowledge Object.

## Layout

- `raw/` — immutable original sources. **Never edit or delete these.**
- `sources/` — one summary per ingested source (`type: Source`).
- `concepts/`, `people/`, `decisions/`, … — Knowledge Objects grouped by type.
- `index.md` — the catalog; read it first to see what exists.
- `log.md` — append-only history of changes.
- `openkos.yaml` — config (model, review, default sensitivity, freshness window).

## Conventions

- **Reuse before creating.** Check `index.md` and update an existing concept rather than duplicating it. Prefer a specific type over `Entity`.
- **Stamp volatile facts.** Counts, versions, latencies, and statuses need an `(as of YYYY-MM-DD)` stamp. Timeless facts need none.
- **Preserve provenance.** Every derived object lists its `raw/` sources in `provenance`; derived knowledge never replaces its source.
- **Respect sensitivity.** Default is `private`. `confidential` objects must never be sent to a cloud model or included in an export. A derived object is at least as sensitive as its most sensitive source.
- **Link by path.** Connect objects with root-relative markdown links, e.g. `[Qdrant](/concepts/qdrant.md)`.
- **Stay OKF-conformant.** Frontmatter uses the OKF field set plus the OpenKOS layer (`id`, `status`, `version`, `freshness`, `sensitivity`, `provenance`).

## After changes

Update `index.md` and append to `log.md`. Consequential changes are proposed for review, not applied silently.
