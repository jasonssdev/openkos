# Proposal: activity-view

## Intent

Refs #1698. The app has no view of what the engine is doing, so a person cannot
tell whether the folder watch or a maintenance pass is running, whether the
model is busy, or what is queued, and cannot judge when it is reasonable to
start "Check all", which competes for the same model.

Success: an Activity screen shows what runs now, what waits, today's model use
against the daily limit, and what ran lately, in plain words, read-only, and an
absent or damaged job record shows an honest empty or unavailable state, never
an error page.

## Scope

### In Scope

1. `application/unattended_activity.py`: `read_engine_activity`, reusing the
   job record's existing read path (`jobs.recent_jobs`, `chat_calls_since`,
   `observations`, `uncommitted_paths`) and the budget's local-day boundary.
   No new SQL.
2. `openkos.api.engine_activity` and the types `EngineActivity`, `EngineJob`
   and `DailyBudget`; additive, `API_VERSION` unchanged.
3. `GET /v1/activity` on the local API, read-only.
4. Client `engineActivity()`, regenerated schema, and the Activity screen under
   `desktop/ui/activity/` with one sidebar entry.

### Out of Scope

- Controls: no pause, stop or "run now". The screen only reads.
- Notifications on a limit reached (the watch status already covers it).
- A per-task live progress count: the job record only holds counts at the end.

## Approach

"Running now" is the newest job when it has no end and began within its
deadline plus a short grace; an older open row is a process that died and is
not shown as running. "Waiting" is the watched folder's files not yet taken
(new or changed, or deferred on the budget) and the paths written but not yet
committed. "Today" is the local calendar day, as the runner counts it.

No ADR: nothing here is hard to reverse; the surface is additive.
