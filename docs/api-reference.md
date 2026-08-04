# API reference

Beacon is a JSON-over-HTTP FastAPI application named `Beacon API`, currently
version `0.3.0`. FastAPI also exposes generated OpenAPI metadata and Swagger UI
at its defaults (`/openapi.json` and `/docs`). This document covers Beacon's
application endpoints and behavior.

See [Data models](data-models.md) for every field and [CLI usage](../CLI_USAGE.md)
for the supported terminal client.

## Base URL and content type

Local and Compose examples use:

```text
http://localhost:8000
```

Request bodies are JSON. Successful application responses are JSON. Datetimes
use Pydantic ISO-8601 serialization and should include an offset.

## Authentication

Every endpoint except `/health` requires this header:

```http
X-Beacon-API-Key: <configured BEACON_API_KEY>
```

A missing or incorrect key returns:

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{"detail":"Invalid Beacon API key"}
```

There is one shared key with no user identities, roles, sessions, or read/write
scopes. Never place the key in a URL or commit it to source control.

## Endpoint summary

| Method and path | Auth | Mutates external state | Purpose |
|---|---:|---:|---|
| `GET /health` | no | no | Beacon process liveness. |
| `GET /status` | yes | no | Secret-safe configuration snapshot. |
| `GET /brief` | yes | no | Stable Daily Brief alias. |
| `POST /interact` | yes | depends on intent | Natural-language or structured-intent front door. |
| `POST /v1/conversation` | yes | depends on tool intent | Persistent bidirectional text interaction. |
| `POST /v1/availability` | yes | no | Explicit ranked availability calculation. |
| `POST /v1/schedule/task/{task_id}` | yes | configurable | Explicit scheduling lifecycle for one existing task. |
| `GET /v1/brief/daily` | yes | no | Versioned Daily Brief endpoint. |

## `GET /health`

Public liveness check. It does not load external integrations or prove they are
reachable.

Success `200`:

```json
{
  "status": "ok",
  "service": "beacon-api"
}
```

## `GET /status`

Returns a configuration-safe snapshot. It does not contact external services;
integration booleans mean configured/enabled, not reachable or healthy.

Success `200` shape:

```json
{
  "status": "ok",
  "service": "beacon-api",
  "version": "0.3.0",
  "timezone": "America/Chicago",
  "calendars": ["theater", "school", "personal"],
  "schedule_calendar": "personal",
  "integrations": {
    "nextcloud": true,
    "vikunja": true,
    "home_assistant": false,
    "travel": false
  },
  "interaction_modes": ["natural_language", "structured_intent", "conversation"]
}
```

`nextcloud` and `vikunja` are always `true` because their settings are required.
`home_assistant` requires both URL and token. `travel` reflects
`DAILY_BRIEF_TRAVEL_ENABLED`. `conversation` appears in `interaction_modes` only
when `CONVERSATION_ENABLED=true`.

## `GET /brief`

Stable automation-facing alias for `GET /v1/brief/daily`.

Query parameters:

| Name | Required | Format | Behavior |
|---|---:|---|---|
| `date` | no | `YYYY-MM-DD` | Defaults to the current date in `BEACON_TIMEZONE`. |

Example:

```bash
curl -sS 'http://localhost:8000/brief?date=2026-08-04' \
  -H 'X-Beacon-API-Key: replace-with-your-key'
```

Success `200` is `DailyBriefResponse`. The operation is read-only. Expected
calendar, Vikunja, Waze, and Home Assistant failures normally produce partial
data plus typed `warnings`, not a failed response. Invalid date syntax is `422`;
an unexpected generation failure is `502` with
`Daily brief generation failed: <message>`.

## `POST /interact`

Beacon's stable interaction front door. It accepts a natural-language `message`,
a validated structured `intent`, or both. At least one is required. If both are
present, `intent` is authoritative and the message is not interpreted.

Natural-language example:

```bash
curl -sS http://localhost:8000/interact \
  -X POST \
  -H 'Content-Type: application/json' \
  -H 'X-Beacon-API-Key: replace-with-your-key' \
  -d '{"message":"Schedule lighting paperwork tomorrow"}'
```

Structured example:

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

Success `200` is `InteractResponse` and contains:

- backend-generated human-readable `result`;
- the accepted `intent`;
- the deterministic `plan`;
- `actions_taken` describing actual execution;
- an optional typed `task`, `schedule`, or `brief` result.

Successful clarification is also `200`: an `UNKNOWN` or unsupported time
constraint produces a question, a `REQUEST_CLARIFICATION` plan, and no external
mutation.

Common interaction errors:

| Status | Cause |
|---|---|
| `400` | Unsupported rules-interpreter input or unsupported intent. |
| `404` | Requested/resolved Vikunja task or calendar event not found. |
| `409` | Ambiguous task title, completed task, multiple linked events, or no availability. |
| `422` | Pydantic request error, missing deadline, or invalid scheduling bounds/value. |
| `502` | Vikunja/CalDAV/Gemini upstream failure or other mapped interaction failure. |
| `503` | Interpreter configuration failure discovered while handling the request. Gemini-key absence is normally caught at startup first. |

See [Interaction](interaction.md) for grammar, planning, and execution details.

## `POST /v1/conversation`

The optional persistent text front door is available only when
`CONVERSATION_ENABLED=true`. The conversational Gemini turn selects a high-level
Beacon function. Local validation maps it directly to `StructuredIntent`, then
the existing planner and executor run it. There is no second model
interpretation pass.

Request:

```json
{
  "message": "Houston Ballet maintenance calls August 17 through August 21, 2026, from 9 AM to 5 PM each day",
  "client_message_id": "client-20260804-001",
  "session_id": "optional-existing-session-id"
}
```

`session_id` is omitted for a new session. `client_message_id` is required for
idempotency. Unknown fields are rejected.

Success shape:

```json
{
  "session_id": "...",
  "turn_id": "...",
  "status": "completed",
  "reply": "I added five maintenance calls.",
  "beacon_result": {"status": "complete", "created_count": 5},
  "degraded": false,
  "provider": {"provider": "gemini", "model": "gemini-3.6-flash"},
  "correlation_id": "...",
  "error": null,
  "idempotent_replay": false
}
```

The full authoritative result includes the validated intent, plan, and action
results. It—not the prose—is the source of truth. A final model-render failure
returns that result, a deterministic fallback, and `degraded: true`; successful
actions are not replayed.

Stable HTTP errors include `404` for an unknown supplied session, `409` for a
message-ID content conflict or concurrent turn, `422` for request-schema errors,
and `503` for disabled/misconfigured conversation or local persistence failure.
Provider failures during an established turn are represented in the typed `200`
response so clients retain session and turn identifiers.

## `POST /v1/availability`

Calculates ranked openings from actual configured CalDAV busy time. It never
writes an event.

Request example:

```json
{
  "earliest_iso": "2026-08-04T09:00:00-05:00",
  "deadline_iso": "2026-08-04T22:00:00-05:00",
  "duration_minutes": 90,
  "buffer_before_minutes": 15,
  "buffer_after_minutes": 15,
  "max_options": 3,
  "calendar_names": ["theater", "school", "personal"],
  "daily_start": "09:00",
  "daily_end": "22:00"
}
```

Success `200` example shape:

```json
{
  "calendars_checked": ["theater", "school", "personal"],
  "events_found": 4,
  "options": [
    {
      "start_iso": "2026-08-04T14:00:00-05:00",
      "end_iso": "2026-08-04T15:30:00-05:00",
      "score": 114.4,
      "reasons": [
        "fits requested duration",
        "daytime opening",
        "leaves at least one hour of flexibility"
      ]
    }
  ],
  "no_availability": false
}
```

Pydantic validation failures are `422`. Any exception inside the route's
calendar/build block, including execution-time parsing of invalid `daily_start`
or `daily_end`, is mapped to `502` with
`Calendar lookup failed: <message>`.

## `POST /v1/schedule/task/{task_id}`

Schedules or recommends one work block for an existing Vikunja task. `task_id`
must be an integer path value.

Request example:

```json
{
  "duration_minutes": 90,
  "earliest_iso": "2026-08-04T09:00:00-05:00",
  "deadline_iso": "2026-08-04T22:00:00-05:00",
  "calendar_name": "personal",
  "availability_calendars": ["theater", "school", "personal"],
  "buffer_before_minutes": 15,
  "buffer_after_minutes": 15,
  "daily_start": "09:00",
  "daily_end": "22:00",
  "create_event": true
}
```

`earliest_iso` defaults to the current Beacon-local time. `deadline_iso`
defaults to the task due date; if both are absent, the request is `422`.
`calendar_name` defaults to `BEACON_SCHEDULE_CALENDAR`, and
`availability_calendars` defaults to `BEACON_CALENDARS`.

Success `200` is `ScheduleTaskResponse` with one authoritative status:

| Status | Meaning | Calendar write |
|---|---|---:|
| `NEW` | No linked event existed; Beacon created one. | create |
| `UNCHANGED` | One linked event already had the selected normalized bounds. | none |
| `UPDATED` | One linked event existed with different bounds and was saved in place. | update |
| `RECOMMENDATION_ONLY` | `create_event=false`; selected slot returned. | none |

Every success includes the normalized task, `selected_option`, checked calendars,
and busy-event count. `calendar_event` is populated for created or existing
events when available. `already_scheduled` is retained for compatibility; new
clients should use `status`.

Error mapping:

| Status | Cause | Typical detail |
|---|---|---|
| `404` | Vikunja task missing | `Vikunja task <id> was not found` |
| `404` | Linked event disappeared/became stale during update | typed lifecycle detail |
| `409` | Task completed | `Task <id> is already completed.` |
| `409` | No ranked opening | `No available work block found.` |
| `409` | Multiple exact marker matches | `Multiple Beacon events found for Vikunja task <id>.` |
| `422` | No request/task deadline | `Task has no due date. Supply deadline_iso.` |
| `422` | Invalid value/bounds/calendar-update shape | underlying validation detail |
| `502` | Vikunja or CalDAV integration/update failure | typed adapter detail |
| `502` | Unexpected mapped failure | `Scheduling integration failed: <message>` |

## `GET /v1/brief/daily`

Versioned form of `/brief`, with the same optional `date=YYYY-MM-DD`, response,
read-only guarantee, warning behavior, and errors.

## Validation and error shape

FastAPI/Pydantic request validation occurs before route execution and returns a
standard `422` body whose `detail` is a list. Route/service errors use a string
`detail`. Clients should use the HTTP status for control flow and treat detail
text as user-facing diagnostic context rather than a stable machine code.

Daily Brief warnings are the exception: they are typed fields inside a successful
`200` response because partial calendar/task/context information can still be
useful.
