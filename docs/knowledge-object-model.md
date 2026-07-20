---
type: Architecture
title: Knowledge Object Model
description: How OpenKOS represents knowledge — an OKF-conformant concept document with a thin, opinionated OpenKOS layer for provenance and freshness.
tags:
  - architecture
  - knowledge-model
  - open-knowledge-format
  - okf
  - openkos
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-15T00:00:00Z
sensitivity: public
---

# Knowledge Object Model

## Overview

A **Knowledge Object (KO)** is the fundamental unit of knowledge in OpenKOS. Concretely, a Knowledge Object **is an OKF concept document**: a single markdown file with YAML frontmatter, identified by its path within a bundle, and linked to other objects through ordinary markdown links.

OpenKOS does not define a competing model. It adopts the Open Knowledge Format as its storage and interchange layer and adds a **thin, opinionated layer** on top — a recommended type vocabulary, a provenance chain, and a freshness class — that turns a minimal OKF bundle into something an engine can maintain and reason over. Any OpenKOS bundle is a valid OKF bundle; any OKF bundle can be read by OpenKOS.

---

## Relationship to OKF

OKF is minimally opinionated: it requires exactly one field (`type`) and leaves the rest to the producer. OpenKOS fills that space with conventions, never with incompatibilities.

| Concern | OKF (the standard) | OpenKOS (our layer) |
| --- | --- | --- |
| File shape | Markdown + YAML frontmatter | Same |
| Required field | `type` | `type` (plus recommended core fields below) |
| Identity | File path | File path |
| Relationships | Markdown links | Markdown links, with a recommended set of typed relations |
| Object types | Producer's choice | A recommended canonical vocabulary |
| Provenance | Not specified | A required provenance chain to immutable sources |
| Freshness | Not specified | A freshness class per object |
| Sensitivity | Not specified | A sensitivity level per object, enforced across trust boundaries |
| Navigation | Optional `index.md` / `log.md` | Generated and maintained automatically |

Everything OpenKOS adds lives in the frontmatter and body as ordinary fields and links, so a bundle degrades gracefully: strip the OpenKOS layer and you still have a conformant OKF bundle that any other tool can read.

---

## Canonical structure

Every Knowledge Object has two parts, exactly as in OKF:

```text
Knowledge Object (one markdown file = one OKF concept)
├── Frontmatter   # structured, queryable metadata
└── Body          # the knowledge itself, in markdown
```

The frontmatter defines identity and lifecycle. The body carries the knowledge and links to related objects.

---

## Core metadata

OpenKOS uses the OKF v0.1 field set as its base, so its documents are interoperable by construction:

```yaml
type:        # required by OKF — what kind of object this is
title:       # human-readable name
description: # one-line summary, used for progressive disclosure
resource:    # canonical link to the underlying resource, if any
tags:        # free-form labels
timestamp:   # last meaningful update (ISO 8601)
```

OpenKOS then adds a small recommended set for engine features:

```yaml
status:      # draft | active | deprecated
version:     # monotonic revision counter
freshness:   # timeless | snapshot | pointer  (see Freshness)
sensitivity: # public | private | confidential  (see Sensitivity; default private)
provenance:  # list of source references this object was derived from
```

Additional fields may be introduced by specialized object types without breaking OKF conformance: OKF §4.1 states that producers MAY include any additional keys, and that consumers SHOULD preserve unknown keys when round-tripping and SHOULD NOT reject documents carrying them.

**Identity is the path.** OKF §2 already defines a concept's identity: the **Concept ID** is the file's path within the bundle with the `.md` suffix removed — `concepts/stoicism.md` is `concepts/stoicism`. OpenKOS adopts that definition rather than adding an `id` field of its own. Inventing a second identifier would give every object two competing IDs, and no other OKF consumer would understand ours. The trade-off is accepted deliberately: moving a file changes its ID, but git records the rename and OKF explicitly tolerates links whose target has moved (§5.3).

---

## A worked example

A complete Knowledge Object — OKF concept document plus the OpenKOS layer — looks like this. It is taken verbatim from [`examples/good-life-demo/`](../examples/good-life-demo/), where someone reading philosophy took notes on Epictetus, then had one of their readings corrected by a friend on a call:

```markdown
---
type: Concept
title: Stoicism
description: Hellenistic school holding that virtue is the only good, and that
  freedom comes from knowing what is up to us.
resource: https://plato.stanford.edu/entries/stoicism/
tags: [philosophy, hellenistic, ethics]
timestamp: 2026-07-14T18:30:00Z
status: active
version: 2
freshness: timeless
sensitivity: confidential
provenance:
  - raw/notes-on-the-enchiridion-2026-07-05.txt
  - raw/call-with-maria-2026-07-14.txt
---

# Stoicism

A Hellenistic school that holds virtue to be the only good. Its practical core is the **dichotomy of control** (*Enchiridion* 1): some things are up to us — judgement, impulse, desire, aversion — and some are not — the body, reputation, office. Suffering comes from wanting what was never ours to govern [1].

**Apatheia** is freedom from the *pathē*, the destructive passions — not the absence of feeling. The Stoics kept the *eupatheiai*, the "good feelings": joy, caution, wishing. The goal is not to stop feeling but to stop being ruled by it [2].

The term is commonly misread as "indifference to emotion", by analogy with the English cognate *apathy*. That reading makes the school sound colder than it is, and it is worth disarming before using the word in front of a general reader [2].

## Related

- [Epicureanism](/concepts/epicureanism.md) — contrasted with; both ask how to live and disagree on what the good is
- [Maria Salazar](/people/maria-salazar.md) — corrected the apatheia reading here
- [Frame the essay on the dichotomy of control](/decisions/frame-the-essay-on-the-dichotomy-of-control.md) — cited in

# Citations

[1] [Reading notes — Enchiridion, 2026-07-05](/sources/notes-on-the-enchiridion-2026-07-05.md)
[2] [Call with Maria Salazar — 2026-07-14](/sources/call-with-maria-2026-07-14.md)
```

Reading it against the model: its Concept ID is its path, `concepts/stoicism` — there is no `id` field. The top block is the OKF field set (`type`…`timestamp`) plus the OpenKOS layer (`status`, `version`, `freshness`, `sensitivity`, `provenance`). The body is human-readable markdown. The links are bundle-relative — the form OKF §5.1 recommends, because it survives a document moving within its subdirectory — and each asserts a relationship whose *kind* is carried by the surrounding prose, not by the link.

Three fields repay a closer look, because each shows a different part of the model working.

**`version: 2`, yet `freshness: timeless`.** These are independent axes and this object separates them cleanly. What a Hellenistic school taught does not decay, so the page needs no date and never goes stale. But it is on its second version: at v1 it read *apatheia* as "indifference to emotion" — the common misreading, straight from the English cognate — and the call corrected it. **The version rose because the reader learned more; freshness moves only when the world changes.** The body carries the current understanding; `log.md` and git carry the fact that it changed.

**`sensitivity: confidential`, on public knowledge.** At v1 this object was `private`, compiled only from private reading notes. Then a confidential source touched it, and the high-water-mark rule raised it. Stoicism is public knowledge; this page about it is not — because of *where the reader learned it*, not what it says. The rule over-classifies rather than leak, and a human can downgrade it after verifying the claim against a public source.

**`provenance` points out of the bundle; `# Citations` points inside it.** Provenance lists the two immutable originals as paths from the workspace root — `raw/` sits beside the bundle, not inside it, because sources are input material rather than concepts. The `# Citations` section (OKF §8) mirrors that lineage into the body, but points at the **Source concepts** representing those originals rather than at the raw files. That indirection is deliberate: every link in the bundle resolves within the bundle, and only a Source concept's `resource` reaches outside it.

The `as of` stamp does not appear here, and that is the point — a timeless fact needs none. It appears where volatile facts actually live, as in the `Person` object from the same bundle:

```markdown
---
type: Person
title: Maria Salazar
resource: https://example.edu/faculty/m-salazar
freshness: pointer
sensitivity: confidential
provenance:
  - raw/call-with-maria-2026-07-14.txt
---

Teaches the Hellenistic ethics seminar this term (as of 2026-07-14) — a post is
a volatile fact, so the faculty page linked in `resource` is the source of truth
here, not this page [1].
```

A role is the canonical volatile fact: it changes, so it is a `pointer`, it carries a stamp, and the lint flags the stamp once it ages past the configured window.

Strip the OpenKOS-layer fields from either object and it is still a conformant OKF concept any other tool can read. A consumer which knows nothing about OpenKOS still sees the citations and the links.

---

## Object types

OKF lets the producer decide what types exist. OpenKOS recommends a **small canonical vocabulary** so that objects from different bundles interoperate predictably. It is grounded, not arbitrary: it combines a foundational split (things that *persist* — continuants — versus things that *happen* — occurrents) with the pragmatic shape of personal knowledge (who, what, when, why, how) plus the engine's need to anchor provenance.

A type earns a place in the canonical core only if it passes three tests: it has **distinct structure** (its own useful fields), **distinct relationships** (it participates in the graph differently), and **transversal recurrence** (it shows up across domains, not just one). Anything that fails is better expressed as a domain extension, a tag, or structure inside a body — not a core type. The measure is usefulness for retrieval and connection, never taxonomic completeness.

**Continuants — the who and what**

- **Person** — an individual.
- **Organization** — a group or institution.
- **Concept** — an idea, topic, theory, term, or framework; the backbone of a knowledge base.
- **Entity** — a concrete thing that is not a person or organization (a tool, product, system, artifact). This is the deliberate **fallback**: used only when no more specific type fits, so the compiler should always prefer a specific type over `Entity`.
- **Place** *(recommended, optional)* — the "where"; included when a bundle needs locations, omitted when it does not.

**Occurrents — the when and how**

- **Event** — a bounded happening at a time (a meeting, incident, milestone, trip).
- **Procedure** — a repeatable how-to: process, method, runbook.

**Knowledge-work objects — the why**

- **Decision** — a choice made, with its rationale, alternatives, and status. High value, because decisions are what people most often lose and most expensively re-derive.
- **Project** — an ongoing effort with a goal and a timespan.

**Provenance (a separate, functional axis)**

- **Source** — an ingested original (article, transcript, paper). Not an ontological category but a bibliographic one: the anchor every derived object points back to.

This is a recommendation, not a constraint, and it is the **stable core (tier 1)**: it changes only rarely and only through an ADR. Two further tiers grow on top without touching it — optional **domain extensions** (tier 2) and **personal, emergent types** (tier 3) coined by a user's own compiler. Because OKF only requires that `type` be present (its value is free), an unknown type is still a valid bundle, so the vocabulary degrades gracefully.

> Note: earlier drafts listed `Observation` as a core type. It was removed because it collides with `Event` (an observation is something that happened) and with the `snapshot` freshness class (a dated observation) — mixing the "what it is" axis with the "how it behaves in time" axis.

### Schema versus vocabulary, and two families of types

Two things are easy to conflate. The **frontmatter schema** — the fields themselves (`type`, `title`, `description`, and so on) — is universal: every concept document carries it. The **type vocabulary** is the set of *values* the `type` field may take, and that is what the canonical core, domain extensions, and personal types above govern. The schema is the carrier; the classification is the value of `type`.

The canonical core is the recommended vocabulary for **knowledge compiled from a user's sources** (Concept, Person, Decision, Event, and so on). Documents that describe *the project itself* — including this repository's own design docs — are also OKF concept documents (we dogfood the format), but they use a small **documentation type set** (`Architecture`, `Reference`, `Vision`, `Roadmap`, `TechStack`) rather than the knowledge vocabulary. That is expected, not a contradiction: OKF requires only that `type` be present, and the three-tier model explicitly allows type sets beyond the knowledge core — the documentation types are, in effect, this repository's own domain extension. So seeing `type: Architecture` at the top of this file, and not finding it among the canonical knowledge types, is correct.

---

## Relationships

Objects connect through ordinary markdown links, which form a graph richer than the folder hierarchy. Links use the bundle-relative form (`/concepts/epicureanism.md`) that OKF §5.1 recommends, because it stays valid when a document moves within its subdirectory.

**OKF links are untyped, and that is the baseline we build on.** §5.3 is explicit: a link from A to B asserts *that* a relationship exists, but the kind of relationship — depends-on, joins-with, part-of — "is conveyed by the surrounding prose, not by the link itself," and a consumer building a graph view "typically treat[s] all links as directed edges of an untyped relationship." OpenKOS does not fight this. The prose next to a link is where the meaning lives, which is why the `## Related` sections in a bundle read `- [Epicureanism](/concepts/epicureanism.md) — contrasted with`.

On top of that baseline, OpenKOS **layers** a recommended relation vocabulary that its own graph and retrieval can traverse:

- references
- depends_on
- derived_from
- related_to
- caused_by
- part_of
- member_of
- produced_by

This vocabulary is an **OpenKOS extension, not an OKF feature**. It is carried in frontmatter as an additional key — legal under §4.1, and something conformant consumers are asked to preserve when round-tripping — so it degrades gracefully: a plain OKF consumer reading an OpenKOS bundle sees exactly the untyped directed edges the spec promises it, loses no structure, and renders the graph correctly. The typing is a bonus for tools that understand it, never a precondition for reading the bundle. The typed graph itself arrives in MVP 2; MVP 1 ships the links and the prose.

---

## Provenance

Every derived Knowledge Object must record where it came from. Knowledge may be extracted from documents, notes, conversations, code, web pages, research papers, images, or audio transcripts.

One object is deliberately exempt: a **Source** carries no `provenance`. A Source is not derived from anything — it *is* the bundle's representation of an original, and its `resource` field already names that original. Giving it a `provenance` that merely repeats its `resource` would be duplication dressed as lineage, and it would blur the one distinction the chain depends on: `provenance` answers *"what was this compiled from?"*, and for a Source the honest answer is "nothing — it is the thing." Lineage runs from a derived object, through the Sources it cites, out to the raw originals. Sources are the end of that chain, not another link in it.

Two rules hold absolutely:

1. **Raw sources are immutable.** OpenKOS reads from them and never rewrites them. They are the source of truth.
2. **Derived knowledge never replaces its source.** OpenKOS always maintains a complete provenance chain between a Knowledge Object and the raw material it was compiled from.

This is what makes retrieval explainable: any answer can be traced back through the objects to the original sources.

---

## Freshness

The most common way a knowledge base rots is a present-tense claim about a fast-changing fact that carries no date — true when written, a quiet lie a week later. OpenKOS prevents this by classifying every object (or claim) as one of three legal forms:

- **Timeless** — a fact that does not decay; no stamp needed.
- **Snapshot** — a dated observation; it never goes stale because it claims what was true *on a date*.
- **Pointer** — for facts whose current value matters, store where the truth lives (a `resource`), optionally with the last observed value and an `as of` stamp.

A freshness lint enforces this: volatile claims must be stamped or expressed as pointers, and aged stamps are surfaced for re-observation rather than silently trusted. Freshness is a property of the system, not a habit the writer has to remember. The lint arrives in stages: in MVP 1 it is purely mechanical, flagging any `as of` stamp older than the configured freshness window; volatility classification and volatility-aware windows (per-type, LLM-suggested) arrive in MVP 2.

These three forms were chosen over the main alternatives: a per-object TTL (guesswork, and a binary cutoff that cannot tell timeless from volatile); a decaying-confidence score (opaque and falsely precise, and at odds with explainability); full bi-temporal modeling (rigorous but heavy for personal markdown); re-verification on read (that is RAG's re-derive-everything model, which breaks local-first and offline use); and pure event-sourcing (clean, since everything becomes a snapshot, but it needs a reduction layer to answer "what is true now"). The three-forms lint is lightweight, explainable, and separates a fact's temporal nature from its content — and as a bonus it yields a lightweight bi-temporal record for free: a `snapshot` approximates valid-time, and git history provides transaction-time.

---

## Sensitivity and access boundaries

Because OpenKOS is local-first, your knowledge already lives on your machine. A sensitivity label is therefore **not encryption** and does not protect against someone with access to your disk — the local user is inside the trust boundary. Its purpose is to govern what crosses a **trust boundary**: what may be sent to a cloud model, what an agent may read, what is included in an export or shared bundle, and what is replicated when syncing. A local-first engine can make a guarantee cloud systems cannot — that confidential knowledge never leaves the device.

Every object carries a `sensitivity` level. Three levels are defined to start, and the set is extensible:

- **public** — safe to share, export, or publish; any model, local or cloud, may process it.
- **private** (default) — stays in your bundle; processed by local models; not exported or shared unless you explicitly choose to.
- **confidential** — never sent to a cloud model, and excluded from exports and sharing; local models only; still readable by local agents.

The default for unlabeled objects is **private** (fail-closed): nothing is treated as public unless declared so.

Two rules make the label meaningful:

1. **Enforcement lives in the engine, not the label.** The `sensitivity` field only declares intent. The engine enforces it at every boundary — most importantly at context assembly, so a confidential object cannot be pulled into a prompt destined for a cloud model. A label without enforcement is only documentation.
2. **Sensitivity propagates along provenance.** A derived object is at least as sensitive as the most sensitive source it was compiled from — a high-water-mark rule. A synthesis that merges a confidential source with a public one becomes confidential. This propagation travels along the [provenance](#provenance) chain.

Enforcement becomes relevant once there are boundaries to cross — cloud model options, agents (MCP), and export/import — which arrive in MVP 3. The field is defined early so knowledge can be labeled from the start; the engine enforces it when those boundaries appear.

---

## Living documents and versioning

A Knowledge Object is a **living document**, not an immutable record. As new sources arrive, the engine rewrites existing objects — revising claims, reconciling contradictions, strengthening synthesis. This is the whole point of the compounding wiki pattern.

History is not lost:

- the raw sources remain immutable;
- the bundle lives in version control (git), so every revision is recoverable;
- an append-only `log.md` records what changed and when.

"Versionable" therefore means *tracked and recoverable*, not *frozen*. The immutability guarantee belongs to the sources; the concept documents are meant to evolve.

---

## Lifecycle

A Knowledge Object moves through a deterministic pipeline:

```text
Raw Source (immutable)
      │
      ▼
Knowledge Extraction
      │
      ▼
Knowledge Object (OKF concept, living)
      │
      ▼
Knowledge Graph (links between objects)
      │
      ▼
Retrieval & Memory
      │
      ▼
Agent Runtime
```

The Knowledge Object is the single canonical representation used throughout the entire architecture — and because it is an OKF concept, that representation is portable beyond OpenKOS.

---

## End of life: archival and deletion

Because OpenKOS accumulates knowledge and preserves history, removal is deliberate, not a raw file delete. Two facts shape it: derived objects are **reconstructible** (deleting an object while its source remains can regenerate it), and **git never forgets** (a normal delete leaves content in history — good for recovery, insufficient for a privacy purge). Most reasons to "delete" are better served by another operation, so OpenKOS offers a graduated set, from least to most destructive:

- **Undo** — revert the last ingest.
- **Archive** — set `status: deprecated`; the object fades from retrieval and the index but stays in history. Non-destructive.
- **Merge** — fold a duplicate or mis-extracted object into another, preserving provenance. Implemented as `openkos merge <survivor-id> <absorbed-id>`: sensitivity is recomputed (never copied) to the more restrictive of the two, inbound links are repointed at the survivor, and the survivor gains an embedded `merged_from` ledger entry recording everything needed to reverse the merge. `openkos unmerge <survivor-id> <absorbed-id>` reverses the most recent merge (LIFO), restoring both objects and every rewritten link to byte parity with their pre-merge state — see `docs/cli.md`.
- **Retire a fact** — move a stale claim into a dated snapshot (the freshness path).
- **Delete an object** — remove a concept document and its references from `index.md`; recoverable via normal git history. From MVP 2, deletion also leaves a tombstone in `log.md`.
- **Purge a source** — the right to be forgotten: remove the raw source and everything derived from it, rewrite git history, and clear derived indexes. Destructive and irreversible.

A `forget` flow presents scope and depth, shows inbound references and derived descendants before acting, defaults to the least destructive option, and requires explicit confirmation for a purge.

---

## Design risks and challenges

The Knowledge Object is the load-bearing abstraction of OpenKOS: the graph, memory, retrieval, and agent layers are all projections of it. That makes its design decisions consequential, and it inherits a long, humbling history — formal attempts to model knowledge as typed, linked units go back decades (frames, the Semantic Web, RDF/OWL, Topic Maps, Cyc, Freebase, Wikidata). Most struggled at scale for the same reasons. We record the main risks here, with the design stance that mitigates each, so contributors keep them in view.

**1. Rigid ontology (the biggest risk).** Real-world knowledge does not fit neat boxes. If the recommended type and relation vocabulary hardens into a strict ontology that authors must obey, OpenKOS recreates the failure mode that sank earlier systems: people spend more effort arguing whether something is a `Concept` or an `Entity` than capturing knowledge. *Stance:* the vocabulary is a **recommendation, never a constraint**. Unknown types are handled gracefully. Start minimal, let types emerge from real use, and resist premature ontologizing. The whole bet — like Karpathy's — is that an LLM can maintain loose, emergent structure cheaply where rigid schemas maintained by humans never scaled; the model must stay on the "light, emergent, markdown-first" side of that line.

**2. Entity resolution and granularity.** Deciding what is *one* object versus many is genuinely hard: is "Stoicism" the school, the doctrine, and the reading notes about it one object or three? Is *apatheia* its own concept or a section of Stoicism? When are two mentions the same object? This "boundary problem" is where knowledge graphs get messy. MVP 1 does formal single-source extraction but sidesteps the boundary problem — no cross-source entity resolution, no dedup, no merge, no cross-source identity matching; that becomes central in MVP 2. *Stance:* prefer fewer, richer objects over many fragmented ones; make merges reversible; keep entity-resolution decisions reviewable rather than silently automatic.

**3. Extraction fidelity.** A Knowledge Object is only as good as the extraction that produced it. Mis-extracted objects, duplicates, and hallucinated relationships contaminate everything downstream. *Stance:* provenance and the human-in-the-loop are the quality control, not decoration — every derived object resolves to a source, and consequential changes stay reviewable.

**4. Justifying the added structure.** Karpathy's pattern works with flat markdown pages and no formal object model. A fair skeptic will ask why the extra structure is worth it. *Stance:* the structure must pay for itself through capabilities plain pages cannot offer — enforceable provenance, schema-level freshness, portability via OKF, and a substrate for the graph and memory layers. Where a piece of structure is not earning its keep, that is a signal of over-engineering to remove, not defend.

**5. Standard drift.** OKF is minimally opinionated (it requires only `type`); the OpenKOS layer adds more. If our vocabulary diverges too far, interoperability — our core differentiator — erodes. The sharper edge of this risk is that OKF is published as **v0.1, Draft**, and its §11 reserves the right for a major version to rename required fields or change reserved filenames: we are building on a young spec, not a settled one. *Stance:* everything we add lives as ordinary frontmatter and links so a bundle always degrades to conformant OKF; we adopt OKF's own definitions instead of restating them (identity is the path, citations are `# Citations`, links are untyped) so there is less surface to drift; bundles declare `okf_version`; the format lives behind a single adapter module so a spec revision is a contained change; and conformance is a tested, ongoing commitment rather than an aspiration. We track the standard rather than fork it — and because the canonical layer is plain markdown plus git, even a worst-case drift leaves the user's knowledge readable without us. See [`okf-alignment.md`](okf-alignment.md).

The throughline: the same concept that repeatedly failed when humans had to maintain it may now work because the LLM maintains it. Our job is to keep the model loose enough for that bet to pay off.

---

## Future extensions

Later versions may introduce domain-specific object types, custom schemas with validation rules, semantic constraints, and richer memory projections. All such extensions must preserve OKF conformance and the provenance and freshness guarantees above — and must be weighed against the design risks in the previous section, especially the pull toward a rigid ontology.
