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

Requires API key. `task_id` is an integer. Body: `ScheduleTaskRequest`. Success `200`: `ScheduleTaskResponse` with:

- `scheduled`: new event created;
- `recommended`: `create_event=false`, no existing event;
- `already_scheduled`: marked event found and `already_scheduled=true`.

All successes include the normalized task, `availability.options[0]` as `selected_option`, checked calendars, and busy-event count. Duplicate lookup occurs even when `create_event=false`.

| Status | Cause | Detail |
|---|---|---|
| `404` | `VikunjaTaskNotFound` | `Vikunja task <id> was not found` |
| `409` | completed task | `Task <id> is already completed.` |
| `422` | missing request/task deadline | `Task has no due date. Supply deadline_iso.` |
| `409` | no availability | `No available work block found.` |
| `422` | caught `ValueError` | underlying message |
| `502` | `VikunjaError` | client-generated message |
| unchanged | explicit `HTTPException` | re-raised |
| `502` | other caught exception | `Scheduling integration failed: <message>` |

Pydantic/path/header validation precedes the route `try` and uses standard `422`. Settings and service construction also precede that `try`, so construction failure is not handled by its catch-all. CalDAV exceptions normally become `502`; CalDAV `ValueError` becomes `422`.
