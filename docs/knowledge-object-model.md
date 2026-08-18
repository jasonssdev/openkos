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

A complete Knowledge Object — OKF concept document plus the OpenKOS layer — looks like this. It is taken verbatim from [`examples/good-life-demo/`](../examples/good-life-demo/), where someone reading philosophy took notes on Epictetus, then had one of their readings corrected by a friend on a call. Its two-source `provenance` is not a single compile: each source first produced its own `Stoicism` concept, and entity resolution's exact-title match reached a `SAME` verdict that merged the two, unioning their provenance:

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
- **Insight** — a filed synthesis: an answer a model produced over the bundle's state at answer time, written back by `query --save` (issue #570). Like `Source`, it is a functional category rather than an ontological one, and like `Source` it is **never emitted by the compiler's classifier** — only the engine's explicit save path writes one. The distinction it encodes is truth-decay: an extracted `Concept` depends on an immutable `Source`, so its truth does not decay; an `Insight` depends on the mutable bundle, so every ingest, merge, or correction can invalidate it. It therefore defaults to the `volatile` tier, declares provenance to the concepts it was synthesized from (so the freshness machinery can flag it when they change), is rendered distinctly in `query`'s citation list (`[synthesis]`), and is labeled as model output in the synthesizer's own context. It is also down-weighted in retrieval itself: an `insights/` id's fused score is deterministically halved, so a filed synthesis cannot outrank the source-derived evidence it was built from merely because it is already phrased the way questions are asked. That is a re-rank rather than an exclusion — a genuinely relevant synthesis still beats a barely relevant source, and a bundle holding no insights fuses exactly as plain reciprocal rank fusion would. All of this exists because compounding on sources is the product's thesis, while unmarked compounding on model output is how a knowledge base rots.

This is a recommendation, not a constraint, and it is the **stable core (tier 1)**: it changes only rarely and only through an ADR. Two further tiers grow on top without touching it — optional **domain extensions** (tier 2) and **personal, emergent types** (tier 3) coined by a user's own compiler. Because OKF only requires that `type` be present (its value is free), an unknown type is still a valid bundle, so the vocabulary degrades gracefully.

> Note: earlier drafts listed `Observation` as a core type. It was removed because it collides with `Event` (an observation is something that happened) and with the `snapshot` freshness class (a dated observation) — mixing the "what it is" axis with the "how it behaves in time" axis.

### When the classification was a close call

Some subjects genuinely sit on a boundary. A seminar someone is teaching this term is defensibly an `Event` (a bounded happening) and defensibly a `Project` (an ongoing effort with a goal and a timespan). The rubric does not resolve that, because the ambiguity is in the subject, not in the rubric.

The type is not cosmetic. It decides the bundle subdirectory, the `index.md` catalog section, and the default volatility tier — `Event` is `static`, `Project` is `volatile`. So the same sentence, classified twice, can land in a different directory under a different refresh expectation.

When the compiler reports that it weighed a runner-up, the object carries an optional `type_alternative` field naming it:

```yaml
type: Event
type_alternative: Project
```

`type` remains the answer: nothing reads `type_alternative` to route or file a document. The field exists so that a close call is **recorded as a close call** rather than filed as a settled fact. It is absent — no sentinel value — whenever the classification was clear, which is the normal case.

This does not make classification deterministic, and is not meant to. It makes the uncertainty legible to whoever reads the bundle later, which is the same posture the format takes toward provenance and freshness: record what is actually known, including how firmly.

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

This vocabulary is an **OpenKOS extension, not an OKF feature**. It is carried in frontmatter as an additional key — legal under §4.1, and something conformant consumers are asked to preserve when round-tripping — so it degrades gracefully: a plain OKF consumer reading an OpenKOS bundle sees exactly the untyped directed edges the spec promises it, loses no structure, and renders the graph correctly. The typing is a bonus for tools that understand it, never a precondition for reading the bundle. The typed graph shipped in MVP 2: `openkos relate` writes typed edges into frontmatter, and hybrid retrieval traverses them. MVP 1 shipped the untyped links and the prose; the typed layer builds on top of them.

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

A freshness lint enforces this: volatile claims must be stamped or expressed as pointers, and aged stamps are surfaced for re-observation rather than silently trusted. Freshness is a property of the system, not a habit the writer has to remember. The lint arrived in stages: in MVP 1 it was purely mechanical, flagging any `as of` stamp older than the configured freshness window; volatility classification and volatility-aware windows (per-type, LLM-suggested) shipped in MVP 2.

These three forms were chosen over the main alternatives: a per-object TTL (guesswork, and a binary cutoff that cannot tell timeless from volatile); a decaying-confidence score (opaque and falsely precise, and at odds with explainability); full bi-temporal modeling (rigorous but heavy for personal markdown); re-verification on read (that is RAG's re-derive-everything model, which breaks local-first and offline use); and pure event-sourcing (clean, since everything becomes a snapshot, but it needs a reduction layer to answer "what is true now"). The three-forms lint is lightweight, explainable, and separates a fact's temporal nature from its content — and as a bonus it yields a lightweight bi-temporal record for free: a `snapshot` approximates valid-time, and git history provides transaction-time.

---

## Sensitivity and access boundaries

Because OpenKOS is local-first, your knowledge already lives on your machine. A sensitivity label is therefore **not encryption** and does not protect against someone with access to your disk — the local user is inside the trust boundary. Its purpose is to govern what crosses a **trust boundary**: what may be sent to a cloud model, what an agent may read, what is included in an export or shared bundle, and what is replicated when syncing. A local-first engine can make a guarantee cloud systems cannot — that confidential knowledge never leaves the device.

Every object carries a `sensitivity` level. Three levels are defined to start, and the set is extensible:

- **public** — safe to share, export, or publish; any model, local or cloud, may process it.
- **private** (default) — stays in your bundle; processed by local models; not exported or shared unless you explicitly choose to.
- **confidential** — never sent to a cloud model, and excluded from exports and sharing; local models only; still readable by local agents. "Local models only" is literal: see [egress, not inference](#what-the-gate-protects-against-egress-not-inference) for how a local backend is verified.

The default for unlabeled objects is **private** (fail-closed): nothing is treated as public unless declared so. That workspace default is a floor rather than a flat rule, because not every kind of object carries the same exposure. `type_sensitivity_defaults` in `openkos.yaml` maps an object type to the number of levels to raise it at birth, and it ships EMPTY, so a stock workspace births every type at its floor. `Person: 1` is the recommended setting when a workspace holds material about other people; with it, a `Person` compiled from an ordinary `private` source is born `confidential` rather than `private` ([ADR-0015](adr/0015-per-type-default-sensitivity.md)). The offset applies to the configured floor and is clamped at `confidential`; because it can only ever raise, a type default can never quietly downgrade an object.

Two rules make the label meaningful:

1. **Enforcement lives in the engine, not the label.** The `sensitivity` field only declares intent. The engine enforces it at every boundary — most importantly at context assembly, so a confidential object cannot be pulled into a prompt destined for a cloud model. A label without enforcement is only documentation.
2. **Sensitivity propagates along provenance.** A derived object is at least as sensitive as the most sensitive source it was compiled from — a high-water-mark rule. A synthesis that merges a confidential source with a public one becomes confidential. This propagation travels along the [provenance](#provenance) chain. It is no longer the only thing that can lift an object above the floor — the per-type offset described above is applied to the configured floor at birth — but it still wins outright wherever the two disagree: an object is born at whichever of the two is more restrictive, so a source more sensitive than floor-plus-offset decides the result on its own, and the offset can never lower what provenance already raised. Both birth seams that create a document — `ingest`'s derived objects and `query --save`'s filed answer — resolve it through the same function, so the two cannot drift apart.

Enforcement is **live at the retrieval boundary today** (shipped in MVP 2): a `confidential` concept is filtered out of context assembly before anything reaches the LLM, with an explicit `--include-confidential` escape for the local user who deliberately wants it in. The remaining boundaries — cloud-model options, agents (MCP), and export/import — arrive in MVP 3, and the same field governs them when they land. The field was defined early so knowledge could be labeled from the start.

### What the gate protects against: egress, not inference

`sensitivity` governs what **leaves the machine**. When the LLM backend is verifiably local, nothing leaves, so the gate has nothing to protect — and it does not fire: a `confidential` concept participates normally in `query`, `contradictions`, `adjudicate`, `suggest-relations`, `suggest-volatility`, and `curate` against a loopback Ollama, with no flag.

"Verifiably local" means loopback **by literal form** — `localhost`, a `127.0.0.0/8` address, or `::1` — checked against the host the client will actually send to. There is no DNS resolution and no allowlist: a hostname that resolves to loopback today can resolve elsewhere tomorrow, and a check that cannot prove a host is local must not grant the exemption. Unknown or unparseable hosts are therefore treated as remote.

| Backend | `confidential` object | `--include-confidential` |
|---|---|---|
| Verified local | sent | not needed |
| Verified remote | blocked | still the escape hatch |
| Unknown / unparseable | blocked (fail closed) | still the escape hatch |

This is a **deliberate change of behavior**, not a relaxation of the policy. It sharpens `confidential` from the blunt "must never reach an LLM" to the precise "must not leave this machine", and it removes the habit the old rule trained — passing `--include-confidential` on every local invocation, which disables the one gate that matters the day the backend is *not* local.

To restore the old blanket behavior for a workspace, set `confidential_local_exemption: false` in `openkos.yaml`. It is a workspace-level key rather than a per-command flag on purpose: a policy that depends on remembering to type a flag is not a policy. `openkos doctor` reports the backend's locality and whether the exemption is consequently active, so the state is inspectable rather than inferred.

---

## Living documents and versioning

A Knowledge Object is a **living document**, not an immutable record. Nothing rewrites an object merely because a new source arrives: the mechanism is a `SAME` verdict from entity resolution — reached through `curate`'s Identity stage or `adjudicate --apply` — which unions provenance and merges bodies for two documents judged to describe the same entity. This is the whole point of the compounding wiki pattern. Merging the bodies is not a concatenation by default: past a threshold on how much of the result is stacked absorbed text, the merge spends a model call rewriting the two into one document in a single voice, which means a merge costs model time and the survivor's prose is model-rewritten unless the operator opts out. The mechanics, the opt-out, and the fallback are under [Merge](#end-of-life-archival-and-deletion) below.

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
- **Merge** — fold a duplicate or mis-extracted object into another, preserving provenance. Implemented as `openkos merge <survivor-id> <absorbed-id>`: sensitivity is recomputed (never copied) to the more restrictive of the two, inbound links are repointed at the survivor, and everything needed to reverse the merge is written to a **ledger sidecar** at `bundle/.state/ledger/<survivor-id>.ledger.okf`. The survivor itself carries no `merged_from` key at all. The ledger used to live in its frontmatter, where each new entry re-embedded every earlier one and the file grew with the square of its merge count, so every tool that opened the concept paid for its whole history to read its current text; [ADR-0013](adr/0013-relocate-merge-ledger-to-bundle-state.md) moved it out, and the sidecar's deliberately non-`.md` suffix keeps it outside every `rglob("*.md")` walk in the engine by construction rather than by an exclusion each walk has to remember. The write is two-phase — a hash-bound `.pending` marker before the survivor is committed, promoted only after the survivor has landed, and only then is the absorbed file removed — which makes a crash mid-merge mechanically detectable rather than a judgement call: the marker's expected hash compared against the survivor on disk decides roll-forward or roll-back with no heuristic. `openkos repair` migrates a ledger still embedded in the legacy frontmatter form into its sidecar. A merge is not purely mechanical either: when the absorbed body would be stacked under a `## Merged content` heading and that stack is a large enough share of the result, `merge` spends one `llm.chat` call rewriting both bodies as a single coherent document — disclosed in the plan before the confirmation prompt, so the model call is part of what the user approves, opted out with `--no-reconcile`, and falling back to the stacked form on any refusal, validation failure, or unreachable model. The ledger still holds the verbatim pre-merge bytes either way, so `openkos unmerge <survivor-id> <absorbed-id>` reverses the most recent merge (LIFO), restoring both objects and every rewritten link to byte parity with their pre-merge state — see `docs/cli.md`.
- **Retire a fact** — move a stale claim into a dated snapshot (the freshness path).
- **Delete an object** — `openkos forget` removes a concept document and its references from `index.md`; recoverable via normal git history. Since MVP 2 `forget` is reference-aware (it refuses to orphan an inbound link unless `--force`) and leaves a tombstone in `log.md`. Removing the document is not the whole job, though, because by now three other stores can be holding the same words. In the same write, `forget` sweeps all three: the **merge-ledger sidecars**, where a prior merge kept the absorbed object's full verbatim bytes (its own sidecar is deleted outright, entries naming it are dropped from other survivors' sidecars, and the entries that survive are scrubbed of its text); the **persisted findings** in `.openkos/findings.db`, which quote claim text verbatim out of concept bodies; and the **live decision records** under `bundle/.state/decisions/`, so a forgotten object's participation in a contradiction verdict does not outlive it under some other concept's sidecar. Without those sweeps a `forget` would delete the page and leave its body sitting in a survivor's ledger — which is the one outcome the privacy case cannot tolerate.
- **Purge a source** — the right to be forgotten, shipped in MVP 2 as `openkos purge`: remove the raw source and everything derived from it, rewrite git history, scrub the catalog and log across all history, and clear derived indexes. Destructive and irreversible.

The `forget`/`purge` flow shows inbound references and (with `--scope source`) derived descendants before acting, defaults to the least destructive scope, and requires explicit confirmation — a typed phrase for a purge.

Note that "deleted", "forgotten", and "purged" are lifecycle *events*, recorded as tombstones in `log.md` and in git history — **not** values of the `status` field, which stays `draft | active | deprecated`. Retrieval keys deprecation off `status: deprecated` (plus `supersedes` edges from `reconcile`); a `forget`/`purge`, by contrast, removes the document outright rather than changing its status.

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
