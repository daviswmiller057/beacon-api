# Today dashboard

Beacon exposes one native-client read model:

```http
GET /api/v1/dashboard/today
X-Beacon-API-Key: <configured BEACON_API_KEY>
```

The endpoint is authenticated and strictly read-only. It creates, updates,
schedules, reschedules, and deletes nothing. It calls `TodayDashboardService`,
which projects the existing deterministic `DailyBriefService`; the HTTP route
does not call Vikunja, CalDAV, Waze, Home Assistant, or any model provider.

## Schema version 1

The JSON response uses snake_case. Top-level fields are:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | integer literal `1` | Contract version. |
| `generated_at` | timezone-aware timestamp | Snapshot generation instant. |
| `timezone` | string | Configured `BEACON_TIMEZONE` IANA name. |
| `local_date` | `YYYY-MM-DD` | Calendar date at `generated_at` in that timezone. |
| `display_name` | string or null | Null because Beacon has no deterministic display-name source. |
| `next_event` | event summary or null | Existing Daily Brief current/next ordinary event. |
| `focus` | task summary or null | Null because Beacon has no focus-selection behavior. |
| `attention_items` | array | Existing overdue tasks; empty when none are present. |
| `priority_tasks` | array | At most five existing incomplete task summaries. |
| `recommended_action` | action summary or null | Null because Beacon has no deterministic recommendation behavior. |

Event summaries contain required string `id`, string `title`, aware `start_at`
and `end_at`, plus nullable `location`, aware `leave_by_at`, and
`calendar_name`. The ID is the CalDAV UID when present; otherwise Beacon derives
a stable opaque ID from the event identity. `leave_by_at` is populated only
when the existing enabled Daily Brief travel service produced a matching
estimate.

Task summaries contain string `id`, string `title`, nullable `project_name`,
`priority`, nullable aware `due_at`, and boolean `completed`. Project name is
null because the current normalized Vikunja task model contains only a numeric
project ID and no project-name lookup. Priority translates Vikunja/Beacon's
existing integer ordering as follows:

| Existing value | API enum |
|---:|---|
| `<= 0` | `none` |
| `1` | `low` |
| `2` | `medium` |
| `3` | `high` |
| `4` | `urgent` |
| `>= 5` | `do_now` |

The priority list preserves existing semantics without a new scoring rule: the
Daily Brief's highest-priority incomplete task appears first, followed by
remaining overdue tasks and then remaining due-today tasks in their existing
service order, with duplicates removed. Completed tasks are excluded.

Attention items contain string `id`, string `title`, nullable `detail`,
`severity`, and `source`. Severity is constrained to `info`, `warning`, or
`critical`; source is constrained to `task` or `calendar`. Version 1 currently
emits overdue tasks only, with `warning` severity and `task` source.

Recommended-action summaries contain string `title`, nullable `detail`, kind
`review`, and nullable string `related_id`. The top-level field is currently
always null because no existing deterministic recommendation supplies it.

All timestamps are RFC 3339/ISO-8601 values with an offset. Naive timestamps are
not valid at this boundary. `generated_at`, `local_date`, event selection, and
due-date normalization all use configured `BEACON_TIMEZONE`, never the host's
local timezone.

## Example

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-04T08:30:00-05:00",
  "timezone": "America/Chicago",
  "local_date": "2026-08-04",
  "display_name": null,
  "next_event": {
    "id": "calendar-event-7",
    "title": "Production meeting",
    "start_at": "2026-08-04T09:00:00-05:00",
    "end_at": "2026-08-04T10:00:00-05:00",
    "location": "Main Theater",
    "leave_by_at": null,
    "calendar_name": "personal"
  },
  "focus": null,
  "attention_items": [
    {
      "id": "overdue_task:42",
      "title": "Submit lighting order",
      "detail": "Due 2026-08-03T17:00:00-05:00",
      "severity": "warning",
      "source": "task"
    }
  ],
  "priority_tasks": [
    {
      "id": "42",
      "title": "Submit lighting order",
      "project_name": null,
      "priority": "urgent",
      "due_at": "2026-08-03T17:00:00-05:00",
      "completed": false
    }
  ],
  "recommended_action": null
}
```

## Empty values and degradation

Null and empty values are intentional. Beacon omits unsupported intelligence
instead of fabricating focus, concerns, or recommendations. Calendar or task
source failures use Daily Brief's established graceful degradation, so a
successful response can contain partial real data. Raw upstream payloads and
exception details are not exposed by this route.

Clients must ignore new optional fields added in future schema-compatible
versions. They should branch on `schema_version` for incompatible contract
changes and must treat documented nullable fields and empty arrays as normal.

## Development and tests

Use deterministic fakes; tests must not contact production services:

```bash
.venv/bin/python -m pytest tests/test_dashboard.py -q
.venv/bin/python -m pytest
```

Generate a development `BEACON_API_KEY` with the secure command in
[Development](development.md), store it only in an uncommitted `.env`, and send
it in `X-Beacon-API-Key`.
