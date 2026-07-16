# Examples

## `good-life-demo`

A small, hand-written OpenKOS **workspace** used as a **reference** and a **test fixture**. It shows what the MVP 1 `ingest` should produce.

The scenario is deliberately ordinary: someone reading philosophy to write an essay. On 5 July they take notes on Epictetus's *Enchiridion*. On 14 July a friend who studies the subject corrects one of their readings on a call, and the base changes accordingly. That is the whole loop — capture, compile, learn, correct.

```
good-life-demo/
├── openkos.yaml        # engine config, including the layout
├── AGENTS.md           # operating manual
├── raw/                # two immutable sources — outside the bundle
│   ├── notes-on-the-enchiridion-2026-07-05.txt
│   └── call-with-maria-2026-07-14.txt
└── bundle/             # the OKF bundle root
    ├── index.md        # catalog (carries okf_version)
    ├── log.md          # history
    ├── sources/        # one Source concept per raw original
    ├── concepts/       # Stoicism, Epicureanism
    ├── people/         # Maria Salazar
    └── decisions/      # Frame the essay on the dichotomy of control
```

Open it in any markdown editor to see the shape of a workspace, or treat it as the expected output when building the compiler.

### What each part is there to demonstrate

**A living object.** `concepts/stoicism.md` is at **v2**. At v1 it read *apatheia* as "indifference to emotion" — the common misreading, straight from the English cognate *apathy*. The call corrected it to freedom from the destructive passions, and the page was rewritten. The body carries the current understanding; `log.md` and git carry the fact that it changed. That division is the model, not a style choice.

**Version and freshness are different axes.** Both concepts are `timeless` — what a Hellenistic school taught does not decay, so neither page needs a date. Yet Stoicism still went from v1 to v2. **The version rose because the reader learned more; freshness would only move if the world changed.** The two are easy to blur — a page that changed *feels* like a page that went stale — so the fixture keeps a case where only one of them moves.

**The volatile fact lives where volatile facts actually live.** `people/maria-salazar.md` is `freshness: pointer`: her current teaching post carries an `(as of 2026-07-14)` stamp and defers to the faculty page in `resource`. Roles change — they are the canonical volatile fact. Concepts mostly do not.

**Sensitivity propagates, and it propagates over time.** This is the part worth reading twice. `concepts/stoicism.md` was **private** at v1, compiled only from private reading notes. At v2 a **confidential** source touched it — so by the high-water-mark rule it became confidential, and `log.md` records the raise. Its sibling `concepts/epicureanism.md`, which the call never touched, is still private.

The consequence is uncomfortable and worth stating plainly: Stoicism is public knowledge, but this bundle's page about it is confidential — because of *where the reader learned it*, not what it says. The rule over-classifies rather than leak, which is the right default and a real cost. The escape hatch is a human: verify the claim against a public source and downgrade the object by hand.

### OKF conformance

`bundle/` is conformant with **OKF v0.1** and is meant to stay that way; it doubles as the fixture for the conformance tests. Each of these is the spec, not a preference:

- No object has an `id` field — a concept's identity is its path (§2).
- `index.md` and `log.md` carry no frontmatter, except the root `index.md`, which carries `okf_version` and nothing else (§6, §11).
- `log.md` uses `## YYYY-MM-DD` headings, newest first (§7).
- Links are untyped: `- [Epicureanism](/concepts/epicureanism.md) — contrasted with` puts the *kind* of relationship in the prose, exactly as §5.3 prescribes.
- Citations point at Source concepts inside the bundle (`/sources/call-with-maria-2026-07-14.md`), never at `raw/` directly — the pattern §8 describes. Only a Source concept's `resource` reaches outside the bundle, so every internal link always resolves.

**Why `raw/` is not inside `bundle/`.** An OKF bundle is a bundle of *concepts*; raw sources are input material. Keeping them apart means `bundle/` holds concept documents and nothing else — so it is conformant by construction (nothing dropped into `raw/` can break it), sources keep their own names and extensions, and `bundle/` can be shared on its own as pure OKF without dragging the originals along. See [`../docs/architecture.md`](../docs/architecture.md).
