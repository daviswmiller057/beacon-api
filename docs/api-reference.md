# API reference

The FastAPI application title is `Beacon API`, version `0.3.0`. See [Data models](data-models.md).

## Authentication

Protected endpoints require `X-Beacon-API-Key`. Missing or incorrect values return
`401` with `{"detail":"Invalid Beacon API key"}`. `/health` is public.

## `GET /health`

Success `200`: `{"status":"ok","service":"beacon-api"}`.

## `GET /status`

Requires API key. Returns a secret-safe service/configuration snapshot: version,
timezone, calendar names, schedule calendar, configured integration flags, and
supported interaction modes. It does not contact external services; use `/brief`
to observe current calendar/task availability.

## `GET /brief`

Requires API key. This is the automation-friendly alias for
`GET /v1/brief/daily`, including the optional `date=YYYY-MM-DD` query parameter.

## `POST /interact`

Requires API key. Accepts `InteractRequest` with a natural-language `message`, a
validated `intent`, or both. When both are supplied, `intent` is authoritative.
Success returns `InteractResponse` containing `result`, the accepted structured
intent, `actions_taken`, and either a full `brief` or `schedule` result.

The local interpreter supports brief/status requests and scheduling a Vikunja
task by title or ID, with optional `today`, `tomorrow`, and duration phrases.
Unsupported intake is `400`; missing task is `404`; ambiguity, completed tasks,
duplicate-marker ambiguity, and no availability are `409`; invalid bounds are
`422`; upstream failures are `502`.

## `POST /v1/availability`

Requires API key. Body: `AvailabilityRequest`. Success `200`: `AvailabilityResponse`, including zero or more ranked options.

Any exception inside the route's integration/build block maps to `502` with `Calendar lookup failed: <message>`. Request validation occurs earlier and maps to FastAPI `422`.

## `POST /v1/schedule/task/{task_id}`

Requires API key. `task_id` is an integer. Body: `ScheduleTaskRequest`. Success `200`: `ScheduleTaskResponse` with explicit status:

- `NEW`: new event created;
- `UNCHANGED`: existing normalized bounds match; no write;
- `UPDATED`: existing resource updated in place;
- `RECOMMENDATION_ONLY`: `create_event=false`; no write.

All successes include the normalized task, `availability.options[0]`, checked calendars, and busy-event count. `already_scheduled` remains for compatibility; clients should use `status`.

| Status | Cause | Detail |
|---|---|---|
| `404` | `VikunjaTaskNotFound` | `Vikunja task <id> was not found` |
| `409` | completed task | `Task <id> is already completed.` |
| `422` | missing request/task deadline | `Task has no due date. Supply deadline_iso.` |
| `409` | no availability | `No available work block found.` |
| `409` | multiple marker matches | `Multiple Beacon events found for Vikunja task <id>.` |
| `404` | missing/stale event during update | typed lifecycle detail |
| `422` | caught `ValueError` | underlying message |
| `502` | `VikunjaError` | client-generated message |
| `502` | CalDAV update/integration error | typed service detail |
| unchanged | explicit `HTTPException` | re-raised |
| `502` | other caught exception | `Scheduling integration failed: <message>` |

Pydantic/path/header validation precedes route execution and uses standard `422`. Service construction is inside the mapped route block. CalDAV `ValueError` is `422`, missing/stale events are `404`, and integration/update failures are `502`.

## `GET /v1/brief/daily`

Requires API key. Optional query parameter `date=YYYY-MM-DD`; omission uses today in `BEACON_TIMEZONE`. Success `200` returns `DailyBriefResponse`. Calendar, Vikunja, Waze, and Home Assistant operational failures normally produce typed warnings and partial `200` responses rather than failing the brief. Invalid query dates use FastAPI `422`; unexpected generation failures map to `502` with `Daily brief generation failed: <message>`.

The endpoint is read-only. It does not schedule, update, delete, or create calendar/task records.
