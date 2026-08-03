# Interaction API

`POST /interact` is Beacon's small, stable front door. It authenticates with
`X-Beacon-API-Key`, converts input to a validated `StructuredIntent`, and then
delegates all decisions and mutations to existing deterministic services.

## Natural-language minimum

The built-in interpreter intentionally supports a narrow offline grammar:

- Daily Brief/status phrases such as `What's on today?`, `brief tomorrow`, or
  `status`.
- Scheduling by title: `Schedule lighting paperwork tomorrow`.
- Scheduling by ID: `Schedule task 42 today for 90 minutes` or `Schedule #42`.
- Durations in minutes or hours. The configurable default is 60 minutes.

`today` and `tomorrow` use `BEACON_TIMEZONE`. Explicit day requests search from
09:00 through 22:00 on that day; requests without a day use now as the earliest
time and the Vikunja due date as the deadline. Title lookup first requires an
exact normalized match, then permits a unique substring match. Beacon returns a
conflict instead of choosing among ambiguous tasks.

The fallback is deliberately not a general chatbot. Unsupported messages return
`400` with guidance and perform no action.

## Structured-intent boundary

An upstream Gemini or n8n workflow may send an already interpreted intent in the
same request. The intent is validated by Pydantic and is authoritative when both
fields are supplied:

```json
{
  "intent": {
    "action": "SCHEDULE_TASK",
    "task_id": 42,
    "target_date": "2026-08-04",
    "duration_minutes": 90,
    "create_event": true
  }
}
```

Supported actions are `BRIEF` and `SCHEDULE_TASK`. A scheduling intent must name
exactly one `task_id` or `task_title`. The external interpreter cannot select a
calendar slot or invoke integrations; `InteractionService` resolves the task and
calls `SchedulerService`, which remains responsible for ranking, duplicate
prevention, and create/update behavior.

## Response

Every success includes human-readable `result`, the accepted `intent`, and an
`actions_taken` audit-shaped list. `brief` or `schedule` contains the complete
typed result used to produce it. Brief actions are marked `READ_ONLY`; schedule
actions expose `NEW`, `UNCHANGED`, `UPDATED`, or `RECOMMENDATION_ONLY`.
