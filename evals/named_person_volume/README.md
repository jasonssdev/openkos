# `named_person_volume` — how many merely-named people, and does it cost a Decision? (#712 slice 1)

#712 rules that a person who is only named must still become a `Person`.
#706 measured that `_has_participant_anchor` never fired — the ACTIVE
suppressor is `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT`'s own anchor demand.
Nobody has measured how many merely-named people a real transcript yields
once that demand is gone, or whether removing it costs a genuine subject.
This eval measures both, before any prompt-level change ships.

```bash
uv run python -u evals/named_person_volume/run.py --self-test
uv run python -u evals/named_person_volume/run.py --runs 3
uv run python -u evals/named_person_volume/run.py --rescore
```

**Use `-u`.** Piping through `tee` makes Python buffer and a long run looks
hung.

## Arms and fixtures

| arm | what it is |
| --- | --- |
| `baseline` | the SHIPPED `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` |
| `treatment` | design D2's rewrite, applied as a monkeypatch on the module constant — production is never edited by this file |

| fixture | what it is |
| --- | --- |
| `es-bare` | constructed Spanish meeting; every person is named (`Ana`, `Bruno`, `Carla`) and nothing else is ever said about any of them |
| `ami-ts3005a` | the real AMI corpus meeting `TS3005a` (single-letter speaker labels, every personal name elided by the corpus itself) |

## The four metrics

- **A** — distinct `Person`/`Organization` titles in `retained`, per run.
- **B** — of those, how many are *merely named* (no stated role/affiliation/
  relation) — from hand-written `adjudication.json`, keyed
  `<fixture>::<type>::<title>`, never regex-derived (#706 precedent).
- **C** — subject recall: `Decision`/`Event`/`Concept`/`Procedure` titles in
  `retained`, matched against a hand-written expected-subject list per
  fixture. Exists so a prompt that wins on people while losing a Decision
  cannot read as an unqualified pass.
- **D** — `produced`/`retained`, `judge_status`, run latency.

## The capacity number

`p_max` — the largest distinct participant count in any TREATMENT run,
across both fixtures — feeds `_PARTICIPANT_BACKSTOP = max(8, ceil(1.5 *
p_max))` (design D3), reported as DERIVED, never chosen.

## The REJECT rule

Any ONE of these rejects the treatment:

1. Subject recall (metric C) drops below baseline on either fixture.
2. Run latency >= 1.5x baseline.
3. The merely-named person count does not increase over baseline (no
   benefit bought).
4. Any proposed name is absent from the source on a name-bearing fixture
   (fabrication) — `es-bare` is name-bearing (`Ana`/`Bruno`/`Carla` are
   stated); `ami-ts3005a` is not (the corpus elides every personal name),
   so rule 4 never fires against `ami-ts3005a` by construction.

Rejection ships nothing prompt-level; the treatment stays in this harness
as a reproducible monkeypatch, exactly like #613/#622/#630/#706's own
rejected treatments.

## `--self-test`

Runs the whole path against a scripted backend, no model. Its first
assertion is that the recording seam captured anything at all: a probe
that silently measures nothing while exiting 0 is the exact failure mode
`evals/participant_anchor` was built to guard against. It also proves the
treatment monkeypatch installs, is sent to the backend, and is restored —
and that a renamed `_PARTICIPANT_CAPTURE_SYSTEM_PROMPT` raises rather than
patching nothing.

## What it measured

See `report.md`.
