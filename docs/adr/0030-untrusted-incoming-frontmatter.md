---
type: Decision
title: "ADR-0030: Incoming frontmatter is untrusted -- preserved whole in one namespace, with a closed per-key lift"
description: A source file's own YAML frontmatter is parsed fail-closed (YAML only, bounded, no anchors or aliases, plain data only), stored whole under the Source's source_frontmatter extension key, and only tags, sensitivity (raise-only) and date are lifted into engine fields; engine-owned keys are never lifted, and author/updated stay namespace-only.
status: Proposed
date: 2026-09-29
tags:
  - openkos
  - adr
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-09-29T00:00:00Z
sensitivity: public
---

# ADR-0030: Incoming frontmatter is untrusted -- preserved whole in one namespace, with a closed per-key lift

- **Status:** Proposed
- **Date:** 2026-09-29

## Context

Many files a person ingests already carry YAML frontmatter written by another tool: Obsidian notes, static-site posts, exported meeting notes. It holds `tags`, `date`, `sensitivity`, `author`, `aliases` and whatever else that tool chose. Until now, `ingest` never parsed it. The block was embedded as text in the Source body, and the user's own curation was invisible to retrieval, filtering and the graph (#1062).

Parsing it is the first time the engine reads YAML it did not write. Every earlier `load_frontmatter` call reads documents the engine produced itself. Five forces shape the decision:

- **The input is untrusted.** Issue #285 established that a source's file name is not a trusted input. A source's content is less trusted still. `yaml.safe_load` blocks object construction, but it does not bound alias expansion ("billion laughs"), recursion depth or size. A non-mapping root makes any `.get` crash, and `python-frontmatter` silently switches parser on a `+++` or `;;;` fence.
- **Some frontmatter keys are engine-owned or trust-bearing.** `type`, `provenance`, `sources`, `status`, `version`, `generated`, `timestamp` and `verified` decide conformance, trust, sensitivity propagation and lifecycle. An external file must never be able to assert them.
- **Sensitivity is a security field.** ADR-0003 makes it a fail-closed high-water mark, and ADR-0010 makes re-ingest raise-only. A file must not be able to lower it.
- **Adopt OKF; do not re-decide it.** OKF v0.2 allows unknown frontmatter keys as extensions (§4.1). The format's own keys keep their OKF meaning.
- **Human curates, engine maintains.** Tags a person adds to a Source by hand must survive a re-ingest. Rewriting existing descendants is a separate, human-invoked verb (ADR-0009), not a side effect.

The decision is hard to reverse: once bundles carry the namespace and lifted values, both the stored shape and the lift rules become data that later releases must keep reading.

## Decision

1. **We parse incoming frontmatter fail-closed, in the OKF adapter seam only.** One function in `model/okf.py` extracts the leading `---` block with the same line rule title derivation uses. One shared function serves both, so they cannot disagree. The function parses YAML only; there is no TOML or JSON fence detection. It rejects a block over 64 KiB before parsing, rejects any anchor or alias, caps nesting depth, loads with PyYAML's `SafeLoader`, and accepts only a string-keyed mapping of plain data (strings, numbers, finite floats, booleans, null, dates, datetimes, lists, mappings) that survives the engine's own dump-and-reload unchanged. Anything else means "no frontmatter" with a named reason. It never fails the ingest, and never changes the raw copy or the body.
2. **We preserve the whole mapping, unmodified in meaning, under one extension key: `source_frontmatter` on the Source.** It is emitted through the engine's normal YAML dumper, so key order and quoting are normalized. The byte-exact original stays in the Source body. Incoming keys are never spread into top-level frontmatter, so they cannot collide with OKF or engine keys. The key records evidence about one raw file, so a merge keeps only the survivor's own value.
3. **We lift only keys that already have a defined meaning and a safe merge rule, through a closed allow-list:**
   - `tags`: a list of strings, or one string as one tag. It is unioned with the Source's existing tags, existing tags first, and never overwrites them. Derived concepts created in the same run inherit the Source's tags.
   - `sensitivity`: folded through `combine_sensitivity`. It can only raise the value, and an unrecognized value fails closed to `confidential`. The lifted value also floors that run's LLM-send gate, so a file that declares itself confidential is not sent for extraction without `--include-confidential`.
   - `date`: an event-date tier below an explicit flag and a stored value, above the file name. It uses the same tolerant shape rules as the stored `event_date`.
4. **Engine-owned and trust-bearing keys are never lifted:** `status`, `type`, `provenance`, `version`, `timestamp`, `generated`, `verified`, `sources`. The allow-list shape enforces this: lift code reads no other key.
5. **`author` and `updated` are not lifted.** ADR-0029 decided that a Source carries no `sources` entry for its raw original, so the `sources[].author`/`last_modified` target #1062 first named does not exist on a Source. Putting either on derived concepts' `sources[]` would make `project_sources` read another document's frontmatter and break its contract of being a pure function of `provenance`. No consumer needs them. They stay in `source_frontmatter`, like `created`, `title` and every other key.
6. **A byte-identical re-ingest of an already-extracted source rewrites only the Source** when its preserved or lifted state differs from what is stored. It makes no LLM call, touches no derived object, and goes through the normal preview, confirm gate, drift guard and commit.

## Consequences

- A person's existing curation (tags, dates, a stricter sensitivity) reaches retrieval, filtering and the graph with no extra step. Lifted tags change FTS and embedding input, and therefore ranking. That is intended.
- Hostile, huge or malformed frontmatter costs one bounded check and is ignored. The ingest still succeeds. Tests assert the specific rejection reason for each hostile shape, so a guard that stops firing cannot hide behind another one.
- The first plain re-ingest after upgrading makes one reviewable Source-only commit for each source that has usable frontmatter. A second re-ingest writes nothing.
- A file's declared sensitivity acts as a floor, like `default_sensitivity`. If a human lowers the Source below it with `set-sensitivity`, the next re-ingest raises it again. Because `raw/` is immutable, the declaration cannot be removed from the stored raw copy. An unrecognized value such as `internal` makes the Source confidential and blocks extraction by default. This is ADR-0003's rule, and it may surprise users.
- Derived concepts that already exist keep their creation-time tags when a Source's incoming tags change. They also keep their own sensitivity when the Source is raised; `set-sensitivity` remains the tool for that (ADR-0009). A re-sync verb for tags is follow-up work, not part of this decision.
- The stored namespace is semantically faithful, not byte-faithful: key order and quoting follow the engine's dumper. Anything that needs the original bytes reads the body.
- A future lift of another key needs its own decision naming its meaning and its merge rule. Widening the allow-list is a policy change, not a refactor.

## Alternatives considered

- **Lift everything recognizable, or spread incoming keys into top-level frontmatter.** An external file could then assert `type`, `provenance` or `sensitivity`, and the engine would have to decide what every foreign key means. Rejected.
- **Store only the lifted keys.** This loses the rest of the user's metadata for no safety gain, since the namespace is inert. Rejected.
- **Reuse `load_frontmatter` unguarded.** It auto-detects TOML/JSON fences, uses its own boundary regex, and bounds neither aliases nor size. Its `dict` return type is not enforced at runtime. Rejected.
- **Fail the ingest on bad frontmatter.** A typo in someone's note would block ingesting the note. Rejected; the raw text is still preserved in the body.
- **Overwrite Source tags with the lifted tags on re-ingest.** This silently discards tags a human added. Rejected.
- **Re-sync existing descendants on every re-ingest.** ADR-0009 already chose an explicit, reviewable verb for rewriting descendants. Rejected for this decision and deferred.
- **Lift `created:` as an event date.** It often records when the file was made, not when the event happened. Rejected; only `date:` is lifted.
