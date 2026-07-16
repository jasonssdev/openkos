# Governance

This document describes how decisions are made in OpenKOS and how the project is organized. It is intentionally lightweight, matching the project's current stage, and is expected to evolve as the community grows.

## Current stage

OpenKOS is **pre-alpha** and led by its founder and lead maintainer, **Jason ([@jasonssdev](https://github.com/jasonssdev))**. At this stage the lead maintainer is the final decision-maker, so that the project can establish a coherent vision and architecture before opening up broader shared ownership. This is a starting point, not a permanent structure — see [Evolving this model](#evolving-this-model).

## Roles

**Users** — anyone who uses OpenKOS. Feedback, questions, and bug reports are contributions and are valued as such.

**Contributors** — anyone who opens an issue or a pull request. You do not need to be added to any list; contributing is open to everyone who follows the [Code of Conduct](CODE_OF_CONDUCT.md) and [Contributing guide](CONTRIBUTING.md).

**Maintainers** — contributors trusted with review and merge rights. Maintainers review pull requests, triage issues, guide design discussions, and keep the project consistent with its principles. During pre-alpha the lead maintainer is the sole maintainer.

**Lead maintainer** — sets overall direction, has final say on decisions that cannot reach consensus, and is responsible for the project's long-term coherence.

## How decisions are made

We aim for **consensus first**. Most changes are decided in the open, on the relevant issue or pull request, through discussion.

- **Small changes** (bug fixes, docs, tightly scoped improvements): decided by normal review on the pull request.
- **Significant changes** (anything touching the knowledge model, OKF conformance, the ingestion pipeline, public interfaces, or the project's principles): start with a **design proposal** issue (there is a template for it). These are discussed openly before implementation.
- **Architecture Decision Records (ADRs):** consequential technical decisions are recorded as ADRs in `docs/adr/`, so the reasoning is preserved for contributors and future maintainers.
- **When consensus is not reached:** the lead maintainer makes the final call, and records the reasoning (usually as an ADR).

Every decision is expected to be consistent with the guiding principles in [CONTRIBUTING.md](CONTRIBUTING.md) — local-first, OKF-conformant, reconstructible, provenance and freshness as first-class, and human-in-the-loop.

## Becoming a maintainer

There is no application form. Maintainers are invited based on a demonstrated track record: sustained, high-quality contributions; sound review and design judgment; and consistent adherence to the project's principles and Code of Conduct. As the contributor base grows, existing maintainers (initially the lead maintainer) extend invitations. Maintainers may step down at any time, and inactive maintainers may be moved to emeritus status.

## Evolving this model

As OpenKOS matures — more maintainers, more adopters, and a clearer role as an OKF-ecosystem implementation — this governance is expected to become more distributed: a maintainer team with shared merge rights, a documented proposal/RFC process, and clearer separation between the lead maintainer's role and day-to-day decisions. Changes to this document follow the same process as any other significant change: a design proposal, open discussion, and an ADR capturing the outcome.

## Licensing of contributions

OpenKOS is licensed under the [Apache License 2.0](LICENSE). By contributing, you agree that your contributions are licensed under the same terms.
