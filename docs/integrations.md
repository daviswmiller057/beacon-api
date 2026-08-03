# External integrations

Beacon keeps protocol details in small synchronous adapters. Business decisions
remain in the planner and domain services; adapters normalize data and perform
explicit external reads/writes.

See [Architecture](architecture.md), [Scheduling](scheduling.md),
[Daily Brief](daily-brief.md), and [Debugging notes](debugging-notes.md).

## Integration matrix

| System | Adapter | Reads | Writes | Timeout/failure behavior |
|---|---|---|---|---|
| Vikunja | `VikunjaClient` | one task, paged task list | create task | HTTP timeout 15s; typed `VikunjaError`/`VikunjaTaskNotFound` |
| Nextcloud | `CalDAVService` | calendars, busy intervals, daily events, linked blocks | create/update events | caldav client behavior; typed calendar errors where wrapped |
| Gemini | `GeminiInterpreter` | structured intent response | none outside Gemini request | HTTP timeout 20s; typed interpreter errors |
| Home Assistant | `HomeAssistantClient` | one weather state | none | HTTP timeout 10s; brief warning on failure |
| Waze | `WazeClient` | route duration/distance | none | library-controlled call; brief warning on failure |

All integration tests use fakes or mocks. The automated suite does not require
live services or real credentials.

## Vikunja

`VikunjaClient` removes a trailing slash from `VIKUNJA_API_URL` and sends:

```http
Authorization: Bearer <VIKUNJA_API_TOKEN>
Accept: application/json
```

### Get one task

`get_task(id)` calls `GET {base}/tasks/{id}` with a 15-second timeout. An exact
HTTP `404` becomes `VikunjaTaskNotFound`; connection errors and other HTTP errors
become `VikunjaError`. Upstream response text included in an error is truncated
to 300 characters.

### List tasks

`list_tasks()` calls `GET {base}/tasks` with `page` and `per_page=100`, continuing
until a page contains fewer than 100 items. A non-list JSON payload is rejected.
This is used for Daily Brief and safe title matching during intake.

### Create a task

`create_task(title, due_date)` requires `VIKUNJA_DEFAULT_PROJECT_ID` and calls:

```text
PUT {base}/projects/{project_id}/tasks
```

with `{"title": ...}` and optional ISO-8601 `due_date`. Missing project
configuration, HTTP/connectivity failures, and malformed task responses become
`VikunjaError`. Beacon currently creates tasks only; it does not update,
complete, delete, label, or add reminders to them.

### Normalization

All task responses use the same mapper:

- `id` and `title` are required from Vikunja;
- falsy/missing description, priority, and labels become `""`, `0`, and `[]`;
- missing `done` becomes `false`;
- terminal `Z` is parsed as `+00:00`;
- empty due dates, `0001-01-01T00:00:00Z`, and parsed years `<=1` become `None`.

Deployment should use the LAN Vikunja endpoint where possible. A public
Cloudflare-proxied hostname previously produced Error 1010; that is operational
history, not a code restriction. Keep private URLs and tokens out of docs/logs.

## Nextcloud / CalDAV

`CalDAVService` creates a `caldav.DAVClient` with the configured URL, username,
and app password, then enumerates the current principal's calendars.

### Calendar selection

- Calendar display-name comparison trims whitespace and is case-insensitive.
- Busy/daily reads use the requested names or `BEACON_CALENDARS`.
- An empty list is falsy and therefore falls back to configured defaults.
- Busy reads silently skip requested names absent from the principal.
- Destination lookup raises `ValueError` if the named calendar is absent.

### Event normalization

Searches use `event=True, expand=True`, and only `VEVENT` components are used.

- aware datetimes are converted to `BEACON_TIMEZONE`;
- naive datetimes are assumed to already be in that timezone;
- all-day dates become local midnight;
- end comes from `DTEND`, then `DURATION`, otherwise equals start;
- events outside the requested range are skipped;
- busy intervals are clipped to request bounds.

### Task marker

Beacon links a work block to a Vikunja task with an exact, case-sensitive
description line:

```text
Vikunja task ID: 42
```

Matching splits the description into lines, so task `42` does not match `420` or
text embedded in another line. Daily Brief parses the same prefix and classifies
events with a numeric marker as Beacon work blocks.

### Reads

- `fetch_busy_intervals` returns clipped `BusyInterval` models and can exclude
  one exact task marker during rescheduling.
- `fetch_calendar_events` returns normalized Daily Brief events with title,
  description, location, bounds, all-day flag, and marker metadata, sorted by
  start, end, calendar, then title.
- `find_task_events` searches one destination calendar and returns every exact
  marker match so the scheduler can reject ambiguity.
- `find_task_event` is a compatibility wrapper that returns only the first match.

### Create

`create_event` rejects `end <= start`, resolves the destination display name,
and calls `calendar.add_event` with start, end, summary, and description. Beacon's
scheduler uses title `Work Block — <task title>` and description:

```text
Scheduled by Beacon

Vikunja task ID: <id>
Priority: <priority>
```

### Update

`update_event` rejects invalid bounds, reloads the original resource, and verifies:

- it is still a `VEVENT`;
- the exact task marker still exists;
- its UID has not changed when both old/current UIDs exist;
- the event uses `DTEND` and is not `DURATION`-based.

It then replaces only `DTSTART` and `DTEND` and calls:

```python
event.save(no_create=True, increase_seqno=False)
```

UID, href, calendar, summary, description, marker, alarms, sequence, and unrelated
properties are preserved. Disappeared/stale events become
`CalendarEventNotFoundError`; unsupported/update failures become
`CalendarEventUpdateError`.

## Gemini

`GeminiInterpreter` is optional and selected with `BEACON_INTERPRETER=gemini`.
It posts to the configured model's `generateContent` endpoint using the
`x-goog-api-key` header, a 20-second timeout, temperature zero, JSON MIME type,
and the `StructuredIntent` serialization schema.

The model receives no integration clients or credentials and cannot execute a
Beacon action. Its first candidate text must independently validate as
`StructuredIntent`; unusable shape and invalid intent are typed response errors.
HTTP/provider failures are interpreter errors and map through `/interact` without
reaching the planner/executor.

## Waze

`WazeClient` wraps pinned `WazeRouteCalculator==0.16`. It accepts free-text
origin/destination strings, calls real-time route calculation, and normalizes
minutes/kilometers to floats rounded to one decimal for `TravelEstimate`.

Home-to-event estimates compute `leave_by = event start - travel duration -
configured buffer`. Sequential checks use unrounded travel minutes. Any library
failure becomes `WazeError`, which Daily Brief converts to a typed warning.

This library uses unofficial Waze Live Map behavior, not a supported server API.
It may change independently, so it is deliberately isolated and never allowed to
fail the whole brief.

## Home Assistant

`HomeAssistantClient` performs:

```http
GET {HOME_ASSISTANT_URL}/api/states/{HOME_ASSISTANT_WEATHER_ENTITY}
Authorization: Bearer <HOME_ASSISTANT_TOKEN>
Accept: application/json
```

It normalizes entity ID, condition, optional temperature/unit/humidity, and
optional `last_updated` into `WeatherConditions`. Missing URL/token,
connectivity, non-success HTTP status, invalid JSON, or invalid response shape
becomes `HomeAssistantError`, which Daily Brief reports as
`WEATHER_UNAVAILABLE`.

This is an outbound read-only weather integration. Beacon does not forecast,
control Home Assistant, expose an inbound conversation agent, or send
notifications through it.

## Secret handling

Never expose or commit:

- `BEACON_API_KEY`;
- `VIKUNJA_API_TOKEN`;
- `NEXTCLOUD_APP_PASSWORD`;
- `GEMINI_API_KEY`;
- `HOME_ASSISTANT_TOKEN`;
- `.env` contents or private service endpoints.

Errors can include truncated upstream text. Review logs and debug output before
sharing them outside the trusted environment.
