# Ideas

A holding place for ideas that are worth considering and have not been decided.

Nothing here is committed. This file is deliberately weaker than the other
documents: [`roadmap.md`](roadmap.md) states what OpenKOS is going to build,
[`docs/adr/`](adr/) records decisions that have been made, and issues track work
that is actually queued. An entry here is none of those — it is a note that
something might be worth doing, kept so it does not have to be rediscovered.

Entries graduate by leaving. When an idea is worth pursuing it becomes an issue;
if pursuing it requires a decision with consequences, that decision becomes an
ADR; if it changes what the product is going to be, it becomes a roadmap line.
At that point the entry is deleted from this file rather than cross-referenced,
so this document never becomes a second, stale copy of the plan.

Each entry states the idea, why it might matter, and what it would cost or risk.
The cost line is the important one — several of these look free and are not.

---

## Distribution

### Ship OpenKOS as an agent plugin

Package the CLI as an installable plugin for coding-agent environments, so a
user can add it from inside the tool they already work in rather than installing
a Python package and learning a new command surface.

*Why it might matter.* The people most likely to want a compiled knowledge base
are already working inside an AI coding tool all day. Meeting them there removes
the install step entirely, and the agent can drive the verbs on their behalf.

*Cost and risk.* A thin wrapper is cheap; keeping it in step with 26 verbs is
not. It also creates a second supported surface with its own bug reports before
the CLI is stable. Probably belongs after the MCP server, not before it — the
plugin should wrap the agent-facing API rather than shelling out to the CLI.

### Container image and compose file

Publish an image with a compose file so the engine can run on a home server or
NAS rather than a laptop.

*Why it might matter.* A meaningful share of local-first, privacy-motivated
users self-host. They do not install Python packages; they pull images. This is
an entire distribution channel currently ignored.

*Cost and risk.* The engine assumes a local Ollama and a local filesystem, both
of which are awkward across a container boundary. Only worth it once there is a
server or API to expose — a containerised CLI nobody can reach is not useful.

---

## Making the bundle legible where the user already is

### An Obsidian query cookbook

A short document with ready-to-paste Dataview queries against the bundle:
objects by type, objects with no relations, sources with no derived objects,
stamps past their freshness window, recently touched pages.

*Why it might matter.* The bundle is already plain markdown with YAML
frontmatter, so this works today with no engine changes. It converts the
portability claim from an assertion into something a reader can try in five
minutes, and it gives the project a read-only UI for free.

*Cost and risk.* Almost none — it is one document. The only maintenance burden
is that the queries reference frontmatter keys, so a key rename breaks them
silently. Worth a test that the documented keys still exist.

### A standalone HTML report

A verb that writes a single self-contained HTML file — type distribution, graph
view, ingestion over time, freshness state — that opens in any browser.

*Why it might matter.* It gives the project a visual surface without a server, a
frontend build, or a long-running process. A generated artifact fits the
existing model: derived, rebuildable, never canonical.

*Cost and risk.* Charts invite scope creep toward a real UI. Constrain it to one
file, no network calls, regenerated on demand and never edited by hand.

### A living overview page

A single page in the bundle that answers "what is this knowledge base about?" —
maintained by the engine, rewritten as the base grows.

*Why it might matter.* `index.md` is a catalogue and `log.md` is a history;
neither orients a person who opens the bundle cold. This is also the first thing
an agent would want to read before answering anything.

*Cost and risk.* It is a synthesis, not an extraction, so it inherits every
question raised about filed answers: what type it is, whether it participates in
retrieval, and how it goes stale when the objects under it change. Should be
designed together with those, not before them.

---

## Surfacing absence, not just maintenance

### Gap detection

Today the engine reports pending *maintenance* — duplicates, contradictions,
untyped edges. It never reports **absence**. It could: a cluster of objects on a
topic with no Decision explaining why; a Person mentioned across several sources
but never developed; a Project with no Procedure attached; a Source whose
extraction produced nothing.

*Why it might matter.* A knowledge base that can say what it does not know is a
categorically different product from one that can only tidy what it has. It also
fits the stated philosophy directly: detecting the gap is the engine's job,
filling it is the human's.

*Cost and risk.* The failure mode is nagging. A gap report that lists everything
missing is noise; the value is in a small number of high-confidence gaps, which
means ranking, which means this depends on pending work being durable first.

### Stuck work

Freshness currently measures how old a claim is. It does not measure how long a
*pending decision* has gone unresolved — a duplicate group proposed repeatedly
and never acted on, a contradiction reported three sessions running.

*Why it might matter.* Repeatedly surfacing something the user keeps skipping is
a signal about the proposal, not about the user. It should either be escalated
or dropped, and either requires noticing it.

*Cost and risk.* Depends entirely on pending work being persisted. Not
independently actionable.

### Trends over the log

`log.md` is written on every operation and never read back. It contains the
material for a periodic report: what the base learned this month, which topics
grew, which went quiet, how curation load changed.

*Why it might matter.* It turns an append-only audit trail into a second
knowledge product at near-zero marginal cost, and it gives a reason to return to
the base that is not maintenance.

*Cost and risk.* The log format is currently written for humans and diffs, not
for parsing. Reading it back would either constrain the format or need a
parallel structured record.

---

## Durable state about how the user works

### Preferences as objects, not configuration

Durable facts about the operator — curation thresholds, which types matter for
this base, decisions already declined and why, house style for filed answers —
recorded as objects in the bundle rather than keys in a config file, and
consulted before any prompt that has a preference-shaped surface.

*Why it might matter.* Configuration answers "how is the engine set up".
Preferences answer "how does this person work", which is knowledge, changes over
time, and belongs where knowledge lives. It also stops the engine re-proposing
things that have already been rejected.

*Cost and risk.* This is the same underlying problem as durable pending work and
should be designed with it, not separately. Two mechanisms for remembering
judgments would be worse than none.

---

## Curation ergonomics

### Findings as a checklist, not a report

`lint` currently reports; the user then decides what to do and issues separate
commands. It could present findings as a list with the fixes it is able to
apply, and let the user select.

*Why it might matter.* The distance between "here is what is wrong" and "here is
what I fixed" is where maintenance workflows are abandoned. The write cores for
most lint findings already exist.

*Cost and risk.* Bulk application of writes needs the same care the curation
loop already applies: never accept a delete in bulk, always show the plan.

### A periodic ritual

A verb intended to be run on a cadence rather than on demand — an end-of-week
pass that reviews what came in, what is pending, and what went stale.

*Why it might matter.* A knowledge base is only as good as the habit around it.
Freshness tiers already imply a cadence; nothing in the product currently
invites one.

*Cost and risk.* Only worth building once there is durable pending work to
review. Otherwise it is `curate` with a different name.

### Compacting history

Older entries in the log and superseded state accumulate indefinitely. They
could be compacted into period summaries, with detail preserved in git rather
than in the working file.

*Why it might matter.* Every file that grows without bound eventually degrades
retrieval, diffs, and portability.

*Cost and risk.* Compaction is lossy by definition, and history is exactly the
thing users trust the format to preserve. Only acceptable if the full record
remains reachable.

---

## Agent-facing surface

These are for the runtime work and should be revisited when it starts.

### A self-configuration prompt

A short block of text in the README that the user pastes into their agent, after
which the agent writes its own connection configuration.

*Why it might matter.* Manual configuration is the largest drop-off point in
adopting any agent-facing server. Handing the task to the agent removes it.

*Cost and risk.* The prompt must not carry secrets, and it will drift from the
actual configuration format unless it is generated rather than hand-maintained.

### A named prompt per tool

Alongside each exposed tool, a matching named prompt so the capability
autocompletes inside the client rather than having to be discovered.

*Why it might matter.* This is the same problem `next` solves in the CLI — no
capability should require reading a reference to find. An agent-facing surface
has the same discovery gap and the same solution.

*Cost and risk.* Doubles the surface to keep in step. Worth generating from one
definition rather than maintaining two lists.

### The reference as a page in the bundle

Ship the verb and tool reference as an object inside the bundle, so an agent can
read its own manual through the same interface it uses for everything else.

*Why it might matter.* It is the strongest possible demonstration that the
format carries real documentation, and it removes a special case: the agent does
not need a second channel to learn what it can do.

*Cost and risk.* A generated page inside a user's bundle is engine-owned content
in a space the user believes is theirs. Needs to be clearly marked, excluded
from curation, and excluded from retrieval, or it will contaminate answers.

---

## Positioning

### State what OpenKOS is not

The README explains what the project is. It does not say what it is not — not a
search engine over your files, not a chat interface, not a note-taking app, not
a hosted service.

*Why it might matter.* Most readers arrive with a nearby category already in
mind and read the whole page through it. Naming the adjacent things and
declining them is faster than out-arguing them.

*Cost and risk.* Comparisons age badly and can read as defensive. Describe
categories, not competitors.

### An interoperability demonstration

Consume a bundle produced by a different OKF implementation, and confirm a
bundle produced by OpenKOS is readable by one.

*Why it might matter.* Standard alignment is currently a claim backed by
conformance to a written spec. A working exchange with an independent
implementation is evidence, and it is the kind of result the format's authors
explicitly asked the community to produce.

*Cost and risk.* The specification is a v0.1 draft, so disagreements found in
such a test may be ambiguities in the spec rather than bugs in either
implementation. That outcome is still worth having — it just needs to be
reported as a spec question, not a defect.
