# Interaction API

`POST /interact` is Beacon's small, stable front door. It authenticates with
`X-Beacon-API-Key`, converts input to a validated `StructuredIntent`, and then
delegates all decisions and mutations to existing deterministic services.

## Natural-language intake

The configured interpreter is selected with `BEACON_INTERPRETER`. `rules` is the
offline default; `gemini` uses structured JSON output and requires
`GEMINI_API_KEY`. Both return the same validated `StructuredIntent`; neither can
call integrations. Beacon's deterministic planner and executor own all actions.

The built-in rules interpreter intentionally supports a narrow offline grammar:

- Daily Brief/status phrases such as `What's on today?`, `brief tomorrow`, or
  `status`.
- Scheduling by title: `Schedule lighting paperwork tomorrow`.
- Scheduling by ID: `Schedule task 42 today for 90 minutes` or `Schedule #42`.
- Durations in minutes or hours. The configurable default is 60 minutes.
- Task creation such as `Buy Liquid IV tomorrow` or `Create a task to file taxes`.

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
    "intent": "SCHEDULE_TASK",
    "task_id": 42,
    "deadline": "2026-08-04",
    "duration_minutes": 90
  }
}
```

Supported intents are `BRIEF`, `CREATE_TASK`, `SCHEDULE_TASK`, and `UNKNOWN`. A
scheduling intent must name exactly one task ID or title. The prior `action`,
`task_title`, and `target_date` input names remain accepted for compatibility,
but responses use intent vocabulary. The external interpreter cannot select a
calendar slot or invoke integrations; `ActionPlanner` selects operations and
`ActionExecutor` delegates scheduling to `SchedulerService`, which remains
responsible for ranking, duplicate prevention, and create/update behavior.

## Response

Every success includes human-readable `result`, the accepted `intent`, and an
`actions_taken` audit-shaped list. `brief` or `schedule` contains the complete
typed result used to produce it. Brief actions are marked `READ_ONLY`; schedule
actions expose `NEW`, `UNCHANGED`, `UPDATED`, or `RECOMMENDATION_ONLY`.
