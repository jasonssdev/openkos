# A title that restates its own type (#804)

Population: **558** distinct stored titles, **155372** pairs compared. Deterministic, stdlib-only -- no model, no GPU.

Types are assigned SYNTHETICALLY, at maximum exposure: every title is treated as being of the type it names. A real bundle excuses a subset of this, so the counts below are an UPPER BOUND -- which is why the false positives are named, not merely counted.

| metric | count |
| --- | --- |
| newly matched (the rule is additive) | 29 |
| **exposed** (adjudicated) | 29 |
| recovered duplicates | 18 |
| **false positives** | 11 |
| unadjudicated | 0 |

**Verdict:** REFUTED -- the rule reports pairs adjudicated as distinct. The #630 bar is ONE false positive, and these survive real typing: an Event about designing a remote is not the Project, and a Decision about re-ranking is not the Procedure. The surviving token is what decides, and 'helios' is structurally identical to 'ranking' -- no pairwise lexical rule separates them.

## False positives

- 'Capacitación del equipo nuevo con un procedimiento de onboarding' || 'Onboarding Procedure' (excused 'procedure')
- 'Decisión sobre el re-ranking del retrieval' || 'Re-ranking Procedure' (excused 'procedure')
- 'Knowledge Recovery Project' || 'Migración del knowledge recovery system al nuevo formato de bundle' (excused 'project')
- 'Knowledge Recovery Project' || 'Reunión del equipo de knowledge recovery system' (excused 'project')
- 'Meeting Discussion on Remote Control Design' || 'Remote Control Design Project' (excused 'project')
- 'Migración del knowledge recovery system al nuevo formato de bundle' || 'Recovery of Knowledge Project' (excused 'project')
- 'Re-ranking Procedure' || 'Retrieval Re-ranking Project' (excused 'procedure')
- 'Re-ranking del retrieval con el judge ensemble' || 'Retrieval Re-ranking Project' (excused 'project')
- 'Recovery of Knowledge Project' || 'Reunión del equipo de knowledge recovery system' (excused 'project')
- 'Remote Control Design Project' || 'Remote Control Design Specifications' (excused 'project')
- 'Remote Control Design Project' || 'User Preferences for Remote Control Design' (excused 'project')

## Recovered duplicates

- 'Button-less Remote Project' || 'Development of a Button-less Remote Control' (excused 'project')
- 'Button-less Remote Project' || 'Development of a Button-less Remote' (excused 'project')
- 'Development of Multi-Agent Research Application' || 'Research Agent Development Project' (excused 'project')
- 'Evaluation Pipeline Project' || 'Evaluation Pipeline and Language Leakage Harness' (excused 'project')
- 'Evaluation Pipeline Project' || 'Evaluation Pipeline and Language Leakage Measurement' (excused 'project')
- 'Knowledge Recovery Project' || 'Knowledge Recovery System Integration' (excused 'project')
- 'Knowledge Recovery Project' || 'Knowledge Recovery System' (excused 'project')
- 'Knowledge Recovery System Integration' || 'Recovery of Knowledge Project' (excused 'project')
- 'Multi-Agent Research Application Development' || 'Research Agent Development Project' (excused 'project')
- 'Onboarding Procedure' || 'Procedimiento de Onboarding para el Equipo Nuevo' (excused 'procedure')
- 'Onboarding Procedure' || 'Procedimiento de Onboarding' (excused 'procedure')
- 'Re-ranking Procedure' || 'Re-ranking del Retrieval' (excused 'procedure')
- 'Re-ranking Procedure' || 'Re-ranking del retrieval con el judge ensemble' (excused 'procedure')
- 'Re-ranking Procedure' || 'Retrieval Re-ranking with Judge Ensemble' (excused 'procedure')
- 'Remote Control Design Project' || 'Remote Control Design for Television' (excused 'project')
- 'Remote Control Design Project' || 'Remote Control Interface Design' (excused 'project')
- 'Remote Control Design Project' || 'Television Remote Control Interface Design' (excused 'project')
- 'Retrieval Re-ranking Project' || 'Retrieval Re-ranking with Judge Ensemble' (excused 'project')
