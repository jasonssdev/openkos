# Maintaining OpenKOS

A guide for **maintainers** — how contributions are reviewed and decided. It complements [CONTRIBUTING.md](CONTRIBUTING.md) (for contributors) and [GOVERNANCE.md](GOVERNANCE.md) (how decisions are made). If you are here to contribute, start there; this document is about the maintainer's side of the flow.

## Mindset

- **Coherence over quantity.** The project's value is its consistency with a small set of principles. Protect that above merge count or contributor happiness.
- **Every merge is a long-term maintenance commitment.** Accepting code means owning it — understanding, fixing, and evolving it for years. The bar to enter is deliberately high.
- **Saying no is part of the job, not a failure.** A kind, well-reasoned no protects the project. Decline early, before a contributor has invested heavily.

## The flow

1. **Issue before PR.** For anything non-trivial, the idea is discussed in an issue (or a design-proposal issue) *before* code is written. This is the single most important rule — it prevents large PRs that don't fit and painful late rejections.
2. **Triage.** Respond promptly (even just "thanks, I'll look"), label it, and judge fit against the vision, roadmap, and principles. Mark suitable ones `good first issue`.
3. **Agree the approach** in the issue for significant changes, so the PR arrives pre-aligned in direction.
4. **Pull request + CI.** The contributor forks, branches, and opens a PR. CI runs automatically (tests, Ruff, MyPy) so review focuses on substance, not formatting.
5. **Review → iterate → merge or close.**

## Review checklist (in priority order)

Review in this order; don't spend time on a later item if an earlier one fails.

1. **Fit and principles** — does it align with the vision, roadmap, and guiding principles (local-first, OKF-conformant, reconstructible, provenance and freshness first-class, human-in-the-loop)? A technically excellent change that violates a principle is a no.
2. **Correctness** — is it right? Tests included, with edge cases, and a regression test for bug fixes?
3. **Scope** — one logical change per PR. Ask to split large or mixed PRs.
4. **Maintenance cost** — new dependencies (each is forever), added abstractions, complexity. Would you want to maintain this in two years?
5. **Docs** — behavior, interface, or model changes update the docs.
6. **Style / format** — leave it to CI. Do not bikeshed.

Tone: be specific, explain the *why* behind each requested change, and praise good work. A review that only points out flaws drives people away.

## Accept or reject

**Accept** when it aligns with vision/roadmap/principles, is correct and tested, is reasonably scoped, and adds no undue maintenance.

**Reject or redirect** when it is out of scope, conflicts with a principle or an ADR, is too large or unfocused, adds heavy dependencies, lacks tests, or is a design you don't want to commit to maintaining. A good PR can still be a no if it takes the project somewhere it shouldn't go — that is a legitimate maintainer call.

Anchor decisions in **documented principles and recorded decisions, not personal taste**. "This conflicts with our local-first principle" or "this is outside the current MVP's contribution surface (see the roadmap)" is objective and impersonal. Once the ADR log begins (with the first code-time decision), cite the relevant ADR the same way.

**The safe zone:** most contributions should target the plugin surface — producers and consumers behind interfaces — which is isolated from the core and low-risk to accept or decline. Prepare `good first issue`s and example plugins there.

## Saying no well

Thank the contributor, explain the reason tied to something documented, offer an alternative if one exists, and keep it warm:

> Thanks for the work here. This adds a cloud service, which conflicts with our local-first principle, so I won't merge it — but an optional adapter behind the `VectorStore` interface would fit. Want to open an issue to design it?

## Automation and protection

- Require **green CI** (tests, lint, type check) and a review before any merge; enable branch protection.
- The PR template checklist prompts contributors to self-review.
- Use labels and `good first issue` to channel help.
- **Never merge code you don't understand**, especially from unknown forks. Don't run privileged CI on untrusted pull requests. For security reports, follow [SECURITY.md](SECURITY.md).

## Legal

OpenKOS is Apache-2.0. Contributions are **inbound = outbound**: by contributing, a contributor licenses their work under the same terms (stated in CONTRIBUTING.md). A DCO sign-off is an optional lightweight step the project may adopt later; a formal CLA is not planned.

## Sustainability

You don't owe anyone a merge or an immediate response — support is best-effort. Decline early, value non-code contributions (docs, triage, reports, reviews), and grow co-maintainers over time (see [GOVERNANCE.md](GOVERNANCE.md)). Don't over-engineer the process before there are contributors: start with "issue before PR + CI + kind review" and add process only as volume requires.
