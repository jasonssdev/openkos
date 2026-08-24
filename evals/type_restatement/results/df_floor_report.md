# Can corpus frequency separate the surviving token? (#837)

Document frequency is counted over **527** distinct normalized keys, through the same token pipeline the near-match rule scores with. Deterministic, stdlib-only -- no model, no GPU.

Adjudicated delta pairs: **18** duplicate, **11** distinct.

| ruling | maxDF | minDF | required tokens (df) | pair |
| --- | --- | --- | --- | --- |
| distinct | 6 | 6 | `ranking` | `decision sobre el re ranking del retrieval || re ranking procedure` |
| distinct | 6 | 6 | `ranking` | `re ranking procedure || retrieval re ranking project` |
| distinct | 7 | 7 | `onboarding` | `capacitacion del equipo nuevo con un procedimiento de onboarding || onboarding procedure` |
| distinct | 9 | 6 | `retrieval`, `ranking` | `re ranking del retrieval con el judge ensemble || retrieval re ranking project` |
| distinct | 27 | 7 | `knowledge`, `recovery` | `knowledge recovery project || migracion del knowledge recovery system al nuevo formato de bundle` |
| distinct | 27 | 7 | `knowledge`, `recovery` | `knowledge recovery project || reunion del equipo de knowledge recovery system` |
| distinct | 27 | 7 | `recovery`, `knowledge` | `migracion del knowledge recovery system al nuevo formato de bundle || recovery of knowledge project` |
| distinct | 27 | 7 | `recovery`, `knowledge` | `recovery of knowledge project || reunion del equipo de knowledge recovery system` |
| distinct | 38 | 18 | `remote`, `control`, `design` | `meeting discussion on remote control design || remote control design project` |
| distinct | 38 | 18 | `remote`, `control`, `design` | `remote control design project || remote control design specifications` |
| distinct | 38 | 18 | `remote`, `control`, `design` | `remote control design project || user preferences for remote control design` |
| duplicate | 6 | 6 | `ranking` | `re ranking del retrieval con el judge ensemble || re ranking procedure` |
| duplicate | 6 | 6 | `ranking` | `re ranking del retrieval || re ranking procedure` |
| duplicate | 6 | 6 | `ranking` | `re ranking procedure || retrieval re ranking with judge ensemble` |
| duplicate | 7 | 7 | `onboarding` | `onboarding procedure || procedimiento de onboarding` |
| duplicate | 7 | 7 | `onboarding` | `onboarding procedure || procedimiento de onboarding para el equipo nuevo` |
| duplicate | 9 | 6 | `retrieval`, `ranking` | `retrieval re ranking project || retrieval re ranking with judge ensemble` |
| duplicate | 13 | 13 | `evaluation`, `pipeline` | `evaluation pipeline and language leakage harness || evaluation pipeline project` |
| duplicate | 13 | 13 | `evaluation`, `pipeline` | `evaluation pipeline and language leakage measurement || evaluation pipeline project` |
| duplicate | 27 | 7 | `knowledge`, `recovery` | `knowledge recovery project || knowledge recovery system` |
| duplicate | 27 | 7 | `knowledge`, `recovery` | `knowledge recovery project || knowledge recovery system integration` |
| duplicate | 27 | 7 | `recovery`, `knowledge` | `knowledge recovery system integration || recovery of knowledge project` |
| duplicate | 38 | 4 | `button`, `less`, `remote` | `button less remote project || development of a button less remote` |
| duplicate | 38 | 4 | `button`, `less`, `remote` | `button less remote project || development of a button less remote control` |
| duplicate | 38 | 18 | `remote`, `control`, `design` | `remote control design for television || remote control design project` |
| duplicate | 38 | 18 | `remote`, `control`, `design` | `remote control design project || remote control interface design` |
| duplicate | 38 | 18 | `remote`, `control`, `design` | `remote control design project || television remote control interface design` |
| duplicate | 56 | 17 | `research`, `agent`, `development` | `development of multi agent research application || research agent development project` |
| duplicate | 56 | 17 | `research`, `agent`, `development` | `multi agent research application development || research agent development project` |

## Identical requirements, opposite rulings

Every statistic a floor could consult is a function of the required tokens alone, so these groups are undecidable by ANY corpus-frequency rule, at any threshold:

- {`control`, `design`, `remote`}
- {`knowledge`, `recovery`}
- {`onboarding`}
- {`ranking`}
- {`ranking`, `retrieval`}

## Zero-false-positive operating points

| statistic | largest zero-FP floor | duplicates kept |
| --- | --- | --- |
| maxDF | 5 | 0 of 18 |
| minDF | 5 | 2 of 18 |

**Verdict:** REFUTED -- 5 required-token set(s) carry both rulings, so no frequency floor -- indeed no corpus statistic at all -- separates them, and the best zero-false-positive floor keeps 2 of 18 adjudicated duplicates.
