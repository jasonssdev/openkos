---
type: Vision
title: OpenKOS Vision
description: Vision, philosophy, and positioning of the OpenKOS project — the local-first engine for the Open Knowledge Format.
tags:
  - openkos
  - vision
  - personal-knowledge-management
  - local-first
  - open-knowledge-format
  - okf
  - ai
resource: https://github.com/jasonssdev/openkos
timestamp: 2026-07-14T21:00:00Z
sensitivity: public
---

# Vision

## One sentence

OpenKOS is an open-source, local-first engine that turns your scattered text into a living, portable knowledge base your AI agents can actually use — built on the **Open Knowledge Format (OKF)** so your knowledge is never locked to any app, model, or vendor.

---

## The problem

Every capable AI assistant starts each conversation from zero. You re-explain the same context, and the insights you build together vanish into chat history. In parallel, your notes pile up in folders, wikis, and documents that nobody keeps current. Two powerful capabilities — your accumulated knowledge and modern language models — sit side by side, disconnected.

The deeper problem underneath is this: **the maintenance cost of knowledge does not scale.** No one can indefinitely organize, update, cross-reference, and reconcile what they know, so every personal knowledge system eventually decays — the burden grows faster than the value. Language models are what finally make a different outcome possible, but they are the enabling technology, not the reason OpenKOS exists. OpenKOS exists to remove that maintenance burden so knowledge can *compound* — grow more useful over the years instead of harder to keep.

The usual answer is retrieval-augmented generation (RAG): index your documents and let the model fetch chunks at query time. But RAG rediscovers everything on every question. Nothing accumulates. The cross-references are never drawn, contradictions are never reconciled, and the synthesis is re-derived from scratch each time.

Andrej Karpathy described a better pattern — the **LLM Wiki**: instead of only retrieving from raw sources, the model *incrementally builds and maintains* a persistent, interlinked knowledge base that sits between you and your sources. Knowledge is compiled once and then kept current. The wiki compounds.

The pattern is powerful, but until recently every implementation was bespoke and every knowledge base was a silo.

---

## What changed: a real standard exists now

The idea of a personal, curated, interlinked knowledge store is old — it runs from Vannevar Bush's Memex (1945) through hypertext, personal knowledge management, and most recently Karpathy's LLM Wiki. OpenKOS sits in that lineage. What the lineage lacked was a shared, open way to *write knowledge down* so different tools could cooperate — and in June 2026 that arrived. Google Cloud published the **Open Knowledge Format (OKF)** — a vendor-neutral, open specification that formalizes the LLM Wiki pattern into a portable, interoperable format. OKF is deliberately minimal: a directory of markdown files with YAML frontmatter, where each file is a *concept*, files link to each other to form a graph, and only one field (`type`) is required.

Google's own framing is the key insight for us: *"What's missing is a format, not another service."* They shipped the format plus reference implementations aimed at enterprise cloud data (an agent that documents BigQuery tables, and a visualizer), and then explicitly invited the community to build producers, consumers, and agents around it.

That invitation is the opening OpenKOS takes.

---

## The OpenKOS bet

**We do not invent a format. We build the engine, and we adopt the lingua franca.**

Google built OKF for organizations and their cloud data warehouses. Nobody has built the counterpart for *individuals*: a private, local-first engine that produces and consumes OKF knowledge on your own machine, from your own text, with your own models. That is OpenKOS.

OpenKOS is simultaneously:

- an **OKF producer** — it reads your raw text and compiles conformant OKF concept documents, with provenance;
- an **OKF consumer** — it retrieves, reasons over, and answers questions from an OKF bundle, always with citations;
- a **local runtime** — a CLI, API, and agent interface that keeps the bundle honest and current over time.

Because the output is *just an OKF bundle* — plain markdown files — it opens in Obsidian, VS Code, GitHub, or any tool, and it can exchange knowledge with any other OKF-speaking system, including Google's. Standing on OKF means your knowledge is interoperable from day one, not trapped inside OpenKOS.

The name says it: OpenKOS is an **Open Knowledge Orchestration System**. It orchestrates the whole lifecycle of your knowledge — sources, knowledge objects, the graph, embeddings and indexes, local models, provenance, and freshness, plus the agents that act on all of it. Knowledge is one component; OpenKOS coordinates everything around it. And your knowledge **lives with you and moves freely** — local-first ownership and open portability are not a trade-off; you get both.

---

## Philosophy

Six principles govern every design decision.

**1. Local-first and private by default.** OpenKOS runs on your machine and works offline. It is designed for local model runtimes (for example Ollama) so your knowledge never has to leave your computer. The cloud is an option you may choose, never a requirement we impose. The software is temporary; the knowledge is permanent.

**2. Standard-aligned, not bespoke.** Storage and interchange are OKF. We contribute to and track the standard rather than reinventing it. Adopting an open, vendor-neutral format is the *most* agnostic choice available — it is the opposite of lock-in. Standards evolve, and OpenKOS evolves with them: we adopt the best open representation available — OKF today — and stay compatible with future ones rather than being married to any single spec forever. The philosophy holds even if a given format is superseded.

**3. Living knowledge, honest over time.** Raw sources are immutable — we read from them, never rewrite them. Concept documents, by contrast, are living: they are rewritten as you learn, and their history is preserved. Every fast-changing fact carries a freshness stamp so that nothing silently rots into a confident lie.

**4. The human curates; the engine maintains.** You choose sources, explore, and ask the questions that matter. The engine does the bookkeeping humans abandon — extraction, cross-linking, deduplication, freshness checks, index upkeep. Consequential changes stay reviewable, not silently automatic. Humans think and decide; the engine reduces cognitive maintenance, not cognitive responsibility.

**5. Reconstructible and explainable.** Every index, embedding, and graph projection must be rebuildable from the canonical OKF bundle plus the immutable sources. Retrieval always cites where an answer came from. Nothing important is hidden in an opaque store you cannot regenerate.

**6. Representation, not truth.** OpenKOS stores *representations* of knowledge — how you understand and document things — not truth itself. It is not an epistemic authority: it does not decide what is objectively true, and it lets multiple, even conflicting, perspectives coexist, each preserving its own context (source, assumptions, evidence, confidence expressed qualitatively). This is distinct from freshness: the engine reconciles what is *out of date*, but it does not adjudicate what is *contested*. OpenKOS preserves representations; it does not validate them — validation, when needed, is the work of humans or specialized agents, never a responsibility the system assumes. Storing a claim is not endorsing it.

---

## Who it is for

OpenKOS is for knowledge-intensive people who want durable, private ownership of what they know: researchers, software and AI engineers, product managers, consultants, founders, writers, students, and lifelong learners. It especially fits people who value local-first software, privacy by design, open standards, explainable systems, and knowledge that compounds across years and projects rather than decaying.

---

## How we are different

The space already has strong reference points. OpenKOS defines itself precisely against them.

- **Karpathy's LLM Wiki** is the seminal idea, deliberately abstract — a pattern, not an implementation. OpenKOS is a concrete engine that instantiates the pattern.
- **Obsidian-based tools** (such as obsidian-mind and obsidian-second-brain) are excellent, but they live inside Obsidian and are packaged as prompt/skill conventions for a specific coding agent. OpenKOS is a standalone, app-agnostic engine whose output is portable OKF, browsable in any editor and not tied to one application.
- **Google's OKF reference stack** targets enterprise cloud data (BigQuery) and is cloud-oriented. OpenKOS targets the individual, runs entirely locally, and is private by default.

The wedge in one line: **OpenKOS is the local-first, personal producer-consumer-runtime for the Open Knowledge Format that nobody else has built.**

---

## What OpenKOS is not

OpenKOS is infrastructure for your knowledge, and it deliberately declines roles that belong to you or to other kinds of software. It is **not**:

- a judge of truth or an epistemic authority;
- a recommendation engine or a belief system;
- a social network or a cloud platform;
- a proprietary knowledge database or a single fixed AI model.

Its job is to organize, connect, and preserve your knowledge. The deciding, believing, and interpreting stay with you.

---

## What success looks like

OpenKOS succeeds when knowledge compounds instead of decaying. A person using it can:

- compile scattered text into a structured, cited, OKF-conformant knowledge base;
- trust that knowledge because provenance and freshness are first-class;
- retrieve trustworthy answers with citations, and file good answers back as new knowledge;
- carry that knowledge across apps, models, and years without migration;
- switch AI models or editors freely, because the format — not the tool — holds the value.

The long-term vision is to be the trusted, open, local-first reference implementation for AI-native personal knowledge — the engine people reach for when they want a knowledge base that is theirs, portable, explainable, and future-proof.
