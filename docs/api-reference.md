# API reference

The FastAPI application title is `Beacon API`, version `0.2.0`. See [Data models](data-models.md).

## Authentication

Protected endpoints require `X-Beacon-API-Key`. Missing headers are FastAPI validation failures (normally `422`); incorrect values return `401` with `{"detail":"Invalid Beacon API key"}`. `/health` is public.

## `GET /health`

Success `200`: `{"status":"ok","service":"beacon-api"}`.

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
